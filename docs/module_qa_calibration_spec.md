# Module E: QA & Calibration — Specification

> **Priority:** T3 (Advanced Intelligence)  
> **Package:** `src/api/assessment/qa/`  
> **Owner Files:** `simulator.py`, `discriminator.py`, `calibration.py`  
> **Depends On:** Module A (called by Facade), `contracts.py`, `llm.py`

---

## 1. Module Scope

This module handles pre-live quality assurance and difficulty calibration:

| Feature | Tier | Description |
|---|---|---|
| **Simulated Candidate QA** | T3 | Pre-live testing via AI Personas (Beginner, Intermediate, Expert) to ensure each question is a "Good Discriminator." |
| **HR Calibration** | T3 | Auto-prompts HR to confirm if overall test difficulty aligns with the JD seniority. HR can approve, reject (with feedback), or request adjustment. |

**What this module does NOT do:**
- Does NOT generate questions (Module C)
- Does NOT modify questions based on QA results (the engines handle regeneration if QA fails)
- Does NOT block publication unilaterally (it provides data; the Facade manages status transitions)

---

## 2. Design Patterns & Internal Structure

### 2.1 Template Method — `simulator.py`

The QA simulation follows a standardized pipeline with customizable persona behavior.

```python
# simulator.py — Structural sketch

class PersonaSimulator:
    """
    Simulates AI candidate personas attempting the assessment.
    Each persona has a calibrated capability profile.
    """

    PERSONA_PROFILES = {
        PersonaLevel.BEGINNER: {
            "description": "A junior developer with 0-1 years of experience. "
                           "Knows basic syntax but struggles with design patterns, "
                           "edge cases, and algorithmic thinking.",
            "expected_score_range": (0, 35),
            "approach": "Often gives partially correct or naive solutions. "
                        "May misunderstand complex requirements.",
        },
        PersonaLevel.INTERMEDIATE: {
            "description": "A mid-level developer with 2-4 years of experience. "
                           "Comfortable with common patterns but may miss edge cases "
                           "or advanced optimizations.",
            "expected_score_range": (35, 70),
            "approach": "Produces working solutions but may lack optimal approaches. "
                        "Handles most cases but not all.",
        },
        PersonaLevel.EXPERT: {
            "description": "A senior developer with 5+ years of experience. "
                           "Deep understanding of concepts, writes clean, efficient code, "
                           "handles edge cases, and considers performance.",
            "expected_score_range": (70, 100),
            "approach": "Produces near-optimal solutions with proper error handling, "
                        "documentation, and edge case coverage.",
        },
    }

    async def simulate(
        self,
        assessment: Assessment,
        personas: List[PersonaLevel],
    ) -> AsyncGenerator[QAResults, None]:
        """
        Template Method pipeline:
        1. For each persona:
           a. Generate simulated answers for each question (LLM call)
           b. Evaluate simulated answers using the same AI evaluator
           c. Compute persona score
           d. Check if score falls within expected range
           e. Yield progressive results
        2. After all personas: compute discriminator score
        """
        results = QAResults(status=QAStatus.PENDING)

        for persona_level in personas:
            profile = self.PERSONA_PROFILES[persona_level]
            simulation = await self._simulate_persona(assessment, persona_level, profile)
            results.simulations.append(simulation)
            yield results  # Progressive streaming

        # Compute discriminator score after all simulations
        results.discriminator_score = DiscriminatorScorer.compute(results.simulations)
        results.status = QAStatus.PASSED if results.discriminator_score >= 0.6 else QAStatus.FAILED
        yield results  # Final result

    async def _simulate_persona(
        self,
        assessment: Assessment,
        persona_level: PersonaLevel,
        profile: dict,
    ) -> PersonaSimulation:
        """
        Simulate one persona attempting all questions.
        
        For each question:
        1. LLM call with persona prompt: "You are a {level} developer. 
           Answer this question as such a developer would."
        2. LLM returns: simulated answer + reasoning
        3. Evaluate the simulated answer against the question's correct answer/scorecard
        4. Record per-question score
        5. Compute average score for this persona
        """
        question_scores = []

        for question in assessment.questions:
            # Step 1: Generate simulated answer
            simulated_answer = await self._generate_persona_answer(
                question, persona_level, profile
            )

            # Step 2: Evaluate simulated answer
            score = await self._evaluate_answer(question, simulated_answer)
            question_scores.append(score)

        avg_score = sum(question_scores) / len(question_scores) if question_scores else 0
        expected_min, expected_max = profile["expected_score_range"]
        is_discriminating = expected_min <= avg_score <= expected_max

        notes = self._generate_simulation_notes(
            persona_level, avg_score, expected_min, expected_max, question_scores
        )

        return PersonaSimulation(
            persona=persona_level,
            expected_score_range=[expected_min, expected_max],
            simulated_score=round(avg_score, 1),
            is_discriminating=is_discriminating,
            notes=notes,
        )

    async def _generate_persona_answer(
        self,
        question: AssessmentQuestion,
        persona_level: PersonaLevel,
        profile: dict,
    ) -> str:
        """
        LLM call to generate a persona-appropriate answer.
        
        Prompt: 'qa-persona-answer' (Langfuse)
        Input: question_text, persona_description, persona_approach
        Output: { answer: str, reasoning: str }
        Model: 'text' (gpt-4.1)
        """
        ...

    async def _evaluate_answer(
        self,
        question: AssessmentQuestion,
        answer: str,
    ) -> float:
        """
        Evaluate the simulated answer.
        
        For objective questions: compare against correct answer → 0 or 100
        For subjective questions: use the existing AI evaluation approach
          (scorecard-based scoring via run_llm_with_openai)
        
        Returns: score as a percentage (0-100)
        """
        ...

    def _generate_simulation_notes(
        self, persona, score, exp_min, exp_max, per_question
    ) -> str:
        """
        Rule-based notes generation.
        Example: "Beginner scored 45% (expected 0-35%). Questions Q3, Q5 were 
                  too easy for differentiation."
        """
        if score < exp_min:
            return f"{persona.value.title()} scored below expected range. Questions may be too hard."
        if score > exp_max:
            hard_qs = [i+1 for i, s in enumerate(per_question) if s < 50]
            return f"{persona.value.title()} scored above expected range. Consider harder questions for: Q{', Q'.join(map(str, hard_qs))}."
        easy_qs = [i+1 for i, s in enumerate(per_question) if s >= 90]
        hard_qs = [i+1 for i, s in enumerate(per_question) if s < 20]
        notes = f"{persona.value.title()} performed within expected range."
        if easy_qs:
            notes += f" Easy questions: Q{', Q'.join(map(str, easy_qs))}."
        if hard_qs:
            notes += f" Struggled with: Q{', Q'.join(map(str, hard_qs))}."
        return notes
```

