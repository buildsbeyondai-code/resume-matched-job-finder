# Completion report: Resume-Matched Job Finder (2026-11-05)

## Delivery summary
- **Outcome:** A working local web app on 127.0.0.1:5070. It reads a resume (PDF/DOCX) locally and lets you edit the extracted profile. It searches Greenhouse, Lever and SmartRecruiters public boards plus Adzuna in parallel, with per-source progress, cancel and partial-failure handling. Results are scored 0-100 with matched and missing skills, duplicates are merged, and links go to the original posting. You can save, hide (with undo), refresh with "New" markers, filter results further and export CSV. Everything is stored in SQLite (WAL), and all data can be deleted with one action. There are light and dark themes with a remembered override and a Descriptions switch.
- **Changed files:** all new. `app/` (8 modules), `static/` (4 files), `tests/` (3 suites + 4 fixtures), `run.sh`, docs.
- **Architecture decisions:** docs/DECISIONS.md D1-D13. The key ones are standard-library-only Python (package installs were blocked) and our own minimal PDF extractor.
- **User-visible behaviour verified:** API journeys J1-J5 run over real HTTP with a mocked network. A live run against the real public APIs: `./run.sh` served `GET /` with 200 (12,604 bytes), and a search for Germany + United States returned Bosch 100, Cloudflare 464 and Palantir 309 postings (Visa 0), giving 286 filtered results. Adzuna correctly reported "Not configured". The server was then stopped.

## Requirements traceability
See docs/REQUIREMENTS.md for the full table. Summary:

| Req | Tests | Result |
|---|---|---|
| M1 / M1-error | ResumeTests (5), test_j1_upload_edit_profile, test_j1_upload_errors | PASS (refocus on retry is browser-only, so UNVERIFIED) |
| M2 / M2-empty | test_m2_country_and_mode, test_j2_j3_m2_m4_m5_m6…, test_m2_empty | PASS |
| M3 | test_m3_allow_list, test_m3_m8_only_allowlisted_and_no_resume_text | PASS |
| M4 | test_m4_scores_and_lists, test_seniority_downranks_mismatch, integration ordering | PASS |
| M5 | test_m5_dedupe, integration (Greenhouse + Lever merged) | PASS |
| M6 | integration https URL check, static new-tab check | PASS (static); opening a new tab in a browser is UNVERIFIED |
| M7 | test_m7_partial_failure, test_cancel, test_retry_backoff_max_two_retries, test_no_retry_on_4xx | PASS |
| M8 | test_m3_m8…, test_m8_persistence_after_restart_and_j4_j5, test_delete_all_data | PASS |
| S1, S3, S4, S5, O2 | persistence/J5, sources/CSV, UI token tests | PASS |
| S2 | client-side filters | UNVERIFIED (no browser test) |
| O1, O3 | not built | N/A |

## Verification evidence
| Check | Command / method | Result | Evidence |
|---|---|---|---|
| Full test suite | `python3 -m unittest discover -s tests -t .` | 42 tests, OK | "Ran 42 tests in 8.581s / OK" |
| App starts | `./run.sh` then a Python urllib probe | 200 on `/`, JSON from `/api/state` | Run during the build; stopped with TaskStop |
| Live sources | POST /api/search (Germany, United States, any mode) | 3 of 4 sources succeeded; Adzuna needs a key | 873 raw postings, 286 results |
| Search latency (live, uncached) | Same | **29.2 s** (target: ≤ 15 s p95) | Cached repeat 3.4 s, so the time is network/payload, not processing |
| Rank 1,000 postings | test_rank_1000_under_1s | < 1 s | PASS |
| Resume parse 5-page PDF | test_parse_5_page_pdf_under_5s | < 5 s | PASS |

