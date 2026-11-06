"""End-to-end unattended run with the REAL apply flow: real Chromium (headless) against the local fake careers web
(tests/fake_careers.py), never a real employer or portal. A browser-level guard aborts and records any request that
leaves 127.0.0.1 / localhost.

M6: a company-site job becomes Submitted only once the confirmation page is detected and a proof screenshot exists.
M7: a CAPTCHA form and a sign-in wall are parked as Needs you with that reason; the CAPTCHA frame is never touched,
    nothing is submitted, and the sign-in form is never filled (no password, no account).
M9/M4: a second run never applies to any of these jobs again.
M14: every server-side request goes to config.ALLOWED_HOSTS; the browser reaches only the apply targets (loopback)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from app import apply, config, details
from tests import fake_careers
from tests.helpers import make_pdf
from tests.test_apply import _chromium_ok
from tests.test_autoapply_integration import DETAILS
from tests.test_unattended import SCORES, Base


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class UnattendedBrowserRun(Base):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        t = Path(cls.home.name)
        cls.saved_env = os.environ.get("RMJF_JOBSEARCH_DIR")
        os.environ["RMJF_JOBSEARCH_DIR"] = str(t)
        (t / details.FILENAME).write_text(DETAILS, encoding="utf-8")
        (t / "cv.pdf").write_bytes(make_pdf())
        cls.saved_cfg = (config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.ALLOW_LOCAL_HTTP)
        config.APPLY_HEADLESS, config.ALLOW_LOCAL_HTTP = True, True
        config.BROWSER_PROFILE = t / "profile"
        cls.careers = fake_careers.FakeCareers()
        cls.left = []

        def guard(route):
            url = route.request.url
            if (urlparse(url).hostname or "") in ("127.0.0.1", "localhost") or url.startswith(("data:", "about:", "blob:")):
                return route.continue_()
            cls.left.append(url)
            return route.abort()
        apply.ASSISTANT.call(lambda: apply.ASSISTANT._context().route("**/*", guard))

    @classmethod
    def tearDownClass(cls):
        try:
            apply.ASSISTANT.call(lambda: apply.ASSISTANT.ctx and apply.ASSISTANT.ctx.close())
        except Exception:  # noqa: BLE001
            pass
        apply.ASSISTANT.ctx = None
        cls.careers.close()
        config.APPLY_HEADLESS, config.BROWSER_PROFILE, config.ALLOW_LOCAL_HTTP = cls.saved_cfg
        if cls.saved_env is None:
            os.environ.pop("RMJF_JOBSEARCH_DIR", None)
        else:
            os.environ["RMJF_JOBSEARCH_DIR"] = cls.saved_env
        cls.home.cleanup()

    def start(self):
        super().start()
        self.runner.assistant = apply.ASSISTANT  # the real browser flow, not the scripted fake

    def test_real_flow_submitted_with_proof_walls_parked_and_never_retried(self):
        sid = self.saved_search()
        f = self.careers.url
        jobs = [("AI Engineer", "Lever Corp", f("/lever/1")), ("Architect", "Captcha Corp", f("/captcha/1")),
                ("Lead", "Signin Corp", f("/signin/1"))]
        self.net.routes["/boards/a/"] = json.dumps([{"id": c.split()[0].lower(), "title": t, "company": c, "url": u}
                                                    for t, c, u in jobs])
        SCORES.update({t: 90 for t, _c, _u in jobs})
        self.setup_on([sid])
        run_id, why = self.go()
        self.assertIsNone(why)
        run = next(r for r in self.ua.runs() if r["id"] == run_id)
        self.assertEqual(run["status"], "done", run["note"])
        got = {j["title"]: j for j in run["jobs"]}
        self.assertEqual({t: j["outcome"] for t, j in got.items()},
                         {"AI Engineer": "submitted", "Architect": "needs_you", "Lead": "needs_you"})

        # M6: Submitted only with the confirmation detected and the screenshot saved (owner-only)
        lever = got["AI Engineer"]
        self.assertIn("Thank you for applying", lever["reason"])
        self.assertTrue(lever["has_proof"])
        proof = Path(self.db.q("SELECT proof FROM ua_jobs WHERE id=?", (lever["id"],))[0]["proof"])
        self.assertEqual(proof.read_bytes()[:4], b"\x89PNG")
        self.assertLessEqual(proof.stat().st_size, 1_000_000)
        self.assertEqual(oct(proof.stat().st_mode & 0o777), "0o600")
        received = fake_careers.fields(self.careers.submissions["lever"])
        self.assertEqual((received["email"], received["resume"]), ("alex.sample@example.com", "cv.pdf"))

        # M7: walls are parked with the exact reason; nothing submitted, the CAPTCHA frame never posted to,
        # and the sign-in form never filled or submitted (no account, no password)
        self.assertIn("CAPTCHA", got["Architect"]["reason"])
        self.assertIn("sign-in", got["Lead"]["reason"])
        self.assertNotIn("captcha", self.careers.submissions)
        posts = [p for m, _h, p in self.careers.log if m == "POST"]
        self.assertNotIn("/signin", posts)
        self.assertFalse([p for p in posts if "recaptcha" in p])
        self.assertEqual(self.call("GET", "/api/unattended")[1]["needs_you"], 2)

        # M4/M9: even when all three are offered to a later run again (as deferred jobs), none is applied to again
        before = (len(self.careers.submissions), len(self.careers.log))
        for j in got.values():
            self.db.x("INSERT INTO ua_jobs(run_id,key,ts,day,outcome,reason) VALUES(?,?,0,'x','deferred','test')",
                      (run_id, j["key"]))
        run2, _ = self.go()
        again = {j["title"]: (j["outcome"], j["reason"]) for j in
                 next(r for r in self.ua.runs() if r["id"] == run2)["jobs"]}
        self.assertEqual(again["AI Engineer"], ("skipped", "already applied"))
        self.assertEqual(again["Architect"], ("skipped", "already in Applications (needs you)"))
        self.assertEqual(again["Lead"], ("skipped", "already in Applications (needs you)"))
        self.assertEqual((len(self.careers.submissions), len(self.careers.log)), before)  # the browser did nothing

        # M14: server-side requests only to allow-listed hosts; the browser never left the machine
        self.assertTrue(all(urlparse(u).hostname in config.ALLOWED_HOSTS for u in self.net.requests))
        self.assertEqual(self.left, [])


if __name__ == "__main__":
    unittest.main()