### 2.2 Discriminator Scoring — `discriminator.py`

```python
# discriminator.py — Structural sketch

class DiscriminatorScorer:
    """
    Measures how well the assessment separates different skill levels.
    A "Good Discriminator" produces clearly separated scores for
    Beginner, Intermediate, and Expert.
    """

    @staticmethod
    def compute(simulations: List[PersonaSimulation]) -> float:
        """
        Discriminator score = f(score separation between personas)
        
        Algorithm:
        1. Sort simulations by expected skill level: beginner < intermediate < expert
        2. Check monotonicity: beginner_score < intermediate_score < expert_score
        3. Compute gap ratios:
           - gap_1 = intermediate_score - beginner_score
           - gap_2 = expert_score - intermediate_score
        4. Score = average(gap_1, gap_2) / max_possible_gap * monotonicity_bonus
        
        max_possible_gap = 50 (ideal: beginner=10, intermediate=50, expert=90 → gaps of 40 each)
        monotonicity_bonus = 1.0 if strictly monotonic, 0.5 if not
        
        Returns: 0.0 (no discrimination) to 1.0 (perfect discrimination)
        """
        if len(simulations) < 2:
            return 0.0

        # Sort by persona level
        level_order = {PersonaLevel.BEGINNER: 0, PersonaLevel.INTERMEDIATE: 1, PersonaLevel.EXPERT: 2}
        sorted_sims = sorted(simulations, key=lambda s: level_order.get(s.persona, 0))

        scores = [s.simulated_score for s in sorted_sims if s.simulated_score is not None]
        if len(scores) < 2:
            return 0.0

        # Check monotonicity
        is_monotonic = all(scores[i] < scores[i+1] for i in range(len(scores)-1))
        monotonicity_bonus = 1.0 if is_monotonic else 0.5

        # Compute gaps
        gaps = [scores[i+1] - scores[i] for i in range(len(scores)-1)]
        avg_gap = sum(gaps) / len(gaps)

        # Normalize: ideal gap is ~30 points between each level
        max_possible_gap = 30.0
        normalized_gap = min(avg_gap / max_possible_gap, 1.0)

        return round(normalized_gap * monotonicity_bonus, 2)
```

### 2.3 Mediator Pattern — `calibration.py`

