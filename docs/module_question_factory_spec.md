# Module C: Question Factory — Specification

> **Priority:** T2 (Integrity & Scaling)  
> **Package:** `src/api/assessment/questions/`  
> **Owner Files:** `factory.py`, `diversity.py`, `variation.py`, `enrichment.py`  
> **Depends On:** Module A (called by engines), Module B (skill ontology input), `contracts.py`

---

## 1. Module Scope

This module handles all question generation, diversification, and metadata enrichment:

| Feature | Tier | Description |
|---|---|---|
| **Core Question Generation** | T1 | Generate assessment questions from skill ontology (JD mode) or curriculum context (Curriculum mode) |
| **Semantic Diversity Engine** | T2 | Use semantic similarity checks to ensure questions within the same assessment are structurally and contextually unique |
| **Dynamic Assessment Modes** | T2 | Three variation modes: `Static` (no variation), `Variable Swap` (change parameters), `Isomorphic Shuffle` (structurally different, same concept) |
| **Metadata Enrichment** | T2 | Every question includes: Skill Tested, Difficulty Reason, and Learning Objective |
| **LLM Flagging & Constraints** (Future Hook) | Future | Logical constraints to break standard LLM patterns — designed as an extensibility point |

**What this module does NOT do:**
- Does NOT determine which skills to test (Module B provides the ontology)
- Does NOT evaluate answers (existing `routes/ai.py` handles evaluation)
- Does NOT verify hashes or track time (Module D handles integrity)

---

## 2. Design Patterns & Internal Structure

### 2.1 Orchestrator — `factory.py`

The factory is the central orchestrator. It:
1. Receives generation context (skill ontology OR curriculum context)
2. Distributes question generation across skills based on weights
3. Applies the selected variation mode
4. Runs diversity checks and regenerates if needed
5. Attaches metadata enrichment
6. Returns the final question list

```python
# factory.py — Structural sketch

class QuestionFactory:
    """
    Orchestrates question generation, diversification, and enrichment.
    """

    def __init__(self):
        self._enricher = MetadataEnricher()
        self._diversity_checker = DiversityChecker()
        self._variation_strategies: dict[VariationMode, VariationStrategy] = {
            VariationMode.STATIC: StaticVariation(),
            VariationMode.VARIABLE_SWAP: VariableSwapVariation(),
            VariationMode.ISOMORPHIC_SHUFFLE: IsomorphicShuffleVariation(),
        }

    async def generate_questions(
        self,
        context: GenerationContext,
        skill_ontology: SkillOntology | None,
        variation_mode: VariationMode,
        target_count: int,
    ) -> AsyncGenerator[List[AssessmentQuestion], None]:
        """
        Main generation pipeline:

        1. PLAN: Determine question distribution across skills
           - If skill_ontology provided: distribute by weight
           - If curriculum context: distribute by milestone/task coverage
        
        2. GENERATE: For each skill/topic, generate base questions via LLM
        
        3. VARY: Apply variation_mode strategy to each question
        
        4. DIVERSIFY: Run semantic diversity check
           - If similarity > threshold (0.85), regenerate with diversity prompt
           - Max 3 regeneration attempts per question
        
        5. ENRICH: Attach metadata to each question
        
        6. YIELD: Stream questions as they are generated
        """
        plan = self._create_distribution_plan(skill_ontology, context, target_count)

        questions_so_far = []
        for skill_allocation in plan:
            for i in range(skill_allocation.count):
                # Generate
                base_q = await self._generate_single(skill_allocation, context)

                # Vary
                strategy = self._variation_strategies[variation_mode]
                varied_q = await strategy.apply(base_q, context)

                # Diversity check against all questions generated so far
                is_diverse = await self._diversity_checker.check_against(varied_q, questions_so_far)
                if not is_diverse:
                    varied_q = await self._regenerate_diverse(varied_q, questions_so_far, skill_allocation, context)

                # Enrich
                enriched_q = await self._enricher.enrich(varied_q, skill_allocation)

                questions_so_far.append(enriched_q)
                yield questions_so_far  # Stream progressive updates

    def _create_distribution_plan(
        self,
        skill_ontology: SkillOntology | None,
        context: GenerationContext,
        target_count: int,
    ) -> List[SkillAllocation]:
        """
        Distribute target_count questions across skills based on weights.
        
        Example: 10 questions, Python=0.5, React=0.3, Docker=0.2
        → Python: 5 questions, React: 3, Docker: 2
        
        Uses round-robin for remainders after integer division.
        Ensures every skill gets at least 1 question.
        """
        ...

    async def _generate_single(
        self,
        allocation: SkillAllocation,
        context: GenerationContext,
    ) -> AssessmentQuestion:
        """
        Single question generation via LLM.
        
        Prompt: 'question-generation' (Langfuse)
        Input: skill name, difficulty, context, question type
        Output: AssessmentQuestion (blocks, answer, type, etc.)
        Model: 'text' (gpt-4.1)
        """
        ...

    async def _regenerate_diverse(
        self,
        question: AssessmentQuestion,
        existing: List[AssessmentQuestion],
        allocation: SkillAllocation,
        context: GenerationContext,
        max_attempts: int = 3,
    ) -> AssessmentQuestion:
        """
        Regenerate a question to be more diverse.
        Includes existing question summaries in the prompt as "avoid" context.
        """
        ...
```

