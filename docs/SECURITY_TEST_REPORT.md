# Security test report: 2.0.0 (2026-11-06)

Written by the developing agent; **this is not an independent review.** No external penetration test was done (THREAT_MODEL.md R5).

## Automated security tests (all passing)
| Area | Test | Result |
|---|---|---|
| CSRF and cross-origin writes: 10 forged variants (no header, other localhost port, https lookalike, remote, `null` origin, cross-site and same-site `Sec-Fetch-Site`, rebinding host, wrong port) | `test_security.Server.test_writes_need_this_apps_origin_and_header` | all refused with 403; no state changed |
| DNS rebinding on reads | `test_reads_refuse_foreign_hosts` | 403 |
| Unauthorized submission | `test_policy.Policy.*`, `AutoSubmitDecisions.*`, `Runner.test_search_only_blocks_before_the_browser`, `UnattendedPolicy` | impossible without a valid, scope-matching, unexpired consent; fails closed without a gate |
| Consent replay and scope tampering | `test_auto_submit_needs_the_exact_confirmed_summary`, `test_scope_change_expiry_and_revocation_end_the_consent` | a confirmation for another scope is rejected; a DB-edited scope voids the consent |
| Duplicate, replayed or concurrent submission | `Claims.*` (20-thread race: exactly one winner), E2E `agg_dup` | blocked |
| Crash, cancel or browser failure during Submit | `Runner.test_crash_after_click…`, `test_cancel_or_apply_error_while_submitting_is_unknown`, `test_restart_turns_an_interrupted_submit_into_unknown` | OUTCOME_UNKNOWN; never retried |
| Prompt injection (fencing, fence-breaking tags) | `test_ai.Answer.test_employer_text_is_fenced_and_cannot_break_out` | contained in the prompt structure (with a fake model) |
| Fabricated or uncited AI answers, wrong model, data minimisation | `test_ai.Answer.*` | discarded or refused; contact and sensitive data not sent |
| Malicious resume files: 300 mutated PDF/DOCX files, a zip bomb, billion laughs, 2,100-entry zip, PDF stream bomb | `test_properties.Parsers` | a clear error or text within 5–10 s; **one crash found and fixed** (`ValueError` on a corrupted zip) |
| URL validation fuzz (2,000 strings) | `test_properties.Urls` | only `https://` job links accepted |
| Audit tampering (edit, delete) | `test_policy.Audit` | detected |
| Delete all data, including the 2.0 tables | `test_policy.DeleteAll` | cleared; policy reset to SEARCH_ONLY; **bug found by SAST review and fixed** |
| Mutation testing of 17 safety rules | `tools/mutation.py` | 17/17 killed |
| Secrets and personal data in tracked files | `tools/scan_secrets.sh` | clean |

## Static analysis and dependencies
- **bandit 1.9.4** on `app/`: 3 High findings (SHA-1 used for identifiers, now `usedforsecurity=False`). After the fix: **0 High**. 6 Medium findings were triaged as false positives:
  - SQL built from constant fragments;
  - an X11 socket existence check;
  - `urlopen` behind the HTTPS allow-list;
  - ElementTree, mitigated by refusing DOCTYPE/ENTITY.
  
  The 21 Low findings are mostly deliberate `try/except` around browser calls.
- **pip-audit** on `requirements-lock.txt`: no known vulnerabilities.
- **Not run:** a type checker or linter (ruff and mypy are not part of the project), DAST against a running instance, and container image scanning (no image was built).

## Not verified
- Behaviour against real employer ATS sites and real Naukri/LinkedIn (PORTAL_POLICY_MATRIX.md).
- A live prompt-injection evaluation against Claude Opus 5.5 (needs paid calls: NEEDS_USER).
- Session theft from the browser profile by a malicious page: the separation relies on Chromium site isolation (THREAT_MODEL.md R3).
- Manual WCAG 2.2 AA review of the new policy panel. Automated checks cover names, focus and contrast tokens.
