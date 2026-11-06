# Enhancement report: unattended auto-apply, 2026-11-06

- **Spec:** docs/ENHANCE_SPEC.md.
- **Standards:** UAISES §22 template and UEUIS Enterprise.
- **Session:** this session resumed build 20261106-071130-3f0256, which had stopped before finishing. The earlier attempt had already built the feature. This session added the missing tests, fixed two broken UI tests, wrote the docs, extended the coverage gate and verified everything.
- **Nothing was committed.** Every change is left in the working tree for review.
- **Previous report:** the report for the earlier job-portal enhancement was moved to docs/ENHANCE_REPORT_portals.md.

## Delivery summary
- **Outcome:** unattended mode is built.
  - **What it does:** while the app is running, it re-runs the saved searches the user switched on, every N minutes. It applies to each *new* job at or above the minimum score, through the existing company site → Naukri → LinkedIn flows.
  - **Off by default.** It is turned on with a review-and-confirm step. The first run is a dry run.
  - **Guards** (each skip is logged with its reason): pause (kill switch on every view), quiet hours, daily cap (deferred, not dropped), company cooldown, duplicate, already applied, hidden by you.
  - **Uncertain outcomes become Needs you**, with the specific reason. Nothing is guessed.
  - **Crash safety:** an "attempting" record is written before any Submit, so no application is ever sent twice.
  - **New views:** Today (daily summary with CSV export) and Run log.
  - **Other features:** auto-pause after 3 failed runs in a row, Run now, a per-search switch, and delete-all cleanup.
- **Changed files (this enhancement):**
  - New: `app/unattended.py` (scheduler, guards, recovery, summary and log).
  - `app/server.py`:
    - Routes: `GET /api/unattended`, `/summary`, `/summary.csv` and `/runs`; `PUT /api/unattended/settings` and `/searches/<id>`; `POST /api/unattended/pause`, `resume`, `off`, `run`, `preview` and `jobs/<id>/dismiss`.
    - Delete-all now also stops runs and removes screenshots.
  - `app/db.py`: the `ua_runs` and `ua_jobs` tables, created additively, and the delete-all list.
  - `app/search.py` and `app/sources.py`: a `fresh=` option that bypasses the 1 h cache (D53).
  - `app/apply.py` and `app/autoapply.py`: two hook call sites (`before_action`, `before_click`), which the kill switch and quiet hours use. The apply logic is otherwise unchanged.
  - UI: `static/index.html`, `static/app.js` and `static/app.css`. They add the status bar and kill switch on every view, the Unattended, Today and Run log sub-views of Applications, and the Resume confirmation dialog.
  - New tests:
    - `tests/test_unattended.py` (22 tests).
    - `tests/test_unattended_ui.py` (5 real-Chromium UI journeys).
    - **New this session:** `tests/test_unattended_browser.py` (a real-Chromium run of the real apply flow against `tests/fake_careers.py`, with a network guard).
  - `tools/coverage.sh`: `app/unattended.py` was added to the 100% line and branch gate.
  - Docs: README.md, docs/DECISIONS.md (D40–D53), docs/LEARNINGS.md, docs/REQUIREMENTS.md (new traceability section), docs/UI_STATES.md, docs/ACCESSIBILITY.md and this report.
- **Architecture decisions:**
  - D40: storage and migration.
  - D41: what a "saved search" is.
  - D42: what counts as "new".
  - D43: dry-run preview first.
  - D44: timer and one catch-up.
  - D45: no overlap, one browser.
  - D46: guard order, and the daily cap counts unattended submissions.
  - D47: pause and quiet hours via the hooks.
  - D48: the crash-safe "attempting" record.
  - D49: Submitted needs both a confirmation and a screenshot.
  - D50: auto-pause.
  - D51: optional features (O1 in-page, O2 built, O3 not built).
  - D52: no personal data in the logs.
  - D53: unattended runs bypass the cache.
