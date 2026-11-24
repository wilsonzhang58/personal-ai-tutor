# Build status

Personal AI Tutor aims to be a one-to-one self-study teacher that gradually understands a learner's recorded knowledge background and carries it across courses. The current local single-user MVP implements a first version of that loop: topic-scoped teaching uses shared concept states to choose prerequisite support, while the reader keeps selected-text questions and explicitly saved notes close to the lesson.

Implemented components include persistent on-demand lesson jobs, cached cited lessons, incremental material routes, a shared six-level profile, side-panel follow-ups, saved notes, optional model-generated exercises and targeted text correction. Existing lessons keep their original snapshots; new questions and first-time lessons read current states. A course resumes its last saved topic/position. Submitted question turns are stored for backend context, but the reader does not replay the complete sidebar conversation after reopening; explicitly saved notes are restored with their lesson blocks.

The deterministic policy serializes settlement and deduplicates evidence. Local matching uses IDs, aliases, cache and E5; no dedicated paid matching job. Background curriculum coverage is a small documented subset, not a complete syllabus.

See [verification results](../evals/RESULTS.md) for fresh-checkout commands and actual outcomes. Browser E2E, OCR, exact formula reconstruction, streaming, authentication and production deployment are not complete. Real-model outputs and scoring remain fallible.

This public snapshot excludes private materials, personal learning records and local development handoff documents.

Publication checks: 42 backend tests, 8 frontend tests, a production build, a zero-known-vulnerability npm audit, isolated HTTP/proxy checks and a fresh-cache authored-demo run passed. No paid API calls were used for publication. Dependency lockfiles, an environment template, `.python-version` and `.nvmrc` are included. This is an audited source snapshot; original development-history documents are intentionally not published.

## Product direction

Knowledge coverage/concept organization, easier and more accurate background initialization, finer lesson/prerequisite support, and passage-linked questions/study records remain the four development priorities. The current seed and mastery rules are limited heuristics, not a comprehensive knowledge model or validated mastery diagnosis. The deterministic authored demo illustrates policy and workflow, not measured learning effectiveness. Exercises are generated; online classic-problem retrieval is not implemented. Successful lessons are not continuously rewritten.

The publication checks above describe the original verification run; they have not been rerun for this documentation-only revision. Historical command results and dates are retained in [verification results](../evals/RESULTS.md).
