# Module A: Assessment Facade — Specification

> **Priority:** T1 (Foundation) — Must be implemented first.  
> **Package:** `src/api/assessment/`  
> **Owner Files:** `facade.py`, `dispatcher.py`, `engines/base.py`, `engines/curriculum_engine.py`, `engines/jd_engine.py`, `contracts.py`  
> **Route File:** `src/api/routes/assessment.py`

---

## 1. Module Scope

This module is the **single entry point** for the entire Assessment Intelligence Suite. It handles:

| Feature | Origin |
|---|---|
| Unified Assessment API surface | Architectural Mandate |
| Facade + Strategy dispatching to Curriculum / JD engines | Architectural Mandate |
| Shared `assessment.json` output contract | Architectural Mandate |
| Assessment lifecycle management (DRAFT → PUBLISHED → ARCHIVED) | Derived from all tiers |

**What this module does NOT do:**
- It does NOT contain skill extraction logic (delegated to Module B)
- It does NOT generate questions (delegated to Module C)
- It does NOT compute integrity metrics (delegated to Module D)
- It does NOT run QA simulations or HR flows (delegated to Module E)
- It does NOT generate training drills (delegated to Module F)

It **orchestrates** all of the above by calling into them.

---

## 2. Design Patterns & Internal Structure

### 2.1 Facade Pattern — `facade.py`

The `AssessmentFacade` class is the public-facing orchestrator. All route handlers in `routes/assessment.py` delegate to this facade. No route handler should contain business logic.

```python
# facade.py — Structural sketch (NOT implementation code)

class AssessmentFacade:
    """
    Single unified interface for all assessment operations.
    Routes call this; this calls into sub-modules.
    """

    def __init__(self):
        self._dispatcher = AssessmentDispatcher()

    async def generate(self, request: GenerateAssessmentRequest) -> AsyncGenerator[Assessment, None]:
        """
        Main generation flow:
        1. Validate request
        2. Create DRAFT assessment record in DB
        3. Dispatch to correct engine via Strategy
        4. Engine internally calls Module B (skills) and Module C (questions)
        5. Module D computes confidence score
        6. Yield progressive Assessment updates as NDJSON
        7. Persist final assessment.json to DB
        8. Transition status to GENERATED
        """
        ...

    async def get(self, assessment_id: str) -> Assessment:
        """Retrieve a persisted assessment by ID."""
        ...

    async def adjust_skills(self, assessment_id: str, adjustments: list, regenerate: bool) -> Assessment:
        """
        Delegate to Module B for weight adjustment.
        If regenerate=True, re-run the engine with updated ontology.
        """
        ...

    async def run_qa(self, assessment_id: str, personas: list) -> AsyncGenerator[QAResults, None]:
        """Delegate to Module E. Transition status to QA_PENDING."""
        ...

    async def calibrate(self, assessment_id: str, calibration: HRCalibrationInput) -> Assessment:
        """Delegate to Module E. Transition status to PUBLISHED or REJECTED."""
        ...

    async def generate_drills(self, assessment_id: str, user_id: int, params: dict) -> AsyncGenerator:
        """Delegate to Module F for post-assessment training."""
        ...

    async def archive(self, assessment_id: str) -> Assessment:
        """Transition status to ARCHIVED. Soft-delete."""
        ...
```

### 2.2 Strategy Pattern — `dispatcher.py`

The dispatcher selects the correct engine based on `assessment_mode`. This replaces a chain of `if/elif` blocks with an extensible registry.

```python
# dispatcher.py — Structural sketch

class AssessmentDispatcher:
    """
    Strategy selector: maps AssessmentMode → Engine instance.
    """

    def __init__(self):
        self._engines: dict[AssessmentMode, AbstractAssessmentEngine] = {
            AssessmentMode.CURRICULUM: CurriculumAssessmentEngine(),
            AssessmentMode.JD: JDAssessmentEngine(),
        }

    def get_engine(self, mode: AssessmentMode) -> AbstractAssessmentEngine:
        engine = self._engines.get(mode)
        if not engine:
            raise ValueError(f"Unknown assessment mode: {mode}")
        return engine

    def register_engine(self, mode: AssessmentMode, engine: AbstractAssessmentEngine):
        """Future extensibility: register new engine types at runtime."""
        self._engines[mode] = engine
```

### 2.3 Abstract Engine — `engines/base.py`

The ABC that both engines must implement. This is the **contract** between the Facade and the engines.

```python
# engines/base.py — Structural sketch

from abc import ABC, abstractmethod

class EngineInput(BaseModel):
    org_id: int
    user_id: int
    source: AssessmentSource
    variation_mode: VariationMode
    settings: AssessmentSettings

class AbstractAssessmentEngine(ABC):

    @abstractmethod
    async def generate(self, input: EngineInput) -> AsyncGenerator[Assessment, None]:
        """
        Core generation method. Must:
        1. Validate the source data
        2. Build context (skills for JD, curriculum for Curriculum)
        3. Generate questions via Module C
        4. Compute confidence via Module D
        5. Yield progressive Assessment snapshots
        """
        ...

    @abstractmethod
    async def validate_source(self, source: AssessmentSource) -> bool:
        """
        Pre-generation validation.
        - CurriculumEngine: checks course_id exists, milestone_ids are valid
        - JDEngine: checks jd_text is non-empty and meets minimum length
        """
        ...
```