- **User-visible behaviour verified:**
  - J1, J3, J4, J5 and J6 in real Chromium against the app's own server (`tests/test_unattended_ui.py`).
  - J2 (a scheduled run with real searching and scoring) is covered by `tests/test_unattended.py`.
  - The real apply flow ran end to end in headless Chromium against local fake careers pages (`tests/test_unattended_browser.py`). No real employer, Naukri or LinkedIn site was contacted.

## Requirements → tests traceability
The full table is in docs/REQUIREMENTS.md, section "Enhancement 2026-11-06 (2)". In short:

| ID | Test |
|---|---|
| M1 | test_unattended `test_m1_…`, `test_migration_…`; test_unattended_ui `test_j1_…` |
| M2 | `test_m2_no_overlap_and_one_catch_up_after_downtime`, `test_timer_waits_while_off_and_ticks_when_due` |
| M3 | `test_m3_only_new_postings_at_or_above_the_minimum` (10 / 4 / 2) |
| M4 | `test_m4_guards_…`, `test_cooldown_…`; test_unattended_browser (later run skips them) |
| M5 | `test_m5_quiet_hours_skip_and_stop_before_submit` |
| M6 | test_unattended_browser (real confirmation + PNG ≤ 1 MB, 0600); `test_m6_…` |
| M7 | test_unattended_browser (real CAPTCHA form and sign-in wall: no POST); `test_m6_m7_outcomes_are_never_guessed` (10 reasons) |
| M8 | `test_m8_pause_stops_the_form_within_5s_and_survives_a_restart`; UI `test_j3_…` (all 8 views, keyboard, < 5 s) |
| M9 | `test_m9_interrupted_attempt_becomes_needs_you_and_is_never_retried` |
| M10 / M11 | `test_m6_submitted_with_proof_m10_run_log_and_m11_summary`; UI `test_j4_j5_…`, `test_j6_…` |
| M12 | `test_m12_one_failing_source_does_not_stop_the_run_and_s1_auto_pause` |
| M13 | UI `test_m13_reflow_320_themes_names_and_settings_error_state`; tests/test_ui_static.py |
| M14 | `test_m14_hosts_allow_listed_and_no_resume_text_sent_or_logged`; test_unattended_browser network guard |
| M15 | Full suite green; no existing test was edited |
| S1–S5, O2 | See REQUIREMENTS.md (all Pass) |
| O1 | Built (live-region notice), not asserted by a test: UNVERIFIED |
| O3 | Not built (D51) |

## Verification evidence
| Check | Command / method | Result | Evidence |
|---|---|---|---|
| Suite at the start of this session | `.venv/bin/python -m unittest discover -s tests -t .` | 119 run, **1 error, 1 failure**, 387 s | The error was in `test_unattended_ui` (test_j3 and test_j4_j5 when run alone): a string `wait_for_function` that the app's CSP (`script-src 'self'`, no unsafe-eval) blocks. Fixed by using function predicates. The failure was `test_rank_1000_under_1s` at 1.20 s, while a second browser suite ran in parallel. It passes alone (3 of 3 with the venv). |
| Spec baseline (before the earlier attempt) | from ENHANCE_SPEC `_resume` | 92 passed, 0 failed | — |
| Full suite + coverage gate (after) | `bash tools/coverage.sh` | **120 tests OK**, 418 s, exit 0 | Gate: apply 511/178, autoapply 159/48, details 161/76, direct 72/10, portals 62/16, **unattended 491/176**: all **100% line and branch**. Whole app 96%. |
| New real-browser run | `.venv/bin/python -m unittest tests.test_unattended_browser` | 1 test OK, 16 s | Submitted with a proof screenshot. CAPTCHA and sign-in wall parked as Needs you. No re-apply. Browser guard list empty. |
| UI journeys | `.venv/bin/python -m unittest tests.test_unattended_ui` | 5 tests OK, 31 s | Covers keyboard, names, ≥ 24 px, focus ≥ 2 px, 320 px in both themes, every state, and Descriptions. |
| System python (CLAUDE.md command) | `python3 -m unittest discover -s tests -t .` | 120 run, 12 skipped, **2 failures** | 1) `test_apply_branches…test_call_propagates_errors_and_context_failures`: Playwright isn't installed for the system python. This environmental failure is known from the previous report. 2) `test_rank_1000_under_1s` fails consistently under the system python (about 1.0–1.2 s against a 1.0 s limit). It passes with the venv. The scoring cost comes from D23 (skills re-detected at scoring time, an earlier enhancement). This change doesn't touch matching.py or skills.py. Both are reported, not hidden. |
| App launch via `./run.sh` | Attempted on port 5093 with a temp DB | **UNVERIFIED** | This session's permission mode blocked starting a server. `server.make_server` with the real index.html and app.js is exercised by the browser tests. |

