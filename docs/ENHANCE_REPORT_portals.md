# Enhancement report: job-portal fallback (Naukri, then LinkedIn), 2026-11-06

Spec: docs/ENHANCE_SPEC.md. Standards: UAISES §22 template, UEUIS Enterprise. Nothing was committed: every change is left in the working tree for review.

## Delivery summary
- **Outcome:** Auto apply and Apply now finish jobs that have no company-site application, on Naukri and then LinkedIn:
  - They search the portal and accept a result only when the company matches and the title similarity is ≥ 0.6. Ties go to the result in the job's city, then the newest.
  - They prefer the portal's "Apply on company site" button when it is shown.
  - On Naukri they fill the chat drawer (contenteditable answers, radio chips, Save). On LinkedIn they fill the Easy Apply dialog (Next, Review, Submit application). A Naukri one-click Apply also works.
  - A job is marked Submitted only after a new confirmation text appears. A screenshot is kept as proof.
  - Portal work runs one job at a time, with a configurable pause, a visible countdown and Cancel.
  - Settings has Naukri/LinkedIn toggles (both on by default), the pause (5–600 s, default 20), and portal sign-in with a status check.
  - Applications shows "via company site / via Naukri / via LinkedIn" and an "Open in browser" link for Needs-you items.
  - Sign-in walls, CAPTCHAs, no match and no confirmation park the job as "Needs you" with the reason. The app never bypasses, guesses or creates anything.
- **Changed files:**
  - `app/apply.py`:
    - `_portal` / `_portals` / `_blocked` / `_wall` / `_register` / `_hook`.
    - `_company_site(search=)` and `_find_form(force)` with a single click.
    - `_open` now has the portal branch, and `_auto` handles the portal outcomes, the no-confirmation rule and step progress.
    - The scanner handles chat-drawer questions.
    - Sign-in and status API added; the profile directory is now 0700.
  - `app/autoapply.py`: per-job hooks (step, rate-limit gate), Cancel / `Cancelled`, `busy()`, and the stored `channel`.
  - `app/portals.py`: configurable base URLs, `search_url`, `signin_url`, `signed_in`, `usable` (test-mode guard), `age_days`, and the new `best_result` ranking.
  - `app/config.py`: `BROWSER_PORTAL_HOSTS`, `PORTAL_PAUSE_RANGE`, and defaults `fallback_portals` / `portal_pause_s`.
  - `app/server.py`: `settings_error` validation (nothing is saved on error), `GET /api/portals`, `POST /api/portals/{check|naukri/signin|linkedin/signin}` (409 during a run), and `POST /api/autoapply/cancel`.
  - UI files: `static/index.html`, `static/app.js`, `static/app.css`.
  - `tools/coverage.sh`: portals.py added to the 100% gate.
  - Tests:
    - `tests/fake_careers.py`: fake Naukri and LinkedIn search, job pages, chat drawer, Easy Apply dialog, one-click Apply, company-site button, sign-in and CAPTCHA walls, no-confirmation case, and a request log.
    - New test files: `tests/test_portals_integration.py`, `tests/test_portals_unit.py` and `tests/test_ui_browser.py`.
  - Docs: README.md, docs/DECISIONS.md (D29–D39), docs/LEARNINGS.md, docs/REQUIREMENTS.md, docs/UI_STATES.md, docs/ACCESSIBILITY.md and this report.
- **Architecture decisions:**
  - D29: portal fallback order and matching.
  - D30: portal hosts are allowed for the browser only (not `ALLOWED_HOSTS`).
  - D31: one pause per job, counted from the previous portal job's end.
  - D32: Applied only after confirmation; Submit is never pressed twice.
  - D33: walls park the job.
  - D34: **deviation**: an interrupted job is still "failed + Retry", not re-queued (M13 and safety).
  - D35: in test mode only loopback portals are used.
  - D36: sign-in status from cookies.
  - D37: cancel semantics.
  - D38: O1 not built.
  - D39: channel stored in the existing JSON (no SQL migration needed).
- **User-visible behaviour verified:**
  - J1–J8 were run end to end in real headless Chromium against local fakes (PortalFallbackIntegration).
  - The Settings and Applications screens were checked in a real browser (UIBrowser).
  - No real Naukri, LinkedIn, Adzuna or employer site was contacted. A browser-level network guard records any non-loopback request, and it recorded none.

## Requirements → tests traceability
See docs/REQUIREMENTS.md, section "Enhancement 2026-11-06". In short:

