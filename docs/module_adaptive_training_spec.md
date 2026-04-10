# Module F: Adaptive Training — Specification

> **Priority:** T3 (Advanced Intelligence)  
> **Package:** `src/api/assessment/training/`  
> **Owner Files:** `profiler.py`, `generator.py`  
> **Depends On:** Module A (called by Facade), Module B (skill ontology), Module C (question generation patterns), `contracts.py`

---

## 1. Module Scope

This module handles post-assessment adaptive drill generation:

| Feature | Tier | Description |
|---|---|---|
| **Trainer Flow (Adaptive Drills)** | T3 | After an assessment is completed, generate a personalized question set based on the candidate's incorrect answers. Targets weak skills/sub-skills with appropriately calibrated difficulty. **Never used for comparative grading.** |

**Critical constraint from the original brief:** Trainer Flow output is **strictly non-comparative**. It is a private learning tool for the individual candidate. It must NEVER appear in leaderboards, scoring comparisons, or HR reports.

**What this module does NOT do:**
- Does NOT replace or modify the original assessment
- Does NOT affect the original assessment scores
- Does NOT share drill results with other users/HR
- Does NOT generate assessment-grade questions (these are practice drills — lower stakes, more hints)

---

## 2. Design Patterns & Internal Structure

### 2.1 Composite Pattern — `profiler.py`

The weakness profiler aggregates information from multiple sources to build a composite weakness profile.

```python
# profiler.py — Structural sketch

class WeaknessProfiler:
    """
    Analyzes a candidate's assessment results to identify specific
    weakness areas for targeted drill generation.
    
    Composite: Aggregates signals from:
    - Per-question scores (primary signal)
    - Skill ontology weights (prioritize high-weight skills)
    - Time logs if available (long thinking time may indicate confusion)
    - Difficulty levels (incorrect on "easy" questions is a stronger signal)
    """

    async def build_profile(
        self,
        assessment: Assessment,
        user_id: int,
        question_results: List[QuestionResult],
        time_logs: TimeLogReport | None = None,
    ) -> WeaknessProfile:
        """
        Build a weakness profile for a specific candidate.
        
        Algorithm:
        1. Identify all incorrect/low-scoring questions
        2. Map each to its tested skill (via metadata.skill_tested)
        3. Compute weakness severity per skill:
           severity = (1 - score/max_score) * skill_weight * difficulty_multiplier
           
           difficulty_multiplier:
             easy question wrong → 1.5x (failing easy = bigger red flag)
             medium question wrong → 1.0x
             hard question wrong → 0.7x (expected to be hard)
        
        4. If time_logs available: boost severity for questions with 
           high thinking_time (> 2x average) — indicates confusion
        
        5. Sort skills by severity (descending)
        6. Return top-N weakness areas with context
        """
        incorrect_questions = self._filter_incorrect(question_results)
        skill_weaknesses = self._map_to_skills(incorrect_questions, assessment)
        
        if time_logs:
            skill_weaknesses = self._boost_with_time_data(skill_weaknesses, time_logs)
        
        sorted_weaknesses = sorted(skill_weaknesses, key=lambda w: w.severity, reverse=True)
        
        return WeaknessProfile(
            assessment_id=assessment.id,
            user_id=user_id,
            weakness_areas=sorted_weaknesses,
            total_incorrect=len(incorrect_questions),
            total_questions=len(question_results),
            overall_score=self._compute_overall_score(question_results),
        )

    def _filter_incorrect(self, results: List[QuestionResult]) -> List[QuestionResult]:
        """
        Filter to incorrect or low-scoring questions.
        
        For objective: is_correct == False
        For subjective: score < pass_score (from scorecard criteria)
        """
        ...

    def _map_to_skills(
        self,
        incorrect: List[QuestionResult],
        assessment: Assessment,
    ) -> List[SkillWeakness]:
        """
        Group incorrect answers by skill and compute severity.
        """
        skill_map: Dict[str, SkillWeakness] = {}

        for result in incorrect:
            question = self._find_question(result.question_id, assessment)
            if not question or not question.metadata:
                continue

            skill_ref = question.metadata.skill_tested
            skill_id = skill_ref.skill_id

            if skill_id not in skill_map:
                skill_weight = self._get_skill_weight(skill_id, assessment.skill_ontology)
                skill_map[skill_id] = SkillWeakness(
                    skill_id=skill_id,
                    skill_name=skill_ref.skill_name,
                    parent_skill=skill_ref.parent_skill,
                    weight=skill_weight,
                    incorrect_questions=[],
                    severity=0.0,
                )

            diff_multiplier = {"easy": 1.5, "medium": 1.0, "hard": 0.7}
            question_severity = (
                (1 - result.score / result.max_score)
                * skill_map[skill_id].weight
                * diff_multiplier.get(question.difficulty.value, 1.0)
            )

            skill_map[skill_id].incorrect_questions.append(result)
            skill_map[skill_id].severity += question_severity

        return list(skill_map.values())
```

