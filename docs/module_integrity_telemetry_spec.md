# Module D: Integrity & Telemetry — Specification

> **Priority:** T1 (Time Logs, Confidence) + T2 (Hash Verification)  
> **Package:** `src/api/assessment/integrity/`  
> **Owner Files:** `telemetry.py`, `confidence.py`, `hash_verify.py`  
> **Depends On:** Module A (called by Facade and engines), Module C (question hashes), `contracts.py`

---

## 1. Module Scope

This module handles assessment integrity enforcement and behavioral telemetry:

| Feature | Tier | Description |
|---|---|---|
| **Granular Time Logs** | T1 | Track "Thinking Time" vs. "Typing Time" per question. Frontend emits keystroke session events; backend ingests and stores them. |
| **Leaderboard Confidence Score** | T1 | A percentage-based audit of how well the generated assessment maps to the source JD or curriculum. |
| **Hash Verification** | T2 | SHA-256 hashing of submissions for instant, low-compute identification of copy-pasted solutions between candidates. |
| **Intuition Check** (Future Hook) | Future | Pre-code logic capture detecting plan↔code mismatches. |

**What this module does NOT do:**
- Does NOT generate questions (Module C)
- Does NOT evaluate answers (existing AI routes handle this)
- Does NOT modify or block submissions (it only detects and flags — downstream decisions are left to the caller)

---

## 2. Design Patterns & Internal Structure

### 2.1 Observer Pattern — `telemetry.py`

The telemetry subsystem follows the Observer pattern: the frontend emits events, and this module ingests them without coupling to the event producer.

```python
# telemetry.py — Structural sketch

class TelemetryEvent(BaseModel):
    """Single telemetry event from the frontend."""
    event_type: str  # "keystroke_session", "focus_change", "plan_capture" (future)
    assessment_id: str
    question_id: int
    user_id: int
    thinking_time_ms: int = 0
    typing_time_ms: int = 0
    idle_periods: List[IdlePeriod] = []
    timestamp: datetime

class IdlePeriod(BaseModel):
    start_ms: int
    end_ms: int

class TelemetryIngester:
    """
    Batch ingestion and storage of telemetry events.
    """

    async def ingest(self, events: List[TelemetryEvent]) -> IngestResult:
        """
        Validate and persist telemetry events.
        
        Validation rules:
        1. assessment_id must reference an existing assessment
        2. question_id must exist within that assessment
        3. user_id must exist
        4. thinking_time_ms and typing_time_ms must be >= 0
        5. timestamp must not be in the future (allow 5-second clock skew tolerance)
        6. Duplicate events (same assessment + question + user + timestamp) are silently dropped
        
        Returns: { accepted: int, rejected: int, rejection_reasons: List[str] }
        """
        accepted = 0
        rejected = 0
        reasons = []

        for event in events:
            try:
                self._validate(event)
                await self._persist(event)
                accepted += 1
            except ValidationError as e:
                rejected += 1
                reasons.append(str(e))

        return IngestResult(accepted=accepted, rejected=rejected, rejection_reasons=reasons)

    async def get_time_logs(
        self,
        assessment_id: str,
        user_id: int,
    ) -> TimeLogReport:
        """
        Aggregate telemetry data for a specific user's assessment session.
        
        Returns:
        - Per-question breakdown: thinking_time, typing_time, total_time, idle_count, longest_idle
        - Aggregate: total_thinking, total_typing, thinking_ratio
        """
        events = await self._load_events(assessment_id, user_id)
        per_question = self._aggregate_per_question(events)
        aggregate = self._compute_aggregate(per_question)
        return TimeLogReport(
            assessment_id=assessment_id,
            user_id=user_id,
            per_question=per_question,
            aggregate=aggregate,
        )

    def _aggregate_per_question(self, events: List[TelemetryEvent]) -> List[QuestionTimeLogs]:
        """
        Group events by question_id.
        Sum thinking_time_ms and typing_time_ms per question.
        Count idle_periods and find the longest idle.
        
        If multiple keystroke_session events exist for the same question
        (e.g., user left and came back), sum them all.
        """
        ...

    def _compute_aggregate(self, per_question: List[QuestionTimeLogs]) -> AggregateTimeLogs:
        """
        Compute totals across all questions.
        thinking_ratio = total_thinking / (total_thinking + total_typing)
        """
        ...
```

