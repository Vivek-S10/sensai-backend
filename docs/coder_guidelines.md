# Coder Guidelines: SensAI Assessment Intelligence Suite

> **This file is the master prompt for all downstream coding agents.** Read it in full before writing any code.

---

## LIVING DOCUMENTATION PROTOCOL

To maintain a single source of truth, you must follow these rules:

### Self-Update Rule
If you change internal logic (add a new field, alter a data flow, introduce a new dependency between modules), you must **immediately update the relevant `.md` file in `docs/`**:

| Change Type | File to Update |
|---|---|
| New domain term or constraint | `docs/context.md` |
| New module, changed integration point, altered pattern | `docs/architecture_spec.md` |
| Changed JSON schema, new endpoint, modified interface | `docs/api_contracts.md` |
| Changed implementation workflow or guardrails | `docs/coder_guidelines.md` (this file) |

### Change-Log Rule
Record **only** major architectural pivots or schema changes in `CHANGELOG.md` at the repo root. Examples of what qualifies:

✅ **Log these:**
- Adding a new module (e.g., Module G)
- Breaking change to `assessment.json` schema
- Changing the engine interface contract
- Switching from SQLite to PostgreSQL
- Adding a new design pattern to the architecture

❌ **Do NOT log these:**
- Adding a helper function
- Fixing a bug in an existing module
- Adding a new field to an existing DB table
- Refactoring internal variable names
- Minor prompt adjustments

---

## IMPLEMENTATION GUARDRAILS (CRITICAL)

### Pre-Flight Report

Before writing **ANY** implementation code, you must produce a **Pre-Flight Report** in the following format:

```markdown
## Pre-Flight Report: [Feature/Module Name]

### New Files (Primary Work)
- `src/api/assessment/[module]/[file].py` — [purpose]
- `src/api/assessment/[module]/[file].py` — [purpose]
- ...

### Global Impact (Existing File Modifications)
- `src/api/main.py` — Adding router registration
- `src/api/config.py` — Adding table name constants
- `src/api/db/__init__.py` — Adding table creation in init_db()
- ...
(or "None — no global files touched")

### Active Conflict Check
> "Are any teammates currently editing [list of global files]?"
```

### Decision Protocol

After producing the Pre-Flight Report:

1. **If NO global files are touched:**
   - State `"No global impact."` and proceed with execution.

2. **If YES — global files are touched:**
   - You **ARE NOT ALLOWED** to write code until the user provides **manual confirmation** for those specific files.
   - Output the Pre-Flight Report and **STOP**.
   - Wait for explicit user approval (e.g., "Go ahead" or "File X is clear").
   - Only after receiving approval for **each** listed global file may you proceed.

---

## IMPLEMENTATION RULES

### Rule 1: Isolation-First

All new assessment code **must** live under `src/api/assessment/`. Do not scatter assessment logic across existing route files or DB files.

**Exception:** The following existing files receive **append-only** modifications (no deletions, no refactors):
- `main.py` — one `include_router` line
- `config.py` — new table name constants
- `db/__init__.py` — new table creation functions + calls in `init_db()`
- `models.py` — new Pydantic models (or import from `assessment/contracts.py`)

### Rule 2: Follow Existing Patterns

| Pattern | Reference File | What to Follow |
|---|---|---|
| Route structure | `routes/task.py` | Router creation, endpoint decorators, response types |
| Streaming AI | `routes/ai.py` | `StreamingResponse` + NDJSON + `stream_llm_with_openai` |
| DB operations | `db/task.py` | Raw `aiosqlite` queries, `get_new_db_connection()` context manager |
| Table creation | `db/__init__.py` | DDL format, index creation, trigger patterns |
| LLM calls | `llm.py` | `run_llm_with_openai`, `stream_llm_with_openai`, `backoff` retry |
| Pydantic models | `models.py` | Enum patterns, `BaseModel` inheritance, field types |
| Config constants | `config.py` | Table name variable naming convention |
| Logging | `utils/logging.py` | `logger` import and usage |
| Langfuse tracing | `routes/ai.py` | `@observe`, `langfuse.start_as_current_span`, prompt fetching |

### Rule 3: Database Conventions

- All tables use `id INTEGER PRIMARY KEY AUTOINCREMENT`
- All tables include `created_at DATETIME DEFAULT CURRENT_TIMESTAMP`, `updated_at DATETIME DEFAULT CURRENT_TIMESTAMP`, `deleted_at DATETIME`
- Soft delete via `deleted_at` (never hard delete)
- Foreign keys with `ON DELETE CASCADE`
- Create indexes on all foreign key columns and frequently queried columns
- Table names are defined as constants in `config.py` and used everywhere via import
- JSON data is stored as `TEXT` (serialized with `json.dumps`, parsed with `json.loads`)

### Rule 4: Migration Strategy

For new tables:
1. Add the `create_xxx_table()` function in `db/__init__.py`
2. Call it in the `init_db()` function (for fresh installs)
3. Add a migration step in `db/migration.py` for existing databases
4. Test both paths: fresh install and migration from existing DB

### Rule 5: LLM Integration

- All LLM prompts must be registered in **Langfuse** (not hardcoded in Python)
- Use `langfuse.get_prompt(name, type="chat", label=LANGFUSE_PROMPT_LABEL)` to fetch prompts
- Use `prompt.compile(**variables)` to inject variables
- Use `stream_llm_with_openai` for streaming responses, `run_llm_with_openai` for non-streaming
- Always create a Pydantic `Output` model for structured LLM responses
- Always wrap LLM calls in Langfuse observations for tracing
- Model selection: Use `openai_plan_to_model_name` from `config.py`