### 2.2 Strategy Pattern — `variation.py`

Three variation strategies, selectable via `variation_mode` in `assessment.json`:

```python
# variation.py — Structural sketch

from abc import ABC, abstractmethod

class VariationContext(BaseModel):
    """Context passed to variation strategies."""
    original_question: AssessmentQuestion | None = None
    skill_name: str
    difficulty: Difficulty
    existing_questions: List[AssessmentQuestion] = []

class VariationStrategy(ABC):
    @abstractmethod
    async def apply(
        self,
        base_question: AssessmentQuestion,
        context: GenerationContext,
    ) -> AssessmentQuestion:
        """Transform the base question according to the variation mode."""
        ...


class StaticVariation(VariationStrategy):
    """
    No modification. Returns the base question as-is.
    Use case: standardized tests where every candidate gets identical questions.
    """
    async def apply(self, base_question, context):
        base_question.metadata.variation_source = VariationMode.STATIC
        return base_question


class VariableSwapVariation(VariationStrategy):
    """
    Swap numerical values, variable names, or data points while
    keeping the problem structure identical.
    
    Example:
      Base: "Write a function that finds the 3rd largest element in a list of 10 integers."
      Swapped: "Write a function that finds the 2nd smallest element in a list of 8 integers."
    
    Implementation:
      - LLM call with prompt 'question-variable-swap'
      - Input: base question + instruction to change ONLY parameters
      - Preserves blocks structure, only modifies content text
    """
    async def apply(self, base_question, context):
        # LLM call to swap variables
        ...
        swapped.metadata.variation_source = VariationMode.VARIABLE_SWAP
        swapped.metadata.original_question_id = base_question.id
        return swapped


class IsomorphicShuffleVariation(VariationStrategy):
    """
    Generate a structurally different question that tests the SAME concept
    at the SAME difficulty level.
    
    Example:
      Base: "Implement a decorator that logs function execution time."
      Isomorphic: "Implement a decorator that caches function results with a TTL."
    
    Implementation:
      - LLM call with prompt 'question-isomorphic-shuffle'
      - Input: base question + skill context + instruction to create
        a fundamentally different problem testing the same concept
      - Full regeneration of blocks and answer
    """
    async def apply(self, base_question, context):
        # LLM call to generate isomorphic question
        ...
        shuffled.metadata.variation_source = VariationMode.ISOMORPHIC_SHUFFLE
        shuffled.metadata.original_question_id = base_question.id
        return shuffled
```

### 2.3 Semantic Diversity — `diversity.py`

