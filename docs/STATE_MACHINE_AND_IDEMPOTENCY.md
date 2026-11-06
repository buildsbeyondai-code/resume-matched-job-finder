# State machine and idempotency (2.0)

## Application status (`app/states.py`, enforced by `db.app_set`)
An illegal change raises `IllegalTransition`, and the check and the write happen under one lock. Re-saving the same status, to update its details, is always allowed.

| From | Allowed to |
|---|---|
| (none) | queued, running, failed, policy_blocked, applied_manually, needs_you |
| queued | running, cancelled, failed, policy_blocked |
| running | any result: submitted, needs_answers, needs_you, no_company_site, failed, cancelled, policy_blocked, ready_for_review, outcome_unknown |
| needs_answers, needs_you, failed, cancelled, no_company_site, policy_blocked, ready_for_review, dismissed | queued, running, applied_manually, dismissed, needs_you, cancelled |
| **submitted** | outcome_unknown only: a confirmation was seen but its proof could not be kept (a more cautious downgrade) |
| **applied_manually** | — (terminal) |
| **outcome_unknown** | applied_manually ("It was sent"), failed ("It was not sent"), only through `Runner.resolve` |

How the specification's workflow maps onto these statuses:

| Specification state | Stored as |
|---|---|
| DISCOVERED, MATCHED, ELIGIBLE | a posting in search results (no application row yet) |
| APPROVED, AUTHORIZED | queued (the user or the unattended policy chose it; the gate decides at Submit) |
| STARTING, FILLING, REVIEWING | running, with the current step in `step` |
| SUBMITTING, CONFIRMING | running, with the submission key claimed |
| SUBMITTED | submitted (confirmation and proof) or applied_manually |
| NEEDS_USER | needs_answers, needs_you, ready_for_review |
| POLICY_BLOCKED | policy_blocked |
| VALIDATION_FAILED, FAILED | failed |
| CANCELLED | cancelled |
| OUTCOME_UNKNOWN | outcome_unknown |
| SKIPPED | dismissed, no_company_site |

**NOT IMPLEMENTED:** EXPIRED, CLOSED and WITHDRAWN. There is no verified source for them, and withdrawing applications isn't automated.

## Submission keys (`app/policy.py`, table `submissions`)
- **Key:** SHA-256 of the normalised employer (with legal suffixes removed), the role title and the first part of the location. The same job reached through Adzuna, a company board or a portal gets the same key.
- **Claim:** `claim()` runs `BEGIN IMMEDIATE` and inserts the key or updates it to SUBMITTING. It refuses when the key is SUBMITTING, CONFIRMING, SUBMITTED or OUTCOME_UNKNOWN. Only NOT_SUBMITTED, meaning the site showed validation errors or the user confirmed nothing was sent, can be claimed again.
- **Moves:**

| From | Allowed to |
|---|---|
| (none) | SUBMITTING |
| SUBMITTING | CONFIRMING, SUBMITTED, OUTCOME_UNKNOWN, NOT_SUBMITTED |
| CONFIRMING | SUBMITTED, OUTCOME_UNKNOWN |
| NOT_SUBMITTED | SUBMITTING |
| OUTCOME_UNKNOWN | SUBMITTED or NOT_SUBMITTED (the user's answer only) |
| SUBMITTED | — |

## Outcome rules after a gated click (`apply._auto`)
| Observation | Submission state | Application status |
|---|---|---|
| New confirmation text (`confirmed()`) | SUBMITTED | submitted (with a proof screenshot) |
| The site shows validation errors | NOT_SUBMITTED | the form is corrected and Submit may be pressed again (at most twice), then failed |
| An ambiguous button opened the next form step | NOT_SUBMITTED | continues |
| No confirmation and no errors | OUTCOME_UNKNOWN | outcome_unknown. **Submit is never pressed again** |
| The click itself failed | OUTCOME_UNKNOWN | outcome_unknown |
| A CAPTCHA appeared after Submit | OUTCOME_UNKNOWN | outcome_unknown |
| Browser crash, cancel or app restart while SUBMITTING | OUTCOME_UNKNOWN (`_unknown_if_submitting`, startup recovery) | outcome_unknown |
| Confirmation after a click that was not gated | claimed after the fact, then SUBMITTED, and `submit.ungated` is audited | submitted |

OUTCOME_UNKNOWN is never re-queued: `enqueue` and `retry` refuse it, and the state machine forbids it. The user records the outcome with "It was sent" or "It was not sent" (`POST /api/applications/{key}/resolve`).

## Invariant tests
`tests/test_policy.py` contains:
- an exhaustive check of every status pair;
- a race where 20 threads claim the same key and exactly one wins;
- crash, cancel and error during SUBMITTING;
- restart recovery;
- a repost blocked as a duplicate.

`tests/test_properties.py` runs random walks over the state machine. `tools/mutation.py` breaks each rule and expects these tests to fail.