**Output Models:**

```python
class QuestionTimeLogs(BaseModel):
    question_id: int
    thinking_time_ms: int
    typing_time_ms: int
    total_time_ms: int
    idle_count: int
    longest_idle_ms: int

class AggregateTimeLogs(BaseModel):
    total_thinking_ms: int
    total_typing_ms: int
    thinking_ratio: float  # 0.0 to 1.0

class TimeLogReport(BaseModel):
    assessment_id: str
    user_id: int
    per_question: List[QuestionTimeLogs]
    aggregate: AggregateTimeLogs

class IngestResult(BaseModel):
    accepted: int
    rejected: int
    rejection_reasons: List[str] = []
```

### 2.2 Pure Computation — `confidence.py`

The confidence score measures how well an assessment maps to its source. This is computed post-generation, **not** at runtime.

```python
# confidence.py — Structural sketch

class ConfidenceCalculator:
    """
    Computes the Leaderboard Confidence Score for an assessment.
    """

    async def compute(
        self,
        assessment: Assessment,
    ) -> ConfidenceScore:
        """
        Compute confidence score based on three dimensions:
        
        1. SKILL COVERAGE (weight: 0.5)
           - JD mode: (skills with at least 1 question) / (total skills in ontology)
           - Curriculum mode: (tasks/topics with at least 1 question) / (total tasks in source)
        
        2. DIFFICULTY DISTRIBUTION (weight: 0.3)
           - Compare actual difficulty distribution to expected distribution
           - Expected depends on seniority:
             - Junior: {easy: 0.5, medium: 0.35, hard: 0.15}
             - Mid: {easy: 0.3, medium: 0.5, hard: 0.2}
             - Senior: {easy: 0.15, medium: 0.35, hard: 0.5}
           - Score = 1.0 - KL_divergence(actual, expected) clamped to [0, 1]
        
        3. QUESTION DIVERSITY (weight: 0.2)
           - Average pairwise diversity score from Module C's DiversityChecker
        
        OVERALL = 0.5 * skill_coverage + 0.3 * difficulty_distribution + 0.2 * diversity_score
        """
        skill_coverage = self._compute_skill_coverage(assessment)
        difficulty_dist = self._compute_difficulty_distribution(assessment)
        diversity = await self._compute_diversity(assessment)

        overall = 0.5 * skill_coverage + 0.3 * difficulty_dist + 0.2 * diversity

        reasoning = self._generate_reasoning(
            skill_coverage, difficulty_dist, diversity, assessment
        )

        return ConfidenceScore(
            overall=round(overall, 2),
            skill_coverage=round(skill_coverage, 2),
            difficulty_distribution=round(difficulty_dist, 2),
            reasoning=reasoning,
        )

    def _compute_skill_coverage(self, assessment: Assessment) -> float:
        """
        Fraction of ontology skills that have at least one question.
        Includes sub-skills: if parent has coverage but child doesn't,
        partial credit (0.5 per uncovered sub-skill).
        """
        if not assessment.skill_ontology:
            # Curriculum mode: skill coverage is based on source task coverage
            return self._compute_curriculum_coverage(assessment)

        all_skill_ids = set()
        for skill in assessment.skill_ontology.skills:
            all_skill_ids.add(skill.id)
            for sub in skill.sub_skills:
                all_skill_ids.add(sub.id)

        tested_skill_ids = set()
        for q in assessment.questions:
            if q.metadata and q.metadata.skill_tested:
                tested_skill_ids.add(q.metadata.skill_tested.skill_id)

        if not all_skill_ids:
            return 0.0

        return len(tested_skill_ids & all_skill_ids) / len(all_skill_ids)

    def _compute_difficulty_distribution(self, assessment: Assessment) -> float:
        """
        Compare actual vs expected difficulty distribution.
        Uses Jensen-Shannon divergence (symmetric) instead of KL divergence.
        """
        actual = {"easy": 0, "medium": 0, "hard": 0}
        for q in assessment.questions:
            actual[q.difficulty.value] = actual.get(q.difficulty.value, 0) + 1

        total = sum(actual.values()) or 1
        actual_dist = {k: v / total for k, v in actual.items()}

        # Infer expected from source
        expected = self._get_expected_distribution(assessment)

        # JS divergence (simplified)
        import math
        score = 0.0
        for key in ["easy", "medium", "hard"]:
            p = actual_dist.get(key, 0.001)
            q = expected.get(key, 0.001)
            m = (p + q) / 2
            if p > 0 and m > 0:
                score += p * math.log(p / m)
            if q > 0 and m > 0:
                score += q * math.log(q / m)
        score /= 2  # JS = average of two KL terms

        # Convert divergence to similarity: 1.0 = perfect match
        return max(0.0, 1.0 - score)

    def _generate_reasoning(self, skill_cov, diff_dist, diversity, assessment) -> str:
        """
        Human-readable explanation. Rule-based (no LLM needed).
        """
        parts = []
        total_skills = 0
        if assessment.skill_ontology:
            total_skills = sum(1 + len(s.sub_skills) for s in assessment.skill_ontology.skills)
        tested = int(skill_cov * total_skills) if total_skills else 0
        parts.append(f"Covers {tested}/{total_skills} extracted skills.")

        if diff_dist < 0.7:
            parts.append("Difficulty distribution is skewed from optimal for the inferred seniority level.")
        else:
            parts.append("Difficulty distribution aligns well with the target seniority.")

        if diversity < 0.7:
            parts.append("Some questions are semantically similar; consider diversifying.")

        return " ".join(parts)
```