```python
# diversity.py — Structural sketch

class DiversityChecker:
    """
    Ensures questions within an assessment are semantically diverse.
    Uses lightweight text similarity (NOT full embedding model initially).
    """

    SIMILARITY_THRESHOLD = 0.85  # Questions above this are "too similar"

    async def check_against(
        self,
        candidate: AssessmentQuestion,
        existing: List[AssessmentQuestion],
    ) -> bool:
        """
        Check if candidate is sufficiently different from all existing questions.
        Returns True if diverse enough, False if too similar.
        """
        if not existing:
            return True

        candidate_text = self._extract_text(candidate)
        for existing_q in existing:
            existing_text = self._extract_text(existing_q)
            similarity = self._compute_similarity(candidate_text, existing_text)
            if similarity > self.SIMILARITY_THRESHOLD:
                return False
        return True

    async def compute_diversity_score(self, questions: List[AssessmentQuestion]) -> float:
        """
        Compute overall diversity score for the assessment.
        0.0 = all identical, 1.0 = all maximally different.
        
        Returns: 1.0 - (average pairwise similarity)
        """
        ...

    def _extract_text(self, question: AssessmentQuestion) -> str:
        """
        Extract plaintext from question blocks for comparison.
        Strips formatting, code blocks, images — keeps only semantic content.
        Uses existing construct_description_from_blocks() from db/utils.py.
        """
        ...

    def _compute_similarity(self, text_a: str, text_b: str) -> float:
        """
        Lightweight similarity computation.
        
        Phase 1 (initial implementation): 
          - Use token-level Jaccard similarity (no external dependencies)
          - Fast, deterministic, good enough for MVP
        
        Phase 2 (future upgrade path):
          - Use sentence-transformers for true semantic similarity
          - Would require adding 'sentence-transformers' to pyproject.toml
          - Design the interface now so swap is seamless
        """
        # Phase 1: Jaccard similarity
        tokens_a = set(text_a.lower().split())
        tokens_b = set(text_b.lower().split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)
```

### 2.4 Metadata Enrichment — `enrichment.py`

```python
# enrichment.py — Structural sketch

class MetadataEnricher:
    """
    Attaches mandatory metadata to every generated question.
    Metadata fields from api_contracts.md:
      - skill_tested: {skill_id, skill_name, parent_skill}
      - difficulty_reason: why this difficulty level
      - learning_objective: what the question assesses
    """

    async def enrich(
        self,
        question: AssessmentQuestion,
        allocation: SkillAllocation,
    ) -> AssessmentQuestion:
        """
        Attach metadata. This may involve an LLM call for difficulty_reason
        and learning_objective if they weren't generated in the initial question
        generation step.
        
        Strategy:
        1. skill_tested → derived directly from SkillAllocation (no LLM needed)
        2. difficulty_reason → can be generated as part of the question generation
           prompt (co-generated). If missing, make a separate LLM call.
        3. learning_objective → same as above (co-generated preferred)
        
        Recommendation: Include difficulty_reason and learning_objective in the
        question generation prompt output model to avoid extra LLM calls.
        """
        question.metadata = QuestionMetadata(
            skill_tested=SkillReference(
                skill_id=allocation.skill_id,
                skill_name=allocation.skill_name,
                parent_skill=allocation.parent_skill_name,
            ),
            difficulty_reason=question.metadata.difficulty_reason or await self._generate_difficulty_reason(question),
            learning_objective=question.metadata.learning_objective or await self._generate_learning_objective(question),
            variation_source=question.metadata.variation_source,
            original_question_id=question.metadata.original_question_id,
        )
        return question

    # Also computes SHA-256 hash of question content for Module D
    def compute_hash(self, question: AssessmentQuestion) -> str:
        """
        SHA-256 of normalized question content.
        Normalization: lowercase, strip whitespace, remove formatting.
        Stored in question.hash field.
        """
        import hashlib
        text = self._extract_text(question).strip().lower()
        return hashlib.sha256(text.encode()).hexdigest()
```

---

## 3. Data Flow

### 3.1 Full Generation Pipeline

