# Enterprise hardening: baseline (before 2.0)

## Heads
- **GitHub `origin/master`:** `e3efe37098e65999c82bb75b3c765a17dfdd8133`, the head the master prompt inspected. It has Claude Opus 5.5 answers and has auto apply, but not unattended mode.
- **Local working tree at the start of this work:** a ReuseFirst build (`20261106-114538-c310d6`, "unattended auto-apply") had swapped an older snapshot over the folder, `.git` included. Locally that dropped `e3efe37` and `app/ai.py`, and removed the git remote.
- **Recovery:** a three-way merge. The base was the build's starting backup (`…-20261106-071130.tar.gz`), ours was `e3efe37`, and theirs was the build output. It was committed as `7689edd` on `master` (local, not pushed). 16 empty placeholder files left by the build's sandbox were removed, and one real email in a build-added test was replaced with fictional data.
- **Hardening branch:** `v2-enterprise-hardening`, created from `7689edd`.

## Baseline tests (`7689edd`)
- `tools/coverage.sh`: **132 tests passed**, 0 failed, 0 skipped, in 281 s.
- 100% line and branch coverage on `ai`, `apply`, `autoapply`, `details`, `direct`, `portals` and `unattended`. Whole app: 96%.
- **Documentation drift:** the README said 120 tests, the master prompt said 92, the actual count was 132.

## Defect register (found by the audit; status at the end of 2.0)
| # | Severity | Defect | Reproduction | Fixed by / test |
|---|---|---|---|---|
| D1 | Critical | Applications were sent whenever Auto apply ran, with no persisted consent, scope or expiry | press Auto apply | `policy.py` gate and consent; `test_policy` |
| D2 | Critical | A company-site Submit with no confirmation and no errors made the loop continue and could press Submit again | form with no confirmation page | OUTCOME_UNKNOWN after any gated click without proof; `test_no_confirmation_means_unknown_and_no_second_press` |
| D3 | High | The same job reached through two boards (Lever, then Adzuna) was applied to twice. The old E2E test expected this | `agg_board` and `lever` in the old journey | submission keys and claims; E2E `agg_dup` |
| D4 | High | A crash or restart after Submit became "failed, press Retry", so a retry could submit twice | kill the app during Submit | `_unknown_if_submitting` and startup recovery; `test_restart_turns_an_interrupted_submit_into_unknown` |
| D5 | High | Any localhost origin, on any port, could write to the API (another local web app could trigger applications) | POST with `Origin: http://127.0.0.1:5057` | strict origin, port and custom header; `test_security` |
| D6 | High | Naukri's one-click Apply and its chat-drawer "Save" could send an application with no Submit-kind button | Naukri drawer flow | gated portal Apply and ambiguous buttons; portal E2E |
| D7 | Medium | Claude answers had no provenance; contact and sensitive data and the full resume were sent; employer text wasn't fenced; no model check, cost log or budget | inspect the prompt | `ai.py` contract; `test_ai` |
| D8 | Medium | A corrupted DOCX raised `ValueError` (500 instead of a clear error); the DOCX size check trusted the zip header; no total PDF inflate cap | fuzz case i=285 | `resume.py` limits; `test_properties` |
| D9 | Medium | No schema versioning or backup before changes | — | `db.migrate`; `test_security.Migration` |
| D10 | Medium | Application statuses could change arbitrarily (no state machine) | — | `states.py` enforced in `app_set` |
| D11 | Low | Ranking 1,000 postings took 1.5 s (skills re-detected per score) | `test_rank_1000_under_1s` | `skills` cache (in `e3efe37`) |
| D12 | Low | Real personal data in test fixtures | grep | fictional fixtures plus `tools/scan_secrets.sh` |
