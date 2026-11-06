"""Branch-level tests for the apply feature: every error and fallback path of the browser worker, the auto-apply
queue, resume_details.md handling, the company-board lookup and the apply HTTP routes. Browser failures that a
real site can't produce on demand (a frame vanishing, a click throwing, the browser failing to launch) use small
fake page objects; everything else runs for real."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from app import apply, autoapply, config, details, direct, server, sources
from app.db import DB
from tests.helpers import FakeNet, make_pdf


# ---------------- fake browser objects ----------------
class Boom(Exception):
    pass


class FakeLoc:
    def __init__(self, n=1, fail=(), log=None):
        self.n, self.fail, self.log = n, set(fail), log if log is not None else []

    @property
    def first(self):
        return self

    def _do(self, name, *a):
        self.log.append((name,) + a)
        if name in self.fail:
            raise Boom(name)

    def count(self):
        self._do("count")
        return self.n

    def or_(self, other):
        return self

    def __getattr__(self, name):  # click, fill, press, check, evaluate, select_option, set_input_files
        return lambda *a, **k: self._do(name, *a)


class FakeFrame:
    def __init__(self, results=None, fail=False, loc=None, role_loc=None):
        self.results, self.fail = results or {}, fail
        self.loc, self.role_loc = loc or FakeLoc(), role_loc or FakeLoc(0)

    def evaluate(self, js, *a):
        if self.fail:
            raise Boom("frame gone")
        for k, v in self.results.items():
            if k in js:
                return v(*a) if callable(v) else v
        return []

    def locator(self, sel):
        if self.fail:
            raise Boom("frame gone")
        return self.loc

    def get_by_role(self, *a, **k):
        return self.role_loc


class FakeCtx:
    def __init__(self):
        self.pages = []


class FakePage:
    def __init__(self, frames=None, url="https://example.com/job", ctx=None, fail=()):
        self.frames = frames or [FakeFrame()]
        self.url, self.fail, self.closed = url, set(fail), False
        self.context = ctx or FakeCtx()
        self.context.pages.append(self)

    def _maybe(self, name):
        if name in self.fail:
            raise Boom(name)

    def goto(self, url, **k):
        self._maybe("goto")
        self.url = url

    def wait_for_load_state(self, *a, **k):
        self._maybe("wait_for_load_state")

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, *a, **k):
        self._maybe("inner_text")
        return ""

    def screenshot(self, **k):
        self._maybe("screenshot")

    def bring_to_front(self):
        self._maybe("bring_to_front")

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


def F(i, kind, label, required=True, options=(), name=""):
    return {"id": f"f0_{i}", "kind": kind, "label": label, "required": required, "options": list(options),
            "name": name, "filled": False}


class TmpDetails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RMJF_JOBSEARCH_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


# ---------------- plan / details / direct (pure) ----------------
class PlanBranches(TmpDetails):
    def test_plan_branches(self):
        det = details.parse("- Languages: English; Tamil\n- Country: Atlantis\n- Gender: Prefer not\n"
                            "- I agree to marketing: No\n- Hometown: Pune\n")
        fields = [F(1, "file", "Cover letter *"), F(2, "file", "Portfolio", required=False),
                  F(3, "select", "Country *", options=["India", "Japan"]),
                  F(4, "select", "Gender", required=False, options=["Male", "Female"]),
                  F(5, "checkboxes", "Languages *", options=["English", "Tamil", "Hindi"]),
                  F(6, "checkboxes", "Hometown *", options=["Mumbai"]),
                  F(7, "checkboxes", "Hometown", required=False, options=["Mumbai"]),
                  F(8, "checkbox", "I agree to marketing"), F(9, "text", "Nickname", required=False)]
        acts, missing, manual = apply.plan(fields, det, None)
        self.assertEqual(manual, ["Cover letter: attach the file in the browser window"])
        self.assertEqual({f["id"]: v for f, v in acts}, {"f0_5": ["English", "Tamil"]})
        self.assertEqual([q["id"] for q in missing], ["f0_3", "f0_6"])
        self.assertEqual(missing[0]["options"], ["India", "Japan"])

    def test_answers_to_save_skips_blank(self):
        qs = [{"id": "a", "label": "Q1", "canon": None}, {"id": "b", "label": "Q2", "canon": None}]
        self.assertEqual(apply.answers_to_save(qs, {"a": "  ", "b": "x"}, {}), [("Q2", "x")])

    def test_confirmed_and_signin(self):
        self.assertTrue(apply.confirmed("", "Thank you for applying!", "u", "u"))
        self.assertFalse(apply.confirmed("Thank you for applying", "Thank you for applying", "u", "u"))
        self.assertTrue(apply.confirmed("", "", "https://x/apply", "https://x/apply/confirmation"))
        self.assertFalse(apply.confirmed("", "", "https://x/confirmation", "https://x/confirmation"))


class DetailsBranches(TmpDetails):
    def test_resume_file_fallbacks(self):
        (self.dir / details.FILENAME).write_text("- Resume file: missing.pdf\n", encoding="utf-8")
        self.assertIsNone(details.resume_file())
        (self.dir / "notes.pdf").write_bytes(b"x")
        (self.dir / "My Resume.docx").write_bytes(b"x")
        self.assertEqual(details.resume_file().name, "My Resume.docx")
        (self.dir / details.FILENAME).write_text(f"- Resume file: {self.dir / 'notes.pdf'}\n", encoding="utf-8")
        self.assertEqual(details.resume_file().name, "notes.pdf")

    def test_save_answers_edge_cases(self):
        self.assertEqual(details.save_answers([("", "x"), ("Q", "  ")]), 0)
        self.assertEqual(details.save_answers([("Notice period", "30 days")]), 1)  # file created
        self.assertIn("## Saved answers\n- Notice period: 30 days", details.path().read_text(encoding="utf-8"))
        details.path().write_text("# Resume details\n- Email: a@b.c\n- Phone:\n", encoding="utf-8")
        details.save_answers([("Phone", "123"), ("City", "Pune")])
        t = details.path().read_text(encoding="utf-8")
        self.assertIn("- Phone: 123", t)
        self.assertTrue(t.rstrip().endswith("## Saved answers\n- City: Pune"))
        details.path().write_text("## Saved answers\n- A: 1\n\n\n## Other\n- B: 2\n", encoding="utf-8")
        details.save_answers([("C", "3")])
        self.assertIn("- A: 1\n- C: 3\n\n\n## Other", details.path().read_text(encoding="utf-8"))

    def test_generate_and_ensure_edge_cases(self):
        out = details.generate("x\nNo Name Here Because This Line Is Far Too Long To Be A Name\n")
        self.assertNotIn("Full name", out)
        (self.dir / "Broken Resume.pdf").write_bytes(b"not a pdf")
        p = details.ensure("Asha Verma\nasha@example.com\n")
        self.assertIn("- Email: asha@example.com", p.read_text(encoding="utf-8"))
        with mock.patch.object(details, "resume_file", return_value=None), \
                mock.patch.object(Path, "exists", side_effect=[False, True]):  # created by another thread meanwhile
            details.ensure()

    def test_lookup_and_choose_edge_cases(self):
        self.assertEqual(details.lookup("", {}), (None, None, None))
        self.assertEqual(details.lookup("x", {}, "first_name")[2], "First name")
        self.assertIsNone(details.choose("", ["Yes"]))
        self.assertEqual(details.choose("yes", ["Yes, I am authorized", "No"]), "Yes, I am authorized")
        self.assertEqual(details.choose("I agree", ["Agree", "Disagree"]), "Agree")
        self.assertIsNone(details.choose("no", ["Maybe", "Later"]))
        self.assertEqual(details.choose("Bachelor", ["Bachelor's Degree", "Master's Degree"]), "Bachelor's Degree")
        self.assertEqual(details.choose("Bengaluru, India", ["Pune", "Bengaluru"]), "Bengaluru")
        self.assertEqual(details.choose("2 weeks", ["Notice: 2 weeks or less", "1 month"]), "Notice: 2 weeks or less")


class DirectBranches(unittest.TestCase):
    def setUp(self):
        self.orig = sources.FETCH

    def tearDown(self):
        sources.FETCH = self.orig

    def test_slug_and_title_edge_cases(self):
        self.assertEqual(direct.slugs("Pvt Ltd"), [])
        self.assertEqual(direct.slugs("Acme"), ["acme"])
        self.assertEqual(direct.slugs("IBM India"), ["ibm"])
        self.assertEqual(direct.title_match("(Remote)", "Engineer"), 0.0)
        self.assertIsNone(direct.find_on_company_board("", "Engineer"))
        self.assertIsNone(direct.find_on_company_board("Acme", ""))

    def test_every_board_parser(self):
        t = "Data Engineer"
        for board, body, url in [
            ("Greenhouse", {"jobs": [{"title": t, "absolute_url": "https://boards.greenhouse.io/acme/1"}]}, "https://boards.greenhouse.io/acme/1"),
            ("Lever", [{"text": t, "hostedUrl": "https://jobs.lever.co/acme/1"}], "https://jobs.lever.co/acme/1"),
            ("SmartRecruiters", {"content": [{"name": t, "id": "9"}]}, "https://jobs.smartrecruiters.com/acme/9"),
            ("Workable", {"jobs": [{"title": t, "shortlink": "https://apply.workable.com/j/1"}]}, "https://apply.workable.com/j/1"),
        ]:
            host = {"Greenhouse": "greenhouse", "Lever": "lever", "SmartRecruiters": "smartrecruiters",
                    "Workable": "workable"}[board]
            routes = {host: body, "": sources.SourceError("HTTP 404")}
            sources.FETCH = FakeNet(routes)
            hit = direct.find_on_company_board("Acme", t)
            self.assertEqual((hit["board"], hit["url"]), (board, url))
        sources.FETCH = FakeNet({"lever": {"error": "not a list"}, "": sources.SourceError("HTTP 404")})
        self.assertIsNone(direct.find_on_company_board("Acme", t))
        sources.FETCH = FakeNet({"greenhouse": {"jobs": [{"title": "Chef", "absolute_url": "https://g/1"}]},
                                 "": sources.SourceError("HTTP 404")})
        self.assertIsNone(direct.find_on_company_board("Acme", t))


# ---------------- browser worker with fake pages ----------------
class WorkerBranches(TmpDetails):
    def setUp(self):
        super().setUp()
        (self.dir / details.FILENAME).write_text("- First name: Alex\n- Email: j@x.io\n- Languages: English\n",
                                                 encoding="utf-8")
        self.a = apply.Assistant()

    def test_call_propagates_errors_and_context_failures(self):
        with self.assertRaises(ZeroDivisionError):
            self.a.call(lambda: 1 / 0)

        class Dead:
            @property
            def pages(self):
                raise Boom("closed")
        self.a.ctx = Dead()
        with mock.patch.dict(sys.modules, {"playwright.sync_api": None}):
            with self.assertRaisesRegex(apply.ApplyError, "not installed"):
                self.a._context()
        self.assertIsNone(self.a.ctx)

        class Live:
            pages, browser = [], None
        self.a.ctx = Live()
        self.assertIs(self.a._context(), self.a.ctx)
        Live.browser = mock.Mock(is_connected=lambda: False)  # browser window was closed by the user
        with mock.patch.dict(sys.modules, {"playwright.sync_api": None}):
            with self.assertRaisesRegex(apply.ApplyError, "not installed"):
                self.a._context()

        for msg, want in (("error while loading shared libraries: libnspr4.so", "system libraries"),
                          ("Host system is missing dependencies", "system libraries"), ("boom\nmore", "could not start: boom")):
            self.a.ctx = None
            self.a.pw = mock.Mock()
            self.a.pw.chromium.launch_persistent_context.side_effect = Exception(msg)
            with mock.patch.object(config, "BROWSER_PROFILE", self.dir / "p"):
                with self.assertRaisesRegex(apply.ApplyError, want):
                    self.a._context()

    def test_scan_settle_find_and_fill_fallbacks(self):
        page = FakePage([FakeFrame(fail=True), FakeFrame({"__rmjfN": [F(1, "text", "First name")]})], fail={"wait_for_load_state"})
        self.assertEqual(len(self.a._scan(page)), 1)
        self.a._settle(page)
        fr, loc = self.a._find(page, "f0_1")
        self.assertIsNotNone(loc)
        self.assertEqual(self.a._find(FakePage([FakeFrame(loc=FakeLoc(0))]), "x"), (None, None))
        with self.assertRaisesRegex(apply.ApplyError, "disappeared"):
            self.a._fill(FakePage([FakeFrame(fail=True)]), F(1, "text", "x"), "v")
        log = []
        page = FakePage([FakeFrame(loc=FakeLoc(fail={"check"}, log=log))])
        self.a._fill(page, F(2, "radio", "Q", options=["Yes", "No"]), "No")
        self.a._fill(page, F(3, "checkboxes", "L", options=["A", "B"]), ["A", "B"])
        self.a._fill(page, F(4, "checkbox", "Agree"), "Yes")
        self.a._fill(page, F(5, "combobox", "City"), "Pune")
        self.a._fill(page, F(6, "select", "Country"), "India")
        self.a._fill(page, F(7, "file", "CV"), "/tmp/cv.pdf")
        names = [x[0] for x in log]
        self.assertEqual(names.count("evaluate"), 4)  # every failed check fell back to a DOM click
        for n in ("click", "fill", "press", "select_option", "set_input_files"):
            self.assertIn(n, names)

    def test_find_form_paths(self):
        page = FakePage([FakeFrame({"__rmjfN": []}, role_loc=FakeLoc(fail={"count"}))])
        self.assertIs(self.a._find_form(page), page)  # role lookup failed: no button
        page = FakePage([FakeFrame({"__rmjfN": []}, role_loc=FakeLoc(1, fail={"click"}))])
        self.assertIs(self.a._find_form(page), page)  # click failed
        ctx = FakeCtx()
        popup = None

        class Opener(FakeLoc):
            def click(self, **k):
                nonlocal popup
                popup = FakePage([FakeFrame({"__rmjfN": [F(i, "text", "x") for i in range(3)]})], ctx=ctx)
        page = FakePage([FakeFrame({"__rmjfN": []}, role_loc=Opener(1))], ctx=ctx)
        self.assertIs(self.a._find_form(page), popup)  # Apply opened the form in a new tab
        page = FakePage([FakeFrame({"__rmjfN": []}, role_loc=FakeLoc(1))])
        self.assertIs(self.a._find_form(page), page)  # two clicks and still no form

    def test_pass_goto_company_site_and_sessions(self):
        fields = [F(1, "text", "First name"), F(2, "text", "Email")]
        fr = FakeFrame({"__rmjfN": lambda *a: fields, "type=password": None}, loc=FakeLoc(fail={"fill"}))
        page = FakePage([fr, FakeFrame(fail=True)], fail={"bring_to_front"})
        s = apply.Session({"title": "T", "company": "C"}, page)
        r = self.a._pass(s)
        self.assertEqual(len(r["manual"]), 2)  # both fills failed; each reported once
        page = FakePage(fail={"goto"})
        with self.assertRaisesRegex(apply.ApplyError, "did not load"):
            self.a._goto(page, "https://x")
        # aggregator: land link fails to load, second land link stays on the aggregator, no board -> search page
        page = FakePage([FakeFrame({"land": ["https://www.adzuna.in/land/ad/1", "https://www.adzuna.in/land/ad/2"]}),
                         FakeFrame(fail=True)], url="https://www.adzuna.in/details/1")
        calls = []

        def goto(p, url):
            calls.append(url)
            if url.endswith("/1"):
                raise apply.ApplyError("x")
            p.url = url
        with mock.patch.object(self.a, "_goto", goto), \
                mock.patch.object(direct, "find_on_company_board", return_value=None):
            self.assertIsNone(self.a._company_site(page, {"url": "https://www.adzuna.in/details/9", "company": "A", "title": "T"}))
        self.assertTrue(calls[-1].startswith("https://duckduckgo.com/"))
        # sessions: closed window, rescan following a new tab, rescan surviving a context error
        with self.assertRaisesRegex(apply.ApplyError, "closed"):
            self.a._session("nope")
        p1 = FakePage()
        s = apply.Session({}, p1)
        self.a.sessions[s.id] = s
        p2 = FakePage(ctx=p1.context)
        with mock.patch.object(self.a, "_pass", return_value={"ok": 1}):
            self.assertEqual(self.a._rescan(s.id), {"ok": 1})
            self.assertIs(s.page, p2)
            self.assertEqual(self.a._rescan(s.id), {"ok": 1})  # already on the newest tab
            self.assertIs(s.page, p2)
            s.page = mock.Mock(is_closed=lambda: False, context=mock.Mock(pages=property(lambda _: 1 / 0)))
            type(s.page.context).pages = mock.PropertyMock(side_effect=Boom("gone"))
            self.assertEqual(self.a._rescan(s.id), {"ok": 1})
        p1.closed = True
        s.page = p1
        with self.assertRaisesRegex(apply.ApplyError, "closed"):
            self.a._session(s.id)

    def test_eval_text_proof_and_close(self):
        page = FakePage([FakeFrame(fail=True)], fail={"inner_text", "screenshot"})
        self.assertEqual(self.a._eval_all(page, "x"), [])
        self.assertEqual(self.a._text(page), "")
        with mock.patch.object(config, "APPLICATIONS_DIR", self.dir / "proofs"):
            self.assertIsNone(self.a._proof(page, "abc"))
        p = FakePage()
        p.closed = True
        s = apply.Session({}, p)
        self.a.sessions[s.id] = s
        self.a.close_session(s.id)  # already closed: nothing to do
        self.a.close_session("missing")
        with mock.patch.object(self.a, "_rescan", return_value={"r": 1}):
            self.assertEqual(self.a.rescan("x"), {"r": 1})


class AutoStepBranches(TmpDetails):
    """apply.Assistant._auto decisions, with the page-level helpers stubbed."""

    def run_auto(self, opened, passes=(), buttons=(), captcha=(False,), texts=("", ""), click_fail=False,
                 errors=(), close_after_click=False, max_steps=8):
        a = apply.Assistant()
        ctx = FakeCtx()
        page = FakePage(ctx=ctx)
        s = apply.Session({"title": "T", "company": "C"}, page)
        a.sessions[s.id] = s
        opened = dict({"session": s.id, "page": "form", "missing": [], "manual": [], "filled": [], "site": "x"}, **opened)
        passes, captcha, texts, errors = iter(passes), iter(captcha), iter(texts), iter(errors)
        a._open = lambda job: opened
        a._pass = lambda sess: next(passes)
        a._captcha = lambda p: next(captcha, False)
        a._text = lambda p: next(texts, "")
        a._errors = lambda p: next(errors, [])
        a._settle = lambda p, ms=0: None
        a._proof = lambda p, k: "/proof.png"
        loc = FakeLoc(fail={"click"} if click_fail else set())
        fr = FakeFrame(loc=loc)
        a._buttons = lambda p: [(fr, b) for b in buttons]
        if close_after_click:
            def click(**k):
                page.closed = True
                FakePage(ctx=ctx)
            loc.click = click
        return a._auto({"key": "k", "title": "T", "company": "C"}, max_steps=max_steps)

    def test_auto_outcomes(self):
        self.assertEqual(self.run_auto({"manual": ["Cover letter: attach"]})["status"], "needs_you")
        r = self.run_auto({"page": "no-form"})
        self.assertIn("No application form", r["reason"])
        r = self.run_auto({"page": "sign-in"})
        self.assertIn("sign-in", r["reason"])
        r = self.run_auto({}, captcha=(True,))
        self.assertIn("CAPTCHA", r["reason"])
        r = self.run_auto({}, buttons=[])
        self.assertIn("No Submit button", r["reason"])
        r = self.run_auto({}, buttons=[{"id": "b1", "kind": "submit", "text": "Submit"}], click_fail=True)
        self.assertIn("could not be clicked", r["reason"])
        r = self.run_auto({}, buttons=[{"id": "b1", "kind": "submit", "text": "Submit"}], captcha=(False, True))
        self.assertIn("after Submit", r["reason"])
        r = self.run_auto({}, buttons=[{"id": "b1", "kind": "submit", "text": "Submit"}], close_after_click=True,
                          texts=("", "Thank you for applying!"))
        self.assertEqual((r["status"], r["proof"]), ("submitted", "/proof.png"))
        base = {"page": "form", "missing": [], "manual": [], "filled": [], "site": "x"}
        r = self.run_auto({}, passes=[base] * 3, buttons=[{"id": "b1", "kind": "next", "text": "Next"}], max_steps=3)
        self.assertIn("did not reach a confirmation", r["reason"])
        r = self.run_auto({}, passes=[base] * 3, buttons=[{"id": "b1", "kind": "submit", "text": "Submit"}],
                          errors=(["Email is invalid"], ["Email is invalid"]))
        self.assertIn("Email is invalid", r["reason"])


# ---------------- auto-apply queue with a fake assistant ----------------
class FakeAssistant:
    def __init__(self, results):
        self.results, self.closed = results, []

    def auto(self, job):
        r = self.results[job["key"]]
        if isinstance(r, BaseException):
            raise r
        return dict(r, session="s-" + job["key"])

    def close_session(self, sid):
        self.closed.append(sid)
        raise Boom("window already gone")


class RunnerBranches(TmpDetails):
    def job(self, k):
        return {"key": k, "title": "T" + k, "company": "C" + k, "url": "https://x/" + k}

    def test_runner_paths(self):
        db = DB(":memory:")
        db.upsert_postings([self.job(k) for k in ("a" * 16, "b" * 16, "c" * 16)])
        db.app_set("c" * 16, "running")
        db.app_set("f" * 16, "submitted")
        fa = FakeAssistant({"a" * 16: apply.ApplyError("The job page did not load"), "b" * 16: Boom("")})
        r = autoapply.Runner(db, fa)
        self.assertEqual(db.app_get("c" * 16)["status"], "failed")  # interrupted by a restart
        self.assertIn("Interrupted", db.app_get("c" * 16)["reason"])
        self.assertEqual(r.run_one("d" * 16)["status"], "failed")  # job no longer stored
        self.assertEqual(r.run_one("a" * 16)["reason"], "The job page did not load")
        self.assertEqual(r.run_one("b" * 16)["reason"], "Browser error: unknown")
        fa.results["c" * 16] = {"status": "needs_answers", "missing": [
            {"id": "f1", "label": "Notice period *", "kind": "text", "options": [], "canon": "Notice period"}]}
        self.assertEqual(r.run_one("c" * 16)["status"], "needs_answers")
        self.assertEqual(fa.closed, ["s-" + "c" * 16])  # closing errors are ignored
        # the same question from a second job: options merged from whichever job has them
        db.upsert_postings([self.job("e" * 16)])
        db.app_set("e" * 16, "needs_answers", company="C" + "e" * 16, missing=[{"id": "f9", "label": "Notice period", "kind": "select",
                                                         "options": ["30 days", "60 days"], "canon": "Notice period",
                                                         "qid": autoapply.qid("Notice period")}])
        db.x("UPDATE applications SET updated=updated+1000 WHERE key=?", ("c" * 16,))  # c (no options) first
        qs = r.questions()
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["options"], ["30 days", "60 days"])
        self.assertEqual(sorted(qs[0]["jobs"]), ["C" + "c" * 16, "C" + "e" * 16])
        # a kick while the worker is alive does not start a second one
        r.thread = mock.Mock(is_alive=lambda: True)
        r._kick()
        self.assertFalse(r.retry("z" * 16))


# ---------------- HTTP routes of the interactive Apply and error mapping ----------------
class RouteBranches(TmpDetails):
    def setUp(self):
        super().setUp()
        (self.dir / details.FILENAME).write_text("- First name: Alex\n- Resume file: cv.pdf\n", encoding="utf-8")
        (self.dir / "cv.pdf").write_bytes(make_pdf())
        self.db = DB(":memory:")
        self.db.upsert_postings([{"key": "a" * 16, "title": "T", "company": "C", "url": "https://x.example/job"},
                                 {"key": "b" * 16, "title": "T", "company": "C", "url": "ftp://x"}])
        self.srv = server.make_server(self.db, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        super().tearDown()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-RMJF-Request": "1", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        code, d = self.call("GET", "/api/apply/details")
        self.assertEqual((code, d["exists"], d["resume_file"]), (200, True, "cv.pdf"))
        self.assertEqual(self.call("POST", f"/api/jobs/{'c' * 16}/apply")[0], 404)
        self.assertEqual(self.call("POST", f"/api/jobs/{'b' * 16}/apply")[0], 400)
        A = apply.ASSISTANT
        with mock.patch.object(A, "open", return_value={"session": "s"}):
            self.assertEqual(self.call("POST", f"/api/jobs/{'a' * 16}/apply"), (200, {"session": "s"}))
        with mock.patch.object(A, "rescan", side_effect=apply.ApplyError("That application window was closed.")):
            self.assertEqual(self.call("POST", "/api/apply/abcdefabcdef/rescan")[0], 422)
        with mock.patch.object(A, "answer", side_effect=TimeoutError()):
            self.assertEqual(self.call("POST", "/api/apply/abcdefabcdef/answers", {"answers": {"f1": "x"}})[0], 504)
        with mock.patch.object(A, "answer", side_effect=RuntimeError("kaboom")):
            code, d = self.call("POST", "/api/apply/abcdefabcdef/answers", {"answers": {"f1": "x"}})
            self.assertEqual((code, d["error"]), (500, "The browser step failed: kaboom"))
        with mock.patch.object(A, "answer", side_effect=RuntimeError("")):
            self.assertEqual(self.call("POST", "/api/apply/abcdefabcdef/answers", {"answers": {}})[1]["error"],
                             "The browser step failed.")
        self.assertEqual(self.call("POST", "/api/apply/abcdefabcdef/answers", {"answers": ["x"]})[0], 400)
        # proof route: a recorded path outside the proofs folder is never served
        self.db.app_set("a" * 16, "submitted", proof="/etc/passwd")
        self.assertEqual(self.call("GET", f"/api/applications/{'a' * 16}/proof")[0], 404)
        self.assertFalse(server.job_url_ok("http://evil.example/x"))
        with mock.patch.object(config, "ALLOW_LOCAL_HTTP", True):
            self.assertTrue(server.job_url_ok("http://localhost:8000/x"))
            self.assertFalse(server.job_url_ok("http://localhost.evil.com/x"))


if __name__ == "__main__":
    unittest.main()
