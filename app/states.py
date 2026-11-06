"""Application status state machine (docs/STATE_MACHINE_AND_IDEMPOTENCY.md). `db.app_set` enforces MOVES, so an
illegal change (for example re-queuing an application whose outcome is unknown) raises instead of happening.

The fine-grained workflow of the specification maps onto these stored statuses:
  DISCOVERED / MATCHED / ELIGIBLE   a posting in search results (no application row yet)
  APPROVED / AUTHORIZED             "queued" (the user or the unattended policy chose it; the policy gate decides)
  STARTING / FILLING / REVIEWING    "running" (the current step is in the row's `step`)
  SUBMITTING / CONFIRMING           "running", with the submission key claimed (policy.SUBMITTING / CONFIRMING)
  SUBMITTED                         "submitted" (confirmation seen and proof kept) or "applied_manually" (the user)
  NEEDS_USER                        "needs_answers", "needs_you", "ready_for_review"
  POLICY_BLOCKED                    "policy_blocked"
  VALIDATION_FAILED / FAILED        "failed" (the site rejected the form, or an error before Submit)
  CANCELLED                         "cancelled"
  OUTCOME_UNKNOWN                   "outcome_unknown" (Submit may have reached the site; only the user resolves it)
  SKIPPED                           "dismissed", "no_company_site"
EXPIRED, CLOSED and WITHDRAWN are not stored: the app has no verified source for them (NOT IMPLEMENTED).
"""

WAITING = ("needs_answers", "needs_you", "failed", "cancelled", "no_company_site", "policy_blocked",
           "ready_for_review", "dismissed")
RESULTS = ("submitted", "needs_answers", "needs_you", "no_company_site", "failed", "cancelled", "policy_blocked",
           "ready_for_review", "outcome_unknown")
STATUSES = ("queued", "running", "applied_manually") + RESULTS + ("dismissed",)
KEEP_WINDOW = ("needs_you", "ready_for_review", "outcome_unknown")  # the user acts in the browser window

MOVES = {
    None: {"queued", "running", "failed", "policy_blocked", "applied_manually", "needs_you"},
    "queued": {"running", "cancelled", "failed", "policy_blocked"},
    "running": set(RESULTS) | {"running", "needs_you"},
    "submitted": {"outcome_unknown"},  # downgrade only: a confirmation was seen but its proof could not be kept
    "applied_manually": set(),
    "outcome_unknown": {"applied_manually", "failed"},  # only through Runner.resolve (the user's answer)
}
for _w in WAITING:
    MOVES[_w] = {"queued", "running", "applied_manually", "dismissed", "needs_you", "cancelled"}


class IllegalTransition(Exception):
    pass


def check(old, new):
    """Raise IllegalTransition unless old -> new is allowed. Re-saving the same status (to update details) is
    always allowed."""
    if new not in STATUSES:
        raise IllegalTransition(f"unknown status {new!r}")
    if old == new:
        return
    if new not in MOVES.get(old, set()):
        raise IllegalTransition(f"{old} -> {new}")
