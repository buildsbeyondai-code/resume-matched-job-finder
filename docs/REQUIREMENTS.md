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