**Data Models:**

```python
class QuestionResult(BaseModel):
    """Input: result of a candidate's answer to a question."""
    question_id: str
    is_correct: bool | None = None  # for objective
    score: float = 0.0  # for subjective
    max_score: float = 100.0
    feedback: str | None = None

class SkillWeakness(BaseModel):
    """Identified weakness in a specific skill."""
    skill_id: str
    skill_name: str
    parent_skill: str | None
    weight: float
    incorrect_questions: List[QuestionResult]
    severity: float  # higher = weaker
    thinking_time_anomaly: bool = False  # True if time data suggests confusion

class WeaknessProfile(BaseModel):
    """Complete weakness profile for a candidate."""
    assessment_id: str
    user_id: int
    weakness_areas: List[SkillWeakness]
    total_incorrect: int
    total_questions: int
    overall_score: float
```

### 2.2 Strategy Pattern — `generator.py`

The drill generator creates practice questions targeted at identified weaknesses.

```python
# generator.py — Structural sketch

class DrillGenerator:
    """
    Generates personalized practice drills for a candidate's weak areas.
    
    Key design decisions:
    1. Drills are EASIER than the original assessment questions (one difficulty level down)
    2. Drills include HINTS — unlike assessment questions
    3. Drills are practice-only — never graded comparatively
    4. Drills focus on the SAME skill but with different problem structures
    """

    async def generate_drills(
        self,
        profile: WeaknessProfile,
        params: DrillParams,
        assessment: Assessment,
    ) -> AsyncGenerator[List[DrillQuestion], None]:
        """
        Drill generation pipeline:
        
        1. Select target skills from weakness profile
           - If focus_skills specified: use those
           - Otherwise: use top-N weakest skills by severity
        
        2. Determine difficulty:
           - "same" → same as the original failed question
           - "easier" → one level down (medium → easy, hard → medium)
           - "harder" → one level up (rare — for candidates who want challenge)
        
        3. For each target skill:
           a. Generate drill question via LLM (with hints)
           b. Ensure diversity vs. original assessment questions
           c. Stream progressive results
        
        4. Build DrillSession record and persist
        """
        target_skills = self._select_targets(profile, params)
        
        drills = []
        for skill_weakness in target_skills:
            count_for_skill = self._allocate_count(skill_weakness, params.max_questions, target_skills)
            
            for i in range(count_for_skill):
                difficulty = self._adjust_difficulty(
                    skill_weakness.incorrect_questions,
                    params.difficulty_preference,
                )
                
                drill = await self._generate_single_drill(
                    skill_weakness=skill_weakness,
                    difficulty=difficulty,
                    assessment=assessment,
                    existing_drills=drills,
                )
                
                drills.append(drill)
                yield drills  # Stream progressive updates

        # Persist the drill session
        await self._persist_session(profile.assessment_id, profile.user_id, drills)

    async def _generate_single_drill(
        self,
        skill_weakness: SkillWeakness,
        difficulty: Difficulty,
        assessment: Assessment,
        existing_drills: List[DrillQuestion],
    ) -> DrillQuestion:
        """
        Generate a single drill question via LLM.
        
        Prompt: 'drill-generation' (Langfuse)
        
        Key differences from assessment question generation:
        1. Include a HINT field in the output
        2. Include a STEP_BY_STEP_SOLUTION
        3. Reference the original failed question for context
           ("The student struggled with [original Q]. Generate a similar
            but simpler practice problem focusing on [specific concept].")
        4. Tone: encouraging, educational (not evaluative)
        """
        # Build context from the original incorrect questions
        original_context = self._build_original_context(skill_weakness)
        
        prompt = langfuse.get_prompt("drill-generation", type="chat", label=LANGFUSE_PROMPT_LABEL)
        messages = prompt.compile(
            skill_name=skill_weakness.skill_name,
            difficulty=difficulty.value,
            original_question_context=original_context,
            avoid_topics=self._summarize_existing(existing_drills),
        )
        
        class DrillOutput(BaseModel):
            blocks: List[dict]
            answer: List[dict]
            hint: str
            step_by_step_solution: str
            learning_concept: str
        
        result = await run_llm_with_openai(
            model=openai_plan_to_model_name["text"],
            messages=messages,
            response_model=DrillOutput,
            max_output_tokens=4096,
        )
        
        return DrillQuestion(
            id=str(uuid.uuid4()),
            skill_id=skill_weakness.skill_id,
            skill_name=skill_weakness.skill_name,
            difficulty=difficulty,
            blocks=result.blocks,
            answer=result.answer,
            hint=result.hint,
            step_by_step_solution=result.step_by_step_solution,
            learning_concept=result.learning_concept,
        )

    def _adjust_difficulty(
        self,
        incorrect_questions: List[QuestionResult],
        preference: str,
    ) -> Difficulty:
        """
        Determine drill difficulty based on original failures.
        
        "easier": drop one level (hard→medium, medium→easy, easy→easy)
        "same": keep same difficulty
        "harder": raise one level (only if requested explicitly)
        """
        # Find the most common difficulty level among incorrect questions
        ...
        if preference == "easier":
            return self._one_level_down(most_common_difficulty)
        elif preference == "harder":
            return self._one_level_up(most_common_difficulty)
        return most_common_difficulty
```

