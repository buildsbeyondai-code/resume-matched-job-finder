"""Auto apply: a queue that applies to jobs one at a time, end to end, on the employer's own website.

For each job the browser worker (apply.Assistant) finds the company's application form, fills it from
resume_details.md, answers screening questions from saved answers, steps through multi-page forms, presses
Submit and checks for a confirmation page (a screenshot is kept as proof). A job that needs something only
the user can give is parked with a clear status instead of guessing:
  needs_answers   required questions not in resume_details.md (answered once in the app, then retried)
  needs_you       sign-in, CAPTCHA, or a widget that can't be filled automatically
  no_company_site the job is only on an aggregator; it is never applied for there
  cancelled       the user cancelled the run before this job was finished (Retry queues it again)
When the company has no application of its own, the job portals enabled in Settings (Naukri, then LinkedIn)
are the last resort. Portal applications run one at a time with the configured pause between them.
"""
import hashlib
import math
import threading
import time

from . import apply, audit, config, details, policy, portals, states

ACTIVE = ("queued", "running")
DONE = ("submitted", "applied_manually")
LOCKED = DONE + ("outcome_unknown",)  # never queued again automatically (STATE_MACHINE_AND_IDEMPOTENCY.md)
MAX_BATCH = 25


class Cancelled(apply.ApplyError):
    """The user cancelled the run."""


class _Hooks:
    """Callbacks the browser worker makes while it applies to one job (on the browser thread)."""

    def __init__(self, runner, key):
        self.runner, self.key, self.used_portal = runner, key, False

    def check(self):
        """Raise Cancelled when the run must stop (unattended mode adds pause and quiet hours)."""
        self.runner.check_cancel()

    def step(self, text):
        self.check()
        self.runner.db.app_set(self.key, "running", step=text, wait_until=None)

    def before_portal(self):
        self.used_portal = True
        self.runner.portal_gate(self.key, self.check)

    def before_submit(self, what, channel):
        """The policy gate and the submission claim, right before a click that may send the application."""
        self.check()
        db = self.runner.db
        job = dict(db.posting(self.key) or {}, key=self.key)
        self.sub_key = policy.gate(db, job, channel)
        attempt = policy.claim(db, self.sub_key, self.key)
        resume = details.resume_file()
        audit.record(db, "submit.attempt", self.key, {
            "attempt": attempt, "button": what[:80], "channel": channel, "company": job.get("company"),
            "title": job.get("title"), "resume_sha256": audit.sha256_file(resume) if resume else None,
            "resume_name": resume.name if resume else None})
        db.app_set(self.key, "running", step=f"Submitting ({what[:40]})")

    def after_submit(self, outcome, detail):
        db = self.runner.db
        if outcome == "submitted_ungated":  # a button not recognised as Submit sent the application
            job = dict(db.posting(self.key) or {}, key=self.key)
            self.sub_key = policy.submission_key(job)
            audit.record(db, "submit.ungated", self.key, {"submission_key": self.sub_key, **detail})
            if policy.state_of(db, self.sub_key) in (None, policy.NOT_SUBMITTED):
                policy.claim(db, self.sub_key, self.key)
            outcome = "submitted"
        to = {"submitted": policy.SUBMITTED, "not_submitted": policy.NOT_SUBMITTED, "unknown": policy.UNKNOWN}[outcome]
        if policy.state_of(db, self.sub_key) != to:
            policy.move(db, self.sub_key, to, detail)


def qid(label: str) -> str:
    return hashlib.sha1(details.norm(details.clean_label(label)).encode(), usedforsecurity=False).hexdigest()[:12]


