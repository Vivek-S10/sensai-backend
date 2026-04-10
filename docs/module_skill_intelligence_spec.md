# Module B: Skill Intelligence — Specification

> **Priority:** T1 (Foundation)  
> **Package:** `src/api/assessment/skills/`  
> **Owner Files:** `extractor.py`, `ontology.py`, `reporter.py`  
> **Depends On:** Module A (called by JD Engine), `contracts.py`

---

## 1. Module Scope

This module handles all skill-related intelligence:

| Feature | Tier | Description |
|---|---|---|
| **Skill Ontology Extraction** | T1 | Two-step JD parser: (1) LLM extracts raw skills, (2) structures them into a weighted ontology tree |
| **Skill Weight Adjustment** | T1 | Recruiters can adjust the importance weights of extracted skills before question generation |
| **Granular Skill Reporting** | T1 | Post-assessment, generates a detailed competency map of sub-topics instead of a single score |

**What this module does NOT do:**
- Does NOT generate questions (that's Module C — this module provides the *skill context* for generation)
- Does NOT compute confidence scores (that's Module D — but this module's ontology is an *input* to confidence calculation)
- Does NOT interact with the Curriculum Engine directly (the Curriculum Engine maps existing scorecard criteria to skills internally)

---

## 2. Design Patterns & Internal Structure

### 2.1 Pipeline Pattern

The skill intelligence workflow is a sequential pipeline:

```
JD Text ──► Step 1: Raw Extraction ──► Step 2: Structuring ──► SkillOntology
                (extractor.py)           (ontology.py)

SkillOntology + Assessment Results ──► Competency Report
                                        (reporter.py)
```

### 2.2 `extractor.py` — JD Skill Extractor

Responsibilities:
1. Take raw JD text as input
2. **Step 1 (LLM call):** Extract a flat list of technical skills, soft skills, and requirements from the JD
3. **Step 2 (LLM call):** Organize the flat list into a hierarchical skill tree with parent-child relationships and initial weights
4. Return a `SkillOntology` object

```python
# extractor.py — Structural sketch

class JDSkillExtractor:
    """
    Two-step LLM pipeline for JD → SkillOntology.
    """

    async def extract(self, jd_text: str) -> SkillOntology:
        """
        Full pipeline:
        1. _extract_raw_skills(jd_text) → RawSkillList
        2. _structure_ontology(raw_skills) → SkillOntology
        """
        raw_skills = await self._extract_raw_skills(jd_text)
        ontology = await self._structure_ontology(raw_skills, jd_text)
        return ontology

    async def _extract_raw_skills(self, jd_text: str) -> RawSkillList:
        """
        Step 1: LLM Call
        - Prompt: 'skill-extraction-raw' (registered in Langfuse)
        - Input: JD text
        - Output: Flat list of {name, category, relevance_hint}
        - Model: text-mini (fast, cheap — this is a classification task)
        """
        ...

    async def _structure_ontology(self, raw_skills: RawSkillList, jd_text: str) -> SkillOntology:
        """
        Step 2: LLM Call
        - Prompt: 'skill-extraction-structure' (registered in Langfuse)
        - Input: Raw skill list + original JD for context
        - Output: Hierarchical tree with weights
        - Model: text (needs reasoning for weight assignment)

        Weight assignment rules (injected into prompt):
        - Weights must sum to 1.0 across top-level skills
        - Sub-skill weights sum to 1.0 within their parent
        - Weight reflects importance in the JD (e.g., if "Python" is mentioned 
          5x and "Docker" 1x, Python gets higher weight)
        """
        ...
```

**LLM Output Models:**

```python
# Pydantic models for LLM structured output

class RawSkillItem(BaseModel):
    name: str = Field(description="Skill name, e.g., 'Python', 'REST API Design'")
    category: str = Field(description="Category: 'technical', 'soft_skill', or 'domain_knowledge'")
    relevance_hint: str = Field(description="Why this skill is relevant to the JD")

class RawSkillList(BaseModel):
    skills: List[RawSkillItem]
    jd_seniority: str = Field(description="Inferred seniority level: 'junior', 'mid', 'senior', 'lead'")
    jd_domain: str = Field(description="Primary domain: 'backend', 'frontend', 'fullstack', 'data', 'devops', etc.")

# SkillOntology is already defined in contracts.py
```

### 2.3 `ontology.py` — Skill Tree Management

Responsibilities:
1. Weight adjustment (recruiter-driven)
2. Weight normalization (ensure sums to 1.0)
3. Persistence (save/load ontology to DB)
4. Querying (get skills by assessment, get skill by ID)

```python
# ontology.py — Structural sketch

class SkillOntologyManager:
    """
    Manages skill ontology CRUD and weight adjustment.
    """

    async def save(self, assessment_id: str, ontology: SkillOntology) -> None:
        """Persist ontology to skill_ontologies + skill_ontology_items tables."""
        ...

    async def load(self, assessment_id: str) -> SkillOntology | None:
        """Load ontology from DB for a given assessment."""
        ...

    async def adjust_weights(
        self,
        assessment_id: str,
        adjustments: List[SkillWeightAdjustment],
    ) -> SkillOntology:
        """
        Apply weight adjustments from recruiter.

        Rules:
        1. Load current ontology
        2. Apply adjustments: {skill_id: new_weight}
        3. Re-normalize: all top-level weights must sum to 1.0
        4. If a sub-skill weight changes, re-normalize within its parent
        5. Persist updated ontology
        6. Return updated ontology
        """
        ...

    def _normalize_weights(self, skills: List[Skill]) -> List[Skill]:
        """
        Ensure weights sum to 1.0.
        Strategy: proportional scaling.
        If recruiter sets Python=0.6, React=0.5, total=1.1
        → scale: Python=0.6/1.1=0.545, React=0.5/1.1=0.454
        """
        ...
```

### 2.4 `reporter.py` — Granular Competency Reporter

Responsibilities:
1. After assessment completion, aggregate per-skill scores
2. Map each answered question to its `metadata.skill_tested`
3. Compute weighted scores per skill and sub-skill
4. Classify competency levels
5. Generate recommendations

```python
# reporter.py — Structural sketch

class CompetencyReporter:
    """
    Generates granular skill reports from assessment results.
    """

    async def generate_report(
        self,
        assessment_id: str,
        user_id: int,
        question_results: List[QuestionResult],
    ) -> SkillReport:
        """
        1. Load skill ontology for this assessment
        2. Map each question's score to its tested skill
        3. Compute weighted averages per skill and sub-skill
        4. Classify: 0-40% = "weak", 40-70% = "developing", 70-90% = "strong", 90%+ = "expert"
        5. Generate LLM-powered recommendations (optional, can be rule-based initially)
        """
        ...

    def _compute_skill_scores(
        self,
        ontology: SkillOntology,
        question_results: List[QuestionResult],
    ) -> List[SkillScore]:
        """
        Pure calculation — no LLM needed.

        For each skill:
          score = sum(question_score * question_weight) / sum(question_weights)

        Question weight within a skill = 1.0 / number_of_questions_for_that_skill
        (equal weight per question unless metadata specifies otherwise)
        """
        ...

    def _classify_competency(self, score: float) -> str:
        """Rule-based classification."""
        if score >= 90: return "expert"
        if score >= 70: return "strong"
        if score >= 40: return "developing"
        return "weak"
```

---

## 3. Data Flow

### 3.1 Extraction Flow (called by JD Engine)

```
┌─────────────┐     jd_text      ┌─────────────┐    RawSkillList    ┌──────────────┐
│ JD Engine   │ ───────────────► │ extractor.py │ ────────────────► │ extractor.py │
│ (Module A)  │                  │ Step 1       │                    │ Step 2       │
└─────────────┘                  └──────────────┘                    └──────┬───────┘
                                                                           │
                                                                    SkillOntology
                                                                           │
                                                                    ┌──────▼───────┐
                                                                    │ ontology.py  │
                                                                    │ (persist)    │
                                                                    └──────┬───────┘
                                                                           │
                                                            Returns to JD Engine
                                                            → passed to Module C
```

### 3.2 Weight Adjustment Flow (called by Facade)

```
┌──────────┐   AdjustSkillsRequest   ┌────────────┐    Updated SkillOntology    ┌──────────────┐
│ Recruiter│ ──────────────────────► │ Facade     │ ──────────────────────────► │ ontology.py  │
│ (Frontend)                         │ (Module A) │                              │ adjust +     │
└──────────┘                         └────────────┘                              │ normalize    │
                                                                                 └──────┬───────┘
                                                                                        │
                                                                    If regenerate=True:
                                                                    → Re-dispatch to engine
                                                                    → Module C regenerates questions
```

### 3.3 Reporting Flow (called by Facade, post-assessment)

```
┌──────────────┐   assessment_id, user_id   ┌──────────────┐
│ Facade       │ ─────────────────────────► │ reporter.py  │
│ (Module A)   │                             │              │
└──────────────┘                             └──────┬───────┘
                                                    │
                                             Load ontology from DB
                                             Load question results from DB
                                                    │
                                             ┌──────▼───────┐
                                             │ SkillReport  │
                                             │ (response)   │
                                             └──────────────┘
```

### 3.4 Output Models

**SkillOntology** — defined in `contracts.py`:

```json
{
  "id": "uuid",
  "skills": [
    {
      "id": "uuid",
      "name": "Python",
      "weight": 0.35,
      "sub_skills": [
        { "id": "uuid", "name": "Decorators", "weight": 0.15 },
        { "id": "uuid", "name": "Async/Await", "weight": 0.20 }
      ]
    }
  ],
  "total_weight": 1.0,
  "extraction_confidence": 0.92
}
```

**SkillReport** — new model, defined in this module:

```python
class SubSkillScore(BaseModel):
    name: str
    score: float
    questions_tested: List[int]
    strength: str  # "weak" | "developing" | "strong" | "expert"

class SkillScore(BaseModel):
    skill_name: str
    weight: float
    score: float
    sub_skills: List[SubSkillScore]

class SkillReport(BaseModel):
    assessment_id: str
    user_id: int
    overall_score: float
    skill_breakdown: List[SkillScore]
    competency_level: str  # aggregate classification
    recommendations: List[str]
```

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | `SkillOntology`, `Skill`, `SubSkill` models |
| `llm.py` | Existing | `run_llm_with_openai` for both extraction steps |
| `langfuse` | External | Prompt registry + tracing |
| `db/assessment.py` | Internal | Save/load ontology data |
| Module A (Facade) | Caller | Facade calls this module's extractor and manager |
| Module C (Question Factory) | Consumer | Question Factory reads the ontology to target skill coverage |

### 4.2 Langfuse Prompts Required

| Prompt Name | Type | Input Variables | Output Model |
|---|---|---|---|
| `skill-extraction-raw` | chat | `jd_text` | `RawSkillList` |
| `skill-extraction-structure` | chat | `raw_skills`, `jd_text` | `SkillOntology` |
| `skill-report-recommendations` | chat | `skill_breakdown`, `jd_context` | `List[str]` (optional, can be rule-based initially) |

### 4.3 Edge Cases

| Edge Case | Handling |
|---|---|
| **JD is very short (< 100 words)** | LLM may extract too few skills. Set a minimum of 3 top-level skills. If extraction returns fewer, warn the user but proceed. |
| **JD contains no technical skills** | Return a `SkillOntology` with `extraction_confidence < 0.3` and a warning message. Let the recruiter add skills manually (future UI feature — design the ontology model to support manual additions). |
| **Duplicate skills extracted** | Step 2 (structuring) is responsible for deduplication. Prompt must instruct: "Merge duplicate or near-synonym skills (e.g., 'JS' and 'JavaScript')." |
| **Weight adjustment sums to 0** | Reject with 400. At least one skill must have weight > 0. |
| **Weight adjustment for non-existent skill_id** | Reject with 400. Validate all skill IDs exist in the ontology before applying. |
| **Reporting: question has no `metadata.skill_tested`** | Skip that question in skill breakdown. Add a note in the report: "N questions could not be mapped to a skill." |
| **Reporting: no questions answered for a skill** | Report that skill with `score: null` and `strength: "untested"`. |
| **LLM returns malformed ontology** | Pydantic validation catches this. Log the error, retry with `backoff`. After max retries, return a minimal ontology with the raw skills as flat list (no hierarchy). |

### 4.4 Database Tables Owned

| Table | Purpose |
|---|---|
| `skill_ontologies` | Ontology header (assessment_id FK, extraction_confidence, raw_jd_text) |
| `skill_ontology_items` | Individual skill nodes (ontology_id FK, parent_item_id for hierarchy, name, weight) |
| `assessment_skill_reports` | Per-user competency data (assessment_id, user_id, report_json) |

```sql
CREATE TABLE IF NOT EXISTS skill_ontologies (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    extraction_confidence REAL,
    raw_jd_text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE INDEX idx_skill_ontology_assessment_id ON skill_ontologies (assessment_id);

CREATE TABLE IF NOT EXISTS skill_ontology_items (
    id TEXT PRIMARY KEY,
    ontology_id TEXT NOT NULL,
    parent_item_id TEXT,
    name TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    category TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (ontology_id) REFERENCES skill_ontologies(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_item_id) REFERENCES skill_ontology_items(id) ON DELETE SET NULL
);

CREATE INDEX idx_skill_item_ontology_id ON skill_ontology_items (ontology_id);
CREATE INDEX idx_skill_item_parent_id ON skill_ontology_items (parent_item_id);

CREATE TABLE IF NOT EXISTS assessment_skill_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    overall_score REAL,
    competency_level TEXT,
    report_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    UNIQUE(assessment_id, user_id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_skill_report_assessment ON assessment_skill_reports (assessment_id);
CREATE INDEX idx_skill_report_user ON assessment_skill_reports (user_id);
```

---

## 5. Performance Considerations

- **Two LLM calls for extraction** → Use the `text-mini` model for Step 1 (classification) and `text` model for Step 2 (reasoning). This balances speed and quality.
- **Ontology persistence** → Store the full ontology as both structured rows (for querying) AND as JSON in the assessment record (for fast retrieval). The DB rows are the source of truth; the JSON is a cache.
- **Report generation** → The score computation is pure math (no LLM). Only the recommendations generation (optional) uses the LLM. Consider making recommendations lazy (generated on first request, then cached).