The calibration module mediates the HR ↔ System feedback loop. It generates the calibration prompt and processes the HR response.

```python
# calibration.py — Structural sketch

class HRCalibrator:
    """
    HR Calibration flow:
    1. System generates a calibration prompt based on the assessment
    2. HR reviews and responds (approve / reject with feedback)
    3. System records the calibration decision
    """

    async def generate_calibration_prompt(
        self,
        assessment: Assessment,
    ) -> CalibrationPrompt:
        """
        Generate a structured prompt for HR review.
        
        Includes:
        - Assessment summary (question count, difficulty breakdown, skills covered)
        - Confidence score
        - QA simulation results (if available)
        - Source JD seniority vs actual difficulty distribution
        - Specific questions/areas that might need review
        
        This is NOT an LLM call — it's rule-based data aggregation.
        """
        difficulty_counts = {"easy": 0, "medium": 0, "hard": 0}
        for q in assessment.questions:
            difficulty_counts[q.difficulty.value] += 1

        skills_tested = set()
        for q in assessment.questions:
            if q.metadata and q.metadata.skill_tested:
                skills_tested.add(q.metadata.skill_tested.skill_name)

        return CalibrationPrompt(
            assessment_id=assessment.id,
            total_questions=len(assessment.questions),
            difficulty_breakdown=difficulty_counts,
            skills_tested=list(skills_tested),
            confidence_score=assessment.confidence_score,
            qa_results_summary=self._summarize_qa(assessment.qa_results),
            source_seniority=self._infer_seniority(assessment),
            review_questions=[
                f"Does the difficulty level (Easy: {difficulty_counts['easy']}, "
                f"Medium: {difficulty_counts['medium']}, Hard: {difficulty_counts['hard']}) "
                f"match the expected seniority for this role?",
                f"Are the skills tested ({', '.join(list(skills_tested)[:5])}...) "
                f"aligned with the job requirements?",
                "Are there any skills that should be tested but aren't?",
            ],
        )

    async def process_calibration(
        self,
        assessment_id: str,
        user_id: int,
        approved: bool,
        feedback: str | None,
        difficulty_alignment: str | None,
    ) -> HRCalibration:
        """
        Record HR's decision.
        
        If approved: update assessment.hr_calibration.status = APPROVED
        If rejected: update status = REJECTED, store feedback
        
        Status transitions are handled by the Facade, not here.
        This module only records the decision.
        """
        calibration = HRCalibration(
            status=CalibrationStatus.APPROVED if approved else CalibrationStatus.REJECTED,
            calibrated_by=user_id,
            calibrated_at=datetime.utcnow(),
            feedback=feedback,
            difficulty_alignment=difficulty_alignment,
        )

        await self._persist_calibration(assessment_id, calibration)
        return calibration


class CalibrationPrompt(BaseModel):
    """Structured data sent to the frontend for HR review."""
    assessment_id: str
    total_questions: int
    difficulty_breakdown: dict
    skills_tested: List[str]
    confidence_score: ConfidenceScore | None
    qa_results_summary: str | None
    source_seniority: str | None
    review_questions: List[str]
```

---

## 3. Data Flow

### 3.1 QA Simulation Flow

```
┌──────────┐   POST /assessment/{id}/qa/simulate   ┌───────────────┐
│ Admin    │ ────────────────────────────────────► │ Facade        │
│ (Frontend)   { personas: ["beginner", ...] }     │ (Module A)    │
└──────────┘                                        └───────┬───────┘
                                                            │
                                                     ┌──────▼───────┐
                                                     │ simulator.py │
                                                     └──────┬───────┘
                                                            │
                                          For each persona, for each question:
                                                            │
                                    ┌───────────────────────┼───────────────────────┐
                                    ▼                       ▼                       ▼
                             ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
                             │ LLM: Generate│        │ LLM: Generate│        │ LLM: Generate│
                             │ Beginner     │        │ Intermediate│         │ Expert       │
                             │ answer       │        │ answer      │         │ answer       │
                             └──────┬──────┘         └──────┬──────┘         └──────┬──────┘
                                    │                       │                       │
                                    ▼                       ▼                       ▼
                             ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
                             │ Evaluate    │         │ Evaluate    │         │ Evaluate    │
                             │ answer      │         │ answer      │         │ answer      │
                             └──────┬──────┘         └──────┬──────┘         └──────┬──────┘
                                    │                       │                       │
                                    └───────────┬───────────┘                       │
                                                │                                   │
                                         ┌──────▼──────┐                            │
                                         │discriminator │◄───────────────────────────┘
                                         │.compute()    │
                                         └──────┬──────┘
                                                │
                                          QAResults (streaming)
                                                │
                                         ┌──────▼──────┐
                                         │ DB: persist │
                                         └─────────────┘
```