**Data Models:**

```python
class DrillParams(BaseModel):
    """Input parameters for drill generation."""
    focus_skills: List[str] | None = None  # skill IDs to focus on
    max_questions: int = 5
    difficulty_preference: str = "easier"  # "easier" | "same" | "harder"

class DrillQuestion(BaseModel):
    """Single drill question — practice-only."""
    id: str
    skill_id: str
    skill_name: str
    difficulty: Difficulty
    blocks: List[dict]
    answer: List[dict]
    hint: str  # NOT present in assessment questions
    step_by_step_solution: str  # NOT present in assessment questions
    learning_concept: str  # What concept this drill reinforces

class DrillSession(BaseModel):
    """A complete drill session for a user."""
    id: str
    assessment_id: str
    user_id: int
    questions: List[DrillQuestion]
    weakness_profile: WeaknessProfile
    created_at: datetime
```

---

## 3. Data Flow

### 3.1 End-to-End Trainer Flow

```
┌──────────────┐
│ Assessment   │ (completed, scores available)
│ Results      │
└──────┬───────┘
       │
       ▼
┌──────────────┐   assessment + question_results   ┌───────────────┐
│ Facade       │ ─────────────────────────────────►│ profiler.py   │
│ (Module A)   │                                    │ build_profile │
└──────────────┘                                    └───────┬───────┘
                                                            │
                                                    WeaknessProfile
                                                            │
                                              ┌─────────────▼─────────────┐
                                              │ Module D (optional)       │
                                              │ Get time_logs for user    │
                                              └─────────────┬─────────────┘
                                                            │
                                                    Enhanced WeaknessProfile
                                                            │
                                              ┌─────────────▼─────────────┐
                                              │ generator.py              │
                                              │ generate_drills()         │◄── LLM calls
                                              └─────────────┬─────────────┘
                                                            │
                                                     DrillQuestion[] (streaming)
                                                            │
                                              ┌─────────────▼─────────────┐
                                              │ DB: persist               │
                                              │ trainer_sessions +        │
                                              │ trainer_questions         │
                                              └───────────────────────────┘
                                                            │
                                                     ┌──────▼──────┐
                                                     │ Frontend    │
                                                     │ (practice   │
                                                     │  UI)        │
                                                     └─────────────┘
```

### 3.2 Input → Output Contract

**Input (from Facade):**

| Parameter | Source | Description |
|---|---|---|
| `assessment_id` | URL path | Which assessment to generate drills for |
| `user_id` | URL path | Which candidate's results to use |
| `focus_skills` | Request body (optional) | Specific skill IDs to drill |
| `max_questions` | Request body (default 5) | Maximum drill questions to generate |
| `difficulty_preference` | Request body (default "easier") | Difficulty adjustment strategy |

**Output (streaming NDJSON):**

