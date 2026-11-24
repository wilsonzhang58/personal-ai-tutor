# Build status

Local single-user MVP. Current implementation includes explicit persistent lesson jobs, cached cited lessons, incremental material routes, a shared six-level knowledge profile, selection questions, notes, optional exercises and targeted text correction.

The deterministic policy serializes settlement and deduplicates evidence. Local matching uses IDs, aliases, cache and E5; no dedicated paid matching job. Background curriculum coverage is a small documented subset, not a complete syllabus.

See [verification results](../evals/RESULTS.md) for fresh-checkout commands and actual outcomes. Browser E2E, OCR, exact formula reconstruction, streaming, authentication and production deployment are not complete. Real-model outputs and scoring remain fallible.

This public snapshot excludes private materials, personal learning records and local development handoff documents.

Publication checks: 42 backend tests, 8 frontend tests, a production build, a zero-known-vulnerability npm audit, isolated HTTP/proxy checks and a fresh-cache authored-demo run passed. No paid API calls were used for publication. Dependency lockfiles, an environment template, `.python-version` and `.nvmrc` are included. This is an audited source snapshot; original development-history documents are intentionally not published.