### 2.3 Decorator Pattern — `hash_verify.py`

Hash verification wraps the submission flow non-invasively. It can be called before or after evaluation — it doesn't block the flow, only flags.

```python
# hash_verify.py — Structural sketch

import hashlib

class SubmissionHashVerifier:
    """
    SHA-256 based duplicate submission detection.
    """

    async def compute_and_check(
        self,
        assessment_id: str,
        question_id: int,
        user_id: int,
        answer_text: str,
    ) -> HashVerificationResult:
        """
        1. Normalize the answer text
        2. Compute SHA-256 hash
        3. Store hash → submission_hashes table
        4. Check against all other hashes for the same assessment + question
        5. Return result with match details
        """
        normalized = self._normalize(answer_text)
        hash_value = self._hash(normalized)

        # Store this submission's hash
        await self._store_hash(assessment_id, question_id, user_id, hash_value)

        # Check for duplicates (same assessment + question, different user)
        matches = await self._find_matches(assessment_id, question_id, user_id, hash_value)

        return HashVerificationResult(
            hash=f"sha256:{hash_value}",
            is_duplicate=len(matches) > 0,
            matched_submissions=matches,
        )

    def _normalize(self, text: str) -> str:
        """
        Normalize answer text for fair comparison:
        1. Strip leading/trailing whitespace
        2. Collapse multiple whitespace to single space
        3. Remove comments (for code: // /* */ # ''')
        4. Lowercase
        5. Remove blank lines
        
        This ensures trivial formatting differences don't produce different hashes.
        """
        import re
        text = text.strip().lower()
        text = re.sub(r'#.*$', '', text, flags=re.MULTILINE)  # Python comments
        text = re.sub(r'//.*$', '', text, flags=re.MULTILINE)  # JS comments
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)  # Block comments
        text = re.sub(r'\s+', ' ', text)  # Collapse whitespace
        return text

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    async def _store_hash(self, assessment_id, question_id, user_id, hash_value):
        """INSERT OR REPLACE into submission_hashes."""
        ...

    async def _find_matches(self, assessment_id, question_id, current_user_id, hash_value):
        """
        SELECT from submission_hashes 
        WHERE assessment_id = ? AND question_id = ? AND hash = ? AND user_id != ?
        
        Returns list of {user_id, submitted_at, similarity: 1.0}
        """
        ...


class HashVerificationResult(BaseModel):
    hash: str
    is_duplicate: bool
    matched_submissions: List[MatchedSubmission] = []

class MatchedSubmission(BaseModel):
    user_id: int
    question_id: int
    submitted_at: datetime
    similarity: float  # 1.0 for exact hash match
```

