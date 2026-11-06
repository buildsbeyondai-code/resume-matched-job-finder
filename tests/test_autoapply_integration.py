"""End-to-end auto apply through the app's HTTP API, a real Chromium browser and a local fake careers web.

Covers: direct company forms (single page, Apply-link, multi-step), screening questions answered once and
reused, file upload, aggregator redirect to the company site, company-board lookup, aggregator-only jobs
(never applied for there), CAPTCHA, sign-in walls, forms rejected by the site, forms with no Submit button,
retry / mark-applied, proof screenshots and what the employer actually received."""
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app import apply, config, details, direct, server
from app.db import DB
from tests import fake_careers
from tests.helpers import make_pdf
from tests.test_apply import _chromium_ok

DETAILS = """# Resume details
## Personal
- Full name: A. Alex Sample
- First name: Alex
- Last name: Sample
- Email: alex.sample@example.com
- Phone: +91 9000000000
- City: Pune
- Country: India
## Links
- LinkedIn: https://www.linkedin.com/in/alexsample
## Current role
- Current company: Contoso
- Years of experience: 16
## Resume
- Resume file: cv.pdf
## Saved answers
"""


def job(key, url, title, company):
    return {"key": key, "source": "Test", "source_id": key, "title": title, "company": company, "location": "Remote",
            "url": url, "posted": "2026-11-01", "description": "", "countries": [], "mode": "remote", "skills": []}


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class AutoApplyIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.saved_env = {k: os.environ.get(k) for k in ("RMJF_JOBSEARCH_DIR",)}
        os.environ["RMJF_JOBSEARCH_DIR"] = str(t)
        (t / details.FILENAME).write_text(DETAILS, encoding="utf-8")
        (t / "cv.pdf").write_bytes(make_pdf())
        cls.saved_cfg = (config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.APPLICATIONS_DIR, config.ALLOW_LOCAL_HTTP)
        config.APPLY_HEADLESS, config.ALLOW_LOCAL_HTTP = True, True
        config.BROWSER_PROFILE, config.APPLICATIONS_DIR = t / "profile", t / "proofs"
        cls.fake = fake_careers.FakeCareers()
        cls.saved_direct = (direct.AGGREGATOR_HOSTS, direct.find_on_company_board, direct.careers_search_url)
        direct.AGGREGATOR_HOSTS = ("adzuna.", "localhost")
        boards = {"Lever Corp": cls.fake.url("/lever/1")}
        direct.find_on_company_board = lambda company, title: (
            {"url": boards[company], "board": "Lever", "title": title, "score": 1.0} if company in boards else None)
        direct.careers_search_url = lambda company, title: cls.fake.url("/search")
        cls.db = DB(":memory:")
        f = cls.fake.url
        cls.jobs = {
            "gh": job("a" * 16, f("/gh/1"), "Senior AI Program Manager", "Greenhouse Corp"),
            "lever": job("b" * 16, f("/lever/1"), "AI Engineer", "Lever Corp"),
            "multi": job("c" * 16, f("/multi/1"), "Release Manager", "Workday Corp"),
            "captcha": job("d" * 16, f("/captcha/1"), "Architect", "Captcha Corp"),
            "signin": job("e" * 16, f("/signin/1"), "Lead", "Signin Corp"),
            "reject": job("f" * 16, f("/reject/1"), "Manager", "Closed Corp"),
            "nobutton": job("1" * 16, f("/nobutton/1"), "Analyst", "Odd Corp"),
            "agg_land": job("2" * 16, f("/details/1", "localhost"), "Senior AI Program Manager", "Greenhouse Corp"),
            "agg_only": job("3" * 16, f("/details/2", "localhost"), "QA Engineer", "Agency Corp"),
            "agg_board": job("4" * 16, f("/details/3", "localhost"), "AI Engineer", "Lever Corp"),
            "http_ext": job("5" * 16, "http://example.com/job", "Insecure", "Nope"),
        }
        cls.db.upsert_postings(list(cls.jobs.values()))
        cls.srv = server.make_server(cls.db, port=0)
        import threading
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        try:
            apply.ASSISTANT.call(lambda: apply.ASSISTANT.ctx and apply.ASSISTANT.ctx.close())
        except Exception:  # noqa: BLE001
            pass
        apply.ASSISTANT.ctx = None
        cls.srv.shutdown()
        cls.fake.close()
        direct.AGGREGATOR_HOSTS, direct.find_on_company_board, direct.careers_search_url = cls.saved_direct
        (config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.APPLICATIONS_DIR, config.ALLOW_LOCAL_HTTP) = cls.saved_cfg
        for k, v in cls.saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.tmp.cleanup()

    # --- helpers ---
    def call(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, timeout=400):
        end = time.time() + timeout
        while time.time() < end:
            _, snap = self.call("GET", "/api/applications")
            if not any(a["status"] in ("queued", "running") for a in snap["items"]):
                return {a["key"]: a for a in snap["items"]}, snap
            time.sleep(0.5)
        self.fail("auto apply did not finish")

    def key(self, name):
        return self.jobs[name]["key"]

    # --- the whole journey in order (one shared browser, like the real app) ---
    def test_auto_apply_journey(self):
        # input validation
        self.assertEqual(self.call("POST", "/api/autoapply", {"keys": "x"})[0], 400)
        self.assertEqual(self.call("POST", "/api/autoapply", {"keys": ["../etc"]})[0], 400)
        self.assertEqual(self.call("POST", "/api/applications/answers", {"answers": []})[0], 400)
        self.assertEqual(self.call("GET", "/api/applications/" + "9" * 16)[0], 404)
        self.assertEqual(self.call("POST", f"/api/applications/{'9' * 16}/retry")[0], 409)

        # 1) batch: every kind of company site at once (the insecure external link is filtered out)
        names = ["gh", "lever", "multi", "captcha", "signin", "reject", "nobutton", "agg_only", "agg_board", "http_ext"]
        code, r = self.call("POST", "/api/autoapply", {"keys": [self.key(n) for n in names]})
        self.assertEqual(code, 202)
        self.assertNotIn(self.key("http_ext"), r["queued"])
        apps, snap = self.wait()
        st = {n: apps[self.key(n)]["status"] for n in names if self.key(n) in apps}
        self.assertEqual(st, {"gh": "needs_answers", "lever": "submitted", "multi": "needs_answers",
                              "captcha": "needs_you", "signin": "needs_you", "reject": "failed",
                              "nobutton": "needs_you", "agg_only": "no_company_site", "agg_board": "submitted"})

        # what Lever Corp received: full name, email, years, current company and the resume file
        lever = fake_careers.fields(self.fake.submissions["lever"])
        self.assertEqual((lever["name"], lever["email"], lever["years"], lever["org"], lever["resume"]),
                         ("A. Alex Sample", "alex.sample@example.com", "16", "Contoso", "cv.pdf"))
        self.assertIn("Lever", apps[self.key("agg_board")]["via"])
        self.assertIn("CAPTCHA", apps[self.key("captcha")]["reason"])
        self.assertIn("sign-in", apps[self.key("signin")]["reason"])
        self.assertIn("closed", apps[self.key("reject")]["reason"])
        self.assertIn("Submit button", apps[self.key("nobutton")]["reason"])
        self.assertIn("aggregator", apps[self.key("agg_only")]["reason"])
        self.assertNotIn("agency", self.fake.submissions)  # never applied through the aggregator

        # proof screenshot for a submitted application
        code, png = self.call("GET", f"/api/applications/{self.key('lever')}/proof", raw=True)
        self.assertEqual((code, png[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.call("GET", f"/api/applications/{self.key('gh')}/proof")[0], 404)
        self.assertEqual(self.call("GET", f"/api/applications/{self.key('lever')}")[1]["status"], "submitted")

        # 2) questions are merged across jobs and asked once
        qs = {q["label"].rstrip(" *"): q for q in snap["questions"]}
        self.assertEqual(set(qs), {"Are you legally authorized to work in the country for which you are applying?",
                                   "Will you now or in the future require visa sponsorship?",
                                   "Why do you want to work here?", "I agree to the privacy policy", "Notice period"})
        answers = {qs["Are you legally authorized to work in the country for which you are applying?"]["qid"]: "Yes",
                   qs["Will you now or in the future require visa sponsorship?"]["qid"]: "No",
                   qs["Why do you want to work here?"]["qid"]: "Your AI programme matches my experience.",
                   qs["I agree to the privacy policy"]["qid"]: "Yes", qs["Notice period"]["qid"]: "30 days"}
        code, r = self.call("POST", "/api/applications/answers", {"answers": answers})
        self.assertEqual((code, r["saved"], sorted(r["retried"])), (200, 5, sorted([self.key("gh"), self.key("multi")])))
        apps, snap = self.wait()
        self.assertEqual(apps[self.key("gh")]["status"], "submitted")
        gh = fake_careers.fields(self.fake.submissions["gh"])
        self.assertEqual({k: gh.get(k) for k in ("first_name", "last_name", "email", "phone", "linkedin", "country",
                                                  "auth", "spon", "why", "privacy", "resume")},
                         {"first_name": "Alex", "last_name": "Sample", "email": "alex.sample@example.com",
                          "phone": "+91 9000000000", "linkedin": "https://www.linkedin.com/in/alexsample",
                          "country": "India", "auth": "yes", "spon": "no",
                          "why": "Your AI programme matches my experience.", "privacy": "on", "resume": "cv.pdf"})
        self.assertNotIn("cover", {k for k, v in gh.items() if v})  # optional cover letter left empty

        # the multi-step form reveals one more question on its last page
        self.assertEqual(apps[self.key("multi")]["status"], "needs_answers")
        self.assertEqual([q["label"].rstrip(" *") for q in snap["questions"]], ["I confirm the information is correct"])
        self.call("POST", "/api/applications/answers", {"answers": {snap["questions"][0]["qid"]: "Yes"}})
        apps, _ = self.wait()
        self.assertEqual(apps[self.key("multi")]["status"], "submitted")
        steps = self.fake.submissions["multi_steps"]
        self.assertEqual((steps[-2]["fn"], steps[-2]["em"], steps[-1]["np"], steps[-1]["ct"]),
                         (["Alex"], ["alex.sample@example.com"], ["30 days"], ["Pune"]))

        # answers were saved to resume_details.md for next time
        text = details.path().read_text(encoding="utf-8")
        for line in ("- Work authorization: Yes", "- Visa sponsorship: No", "- Notice period: 30 days",
                     "- Why do you want to work here?: Your AI programme matches my experience."):
            self.assertIn(line, text)

        # 3) an aggregator job that links to the company site is applied for there, with no new questions
        self.fake.submissions.pop("gh")
        self.call("POST", "/api/autoapply", {"keys": [self.key("agg_land")]})
        apps, snap = self.wait()
        a = apps[self.key("agg_land")]
        self.assertEqual((a["status"], a["site"]), ("submitted", "127.0.0.1"))
        self.assertIn("Adzuna's link", a["via"])
        self.assertEqual(fake_careers.fields(self.fake.submissions["gh"])["first_name"], "Alex")

        # 4) already submitted jobs are never applied for twice
        code, r = self.call("POST", "/api/autoapply", {"keys": [self.key("lever"), self.key("agg_land")]})
        self.assertEqual(r["queued"], [])
        self.assertEqual(self.call("POST", f"/api/applications/{self.key('lever')}/retry")[0], 409)

        # 5) retry and mark applied
        code, _ = self.call("POST", f"/api/applications/{self.key('captcha')}/retry")
        self.assertEqual(code, 200)
        apps, _ = self.wait()
        self.assertEqual(apps[self.key("captcha")]["status"], "needs_you")
        code, snap = self.call("POST", f"/api/applications/{self.key('captcha')}/mark")
        self.assertEqual(code, 200)
        self.assertEqual({a["key"]: a["status"] for a in snap["items"]}[self.key("captcha")], "applied_manually")
        self.assertEqual(self.call("POST", f"/api/applications/{'9' * 16}/mark")[0], 409)
        self.assertEqual(snap["counts"]["submitted"], 5)
        self.assertEqual(self.db.job_states()[self.key("lever")], "saved")


if __name__ == "__main__":
    unittest.main()
