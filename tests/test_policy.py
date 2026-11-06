"""Application policy, consent, the submission gate, idempotent submission claims, the application state machine
and the audit chain (docs/APPLICATION_AUTHORIZATION.md, docs/STATE_MACHINE_AND_IDEMPOTENCY.md).

These are the release-blocking invariants: nothing is submitted without a valid, scope-matching consent; the same
application is never submitted twice; an uncertain Submit becomes OUTCOME_UNKNOWN and is never retried by itself."""
import itertools
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from app import apply, audit, autoapply, policy, server, states
from app.db import DB
from tests.helpers import GateHooks, authorize, seed
from tests.test_apply_branches import FakeCtx, FakeFrame, FakeLoc, FakePage

JOB = {"key": "a" * 16, "title": "Release Manager", "company": "Acme Pvt Ltd", "location": "Pune, India",
       "url": "https://jobs.example.test/1"}


def posting(db, **kw):
    j = dict(JOB, source="Test", source_id=kw.get("key", JOB["key"]), posted="2026-11-01", description="",
             countries=[], mode="remote", skills=[], **kw)
    db.upsert_postings([j])
    return j


class Policy(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")

    def test_default_is_search_only_and_blocks_the_browser(self):
        v = policy.view(self.db)
        self.assertEqual((v["mode"], v["effective_mode"], v["consent"]), (policy.SEARCH_ONLY, policy.SEARCH_ONLY, None))
        with self.assertRaises(policy.Blocked) as e:
            policy.can_start(self.db)
        self.assertIn("Search only", str(e.exception))
        with self.assertRaises(policy.Blocked):
            policy.gate(self.db, JOB, "company")

    def test_auto_submit_needs_the_exact_confirmed_summary(self):
        out, errs = policy.update(self.db, {"mode": policy.AUTO})
        self.assertEqual((out, errs[0]["field"]), (None, "confirm"))
        out, errs = policy.update(self.db, {"mode": policy.AUTO, "confirm": "0" * 64})
        self.assertEqual(errs[0]["field"], "confirm")
        prev, _ = policy.preview_summary(self.db, {"mode": policy.AUTO, "daily_cap": 3})
        out, errs = policy.update(self.db, {"mode": policy.AUTO, "daily_cap": 4, "confirm": prev["summary_hash"]})
        self.assertEqual(errs[0]["field"], "confirm")  # confirmed a different scope (3 a day, not 4)
        out, errs = policy.update(self.db, {"mode": policy.AUTO, "daily_cap": 3, "confirm": prev["summary_hash"]})
        self.assertEqual((errs, out["effective_mode"], out["consent_valid"]), ([], policy.AUTO, True))
        self.assertIn("at most 3 per day", out["consent"]["summary"])
        self.assertEqual(policy.can_start(self.db), policy.AUTO)
        # re-saving the same scope keeps the consent without a new confirmation
        out, errs = policy.update(self.db, {"mode": policy.AUTO})
        self.assertEqual((errs, out["consent_valid"]), ([], True))

    def test_scope_change_expiry_and_revocation_end_the_consent(self):
        authorize(self.db)
        p = policy.load(self.db)
        p["daily_cap"] = 50  # scope edited outside the confirmation flow
        self.db.set_setting(policy.KEY, p)
        self.assertEqual(policy.effective_mode(policy.load(self.db)), policy.PREPARE)
        authorize(self.db, daily_cap=5)
        later = time.time() + 31 * 86400
        self.assertEqual(policy.effective_mode(policy.load(self.db), later), policy.PREPARE)  # expired after 30 days
        with self.assertRaises(policy.Blocked) as e:
            policy.gate(self.db, JOB, "company", now=later)
        self.assertEqual(e.exception.status, "ready_for_review")
        self.assertIn("expired or the scope changed", str(e.exception))
        out, errs = policy.update(self.db, {"mode": policy.PREPARE})
        self.assertEqual((errs, out["consent"]), ([], None))
        kinds = [e["kind"] for e in audit.events(self.db)]
        self.assertIn("consent.revoked", kinds)
        self.assertIn("consent.granted", kinds)

    def test_validation(self):
        for body, field in (({"mode": "YOLO"}, "mode"), ({"daily_cap": 0}, "daily_cap"), ({"daily_cap": True}, "daily_cap"),
                            ({"expires_days": 400}, "expires_days"), ({"excluded_companies": "Acme"}, "excluded_companies"),
                            ({"excluded_companies": [""]}, "excluded_companies"), ({"channels": ["indeed"]}, "channels")):
            with self.subTest(body=body):
                self.assertEqual(policy.update(self.db, body)[1][0]["field"], field)
                self.assertEqual(policy.preview_summary(self.db, body)[1][0]["field"], field)
        out, errs = policy.update(self.db, {"excluded_companies": [" Acme ", "Acme", "Bolt"], "channels": ["linkedin", "company"]})
        self.assertEqual((out["excluded_companies"], out["channels"]), (["Acme", "Bolt"], ["company", "linkedin"]))

    def test_gate_rules(self):
        authorize(self.db, excluded_companies=["ACME"], channels=["company"], daily_cap=1)
        with self.assertRaises(policy.Blocked) as e:
            policy.gate(self.db, JOB, "company")
        self.assertIn("never to apply to", str(e.exception))  # "Acme Pvt Ltd" matches "ACME"
        other = dict(JOB, key="b" * 16, company="Bolt")
        with self.assertRaises(policy.Blocked) as e:
            policy.gate(self.db, other, "naukri")
        self.assertIn("Naukri is not allowed", str(e.exception))
        key = policy.gate(self.db, other, "company")
        policy.claim(self.db, key, other["key"])
        policy.move(self.db, key, policy.SUBMITTED)
        with self.assertRaises(policy.Blocked) as e:
            policy.gate(self.db, dict(JOB, key="c" * 16, company="Cog"), "company")
        self.assertIn("Daily limit reached (1", str(e.exception))
        self.assertTrue(all(ev["data"]["allowed"] is False for ev in audit.events(self.db)
                            if ev["kind"] == "submit.gate" and ev["job_key"] != "b" * 16))


class Claims(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")
        self.key = policy.submission_key(JOB)

    def test_same_job_from_another_board_has_the_same_key(self):
        repost = dict(JOB, key="z" * 16, company="ACME pvt. ltd.", title="Release  Manager", location="Pune",
                      url="https://boards.example/other")
        self.assertEqual(policy.submission_key(repost), self.key)
        self.assertNotEqual(policy.submission_key(dict(JOB, location="Delhi")), self.key)

    def test_never_twice(self):
        policy.claim(self.db, self.key, JOB["key"])
        with self.assertRaises(policy.Blocked) as e:  # still SUBMITTING
            policy.claim(self.db, self.key, JOB["key"])
        self.assertEqual(e.exception.status, "outcome_unknown")
        policy.move(self.db, self.key, policy.UNKNOWN)
        with self.assertRaises(policy.Blocked) as e:  # a repost of the same job
            policy.claim(self.db, self.key, "z" * 16)
        self.assertEqual(e.exception.status, "policy_blocked")
        self.assertIn("another posting of the same job", str(e.exception))
        policy.move(self.db, self.key, policy.NOT_SUBMITTED)  # the user checked: nothing was sent
        policy.claim(self.db, self.key, JOB["key"])
        policy.move(self.db, self.key, policy.SUBMITTED)
        with self.assertRaises(policy.Blocked) as e:
            policy.claim(self.db, self.key, JOB["key"])
        self.assertIn("already submitted", str(e.exception))
        self.assertEqual(sum(1 for ev in audit.events(self.db) if ev["kind"] == "submit.duplicate_blocked"), 3)

    def test_illegal_moves(self):
        with self.assertRaises(policy.IllegalTransition):
            policy.move(self.db, self.key, policy.SUBMITTED)  # never claimed
        policy.claim(self.db, self.key, JOB["key"])
        policy.move(self.db, self.key, policy.CONFIRMING)
        for to in (policy.SUBMITTING, policy.NOT_SUBMITTED):
            with self.assertRaises(policy.IllegalTransition):
                policy.move(self.db, self.key, to)
        policy.move(self.db, self.key, policy.SUBMITTED)
        for to in (policy.SUBMITTING, policy.UNKNOWN, policy.NOT_SUBMITTED, policy.CONFIRMING):
            with self.assertRaises(policy.IllegalTransition):
                policy.move(self.db, self.key, to)

    def test_concurrent_claims_one_winner(self):
        """20 workers race for the same application: exactly one may press Submit."""
        wins, blocked = [], []
        start = threading.Barrier(20)

        def worker(i):
            start.wait()
            try:
                wins.append(policy.claim(self.db, self.key, f"{i:016x}"))
            except policy.Blocked:
                blocked.append(i)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual((len(wins), len(blocked)), (1, 19))

    def test_claim_rolls_back_on_database_error(self):
        self.db.conn.execute("DROP TABLE submissions")
        with self.assertRaises(Exception):
            policy.claim(self.db, self.key, JOB["key"])
        self.db.conn.executescript("CREATE TABLE submissions (submission_key TEXT PRIMARY KEY, job_key TEXT NOT NULL, "
                                   "state TEXT NOT NULL, attempt_id TEXT NOT NULL, created REAL NOT NULL, "
                                   "updated REAL NOT NULL, detail TEXT);")
        policy.claim(self.db, self.key, JOB["key"])  # the connection was left usable


class StateMachine(unittest.TestCase):
    def test_invariants_over_every_pair(self):
        for old, new in itertools.product((None,) + states.STATUSES, states.STATUSES):
            allowed = old == new or new in states.MOVES.get(old, set())
            with self.subTest(old=old, new=new):
                if allowed:
                    states.check(old, new)
                else:
                    with self.assertRaises(states.IllegalTransition):
                        states.check(old, new)
        # the safety invariants, stated directly
        for new in ("queued", "running"):
            self.assertNotIn(new, states.MOVES["outcome_unknown"])
        self.assertEqual(states.MOVES["submitted"], {"outcome_unknown"})  # only a more cautious downgrade
        self.assertEqual(states.MOVES["applied_manually"], set())
        self.assertNotIn("submitted", states.MOVES[None])
        with self.assertRaises(states.IllegalTransition):
            states.check("running", "teleported")

    def test_db_enforces_it(self):
        db = DB(":memory:")
        db.app_set("k", "queued")
        db.app_set("k", "running")
        db.app_set("k", "outcome_unknown")
        with self.assertRaises(states.IllegalTransition):
            db.app_set("k", "queued")
        self.assertEqual(db.app_get("k")["status"], "outcome_unknown")


class FakeAssistant:
    """Calls the runner's hooks the way apply._auto does, then does what `script` says."""
    def __init__(self, script):
        self.script, self.closed = script, []

    def auto(self, job):
        h = job["hooks"]
        return self.script(h, job)

    def close_session(self, sid):
        self.closed.append(sid)


def result(status, **kw):
    return dict({"session": "s1", "status": status, "url": "https://x", "site": "x"}, **kw)


class Runner(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")
        posting(self.db)
        self.key = JOB["key"]

    def run_with(self, script, authorized=True):
        if authorized:
            authorize(self.db)
        r = autoapply.Runner(self.db, assistant=FakeAssistant(script))
        r.run_one(self.key)
        return r, self.db.app_get(self.key)

    def test_search_only_blocks_before_the_browser(self):
        called = []
        r, a = self.run_with(lambda h, j: called.append(1), authorized=False)
        self.assertEqual((a["status"], called), ("policy_blocked", []))

    def test_confirmed_submit_records_submitted(self):
        def script(h, j):
            h.before_submit("Submit", "company")
            h.after_submit("submitted", {"why": "confirmation shown"})
            return result("submitted", proof="/p.png", confirmation="Thanks")
        r, a = self.run_with(script)
        self.assertEqual(a["status"], "submitted")
        self.assertEqual(policy.key_for_job(self.db, self.key)[1], policy.SUBMITTED)
        ev = [e for e in audit.events(self.db, self.key) if e["kind"] == "submit.attempt"][0]["data"]
        self.assertEqual((ev["channel"], ev["company"]), ("company", "Acme Pvt Ltd"))

    def test_crash_after_click_is_outcome_unknown_and_never_retried(self):
        def script(h, j):
            h.before_submit("Submit", "company")
            raise RuntimeError("browser died after the click")
        r, a = self.run_with(script)
        self.assertEqual(a["status"], "outcome_unknown")
        self.assertFalse(r.retry(self.key))
        self.assertEqual(r.enqueue([self.key]), [])
        self.assertEqual(a["status"], self.db.app_get(self.key)["status"])
        # the user checks and records it
        self.assertFalse(r.resolve(self.key, "maybe"))
        self.assertTrue(r.resolve(self.key, "not_submitted"))
        self.assertEqual(self.db.app_get(self.key)["status"], "failed")
        self.assertEqual(policy.key_for_job(self.db, self.key)[1], policy.NOT_SUBMITTED)
        self.assertFalse(r.resolve(self.key, "submitted"))  # only an unknown outcome can be resolved
        self.assertTrue(r.retry(self.key))

    def test_cancel_or_apply_error_while_submitting_is_unknown(self):
        for exc in (autoapply.Cancelled("stop"), apply.ApplyError("gone")):
            with self.subTest(exc=type(exc).__name__):
                db = self.db = DB(":memory:")
                posting(db)

                def script(h, j, exc=exc):
                    h.before_submit("Submit", "company")
                    raise exc
                r, a = self.run_with(script)
                self.assertEqual(a["status"], "outcome_unknown")
                self.assertTrue(r.mark_applied(self.key))  # "I applied" resolves it as submitted
                self.assertEqual(self.db.app_get(self.key)["status"], "applied_manually")
                self.assertEqual(policy.key_for_job(db, self.key)[1], policy.SUBMITTED)
                self.assertFalse(r.mark_applied(self.key))

    def test_errors_before_submit_stay_retryable(self):
        for exc, status in ((autoapply.Cancelled("stop"), "cancelled"), (apply.ApplyError("x"), "failed"),
                            (RuntimeError("x"), "failed")):
            with self.subTest(status=status):
                self.db = DB(":memory:")
                posting(self.db)

                def script(h, j, exc=exc):
                    raise exc
                r, a = self.run_with(script)
                self.assertEqual(a["status"], status)

    def test_restart_turns_an_interrupted_submit_into_unknown(self):
        authorize(self.db)
        seed(self.db, self.key, "running")
        policy.claim(self.db, policy.submission_key(JOB), self.key)
        seed(self.db, "b" * 16, "running")  # interrupted before any Submit
        autoapply.Runner(self.db, assistant=FakeAssistant(None))
        self.assertEqual(self.db.app_get(self.key)["status"], "outcome_unknown")
        self.assertIn("app stopped while Submit", self.db.app_get(self.key)["reason"])
        self.assertEqual(self.db.app_get("b" * 16)["status"], "failed")

    def test_window_kept_open_only_when_the_user_acts_in_it(self):
        for status, closed in (("ready_for_review", []), ("outcome_unknown", []), ("needs_answers", ["s1"])):
            with self.subTest(status=status):
                self.db = DB(":memory:")
                posting(self.db)
                fake = FakeAssistant(lambda h, j, st=status: result(st))
                authorize(self.db)
                autoapply.Runner(self.db, assistant=fake).run_one(self.key)
                self.assertEqual(fake.closed, closed)
        fake = FakeAssistant(lambda h, j: result("policy_blocked", session=None))
        self.db = DB(":memory:")
        posting(self.db)
        authorize(self.db)
        autoapply.Runner(self.db, assistant=fake).run_one(self.key)
        self.assertEqual(fake.closed, [])

    def test_ungated_submission_is_claimed_and_flagged(self):
        authorize(self.db)
        h = autoapply._Hooks(autoapply.Runner(self.db, assistant=FakeAssistant(None)), self.key)
        h.after_submit("submitted_ungated", {"button": "Next"})
        key, st = policy.key_for_job(self.db, self.key)
        self.assertEqual(st, policy.SUBMITTED)
        h.after_submit("submitted", {})  # reported twice: no illegal move
        self.assertEqual(policy.key_for_job(self.db, self.key)[1], policy.SUBMITTED)
        self.assertIn("submit.ungated", [e["kind"] for e in audit.events(self.db)])
        # an ungated confirmation for a key already SUBMITTING (claimed) is not claimed twice
        db = self.db = DB(":memory:")
        posting(db)
        h = autoapply._Hooks(autoapply.Runner(db, assistant=FakeAssistant(None)), self.key)
        policy.claim(db, policy.submission_key(JOB), self.key)
        h.after_submit("submitted_ungated", {})
        self.assertEqual(policy.key_for_job(db, self.key)[1], policy.SUBMITTED)

    def test_gate_refusal_reaches_the_browser_worker(self):
        authorize(self.db, excluded_companies=["Acme"])
        h = autoapply._Hooks(autoapply.Runner(self.db, assistant=FakeAssistant(None)), self.key)
        with self.assertRaises(policy.Blocked):
            h.before_submit("Submit", "company")
        self.assertEqual(self.db.q("SELECT COUNT(*) AS n FROM submissions")[0]["n"], 0)  # nothing claimed


class AutoSubmitDecisions(unittest.TestCase):
    """apply.Assistant._auto around a gated click, with stubbed pages."""

    def run_auto(self, buttons, hooks, texts=("", ""), errors=(), click_fail=False, scan_after=(), max_steps=3,
                 captcha=(False,), open_raises=None):
        a = apply.Assistant()
        page = FakePage(ctx=FakeCtx())
        s = apply.Session({"title": "T", "company": "C"}, page)
        a.sessions[s.id] = s
        base = {"session": s.id, "page": "form", "missing": [], "manual": [], "filled": [], "site": "x"}
        texts, errors, scans, captchas = iter(texts), iter(errors), iter(scan_after), iter(captcha)

        def opened(job):
            if open_raises:
                raise open_raises
            return base
        a._open = opened
        a._pass = lambda sess: base
        a._captcha = lambda p: next(captchas, False)
        a._text = lambda p: next(texts, "")
        a._errors = lambda p: next(errors, [])
        a._settle = lambda p, ms=0: None
        a._proof = lambda p, k: "/proof.png"
        a._scan = lambda p: next(scans, [])
        fr = FakeFrame(loc=FakeLoc(fail={"click"} if click_fail else set()))
        a._buttons = lambda p: [(fr, b) for b in buttons]
        job = {"key": "k", "title": "T", "company": "C"}
        if hooks is not None:
            job["hooks"] = hooks
        return a._auto(job, max_steps=max_steps)

    SUBMIT = [{"id": "b1", "kind": "submit", "text": "Submit application"}]

    def test_without_a_gate_nothing_is_sent(self):
        r = self.run_auto(self.SUBMIT, hooks=None)
        self.assertEqual(r["status"], "ready_for_review")

    def test_gate_refusal_stops_before_the_click(self):
        h = GateHooks(allow=False)
        r = self.run_auto(self.SUBMIT, hooks=h)
        self.assertEqual((r["status"], h.outcomes), ("policy_blocked", []))

    def test_no_confirmation_means_unknown_and_no_second_press(self):
        h = GateHooks()
        r = self.run_auto(self.SUBMIT, hooks=h, max_steps=5)
        self.assertEqual((r["status"], h.outcomes, len(h.gates)), ("outcome_unknown", ["unknown"], 1))

    def test_validation_errors_mean_not_submitted(self):
        h = GateHooks()
        r = self.run_auto(self.SUBMIT, hooks=h, errors=(["Phone is required"], ["Phone is required"]))
        self.assertEqual((r["status"], h.outcomes), ("failed", ["not_submitted", "not_submitted"]))

    def test_ambiguous_continue_is_gated(self):
        cont = [{"id": "b1", "kind": "next", "text": "Continue"}]
        r = self.run_auto(cont, hooks=GateHooks(allow=False))
        self.assertEqual(r["status"], "policy_blocked")
        h = GateHooks()  # it opened the next page of the form: not sent
        r = self.run_auto(cont, hooks=h, scan_after=([{"kind": "text"}],), max_steps=1)
        self.assertEqual((h.outcomes, r["status"]), (["not_submitted"], "failed"))
        h = GateHooks()  # nothing visible afterwards: unknown
        r = self.run_auto(cont, hooks=h, scan_after=([],))
        self.assertEqual((h.outcomes, r["status"]), (["unknown"], "outcome_unknown"))
        h = GateHooks()  # a plain "Next" is not gated
        self.run_auto([{"id": "b1", "kind": "next", "text": "Next"}], hooks=h, max_steps=1)
        self.assertEqual(h.gates, [])

    def test_portal_apply_blocked_before_any_session(self):
        r = self.run_auto(self.SUBMIT, hooks=GateHooks(), open_raises=policy.Blocked("Ready for your review", "ready_for_review"))
        self.assertEqual((r["status"], r["session"], r["reason"]), ("ready_for_review", None, "Ready for your review"))

    def test_captcha_after_a_plain_next_needs_you(self):
        h = GateHooks()
        r = self.run_auto([{"id": "b1", "kind": "next", "text": "Next"}], hooks=h, captcha=(False, True))
        self.assertEqual((r["status"], h.outcomes), ("needs_you", []))
        h = GateHooks()
        r = self.run_auto(self.SUBMIT, hooks=h, captcha=(False, True))
        self.assertEqual((r["status"], h.outcomes), ("outcome_unknown", ["unknown"]))

    def test_after_without_hooks_is_harmless(self):
        apply.Assistant._after({"key": "k"}, "submitted", why="x")  # nothing to report to: no error

    def test_ungated_click_that_confirmed_is_reported(self):
        h = GateHooks()
        r = self.run_auto([{"id": "b1", "kind": "next", "text": "Next"}], hooks=h, texts=("", "Thank you for applying!"))
        self.assertEqual((r["status"], h.outcomes, h.gates), ("submitted", ["submitted_ungated"], []))

    def test_confirmation_records_submitted(self):
        h = GateHooks()
        r = self.run_auto(self.SUBMIT, hooks=h, texts=("", "Thank you for applying!"))
        self.assertEqual((r["status"], h.outcomes), ("submitted", ["submitted"]))


class Audit(unittest.TestCase):
    def test_chain_detects_edits_deletes_and_reorders(self):
        db = DB(":memory:")
        for i in range(5):
            audit.record(db, "test", "k", {"i": i})
        self.assertEqual(audit.verify(db)[:2], (True, 5))
        db.x("UPDATE audit SET data=? WHERE seq=3", (json.dumps({"i": 99}),))
        self.assertEqual(audit.verify(db), (False, 3, audit.events(db)[3]["hash"], 3))
        db2 = DB(":memory:")
        for i in range(3):
            audit.record(db2, "test", None, {"i": i})
        db2.x("DELETE FROM audit WHERE seq=2")
        self.assertFalse(audit.verify(db2)[0])
        self.assertEqual(audit.sha256_text("a"), "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DB(":memory:")
        posting(cls.db)
        cls.srv = server.make_server(cls.db, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-RMJF-Request": "1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_policy_flow(self):
        code, v = self.call("GET", "/api/policy")
        self.assertEqual((code, v["mode"]), (200, "SEARCH_ONLY"))
        code, r = self.call("PUT", "/api/policy", {"mode": "AUTHORIZED_AUTO_SUBMIT"})
        self.assertEqual((code, r["errors"][0]["field"]), (400, "confirm"))
        code, r = self.call("POST", "/api/policy/preview", {"mode": "AUTHORIZED_AUTO_SUBMIT", "daily_cap": 2})
        self.assertIn("at most 2 per day", r["summary"])
        self.assertEqual(self.call("POST", "/api/policy/preview", {"daily_cap": 0})[0], 400)
        code, v = self.call("PUT", "/api/policy", {"mode": "AUTHORIZED_AUTO_SUBMIT", "daily_cap": 2,
                                                   "confirm": r["summary_hash"]})
        self.assertEqual((code, v["effective_mode"]), (200, "AUTHORIZED_AUTO_SUBMIT"))
        code, a = self.call("GET", "/api/audit")
        self.assertEqual((code, a["intact"]), (200, True))
        self.assertIn("consent.granted", [e["kind"] for e in a["recent"]])

    def test_resolve_route(self):
        self.assertEqual(self.call("POST", f"/api/applications/{JOB['key']}/resolve", {"outcome": "submitted"})[0], 409)
        seed(self.db, JOB["key"], "outcome_unknown")
        code, snap = self.call("POST", f"/api/applications/{JOB['key']}/resolve", {"outcome": "submitted"})
        self.assertEqual(code, 200)
        self.assertEqual(self.db.app_get(JOB["key"])["status"], "applied_manually")


if __name__ == "__main__":
    unittest.main()


class UnattendedPolicy(unittest.TestCase):
    def test_runs_need_authorized_auto_submit_and_unknown_cannot_be_dismissed(self):
        from app import unattended
        db = DB(":memory:")
        posting(db)
        ua = unattended.Unattended(db, autoapply.Runner(db, assistant=FakeAssistant(None)), start=False)
        ua._save(enabled=True, quiet_start="00:00", quiet_end="00:00", searches=["x"], preview_done=True)
        _, reason = ua.tick("manual", dry=False)
        self.assertIn("automatic submission is not authorized", reason)
        _, reason = ua.tick("manual", dry=True)  # a dry-run preview never submits: allowed
        self.assertNotIn("not authorized", reason or "")
        seed(db, JOB["key"], "outcome_unknown")
        db.x("INSERT INTO ua_jobs(run_id, key, ts, day, outcome, reason) VALUES(1, ?, 0, 'd', 'needs_you', 'r')", (JOB["key"],))
        jid = db.q("SELECT id FROM ua_jobs")[0]["id"]
        self.assertFalse(ua.dismiss(jid))
        self.assertEqual(db.app_get(JOB["key"])["status"], "outcome_unknown")
        self.assertEqual(db.q("SELECT resolved FROM ua_jobs")[0]["resolved"], 0)
        ua.close()


class DeleteAll(unittest.TestCase):
    def test_delete_all_clears_2_0_records_and_resets_the_policy(self):
        db = DB(":memory:")
        posting(db)
        authorize(db)
        policy.claim(db, policy.submission_key(JOB), JOB["key"])
        db.x("INSERT INTO ai_usage(ts, day, cost_usd, ok, prompt_version, questions) VALUES(1, 'd', 0.1, 1, 'v', 1)")
        db.delete_all()
        for t in ("submissions", "ai_usage"):
            self.assertEqual(db.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"], 0, t)
        self.assertEqual([e["kind"] for e in audit.events(db)], ["data.deleted_all"])  # nothing personal kept
        self.assertTrue(audit.verify(db)[0])
        self.assertEqual(policy.view(db)["effective_mode"], policy.SEARCH_ONLY)