---

## 3. Data Flow

### 3.1 Telemetry Ingestion Flow

```
┌──────────────┐   POST /assessment/telemetry    ┌──────────────┐
│ Frontend     │ ──────────────────────────────► │ telemetry.py │
│ (JavaScript  │   { events: [...] }              │ ingest()     │
│  keylogger)  │                                  └──────┬───────┘
└──────────────┘                                         │
                                                         ▼
                                                  ┌──────────────┐
                                                  │ assessment_  │
                                                  │ telemetry    │
                                                  │ (DB table)   │
                                                  └──────────────┘
```

Frontend implementation note (for `coder_guidelines.md` compliance):
- The frontend must track keydown/keyup events per question
- `thinking_time_ms` = time from question display to first keystroke
- `typing_time_ms` = total duration of keystroke activity
- `idle_periods` = gaps > 5 seconds between keystrokes
- Events are batched and sent on question transition or every 30 seconds (whichever is first)

### 3.2 Confidence Scoring Flow (at generation time)

```
┌──────────────┐   assessment (with questions + ontology)   ┌────────────────┐
│ Engine       │ ────────────────────────────────────────► │ confidence.py  │
│ (Module A)   │                                            │ compute()      │
└──────────────┘                                            └──────┬─────────┘
                                                                   │
                                                            ConfidenceScore
                                                                   │
                                                            Written into
                                                            assessment.json
```

### 3.3 Hash Verification Flow (at submission time)

```
┌──────────────┐   POST /assessment/verify-hash    ┌────────────────┐
│ Submission   │ ─────────────────────────────────►│ hash_verify.py │
│ handler      │   { answer_text, question_id }     │ compute_and_   │
│ (route)      │                                    │ check()        │
└──────────────┘                                    └──────┬─────────┘
                                                           │
                                              ┌────────────┼────────────┐
                                              ▼            ▼            ▼
                                         store hash   check matches   return result
                                              │            │
                                         ┌────▼────┐  ┌───▼──────┐
                                         │submission│  │submission│
                                         │_hashes   │  │_hashes   │
                                         │(INSERT)  │  │(SELECT)  │
                                         └─────────┘  └──────────┘
```

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | `ConfidenceScore`, `Assessment` models |
| `db/assessment.py` | Internal | Storage for telemetry events, submission hashes |
| Module C | Input | `DiversityChecker.compute_diversity_score()` for confidence calculation |
| Module A | Caller | Facade routes telemetry + hash verification calls here |

**NO LLM DEPENDENCY.** This entire module is computational — no LLM calls needed. This makes it fast, deterministic, and cheap.

### 4.2 Edge Cases

