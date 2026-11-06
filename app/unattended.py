"""Unattended auto-apply: while the app is running, re-run the saved searches the user switched on, on a
schedule, and apply to every new job at or above the minimum match score through the existing apply flows
(company site, then Naukri, then LinkedIn: app/apply.py via autoapply.Runner.run_one).

Guard rails (each skip is logged with its reason): pause / kill switch (persisted), quiet hours, a daily cap
(remaining jobs are deferred to a later run, not dropped), a per-company cooldown, duplicates, jobs already in
Applications and hidden jobs. Anything uncertain is parked as "Needs you" with the reason; nothing is guessed.

Crash safety (M9): an 'attempting' row is committed before the application starts, so before Submit can be
pressed. On start, every 'attempting' row becomes Needs you ("Interrupted, check whether it was submitted") and
that job is never applied to again automatically (it is in Applications, so the "already applied" guard skips it).

Threads: one timer thread (sleeps until the next due time; no CPU between runs) and at most one run thread. The
browser itself is the single apply-browser thread of app/apply.py, so at most one apply session exists at a time.
Logs hold only job, company, score, source, status and reason: never form values or resume text.
"""
import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path

from . import autoapply, config, direct, policy, search

KEY = "unattended"  # settings row holding this feature's settings and state
DEFAULTS = {
    "configured": False,  # the user has saved unattended settings at least once (else: empty state)
    "enabled": False,     # off by default, also after an upgrade (M1)
    "paused": False,      # the kill switch; persisted, so it stays paused after a restart (M8)
    "pause_reason": "",
    "auto_paused": False,  # paused by S1 (3 run-level errors in a row), shown as a banner
    "interval_min": 60, "min_score": 80, "daily_cap": 15, "cooldown_days": 30,
    "quiet_start": "22:00", "quiet_end": "07:00",
    "searches": [],       # ids of the saved searches switched on (S5); none by default
    "last_tick": None,    # when the schedule last fired (next run = last_tick + interval)
    "preview_done": False,  # a dry-run preview runs once before the first real run
    "error_streak": 0,
}
LIMITS = {  # field: (min, max, message); messages say how to fix the problem (ISO 9241-110 error tolerance)
    "interval_min": (15, 1440, "Interval must be 15-1440 minutes"),
    "min_score": (0, 100, "Minimum match score must be 0-100"),
    "daily_cap": (1, 100, "Daily cap must be 1-100 applications"),
    "cooldown_days": (0, 365, "Company cooldown must be 0-365 days"),
}
AUTO_PAUSE_AFTER = 3  # run-level errors in a row (S1); 0 would mean never
INTERRUPTED = "Interrupted, check whether it was submitted"
DONE = ("submitted", "applied_manually")
OPEN_APP = ("needs_you", "needs_answers", "failed", "cancelled", "no_company_site", "outcome_unknown",
            "ready_for_review", "policy_blocked")  # still waiting for the user
