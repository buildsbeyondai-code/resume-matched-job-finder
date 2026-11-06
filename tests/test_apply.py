"""Apply assistant: resume_details.md parsing/saving, field mapping and planning, plus a real-browser fill of a
local practice form (skipped when Chromium cannot start)."""
import os
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import apply, details
from tests.helpers import make_pdf

SAMPLE = """# Resume details
## Personal
- Full name: A. Alex Sample
- First name: Alex
- Last name: Sample
- Email: alex.sample@example.com
- Phone: +91 9000000000
- Phone number without country code: 9000000000
- Country: India
## Links
- LinkedIn: https://www.linkedin.com/in/alexsample
## Resume
- Resume file: cv.pdf
## Saved answers
"""


class DetailsDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("RMJF_JOBSEARCH_DIR")
        os.environ["RMJF_JOBSEARCH_DIR"] = self.tmp.name
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("RMJF_JOBSEARCH_DIR", None)
        else:
            os.environ["RMJF_JOBSEARCH_DIR"] = self.old
        self.tmp.cleanup()

    def write(self, text=SAMPLE):
        (self.dir / details.FILENAME).write_text(text, encoding="utf-8")
        (self.dir / "cv.pdf").write_bytes(make_pdf())


class DetailsTests(DetailsDir):
    def test_parse_and_lookup(self):
        self.write()
        d = details.load()
        self.assertEqual(details.lookup("First Name *", d)[0], "Alex")
        self.assertEqual(details.lookup("Surname", d)[0], "Sample")
        self.assertEqual(details.lookup("Email address", d)[0], "alex.sample@example.com")
        self.assertEqual(details.lookup("LinkedIn Profile", d)[0], "https://www.linkedin.com/in/alexsample")
        self.assertEqual(details.lookup("Name", d)[0], "A. Alex Sample")
        self.assertIsNone(details.lookup("Are you legally authorized to work in India?", d)[0])
        self.assertEqual(details.resume_file(), self.dir / "cv.pdf")

    def test_save_answers_appends_and_replaces(self):
        self.write()
        n = details.save_answers([("Work authorization", "Yes"), ("Why this company?", "Mission\nfit"),
                                  ("Email", "new@example.com")])
        self.assertEqual(n, 3)
        text = (self.dir / details.FILENAME).read_text(encoding="utf-8")
        self.assertIn("- Work authorization: Yes", text)
        self.assertIn("- Why this company?: Mission fit", text)
        self.assertEqual(text.count("Email:"), 1)
        self.assertGreater(text.index("Work authorization"), text.index("## Saved answers"))
        d = details.load()
        self.assertEqual(details.lookup("Are you legally authorized to work in the country?", d)[0], "Yes")
        self.assertEqual(details.lookup("Why this company? *", d)[0], "Mission fit")
        self.assertEqual(details.lookup("Email", d)[0], "new@example.com")

    def test_ensure_creates_from_resume_without_overwriting(self):
        lines = ["Asha Verma", "Email: asha@example.com  Phone: +44 20 7946 0958",
                 "linkedin.com/in/ashaverma", "Python, SQL"]
        (self.dir / "Asha Resume.pdf").write_bytes(make_pdf(lines, pages=1))
        p = details.ensure()
        d = details.load()
        self.assertEqual(d["email"][1], "asha@example.com")
        self.assertEqual(d["first name"][1], "Asha")
        self.assertEqual(d["linkedin"][1], "https://linkedin.com/in/ashaverma")
        self.assertEqual(d["resume file"][1], "Asha Resume.pdf")
        p.write_text(SAMPLE, encoding="utf-8")
        details.ensure()
        self.assertEqual(p.read_text(encoding="utf-8"), SAMPLE)

    def test_labels_and_high_school(self):
        self.write("- School: Northfield University\n")
        d = details.load()
        self.assertEqual(details.clean_label("Resume/CV ✱ ATTACH RESUME/CV"), "Resume/CV")
        self.assertEqual(details.clean_label("Full name ✱"), "Full name")
        self.assertIsNone(details.lookup("High School Name ✱", d)[0])
        self.assertEqual(details.lookup("University", d)[0], "Northfield University")

    def test_choose(self):
        self.assertEqual(details.choose("Yes", ["Yes", "No"]), "Yes")
        self.assertEqual(details.choose("no", ["Yes, I will", "No, I will not"]), "No, I will not")
        self.assertEqual(details.choose("India", ["United States", "India", "Indonesia"]), "India")
        self.assertIsNone(details.choose("Maybe", ["Yes", "No"]))


def F(i, kind, label, required=True, options=(), name=""):
    return {"id": f"f0_{i}", "kind": kind, "label": label, "required": required, "options": list(options),
            "name": name, "filled": False}