class Runner:
    def __init__(self, db, assistant=None):
        self.db = db
        self.assistant = assistant or apply.ASSISTANT
        self.lock = threading.Lock()
        self.thread = None
        self.current = None
        self.cancelled = threading.Event()
        self.last_portal_end = 0.0  # when the last portal application finished (rate limit)
        self.portal_starts = []  # when each portal application started (shown in tests and diagnostics)
        for a in db.app_list():  # a run interrupted by an app restart
            if a["status"] in ACTIVE:
                if not self._unknown_if_submitting(a["key"], "the app stopped while Submit was being pressed"):
                    db.app_set(a["key"], "failed", reason="Interrupted when the app stopped. Press Retry.")

    def _unknown_if_submitting(self, key, why):
        """A claim still SUBMITTING/CONFIRMING means Submit may have reached the site: record OUTCOME_UNKNOWN
        (never retried automatically). Returns True when that happened."""
        sub, st = policy.key_for_job(self.db, key)
        if st not in (policy.SUBMITTING, policy.CONFIRMING):
            return False
        policy.move(self.db, sub, policy.UNKNOWN, {"why": why})
        self.db.app_set(key, "outcome_unknown", step="", wait_until=None,
                        reason=f"Outcome unknown: {why}. Check in the browser or your email whether the application "
                               "was sent, then record it here.")
        return True

    # --- queue ---
    def enqueue(self, keys):
        """Queue jobs (skipping ones already submitted or in the queue). Returns the keys queued."""
        queued = []
        for k in list(dict.fromkeys(keys))[:MAX_BATCH]:
            job = self.db.posting(k)
            cur = self.db.app_get(k)
            if not job or (cur and cur["status"] in LOCKED + ACTIVE):
                continue
            self.db.app_set(k, "queued", title=job.get("title"), company=job.get("company"),
                            job_url=job.get("url"), reason="", missing=[], queued_at=time.time())
            queued.append(k)
        self._kick()
        return queued

    def _kick(self):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="auto-apply", daemon=True)
                self.thread.start()

    def _next(self):
        items = [a for a in self.db.app_list() if a["status"] == "queued"]
        return min(items, key=lambda a: a.get("queued_at", 0))["key"] if items else None

    def _loop(self):
        while True:
            key = self._next()
            if key is None:
                self.current = None
                return
            self.current = key
            self.run_one(key)

    def run_one(self, key, hooks=None):
        job = self.db.posting(key)
        if not job:
            return self.db.app_set(key, "failed", reason="The job is no longer stored. Run the search again.")
        self.cancelled.clear()
        hooks = hooks or _Hooks(self, key)
        try:
            policy.can_start(self.db)
        except policy.Blocked as e:
            return self.db.app_set(key, "policy_blocked", reason=str(e), step="", wait_until=None,
                                   title=job.get("title"), company=job.get("company"), job_url=job.get("url"))
        st = self.db.settings()
        job = dict(job, key=key, hooks=hooks, portals=portals.enabled(st.get("fallback_portals")),
                   ai=st.get("ai_answers", True))
        self.db.app_set(key, "running", reason="", started=time.time(), title=job.get("title"), step="Starting",
                        wait_until=None, company=job.get("company"), job_url=job.get("url"))
        try:
            r = self.assistant.auto(job)
        except Cancelled as e:
            if self._unknown_if_submitting(key, "the run was stopped while Submit was being pressed"):
                return self.db.app_get(key)
            return self.db.app_set(key, "cancelled", reason=str(e), step="", wait_until=None)
        except apply.ApplyError as e:
            if self._unknown_if_submitting(key, "the browser failed while Submit was being pressed"):
                return self.db.app_get(key)
            return self.db.app_set(key, "failed", reason=str(e), step="", wait_until=None)
        except Exception as e:  # noqa: BLE001 - one job's failure must not stop the queue
            if self._unknown_if_submitting(key, "the browser failed while Submit was being pressed"):
                return self.db.app_get(key)
            return self.db.app_set(key, "failed", step="", wait_until=None,
                                   reason="Browser error: " + (str(e).splitlines() or ["unknown"])[0][:300])
        finally:
            if hooks.used_portal:
                self.last_portal_end = time.time()
        status = r["status"]
        data = {"reason": r.get("reason", ""), "url": r.get("url"), "site": r.get("site"), "via": r.get("via"),
                "channel": r.get("channel"), "step": "", "wait_until": None,
                "missing": [dict(q, qid=qid(q["label"])) for q in r.get("missing", [])],
                "ai": r.get("ai", []), "ai_error": r.get("ai_error", "")}
        if status == "submitted":
            data.update(proof=r.get("proof"), confirmation=r.get("confirmation"), submitted_at=time.time())
            self.db.set_job_state(key, "saved")
        if status not in states.KEEP_WINDOW and r.get("session"):  # keep it open only when the user acts in it
            try:
                self.assistant.close_session(r["session"])
            except Exception:  # noqa: BLE001
                pass
        return self.db.app_set(key, status, **data)

    def busy(self):
        """True while a run is active (a job queued or running)."""
        return self.current is not None or any(a["status"] in ACTIVE for a in self.db.app_list())

    # --- portal rate limit and cancel ---
    def pause_s(self):
        return min(config.PORTAL_PAUSE_RANGE[1], max(0.0, float(self.db.settings()["portal_pause_s"])))

    def portal_gate(self, key, check=None):
        """Wait until the configured pause has passed since the last portal application (shown as a countdown)."""
        check = check or self.check_cancel
        until = self.last_portal_end + self.pause_s()
        left = until - time.time()
        if self.last_portal_end and left > 0:
            self.db.app_set(key, "running", wait_until=until,
                            step=f"Waiting {math.ceil(left)} s before the next job-portal application (rate limit)")
            while left > 0:
                check()
                time.sleep(min(0.2, left))
                left = until - time.time()
        check()
        self.portal_starts.append(time.time())

    def check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled("Cancelled by you before this application was sent. Press Retry to apply.")

    def cancel(self):
        """Stop the run: queued jobs are cancelled and the running one stops at its next step (never after its
        Submit was pressed, so nothing is half-counted)."""
        n = 0
        for a in self.db.app_list():
            if a["status"] == "queued":
                self.db.app_set(a["key"], "cancelled", reason="Cancelled by you. Press Retry to apply.")
                n += 1
        if self.current is not None:
            self.cancelled.set()
        return n

    # --- user actions ---
    def questions(self):
        """Unique questions across all jobs waiting for answers."""
        out = {}
        for a in self.db.app_list():
            if a["status"] != "needs_answers":
                continue
            for q in a.get("missing", []):
                e = out.setdefault(q["qid"], {**q, "jobs": []})
                e["jobs"].append(a.get("company") or "")
                if q.get("options") and not e.get("options"):
                    e["options"] = q["options"]
        return list(out.values())

    def answer(self, answers):
        """Save answers ({qid: value}) to resume_details.md and retry every job they unblock."""
        qs = [dict(q, id=q["qid"]) for q in self.questions()]
        det = details.load() or {}
        saved = details.save_answers(apply.answers_to_save(qs, answers, det))
        answered = {k for k, v in answers.items() if str(v).strip()}
        retry = [a["key"] for a in self.db.app_list() if a["status"] == "needs_answers"
                 and all(q["qid"] in answered for q in a.get("missing", []))]
        for k in retry:
            self.db.app_set(k, "queued", queued_at=time.time())
        self._kick()
        return {"saved": saved, "retried": retry}

    def retry(self, key):
        a = self.db.app_get(key)
        if not a or a["status"] in LOCKED + ACTIVE:
            return False
        self.db.app_set(key, "queued", queued_at=time.time(), reason="")
        self._kick()
        return True

    def resolve(self, key, outcome):
        """The user's answer for an Outcome unknown application: "submitted" or "not_submitted"."""
        a = self.db.app_get(key)
        if not a or a["status"] != "outcome_unknown" or outcome not in ("submitted", "not_submitted"):
            return False
        sub, st = policy.key_for_job(self.db, key)
        if st == policy.UNKNOWN:
            policy.move(self.db, sub, policy.SUBMITTED if outcome == "submitted" else policy.NOT_SUBMITTED,
                        {"why": "recorded by the user"})
        audit.record(self.db, "submit.resolved", key, {"outcome": outcome})
        if outcome == "submitted":
            self.db.app_set(key, "applied_manually", submitted_at=time.time(),
                            reason="You confirmed the application was sent.")
        else:
            self.db.app_set(key, "failed", reason="You confirmed nothing was sent. Retry applies again.")
        return True

    def mark_applied(self, key):
        a = self.db.app_get(key)
        if not a:
            return False
        if a["status"] == "outcome_unknown":
            return self.resolve(key, "submitted")
        if a["status"] in DONE:
            return False
        self.db.app_set(key, "applied_manually", submitted_at=time.time(), reason="Marked as applied by you.")
        return True

    def snapshot(self):
        items = self.db.app_list()
        counts = {}
        for a in items:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return {"items": items, "questions": self.questions(), "counts": counts, "running": self.current,
                "now": time.time()}