| ID | Test |
|---|---|
| M1 order | test_portals_integration (request-log order) |
| M2 company-site button | test_portals_integration (Naukri → fake Lever form, "via company site") |
| M3 matching / next portal / not found | test_portals_integration + test_portals_unit.test_best_result_company_title_location_and_age |
| M4 Naukri chat + one-click | test_portals_integration (answers received: years "16", relocate "Yes") |
| M5 LinkedIn Easy Apply | test_portals_integration (`pressed == [Next, Review, Submit application]`) |
| M6 no confirmation / screenshot | test_portals_integration (needs_you "Confirmation not found", no proof; proof PNG < 1 MB for Applied) |
| M7 settings | test_portals_unit.test_m7_defaults_validation_and_persistence + test_ui_browser.test_m7_settings_portal_journey |
| M8 pause / serial | test_portals_integration (`portal_starts` gaps ≥ 2 s) + test_portals_unit.RunnerGate |
| M9 walls / portals off / no Adzuna POST | test_portals_integration |
| M10 merged question | test_portals_integration (one question for 3 jobs; saved as "Willing to relocate: Yes"; all resume) |
| M11 via labels / empty | test_ui_browser.test_m11_applications_via_labels_progress_and_empty_state |
| M12 coverage + network guard | tools/coverage.sh; `self.left == []` |
| M13 existing tests | full suite green, existing test files unedited |
| S1 sign-in | test_portals_integration, test_portals_unit.test_portal_routes_cancel_and_busy |
| S2 live progress | test_ui_browser + test_portals_integration |
| S3 Open in browser | test_ui_browser |
| Migration | test_portals_unit.test_migration_of_an_existing_install |
| Security: profile 0700 | test_portals_unit.ProfilePermissions |

## Verification evidence
| Check | Command / method | Result | Evidence |
|---|---|---|---|
| Baseline suite (before) | `bash tools/coverage.sh` | **79 tests OK**, 85.6 s; gate 100% (apply 412 stmts / 140 branches, autoapply 109/34, details 161/76, direct 72/10); portals.py 29% (not gated, not wired) | Run at the start of the session |
| Baseline with system python (before) | `python3 -m unittest discover -s tests -t .` | 79 run, 1 failure, 2 skipped | The failure is environmental: Playwright isn't installed for the system python, so the "system libraries" message test gets "not installed". The same failure remains under the system python after the change. The venv run is the reference. |
| Full suite (after) | `bash tools/coverage.sh` | **92 tests OK**, 222.3 s, exit 0 | Gate: apply 509/178, autoapply 156/48, details 161/76, direct 72/10, portals 62/16, **all 100% line and branch** |
| Portal end-to-end | `.venv/bin/python -m unittest tests.test_portals_integration` | OK, 128 s (9 jobs, 2 rounds, 2 s pauses, the cancel case) | Network guard list empty |
| Browser UI tests | `.venv/bin/python -m unittest tests.test_ui_browser` | 3 tests OK, 8.5 s | Keyboard, names, focus, 24 px targets, 320 px, themes, errors |
| Fast unit inner loop | `python3 -m unittest tests.test_portals_unit tests.test_ui_static tests.test_unit` | 41 tests OK, 3.5 s | — |
| Static UI conformance | tests/test_ui_static.py | 10 OK (contrast both themes, labels, no innerHTML, no gradients, one primary per view) | — |
| App launch via `./run.sh` | Not run: this session's permission mode blocked starting a server process | UNVERIFIED | The same `server.make_server` + real index.html/app.js are exercised by test_ui_browser and the integration tests |

## Release quality gates (UAISES §18)
| Gate | PASS / FAIL / N/A / UNVERIFIED | Reason |
|---|---|---|
| Requirements | PASS | M1–M13 and S1–S4 have acceptance tests (traceability above). O1 is deliberately not built (D38). The restart behaviour deviates from the spec (D34, documented). |
| Real functionality | PASS (against fakes) / UNVERIFIED (real portals) | Every behaviour runs real Playwright actions on realistic fake pages. The real Naukri and LinkedIn DOMs were not used (the spec forbids testing against them), so selectors such as the chat-drawer class names and button texts may need tuning on the live sites. |
| Code quality | PASS | The diff only touches the apply feature, settings and UI. Existing conventions are kept. No unrelated refactoring. No linter is configured in the project. |
| Testing | PASS | Unit, route, integration and real-browser end-to-end tests. 100% line and branch on the 5 gated modules. All 79 pre-existing tests still pass, unedited. |
| Security | PASS | Server binds to 127.0.0.1. Portal hosts are browser-only (D30). No passwords are stored. The profile is 0700. No CAPTCHA bypass, account creation or Adzuna apply path, each enforced by a test. Writes keep the Origin/Host checks. No innerHTML in app code. Test mode can't reach real portals (D35). A formal threat review or scanner was not run. |
| Reliability | PASS | One job's failure parks only that job (continues to the next). Submit is never pressed twice on a portal. Cancel never marks a job Applied. Interrupted jobs → "failed, Retry" (D34). |
| Performance | PASS / UNVERIFIED | The portal search and match on the fakes takes a few seconds per job (the whole 9-job run including 2 s pauses passes in 128 s). The API answers in < 1 s during a run (asserted). The ≤ 200 ms target and the ≤ 30 s on the real sites were not measured. |
| Packaging | PASS | No new dependencies or install steps. Existing databases work unchanged (migration test). |
| Operations | PASS | Every parked job shows its reason and next step in the UI. Browser errors are recorded on the job. Rollback = turn both portals off in Settings, or revert the working-tree change. |
| Velocity | PASS | Inner loop 41 tests in 3.5 s; full gate 222 s (≤ 5 min target); the queue drains without fixed intervals except the user-set pause. |