## Release quality gates (UAISES §18)
| Gate | State | Reason |
|---|---|---|
| Requirements | PASS | Acceptance criteria mapped to tests in docs/REQUIREMENTS.md |
| Real functionality | PASS | Live APIs verified. No fake data; fixtures are used only in tests |
| Code quality | UNVERIFIED | No linter or type checker was available (installs blocked). Self-reviewed only |
| Testing | PASS (with gap) | Unit, contract, integration and API-E2E tests pass. Browser E2E is missing (see UI gates) |
| Security | PASS | 127.0.0.1 only; HTTPS allow-list; key only from env and redacted; CSP; same-origin writes; Host check; size and type limits; no HTML rendering; CSV formula neutralised; delete-all. No external scan was run |
| Reliability | PASS | Per-source isolation, retries with backoff, WAL, data persists across restart (tested) |
| Performance | FAIL | Live uncached search took 29.2 s against the 15 s target. Ranking and parsing targets pass. Memory under 300 MB not measured (UNVERIFIED) |
| Packaging | PASS | `./run.sh` / `python3 -m app`, no dependencies, `.env.example` |
| Operations | PASS | Path-only request log (no queries or bodies); errors shown in the UI per source |
| Velocity | PASS | Full suite runs in about 8.6 s |

## UI gates (UEUIS §20)
| Gate | State | Reason |
|---|---|---|
| Interaction principles | UNVERIFIED | Designed in (cancel, undo, editable profile, feedback) but not usability-reviewed in a browser |
| States | PASS (static) / UNVERIFIED (visual) | docs/UI_STATES.md inventory implemented. Not checked visually |
| Accessibility | UNVERIFIED | No axe, keyboard, screen-reader or zoom pass possible (no browser). Static checks pass |
| Theming | PASS (contrast) / UNVERIFIED (visual) | Automated contrast test for every token pair in both themes. No screenshots |
| Palette and restraint | PASS | Named palettes "Pine & Paper" and "Pine Night"; test confirms no gradients, glass or text-shadow |
| Focus and descriptions | PASS (code) | Descriptions switch is remembered and hides only `.desc`. History and saved jobs are on their own tab |
| Restraint and density | PASS (static) / UNVERIFIED (1366×768 screenshot) | One primary button per view (tested) |
| Professional polish | UNVERIFIED | No screenshots |
| Rich interface | PASS (code) | Score bands with text, freshness ("searched …"), "New" chips, per-source status |
| Visual design | UNVERIFIED | Tokens and type scale are in place but not reviewed visually |
| Responsive | UNVERIFIED | Reflow rules present; 320 px no-scroll not measured in a browser |
| Content | PASS | Errors give the reason and the next step |
| Forms | PASS (API) / UNVERIFIED (browser) | Labels, aria-invalid, error summary, input kept |
| Truthfulness | PASS | Every control calls a real endpoint; Adzuna honestly shows "Needs API key" |
| Ethics | PASS | No dark patterns; delete uses a named confirmation |
| i18n | N/A | Not localised |
| Performance | UNVERIFIED | 100 ms UI feedback not measured |
| Usability | UNVERIFIED | No §16 evaluation |
| Tests | FAIL | Automated browser journey tests (keyboard, 320 px, axe) are missing because no browser could be installed |

## Risks and remaining work
- **Blockers:** none for local use. For Enterprise UI sign-off, browser-based accessibility testing is still needed.
- **Unverified claims:** axe and keyboard checks, 320 px and 200% zoom, the 300 MB RAM limit, and S2 client filters.
- **Known limitations:** the PDF extractor cannot read CID/Type0-font or scanned PDFs (it shows a clear error and suggests DOCX). Large MNCs are only reachable through Adzuna, which needs a key. Live search latency is above target. Default board tokens may change.
- **Rollback:** delete `data/` (local DB). The app has no other state.
- **Next highest-value step:** install Playwright and axe-core and add browser journey tests. Cap per-source payload size (for example, skip `content=true` in favour of per-job detail on expand) so search meets the 15 s target.