STATUS_WORDS = {"queued": "queued", "running": "applying now", "needs_answers": "needs your answers",
                "needs_you": "needs you", "no_company_site": "no company website", "failed": "failed",
                "cancelled": "cancelled", "dismissed": "dismissed"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------- pure helpers (unit-tested) ----------------
def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet(ts, start, end):
    """True when local time `ts` is inside quiet hours [start, end); the window may span midnight.
    start == end means no quiet hours."""
    s, e = minutes(start), minutes(end)
    if s == e:
        return False
    lt = time.localtime(ts)
    t = lt.tm_hour * 60 + lt.tm_min
    return s <= t < e if s < e else (t >= s or t < e)


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def search_id(params_json):
    return hashlib.sha1(params_json.encode(), usedforsecurity=False).hexdigest()[:12]


def search_label(params):
    parts = []
    if params.get("keywords"):
        parts.append(f'"{params["keywords"]}"')
    if params.get("skills"):
        parts.append(", ".join(params["skills"]))
    parts.append({"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(params.get("mode"), "Any work mode"))
    parts.append(", ".join(params.get("countries") or []) or "any country")
    if params.get("companies"):
        parts.append("companies: " + ", ".join(params["companies"]))
    return " · ".join(parts)


def company_norm(name):
    name = re.sub(direct.SUFFIXES, " ", (name or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", name))


def summary_text(s):
    """The plain-language summary shown before the user confirms (J1)."""
    quiet = (f", not between {s['quiet_start']} and {s['quiet_end']}" if s["quiet_start"] != s["quiet_end"]
             else ", at any time of day")
    cool = (f" At most one application per company every {s['cooldown_days']} days." if s["cooldown_days"]
            else " No company cooldown.")
    n = len(s["searches"])
    return (f"Up to {s['daily_cap']} applications a day to jobs scoring {s['min_score']} or more, every "
            f"{s['interval_min']} min{quiet}.{cool} Uses {n} saved search{'' if n == 1 else 'es'}.")


def validate(body, known_searches):
    """Check a settings form. Returns (clean values, [{field, message}]). Nothing is saved when there are errors."""
    clean, errors = {}, []
    for field, (lo, hi, msg) in LIMITS.items():
        if field not in body:
            continue
        v = body[field]
        ok = not isinstance(v, bool) and isinstance(v, (int, str)) and re.fullmatch(r"\s*\d{1,5}\s*", str(v))
        if not ok or not lo <= int(v) <= hi:
            errors.append({"field": field, "message": msg + "."})
        else:
            clean[field] = int(v)
    for field in ("quiet_start", "quiet_end"):
        if field in body:
            v = body[field]
            if not isinstance(v, str) or not HHMM.match(v.strip()):
                errors.append({"field": field, "message": "Enter a time as HH:MM, for example 22:00."})
            else:
                clean[field] = v.strip()
    if "searches" in body:
        v = body["searches"]
        if not isinstance(v, list) or not all(isinstance(x, str) and x in known_searches for x in v):
            errors.append({"field": "searches", "message": "Choose saved searches from the list."})
        else:
            clean["searches"] = list(dict.fromkeys(v))
    return clean, errors


def job_url_ok(url):
    url = url or ""
    return url.startswith("https://") or (config.ALLOW_LOCAL_HTTP and
                                          re.match(r"^http://(127\.0\.0\.1|localhost)[:/]", url) is not None)


class _Hooks(autoapply._Hooks):
    """The auto-apply hooks plus the unattended stop rules: checked before every form action and click."""

    def __init__(self, runner, key, ua):
        super().__init__(runner, key)
        self.ua = ua

    def check(self):
        why = self.ua.abort_reason()
        if why:
            raise autoapply.Cancelled(why)
        self.runner.check_cancel()

    def before_action(self):
        self.check()

    def before_click(self, kind):
        self.check()


class Unattended:
    def __init__(self, db, runner, clock=None, start=True):
        self.db, self.runner = db, runner
        self.clock = clock or time.time
        self.wake = threading.Event()   # settings changed: recompute the next due time
        self.stopped = threading.Event()
        self.abort = threading.Event()  # pause / off pressed: stop the job in progress before its next action
        self.lock = threading.Lock()
        self.tick_lock = threading.Lock()  # a tick checks "previous run active" and starts the run atomically
        self.worker = None  # the run thread
        self.timer = None
        self.current = None  # the job being applied to (shown in the status bar)
        self.run_quiet = None  # (start, end) of the run in progress
        self.recover()
        if start:
            self.start()

    # ---------------- settings ----------------
    def settings(self):
        return {**DEFAULTS, **(self.db.get_setting(KEY) or {})}

    def _save(self, **changes):
        with self.lock:
            s = self.settings()
            s.update(changes)
            self.db.set_setting(KEY, s)
        self.wake.set()
        return s

    def saved_searches(self):
        """The saved searches (each distinct search the user ran), newest first, with their switch (S5)."""
        on = set(self.settings()["searches"])
        rows = self.db.q("SELECT params, MAX(created) AS last, COUNT(*) AS n FROM searches GROUP BY params "
                         "ORDER BY last DESC LIMIT 50")
        out = []
        for r in rows:
            sid = search_id(r["params"])
            out.append({"id": sid, "label": search_label(json.loads(r["params"])), "last_run": r["last"],
                        "runs": r["n"], "enabled": sid in on})
        return out

    def _params(self, sid):
        for r in self.db.q("SELECT DISTINCT params FROM searches"):
            if search_id(r["params"]) == sid:
                return r["params"]
        return None

    def update(self, body):
        """Save settings (validate_only: just check and return the summary). Enabling needs confirm: true.
        Returns (status dict, errors)."""
        if not isinstance(body, dict):
            return None, [{"field": None, "message": "The request could not be read."}]
        known = {x["id"] for x in self.saved_searches()}
        clean, errors = validate(body, known)
        cur = self.settings()
        merged = {**cur, **clean}
        enable = body.get("enable") is True
        if (enable or merged["enabled"]) and not merged["searches"]:
            errors.append({"field": "searches", "message": "Switch on at least one saved search."})
        if enable and not cur["enabled"] and body.get("confirm") is not True and not body.get("validate_only"):
            errors.append({"field": "confirm", "message": "Review the summary, then select Confirm and turn on."})
        if errors:
            return None, errors
        if body.get("validate_only"):
            return {"summary_text": summary_text(merged)}, []
        changes = dict(clean, configured=True)
        first_preview = False
        if enable and not cur["enabled"]:
            changes.update(enabled=True, paused=False, pause_reason="", auto_paused=False, error_streak=0,
                           last_tick=self.clock())  # first real run one interval from now
            first_preview = not cur["preview_done"]
            self.abort.clear()
        self._save(**changes)
        if first_preview:  # the dry-run preview runs once, right away, before the first real run
            self.tick("preview")
        return self.status(), []

    def set_search(self, sid, on):
        if sid not in {x["id"] for x in self.saved_searches()}:
            return False
        cur = [x for x in self.settings()["searches"] if x != sid]
        self._save(searches=cur + [sid] if on else cur)
        return True

    def pause(self, reason="Paused by you.", auto=False):
        """The kill switch: stops the schedule and aborts the job in progress before its next form action."""
        self.abort.set()
        return self._save(paused=True, pause_reason=reason, auto_paused=auto)

    def resume(self):
        self.abort.clear()
        return self._save(paused=False, pause_reason="", auto_paused=False, error_streak=0, last_tick=self.clock())

    def turn_off(self):
        self.abort.set()
        return self._save(enabled=False)

    def mode(self, s=None):
        s = s or self.settings()
        return "off" if not s["enabled"] else "paused" if s["paused"] else "running"

    def due(self, s=None):
        """When the timer fires next (also while paused, so a paused tick is logged as skipped), else None."""
        s = s or self.settings()
        if not s["enabled"]:
            return None
        return (s["last_tick"] or self.clock()) + s["interval_min"] * 60

    def next_run(self, s=None):
        """The next run shown to the user: none while off or paused."""
        s = s or self.settings()
        return None if s["paused"] else self.due(s)

    # ---------------- timer ----------------
    def start(self):
        if self.timer is None:
            self.timer = threading.Thread(target=self._timer_loop, name="unattended-timer", daemon=True)
            self.timer.start()

    def stop_timer(self):
        """Stop the timer thread (no more scheduled ticks). A run in progress is not affected."""
        self.stopped.set()
        self.wake.set()

    def close(self):
        """Shut down: no more ticks, and a run in progress stops before its next form action."""
        self.abort.set()
        self.stop_timer()

    def _timer_loop(self):
        while not self.stopped.is_set():
            due = self.due()
            wait = None
            if due is not None:
                late = self.clock() - due
                if late >= 0:  # after downtime this fires once (catch-up), then the schedule restarts from now
                    self.tick("catch-up" if late > 120 else "schedule")
                    continue
                wait = min(-late, 300)
            self.wake.wait(wait)
            self.wake.clear()

    def run_active(self):
        return self.worker is not None and self.worker.is_alive()

    def tick(self, trigger, dry=None):
        """Start a run unless a guard says no (then the skip is logged). Returns (run id, skip reason).
        Until a dry-run preview has finished, every run is a preview (nothing is submitted)."""
        with self.tick_lock:
            return self._tick(trigger, dry)

    def _tick(self, trigger, dry):
        s = self.settings()
        now = self.clock()
        dry = (not s["preview_done"]) if dry is None else dry
        if trigger in ("schedule", "catch-up"):
            s = self._save(last_tick=now)
        reason = None
        if self.run_active():
            reason = "previous run active"
        elif not s["enabled"] and trigger != "preview":
            reason = "unattended mode is off"
        elif s["paused"]:
            reason = "paused"
        elif in_quiet(now, s["quiet_start"], s["quiet_end"]):
            reason = "quiet hours"
        elif not dry and policy.effective_mode(policy.load(self.db), now) != policy.AUTO:
            reason = ("automatic submission is not authorized (Settings, Application policy: "
                      "choose \"Submit automatically\" and confirm its scope)")
        elif not dry and self.submitted_today(now) >= s["daily_cap"]:
            reason = "daily cap reached"
        elif not s["searches"]:
            reason = "no saved searches switched on"
        if reason:
            return self._log_run(trigger, "skipped", reason, now, dry), reason
        run_id = self._log_run(trigger, "running", "", now, dry)
        self.abort.clear()
        t = threading.Thread(target=self._run, args=(run_id, s, dry), name="unattended-run", daemon=True)
        t.start()
        self.worker = t
        return run_id, None

    def _log_run(self, trigger, status, note, now, dry):
        return self.db.x("INSERT INTO ua_runs(started,ended,day,trigger,status,note,dry,searches,source_errors,counts) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (now, now if status != "running" else None, day_of(now), trigger, status, note, int(dry),
                          "[]", "{}", "{}"))

    # ---------------- crash recovery (M9) ----------------
    def recover(self):
        now = self.clock()
        with self.db.lock:
            self.db.conn.execute("UPDATE ua_runs SET status='error', ended=?, note='The app stopped during this run.' "
                                 "WHERE status='running'", (now,))
            rows = self.db.conn.execute("SELECT id, key FROM ua_jobs WHERE outcome='attempting'").fetchall()
            self.db.conn.execute("UPDATE ua_jobs SET outcome='needs_you', reason=? WHERE outcome='attempting'",
                                 (INTERRUPTED,))
            self.db.conn.commit()
        for r in rows:  # never retried automatically: the job stays in Applications as Needs you
            self.db.app_set(r["key"], "needs_you", reason=INTERRUPTED + " (the app stopped while applying).")

    # ---------------- guards ----------------
    def submitted_today(self, now=None):
        return self.db.q("SELECT COUNT(*) AS n FROM ua_jobs WHERE day=? AND outcome='submitted'",
                         (day_of(now or self.clock()),))[0]["n"]

    def cooldown_until(self, company, days, now):
        """When the company may be applied to again (manual and automatic applications both count), else None."""
        if not days:
            return None
        c = company_norm(company)
        last = 0.0
        for a in self.db.app_list():
            if a["status"] in DONE and c and company_norm(a.get("company")) == c:
                last = max(last, a.get("submitted_at") or a.get("updated") or 0)
        until = last + days * 86400
        return until if last and until > now else None

    def ineligible(self, p, s, states, now):
        key = p["key"]
        app = self.db.app_get(key)
        if app and app["status"] in DONE:
            return "already applied"
        if app:
            return f"already in Applications ({STATUS_WORDS.get(app['status'], app['status'])})"
        if states.get(key) == "hidden":
            return "hidden by you"
        until = self.cooldown_until(p.get("company"), s["cooldown_days"], now)
        if until:
            return f"company cooldown until {day_of(until)}"
        if not job_url_ok(p.get("url")):
            return "no application link"
        return None

    def abort_reason(self):
        if self.abort.is_set():
            return INTERRUPTED + " (unattended mode was paused)."
        if self.run_quiet and in_quiet(self.clock(), *self.run_quiet):
            return INTERRUPTED + " (quiet hours began)."
        return None

    def stop_reason(self, s):
        """Why the run must not start another job (the remaining ones are deferred), else None."""
        if self.abort.is_set():
            return "paused"
        if in_quiet(self.clock(), s["quiet_start"], s["quiet_end"]):
            return "quiet hours"
        if any(a["status"] in autoapply.ACTIVE for a in self.db.app_list()):
            return "another auto apply is running"
        return None

    def deferred_keys(self):
        rows = self.db.q("SELECT key FROM ua_jobs WHERE id IN (SELECT MAX(id) FROM ua_jobs GROUP BY key) "
                         "AND outcome IN ('deferred','preview') ORDER BY id")
        return [r["key"] for r in rows]

    # ---------------- a run ----------------
    def _job_row(self, run_id, p, outcome, reason, source, now):
        return self.db.x("INSERT INTO ua_jobs(run_id,key,ts,day,outcome,reason,title,company,score,source) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (run_id, p["key"], now, day_of(now), outcome, reason[:400], p.get("title", "")[:200],
                          p.get("company", "")[:200], int(p.get("score") or 0), source[:200]))

    def _source_name(self, p):
        return ", ".join(x["name"] for x in p.get("sources") or []) or p.get("source", "")

    def _run(self, run_id, s, dry):
        counts = {"found": 0, "new": 0, "candidates": 0, "submitted": 0, "needs_you": 0, "skipped": 0,
                  "deferred": 0, "preview": 0}
        errors, searched = {}, []
        status, note = "done", ""
        self.run_quiet = (s["quiet_start"], s["quiet_end"])
        try:
            candidates, ok_searches = [], 0
            for key in self.deferred_keys():  # deferred by the cap (or a preview) in an earlier run
                p = self.db.posting(key)
                if p:
                    candidates.append((p, "deferred from an earlier run"))
            for sid in s["searches"]:
                if self.stop_reason(s) == "paused":  # paused while searching: not an error
                    note = "Stopped early: unattended mode was paused."
                    break
                params_json = self._params(sid)
                if params_json is None:
                    errors["Saved search " + sid] = "This saved search no longer exists."
                    continue
                params = json.loads(params_json)
                label = search_label(params)
                prev = self.db.last_search(params_json)
                prev_keys = set(json.loads(prev["result_keys"])) if prev else set()
                run = search.start(self.db, params, sync=True, fresh=True)  # the same search and scoring as the UI
                failed = [x for x in run.status.values() if x["state"] == "failed"]
                for f in failed:  # one failing source never aborts the run (M12)
                    errors[f["label"]] = f["error"]
                if run.result is None or (failed and len(failed) == len(run.status)):
                    searched.append({"id": sid, "label": label, "found": 0, "new": 0, "failed": True})
                    continue
                ok_searches += 1
                cur = self.db.last_search(params_json)
                keys = json.loads(cur["result_keys"])
                new = [k for k in keys if k not in prev_keys]
                counts["found"] += len(keys)
                counts["new"] += len(new)
                searched.append({"id": sid, "label": label, "found": len(keys), "new": len(new), "failed": False})
                for k in new:
                    p = self.db.posting(k)
                    if p and (p.get("score") or 0) >= s["min_score"]:
                        candidates.append((p, label))
            if s["searches"] and not ok_searches and not candidates and not note:
                status, note = "error", "No saved search could be run: every source failed."
            candidates.sort(key=lambda c: -(c[0].get("score") or 0))
            counts["candidates"] = len(candidates)
            self._process(run_id, s, dry, candidates, counts)
        except Exception as e:  # noqa: BLE001 - a run-level error is logged (and counts toward the auto-pause)
            status, note = "error", "The run failed: " + (str(e).splitlines() or [type(e).__name__])[0][:300]
        finally:
            self.run_quiet = None
            self.current = None
        self.db.x("UPDATE ua_runs SET ended=?, status=?, note=?, searches=?, source_errors=?, counts=? WHERE id=?",
                  (self.clock(), status, note, json.dumps(searched), json.dumps(errors), json.dumps(counts), run_id))
        self._after_run(status, note, dry)

    def _after_run(self, status, note, dry):
        s = self.settings()
        if dry:
            self._save(preview_done=True)
            return
        if status != "error":
            if s["error_streak"]:
                self._save(error_streak=0)
            return
        streak = s["error_streak"] + 1
        self._save(error_streak=streak)
        if AUTO_PAUSE_AFTER and streak >= AUTO_PAUSE_AFTER and not s["paused"]:
            self.pause(f"Paused automatically: the last {streak} runs failed ({note}) Check your network and "
                       f"job sources, then resume.", auto=True)

    def _process(self, run_id, s, dry, candidates, counts):
        seen, states = set(), self.db.job_states()
        cap_left = s["daily_cap"] - self.submitted_today()
        for p, source in candidates:
            now = self.clock()
            key = p["key"]
            src = f"{self._source_name(p)} ({source})" if self._source_name(p) else source
            if key in seen:  # the same job (dedupe key) from two saved searches
                self._job_row(run_id, p, "skipped", "duplicate", src, now)
                counts["skipped"] += 1
                continue
            seen.add(key)
            why = self.ineligible(p, s, states, now)
            if why:
                self._job_row(run_id, p, "skipped", why, src, now)
                counts["skipped"] += 1
                continue
            stop = self.stop_reason(s)
            if stop or cap_left <= 0:
                self._job_row(run_id, p, "deferred", f"deferred: {stop or 'daily cap'}", src, now)
                counts["deferred"] += 1
                continue
            if dry:
                self._job_row(run_id, p, "preview", "dry run: would apply", src, now)
                counts["preview"] += 1
                continue
            outcome = self._apply(run_id, p, src)
            counts[outcome] += 1
            if outcome == "submitted":
                cap_left -= 1

    def _apply(self, run_id, p, src):
        key = p["key"]
        # M9: committed before the application starts, so before any Submit can be pressed
        row = self._job_row(run_id, p, "attempting", "Applying now", src, self.clock())
        self.current = {"key": key, "title": p.get("title"), "company": p.get("company")}
        self.runner.current = key
        try:
            a = self.runner.run_one(key, hooks=_Hooks(self.runner, key, self))
        finally:
            self.runner.current = None
        outcome, reason, proof = self._outcome(key, a)
        self.db.x("UPDATE ua_jobs SET outcome=?, reason=?, proof=?, ts=? WHERE id=?",
                  (outcome, reason[:400], proof, self.clock(), row))
        return outcome

    def _outcome(self, key, a):
        """Map an application result to Submitted / Needs you (never guessed)."""
        st = a["status"]
        if st == "submitted":
            proof = a.get("proof")
            if proof and Path(proof).is_file():
                _owner_only(Path(proof))
                return "submitted", a.get("confirmation") or "Confirmation page shown", proof
            reason = "Confirmation seen, but the proof screenshot could not be saved. Check the application."
            self.db.app_set(key, "outcome_unknown", reason=reason)  # never retried; the user records it
            return "needs_you", reason, None
        if st == "needs_answers":
            qs = "; ".join(q.get("label", "").rstrip(" *✱") for q in a.get("missing", []))
            return "needs_you", f"Unanswered required question: {qs}"[:400], None
        if st == "cancelled":
            reason = a.get("reason") or INTERRUPTED
            if not reason.startswith("Interrupted"):
                reason = INTERRUPTED + " (stopped by you)."
            self.db.app_set(key, "needs_you", reason=reason)
            return "needs_you", reason, None
        if st == "failed":
            return "needs_you", "Unexpected page or error: " + (a.get("reason") or "unknown"), None
        return "needs_you", a.get("reason") or "Needs you", None

    # ---------------- views ----------------
    def needs_you_open(self):
        rows = self.db.q("SELECT key FROM ua_jobs WHERE outcome='needs_you' AND resolved=0")
        n = 0
        for k in {r["key"] for r in rows}:
            a = self.db.app_get(k)
            n += bool(a and a["status"] in OPEN_APP)
        return n

    def status(self):
        s = self.settings()
        now = self.clock()
        searches = self.saved_searches()
        return {"mode": self.mode(s), "configured": s["configured"], "now": now,
                "settings": {k: s[k] for k in ("interval_min", "min_score", "daily_cap", "cooldown_days",
                                               "quiet_start", "quiet_end", "searches")},
                "summary_text": summary_text(s), "next_run": self.next_run(s),
                "next_run_hhmm": hhmm(self.next_run(s)) if self.next_run(s) else None,
                "quiet_now": in_quiet(now, s["quiet_start"], s["quiet_end"]),
                "run_active": self.run_active(), "current": self.current,
                "today": {"submitted": self.submitted_today(now), "cap": s["daily_cap"]},
                "needs_you": self.needs_you_open(), "pause_reason": s["pause_reason"],
                "auto_paused": s["auto_paused"], "preview_done": s["preview_done"], "searches": searches}

    def _job_view(self, r):
        d = dict(r)
        a = self.db.app_get(r["key"]) or {}
        d["app_status"] = a.get("status")
        d["url"] = a.get("url") if str(a.get("url") or "").startswith("https://") else a.get("job_url")
        d["has_proof"] = bool(r["proof"] and Path(r["proof"]).is_file())
        d.pop("proof", None)  # the file path stays on the server; the UI uses /api/applications/<key>/proof
        d["open"] = r["outcome"] == "needs_you" and not r["resolved"] and a.get("status") in OPEN_APP
        return d

    def summary(self, day):
        runs = self.db.q("SELECT COUNT(*) AS n FROM ua_runs WHERE day=?", (day,))[0]["n"]
        rows = self.db.q("SELECT * FROM ua_jobs WHERE day=? ORDER BY ts DESC, id DESC", (day,))
        groups = {"applied": [], "needs_you": [], "skipped": [], "in_progress": []}
        for r in rows:
            g = {"submitted": "applied", "needs_you": "needs_you", "attempting": "in_progress"}.get(r["outcome"], "skipped")
            groups[g].append(self._job_view(r))
        return {"day": day, "runs": runs, **groups,
                "counts": {k: len(v) for k, v in groups.items()}}

    def summary_csv(self, day):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time", "outcome", "title", "company", "score", "source", "reason"])
        sm = self.summary(day)
        for g in ("applied", "needs_you", "skipped"):
            for r in sm[g]:
                row = [time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["outcome"], r["title"],
                       r["company"], r["score"], r["source"], r["reason"]]
                w.writerow(["'" + str(c) if str(c)[:1] in "=+-@" else c for c in row])  # no formula injection
        return buf.getvalue()

    def runs(self, limit=30):
        out = []
        for r in self.db.q("SELECT * FROM ua_runs ORDER BY id DESC LIMIT ?", (limit,)):
            d = dict(r)
            for k in ("searches", "source_errors", "counts"):
                d[k] = json.loads(d[k] or "null")
            d["jobs"] = [self._job_view(j) for j in self.db.q("SELECT * FROM ua_jobs WHERE run_id=? ORDER BY id",
                                                              (r["id"],))]
            out.append(d)
        return out

    def dismiss(self, job_id):
        r = self.db.q("SELECT key FROM ua_jobs WHERE id=? AND outcome='needs_you'", (job_id,))
        if not r:
            return False
        a = self.db.app_get(r[0]["key"])
        if a and a["status"] == "outcome_unknown":
            return False  # an unknown outcome is recorded, not dismissed (Applications: "It was sent" / "Not sent")
        self.db.x("UPDATE ua_jobs SET resolved=1 WHERE key=? AND outcome='needs_you'", (r[0]["key"],))
        if a and a["status"] in OPEN_APP:
            self.db.app_set(r[0]["key"], "dismissed", reason="Dismissed by you.")
        return True

    def wipe(self):
        """Delete all data (S4): stop any run and remove the proof screenshots (the tables are cleared by the DB)."""
        self.abort.set()
        folder = config.APPLICATIONS_DIR
        if folder.is_dir():
            for f in folder.glob("*.png"):
                f.unlink()
        self.wake.set()


def _owner_only(path):
    """Proof screenshots and their folder are readable by the owner only."""
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - e.g. a filesystem without POSIX permissions
        pass