## UI gate table (UEUIS §20)
| Gate | PASS / FAIL / N/A / UNVERIFIED | Reason |
|---|---|---|
| Interaction principles | PASS | Self-descriptive: the current job, step and channel are always shown. Controllable: Cancel at every stage, Retry. Error tolerant: each Needs-you reason says what to do next. Conforms to expectations: links navigate, buttons act. |
| States | PASS | docs/UI_STATES.md lists every state of Applications and Settings. Empty, loading/progress, error, partial and success are covered by test_ui_browser and the integration test. |
| Accessibility | UNVERIFIED | The automated parts pass: keyboard reachability, names, focus ≥ 2 px, 24 px targets, reflow at 320 px, live regions, contrast tokens. axe-core, a screen reader, and 200% zoom / text spacing were not run. |
| Theming | PASS | No new colour tokens. The contrast tests pass in both themes. The browser test shows light and dark differ in the header, navigation and background, and the choice is remembered. |
| Palette and restraint | PASS | "Pine & Paper" / "Pine Night" unchanged. No gradients, glows or glass (static test). The new controls are text buttons. |
| Focus and descriptions | PASS | The portal explanation uses `.desc` (hidden by the Descriptions switch). Errors and data never use it. |
| Restraint and density | PASS / UNVERIFIED | One primary button per view (static test). Screenshots at 1366×768 and 1536×864 were not reviewed by a person. |
| Professional polish | UNVERIFIED | There are busy states on the sign-in buttons and Cancel, and no nested interactive elements. No human screenshot review per theme. |
| Rich interface | PASS | Live run: current step, countdown with server-time correction, queue size, and the "via" channel chip. |
| Visual design | PASS / UNVERIFIED | Tokens only, and status is never shown by colour alone (chips carry text). No visual review by a person. |
| Responsive | PASS / UNVERIFIED | 320 px with no horizontal scroll on all tabs (browser test). The 200% zoom check was not run. |
| Content | PASS | Plain-language reasons ("Naukri needs a sign-in. Open Settings, select Sign in to Naukri, …", "Confirmation not found: …"). |
| Forms | PASS | Visible labels, a unit, min/max, an inline error linked by aria-describedby, aria-invalid, focus moves to the field, input kept, nothing saved on error (browser and API tests). |
| Truthfulness | PASS | Submitted only after a verified confirmation. Sign-in status says "not checked yet" until it is actually checked. |
| Ethics | PASS | No dark patterns. The README and the Settings text warn about portal terms of use, and portals can be turned off. |
| i18n | N/A | The app is not localised. |
| Performance | UNVERIFIED | Not measured on target hardware against the real portals. |
| Usability | UNVERIFIED | No §16 evaluation with users. |
| Tests | PASS | The §17 automated suite is green, and every primary journey of this change is covered. |

## Risks and remaining work
- **Blockers:** none.
- **Unverified claims:**
  - Behaviour on the live Naukri and LinkedIn DOMs (selectors, the chat-drawer class names, the LinkedIn review step and the session cookie names) is untested, because the spec forbids live tests.
  - Not run: axe-core, a screen reader, 200% zoom, and Firefox/Safari.
  - Not measured: API latency ≤ 200 ms and proof screenshot size on real pages (a full-page PNG of a long portal page can exceed 1 MB).
  - Launch via `./run.sh` was not run (permission mode).
- **Known limitations:**
  - O1 (per-run cap) is not built.
  - An interrupted job is not auto-re-queued (D34).
  - Navigation timeouts are the existing 45 s, not the 30 s per step in the spec.
  - The browser tab of a cancelled job stays open until the next run.
  - LinkedIn and Naukri may change their UIs, and the portals' terms restrict automation (accepted by the user in the spec).
- **Rollback:** untick both portals in Settings (instant, no code change), or discard the uncommitted working-tree changes.
- **Next highest-value step:** sign in once and run one supervised Auto apply on a single real Naukri job and a single LinkedIn Easy Apply job with the window visible. Then tune the selectors (`chatbot_Drawer`, `botMsg`, the radio-chip containers) and the `nauk_at`/`li_at` cookie names if needed, and record the results in docs/LEARNINGS.md.
