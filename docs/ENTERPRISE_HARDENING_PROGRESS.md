# Enterprise hardening: progress log (2.0.0)

Branch `v2-enterprise-hardening` was created from `7689edd`, the recovered local master; its base is GitHub `e3efe37`. **Nothing was pushed, merged, deployed or submitted to any real employer or portal.**

| Pass | Status | Evidence |
|---|---|---|
| 0. Recovery of the repo clobbered by the ReuseFirst build (three-way merge) | done, commit `7689edd` | 132 tests passed, 7 modules at 100% |
| P0 endpoint security, health, migrations | done | `test_security` |
| P0 application policy, consent, gate (fail closed) | done | `test_policy`, `test_policy_ui`, mutation tests |
| P0 state machine, submission keys, OUTCOME_UNKNOWN, crash recovery | done | `test_policy`, `test_properties`, E2E `agg_dup` and nocon resolve |
| P0 audit log | done | `test_policy.Audit` |
| P1 Claude contract (citations, minimisation, fencing, model check, retries, budget, usage) | done | `test_ai` |
| P1 resume parser limits | done; fuzzing found and fixed a crash | `test_properties` |
| P1 verification: fuzz/property tests, mutation tool, benchmark, CI, SBOM, secret scan, SAST, pip-audit | done; CI written but not run on GitHub | TEST_MATRIX.md, SECURITY_TEST_REPORT.md |
| P1 enterprise platform (tenancy, SSO, KMS, queues) | **NOT IMPLEMENTED** (design only) | ENTERPRISE_DEPLOYMENT.md |
| P2 UI: policy panel, consent dialog, new statuses | done | `test_policy_ui`, `test_ui_static` |
| Docs (the 13 required, plus migration guide and changelog) | done | docs/ |

## Defects found during the work (beyond the baseline register)
1. Naukri drawer "Save" and the one-click Apply sent applications without a gated button. Now gated.
2. Fuzzing found that a corrupted DOCX raised `ValueError`, which became a 500 error. Fixed.
3. SAST review found that "Delete all data" left the 2.0 tables (submissions, audit, ai_usage) in place. Fixed and tested.
4. A test-only race: closing a SQLite connection used by other threads caused an intermittent segmentation fault under coverage. The test was rewritten. Production code never closes a shared connection.
5. Test isolation: a failed portal test left jobs queued on the shared browser thread, which hung the later modules. Teardown now cancels the queue.

## Blockers / NEEDS_USER
- **Push:** approved by the user ("push to main branch"); merged to `master` and pushed.
- **Live verification** of ATS sites and portals: needs explicit authorization and non-submitting test accounts or sandboxes.
- ~~Live Claude evaluation~~: done with the user's approval (33/33, AI_EVAL_RESULTS.md).
- **Legal review** of Naukri and LinkedIn automation terms, and DPDP/GDPR, by the user or counsel.
- **Container build:** no Docker or Podman on this machine.

## Commands to reproduce
```
tools/coverage.sh
.venv/bin/python tools/mutation.py
tools/scan_secrets.sh
.venv/bin/python tools/bench.py
```
