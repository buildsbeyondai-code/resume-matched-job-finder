# Requirements → acceptance criteria → tests

| ID | Acceptance criterion (summary) | Test(s) | Status |
|---|---|---|---|
| M1 | A 2-page PDF is parsed in ≤ 5 s into editable skills, titles and years | test_unit.ResumeTests.test_m1_pdf_extracts_profile_within_5s, test_m1_docx, test_parse_5_page_pdf_under_5s; test_integration.test_j1_upload_edit_profile | Pass |
| M1-error | A 15 MB or corrupted file gives a reason (size/format) and the control is re-focused for retry | test_unit.test_m1_error_oversize, test_m1_error_corrupted; test_integration.test_j1_upload_errors (refocus is in app.js uploadError, not browser-tested) | Pass (API); focus UNVERIFIED |
| M2 | Germany + Remote + Python returns only Germany-eligible remote jobs | test_unit.MatchingTests.test_m2_country_and_mode; test_integration.test_j2_j3_m2_m4_m5_m6_search_rank_dedupe | Pass |
| M2-empty | No matches gives an empty state with "Widen filters" | test_integration.test_m2_empty (API); UI markup #emptyResults/#widenBtn | Pass (API) |
| M3 | Requests go only to allow-listed endpoints | test_unit.NetworkPolicyTests.test_m3_allow_list; test_integration.test_m3_m8_only_allowlisted_and_no_resume_text | Pass |
| M4 | A full-match job scores higher than a no-match job, results are in descending order, and matched/missing skills are listed | test_unit.test_m4_scores_and_lists, test_seniority_downranks_mismatch; integration order check | Pass |
| M5 | The same job from two sources appears once with both sources | test_unit.test_m5_dedupe; integration (Greenhouse + Lever) | Pass |
| M6 | "View posting" opens the original URL in a new tab | integration (https URL); test_ui_static.test_no_unsafe_dom_and_new_tab_links | Pass (static) |
| M7 | A source times out, the others still show, and a banner names the failed source; per-source progress and cancel work | test_integration.test_m7_partial_failure, test_cancel; retry/backoff test_retry_backoff_max_two_retries | Pass |
| M8 | No request contains resume text, and data persists after restart | test_integration.test_m3_m8_only_allowlisted_and_no_resume_text, test_m8_persistence_after_restart_and_j4_j5, test_delete_all_data | Pass |
| S1 | Save and hide persist, and hiding can be undone | test_m8_persistence_after_restart_and_j4_j5 | Pass |
| S2 | Filter by date, company and minimum score (plus salary) | Client-side in app.js visibleJobs() | Not automated (UNVERIFIED) |
| S3 | "New since last search" marker | test_m8_persistence_after_restart_and_j4_j5 (J5) | Pass |
| S4 | Configurable sources and company list | test_sources_crud_and_csv | Pass |
| S5 | Light/dark theme following the system, with an override | test_ui_static contrast/themes tests | Pass (static) |
| O2 | CSV export | test_sources_crud_and_csv | Pass |
| O1, O3 | LLM explanation, scheduled refresh | Not built (D10) | N/A |
| NFR | Rank 1,000 postings in < 1 s; 1 h cache; key never exposed; CSP; localhost only | test_rank_1000_under_1s, test_identical_query_cached, test_settings_never_expose_keys, test_security_headers_origin_and_host | Pass |

## Enhancement 2026-11-06: job-portal fallback (docs/ENHANCE_SPEC.md)
Test file abbreviations: **PI** = tests/test_portals_integration.py `PortalFallbackIntegration.test_portal_fallback_journey` (real Chromium against the fake portals), **PU** = tests/test_portals_unit.py, **UB** = tests/test_ui_browser.py, **AB** = tests/test_apply_branches.py, **AI** = tests/test_autoapply_integration.py.