```
┌─────────────────┐
│ Engine          │  GenerationContext + SkillOntology + VariationMode + target_count
│ (Module A)      │──────────────────────────────────────┐
└─────────────────┘                                      │
                                                         ▼
                                              ┌─────────────────────┐
                                              │ factory.py          │
                                              │ _create_plan()      │
                                              └──────────┬──────────┘
                                                         │ SkillAllocation[]
                                                         ▼
                                              ┌─────────────────────┐
                                              │ factory.py          │
                                        ┌────►│ _generate_single()  │◄─── LLM Call
                                        │     └──────────┬──────────┘
                                        │                │ base AssessmentQuestion
                                        │                ▼
                                        │     ┌─────────────────────┐
                                        │     │ variation.py        │
                                        │     │ strategy.apply()    │◄─── LLM Call (swap/shuffle)
                                        │     └──────────┬──────────┘
                                        │                │ varied AssessmentQuestion
                                        │                ▼
                                        │     ┌─────────────────────┐
                                        │     │ diversity.py        │
                                        │     │ check_against()     │
                                        │     └──────┬───────┬──────┘
                                        │            │       │
                                        │      diverse?   too similar?
                                        │            │       │
                                        │            ▼       ▼
                                        │         proceed   regenerate (loop back, max 3x)
                                        │            │
                                        │            ▼
                                        │     ┌─────────────────────┐
                                        │     │ enrichment.py       │
                                        │     │ enrich()            │
                                        │     └──────────┬──────────┘
                                        │                │ enriched AssessmentQuestion
                                        │                ▼
                                        │         yield to caller
                                        │                │
                                        └────────────────┘ (loop for next skill allocation)
```

### 3.2 Input Model — `GenerationContext`

```python
class GenerationContext(BaseModel):
    """
    Context provided by the calling engine.
    One of curriculum_context or jd_context will be populated.
    """
    # Curriculum mode
    curriculum_context: Optional[CurriculumContext] = None

    # JD mode
    jd_context: Optional[JDContext] = None

    # Shared
    question_types: List[str] = ["objective", "subjective"]  # types to generate
    input_types: List[str] = ["code", "text"]  # input modalities
    difficulty_distribution: Dict[str, float] = {"easy": 0.3, "medium": 0.5, "hard": 0.2}

class CurriculumContext(BaseModel):
    course_name: str
    milestones: List[dict]  # existing milestone data with tasks
    existing_questions: List[dict]  # existing questions for reference (avoid duplication)
    scorecard_criteria: List[dict]  # existing scorecards for skill mapping

class JDContext(BaseModel):
    jd_text: str
    jd_seniority: str
    jd_domain: str
    skill_ontology: SkillOntology
```

### 3.3 Internal Models

```python
class SkillAllocation(BaseModel):
    """Plan for how many questions to generate per skill."""
    skill_id: str
    skill_name: str
    parent_skill_name: Optional[str]
    weight: float
    count: int  # number of questions to generate
    difficulty: Difficulty  # assigned difficulty for this allocation
    question_type: str  # "objective" or "subjective"
    input_type: str  # "code" or "text"
```

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | `AssessmentQuestion`, `QuestionMetadata`, `SkillReference`, `VariationMode`, `Difficulty` |
| `llm.py` | Existing | `stream_llm_with_openai`, `run_llm_with_openai` for question generation |
| `langfuse` | External | Prompts + tracing |
| `db/utils.py` | Existing | `construct_description_from_blocks()` for text extraction |
| Module B | Input | `SkillOntology` for JD-mode generation |
| Module A | Caller | Engines call this factory |

### 4.2 Langfuse Prompts Required

| Prompt Name | Type | Input Variables | Output Model |
|---|---|---|---|
| `question-generation` | chat | `skill_name`, `difficulty`, `question_type`, `input_type`, `context`, `avoid_topics` | `AssessmentQuestion` (partial — blocks, answer, difficulty_reason, learning_objective) |
| `question-variable-swap` | chat | `base_question_text`, `base_answer_text`, `instruction` | `AssessmentQuestion` (modified blocks + answer) |
| `question-isomorphic-shuffle` | chat | `base_question_summary`, `skill_name`, `difficulty`, `context` | `AssessmentQuestion` (entirely new blocks + answer) |
| `question-diversity-regen` | chat | `candidate_text`, `existing_summaries`, `skill_name`, `difficulty` | `AssessmentQuestion` (regenerated) |

