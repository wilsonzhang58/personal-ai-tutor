# Verification results

## Clean publication snapshot — 2026-11-24

The release was exported from an allowlist into a separate directory. No original `.git`, `.env`, virtual environment, installed Node dependencies, private database, course files, model cache or learning records were copied. The original local project/history remain separate and are not part of this public history.

Environment: macOS ARM64, Python 3.11.9, Node 22.23.5, npm 10.9.2 and uv 0.13.9. Node dependencies were installed with `npm ci`; Python dependencies were downloaded into a new cache and installed into a new virtual environment from `uv.lock`. Node 22 and uv were temporary tools for verification, not runtime files bundled in Git.

| Command or check | Actual result |
|---|---|
| `uv lock --check` | Passed; lock agrees with project requirements |
| `uv sync --locked --extra dev` | Passed; independent environment, 92 installed packages |
| `npm --prefix frontend ci` | Passed with Node 22 and the committed lockfile |
| `uv run --locked pytest -q` | 42 passed; deprecation warnings from FastAPI/SWIG remain |
| `npm --prefix frontend test` | 8 passed, including 5 mocked-API DOM workflow tests |
| `npm --prefix frontend run build` | Passed; non-blocking bundle-size warning remains |
| `npm --prefix frontend audit` | 0 known vulnerabilities at verification time |
| Backend first startup | Created schema/database and exactly two authored demo courses |
| Fresh E5 cache + explicit demo lesson | Public weights downloaded; lesson completed with 2 source citations in about 66.6 seconds including initial loading |
| Demo selection question, note and position | Passed via normal HTTP API; reopening returned the same lesson |
| Frontend HTTP/proxy | Passed; instance ID matched the isolated backend |
| `python -m evals.retrieval` | Passed with actual local E5 and public model metadata access |
| `python -m evals.teaching_policy` | Passed against the initialized authored-demo database |
| Reading and mastery policy | Deterministic date/concurrency/evidence tests passed; not a longitudinal learner study |

The usual README commands were run from the clean project root. To keep the original learning service untouched, the smoke servers used ports 18000/15173 and the documented `TUTOR_BACKEND_URL` override. The fresh model download used a separate `HF_HOME`; dependency installers used their ordinary public registries. No paid model or embedding API call was made for publication.

The independent CLI checks `python -m evals.retrieval` and `python -m evals.teaching_policy` are intended to run after initialization. Retrieval uses local PyTorch/E5 inference; network access may be used by Hugging Face for model metadata checks even with cached weights.

## Scope of evidence

Mocked tests cover generation start/idempotency/retry, course routes, material correction, aliases and SQL filtering before pagination, manual mastery/background/merge, reading progress, notes, practice evidence and date limits. The release also includes an in-memory, project-authored PDF parsing test; no private PDF is needed.

Real model integration was sampled during development with authored demo material. It was not rerun for this publication, and no personal responses or private-course acceptance records are included. Paid route reordering, live ambiguous piggyback resolution and open-ended high-level grading have not received a fresh live acceptance pass. Browser layout and real pointer interaction are not claimed as E2E-tested. Formula correctness and teaching quality require human review; no fixed token-saving percentage is claimed.
