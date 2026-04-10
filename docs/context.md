# Context: SensAI Assessment Intelligence Suite

## High-Level Goal

SensAI is an AI-first Learning Management System (LMS) that uses OpenAI-powered AI to coach students through questions and evaluate their learning. The current platform supports **Curriculum-driven** assessment—educators build courses, milestones, quizzes, assignments, and learning materials, then the AI evaluates student responses against scorecards.

The **Assessment Intelligence Suite** extends this foundation to support a second, parallel generation mode: **JD-driven (Job Description) assessment**. Recruiters will paste a Job Description; the system will extract a skill ontology, generate structurally diverse questions mapped to those skills, and produce granular competency reports.

Both generation modes—Curriculum and JD—must converge on a **single shared output contract** (`assessment.json`) so that all downstream features (reporting, integrity checks, leaderboards, trainer flows) are **mode-agnostic**.

---

## Constraints

| Constraint | Detail |
|---|---|
| **No ORM** | The codebase uses raw SQL via `aiosqlite`. All new tables must follow the same convention (see `db/__init__.py`). |
| **LLM Provider** | All AI calls go through OpenAI (via `langfuse`-instrumented `AsyncOpenAI`). Prompt management is handled by **Langfuse** (remote prompt registry). New prompts must be registered there. |
| **Streaming-first** | AI responses are streamed as **NDJSON** over `StreamingResponse`. All new AI endpoints must follow this pattern (see `routes/ai.py`). |
| **Single SQLite DB** | The current system uses a single `db.sqlite` file. New tables must be migrated via `db/migration.py`. |
| **Frontend: Next.js + Tailwind** | The frontend is a Next.js 14 app with Tailwind CSS. New UI components must use existing patterns from `components/`. |
| **No Auth Middleware on AI routes** | The `ai` router currently does not enforce authentication middleware; user identity is passed in request bodies. New assessment routes should follow the same pattern but design for future middleware integration. |
| **Stateless Horizontally** | While currently single-instance, the design must not introduce server-local state that would prevent future horizontal scaling (e.g., no in-memory caches for assessment sessions). |

---

## Domain Terminology

| Term | Definition |
|---|---|
| **Organization (Org)** | A school or company that uses SensAI. Has members, cohorts, and courses. |
| **Cohort** | A group of learners/mentors within an Org, enrolled in courses. |
| **Course** | A structured collection of Milestones → Tasks. |
| **Milestone** | A grouping of Tasks within a Course (like a module/chapter). |
| **Task** | An atomic learning unit: `quiz`, `learning_material`, or `assignment`. |
| **Question** | A single question within a Quiz Task. Has `type` (objective/subjective), `input_type` (code/text/audio), and `response_type` (chat/exam). |
| **Scorecard** | A rubric with weighted criteria used to evaluate subjective responses. |
| **Block** | A content unit (rich text, code, image, etc.) used in questions, learning materials, and assignments. |
| **JD (Job Description)** | A text document describing a role's requirements. Used as input for the new JD-driven assessment generation engine. |
| **Skill Ontology** | A hierarchical extraction of technical skills and sub-skills from a JD, with importance weights. |
| **Assessment** | The unified output of either generation engine, conforming to `assessment.json`. |
| **Variation Mode** | How questions are diversified: `Static` (no variation), `Variable Swap` (change parameters), `Isomorphic Shuffle` (structurally different, same concept). |
| **Thinking Time** | The idle duration before a candidate begins typing/coding a response. |
| **Typing Time** | The active duration of keyboard/coding activity during a response. |
| **Confidence Score** | A percentage measuring how well the generated assessment maps to its source JD or Curriculum. |
| **Discriminator Quality** | A measure of how well a question separates Beginner, Intermediate, and Expert candidates. Validated by the Simulated Candidate QA feature. |
| **Trainer Flow** | A post-assessment, non-graded, personalized practice session generated from a candidate's incorrect answers. |

---

## Existing Codebase Summary

### Backend (`sensai-backend`)

| Layer | Purpose | Key Files |
|---|---|---|
| **Entry Point** | FastAPI app with CORS, Sentry, static mounts | `main.py` |
| **Routes** | 15 routers: `auth`, `batch`, `code`, `cohort`, `course`, `org`, `task`, `chat`, `user`, `milestone`, `hva`, `file`, `ai`, `scorecard`, `integration` | `routes/*.py` |
| **AI Engine** | 2 endpoints: `/ai/chat` (quiz + learning material), `/ai/assignment` | `routes/ai.py` |
| **LLM Wrapper** | OpenAI streaming + non-streaming with `backoff`, `instructor`, `jiter` partial parsing | `llm.py` |
| **Data Models** | Pydantic models for all request/response contracts | `models.py` |
| **Database** | Raw `aiosqlite` with manual table creation and migration | `db/__init__.py`, `db/*.py` |
| **Config** | Table names, model mappings, file paths | `config.py`, `settings.py` |
| **Utilities** | Logging, S3, audio, file analysis, DB helpers, concurrency | `utils/*.py` |
| **Integrations** | BigQuery sync, Slack notifications, APScheduler cron | `bq/`, `slack.py`, `cron.py`, `scheduler.py` |
| **WebSockets** | Real-time course generation updates | `websockets.py` |
| **Public API** | API-key-authenticated endpoints for external consumption | `public.py` |

### Frontend (`sensai-frontend`)

| Layer | Purpose | Key Files |
|---|---|---|
| **Framework** | Next.js 14 (App Router) | `next.config.ts`, `src/app/` |
| **Styling** | Tailwind CSS | `tailwind.config.js`, `src/index.css` |
| **Auth** | NextAuth.js with Google provider | `src/lib/auth.ts`, `src/middleware.ts` |
| **State** | React Context + custom hooks | `src/context/`, `src/lib/hooks/` |
| **Components** | 60+ components (Quiz Editor, Code Editor, Chat, Scorecard, Course Module List, etc.) | `src/components/` |
| **Types** | TypeScript interfaces for Tasks, Quizzes, Scorecards | `src/types/` |
| **Observability** | Sentry (client + edge + server) | `sentry.*.config.ts`, `instrumentation*.ts` |