### 3.2 HR Calibration Flow

```
┌──────────┐   GET /assessment/{id}         ┌──────────────┐
│ HR User  │ ◄──────────────────────────── │ Facade       │
│          │   assessment + calibration_    │              │
│          │   prompt (generated)           └──────────────┘
│          │
│          │   POST /assessment/{id}/calibrate
│          │   { approved: true/false, feedback: "..." }
│          │ ─────────────────────────────► ┌──────────────┐
└──────────┘                                │ calibration. │
                                            │ py           │
                                            │ process_     │
                                            │ calibration()│
                                            └──────┬───────┘
                                                   │
                                            Updates assessment
                                            status via Facade
```

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | `QAResults`, `PersonaSimulation`, `HRCalibration`, `CalibrationStatus`, `PersonaLevel` |
| `llm.py` | Existing | `run_llm_with_openai` for persona answer generation and evaluation |
| `langfuse` | External | Prompt registry + tracing for persona simulation |
| `db/assessment.py` | Internal | Storage for QA results and calibration logs |
| Module A (Facade) | Caller | Facade orchestrates the QA and calibration flows |

### 4.2 Langfuse Prompts Required

| Prompt Name | Type | Input Variables | Output Model |
|---|---|---|---|
| `qa-persona-answer` | chat | `question_text`, `persona_description`, `persona_approach`, `skill_name` | `{ answer: str, reasoning: str }` |
| `qa-evaluate-answer` | chat | `question_text`, `correct_answer_or_scorecard`, `candidate_answer` | `{ score: float, feedback: str }` |

### 4.3 Edge Cases

| Edge Case | Handling |
|---|---|
| **Assessment has 0 questions** | Return `QAResults(status=SKIPPED, discriminator_score=0.0)` with note "No questions to simulate." |
| **Persona answer generation fails** | Retry with backoff. After max retries, mark that persona as `simulated_score: None, is_discriminating: None, notes: "Simulation failed"`. Don't block other personas. |
| **All personas score similarly** | Discriminator score will be low (< 0.3). Status = FAILED. Notes suggest increasing difficulty variance. |
| **Expert scores lower than Beginner** | Monotonicity check fails → `monotonicity_bonus = 0.5`. Notes flag the inversion explicitly. |
| **HR calibration timeout** | The system does NOT auto-approve. Assessment stays in `CALIBRATION_PENDING` indefinitely until HR acts. The Facade can provide a "skip calibration" endpoint for admins. |
| **HR rejects multiple times** | Each rejection is logged. Assessment cycles: `CALIBRATION_PENDING → REJECTED → GENERATED (regenerate) → CALIBRATION_PENDING`. No limit on cycles. |
| **QA simulation is expensive** | For a 10-question assessment × 3 personas = 30 LLM generation calls + 30 evaluation calls = 60 LLM calls. Warn the user about cost/time. Consider allowing per-question opt-in. |

### 4.4 Database Tables Owned

| Table | Purpose |
|---|---|
| `qa_simulation_results` | Per-assessment QA run (stores the full `QAResults` JSON + individual persona results) |
| `hr_calibration_log` | HR approval/rejection records with timestamps and feedback |

```sql
CREATE TABLE IF NOT EXISTS qa_simulation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    discriminator_score REAL,
    results_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE INDEX idx_qa_result_assessment ON qa_simulation_results (assessment_id);

CREATE TABLE IF NOT EXISTS hr_calibration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    calibrated_by INTEGER NOT NULL,
    status TEXT NOT NULL,
    feedback TEXT,
    difficulty_alignment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (calibrated_by) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_calibration_assessment ON hr_calibration_log (assessment_id);
CREATE INDEX idx_calibration_user ON hr_calibration_log (calibrated_by);
```

---

## 5. Performance Considerations

- **QA simulation is the most expensive operation** in the entire suite. For large assessments, consider:
  - Allowing partial simulation (subset of questions)
  - Caching persona answer patterns for common skill types
  - Running persona simulations concurrently via `asyncio.gather()` (3 personas in parallel)
- **Per-question parallelization within a persona** is also safe since questions are independent.
- **Calibration is cheap** — it's mostly data aggregation and DB writes. No performance concerns.
- **Cache QA results** — don't re-run simulation if the assessment hasn't changed since last QA. Check `assessment.updated_at` before running.
