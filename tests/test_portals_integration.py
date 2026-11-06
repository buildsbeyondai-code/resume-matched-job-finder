"""End-to-end portal fallback (Naukri, then LinkedIn) through the app's HTTP API, a real Chromium browser and the
local fake portals in tests/fake_careers.py. The real Naukri and LinkedIn sites are never contacted: a network
guard on the browser context aborts and records any request that leaves 127.0.0.1 / localhost.

Covers M1-M11 of docs/ENHANCE_SPEC.md: company site first, portal search with company + title matching, the
Naukri chat drawer, Naukri one-click Apply, LinkedIn Easy Apply (Next / Review / Submit application), "Apply on
company site" on a portal, no match on any portal, no confirmation, sign-in walls, CAPTCHAs, portals turned off,
the pause between portal applications, questions merged across jobs and answered once, and the "via" channel."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from app import apply, config, details, direct, portals, server
from app.db import DB
from tests import fake_careers
from tests.helpers import make_pdf, authorize, GateHooks
from tests.test_apply import _chromium_ok
from tests.test_autoapply_integration import DETAILS, job

PAUSE_S = 2


def pj(jid, title, company, kind, location="Pune", age="2 days ago"):
    return {"id": jid, "title": title, "company": company, "location": location, "age": age, "kind": kind}


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class PortalFallbackIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.saved_env = os.environ.get("RMJF_JOBSEARCH_DIR")
        os.environ["RMJF_JOBSEARCH_DIR"] = str(t)
        (t / details.FILENAME).write_text(DETAILS, encoding="utf-8")
        (t / "cv.pdf").write_bytes(make_pdf())
        cls.saved_cfg = (config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.APPLICATIONS_DIR, config.ALLOW_LOCAL_HTTP)
        config.APPLY_HEADLESS, config.ALLOW_LOCAL_HTTP = True, True
        config.BROWSER_PROFILE, config.APPLICATIONS_DIR = t / "profile", t / "proofs"
        cls.fake = fake = fake_careers.FakeCareers()
        cls.saved_direct = (direct.AGGREGATOR_HOSTS, direct.find_on_company_board, direct.careers_search_url)
        cls.saved_bases = {k: p["base"] for k, p in portals.PORTALS.items()}
        direct.AGGREGATOR_HOSTS = ("adzuna.", "localhost")
        direct.find_on_company_board = lambda company, title: None  # no company has a public board here
        direct.careers_search_url = lambda company, title: fake.url("/search")
        portals.PORTALS["naukri"]["base"] = fake.url("/naukri")
        portals.PORTALS["linkedin"]["base"] = fake.url("/linkedin")
        fake.portal_jobs["naukri"] = [
            pj("n1", "Release Manager", "Acme Analytics", "chat"),
            pj("n2", "Office Manager", "Acme Analytics", "chat"),         # same company, other title
            pj("n3", "Release Manager", "Other Corp", "chat"),            # same title, other company
            pj("n4", "Data Engineer", "Bolt Data", "oneclick"),
            pj("n5", "AI Engineer", "Lever Corp", "company"),
            pj("n6", "Scrum Master", "Echo Labs", "signin"),
        ]
        fake.portal_jobs["linkedin"] = [
            pj("71", "Platform Engineer", "Cobalt Systems", "easy"),
            pj("72", "QA Lead", "Delta Soft", "nocon"),
            pj("73", "Support Engineer", "Foxtrot Inc", "captcha"),
        ]
        a = lambda n: fake.url(f"/details/{n}", "localhost")  # noqa: E731 - aggregator pages with no company link
        cls.jobs = {
            "chat": job("a1" * 8, a(21), "Release Manager", "Acme Analytics"),
            "oneclick": job("a2" * 8, a(22), "Data Engineer", "Bolt Data"),
            "easy": job("a3" * 8, a(23), "Platform Engineer", "Cobalt Systems"),
            "company": job("a4" * 8, a(24), "AI Engineer", "Lever Corp"),
            "nowhere": job("a5" * 8, a(25), "Chef", "Nowhere Foods"),
            "nocon": job("a6" * 8, a(26), "QA Lead", "Delta Soft"),
            "signin": job("a7" * 8, a(27), "Scrum Master", "Echo Labs"),
            "captcha": job("a8" * 8, a(28), "Support Engineer", "Foxtrot Inc"),
            "off": job("a9" * 8, fake.url("/details/2", "localhost"), "QA Engineer", "Agency Corp"),
        }
        for j in cls.jobs.values():
            j["location"] = "Pune, India"
        cls.db = DB(":memory:")
        authorize(cls.db)  # these tests submit to the local fake portals
        cls.db.upsert_postings(list(cls.jobs.values()))
        cls.db.set_setting("portal_pause_s", PAUSE_S)  # the UI allows 5-600 s; tests use a shorter pause
        cls.srv = server.make_server(cls.db, port=0)
        cls.runner = cls.srv.RequestHandlerClass.auto
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        # network guard: the browser may only reach the local fakes
        cls.left = []

        def guard(route):
            host = urlparse(route.request.url).hostname or ""
            if host in ("127.0.0.1", "localhost") or route.request.url.startswith(("data:", "about:", "blob:")):
                return route.continue_()
            cls.left.append(route.request.url)
            return route.abort()
        apply.ASSISTANT.call(lambda: apply.ASSISTANT._context().route("**/*", guard))

    @classmethod
    def tearDownClass(cls):
        runner = getattr(cls.srv.RequestHandlerClass, "auto", None)
        if runner is not None:  # a failed test must not leave jobs queued on the shared browser thread
            runner.cancel()
        try:
            apply.ASSISTANT.call(lambda: apply.ASSISTANT.ctx and apply.ASSISTANT.ctx.close())
        except Exception:  # noqa: BLE001
            pass
        apply.ASSISTANT.ctx = None
        apply.ASSISTANT.portal_state.clear()
        cls.srv.shutdown()
        cls.fake.close()
        direct.AGGREGATOR_HOSTS, direct.find_on_company_board, direct.careers_search_url = cls.saved_direct
        for k, b in cls.saved_bases.items():
            portals.PORTALS[k]["base"] = b
        (config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.APPLICATIONS_DIR, config.ALLOW_LOCAL_HTTP) = cls.saved_cfg
        if cls.saved_env is None:
            os.environ.pop("RMJF_JOBSEARCH_DIR", None)
        else:
            os.environ["RMJF_JOBSEARCH_DIR"] = cls.saved_env
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-RMJF-Request": "1", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, timeout=400):
        end = time.time() + timeout
        while time.time() < end:
            _, snap = self.call("GET", "/api/applications")
            if not any(a["status"] in ("queued", "running") for a in snap["items"]):
                return {a["key"]: a for a in snap["items"]}, snap
            time.sleep(0.3)
        self.fail("auto apply did not finish")

    def key(self, n):
        return self.jobs[n]["key"]

    def test_portal_fallback_journey(self):
        names = ["chat", "oneclick", "easy", "company", "nowhere", "nocon", "signin", "captcha"]
        code, r = self.call("POST", "/api/autoapply", {"keys": [self.key(n) for n in names]})
        self.assertEqual((code, len(r["queued"])), (202, len(names)))
        # while the run is active the API answers fast and the browser can't be borrowed for a sign-in
        t0 = time.time()
        code, _ = self.call("POST", "/api/portals/check")
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(code, 409)
        apps, snap = self.wait()
        st = {n: apps[self.key(n)]["status"] for n in names}
        self.assertEqual(st, {"chat": "needs_answers", "oneclick": "submitted", "easy": "needs_answers",
                              "company": "submitted", "nowhere": "needs_you", "nocon": "needs_answers",
                              "signin": "needs_you", "captcha": "needs_you"})

        # M1: the company site (aggregator page) is tried before Naukri, and Naukri before LinkedIn
        log = [(h, p) for m, h, p in self.fake.log if m == "GET"]
        first_portal = next(i for i, (h, p) in enumerate(log) if p.startswith("/naukri/") and "release-manager" in p)
        self.assertLess(log.index(("localhost", "/details/21")), first_portal)
        easy_paths = [i for i, (h, p) in enumerate(log) if "platform-engineer" in p or p == "/linkedin/jobs/search/"]
        self.assertLess(log.index(("localhost", "/details/23")), easy_paths[0])

        # M3: of three Naukri rows only the one matching company AND title was opened
        opened = [p for _h, p in log if p.startswith("/naukri/job-listings-")]
        self.assertNotIn("/naukri/job-listings-n2", opened)
        self.assertNotIn("/naukri/job-listings-n3", opened)
        self.assertIn("/naukri/job-listings-n1", opened)
        a = apps[self.key("nowhere")]
        self.assertIn("Not found on portals", a["reason"])
        self.assertTrue(any(p == "/linkedin/jobs/search/" for _h, p in log))  # LinkedIn was tried after Naukri

        # M4 one-click Naukri: submitted once the confirmation appeared, with proof (M6) and channel (M11)
        a = apps[self.key("oneclick")]
        self.assertEqual((a["channel"], a["via"]), ("naukri", "Naukri"))
        self.assertIn("successfully applied", a["confirmation"])
        self.assertTrue(Path(a["proof"]).is_file())
        self.assertLess(Path(a["proof"]).stat().st_size, 1_000_000)
        self.assertIn("naukri-n4", self.fake.portal_applied)

        # M2: "Apply on company site" on Naukri is followed; the company form is submitted there
        a = apps[self.key("company")]
        self.assertEqual(a["channel"], "company")
        self.assertIn("Naukri's link to the company website", a["via"])
        lever = fake_careers.fields(self.fake.submissions["lever"])
        self.assertEqual((lever["name"], lever["resume"]), ("A. Alex Sample", "cv.pdf"))

        # M9: walls are never clicked
        self.assertIn("Naukri needs a sign-in", apps[self.key("signin")]["reason"])
        self.assertIn("CAPTCHA", apps[self.key("captcha")]["reason"])
        self.assertFalse([1 for m, _h, p in self.fake.log if m == "POST" and p == "/signin"])
        self.assertEqual(self.call("GET", "/api/portals")[1]["portals"][0]["signed_in"], False)  # Naukri's wall

        # M8: portal applications one at a time, successive starts at least the pause apart
        starts = self.runner.portal_starts
        self.assertGreaterEqual(len(starts), len(names))
        self.assertTrue(all(b - a >= PAUSE_S - 0.05 for a, b in zip(starts, starts[1:])), starts)

        # M10: the same unanswered question from Naukri's chat and LinkedIn's dialog is asked once
        qs = snap["questions"]
        self.assertEqual([q["label"].rstrip(" *") for q in qs], ["Are you willing to relocate to Pune?"])
        self.assertEqual(sorted(qs[0]["jobs"]), ["Acme Analytics", "Cobalt Systems", "Delta Soft"])
        code, r = self.call("POST", "/api/applications/answers", {"answers": {qs[0]["qid"]: "Yes"}})
        self.assertEqual(sorted(r["retried"]), sorted([self.key("chat"), self.key("easy"), self.key("nocon")]))
        apps, snap = self.wait()
        self.assertIn("- Willing to relocate: Yes", details.path().read_text(encoding="utf-8"))
        self.assertEqual(self.call("GET", "/api/portals")[1]["portals"][0]["signed_in"], True)  # Naukri job reached

        # M4 Naukri chat drawer: every answer typed / chosen, Save per question, confirmation verified
        a = apps[self.key("chat")]
        self.assertEqual((a["status"], a["channel"]), ("submitted", "naukri"))
        self.assertEqual(self.fake.portal_applied["naukri-n1"]["answers"],
                         [["How many years of experience do you have?", "16"], ["Are you willing to relocate to Pune?", "Yes"]])
        # M5 LinkedIn Easy Apply: three steps, Next / Review / Submit application in order
        a = apps[self.key("easy")]
        self.assertEqual((a["status"], a["channel"], a["via"]), ("submitted", "linkedin", "LinkedIn"))
        got = self.fake.portal_applied["linkedin-71"]
        self.assertEqual(got["pressed"], ["Next", "Review", "Submit application"])
        self.assertEqual((got["data"]["em"], got["data"]["ph"], got["data"]["cv"], got["data"]["rel"]),
                         ("alex.sample@example.com", "+91 9000000000", "cv.pdf", "Yes"))
        self.assertTrue(Path(a["proof"]).is_file())
        # M6: a Submit with no confirmation is never counted as applied, and Submit is never pressed twice
        self.assertEqual(apps[self.key("nocon")]["status"], "outcome_unknown")  # never retried by itself
        self.assertIn("Confirmation not found", apps[self.key("nocon")]["reason"])
        self.assertFalse(apps[self.key("nocon")].get("proof"))
        self.assertEqual(self.fake.portal_applied["linkedin-72"]["pressed"], ["Next", "Review", "Submit application"])
        # an applied job is never applied for again
        self.assertEqual(self.call("POST", "/api/autoapply", {"keys": [self.key("chat")]})[1]["queued"], [])

        # M9: an aggregator-only job with portals turned off is "No company website"; the aggregator never gets a POST
        self.assertEqual(self.call("PUT", "/api/settings", {"fallback_portals": []})[0], 200)
        self.call("POST", "/api/autoapply", {"keys": [self.key("off")]})
        apps, _ = self.wait()
        self.assertEqual(apps[self.key("off")]["status"], "no_company_site")
        self.assertFalse([1 for m, h, _p in self.fake.log if m == "POST" and h == "localhost"])

        # LinkedIn sign-in wall on its search page (Naukri off): parked, the run goes on
        self.call("PUT", "/api/settings", {"fallback_portals": ["linkedin"]})
        self.fake.walls["linkedin"] = "signin"
        self.call("POST", f"/api/applications/{self.key('nowhere')}/retry")
        apps, _ = self.wait()
        self.assertIn("LinkedIn needs a sign-in", apps[self.key("nowhere")]["reason"])
        self.assertEqual(self.call("GET", "/api/portals")[1]["portals"][1]["signed_in"], False)
        self.fake.walls["linkedin"] = "captcha"
        self.call("POST", f"/api/applications/{self.key('nowhere')}/retry")
        apps, _ = self.wait()
        self.assertIn("LinkedIn showed a CAPTCHA", apps[self.key("nowhere")]["reason"])
        self.fake.walls["linkedin"] = None

        # Outcome unknown is never queued again until the user records what happened
        self.assertEqual(self.call("POST", "/api/autoapply", {"keys": [self.key("nocon")]})[1]["queued"], [])
        code, _ = self.call("POST", f"/api/applications/{self.key('nocon')}/resolve", {"outcome": "not_submitted"})
        self.assertEqual((code, self.db.app_get(self.key("nocon"))["status"]), (200, "failed"))

        # cancel: queued jobs are cancelled, the running one stops before it applies; Retry works afterwards
        self.call("PUT", "/api/settings", {"portal_pause_s": 600})
        self.runner.last_portal_end = time.time()  # the next portal application must wait out the pause
        self.call("POST", "/api/autoapply", {"keys": [self.key("nocon"), self.key("signin")]})
        end = time.time() + 30
        while time.time() < end:
            a = self.call("GET", f"/api/applications/{self.key('nocon')}")[1]
            if a.get("wait_until"):
                break
            time.sleep(0.2)
        self.assertIn("rate limit", a["step"])  # the countdown is visible while waiting
        code, r = self.call("POST", "/api/autoapply/cancel")
        self.assertEqual((code, r["cancelled"]), (200, 1))
        apps, _ = self.wait()
        self.assertEqual({apps[self.key(n)]["status"] for n in ("nocon", "signin")}, {"cancelled"})
        self.db.set_setting("portal_pause_s", PAUSE_S)

        # sign-in support: opens the portal's sign-in page in the app browser; status comes from cookies
        code, r = self.call("POST", "/api/portals/linkedin/signin")
        self.assertEqual((code, r["opened"]), (200, "LinkedIn"))
        self.assertEqual(self.call("POST", "/api/portals/check")[0], 200)
        self.assertEqual(self.call("POST", "/api/portals/indeed/signin")[0], 404)

        # nothing ever left the machine
        self.assertEqual(self.left, [])


if __name__ == "__main__":
    unittest.main()