### 2.4 Curriculum Engine — `engines/curriculum_engine.py`

```
Input:  course_id, milestone_ids (optional), task_ids (optional)
Flow:
  1. Fetch course structure from existing DB (db/course.py → get_course)
  2. Fetch task/question data from existing DB (db/task.py → get_task)
  3. Build curriculum context (blocks, scorecard criteria, learning objectives)
  4. Pass to Module C (Question Factory) to generate/transform questions
  5. Pass to Module D (Integrity) for confidence scoring
  6. Assemble assessment.json
Output: Assessment (streaming)
```

**Key constraint:** This engine READS from existing tables but WRITES only to new assessment tables. It must use the existing DAL functions from `db/course.py` and `db/task.py` — do NOT rewrite SQL queries for existing data.

### 2.5 JD Engine — `engines/jd_engine.py`

```
Input:  jd_text (raw Job Description string)
Flow:
  1. Delegate to Module B (Skill Intelligence) for ontology extraction
  2. Yield partial Assessment with skill_ontology populated (so frontend can show progress)
  3. Pass skill ontology to Module C (Question Factory) for generation
  4. Pass to Module D (Integrity) for confidence scoring
  5. Assemble assessment.json
Output: Assessment (streaming)
```

**Key constraint:** This engine makes LLM calls (via Module B and C). It MUST use Langfuse tracing (`langfuse.start_as_current_span`) wrapping the entire generation flow, so the full pipeline is traceable.

---

## 3. Data Flow

### 3.1 Inputs

| Endpoint | Input Model | Source |
|---|---|---|
| `POST /assessment/generate` | `GenerateAssessmentRequest` | Frontend form submission |
| `GET /assessment/{id}` | Path parameter | Frontend navigation |
| `PUT /assessment/{id}/skills` | `AdjustSkillsRequest` | Recruiter weight editor UI |
| `POST /assessment/{id}/calibrate` | `CalibrateRequest` | HR confirmation dialog |
| `POST /assessment/{id}/qa/simulate` | `SimulateQARequest` | Admin QA trigger |
| `POST /assessment/{id}/train/user/{uid}` | `GenerateDrillsRequest` | Post-assessment training UI |

### 3.2 Outputs

All outputs conform to `assessment.json` (the `Assessment` Pydantic model from `contracts.py`).

- **Streaming endpoints** return `StreamingResponse` with `application/x-ndjson` media type
- **Non-streaming endpoints** return standard JSON responses

### 3.3 Inter-Module Communication

```
                    ┌─────────────────┐
  HTTP Request ────►│  routes/        │
                    │  assessment.py  │
                    └───────┬─────────┘
                            │ delegates
                    ┌───────▼─────────┐
                    │ AssessmentFacade │
                    └───────┬─────────┘
                            │ dispatches
                    ┌───────▼──────────┐
                    │ Dispatcher       │
                    │ (Strategy select)│
                    └──┬───────────┬───┘
                       │           │
            ┌──────────▼──┐  ┌────▼──────────┐
            │ Curriculum  │  │ JD Engine     │
            │ Engine      │  │               │
            └──────┬──────┘  └───┬───────────┘
                   │             │
                   │    ┌────────▼────────┐
                   │    │ Module B: Skill  │ (JD only)
                   │    │ Intelligence    │
                   │    └────────┬────────┘
                   │             │
                   └──────┬──────┘
                          │
                  ┌───────▼────────┐
                  │ Module C:      │
                  │ Question       │
                  │ Factory        │
                  └───────┬────────┘
                          │
                  ┌───────▼────────┐
                  │ Module D:      │
                  │ Integrity      │
                  │ (confidence)   │
                  └───────┬────────┘
                          │
                  ┌───────▼────────┐
                  │ DB: persist    │
                  │ assessment.json│
                  └────────────────┘
```

---

## 4. Dependencies & Edge Cases

### 4.1 Dependencies

| Dependency | Type | Details |
|---|---|---|
| `contracts.py` | Internal | Must be created BEFORE this module |
| `db/assessment.py` | Internal | DAL for assessment CRUD — create alongside this module |
| `db/course.py` (existing) | Read-only | Curriculum engine reads course structures |
| `db/task.py` (existing) | Read-only | Curriculum engine reads task/question data |
| `llm.py` (existing) | Utility | Engines use for LLM generation calls |
| `langfuse` | External | Tracing wrapper for all generation flows |
| Module B | Runtime | JD engine calls skill extractor |
| Module C | Runtime | Both engines call question factory |
| Module D | Runtime | Both engines call confidence scorer |

