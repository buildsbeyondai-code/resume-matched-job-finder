"""Unattended auto-apply (docs/ENHANCE_SPEC.md M1-M15, S1-S5, O2): settings and validation, the scheduler
(no overlap, one catch-up), candidate selection, every eligibility guard, quiet hours, the pause / kill switch,
crash safety, the run log and daily summary, per-source failures, outbound hosts, delete-all and security.

The browser is replaced by a scripted fake assistant (the real-browser run is in tests/test_unattended_browser.py,
the UI journeys in tests/test_unattended_ui.py); searches run through the real search.py / matching.py over a mocked network."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app import apply, autoapply, config, matching, sources, unattended
from app.db import DB
from app.server import make_server
from tests.helpers import RESUME_LINES, FakeNet

SCORES = {}  # posting title -> match score (the real scorer is replaced so tests control the numbers exactly)
REAL_SCORE = matching.score


def fake_score(p, profile, level="mid"):
    return {"score": SCORES.get(p.get("title"), 50), "matched": [], "missing": []}


class FakeBoard:
    """A job source whose postings the test controls. Its URL is an allow-listed host (FakeNet answers it)."""
    kind = "fakeboard"

    def urls(self, src, params):
        return [f"https://boards-api.greenhouse.io/v1/boards/{src['token']}/jobs"]

    def parse(self, body, src):
        return [sources._posting("FakeBoard", j["id"], j["title"], j["company"], "Remote", j.get("url") or
                                 f"https://jobs.example.test/{j['id']}", "2026-11-01", j.get("desc", ""), mode="remote")
                for j in json.loads(body)]


def board(*jobs):
    return json.dumps([{"id": t.replace(" ", "-").lower(), "title": t, "company": c} for t, c in jobs])


class FakeAssistant:
    """Stands in for apply.ASSISTANT: `script[title]` is a dict result or a function (job) -> dict."""

    def __init__(self):
        self.script, self.calls, self.closed = {}, [], []

    def auto(self, job):
        self.calls.append(job["title"])
        r = self.script.get(job["title"], "submit")
        if callable(r):
            r = r(job)
        if r == "submit":
            r = submitted(job)
        return {"session": "s1", **r}

    def close_session(self, sid):
        self.closed.append(sid)


def submitted(job):
    config.APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    proof = config.APPLICATIONS_DIR / f"{job['key']}.png"
    proof.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return {"status": "submitted", "proof": str(proof), "confirmation": "Thank you for applying!", "channel": "company"}


def at(hh, mm, day_offset=0):
    """A timestamp at local time hh:mm today (+ days)."""
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, hh, mm, 0, 0, 0, -1))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.dbfile = str(t / "t.sqlite3")
        self.saved = (config.APPLICATIONS_DIR, sources.FETCH, matching.score, dict(sources.ADAPTERS), config.MAX_RETRIES)
        config.MAX_RETRIES = 0  # a failing source fails at once (no back-off waits in tests)
        config.APPLICATIONS_DIR = t / "proofs"
        sources.ADAPTERS["fakeboard"] = FakeBoard()
        matching.score = fake_score
        SCORES.clear()
        self.net = FakeNet({"/boards/a/": board(("Old One", "Acme"), ("Old Two", "Bolt")),
                            "/boards/b/": board(("Other Job", "Cobalt"))})
        sources.FETCH = self.net
        self.fake = FakeAssistant()
        self.clock = None
        self.start()
        self.db.x("DELETE FROM sources")
        for token, label in (("a", "Board A"), ("b", "Board B")):
            self.db.x("INSERT INTO sources(kind,token,label,enabled) VALUES('fakeboard',?,?,1)", (token, label))
        self.db.save_profile(["Python"], ["Engineer"], 6, "cv.pdf", "\n".join(RESUME_LINES))

    def start(self):
        self.db = DB(self.dbfile)
        self.srv = make_server(self.db, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.ua = self.srv.RequestHandlerClass.unattended
        self.runner = self.srv.RequestHandlerClass.auto
        self.runner.assistant = self.fake
        self.ua.stop_timer()  # tests fire ticks themselves (the timer has its own test)
        self.ua.clock = lambda: self.clock if self.clock is not None else time.time()

    def stop(self):
        self.ua.close()
        if self.ua.worker:
            self.ua.worker.join(10)
        self.srv.shutdown()
        self.srv.server_close()
        self.db.conn.close()

    def tearDown(self):
        self.stop()
        config.APPLICATIONS_DIR, sources.FETCH, matching.score, ads, config.MAX_RETRIES = self.saved
        sources.ADAPTERS.clear()
        sources.ADAPTERS.update(ads)
        self.tmp.cleanup()

    # --- helpers ---
    def call(self, method, path, body=None, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-RMJF-Request": "1", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def saved_search(self, **params):
        p = {"countries": [], "mode": "any", **params}
        run = __import__("app.search", fromlist=["start"]).start(self.db, p, sync=True, fresh=True)
        self.assertIsNotNone(run.result)
        return unattended.search_id(json.dumps(run.params, sort_keys=True))

    def setup_on(self, sids, **over):
        """Turn unattended mode on (confirmed) with the preview already done, so runs are real."""
        body = {"searches": sids, "interval_min": 60, "min_score": 80, "daily_cap": 15, "cooldown_days": 30,
                "quiet_start": "00:00", "quiet_end": "00:00", **over, "enable": True, "confirm": True}
        self.ua._save(preview_done=True)
        code, r = self.call("PUT", "/api/unattended/settings", body)
        self.assertEqual(code, 200, r)
        return r

    def go(self, trigger="manual"):
        run_id, why = self.ua.tick(trigger)
        if why is None:
            self.ua.worker.join(30)
            self.assertFalse(self.ua.worker.is_alive())
        return run_id, why

    def run_row(self, run_id):
        return dict(self.db.q("SELECT * FROM ua_runs WHERE id=?", (run_id,))[0])


class Settings(Base):
    def test_m1_off_by_default_validation_confirm_and_enable(self):
        code, st = self.call("GET", "/api/unattended")
        self.assertEqual((code, st["mode"], st["configured"], st["searches"]), (200, "off", False, []))
        self.assertIsNone(st["next_run"])
        sid = self.saved_search(skills=["Python"])
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual([(s["id"], s["enabled"]) for s in st["searches"]], [(sid, False)])
        self.assertIn("Python", st["searches"][0]["label"])
        # interval 5: inline error with the fix, nothing saved
        code, r = self.call("PUT", "/api/unattended/settings", {"interval_min": 5, "searches": [sid]})
        self.assertEqual((code, r["field"], r["error"]), (400, "interval_min", "Interval must be 15-1440 minutes."))
        self.assertIsNone(self.db.get_setting("unattended"))
        bad = {"interval_min": "abc", "min_score": 101, "daily_cap": 0, "cooldown_days": True,
               "quiet_start": "25:00", "quiet_end": 7, "searches": ["nope"]}
        code, r = self.call("PUT", "/api/unattended/settings", bad)
        self.assertEqual(code, 400)
        self.assertEqual({e["field"] for e in r["errors"]}, set(bad))
        self.assertIsNone(self.db.get_setting("unattended"))
        self.assertEqual(self.call("PUT", "/api/unattended/settings", [1])[0], 400)
        # enabling needs at least one search and an explicit confirmation
        code, r = self.call("PUT", "/api/unattended/settings", {"enable": True, "confirm": True})
        self.assertEqual((code, r["field"]), (400, "searches"))
        code, r = self.call("PUT", "/api/unattended/settings", {"searches": [sid], "enable": True})
        self.assertEqual((code, r["field"]), (400, "confirm"))
        self.assertIsNone(self.db.get_setting("unattended"))
        # review: the plain-language summary, still nothing saved
        code, r = self.call("PUT", "/api/unattended/settings", {"searches": [sid], "enable": True, "validate_only": True})
        self.assertEqual(code, 200)
        self.assertTrue(r["summary_text"].startswith(
            "Up to 15 applications a day to jobs scoring 80 or more, every 60 min, not between 22:00 and 07:00."))
        self.assertIsNone(self.db.get_setting("unattended"))
        # confirm: saved, running, next run shown as HH:MM; the dry-run preview starts at once
        code, st = self.call("PUT", "/api/unattended/settings", {"searches": [sid], "enable": True, "confirm": True,
                                                                  "quiet_start": "00:00", "quiet_end": "00:00"})
        self.assertEqual((code, st["mode"], st["configured"]), (200, "running", True))
        self.assertRegex(st["next_run_hhmm"], r"^\d\d:\d\d$")
        self.assertAlmostEqual(st["next_run"] - time.time(), 3600, delta=30)
        self.ua.worker.join(30)
        runs = self.ua.runs()
        self.assertEqual((runs[0]["trigger"], runs[0]["dry"], runs[0]["status"]), ("preview", 1, "done"))
        self.assertEqual(self.fake.calls, [])  # a preview never applies
        self.assertTrue(self.ua.settings()["preview_done"])
        self.assertIn("at any time of day", unattended.summary_text(self.ua.settings()))
        # saving changes while on needs no new confirmation; the settings persist across a restart
        code, st = self.call("PUT", "/api/unattended/settings", {"min_score": 70, "cooldown_days": 0})
        self.assertEqual((code, st["settings"]["min_score"]), (200, 70))
        self.assertIn("No company cooldown", st["summary_text"])
        self.stop()
        self.start()
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual((st["mode"], st["settings"]["min_score"], st["settings"]["searches"]), ("running", 70, [sid]))

    def test_s5_per_search_switch_and_unknown_search(self):
        sid = self.saved_search(skills=["Python"])
        code, st = self.call("PUT", f"/api/unattended/searches/{sid}", {"enabled": True})
        self.assertEqual((code, st["searches"][0]["enabled"]), (200, True))
        self.assertEqual(self.ua.settings()["searches"], [sid])
        code, st = self.call("PUT", f"/api/unattended/searches/{sid}", {"enabled": False})
        self.assertEqual(st["searches"][0]["enabled"], False)
        self.assertEqual(self.call("PUT", "/api/unattended/searches/" + "0" * 12, {"enabled": True})[0], 404)
        self.assertEqual(self.call("PUT", f"/api/unattended/searches/{sid}", [True])[1]["searches"][0]["enabled"], False)

    def test_pause_resume_off_run_now_and_security(self):
        self.assertEqual(self.call("POST", "/api/unattended/run")[0], 409)  # not set up yet
        self.assertEqual(self.call("POST", "/api/unattended/resume", {"confirm": True})[0], 409)  # off
        sid = self.saved_search()
        self.setup_on([sid])
        # cross-site writes are rejected (Origin/Host checks)
        self.assertEqual(self.call("POST", "/api/unattended/pause", {}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/unattended/settings", {"enable": False}, {"Origin": "http://evil"})[0], 403)
        self.assertEqual(self.ua.mode(), "running")
        code, st = self.call("POST", "/api/unattended/pause")
        self.assertEqual((code, st["mode"], st["next_run"]), (200, "paused", None))
        self.assertEqual(st["pause_reason"], "Paused by you.")
        code, r = self.call("POST", "/api/unattended/run")  # Run now respects the pause (S2)
        self.assertEqual((code, r["skipped"]), (409, "paused"))
        self.assertEqual(self.call("POST", "/api/unattended/resume", {})[0], 400)  # resume asks for confirmation
        code, st = self.call("POST", "/api/unattended/resume", {"confirm": True})
        self.assertEqual((code, st["mode"]), (200, "running"))
        code, st = self.call("POST", "/api/unattended/off")
        self.assertEqual(st["mode"], "off")
        code, r = self.call("POST", "/api/unattended/run")
        self.assertEqual((code, r["skipped"]), (409, "unattended mode is off"))
        self.assertEqual(self.call("POST", "/api/unattended/nope", {})[0], 404)
        self.assertEqual(self.call("POST", "/api/unattended/jobs/99/dismiss")[0], 404)
        self.assertEqual(self.call("GET", "/api/unattended/summary?day=bad")[0], 400)
        code, r = self.call("POST", "/api/unattended/preview")  # a preview can run while off
        self.assertEqual(code, 202)
        self.ua.worker.join(30)
        self.assertEqual(self.ua.runs()[0]["trigger"], "preview")

    def test_migration_of_an_existing_install(self):
        self.stop()
        os.remove(self.dbfile)
        con = sqlite3.connect(self.dbfile)  # a database from before this change (no ua_* tables)
        con.executescript("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
                          "INSERT INTO settings VALUES ('min_score', '55');")
        con.commit()
        con.close()
        self.start()
        self.assertEqual(self.db.settings()["min_score"], 55)  # existing data untouched
        self.assertEqual(self.call("GET", "/api/unattended")[1]["mode"], "off")
        self.assertEqual(self.ua.runs(), [])
        self.stop()
        self.start()  # the migration is idempotent
        self.assertEqual(self.ua.runs(), [])


class Runs(Base):
    def test_m3_only_new_postings_at_or_above_the_minimum(self):
        sid = self.saved_search()
        self.assertEqual(len(json.loads(self.db.last_search()["result_keys"])), 3)
        old = [("Old One", "Acme"), ("Old Two", "Bolt"), ("Old Three", "Acme"), ("Old Four", "Bolt"),
               ("Old Five", "Acme"), ("Old Six", "Bolt")]
        new = [("New High", "Delta"), ("New Edge", "Echo"), ("New Low", "Fox"), ("New Near", "Golf")]
        self.net.routes["/boards/a/"] = board(*old[:2])
        self.net.routes["/boards/b/"] = board(*old[2:])
        self.saved_search()  # the search last ran with these 6 postings
        SCORES.update({t: 95 for t, _ in old})
        SCORES.update({"New High": 90, "New Edge": 80, "New Low": 50, "New Near": 79})
        self.net.routes["/boards/b/"] = board(*old[2:], *new)  # now 10 postings, 4 new, 2 of them >= 80
        self.setup_on([sid])
        run_id, why = self.go()
        self.assertIsNone(why)
        self.assertEqual(sorted(self.fake.calls), ["New Edge", "New High"])
        r = self.run_row(run_id)
        counts = json.loads(r["counts"])
        self.assertEqual((counts["found"], counts["new"], counts["candidates"], counts["submitted"]), (10, 4, 2, 2))
        self.assertEqual(r["status"], "done")
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual(st["today"], {"submitted": 2, "cap": 15})

    def test_m4_guards_duplicate_applied_hidden_cooldown_and_cap(self):
        sid_a = self.saved_search(companies=["Acme"])
        sid_b = self.saved_search()
        jobs = [("Manual Done", "Hand Co"), ("Hidden Job", "Hide Co"), ("Cool Job", "Cool Co"),
                ("Cap One", "One Co"), ("Cap Two", "Two Co"), ("Cap Three", "Three Co"), ("Acme Lead", "Acme Ltd")]
        SCORES.update({t: 90 for t, _ in jobs})
        SCORES["Cap One"], SCORES["Cap Two"], SCORES["Cap Three"] = 89, 88, 87
        self.net.routes["/boards/a/"] = board(*jobs)
        # one job the user applied to by hand; one hidden; a company applied to 3 days ago (30-day cooldown)
        key = {t: matching.norm_key({"title": t, "company": c, "location": "Remote"}) for t, c in jobs}
        self.db.app_set(key["Manual Done"], "applied_manually", title="Manual Done", company="Hand Co")
        self.db.set_job_state(key["Hidden Job"], "hidden")
        three_days = time.time() - 3 * 86400
        self.db.app_set("c" * 16, "submitted", title="Earlier", company="Cool Co Pvt Ltd", submitted_at=three_days)
        self.setup_on([sid_a, sid_b], daily_cap=2)
        run_id, _ = self.go()
        rows = self.db.q("SELECT title, outcome, reason FROM ua_jobs WHERE run_id=? ORDER BY id", (run_id,))
        got = [(r["title"], r["outcome"], r["reason"]) for r in rows]
        self.assertIn(("Manual Done", "skipped", "already applied"), got)
        self.assertIn(("Hidden Job", "skipped", "hidden by you"), got)
        self.assertIn(("Cool Job", "skipped", f"company cooldown until {unattended.day_of(three_days + 30 * 86400)}"), got)
        self.assertIn(("Acme Lead", "skipped", "duplicate"), got)  # found by both saved searches
        self.assertEqual([t for t, o, _ in got if o == "submitted"], ["Acme Lead", "Cap One"])
        self.assertEqual([(t, why) for t, o, why in got if o == "deferred"],
                         [("Cap Two", "deferred: daily cap"), ("Cap Three", "deferred: daily cap")])
        # the cap is reached: the next tick today is skipped, and logged
        _, why = self.go("schedule")
        self.assertEqual(why, "daily cap reached")
        # the next day the deferred jobs are considered again (they are not new any more)
        self.clock = time.time() + 86400
        run_id, _ = self.go()
        rows = self.db.q("SELECT title, outcome, source FROM ua_jobs WHERE run_id=? ORDER BY id", (run_id,))
        self.assertEqual([(r["title"], r["outcome"]) for r in rows], [("Cap Two", "submitted"), ("Cap Three", "submitted")])
        self.assertIn("deferred from an earlier run", rows[0]["source"])
        self.assertEqual(self.fake.calls, ["Acme Lead", "Cap One", "Cap Two", "Cap Three"])
        # a later run never applies to them twice
        self.net.routes["/boards/a/"] = board(*jobs, ("Fresh", "Cool Co"))
        SCORES["Fresh"] = 99
        self.ua._save(daily_cap=100)
        run_id, _ = self.go()
        self.assertEqual(self.fake.calls[-1], "Cap Three")
        self.assertEqual(self.db.q("SELECT reason FROM ua_jobs WHERE run_id=?", (run_id,))[0]["reason"][:24],
                         "company cooldown until 2")

    def test_m5_quiet_hours_skip_and_stop_before_submit(self):
        sid = self.saved_search()
        self.setup_on([sid], quiet_start="22:00", quiet_end="07:00")
        self.clock = at(23, 10)
        run_id, why = self.go("schedule")
        self.assertEqual(why, "quiet hours")
        r = self.run_row(run_id)
        self.assertEqual((r["status"], r["note"]), ("skipped", "quiet hours"))
        # a run that starts at 21:59: quiet hours begin before Submit, so Submit is never pressed
        self.clock = at(21, 59)
        pressed = []

        def slow_form(job):
            job["hooks"].step("Filling step 1 on the company website")
            job["hooks"].before_action()
            self.clock = at(22, 0)  # quiet hours begin while the form is being filled
            job["hooks"].before_click("submit")
            pressed.append("submit")
            return submitted(job)
        self.fake.script.update({"Night One": slow_form, "Night Two": "submit"})
        SCORES.update({"Night One": 99, "Night Two": 98})
        self.net.routes["/boards/a/"] = board(("Night One", "N1"), ("Night Two", "N2"))
        run_id, why = self.go()
        self.assertIsNone(why)
        self.assertEqual(pressed, [])
        rows = {r["title"]: r for r in self.db.q("SELECT * FROM ua_jobs WHERE run_id=?", (run_id,))}
        self.assertEqual(rows["Night One"]["outcome"], "needs_you")
        self.assertIn("Interrupted, check", rows["Night One"]["reason"])
        self.assertIn("quiet hours", rows["Night One"]["reason"])
        self.assertEqual((rows["Night Two"]["outcome"], rows["Night Two"]["reason"]), ("deferred", "deferred: quiet hours"))
        self.assertEqual(self.fake.calls, ["Night One"])
        # helpers: a window inside one day, and no quiet hours
        self.assertTrue(unattended.in_quiet(at(13, 0), "12:00", "14:00"))
        self.assertFalse(unattended.in_quiet(at(14, 0), "12:00", "14:00"))
        self.assertTrue(unattended.in_quiet(at(6, 59), "22:00", "07:00"))
        self.assertFalse(unattended.in_quiet(at(7, 0), "22:00", "07:00"))

    def test_m6_m7_outcomes_are_never_guessed(self):
        sid = self.saved_search()
        cases = {
            "Proofless": {"status": "submitted", "proof": None, "confirmation": "Thanks"},
            "Captcha Job": {"status": "needs_you", "reason": "The form has a CAPTCHA. Solve it in the browser window."},
            "Wall Job": {"status": "needs_you", "reason": "x.example needs a sign-in. Sign in once in the browser window."},
            "Account Job": {"status": "needs_you", "reason": "x.example needs a sign-in (create account)."},
            "Question Job": {"status": "needs_answers", "missing": [{"label": "Notice period *", "kind": "text"}]},
            "No Match Job": {"status": "needs_you", "reason": "Not found on portals: no Naukri or LinkedIn job matched."},
            "No Confirm Job": {"status": "needs_you", "reason": "Confirmation not found: LinkedIn showed no confirmation."},
            "Timeout Job": {"status": "failed", "reason": "Browser error: Timeout 45000ms exceeded"},
            "Agg Job": {"status": "no_company_site", "reason": "No application on the company's own website was found."},
            "Crash Job": lambda job: (_ for _ in ()).throw(RuntimeError("page crashed")),
        }
        self.fake.script.update(cases)
        SCORES.update({t: 90 for t in cases})
        self.net.routes["/boards/a/"] = board(*[(t, t.split()[0] + " Co") for t in cases])
        self.setup_on([sid])
        run_id, _ = self.go()
        rows = {r["title"]: r for r in self.db.q("SELECT * FROM ua_jobs WHERE run_id=?", (run_id,))}
        self.assertTrue(all(r["outcome"] == "needs_you" for r in rows.values()), {t: r["outcome"] for t, r in rows.items()})
        self.assertIn("proof screenshot could not be saved", rows["Proofless"]["reason"])
        self.assertIn("CAPTCHA", rows["Captcha Job"]["reason"])
        self.assertIn("sign-in", rows["Wall Job"]["reason"])
        self.assertEqual(rows["Question Job"]["reason"], "Unanswered required question: Notice period")
        self.assertIn("Not found on portals", rows["No Match Job"]["reason"])
        self.assertIn("Confirmation not found", rows["No Confirm Job"]["reason"])
        self.assertIn("Unexpected page or error: Browser error: Timeout", rows["Timeout Job"]["reason"])
        self.assertIn("page crashed", rows["Crash Job"]["reason"])
        self.assertIn("company's own website", rows["Agg Job"]["reason"])
        key = rows["Proofless"]["key"]
        self.assertEqual(self.db.app_get(key)["status"], "needs_you")  # not counted as Submitted without proof
        self.assertEqual(self.db.app_get(rows["Question Job"]["key"])["status"], "needs_answers")  # Answer once works
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual((st["needs_you"], st["today"]["submitted"]), (10, 0))
        # J5: dismiss one; answering the question resolves another through the existing flow
        code, st = self.call("POST", f"/api/unattended/jobs/{rows['Captcha Job']['id']}/dismiss")
        self.assertEqual((code, st["needs_you"]), (200, 9))
        self.assertEqual(self.db.app_get(rows["Captcha Job"]["key"])["status"], "dismissed")
        self.db.app_set(rows["Question Job"]["key"], "submitted")
        self.assertEqual(self.call("GET", "/api/unattended")[1]["needs_you"], 8)
        self.assertTrue(self.ua.dismiss(rows["Question Job"]["id"]))  # already resolved: the app status is kept
        self.assertEqual(self.db.app_get(rows["Question Job"]["key"])["status"], "submitted")

    def test_m6_submitted_with_proof_m10_run_log_and_m11_summary(self):
        sid = self.saved_search()
        SCORES.update({"Good Job": 92, "Wall Job": 91})
        self.fake.script["Wall Job"] = {"status": "needs_you", "reason": "The form has a CAPTCHA."}
        self.net.routes["/boards/a/"] = board(("Good Job", "Good Co"), ("Wall Job", "Wall Co"))
        self.setup_on([sid])
        run_id, _ = self.go()
        code, r = self.call("GET", "/api/unattended/runs")
        run = next(x for x in r["runs"] if x["id"] == run_id)
        self.assertEqual((run["trigger"], run["status"]), ("manual", "done"))
        self.assertTrue(run["started"] <= run["ended"])
        self.assertEqual(run["searches"][0]["id"], sid)
        self.assertEqual(run["source_errors"], {})
        outcomes = {j["title"]: (j["outcome"], j["reason"], j["has_proof"]) for j in run["jobs"]}
        self.assertEqual(outcomes["Good Job"], ("submitted", "Thank you for applying!", True))
        self.assertEqual(outcomes["Wall Job"][:2], ("needs_you", "The form has a CAPTCHA."))
        self.assertNotIn("proof", run["jobs"][0])  # the server path is never sent to the UI
        good = next(j for j in run["jobs"] if j["title"] == "Good Job")
        code, png = self.call("GET", f"/api/applications/{good['key']}/proof", raw=True)
        self.assertEqual((code, png[:4]), (200, b"\x89PNG"))
        proof = config.APPLICATIONS_DIR / f"{good['key']}.png"
        self.assertEqual(oct(proof.stat().st_mode & 0o777), "0o600")  # owner-only
        self.assertEqual(oct(config.APPLICATIONS_DIR.stat().st_mode & 0o777), "0o700")
        # summary: 3 / 2 / 5 with reasons
        today = unattended.day_of(time.time())
        for i in range(2):
            self.ua._job_row(run_id, {"key": f"{i:016x}", "title": f"S{i}", "company": "C", "score": 85}, "submitted", "ok", "src", time.time())
        for i in range(1):
            self.ua._job_row(run_id, {"key": f"{i + 5:016x}", "title": f"N{i}", "company": "C"}, "needs_you", "CAPTCHA", "src", time.time())
        for i in range(5):
            self.ua._job_row(run_id, {"key": f"{i + 9:016x}", "title": f"K{i}", "company": "C"}, "skipped", "hidden by you", "src", time.time())
        code, sm = self.call("GET", f"/api/unattended/summary?day={today}")
        self.assertEqual((sm["counts"]["applied"], sm["counts"]["needs_you"], sm["counts"]["skipped"]), (3, 2, 5))
        self.assertTrue(all(r["reason"] for g in ("applied", "needs_you", "skipped") for r in sm[g]))
        self.assertEqual(sm["runs"], 1)
        self.assertEqual(self.call("GET", "/api/unattended/summary")[1]["day"], today)
        code, sm = self.call("GET", "/api/unattended/summary?day=2001-01-01")
        self.assertEqual((sm["runs"], sm["counts"]["applied"]), (0, 0))  # empty state
        # O2: CSV export of the day, safe against formula injection
        self.ua._job_row(run_id, {"key": "f" * 16, "title": "=HYPERLINK(1)", "company": "C"}, "skipped", "x", "s", time.time())
        code, body = self.call("GET", f"/api/unattended/summary.csv?day={today}", raw=True)
        text = body.decode()
        self.assertTrue(text.startswith("time,outcome,title,company,score,source,reason"))
        self.assertIn("'=HYPERLINK(1)", text)
        self.assertIn("Good Job", text)
        # 500 summary rows are produced quickly (the UI renders the same data)
        for i in range(500):
            self.ua._job_row(run_id, {"key": f"{i:016x}", "title": f"Bulk {i}", "company": "C"}, "skipped", "duplicate", "s", time.time())
        t0 = time.time()
        code, sm = self.call("GET", f"/api/unattended/summary?day={today}")
        self.assertLess(time.time() - t0, 1.0)
        self.assertGreaterEqual(sm["counts"]["skipped"], 505)

    def test_m12_one_failing_source_does_not_stop_the_run_and_s1_auto_pause(self):
        sid = self.saved_search()
        SCORES.update({"Good A": 90, "Good B": 90})
        self.net.routes["/boards/a/"] = board(("Good A", "Alpha"))
        self.net.routes["/boards/b/"] = urllib.error.HTTPError("https://x", 500, "Server Error", {}, None)
        self.setup_on([sid])
        run_id, _ = self.go()
        r = self.run_row(run_id)
        self.assertEqual(r["status"], "done")
        self.assertIn("Board B", json.loads(r["source_errors"]))
        self.assertEqual(self.fake.calls, ["Good A"])
        # S1: three run-level errors in a row (every source down) pause unattended mode with a reason
        self.net.routes["/boards/a/"] = urllib.error.URLError("timed out")
        for i in range(3):
            run_id, why = self.go()
            self.assertIsNone(why)
            self.assertEqual(self.run_row(run_id)["status"], "error")
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual((st["mode"], st["auto_paused"]), ("paused", True))
        self.assertIn("Paused automatically: the last 3 runs failed", st["pause_reason"])
        self.call("POST", "/api/unattended/resume", {"confirm": True})
        self.assertEqual(self.ua.settings()["error_streak"], 0)
        # a good run resets the streak
        self.ua._save(error_streak=2)
        self.net.routes["/boards/a/"] = board(("Good A", "Alpha"))
        self.go()
        self.assertEqual(self.ua.settings()["error_streak"], 0)

    def test_run_level_exception_and_missing_search(self):
        sid = self.saved_search()
        self.setup_on([sid])
        self.ua._save(searches=[sid, "0" * 12])
        orig = self.ua._process
        self.ua._process = lambda *a: (_ for _ in ()).throw(ValueError("boom"))
        try:
            run_id, _ = self.go()
        finally:
            self.ua._process = orig
        r = self.run_row(run_id)
        self.assertEqual((r["status"], r["note"]), ("error", "The run failed: boom"))
        self.assertIn("This saved search no longer exists.", json.loads(r["source_errors"]).values())
        self.ua._save(searches=[])
        self.assertEqual(self.go()[1], "no saved searches switched on")
        self.ua._save(searches=[sid])
        self.db.posting = (lambda orig: lambda k: None if k == "gone" else orig(k))(self.db.posting)
        self.db.x("INSERT INTO ua_jobs(run_id,key,ts,day,outcome) VALUES(0,'gone',0,'x','deferred')")
        run_id, _ = self.go()  # a deferred job that is no longer stored is simply dropped
        self.assertEqual(self.run_row(run_id)["status"], "done")

    def test_m14_hosts_allow_listed_and_no_resume_text_sent_or_logged(self):
        sid = self.saved_search(skills=["Python"])
        SCORES["Leaky"] = 99
        self.net.routes["/boards/a/"] = board(("Leaky", "Leak Co"))
        self.setup_on([sid])
        self.go()
        from urllib.parse import urlparse
        self.assertTrue(self.net.requests)
        for url in self.net.requests:
            self.assertIn(urlparse(url).hostname, config.ALLOWED_HOSTS)
            self.assertNotIn("7f3a9c", url)
        logged = json.dumps([dict(r) for r in self.db.q("SELECT * FROM ua_jobs")] +
                            [dict(r) for r in self.db.q("SELECT * FROM ua_runs")])
        for line in RESUME_LINES:
            self.assertNotIn(line, logged)
        self.assertNotIn("7f3a9c", logged)


    def test_preview_first_then_real_run_and_edge_guards(self):
        sid = self.saved_search()
        SCORES.update({"Preview Job": 90, "Plain Http": 90})
        self.net.routes["/boards/a/"] = json.dumps([
            {"id": "p1", "title": "Preview Job", "company": "Pv Co"},
            {"id": "p2", "title": "Plain Http", "company": "Ph Co", "url": "http://insecure.example/job"}])
        code, st = self.call("PUT", "/api/unattended/settings", {"searches": [sid], "enable": True, "confirm": True,
                                                                  "quiet_start": "00:00", "quiet_end": "00:00"})
        self.ua.worker.join(30)
        run = self.ua.runs()[0]
        got = {j["title"]: (j["outcome"], j["reason"]) for j in run["jobs"]}
        self.assertEqual(got["Preview Job"], ("preview", "dry run: would apply"))
        self.assertEqual(got["Plain Http"], ("skipped", "no application link"))
        self.assertEqual((self.fake.calls, run["counts"]["preview"]), ([], 1))
        run_id, _ = self.go()  # the first real run applies to what the preview showed (not new any more)
        self.assertEqual(self.fake.calls, ["Preview Job"])
        # paused while searching: the run stops early and is not counted as an error
        self.ua.abort.set()
        rid = self.ua._log_run("manual", "running", "", time.time(), False)
        self.ua._run(rid, self.ua.settings(), False)
        r = self.run_row(rid)
        self.assertEqual((r["status"], r["note"]), ("done", "Stopped early: unattended mode was paused."))
        self.ua.timer = None
        self.ua.start()  # starting twice keeps one timer
        t = self.ua.timer
        self.ua.start()
        self.assertIs(self.ua.timer, t)


class Safety(Base):
    def blocking_form(self, started, actions):
        def form(job):
            started.set()
            for _ in range(200):  # a long form: one action every 50 ms
                job["hooks"].before_action()
                actions.append(time.time())
                time.sleep(0.05)
            job["hooks"].before_click("submit")
            return submitted(job)
        return form

    def test_m8_pause_stops_the_form_within_5s_and_survives_a_restart(self):
        sid = self.saved_search()
        started, actions = threading.Event(), []
        self.fake.script["Long Form"] = self.blocking_form(started, actions)
        SCORES.update({"Long Form": 95, "Next Job": 94})
        self.net.routes["/boards/a/"] = board(("Long Form", "Lf Co"), ("Next Job", "Nj Co"))
        self.setup_on([sid])
        run_id, why = self.ua.tick("manual")
        self.assertTrue(started.wait(10))
        time.sleep(0.2)
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual((st["run_active"], st["current"]["title"]), (True, "Long Form"))
        t0 = time.time()
        code, st = self.call("POST", "/api/unattended/pause")
        self.assertEqual((code, st["mode"]), (200, "paused"))
        self.ua.worker.join(10)
        last_action = max(actions)
        self.assertLess(last_action - t0, 5)
        time.sleep(0.2)
        self.assertEqual(max(actions), last_action)  # no further form action
        rows = {r["title"]: r for r in self.db.q("SELECT * FROM ua_jobs WHERE run_id=?", (run_id,))}
        self.assertEqual(rows["Long Form"]["outcome"], "needs_you")
        self.assertTrue(rows["Long Form"]["reason"].startswith("Interrupted, check"))
        self.assertEqual((rows["Next Job"]["outcome"], rows["Next Job"]["reason"]), ("deferred", "deferred: paused"))
        self.assertEqual(self.db.app_get(rows["Long Form"]["key"])["status"], "needs_you")
        self.stop()
        self.start()
        self.assertEqual(self.call("GET", "/api/unattended")[1]["mode"], "paused")  # still paused after a restart

    def test_m2_no_overlap_and_one_catch_up_after_downtime(self):
        sid = self.saved_search()
        started, actions = threading.Event(), []
        self.fake.script["Long Form"] = self.blocking_form(started, actions)
        SCORES["Long Form"] = 95
        self.net.routes["/boards/a/"] = board(("Long Form", "Lf Co"))
        self.setup_on([sid])
        self.ua.tick("manual")
        self.assertTrue(started.wait(10))
        run_id, why = self.ua.tick("schedule")
        self.assertEqual(why, "previous run active")
        self.assertEqual((self.run_row(run_id)["status"], self.run_row(run_id)["note"]), ("skipped", "previous run active"))
        self.ua.pause()
        self.ua.worker.join(10)
        self.call("POST", "/api/unattended/resume", {"confirm": True})
        # the app was closed for 3 intervals: on start exactly one catch-up run happens
        self.stop()
        self.db = DB(self.dbfile)
        self.db.set_setting("unattended", {**self.db.get_setting("unattended"), "last_tick": time.time() - 3 * 3600})
        self.db.conn.close()
        before = len(DB(self.dbfile).q("SELECT id FROM ua_runs"))
        self.db = DB(self.dbfile)
        srv = make_server(self.db, port=0)  # starts the real timer
        ua = srv.RequestHandlerClass.unattended
        srv.RequestHandlerClass.auto.assistant = self.fake
        try:
            for _ in range(100):
                if ua.worker is not None:
                    break
                time.sleep(0.05)
            ua.worker.join(30)
            time.sleep(0.3)
            runs = ua.runs()
            self.assertEqual(len(runs) - before, 1)
            self.assertEqual(runs[0]["trigger"], "catch-up")
            self.assertAlmostEqual(ua.next_run() - time.time(), 3600, delta=30)
        finally:
            ua.close()
            srv.server_close()
        self.srv.server_close()
        self.start()

    def test_m9_interrupted_attempt_becomes_needs_you_and_is_never_retried(self):
        sid = self.saved_search()
        SCORES["Crash Job"] = 99
        self.net.routes["/boards/a/"] = board(("Crash Job", "Crash Co"))
        self.setup_on([sid])
        key = matching.norm_key({"title": "Crash Job", "company": "Crash Co", "location": "Remote"})

        def killed(job):  # the process dies after the 'attempting' record, before any confirmation
            rows = self.db.q("SELECT outcome FROM ua_jobs WHERE key=?", (key,))
            self.assertEqual([r["outcome"] for r in rows], ["attempting"])
            raise SystemExit  # stands in for a kill: nothing after this point runs in the run thread
        self.fake.script["Crash Job"] = killed
        self.ua.tick("manual")
        self.ua.worker.join(10)
        self.db.x("UPDATE applications SET status='running' WHERE key=?", (key,))
        self.db.x("UPDATE ua_runs SET status='running', ended=NULL")
        self.stop()
        self.start()  # restart: the Runner, then unattended recovery
        a = self.db.app_get(key)
        self.assertEqual(a["status"], "needs_you")
        self.assertTrue(a["reason"].startswith("Interrupted, check"))
        self.assertEqual(self.db.q("SELECT outcome FROM ua_jobs WHERE key=?", (key,))[0]["outcome"], "needs_you")
        self.assertEqual(self.ua.runs()[0]["status"], "error")
        self.fake.script["Crash Job"] = "submit"
        run_id, _ = self.go()  # even when offered again, it is skipped, never applied to again
        self.ua._process(run_id, self.ua.settings(), False, [(self.db.posting(key), "test")],
                         {"skipped": 0, "deferred": 0, "submitted": 0, "needs_you": 0})
        self.assertEqual(self.fake.calls, ["Crash Job"])
        self.assertEqual(self.db.q("SELECT reason FROM ua_jobs WHERE run_id=?", (run_id,))[-1]["reason"],
                         "already in Applications (needs you)")

    def test_another_auto_apply_running_defers_and_manual_cancel_interrupts(self):
        sid = self.saved_search()
        SCORES.update({"Wait Job": 95})
        self.net.routes["/boards/a/"] = board(("Wait Job", "W Co"))
        self.setup_on([sid])
        self.db.app_set("9" * 16, "queued", title="Manual", company="M")
        run_id, _ = self.go()
        self.assertEqual(self.db.q("SELECT reason FROM ua_jobs WHERE run_id=?", (run_id,))[0]["reason"],
                         "deferred: another auto apply is running")
        self.db.app_set("9" * 16, "cancelled")

        def cancelled_by_user(job):
            self.runner.cancelled.set()  # the Applications tab's "Cancel auto apply"
            job["hooks"].before_portal()
            return submitted(job)
        self.fake.script["Wait Job"] = cancelled_by_user
        run_id, _ = self.go()
        row = self.db.q("SELECT outcome, reason FROM ua_jobs WHERE run_id=?", (run_id,))[0]
        self.assertEqual(row["outcome"], "needs_you")
        self.assertEqual(row["reason"], unattended.INTERRUPTED + " (stopped by you).")

    def test_s4_delete_all_removes_settings_logs_and_screenshots(self):
        sid = self.saved_search()
        SCORES["Proof Job"] = 95
        self.net.routes["/boards/a/"] = board(("Proof Job", "P Co"))
        self.setup_on([sid])
        self.go()
        self.assertTrue(list(config.APPLICATIONS_DIR.glob("*.png")))
        code, _ = self.call("DELETE", "/api/data")
        self.assertEqual(code, 200)
        self.assertEqual(list(config.APPLICATIONS_DIR.glob("*.png")), [])
        self.assertEqual((self.ua.runs(), self.db.get_setting("unattended")), ([], None))
        st = self.call("GET", "/api/unattended")[1]
        self.assertEqual((st["mode"], st["configured"]), ("off", False))
        config.APPLICATIONS_DIR = Path(self.tmp.name) / "missing"
        self.ua.wipe()  # no proof folder yet: nothing to remove


class Helpers(unittest.TestCase):
    def test_labels_and_formats(self):
        self.assertEqual(unattended.search_label({"keywords": "ai", "skills": ["Python"], "mode": "remote",
                                                  "countries": ["India"], "companies": ["Acme"]}),
                         '"ai" · Python · Remote · India · companies: Acme')
        self.assertEqual(unattended.search_label({"mode": "any"}), "Any work mode · any country")
        self.assertEqual(unattended.company_norm("Acme Pvt. Ltd."), unattended.company_norm("ACME"))
        self.assertEqual(unattended.minutes("07:30"), 450)
        self.assertFalse(unattended.in_quiet(time.time(), "10:00", "10:00"))
        self.assertTrue(unattended.job_url_ok("https://x"))
        self.assertFalse(unattended.job_url_ok("http://example.com"))
        s = dict(unattended.DEFAULTS, searches=["a"])
        self.assertIn("1 saved search.", unattended.summary_text(s))

    def test_cooldown_ignores_other_companies_and_zero_days(self):
        db = DB(":memory:")
        ua = unattended.Unattended(db, autoapply.Runner(db, assistant=object()), start=False)
        now = time.time()
        db.app_set("a" * 16, "submitted", company="Acme", submitted_at=now - 86400)
        db.app_set("b" * 16, "needs_you", company="Bolt")
        self.assertIsNone(ua.cooldown_until("Acme", 0, now))
        self.assertIsNone(ua.cooldown_until("Bolt", 30, now))
        self.assertIsNone(ua.cooldown_until("Acme", 1, now + 2 * 86400))
        self.assertAlmostEqual(ua.cooldown_until("Acme Inc", 30, now), now + 29 * 86400, delta=5)
        ua.close()

    def test_timer_waits_while_off_and_ticks_when_due(self):
        db = DB(":memory:")
        fake = FakeAssistant()
        ua = unattended.Unattended(db, autoapply.Runner(db, assistant=fake))
        try:
            time.sleep(0.2)
            self.assertEqual(ua.runs(), [])  # off: no ticks, no log entries
            ua._save(enabled=True, configured=True, quiet_start="00:00", quiet_end="00:00", last_tick=time.time() - 3550,
                     interval_min=60)
            for _ in range(100):  # due in ~50 s; the clock is moved on instead of waiting
                if ua.runs():
                    break
                ua.clock = lambda: time.time() + 60
                ua.wake.set()
                time.sleep(0.05)
            self.assertEqual((ua.runs()[0]["trigger"], ua.runs()[0]["note"]), ("schedule", "no saved searches switched on"))
        finally:
            ua.close()

    def test_browser_env_switches_to_the_wslg_display(self):
        orig = apply.os.path.exists
        apply.os.path.exists = lambda p: p == "/tmp/.X11-unix/X0" or orig(p)
        try:
            self.assertEqual(apply.browser_env({"DISPLAY": "127.0.0.1:0.0"})["DISPLAY"], ":0")
        finally:
            apply.os.path.exists = orig


if __name__ == "__main__":
    unittest.main()