### Rule 6: Testing

- Write tests in `tests/api/test_assessment.py` (mirroring the existing test structure)
- Use the fixtures from `tests/conftest.py`
- Test both success and error paths for each endpoint
- For LLM-dependent tests, mock `run_llm_with_openai` and `stream_llm_with_openai`

### Rule 7: No Side Effects on Existing Features

- The assessment module must **never** import from or modify existing route handlers
- Read access to existing DB tables (courses, tasks, questions) is allowed via existing DAL functions
- Write access is limited to new assessment-specific tables only
- The existing AI endpoints (`/ai/chat`, `/ai/assignment`) must continue to function identically

---

## BUILD ORDER

Follow the dependency graph from `architecture_spec.md`:

### Phase 1: Foundation
1. `assessment/contracts.py` — All Pydantic models
2. `config.py` — Table name constants (append)
3. `db/__init__.py` — Table creation functions (append)
4. `db/assessment.py` — Assessment DAL (CRUD for all new tables)
5. `assessment/__init__.py` — Package setup
6. `assessment/engines/base.py` — Abstract engine interface
7. `assessment/facade.py` — Facade class
8. `assessment/dispatcher.py` — Strategy dispatcher
9. `routes/assessment.py` — Router scaffolding
10. `main.py` — Router registration (append)

### Phase 2: Skill Intelligence + Engines
11. `assessment/skills/extractor.py` — JD parser
12. `assessment/skills/ontology.py` — Skill tree management
13. `assessment/engines/jd_engine.py` — JD generation engine
14. `assessment/engines/curriculum_engine.py` — Curriculum generation engine
15. `assessment/skills/reporter.py` — Competency reporting

### Phase 3: Question Factory
16. `assessment/questions/factory.py` — Question orchestrator
17. `assessment/questions/enrichment.py` — Metadata attachment
18. `assessment/questions/variation.py` — Variation strategies
19. `assessment/questions/diversity.py` — Semantic diversity checks

### Phase 4: Integrity & Telemetry
20. `assessment/integrity/telemetry.py` — Time log ingestion
21. `assessment/integrity/hash_verify.py` — SHA-256 verification
22. `assessment/integrity/confidence.py` — Confidence scoring

### Phase 5: QA & Calibration
23. `assessment/qa/simulator.py` — AI persona runner
24. `assessment/qa/discriminator.py` — Discriminator scoring
25. `assessment/qa/calibration.py` — HR calibration flow

### Phase 6: Adaptive Training
26. `assessment/training/profiler.py` — Weakness analysis
27. `assessment/training/generator.py` — Drill generation

---

## NAMING CONVENTIONS

| Entity | Convention | Example |
|---|---|---|
| Python files | `snake_case.py` | `hash_verify.py` |
| Python classes | `PascalCase` | `AssessmentFacade` |
| Python functions | `snake_case` | `extract_skills()` |
| DB table constants | `snake_case + _table_name` | `assessments_table_name` |
| API endpoints | `kebab-case` in URL | `/assessment/verify-hash` |
| Pydantic models | `PascalCase` | `AssessmentQuestion` |
| Enums | `UPPER_SNAKE_CASE` values | `ISOMORPHIC_SHUFFLE` |
| Test files | `test_<module>.py` | `test_assessment.py` |

---

## ERROR HANDLING

Follow the existing pattern from `routes/ai.py`:

```python
# Validation errors → 400
raise HTTPException(status_code=400, detail="Assessment mode is required")

# Not found → 404
raise HTTPException(status_code=404, detail="Assessment not found")

# Auth/Permission → 403
raise HTTPException(status_code=403, detail="Not authorized to access this assessment")

# LLM errors → caught by backoff, then 500
# Always log errors via utils/logging.py logger
```

---

## DEPENDENCY MANAGEMENT

If a new feature requires a new Python package:
1. Add it to `pyproject.toml` under `[project] dependencies`
2. Run `uv lock` to update `uv.lock`
3. Document the dependency and its purpose in the Pre-Flight Report
4. Prefer packages already in the project (e.g., use `openai` not `litellm`, use `pydantic` not `attrs`)

Currently available relevant packages:
- `openai` — LLM calls
- `pydantic` / `pydantic-settings` — Models and settings
- `fastapi` — API framework
- `aiosqlite` — Async SQLite
- `backoff` — Retry logic
- `langfuse` — Prompt registry + tracing
- `jiter` — Fast JSON parsing for streaming
- `instructor` — Structured LLM outputs
- `langchain-core` — Output parsers (used sparingly)
- `boto3` — S3 operations
- `sentry-sdk` — Error tracking

---

## FRONTEND INTEGRATION NOTES

Frontend work comes **after** backend modules are complete. When implementing frontend:

- Framework: Next.js 14 (App Router) with TypeScript
- Styling: Tailwind CSS (existing config in `tailwind.config.js`)
- API client: Raw `fetch()` calls (see `src/lib/api.ts` for pattern)
- State: React Context + custom hooks
- Components: Add to `src/components/` following existing naming patterns
- Types: Add to `src/types/` and re-export from `src/types/index.ts`
- Streaming: Use `ReadableStream` for NDJSON consumption (see existing chat components)
