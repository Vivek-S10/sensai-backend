# API Contracts: SensAI Assessment Intelligence Suite

## 1. The `assessment.json` Output Contract

This is the **single shared contract** that both the Curriculum and JD engines produce. All downstream modules (reporting, integrity, QA, training) consume this schema. It is mode-agnostic.

### 1.1 Top-Level Schema

```json
{
  "$schema": "assessment/v1",
  "id": "uuid-v4",
  "org_id": 1,
  "created_by": 7,
  "created_at": "2026-04-10T12:00:00Z",
  "updated_at": "2026-04-10T12:00:00Z",
  "status": "draft | generated | qa_pending | calibration_pending | published | archived",

  "mode": "curriculum | jd",
  "variation_mode": "static | variable_swap | isomorphic_shuffle",

  "source": {
    "type": "jd | curriculum",
    "jd_text": "Full JD text (only when mode=jd)",
    "course_id": null,
    "milestone_ids": [],
    "task_ids": []
  },

  "skill_ontology": {
    "id": "uuid-v4",
    "skills": [
      {
        "id": "uuid-v4",
        "name": "Python",
        "weight": 0.35,
        "sub_skills": [
          {
            "id": "uuid-v4",
            "name": "Decorators",
            "weight": 0.15
          },
          {
            "id": "uuid-v4",
            "name": "Async/Await",
            "weight": 0.20
          }
        ]
      }
    ],
    "total_weight": 1.0,
    "extraction_confidence": 0.92
  },

  "questions": [
    {
      "id": "uuid-v4",
      "position": 1,
      "blocks": [],
      "answer": [],
      "type": "objective | subjective",
      "input_type": "code | text | audio",
      "response_type": "chat | exam",
      "coding_languages": ["python"],
      "difficulty": "easy | medium | hard",
      "time_limit_seconds": 1800,

      "metadata": {
        "skill_tested": {
          "skill_id": "uuid-v4",
          "skill_name": "Decorators",
          "parent_skill": "Python"
        },
        "difficulty_reason": "Requires understanding of closure scope and function wrapping",
        "learning_objective": "Candidate can implement and compose custom decorators",
        "variation_source": "isomorphic_shuffle",
        "original_question_id": "uuid-v4 | null"
      },

      "diversity_fingerprint": "sha256-of-semantic-embedding",
      "hash": "sha256-of-question-content"
    }
  ],

  "confidence_score": {
    "overall": 0.87,
    "skill_coverage": 0.92,
    "difficulty_distribution": 0.81,
    "reasoning": "Covers 11/12 extracted skills. Difficulty leans medium-hard (JD is for Senior role)."
  },

  "qa_results": {
    "status": "passed | failed | pending | skipped",
    "simulations": [
      {
        "persona": "beginner | intermediate | expert",
        "expected_score_range": [0, 30],
        "simulated_score": 22,
        "is_discriminating": true,
        "notes": "Beginner struggled with Q3, Q5 as expected."
      }
    ],
    "discriminator_score": 0.85
  },

  "hr_calibration": {
    "status": "pending | approved | rejected | skipped",
    "calibrated_by": null,
    "calibrated_at": null,
    "feedback": null,
    "difficulty_alignment": null
  },

  "settings": {
    "max_duration_minutes": 90,
    "shuffle_questions": true,
    "show_feedback": false,
    "allow_retakes": false,
    "require_webcam": false
  }
}
```

### 1.2 Pydantic Model Definition

This will live in `src/api/assessment/contracts.py`:

