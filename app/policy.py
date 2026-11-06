"""Application policy, consent and the submission gate (docs/APPLICATION_AUTHORIZATION.md,
docs/STATE_MACHINE_AND_IDEMPOTENCY.md).

Modes:
  SEARCH_ONLY            (default) search and rank only; the browser is never driven.
  PREPARE_FOR_REVIEW     fill the application up to its final Submit button, then stop ("Ready for your review").
  AUTHORIZED_AUTO_SUBMIT press Submit, but only inside the scope the user confirmed, before the consent expires.

`gate(db, job, channel)` is the single deterministic decision made right before every Submit click. A language
model never takes part in it. Every decision is written to the audit log.

Idempotency: each application has a submission key (employer + role + location, normalised, so a repost of the
same job gets the same key). `claim` takes the key in one SQLite transaction before Submit. A key that is
SUBMITTING, CONFIRMING, SUBMITTED or OUTCOME_UNKNOWN can't be claimed again: no second Submit without proof that
the first did not go through. Only NOT_SUBMITTED (the site showed validation errors, or the user confirmed nothing
was sent) can be claimed again.
"""
import hashlib
import json
import re
import secrets
import time

from . import audit, direct

SEARCH_ONLY, PREPARE, AUTO = "SEARCH_ONLY", "PREPARE_FOR_REVIEW", "AUTHORIZED_AUTO_SUBMIT"
MODES = (SEARCH_ONLY, PREPARE, AUTO)
KEY = "policy"
DEFAULTS = {
    "mode": SEARCH_ONLY,
    "excluded_companies": [],  # never apply to these employers
    "daily_cap": 10,           # submissions per local day, all channels
    "channels": ["company", "naukri", "linkedin"],  # where Submit may be pressed (portals also need Settings on)
    "expires_days": 30,        # consent lifetime
    "consent": None,           # {at, expires, summary, summary_hash, mode}; None = never given or revoked
}
LIMITS = {"daily_cap": (1, 100), "expires_days": (1, 365)}
CHANNEL_NAMES = {"company": "company websites", "naukri": "Naukri", "linkedin": "LinkedIn"}

# submission states (the part of the state machine around Submit)
SUBMITTING, CONFIRMING, SUBMITTED, UNKNOWN, NOT_SUBMITTED = (
    "SUBMITTING", "CONFIRMING", "SUBMITTED", "OUTCOME_UNKNOWN", "NOT_SUBMITTED")
BLOCKING = (SUBMITTING, CONFIRMING, SUBMITTED, UNKNOWN)
SUBMISSION_MOVES = {  # legal transitions of a submission key
    None: {SUBMITTING},
    SUBMITTING: {CONFIRMING, SUBMITTED, UNKNOWN, NOT_SUBMITTED},
    CONFIRMING: {SUBMITTED, UNKNOWN},
    NOT_SUBMITTED: {SUBMITTING},
    UNKNOWN: {SUBMITTED, NOT_SUBMITTED},  # only by the user's explicit resolution
    SUBMITTED: set(),
}


class Blocked(Exception):
    """The policy forbids this action. `status` is the application status to record."""

    def __init__(self, reason, status="policy_blocked"):
        super().__init__(reason)
        self.status = status


class IllegalTransition(Exception):
    pass


# ---------------- policy and consent ----------------
def load(db):
    p = dict(DEFAULTS)
    p.update(db.get_setting(KEY, {}) or {})
    return p


def summary(p):
    """The exact scope the user confirms. Its hash is stored with the consent, so a later scope change needs a
    new confirmation."""
    ex = ", ".join(p["excluded_companies"]) or "none"
    ch = ", ".join(CHANNEL_NAMES[c] for c in p["channels"]) or "nowhere"
    return (f"Submit job applications in my name automatically, without asking each time: on {ch}; at most "
            f"{p['daily_cap']} per day; never to these employers: {ex}. Only questions answered from my resume "
            f"details are filled; anything else is asked first. This permission ends after {p['expires_days']} "
            f"days or when I switch it off.")