### 4.2 Edge Cases

| Edge Case | Handling Strategy |
|---|---|
| **Empty JD text** | `validate_source()` rejects with 400. Minimum 50 characters. |
| **Course with no published tasks** | `validate_source()` rejects with 400. At least 1 published task required. |
| **LLM timeout during generation** | `backoff` retry (existing pattern). After max retries, transition assessment to `status=DRAFT` with error metadata, return 500. |
| **Concurrent generation for same assessment** | Check `status != DRAFT` before starting. If already `GENERATED`, reject with 409 Conflict. |
| **Partial generation failure** | Store whatever was generated so far. Set status to `DRAFT` with `error` field in metadata. Allow retry. |
| **Unknown assessment mode** | Dispatcher raises `ValueError` → route catches → returns 400. |
| **Assessment not found** | DAL returns `None` → route returns 404. |
| **Skill weight adjustment after questions generated** | If `regenerate=True`, delete existing questions and re-run. If `regenerate=False`, update ontology only (questions may be stale — add warning in response). |

### 4.3 Status Transition Guards

```python
# Allowed transitions — enforce in facade.py
ALLOWED_TRANSITIONS = {
    AssessmentStatus.DRAFT: {AssessmentStatus.GENERATED},
    AssessmentStatus.GENERATED: {
        AssessmentStatus.QA_PENDING,
        AssessmentStatus.CALIBRATION_PENDING,
        AssessmentStatus.PUBLISHED,  # skip QA + calibration
    },
    AssessmentStatus.QA_PENDING: {
        AssessmentStatus.CALIBRATION_PENDING,
        AssessmentStatus.PUBLISHED,  # skip calibration
        AssessmentStatus.GENERATED,  # QA failed → regenerate
    },
    AssessmentStatus.CALIBRATION_PENDING: {
        AssessmentStatus.PUBLISHED,  # HR approved
        AssessmentStatus.GENERATED,  # HR rejected → regenerate
    },
    AssessmentStatus.PUBLISHED: {AssessmentStatus.ARCHIVED},
}
```

Any transition not in this map must raise a 400 error.

---

## 5. Route Handler Scaffold

```python
# routes/assessment.py — Structural scaffold

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from api.assessment.facade import AssessmentFacade
from api.assessment.contracts import (
    Assessment, GenerateAssessmentRequest, AdjustSkillsRequest,
    CalibrateRequest, SimulateQARequest, GenerateDrillsRequest,
)

router = APIRouter()
facade = AssessmentFacade()

@router.post("/generate")
async def generate_assessment(request: GenerateAssessmentRequest):
    async def stream():
        async for partial in facade.generate(request):
            yield partial.model_dump_json() + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson")

@router.get("/{assessment_id}")
async def get_assessment(assessment_id: str):
    result = await facade.get(assessment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return result

@router.put("/{assessment_id}/skills")
async def adjust_skills(assessment_id: str, request: AdjustSkillsRequest):
    return await facade.adjust_skills(assessment_id, request.adjustments, request.regenerate_questions)

@router.post("/{assessment_id}/calibrate")
async def calibrate(assessment_id: str, request: CalibrateRequest):
    return await facade.calibrate(assessment_id, request)

@router.post("/{assessment_id}/qa/simulate")
async def simulate_qa(assessment_id: str, request: SimulateQARequest):
    async def stream():
        async for partial in facade.run_qa(assessment_id, request.personas):
            yield partial.model_dump_json() + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson")

@router.post("/{assessment_id}/train/user/{user_id}")
async def generate_drills(assessment_id: str, user_id: int, request: GenerateDrillsRequest):
    async def stream():
        async for partial in facade.generate_drills(assessment_id, user_id, request.model_dump()):
            yield partial.model_dump_json() + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson")

@router.get("/{assessment_id}/confidence")
async def get_confidence(assessment_id: str):
    assessment = await facade.get(assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment.confidence_score
```

---

## 6. Database Tables Owned

| Table | Purpose |
|---|---|
| `assessments` | Core record: id, org_id, created_by, status, mode, variation_mode, assessment_json (TEXT), settings (TEXT) |

Schema:

```sql
CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    org_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    mode TEXT NOT NULL,
    variation_mode TEXT NOT NULL DEFAULT 'static',
    assessment_json TEXT,
    settings TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_assessment_org_id ON assessments (org_id);
CREATE INDEX idx_assessment_created_by ON assessments (created_by);
CREATE INDEX idx_assessment_status ON assessments (status);
```

> All other tables are owned by their respective modules. This module only reads from them.

---

## 7. Langfuse Prompts Required

This module does not directly call the LLM, but it wraps the generation flow in a root Langfuse span. The engines and sub-modules register their own prompts.

| Prompt Name | Owner | Used By |
|---|---|---|
| (root span only) | Module A | `facade.py` → `langfuse.start_as_current_span(name="assessment_generation")` |