```python
from __future__ import annotations
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


# ─── Enums ──────────────────────────────────────────────

class AssessmentMode(str, Enum):
    CURRICULUM = "curriculum"
    JD = "jd"

class VariationMode(str, Enum):
    STATIC = "static"
    VARIABLE_SWAP = "variable_swap"
    ISOMORPHIC_SHUFFLE = "isomorphic_shuffle"

class AssessmentStatus(str, Enum):
    DRAFT = "draft"
    GENERATED = "generated"
    QA_PENDING = "qa_pending"
    CALIBRATION_PENDING = "calibration_pending"
    PUBLISHED = "published"
    ARCHIVED = "archived"

class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

class QAStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"

class CalibrationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"

class PersonaLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"


# ─── Skill Ontology ────────────────────────────────────

class SubSkill(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    weight: float = Field(ge=0.0, le=1.0)

class Skill(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    weight: float = Field(ge=0.0, le=1.0)
    sub_skills: List[SubSkill] = []

class SkillOntology(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skills: List[Skill]
    total_weight: float = 1.0
    extraction_confidence: float = Field(ge=0.0, le=1.0)


# ─── Question Metadata ─────────────────────────────────

class SkillReference(BaseModel):
    skill_id: str
    skill_name: str
    parent_skill: Optional[str] = None

class QuestionMetadata(BaseModel):
    skill_tested: SkillReference
    difficulty_reason: str
    learning_objective: str
    variation_source: Optional[VariationMode] = None
    original_question_id: Optional[str] = None


# ─── Assessment Question ───────────────────────────────

class AssessmentQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    position: int
    blocks: List[Dict[str, Any]]
    answer: Optional[List[Dict[str, Any]]] = None
    type: str  # "objective" | "subjective"
    input_type: str  # "code" | "text" | "audio"
    response_type: str  # "chat" | "exam"
    coding_languages: Optional[List[str]] = None
    difficulty: Difficulty
    time_limit_seconds: Optional[int] = None
    metadata: QuestionMetadata
    diversity_fingerprint: Optional[str] = None
    hash: Optional[str] = None


# ─── Confidence Score ──────────────────────────────────

class ConfidenceScore(BaseModel):
    overall: float = Field(ge=0.0, le=1.0)
    skill_coverage: float = Field(ge=0.0, le=1.0)
    difficulty_distribution: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ─── QA Results ────────────────────────────────────────

class PersonaSimulation(BaseModel):
    persona: PersonaLevel
    expected_score_range: List[float]  # [min, max]
    simulated_score: Optional[float] = None
    is_discriminating: Optional[bool] = None
    notes: Optional[str] = None

class QAResults(BaseModel):
    status: QAStatus = QAStatus.PENDING
    simulations: List[PersonaSimulation] = []
    discriminator_score: Optional[float] = None


# ─── HR Calibration ───────────────────────────────────

class HRCalibration(BaseModel):
    status: CalibrationStatus = CalibrationStatus.PENDING
    calibrated_by: Optional[int] = None
    calibrated_at: Optional[datetime] = None
    feedback: Optional[str] = None
    difficulty_alignment: Optional[str] = None


# ─── Assessment Source ─────────────────────────────────

class AssessmentSource(BaseModel):
    type: AssessmentMode
    jd_text: Optional[str] = None
    course_id: Optional[int] = None
    milestone_ids: Optional[List[int]] = []
    task_ids: Optional[List[int]] = []


# ─── Assessment Settings ──────────────────────────────

class AssessmentSettings(BaseModel):
    max_duration_minutes: Optional[int] = 90
    shuffle_questions: bool = True
    show_feedback: bool = False
    allow_retakes: bool = False
    require_webcam: bool = False


# ─── Root Assessment Contract ─────────────────────────

class Assessment(BaseModel):
    """
    The unified output contract for both Curriculum and JD assessment engines.
    This is the 'assessment.json' — the single source of truth for all downstream modules.
    """
    schema_version: str = "assessment/v1"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: int
    created_by: int
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: AssessmentStatus = AssessmentStatus.DRAFT

    mode: AssessmentMode
    variation_mode: VariationMode = VariationMode.STATIC

    source: AssessmentSource
    skill_ontology: Optional[SkillOntology] = None
    questions: List[AssessmentQuestion] = []

    confidence_score: Optional[ConfidenceScore] = None
    qa_results: Optional[QAResults] = None
    hr_calibration: Optional[HRCalibration] = None
    settings: AssessmentSettings = AssessmentSettings()
```

---

## 2. API Endpoint Contracts

### 2.1 Assessment Facade Router (`/assessment`)

#### `POST /assessment/generate` — Create a new assessment

**Request:**

```json
{
  "org_id": 1,
  "user_id": 7,
  "mode": "jd",
  "variation_mode": "isomorphic_shuffle",
  "source": {
    "type": "jd",
    "jd_text": "We are looking for a Senior Python Developer..."
  },
  "settings": {
    "max_duration_minutes": 60,
    "shuffle_questions": true
  }
}
```

**Response:** Streaming `application/x-ndjson`. Each line is a partial `Assessment` object (following the existing streaming pattern from `routes/ai.py`).

---

#### `GET /assessment/{assessment_id}` — Retrieve an assessment

**Response:** Full `Assessment` JSON.

---

#### `PUT /assessment/{assessment_id}/skills` — Adjust skill weights

**Request:**

```json
{
  "adjustments": [
    { "skill_id": "uuid", "new_weight": 0.45 },
    { "skill_id": "uuid", "new_weight": 0.10 }
  ],
  "regenerate_questions": true
}
```

**Response:** Updated `Assessment` JSON (or streaming if `regenerate_questions=true`).

---

#### `POST /assessment/{assessment_id}/calibrate` — HR Calibration response

**Request:**

```json
{
  "user_id": 7,
  "approved": true,
  "feedback": "Difficulty level looks appropriate for the Senior role.",
  "difficulty_alignment": "appropriate"
}
```

**Response:**

```json
{
  "status": "approved",
  "assessment_status": "published"
}
```