| ID | Acceptance criterion (summary) | Test(s) | Status |
|---|---|---|---|
| M1 | The company site is tried first; Naukri runs only after it finds no application (call order) | PI "M1" block (request-log order: /details/N before the Naukri search, Naukri before LinkedIn) | Pass |
| M2 | Portal "Apply on company site" is followed, the company form is submitted, and the job is recorded "via company site" | PI "M2" block (Naukri → fake Lever form, channel=company) | Pass |
| M3 | Only the row matching company + title is opened; no match → LinkedIn; no match on either → "Needs you: not found on portals" | PI "M3" block; PU `test_best_result_company_title_location_and_age` | Pass |
| M4 | Naukri chat drawer: text and radio-chip answers, Save per step, Applied after "You have successfully applied"; one-click Apply → Applied | PI "M4" blocks (answers received by the fake; one-click job) | Pass |
| M5 | LinkedIn Easy Apply: 3 steps, Next/Review/Submit application in order, Applied after "Your application was sent" | PI "M5" block (`pressed == [Next, Review, Submit application]`) | Pass |
| M6 | No confirmation → "Needs you: confirmation not found", not Applied; an Applied job has a screenshot (≤ 1 MB) | PI "M6" block and proof-file checks | Pass |
| M7 | Defaults on/on/20 s; Naukri off + 45 persists across a restart; out-of-range or non-numeric pause → inline error, nothing saved | PU `test_m7_defaults_validation_and_persistence`; UB `test_m7_settings_portal_journey` | Pass |
| M8 | Portal applications never overlap; successive starts ≥ the pause (2 s in tests) | PI "M8" block (`runner.portal_starts`); PU `RunnerGate` | Pass |
| M9 | A sign-in wall or CAPTCHA is never clicked → "Needs you" with the reason, and the run continues; Adzuna-only with portals off → "No company website", no POST to Adzuna | PI "M9" blocks (Naukri sign-in layer, LinkedIn CAPTCHA page, LinkedIn sign-in/CAPTCHA search walls, portals off) | Pass |
| M10 | The same unanswered question from two jobs is listed once; the answer is appended to resume_details.md and both jobs resume | PI "M10" block (Naukri chat + 2 LinkedIn dialogs → one question) | Pass |
| M11 | Applications shows "via company site / Naukri / LinkedIn"; empty state when there are none | UB `test_m11_applications_via_labels_progress_and_empty_state`; PI channel checks | Pass |
| M12 | tools/coverage.sh exits 0 only at 100% line+branch for the five modules; the network guard confirms no request left loopback | `bash tools/coverage.sh` (exit 0, 100%); PI `self.left == []`; UB route guard | Pass |
| M13 | All previously existing tests pass unchanged | Full suite: 92 tests OK; no existing test was edited (the only change to an existing test file is new fake portal pages added to tests/fake_careers.py) | Pass |
| S1 | Sign in to Naukri/LinkedIn from Settings; session status shown | PI sign-in/check calls; PU `test_portal_routes_cancel_and_busy`; UB status text | Pass |
| S2 | Countdown and current step in an aria-live region | UB (`#appProgress` aria-live=polite, countdown text); PI (`step` contains "rate limit" while waiting) | Pass |
| S3 | "Open in browser" on a "Needs you" item | UB (link name and href) | Pass |
| S4 | Traceability table | This table | Pass |
| O1 | Per-run cap on portal applications | Not built (D38) | N/A |
| NFR | Portal search + match ≤ 15 s per job on the fakes; API ≤ 200 ms during a run; profile dir 0700 | PI (whole 9-job run with 2 s pauses finishes inside the 400 s wait; the `/api/portals/check` reply during a run takes < 1 s); PU `test_browser_profile_is_owner_only` | Pass (≤ 200 ms not measured exactly: UNVERIFIED) |

## Enhancement 2026-11-06 (2): unattended auto-apply (docs/ENHANCE_SPEC.md)
Test file abbreviations:
- **U** = tests/test_unattended.py (classes Settings, Runs, Safety, Helpers). Searches run through the real search.py and matching.py over a mocked network, with a scripted apply browser.
- **UB** = tests/test_unattended_browser.py: the real apply flow in real Chromium against the local fake careers web, with a network guard.
- **UUI** = tests/test_unattended_ui.py: real Chromium driving the app's own UI.

