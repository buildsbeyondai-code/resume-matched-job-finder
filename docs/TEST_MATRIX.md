# Test matrix: 2.0.0

Commands (run from the repo root):

| Gate | Command | Result on `v2-enterprise-hardening` |
|---|---|---|
| Full suite + 100% branch coverage on the safety modules | `tools/coverage.sh` | **187 passed, 0 failed, 0 skipped** (~5.5 min) |
| Coverage | (same) | `ai, apply, audit, autoapply, details, direct, policy, portals, states, unattended`: **100% line and branch**; `db` 100%; whole app 97% |
| Mutation tests | `.venv/bin/python tools/mutation.py` | **17/17 killed** |
| Secret and personal-data scan | `tools/scan_secrets.sh` | clean |
| SAST | `bandit -r app` (separate venv) | 0 High; Medium triaged (SECURITY_TEST_REPORT.md) |
| Dependency audit | `pip-audit -r requirements-lock.txt` | no known vulnerabilities |
| Benchmark | `.venv/bin/python tools/bench.py` | see below |

## What each area is tested with
| Area | Unit / contract | Property / fuzz | Browser E2E (real Chromium, local fakes) | Live |
|---|---|---|---|---|
| Policy, consent, gate | test_policy | — | test_policy_ui (consent dialog) | n/a |
| Submission idempotency, state machine | test_policy (exhaustive pairs, 20-thread race) | test_properties (random walks) | test_autoapply_integration (`agg_dup`), test_portals_integration (unknown outcome, resolve) | n/a |
| Greenhouse / Lever forms | test_apply, test_apply_branches | — | test_autoapply_integration, test_unattended_browser | **NOT VERIFIED** |
| SmartRecruiters / Ashby / Workable | test_direct (lookup), test_unit (adapters) | — | **no form fixture (gap)** | **NOT VERIFIED** |
| Naukri / LinkedIn (synthetic) | test_portals_unit | — | test_portals_integration (one-click, drawer, Easy Apply, walls, CAPTCHA, unknown outcome) | **NOT VERIFIED** |
| Claude answers | test_ai (fake CLI: citations, minimisation, fencing, model check, retries, budget) | — | test_ai_integration | `tools/ai_eval.py`: 3 live calls, 33/33 checks (AI_EVAL_RESULTS.md) |
| Resume parsing | test_unit | test_properties (300 mutated files, bombs) | — | — |
| Server security, health, migrations | test_security, test_integration | test_properties (URLs) | — | — |
| UI | test_ui_static (names, contrast tokens, one primary action, no innerHTML) | — | test_ui_browser, test_unattended_ui, test_policy_ui | manual WCAG review NOT VERIFIED |
| Unattended mode | test_unattended | — | test_unattended_ui, test_unattended_browser | NOT VERIFIED |

## Fault injection covered
| Fault | Covered |
|---|---|
| Crash or exception after the Submit click | `Runner.test_crash_after_click…` |
| Cancel during Submit | yes |
| Browser error during Submit | yes |
| App restart while SUBMITTING | yes |
| Click failure | yes |
| CAPTCHA after Submit | yes |
| Validation errors (twice) | yes |
| Expired consent | yes |
| Changed scope | yes |
| Sign-in walls | yes |
| CAPTCHA walls | yes |
| Claude timeout, 529 retry, wrong model, garbage output | yes |
| Database unavailable (`/readyz` 503) | yes |
| Concurrent claims | yes |

Not covered:
- network partition mid-request to a real site;
- a partial database commit (SQLite transactions are relied on);
- a changed ATS form layout on a real site.

## Benchmark (this machine, WSL2, 2026-11-06; `tools/bench.py` defaults)
| Metric | Value |
|---|---|
| API latency, 8 concurrent clients, 400 requests over 5 endpoints | p50 15.5 ms, p95 30.0 ms, p99 36.2 ms |
| Throughput | 518 requests/s |
| Ranking | 2,000 postings in 1.07 s (~1,865/s, cold skill cache) |
| Peak RSS | 44.5 MB |

These are single-machine measurements, not an SLO. No production history exists.