| Edge Case | Handling |
|---|---|
| **Telemetry events arrive out of order** | Sort by `timestamp` during aggregation. Don't reject out-of-order events. |
| **Telemetry for non-existent assessment** | Reject with `validation_error`. Return in `rejection_reasons`. |
| **thinking_time_ms = 0** | Valid — candidate started typing immediately. Don't reject. |
| **Very large thinking_time (> 1 hour)** | Accept but flag. Could indicate the candidate left and came back. Include in `idle_periods` analysis. |
| **Identical hash from same user** | This is a re-submission, not cheating. Filter: only flag if `user_id != current_user_id`. |
| **Hash collision** | SHA-256 collision is astronomically unlikely. If it occurs, the `matched_submissions` will include a false positive. This is acceptable — human review is the final arbiter. |
| **Empty answer text** | Compute hash of empty string. This will match other empty submissions — that's correct behavior (both submitted nothing). |
| **Code answer with different variable names** | Current normalization does NOT handle semantic equivalence. The hash will differ. This is by design — SHA-256 is for exact/near-exact copy detection. Semantic plagiarism detection is out of scope and can be a future enhancement. |
| **Confidence score for assessment with 0 questions** | Return `{ overall: 0.0, skill_coverage: 0.0, ... , reasoning: "No questions generated." }` |
| **Confidence on curriculum mode with no ontology** | Use task coverage instead of skill coverage. Coverage = (source tasks with questions) / (total source tasks). |

### 4.3 Database Tables Owned

| Table | Purpose |
|---|---|
| `assessment_telemetry` | Keystroke session events (thinking/typing time per question per user) |
| `submission_hashes` | SHA-256 hashes of candidate submissions for copy-paste detection |

```sql
CREATE TABLE IF NOT EXISTS assessment_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'keystroke_session',
    thinking_time_ms INTEGER NOT NULL DEFAULT 0,
    typing_time_ms INTEGER NOT NULL DEFAULT 0,
    idle_periods TEXT,  -- JSON array of {start_ms, end_ms}
    timestamp DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_telemetry_assessment ON assessment_telemetry (assessment_id);
CREATE INDEX idx_telemetry_user ON assessment_telemetry (user_id);
CREATE INDEX idx_telemetry_question ON assessment_telemetry (question_id);
CREATE INDEX idx_telemetry_compound ON assessment_telemetry (assessment_id, user_id, question_id);

CREATE TABLE IF NOT EXISTS submission_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    hash TEXT NOT NULL,
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    UNIQUE(assessment_id, question_id, user_id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_subhash_assessment ON submission_hashes (assessment_id);
CREATE INDEX idx_subhash_hash ON submission_hashes (hash);
CREATE INDEX idx_subhash_compound ON submission_hashes (assessment_id, question_id, hash);
```

---

## 5. Future Hook: Intuition Check

The telemetry table already supports `event_type = "plan_capture"` for the future Intuition Check feature:

```python
# Future: intuition_check.py (NOT implemented now)

class IntuitionChecker:
    """
    Compares a candidate's pre-code stated plan with their actual code.
    Uses the telemetry table with event_type = 'plan_capture'.
    """

    async def capture_plan(self, assessment_id, question_id, user_id, plan_text):
        """Store the candidate's stated plan as a telemetry event."""
        ...

    async def compare_plan_to_code(self, assessment_id, question_id, user_id, code_text):
        """
        LLM call: compare plan_text to code_text.
        Returns mismatch score + explanation.
        """
        ...
```

**Preparation for coder:** When building `telemetry.py`, ensure the `event_type` field accepts arbitrary strings (not just "keystroke_session"). The `thinking_time_ms` and `typing_time_ms` fields can be 0 for non-keystroke events. The `idle_periods` JSON field can carry plan text by repurposing it (or add a `payload TEXT` column now — cheap insurance).

---

## 6. Performance Considerations

- **Telemetry ingestion is write-heavy.** Use batch inserts (`INSERT INTO ... VALUES (...), (...), ...`) for efficiency. The `ingest()` method accepts a list, not individual events.
- **Hash lookups must be fast.** The compound index on `(assessment_id, question_id, hash)` ensures O(log n) lookups.
- **Confidence computation is pure CPU.** No LLM, no I/O (except loading the assessment and diversity scores). Cache the result in the assessment record — don't recompute on every GET.
- **No real-time telemetry processing.** Telemetry is stored raw; aggregation happens on-demand when `get_time_logs()` is called. This keeps ingestion fast.