| ID | Acceptance criterion (summary) | Test(s) | Status |
|---|---|---|---|
| M1 | Off and empty state after an upgrade. Interval 5 gives "Interval must be 15-1440 minutes" and nothing is saved. Enabling needs a confirmation, then the status shows "Running · next run HH:MM". | U `test_m1_off_by_default_validation_confirm_and_enable`, `test_migration_of_an_existing_install`; UUI `test_j1_set_up_with_inline_errors_review_and_confirm` | Pass |
| M2 | A tick during a run is logged as "skipped: previous run active". After 3 missed intervals, exactly one catch-up run happens. | U `test_m2_no_overlap_and_one_catch_up_after_downtime`, `test_timer_waits_while_off_and_ticks_when_due` | Pass |
| M3 | 10 postings, 4 of them new, 2 of those ≥ 80: exactly those 2 become candidates | U `test_m3_only_new_postings_at_or_above_the_minimum` | Pass |
| M4 | Skip reasons: already applied, company cooldown until <date>, duplicate, hidden. "deferred: daily cap" jobs are reconsidered the next day. | U `test_m4_guards_duplicate_applied_hidden_cooldown_and_cap`, `test_cooldown_ignores_other_companies_and_zero_days`; UB (a later run skips the jobs as "already applied" / "already in Applications") | Pass |
| M5 | 23:10 inside 22:00-07:00 gives "skipped: quiet hours". When quiet hours begin during a run, Submit is never pressed and the remaining jobs are deferred. | U `test_m5_quiet_hours_skip_and_stop_before_submit` | Pass |
| M6 | Submitted only after the confirmation is detected and a screenshot is saved. Otherwise Needs you. | UB `test_real_flow_submitted_with_proof_walls_parked_and_never_retried` (real Chromium, real confirmation page, PNG ≤ 1 MB, 0600); U `test_m6_submitted_with_proof_m10_run_log_and_m11_summary`, `test_m6_m7_outcomes_are_never_guessed` (confirmation without a screenshot gives Needs you) | Pass |
| M7 | Each of these gives Needs you with its specific reason: CAPTCHA, sign-in wall, account prompt, unanswered question, no portal match, no confirmation, unexpected page or timeout. Nothing is submitted. | UB (CAPTCHA form and sign-in wall in real Chromium: no POST, nothing sent to the CAPTCHA frame, the sign-in form is never submitted); U `test_m6_m7_outcomes_are_never_guessed` (10 outcomes) | Pass |
| M8 | Pausing mid-form: no further form action within 5 s, the job becomes Needs you "Interrupted, check", the status shows Paused, and it is still Paused after a restart. The kill switch is on every view. | U `test_m8_pause_stops_the_form_within_5s_and_survives_a_restart`; UUI `test_j3_kill_switch_on_every_view_and_resume_confirmation` | Pass |
| M9 | A job left as 'attempting' becomes Needs you "Interrupted, check" on restart and is never retried | U `test_m9_interrupted_attempt_becomes_needs_you_and_is_never_retried`; UB second run | Pass |
| M10 | Run log: start and end time, trigger, searches, per-source errors, every job's outcome and reason, and the proof link | U `test_m6_submitted_with_proof_m10_run_log_and_m11_summary`; UUI `test_j6_run_log_states_and_per_job_outcomes` | Pass |
| M11 | Today: counts 3/2/5 with reasons, the empty state, and error with Retry | U (as M10); UUI `test_j4_j5_daily_summary_states_and_dismiss` | Pass |
| M12 | One source fails with HTTP 500: the other sources are still processed, and the log names the failed source | U `test_m12_one_failing_source_does_not_stop_the_run_and_s1_auto_pause`; UUI `test_j6` ("Board B: Unavailable …") | Pass |
| M13 | New views: every state reachable, light and dark themes, no horizontal scroll at 320 px, contrast checks pass | UUI `test_m13_reflow_320_themes_names_and_settings_error_state`, plus the state checks in `test_j1`/`test_j4_j5`/`test_j6`; tests/test_ui_static.py | Pass |
| M14 | Outbound requests go only to allow-listed hosts (the browser only to the loopback apply targets). No resume text in requests or logs. No innerHTML. | U `test_m14_hosts_allow_listed_and_no_resume_text_sent_or_logged`; UB (network guard, `ALLOWED_HOSTS`); test_ui_static (no innerHTML) | Pass |
| M15 | The existing suite passes unchanged | Full suite and coverage gate (docs/ENHANCE_REPORT.md) | Pass |
| S1 | Auto-pause after 3 run-level errors, with a banner | U `test_m12_…_s1_auto_pause`; UUI `test_j3` (banner) | Pass |
| S2 | Run now respects every guard | U `test_pause_resume_off_run_now_and_security`; UUI `test_j1` | Pass |
| S3 | Status bar: next run, today against the cap, and the open Needs you count | U `test_m3` (`today`); UUI `test_j1`, `test_j4_j5` ("2 need you" changes to "1 needs you") | Pass |
| S4 | Delete all removes the settings, logs and screenshots | U `test_s4_delete_all_removes_settings_logs_and_screenshots` | Pass |
| S5 | A switch for each saved search | U `test_s5_per_search_switch_and_unknown_search` | Pass |
| O1 | In-page notice of new Needs you items | Live-region announcement in static/app.js; not asserted by a test | Built, UNVERIFIED |
| O2 | Daily summary CSV | U `test_m6_…_m11_summary` (CSV and formula-injection quoting) | Pass |
| O3 | Screenshot expiry | Not built (D51; the spec default of 0 means keep forever) | N/A |
| Security | Cross-site writes are rejected (403). Resume needs `confirm`. Proof files are 0600 in a 0700 folder. | U `test_pause_resume_off_run_now_and_security`, `test_m6_…`; UB | Pass |
| Perf | Summary API with 500 rows < 1 s, and < 1.5 s including rendering. Pause takes effect in < 5 s. | U `test_m6_…`; UUI `test_j4_j5`; U `test_m8` | Pass (API p95 < 300 ms during a run: UNVERIFIED) |