Each line is a progressive `List[DrillQuestion]` — the list grows as drills are generated.

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | `Assessment`, `AssessmentQuestion`, `SkillOntology`, `Difficulty` |
| `llm.py` | Existing | `run_llm_with_openai` for drill generation |
| `langfuse` | External | Prompt registry + tracing |
| `db/assessment.py` | Internal | Load assessment, store drill sessions |
| Module A (Facade) | Caller | Routes drill generation requests |
| Module B (Skill Intelligence) | Input | Skill ontology for understanding skill hierarchy |
| Module D (Integrity/Telemetry) | Optional Input | Time logs to enhance weakness profiling |

### 4.2 Langfuse Prompts Required

| Prompt Name | Type | Input Variables | Output Model |
|---|---|---|---|
| `drill-generation` | chat | `skill_name`, `difficulty`, `original_question_context`, `avoid_topics` | `DrillOutput` (blocks, answer, hint, step_by_step_solution, learning_concept) |

### 4.3 Edge Cases

| Edge Case | Handling |
|---|---|
| **Candidate got all questions correct** | Profile has no weaknesses. Return empty drill set with message: "Congratulations! No weak areas identified." |
| **All questions are for the same skill** | All drills will target that skill. Vary by sub-skill if ontology has sub-skills, or by difficulty level. |
| **focus_skills contains invalid skill ID** | Filter it out silently. If all IDs are invalid, fall back to top weaknesses. |
| **max_questions = 0** | Return empty list. No LLM calls. |
| **max_questions > 20** | Cap at 20. This prevents excessive LLM cost. |
| **Assessment has no skill ontology (Curriculum mode)** | Use question metadata `skill_tested` field directly. If no metadata, group by question topic (task title). |
| **Time logs not available** | Skip time-based severity boosting. Profiling still works — just with fewer signals. |
| **Original question blocks contain images** | Drill generation prompt cannot "see" images. Extract text-only content for context. Note the limitation in the prompt. |
| **LLM generates a drill identical to the original** | Check hash against original assessment questions + existing drills. If duplicate, regenerate (max 3 attempts). |
| **Non-comparative enforcement** | The database schema does NOT have a foreign key back to any leaderboard or scoring table. Drill results are isolated. The API does not expose any endpoint to compare drill scores across users. |

### 4.4 Database Tables Owned

| Table | Purpose |
|---|---|
| `trainer_sessions` | Drill session metadata (links to assessment + user, stores weakness profile) |
| `trainer_questions` | Individual drill questions within a session |

```sql
CREATE TABLE IF NOT EXISTS trainer_sessions (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    weakness_profile_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_trainer_session_assessment ON trainer_sessions (assessment_id);
CREATE INDEX idx_trainer_session_user ON trainer_sessions (user_id);
CREATE UNIQUE INDEX idx_trainer_session_compound ON trainer_sessions (assessment_id, user_id);

CREATE TABLE IF NOT EXISTS trainer_questions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    skill_id TEXT,
    skill_name TEXT,
    difficulty TEXT,
    blocks TEXT NOT NULL,
    answer TEXT,
    hint TEXT,
    step_by_step_solution TEXT,
    learning_concept TEXT,
    position INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (session_id) REFERENCES trainer_sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_trainer_question_session ON trainer_questions (session_id);
CREATE INDEX idx_trainer_question_skill ON trainer_questions (skill_id);
```

---

## 5. Non-Comparative Enforcement Checklist

This checklist ensures the "never used for comparative grading" mandate from the original brief:

- [ ] `trainer_sessions` and `trainer_questions` have NO foreign key to any scoring/leaderboard table
- [ ] No API endpoint that aggregates drill scores across users exists
- [ ] No API endpoint that includes drill results in assessment reports exists
- [ ] The `SkillReport` from Module B does NOT reference drill performance
- [ ] The frontend drill UI does NOT show comparisons with other candidates
- [ ] Drill data is visible ONLY to the candidate and their org admin (for learning path tracking, not grading)

---

## 6. Performance Considerations

- **Drill generation cost:** ~1 LLM call per drill question. For `max_questions=5`, that's 5 calls. Cheaper than QA simulation.
- **Profiling is pure computation** — no LLM needed. Fast.
- **Streaming is essential** — generating 5 drills takes time. Stream each as it's ready.
- **Cache weakness profiles** — if the same user requests drills multiple times for the same assessment, reuse the profile (assessment results don't change). Only regenerate drills.
- **Unique drill session per request** — each drill generation creates a new session. Previous sessions are kept for learning path analysis. No deduplication.