## Release quality gates (UAISES §18)
| Gate | PASS / FAIL / N/A / UNVERIFIED | Reason |
|---|---|---|
| Requirements | PASS | M1–M15 and S1–S5 have acceptance tests. O2 is built and tested. O1 is built but untested. O3 is deliberately not built (D51). |
| Real functionality | PASS (fakes) / UNVERIFIED (real sites) | The real search and scoring code runs, and so does the real Playwright apply flow, against realistic local fakes. The live employer and portal DOMs were not used, because the spec forbids that. |
| Code quality | PASS | The feature lives in one module. Shared code changed only at two hook call sites and the `fresh` flag. There is no unrelated refactoring. No linter is configured. |
| Testing | PASS | Unit, route, integration, real-browser end-to-end, and UI tests. 100% line and branch on the 6 gated modules. Existing tests are unedited. |
| Security | PASS | Server binds to 127.0.0.1. Cross-site writes return 403 (tested). Resume needs `confirm`. Outbound requests go only through `ALLOWED_HOSTS` (tested), and the browser reaches only the apply targets (guard tested). Logs contain no resume text (tested). Proof files are 0600 in a 0700 folder. No passwords, no account creation, no CAPTCHA solving. No innerHTML (static test). A formal threat model and a scanner were not run. |
| Reliability | PASS | "attempting" is written before Submit, and recovery runs on start (M9). Runs never overlap. One failing source never aborts a run. Pause and settings persist. Auto-pause after 3 run-level errors. |
| Performance | PASS / UNVERIFIED | Pause takes < 5 s (tested). The 500-row summary API takes < 1 s, and < 1.5 s including rendering. API p95 < 300 ms during a real run, ≤ 200 ms settings saves, and ≤ 500 MB peak memory were not measured. |
| Packaging | PASS | No new dependencies or install steps. The migration is additive and idempotent (tested). The mode is off after an upgrade. |
| Operations | PASS | The run log shows every run, trigger, source error and job outcome. The banner explains an auto-pause. Rollback: Turn off, or Pause. |
| Velocity | PASS / UNVERIFIED | The unattended tests run quickly without the browser. The full gate takes 418 s, which exceeds the ≤ 5 min target because the real-browser suites dominate. |

