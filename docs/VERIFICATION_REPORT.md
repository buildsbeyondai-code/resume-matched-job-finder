# Independent verification report

Written by ReuseFirst after the build, not by the agent that wrote the code. Commands ran in the sandbox with no network.

- Job: `20261106-114538-c310d6`
- Result: **SUCCEEDED**
- Verified at: 2026-11-06 11:50:23

| Gate | Mandatory | Status | Detail |
|---|---|---|---|
| Required documents present | yes | VERIFIED | 5 documents present |
| Syntax valid | yes | VERIFIED | 32 Python file(s) parse; 2 JavaScript/TypeScript file(s) NOT VERIFIED (no node in the verifier) |
| No secrets in files | yes | VERIFIED | no known secret patterns |
| Test suite passes (re-run independently) | yes | VERIFIED | 120 passed, 0 failed, 0 skipped in 280.05 s: ./.venv/bin/python -m unittest discover -s tests |
| Existing tests still pass | yes | VERIFIED | before: 92 passed; after: 120 passed |
| Dependency inventory (CycloneDX SBOM) and pinning | no | VERIFIED | 6 dependencies in docs/sbom.cdx.json; all pinned |
| Known-vulnerability check (OSV) | yes | VERIFIED | no known vulnerabilities (OSV) |
| Reuse provenance and licences | yes | N/A | no external code selected |
| Static analysis (ruff) | no | NOT VERIFIED | ruff not installed in the project |
| Committed to git | no | N/A | git not requested |
| App starts and serves (sandboxed) | yes | VERIFIED | GET / on 127.0.0.1:49869: HTTP 200 (port set with RMJF_PORT) |

Statuses: VERIFIED (checked and passed), NOT VERIFIED (could not be checked), FAILED, BLOCKED (check could not run), N/A.