def summary_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate(body):
    """Returns (clean changes, [{field, message}])."""
    clean, errors = {}, []
    if "mode" in body:
        if body["mode"] not in MODES:
            errors.append({"field": "mode", "message": "Choose one of the three modes."})
        else:
            clean["mode"] = body["mode"]
    for f, (lo, hi) in LIMITS.items():
        if f in body:
            v = body[f]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                errors.append({"field": f, "message": f"Enter a whole number from {lo} to {hi}."})
            else:
                clean[f] = v
    if "excluded_companies" in body:
        v = body["excluded_companies"]
        if not isinstance(v, list) or not all(isinstance(x, str) and 0 < len(x.strip()) <= 120 for x in v) or len(v) > 200:
            errors.append({"field": "excluded_companies", "message": "List up to 200 company names."})
        else:
            clean["excluded_companies"] = list(dict.fromkeys(x.strip() for x in v))
    if "channels" in body:
        v = body["channels"]
        if not isinstance(v, list) or not all(c in CHANNEL_NAMES for c in v):
            errors.append({"field": "channels", "message": "Choose channels from the list."})
        else:
            clean["channels"] = [c for c in CHANNEL_NAMES if c in v]
    return clean, errors


def update(db, body, now=None):
    """Change the policy. Turning on AUTHORIZED_AUTO_SUBMIT needs `confirm` = the hash of the summary the user
    was shown for exactly the resulting scope. Any change of scope or mode ends an earlier consent."""
    now = now or time.time()
    clean, errors = validate(body)
    if errors:
        return None, errors
    old = load(db)
    new = dict(old, **clean)
    scope_changed = any(new[k] != old[k] for k in ("mode", "excluded_companies", "daily_cap", "channels", "expires_days"))
    if new["mode"] == AUTO:
        text = summary(new)
        if scope_changed or not consent_valid(old, now):
            if body.get("confirm") != summary_hash(text):
                return None, [{"field": "confirm", "message": "Confirm the summary shown to turn on automatic submission."}]
            new["consent"] = {"at": now, "expires": now + new["expires_days"] * 86400, "summary": text,
                              "summary_hash": summary_hash(text), "mode": AUTO}
            audit.record(db, "consent.granted", data={"summary_hash": new["consent"]["summary_hash"],
                                                      "summary": text, "expires": new["consent"]["expires"]})
    elif old.get("consent"):
        new["consent"] = None
        audit.record(db, "consent.revoked", data={"mode": new["mode"]})
    db.set_setting(KEY, new)
    audit.record(db, "policy.changed", data={k: new[k] for k in ("mode", "excluded_companies", "daily_cap",
                                                                   "channels", "expires_days")})
    return view(db, now), []


def consent_valid(p, now=None):
    c = p.get("consent")
    return bool(c and c.get("mode") == AUTO and c.get("expires", 0) > (now or time.time())
                and c.get("summary_hash") == summary_hash(summary(p)))


def effective_mode(p, now=None):
    """AUTHORIZED_AUTO_SUBMIT whose consent expired or no longer matches the scope acts as PREPARE_FOR_REVIEW."""
    if p["mode"] == AUTO and not consent_valid(p, now):
        return PREPARE
    return p["mode"]


def view(db, now=None):
    p = load(db)
    now = now or time.time()
    return {**{k: p[k] for k in DEFAULTS if k != "consent"}, "effective_mode": effective_mode(p, now),
            "consent": p["consent"], "consent_valid": consent_valid(p, now),
            "summary": summary(p), "summary_hash": summary_hash(summary(p)),
            "submitted_today": submitted_today(db, now)}


def preview_summary(db, body):
    """The summary for a scope the user is about to save (shown in the confirmation dialog)."""
    clean, errors = validate(body)
    if errors:
        return None, errors
    text = summary(dict(load(db), **clean))
    return {"summary": text, "summary_hash": summary_hash(text)}, []


# ---------------- the gate ----------------
def can_start(db, now=None):
    """Whether the browser may be driven at all (Apply / Auto apply / unattended runs)."""
    p = load(db)
    if effective_mode(p, now) == SEARCH_ONLY:
        raise Blocked("Search only: applying is switched off. Choose \"Prepare for review\" or \"Submit "
                      "automatically\" in Settings → Application policy.")
    return effective_mode(p, now)