## UI gate table (UEUIS §20)
| Gate | PASS / FAIL / N/A / UNVERIFIED | Reason |
|---|---|---|
| Interaction principles | PASS | Self-descriptive: the status bar always shows the mode, the next run, today against the cap, and Needs you. Controllable: pause and resume from every view, Turn off. Error tolerant: inline fixes and a confirm step before enabling. Conforms to expectations: links navigate, buttons act. |
| States | PASS | docs/UI_STATES.md covers the status bar, Unattended, Today and Run log. Loading, empty, error with Retry, success, busy and disabled states are asserted in test_unattended_ui. |
| Accessibility | UNVERIFIED | The automated parts pass: keyboard reachability of the kill switch on all 8 views, accessible names, focus ≥ 2 px, targets ≥ 24 px, 320 px reflow, live regions (assertive and polite), and the static contrast checks. axe-core, a screen reader, 200% zoom and Firefox/Safari were not run. |
| Theming | PASS | The contrast tests pass in both themes. The status bar differs between light and dark (tested). The theme choice is remembered. |
| Palette and restraint | PASS | "Pine & Paper" / "Pine Night" are unchanged. No gradients, glows or glass (static test). |
| Organised screens | UNVERIFIED | No §8.20 browser test for inner scroll bars or empty bands on the new views. |
| Honest actions | PASS | Run now returns "Not started: <reason>" when a guard says no. Submitted only with a confirmation and a screenshot. |
| Focus and descriptions | PASS | The new explanatory text uses `.desc` and is hidden by the switch. Hints and errors stay visible (tested). The run log and summary are on their own sub-views. |
| Restraint and density | PASS / UNVERIFIED | One primary button per view (static test). No human review of screenshots at 1366×768 and 1536×864. |
| Professional polish | UNVERIFIED | Busy states on Save, a first-run empty state, real tables, and no nested interactive elements. No human screenshot review per theme. |
| Rich interface | PASS | Live status with the next run and the current job. Scores are shown in the tables. Per-run expandable job lists. |
| Visual design | PASS / UNVERIFIED | Tokens only. Status uses text plus an icon (✓ / ! / – and ▶ / ❚❚ / ○), never colour alone. No visual review by a person. |
| Responsive | PASS / UNVERIFIED | 320 px with no horizontal scroll on the new views in both themes (tested). 200% zoom not run. |
| Content | PASS | Plain-language summaries ("Up to 15 applications a day to jobs scoring 80 or more, every 60 min, not between 22:00 and 07:00") and reasons ("company cooldown until 2026-12-01", "Interrupted, check whether it was submitted"). |
| Forms | PASS | Visible labels and units, an error summary with links, inline errors, `aria-invalid`, input kept, and nothing saved on error (tested in the API and in the browser). |
| Truthfulness | PASS | Counts come from the run log. Submitted only after verified proof. "status unavailable" is shown when the server can't be reached. |
| Ethics | PASS | Off by default, explicit confirmation, a visible kill switch, and no dark patterns. |
| i18n | N/A | The app is not localised. |
| Performance | UNVERIFIED | Not measured on target hardware during real runs. |
| Usability | UNVERIFIED | No §16 evaluation with users. |
| Tests | PASS | The §17 automated suite is green, and every journey of this change (J1–J6) is covered. |

## Risks and remaining work
- **Blockers:** none.
- **Unverified claims:**
  - Behaviour against live employer, Naukri and LinkedIn pages.
  - The `./run.sh` launch (blocked by the permission mode).
  - axe-core, a screen reader, 200% zoom, and Firefox/Safari.
  - API p95 and memory during a real run.
  - The O1 announcement (not asserted by a test).
- **Known limitations:**
  - The daily cap counts unattended submissions only (D46).
  - The first enable always runs a dry run first (D43).
  - On a company form with a CAPTCHA, the visible fields may already be filled before the CAPTCHA is detected. Nothing is ever typed into the CAPTCHA frame or submitted.
  - Screenshot expiry (O3) is not built.
  - `test_rank_1000_under_1s` fails under the system `python3` on this machine. This predates this change.
  - Automated applying may break the Naukri and LinkedIn terms of use (see README).
- **Rollback:**
  - Instant: **Pause auto-apply**, or **Turn off** in Applications → Unattended. Settings are kept.
  - Full: discard the uncommitted working-tree changes. The `ua_*` tables are additive and harmless if left behind.
- **Next highest-value step:** turn the mode on with one saved search, a cap of 1 and the browser visible. Read the dry-run results in the Run log, then let one real run happen while you watch. Record what the live sites did in docs/LEARNINGS.md.