### 4.3 Edge Cases

| Edge Case | Handling |
|---|---|
| **target_count = 0** | Return empty list. No LLM calls. |
| **target_count > 50** | Cap at 50 and warn. Prevents excessive LLM cost. |
| **Skill has weight = 0** | Skip that skill entirely in distribution plan. |
| **All skills have equal weight** | Distribute questions evenly (round-robin for remainders). |
| **Diversity check fails 3 times** | Accept the question with a `diversity_warning: true` flag. Log the issue. Don't block the entire generation. |
| **LLM generates question in wrong format** | Pydantic validation catches. Retry with `backoff`. After 5 retries, skip that question and reduce count. Log warning. |
| **Variable Swap produces identical question** | Compare hash before and after. If identical, fall back to Isomorphic Shuffle for that question. |
| **Curriculum mode: no existing questions** | Generate entirely new questions based on task/milestone content (blocks, learning material). |
| **Curriculum mode: scorecard → skill mapping** | Map each scorecard criterion name to a skill. If no explicit ontology, use criterion names as the skill names. This is a reasonable heuristic for non-JD mode. |
| **Question has code blocks** | Preserve code block formatting in `blocks`. Ensure hash computation normalizes code (strip comments, normalize whitespace). |

### 4.4 Database Tables Owned

| Table | Purpose |
|---|---|
| `question_metadata` | Enriched metadata per question (can also be stored inline in `assessment_json`, but a separate table enables querying) |
| `question_hashes` | SHA-256 hashes per generated question (used by Module D for copy-paste detection) |

```sql
CREATE TABLE IF NOT EXISTS question_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    skill_id TEXT,
    skill_name TEXT,
    parent_skill TEXT,
    difficulty TEXT,
    difficulty_reason TEXT,
    learning_objective TEXT,
    variation_source TEXT,
    original_question_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    UNIQUE(assessment_id, question_id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE INDEX idx_qmeta_assessment ON question_metadata (assessment_id);
CREATE INDEX idx_qmeta_skill ON question_metadata (skill_id);

CREATE TABLE IF NOT EXISTS question_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    hash TEXT NOT NULL,
    normalized_text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    UNIQUE(assessment_id, question_id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE INDEX idx_qhash_assessment ON question_hashes (assessment_id);
CREATE INDEX idx_qhash_hash ON question_hashes (hash);
```

---

## 5. Future Hook: LLM Flagging & Constraints

The `factory.py` orchestrator is designed to accept a **constraint injection middleware** in the future:

```python
# Future: constraint_injector.py (NOT implemented now)

class ConstraintInjector:
    """
    Injects logical constraints into LLM prompts to break
    standard LLM output patterns.
    
    Example constraints:
    - "The solution must NOT use recursion"
    - "The answer must involve exactly 2 loops"
    - "The function signature must use type hints"
    """
    def inject(self, prompt: str, constraints: List[ConstraintRule]) -> str:
        ...
```

**Preparation for coder:** The `AssessmentQuestion` model already has an optional `constraints` field (in `contracts.py`). When implementing `factory.py`, pass constraints to the generation prompt if present. For now, the field will always be `None`.

---

## 6. Performance Considerations

- **Parallel generation:** Questions for different skills can be generated concurrently. Use `asyncio.gather()` or `asyncio.TaskGroup` for parallel LLM calls within `factory.py`. However, diversity checks must be sequential (each new question checks against all previous ones).
- **Streaming:** Yield after each question is generated and enriched. Don't wait for all questions to be ready.
- **LLM call reduction:** Include `difficulty_reason` and `learning_objective` in the base generation prompt output model. This avoids 2 extra LLM calls per question in `enrichment.py`.
- **Hash precomputation:** Compute question hashes during enrichment (already in the pipeline). Store immediately to `question_hashes` table.
