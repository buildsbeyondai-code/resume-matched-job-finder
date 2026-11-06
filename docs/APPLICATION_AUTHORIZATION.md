# Application authorization (2.0)

The app sends applications in the user's name. An application can't be recalled, so sending one needs an explicit, scoped and expiring permission. This document describes how that permission works. The code is `app/policy.py`; the tests are `tests/test_policy.py`.

## Modes
| Mode | Browser | Submit | Default |
|---|---|---|---|
| `SEARCH_ONLY` | never driven | never | **yes** (also after upgrading from 1.x) |
| `PREPARE_FOR_REVIEW` | fills forms | never: stops at "Ready for your review", with the window left open | |
| `AUTHORIZED_AUTO_SUBMIT` | fills forms | yes, inside the confirmed scope until the consent expires | |

## Granting automatic submission
1. In Settings → Application policy, the user chooses "Submit automatically" and the limits: the daily cap (1–100), the permission lifetime (1–365 days), excluded employers, and the channels. Company websites are allowed by default; Naukri and LinkedIn must be ticked on purpose, after you have reviewed their terms (PORTAL_POLICY_MATRIX.md).
2. The UI asks the server for the **exact summary sentence** for that scope (`POST /api/policy/preview`) and shows it in a dialog.
3. "I allow this" sends the scope together with the SHA-256 of the summary that was shown (`PUT /api/policy`, field `confirm`). The server recomputes the summary and refuses the change unless the hashes match. A scope the user didn't see can't be saved.
4. The consent `{at, expires, summary, summary_hash}` is stored, and `consent.granted` is written to the audit log.

## Ending it
- **Expiry.** After `expires_days`, the effective mode becomes PREPARE_FOR_REVIEW and the UI says so.
- **Scope change.** Any change to the cap, channels, exclusions or lifetime, including one written straight into the database, makes the stored hash stop matching. The effective mode drops to PREPARE_FOR_REVIEW until the user confirms again.
- **Revocation.** Choosing another mode deletes the consent (`consent.revoked` is audited).
- **Pause.** The unattended kill switch and the auto-apply Cancel stop at the next step, and never between Submit and its confirmation check.

## The gate
`policy.gate(db, job, channel)` runs right before every click that may send an application:
- a button classified as Submit;
- a job portal's Apply button (Naukri can apply in one click);
- an ambiguous "Continue", "Proceed" or "Save" (a Naukri chat drawer's last Save sends the application).

The gate is deterministic code; no language model takes part. It checks the effective mode, the channel, the excluded employers (matched after legal suffixes are removed) and the daily cap. SUBMITTED, CONFIRMING and OUTCOME_UNKNOWN all count toward the cap. Every decision is audited. If no gate is present (a code path without runner hooks), the worker **fails closed** and stops at "Ready for your review".

Unattended runs (`app/unattended.py`) also check the effective mode before each run and skip with the reason when automatic submission isn't authorized.

## What consent can't override
These always come back to the user as Needs you, with a direct link:
- sign-in, MFA and CAPTCHA;
- legal declarations and consents;
- questions not answered from the user's own facts.

Job portals' terms and rate limits still apply (PORTAL_POLICY_MATRIX.md). The app never creates accounts.

## Development note
No real application was submitted while 2.0 was developed or tested. All tests run against local fakes (`tests/fake_careers.py`). Testing against a real ATS or portal needs a separate, explicit authorization.