---

#### `POST /assessment/{assessment_id}/qa/simulate` — Trigger simulated QA

**Request:**

```json
{
  "personas": ["beginner", "intermediate", "expert"]
}
```

**Response:** Streaming `application/x-ndjson` with progressive `QAResults`.

---

### 2.2 Telemetry Endpoints

#### `POST /assessment/telemetry` — Ingest telemetry events

**Request:**

```json
{
  "events": [
    {
      "event_type": "keystroke_session",
      "assessment_id": "uuid",
      "question_id": 42,
      "user_id": 7,
      "thinking_time_ms": 12400,
      "typing_time_ms": 45200,
      "idle_periods": [
        { "start_ms": 3000, "end_ms": 8500 }
      ],
      "timestamp": "2026-04-10T12:00:00Z"
    }
  ]
}
```

**Response:**

```json
{ "accepted": 1, "rejected": 0 }
```

---

#### `GET /assessment/{assessment_id}/telemetry/user/{user_id}` — Get time logs

**Response:**

```json
{
  "assessment_id": "uuid",
  "user_id": 7,
  "per_question": [
    {
      "question_id": 42,
      "thinking_time_ms": 12400,
      "typing_time_ms": 45200,
      "total_time_ms": 57600,
      "idle_count": 1,
      "longest_idle_ms": 5500
    }
  ],
  "aggregate": {
    "total_thinking_ms": 45000,
    "total_typing_ms": 120000,
    "thinking_ratio": 0.27
  }
}
```

---

### 2.3 Integrity Endpoints

#### `POST /assessment/verify-hash` — Check for copy-paste

**Request:**

```json
{
  "assessment_id": "uuid",
  "question_id": 42,
  "user_id": 7,
  "answer_text": "def fibonacci(n): ..."
}
```

**Response:**

```json
{
  "hash": "sha256:abc123...",
  "is_duplicate": false,
  "matched_submissions": []
}
```

If duplicate detected:

```json
{
  "hash": "sha256:abc123...",
  "is_duplicate": true,
  "matched_submissions": [
    {
      "user_id": 12,
      "question_id": 42,
      "submitted_at": "2026-04-10T11:00:00Z",
      "similarity": 1.0
    }
  ]
}
```

---

### 2.4 Skill Report Endpoints

#### `GET /assessment/{assessment_id}/report/user/{user_id}` — Granular skill report

**Response:**

```json
{
  "assessment_id": "uuid",
  "user_id": 7,
  "overall_score": 72.5,
  "skill_breakdown": [
    {
      "skill_name": "Python",
      "weight": 0.35,
      "score": 80.0,
      "sub_skills": [
        {
          "name": "Decorators",
          "score": 90.0,
          "questions_tested": [1, 4],
          "strength": "strong"
        },
        {
          "name": "Async/Await",
          "score": 65.0,
          "questions_tested": [3],
          "strength": "developing"
        }
      ]
    }
  ],
  "competency_level": "intermediate",
  "recommendations": [
    "Focus on asynchronous programming patterns",
    "Strong decorator understanding; explore metaclasses"
  ]
}
```

---

### 2.5 Trainer Flow Endpoints

#### `POST /assessment/{assessment_id}/train/user/{user_id}` — Generate adaptive drills

**Request:**

```json
{
  "focus_skills": ["uuid-skill-1"],
  "max_questions": 5,
  "difficulty_preference": "same | easier | harder"
}
```

**Response:** Streaming `application/x-ndjson` with progressive drill questions.

---

### 2.6 Leaderboard Confidence Endpoint

#### `GET /assessment/{assessment_id}/confidence` — Assessment confidence score

**Response:**

```json
{
  "assessment_id": "uuid",
  "confidence_score": {
    "overall": 0.87,
    "skill_coverage": 0.92,
    "difficulty_distribution": 0.81,
    "reasoning": "Covers 11/12 extracted skills."
  }
}
```

---

## 3. Interface Contracts Between Modules

These are the internal Python interfaces (ABCs) that modules use to communicate.

### 3.1 Engine Interface

```python
# assessment/engines/base.py

from abc import ABC, abstractmethod
from assessment.contracts import Assessment, AssessmentSource, AssessmentSettings, VariationMode
from typing import AsyncGenerator

class EngineInput(BaseModel):
    org_id: int
    user_id: int
    source: AssessmentSource
    variation_mode: VariationMode
    settings: AssessmentSettings

class AbstractAssessmentEngine(ABC):
    @abstractmethod
    async def generate(self, input: EngineInput) -> AsyncGenerator[Assessment, None]:
        """Stream progressive assessment output."""
        ...

    @abstractmethod
    async def validate_source(self, source: AssessmentSource) -> bool:
        """Validate that the source data is sufficient for generation."""
        ...
```