class PlanTests(DetailsDir):
    def test_plan_fills_known_and_asks_only_required_unknown(self):
        self.write()
        fields = [F(1, "text", "First Name*"), F(2, "text", "Last Name*"), F(3, "email", "Email*"),
                  F(4, "tel", "Phone*"), F(5, "file", "Resume/CV*"), F(6, "file", "Cover Letter", False),
                  F(7, "select", "Country*", options=["United States", "India"]),
                  F(8, "radio", "Will you require visa sponsorship?*", options=["Yes", "No"]),
                  F(9, "text", "Website", False), F(10, "textarea", "Why do you want to join us?*")]
        acts, missing, manual = apply.plan(fields, details.load(), self.dir / "cv.pdf")
        got = {f["id"]: v for f, v in acts}
        self.assertEqual(got["f0_1"], "Alex")
        self.assertEqual(got["f0_4"], "+91 9000000000")
        self.assertEqual(got["f0_5"], str(self.dir / "cv.pdf"))
        self.assertEqual(got["f0_7"], "India")
        self.assertNotIn("f0_6", got)
        self.assertEqual([q["id"] for q in missing], ["f0_8", "f0_10"])
        self.assertEqual(missing[0]["canon"], "Visa sponsorship")
        self.assertEqual(manual, [])

    def test_phone_without_code_when_form_has_code_field(self):
        self.write()
        fields = [F(1, "select", "Country code", options=["+1", "+91"]), F(2, "tel", "Phone number")]
        acts, _, _ = apply.plan(fields, details.load(), None)
        self.assertEqual({f["id"]: v for f, v in acts}["f0_2"], "9000000000")

    def test_answers_saved_under_canonical_then_question(self):
        self.write()
        d = details.load()
        missing = [{"id": "a", "label": "Will you require visa sponsorship? *", "canon": "Visa sponsorship"},
                   {"id": "b", "label": "Why do you want to join us? *", "canon": None},
                   {"id": "c", "label": "Country of residence *", "canon": "Country"}]
        out = apply.answers_to_save(missing, {"a": "No", "b": "Because", "c": "India (resident)"}, d)
        self.assertEqual(out, [("Visa sponsorship", "No"), ("Why do you want to join us?", "Because"),
                               ("Country of residence", "India (resident)")])


class SignInTests(unittest.TestCase):
    def test_signin_detection(self):
        self.assertTrue(apply.is_signin("https://www.adzuna.in/authenticate/register?redirect_to=1", False))
        self.assertTrue(apply.is_signin("https://x.myworkdayjobs.com/en-US/careers/login", False))
        self.assertTrue(apply.is_signin("https://example.com/careers/job/1", True))
        self.assertFalse(apply.is_signin("https://jobs.lever.co/palantir/abc/apply", False))
        self.assertFalse(apply.is_signin("https://www.adzuna.in/details/5919137947", False))


class BrowserEnvTests(unittest.TestCase):
    def test_wslg_display_used_when_display_points_elsewhere(self):
        env = apply.browser_env({"DISPLAY": "127.0.0.1:0.0"})
        if os.path.exists("/tmp/.X11-unix/X0"):
            self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(apply.browser_env({"DISPLAY": ":1"})["DISPLAY"], ":1")


PRACTICE_FORM = """<!doctype html><html><body>
<h1>Senior Engineer</h1><a href="#" onclick="document.getElementById('app').hidden=false;return false;">Apply for this job</a>
<form id="app" hidden>
 <label for="fn">First Name *</label><input id="fn" name="first_name" required>
 <label for="ln">Last Name *</label><input id="ln" name="last_name" required>
 <label for="em">Email *</label><input id="em" type="email" required>
 <label for="ph">Phone</label><input id="ph" type="tel">
 <label for="cv">Resume/CV *</label><input id="cv" type="file" required>
 <label for="co">Country *</label><select id="co" required><option value="">Select...</option><option>United States</option><option>India</option></select>
 <fieldset><legend>Will you now or in the future require visa sponsorship? *</legend>
  <label><input type="radio" name="spon" value="y" required> Yes</label><label><input type="radio" name="spon" value="n"> No</label></fieldset>
 <label for="why">Why do you want to work here? *</label><textarea id="why" required></textarea>
 <button type="submit">Submit application</button>
</form></body></html>"""


def _chromium_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class BrowserTests(DetailsDir):
    def test_fills_practice_form_and_asks_missing_then_saves(self):
        self.write()
        site = Path(self.tmp.name) / "site"
        site.mkdir()
        (site / "job.html").write_text(PRACTICE_FORM, encoding="utf-8")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["RMJF_APPLY_HEADLESS"] = "1"
        from app import config
        config.APPLY_HEADLESS = True
        config.BROWSER_PROFILE = Path(self.tmp.name) / "profile"
        a = apply.Assistant()
        try:
            r = a.open({"url": f"http://127.0.0.1:{srv.server_address[1]}/job.html", "title": "T", "company": "C"})
            self.assertEqual(r["page"], "form")
            self.assertEqual(sorted(q["label"].rstrip(" *") for q in r["missing"]),
                             ["Why do you want to work here?", "Will you now or in the future require visa sponsorship?"])
            for want in ("First Name", "Last Name", "Email", "Phone", "Resume/CV", "Country"):
                self.assertIn(want, r["filled"])
            ans = {q["id"]: ("No" if q["options"] else "I like the mission.") for q in r["missing"]}
            r2 = a.answer(r["session"], ans)
            self.assertEqual(r2["missing"], [])
            self.assertEqual(r2["saved"], 2)
            vals = a.call(lambda: a.sessions[r["session"]].page.evaluate(
                "() => [fn.value, ln.value, em.value, co.value, document.querySelector('[name=spon]:checked')?.value,"
                " why.value, cv.files.length]"))
            self.assertEqual(vals, ["Alex", "Sample", "alex.sample@example.com", "India", "n", "I like the mission.", 1])
            text = (self.dir / details.FILENAME).read_text(encoding="utf-8")
            self.assertIn("- Visa sponsorship: No", text)
            self.assertIn("- Why do you want to work here?: I like the mission.", text)
        finally:
            try:
                a.call(lambda: a.ctx and a.ctx.close())
            except Exception:  # noqa: BLE001
                pass
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