def gate(db, job, channel, now=None):
    """Decide right before Submit. Returns the submission key to claim; raises Blocked otherwise."""
    now = now or time.time()
    p = load(db)
    mode = effective_mode(p, now)
    reason, status = None, "policy_blocked"
    if mode == SEARCH_ONLY:
        reason = "Search only: applying is switched off in Settings → Application policy."
    elif mode == PREPARE:
        reason = ("Ready for your review: the form is filled. Check it in the browser window and press Submit "
                  "there yourself" + (" (automatic submission is on, but its permission expired or the scope changed;"
                                      " confirm it again in Settings)" if p["mode"] == AUTO else "") + ".")
        status = "ready_for_review"
    elif channel not in p["channels"]:
        reason = f"Automatic submission on {CHANNEL_NAMES.get(channel, channel)} is not allowed by your policy."
    elif excluded(job.get("company"), p["excluded_companies"]):
        reason = f"{job.get('company')} is on your list of employers never to apply to."
    elif submitted_today(db, now) >= p["daily_cap"]:
        reason = f"Daily limit reached ({p['daily_cap']} submissions today). The job can be retried tomorrow."
    audit.record(db, "submit.gate", job.get("key"), {"channel": channel, "mode": mode,
                                                     "allowed": reason is None, "reason": reason or ""})
    if reason:
        raise Blocked(reason, status)
    return submission_key(job)


def excluded(company, names):
    c = norm_company(company)
    return bool(c) and any(norm_company(n) == c for n in names)


def norm_company(name):
    name = re.sub(direct.SUFFIXES, " ", (name or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", name))


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def submission_key(job):
    """Same employer + role + location = same application, whichever board or URL the posting came from."""
    loc = _norm(re.split(r"[,/(]", job.get("location") or "")[0])
    raw = "|".join((norm_company(job.get("company")), _norm(job.get("title")), loc))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------- submission claims ----------------
def claim(db, key, job_key, now=None):
    """Take the submission key before pressing Submit (one transaction). Returns the attempt id.
    Raises Blocked when an earlier attempt may have gone through."""
    now = now or time.time()
    attempt = secrets.token_hex(8)
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            row = cur.execute("SELECT state, job_key FROM submissions WHERE submission_key=?", (key,)).fetchone()
            if row and row["state"] in BLOCKING:
                cur.execute("ROLLBACK")
                words = {SUBMITTED: "was already submitted", UNKNOWN: "may already have been submitted (outcome unknown)",
                         SUBMITTING: "is being submitted", CONFIRMING: "is being submitted"}[row["state"]]
                audit.record(db, "submit.duplicate_blocked", job_key, {"submission_key": key, "state": row["state"]})
                same = row["job_key"] == job_key
                raise Blocked(f"Not submitted again: this application {words}"
                              + ("" if same else " from another posting of the same job") + ".",
                              "outcome_unknown" if same and row["state"] != SUBMITTED else "policy_blocked")
            if row:
                cur.execute("UPDATE submissions SET state=?, job_key=?, attempt_id=?, updated=? WHERE submission_key=?",
                            (SUBMITTING, job_key, attempt, now, key))
            else:
                cur.execute("INSERT INTO submissions(submission_key, job_key, state, attempt_id, created, updated) "
                            "VALUES(?,?,?,?,?,?)", (key, job_key, SUBMITTING, attempt, now, now))
            cur.execute("COMMIT")
        except Blocked:
            raise
        except Exception:
            cur.execute("ROLLBACK")
            raise
    audit.record(db, "submit.claimed", job_key, {"submission_key": key, "attempt": attempt})
    return attempt


def state_of(db, key):
    r = db.q("SELECT state FROM submissions WHERE submission_key=?", (key,))
    return r[0]["state"] if r else None


def move(db, key, to, detail=None, now=None):
    """Move a submission key along SUBMISSION_MOVES; anything else raises IllegalTransition."""
    with db.lock:
        cur = state_of(db, key)
        if to not in SUBMISSION_MOVES[cur]:
            raise IllegalTransition(f"{cur} -> {to}")
        db.x("UPDATE submissions SET state=?, updated=?, detail=? WHERE submission_key=?",
             (to, now or time.time(), json.dumps(detail or {}), key))
    audit.record(db, "submit.state", None, {"submission_key": key, "from": cur, "to": to, **(detail or {})})


def key_for_job(db, job_key):
    r = db.q("SELECT submission_key, state FROM submissions WHERE job_key=? ORDER BY updated DESC LIMIT 1", (job_key,))
    return (r[0]["submission_key"], r[0]["state"]) if r else (None, None)


def submitted_today(db, now=None):
    lt = time.localtime(now or time.time())
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return db.q("SELECT COUNT(*) AS n FROM submissions WHERE state IN (?,?,?) AND updated >= ?",
                (SUBMITTED, UNKNOWN, CONFIRMING, start))[0]["n"]