### 3.2 Skill Extractor Interface

```python
# assessment/skills/extractor.py

from abc import ABC, abstractmethod
from assessment.contracts import SkillOntology

class SkillExtractorInterface(ABC):
    @abstractmethod
    async def extract(self, text: str) -> SkillOntology:
        """Extract skills from JD text. Two-step: raw extraction → structured ontology."""
        ...

    @abstractmethod
    async def adjust_weights(self, ontology: SkillOntology, adjustments: list) -> SkillOntology:
        """Allow recruiter to adjust skill weights."""
        ...
```

### 3.3 Question Factory Interface

```python
# assessment/questions/factory.py

from abc import ABC, abstractmethod
from assessment.contracts import AssessmentQuestion, SkillOntology, VariationMode
from typing import List, AsyncGenerator

class QuestionFactoryInterface(ABC):
    @abstractmethod
    async def generate_questions(
        self,
        context: dict,
        skill_ontology: SkillOntology | None,
        variation_mode: VariationMode,
        count: int,
    ) -> AsyncGenerator[List[AssessmentQuestion], None]:
        ...

    @abstractmethod
    async def check_diversity(self, questions: List[AssessmentQuestion]) -> float:
        """Returns diversity score (0.0 = all identical, 1.0 = all unique)."""
        ...
```

### 3.4 Integrity Interface

```python
# assessment/integrity/hash_verify.py

from abc import ABC, abstractmethod

class HashVerifierInterface(ABC):
    @abstractmethod
    async def compute_hash(self, content: str) -> str:
        """SHA-256 hash of normalized content."""
        ...

    @abstractmethod
    async def check_duplicate(self, hash: str, assessment_id: str) -> dict:
        """Check against stored hashes. Returns match info."""
        ...

    @abstractmethod
    async def store_hash(self, hash: str, metadata: dict) -> None:
        """Persist hash for future comparison."""
        ...
```

### 3.5 Telemetry Interface

```python
# assessment/integrity/telemetry.py

from abc import ABC, abstractmethod
from typing import List

class TelemetryEvent(BaseModel):
    event_type: str
    assessment_id: str
    question_id: int
    user_id: int
    thinking_time_ms: int
    typing_time_ms: int
    idle_periods: List[dict] = []
    timestamp: str

class TelemetryInterface(ABC):
    @abstractmethod
    async def ingest(self, events: List[TelemetryEvent]) -> dict:
        """Batch ingest telemetry events. Returns accepted/rejected counts."""
        ...

    @abstractmethod
    async def get_time_logs(self, assessment_id: str, user_id: int) -> dict:
        """Retrieve per-question and aggregate time logs."""
        ...
```

---

## 4. Assessment Lifecycle State Machine

```
                    ┌──────────┐
                    │  DRAFT   │
                    └────┬─────┘
                         │ generate()
                    ┌────▼─────┐
               ┌───►│GENERATED │
               │    └────┬─────┘
               │         │ run_qa()
               │    ┌────▼──────┐
               │    │QA_PENDING │
               │    └────┬──────┘
               │         │ qa complete
     regenerate│    ┌────▼───────────┐
               │    │CALIBRATION_    │
               │    │PENDING         │
               │    └────┬───────────┘
               │         │ HR approves
               │    ┌────▼──────┐
               └────┤PUBLISHED  │
                    └────┬──────┘
                         │ archive()
                    ┌────▼──────┐
                    │ ARCHIVED  │
                    └───────────┘
```

Transitions that skip optional steps:
- `GENERATED → PUBLISHED` (if QA and Calibration are skipped)
- `GENERATED → CALIBRATION_PENDING` (if QA is skipped)
- `QA_PENDING → PUBLISHED` (if Calibration is skipped)

---

## 5. Compatibility with Existing Contracts

The new `Assessment` contract is **fully independent** from the existing `Task`/`Question`/`Quiz` models. However, the Curriculum Engine will **read** data from existing tables to construct assessments. The mapping:

| Existing Model | Assessment Model |
|---|---|
| `Task.blocks` | `AssessmentQuestion.blocks` |
| `Question.type` | `AssessmentQuestion.type` |
| `Question.input_type` | `AssessmentQuestion.input_type` |
| `Question.response_type` | `AssessmentQuestion.response_type` |
| `Question.coding_languages` | `AssessmentQuestion.coding_languages` |
| `Question.answer` | `AssessmentQuestion.answer` |
| `Scorecard.criteria` | Mapped to `QuestionMetadata.skill_tested` |

The existing AI evaluation endpoints (`/ai/chat`, `/ai/assignment`) remain unchanged. Assessment questions that need AI evaluation will reuse the existing `llm.py` infrastructure, with evaluation prompts registered in Langfuse.
