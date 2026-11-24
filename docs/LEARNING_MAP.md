# Code map

Read in the order of the learning experience: course material becomes a route; a chosen topic becomes a saved lesson using current knowledge records; selected-text questions and saved notes support continued reading. The current UI is Chinese; the function names below refer to the shipped implementation.

| Responsibility | Main files and functions |
|---|---|
| Configuration and initialization | `backend/app/config.py`; `db.py::database_url`; `migration.py::migrate`; `main.py::startup`, `seed_demo` |
| API and schemas | `backend/app/main.py`: Pydantic inputs and `/v1` routes |
| Persistent lesson jobs | `generation.py::request`, `status`, `run`; `main.py::ensure_lesson`; `locks.py::task_lock` |
| Private file storage | `storage.py::bind_folder`, `upload_one`, `original_path` |
| Parsing and correction | `ingestion.py::parse_upload`, `split_pages`, `confirm`; unchanged vectors carry forward, old citation chunks remain |
| Incremental routes | `incremental.py::sync_course`, `fingerprint`, `bootstrap`; resumable bounded extraction, source links, append-only merge |
| Planning contracts / compatibility | `planning.py::CoursePlan`, `extract_plan`; legacy manual-plan API retained for compatibility; current uploads use incremental sync |
| Prerequisite discovery | `dependencies.py::analyze`; scoped cached analysis followed by current learner state |
| Local concept matching | `resolver.py::resolve`, `resolve_batch`, `canonical`; `matching.py` optionally adds compact decisions to an already-needed request |
| Teaching policy | `teaching.py::make_plan`, `depth_for`; six levels select support at the actual point of use |
| RAG and graph | `retrieval.py::encode`, `search`, `top_k`; `tools.py`; `graph.py::run_graph`, `model_plan` |
| Model boundary | `llm.py::generate_with_tools`, `ModelOutput`; `prompts/teach.md`, `answer_selection.md`; Responses call IDs and citation validation |
| Mastery settlement | `policy.py::reduce`; `mastery.py::settle`; `knowledge.py::snapshot`; transactions, effective dates and consumed identities |
| Submitted questions and generated exercises | `questions.py::record_attribution`; `practice.py::generate`, `reveal`, `grade`, `verified_numeric` |
| Local library | `classification.py`; `library.py::rows`, `merge`; `background.py::initialize`; SQL filters before pagination |
| Optional reorder | `reordering.py::reorder`; validates topic identity and preserves completed/current slots |
| Reader and notes | `frontend/src/main.tsx::App`, `openTopic`, `startLearning`, `captureSelection`, `saveNote`, `readingScroll`, `complete` |
| Dialogs and rendering | `BatchUpload.tsx`, `Library.tsx`, `Materials.tsx`, `Markdown.tsx` |
| Deterministic verification | `tests/`; temporary databases, clock control and explicit model fakes; `evals/mastery_examples.json` is synthetic |
| Frontend verification | `Markdown.test.tsx`, `Workflow.test.tsx`; math rendering and mocked-API DOM interactions |

Existing successful lessons keep their original snapshot. New questions and first-time lessons read current knowledge. Opening a topic never schedules paid generation; `startLearning` explicitly creates the persistent job. Navigation and reading-position saves do not settle completion. The frontend no longer requests historical explanation/retraction views; legacy backend evidence correction remains available internally for compatibility.


## Reading continuity and scope

`main.py::_execute_turn` persists submitted questions, selections and results as `Turn` records and supplies bounded thread context to later questions. In `frontend/src/main.tsx`, `answers` is temporary UI state cleared by `openTopic`; no full conversation replay is loaded. `saveNote` explicitly persists the edited text, selected quote and anchor against a lesson/block, and `showLesson` loads those saved notes. `progress`/`resume` store the last topic/scroll position per course. These are separate records with different restore behavior, not a universal chat history.

The demo's row-operations concept is shared between its two courses. `teaching.make_plan` maps its current state to support depth; the real teaching prompt asks for supplementation where it is needed. The deterministic demo composes fixed example blocks, so it does not demonstrate the model's full point-of-use composition. Background initialization fills only missing states from the supported seed; the broader long-term tutor remains the product direction described in the README.
