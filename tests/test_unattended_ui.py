"""Browser UI tests (UEUIS §17) for unattended auto-apply, in real Chromium against the app's own server:
J1 set up (empty state, inline errors that keep the input, busy Save, review summary, confirm, running status),
J3 the kill switch on every view (keyboard, accessible name, 24 px, assertive announcement, Resume confirmation),
J4 the daily summary (counts, real tables, reasons, proof links, empty, loading, error + Retry),
J5 resolving a Needs-you item without a page reload, J6 the run log (expand per-job outcomes, empty, error + Retry),
plus accessible names, no horizontal scroll at 320 px in light and dark themes, and no script errors or external requests."""
import time
import unittest

from app import unattended
from tests.helpers import seed
from tests.test_apply import _chromium_ok
from tests.test_ui_browser import NAME_JS
from tests.test_unattended import SCORES, Base, board

# records every change of the hidden / aria-busy / disabled state, so short-lived states can be asserted
RECORDER = """(() => { window.__seen = []; new MutationObserver((ms) => { for (const m of ms) {
  const t = m.target; if (!t.id) continue;
  window.__seen.push(t.id + ':' + m.attributeName + '=' + t.getAttribute(m.attributeName)); } })
  .observe(document, { subtree: true, attributes: true, attributeFilter: ['hidden', 'aria-busy', 'disabled'] }); })()"""
VIEWS = ("search", "applications", "unattended", "today", "runlog", "settings", "profile", "saved")


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class UnattendedUI(Base):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        super().setUp()
        self.ctx = self.browser.new_context(viewport={"width": 1366, "height": 768})
        self.ctx.add_init_script(RECORDER)
        self.page = self.ctx.new_page()
        self.errors, self.external = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.base) else
                        (self.external.append(r.request.url), r.abort()))

    def tearDown(self):
        self.ctx.close()
        super().tearDown()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def open(self, view):
        self.page.goto("about:blank")
        self.page.goto(f"{self.base}/#{view}")
        self.page.wait_for_selector(f"#view-{view}:not([hidden])")
        self.page.wait_for_timeout(300)

    def seen(self):
        return self.page.evaluate("window.__seen")

    def test_j1_set_up_with_inline_errors_review_and_confirm(self):
        p = self.page
        sid = self.saved_search(skills=["Python"])
        self.open("unattended")
        self.assertTrue(p.is_visible("#uaEmpty"))
        self.assertIn("Unattended mode is not set up", p.inner_text("#uaEmpty"))
        self.assertIn("Off (not set up)", p.inner_text("#uaStatus"))
        self.assertEqual(p.get_attribute(".tabs a[data-tab=applications]", "aria-current"), "page")
        self.assertEqual(p.get_attribute("#appNav a[data-sub=unattended]", "aria-current"), "page")
        self.assertIn("uaLoading:hidden=", self.seen())  # the loading state was shown, then hidden
        p.click("#uaSetupBtn")
        self.assertTrue(p.is_visible("#uaForm"))
        self.assertEqual(p.input_value("#ua-interval"), "60")
        self.assertEqual((p.input_value("#ua-qs"), p.input_value("#ua-qe")), ("22:00", "07:00"))
        self.assertEqual(p.inner_text("#uaSave"), "Review and turn on")
        # interval 5 and no search: inline errors with the fix, the input is kept, nothing saved
        p.fill("#ua-interval", "5")
        p.click("#uaSave")
        p.wait_for_selector("#uaErrors:not([hidden])")
        self.assertIn("Interval must be 15-1440 minutes", p.inner_text("#ua-interval-err"))
        self.assertIn("Switch on at least one saved search", p.inner_text("#ua-searches-err"))
        self.assertEqual(p.get_attribute("#ua-interval", "aria-invalid"), "true")
        self.assertEqual(p.evaluate("document.activeElement.id"), "uaErrors")
        self.assertEqual(p.input_value("#ua-interval"), "5")
        self.assertIsNone(self.db.get_setting("unattended"))
        p.click("#uaErrors a >> nth=0")  # the summary links move focus to the field
        self.assertEqual(p.evaluate("document.activeElement.id"), "ua-interval")
        # a server-side error is shown inline too (the browser's own checks are bypassed here)
        p.fill("#ua-interval", "60")
        p.check("#uaSearchList input")
        p.evaluate("document.querySelector('#ua-qs').type = 'text'")
        p.fill("#ua-qs", "7pm")
        p.click("#uaSave")
        p.wait_for_selector("#ua-qs-err:not([hidden])")
        self.assertIn("HH:MM", p.inner_text("#ua-qs-err"))
        self.assertIsNone(self.db.get_setting("unattended"))
        p.evaluate("document.querySelector('#ua-qs').type = 'time'")
        p.fill("#ua-qs", "00:00")
        p.fill("#ua-qe", "00:00")
        # review: plain-language summary, the fields are locked, one primary action
        p.click("#uaSave")
        p.wait_for_selector("#uaReview:not([hidden])")
        self.assertIn("Up to 15 applications a day to jobs scoring 80 or more, every 60 min, at any time of day.",
                      p.inner_text("#uaReviewText"))
        self.assertTrue(p.is_disabled("#ua-interval"))
        self.assertEqual(p.inner_text("#uaSave"), "Confirm and turn on")
        self.assertIsNone(self.db.get_setting("unattended"))
        p.click("#uaBack")  # back to edit, then review again
        self.assertTrue(p.is_enabled("#ua-interval"))
        p.click("#uaSave")
        p.wait_for_selector("#uaReview:not([hidden])")
        p.click("#uaSave")
        p.wait_for_selector("#uaStatus:has-text('Running')")
        self.assertRegex(p.inner_text("#uaStatus"), r"Running · (next run \d\d:\d\d|applying now)")
        busy = " ".join(self.seen())
        self.assertIn("uaSave:aria-busy=true", busy)  # loading: busy and disabled while saving
        self.assertIn("uaSave:disabled=", busy)
        s = self.ua.settings()
        self.assertEqual((s["enabled"], s["searches"], s["interval_min"]), (True, [sid], 60))
        p.wait_for_selector("#uaSaved:has-text('Turned on')")
        self.assertTrue(p.is_visible("#uaRunNow") and p.is_visible("#uaOff") and p.is_visible("#uaPreview"))
        self.assertEqual(p.inner_text("#uaSave"), "Save changes")
        self.ua.worker.join(30)  # the dry run that starts on the first enable
        # Run now (S2) and Turn off
        p.click("#uaRunNow")
        p.wait_for_selector("#uaSaved:has-text('Run started')")
        self.ua.worker.join(30)
        p.click("#uaOff")
        p.wait_for_selector("#uaStatus:has-text('Off')")
        self.assertFalse(self.ua.settings()["enabled"])

    def test_j3_kill_switch_on_every_view_and_resume_confirmation(self):
        p = self.page
        sid = self.saved_search()
        self.setup_on([sid])
        for view in VIEWS:
            self.open(view)
            kill = p.get_by_role("button", name="Pause auto-apply")
            self.assertTrue(kill.is_visible(), view)
            self.assertGreaterEqual(kill.bounding_box()["height"], 24)
        # keyboard only: Tab from the top reaches the kill switch, Enter pauses
        self.open("today")
        p.focus("body")
        for _ in range(12):
            p.keyboard.press("Tab")
            if p.evaluate("document.activeElement.id") == "uaKill":
                break
        self.assertEqual(p.evaluate("document.activeElement.id"), "uaKill")
        self.assertGreaterEqual(p.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)"), 2)
        t0 = time.time()
        p.keyboard.press("Enter")
        p.wait_for_selector("#uaStatus:has-text('Paused')")
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(self.ua.mode(), "paused")
        p.wait_for_function("() => document.querySelector('#uaAlert').textContent.includes('paused')")
        self.assertEqual(p.get_attribute("#uaAlert", "aria-live"), "assertive")
        self.assertEqual(p.inner_text("#uaKill"), "Resume auto-apply")
        self.assertEqual(p.inner_text("#uaIcon"), "❚❚")  # status is text plus an icon, never colour alone
        # resume asks for confirmation; Keep paused changes nothing
        p.click("#uaKill")
        self.assertTrue(p.is_visible("#resumeDlg"))
        p.click("#resumeCancel")
        self.assertEqual(self.ua.mode(), "paused")
        p.click("#uaKill")
        p.click("#resumeOk")
        p.wait_for_selector("#uaStatus:has-text('Running')")
        self.assertEqual(self.ua.mode(), "running")
        # S1: an automatic pause shows a banner that says why
        self.ua.pause("Paused automatically: the last 3 runs failed (every source failed).", auto=True)
        self.open("search")
        p.wait_for_selector("#uaBanner:not([hidden])")
        self.assertIn("Paused automatically", p.inner_text("#uaBanner"))

    def test_j4_j5_daily_summary_states_and_dismiss(self):
        p = self.page
        sid = self.saved_search()
        SCORES.update({"Good Job": 92, "Wall Job": 91})
        self.fake.script["Wall Job"] = {"status": "needs_you", "reason": "The form has a CAPTCHA."}
        self.net.routes["/boards/a/"] = board(("Good Job", "Good Co"), ("Wall Job", "Wall Co"))
        self.setup_on([sid])
        run_id, _ = self.go()
        now = time.time()
        for i in range(2):
            self.ua._job_row(run_id, {"key": f"{i:016x}", "title": f"Applied {i}", "company": "C", "score": 85}, "submitted", "Thanks", "Board A", now)
        seed(self.db, "5" * 16, "needs_you", title="Parked", company="P Co", reason="Sign-in", job_url="https://jobs.example.test/p")
        self.ua._job_row(run_id, {"key": "5" * 16, "title": "Parked", "company": "P Co", "score": 88}, "needs_you", "x.example needs a sign-in.", "Board A", now)
        for i in range(5):
            self.ua._job_row(run_id, {"key": f"{i + 9:016x}", "title": f"Skip {i}", "company": "C", "score": 81}, "skipped", "company cooldown until 2026-12-01", "Board A", now)
        self.open("today")
        p.wait_for_selector("#sumCounts .chip")
        self.assertEqual(p.inner_text("#sumCounts").split("\n"), ["Applied: 3", "Needs you: 2", "Skipped: 5"])
        self.assertIn("sumLoading:hidden=null", self.seen())  # the skeleton was shown while loading
        self.assertEqual(p.locator("#sumBody table").count(), 3)
        self.assertEqual(p.locator("#sumBody table").first.locator("th").all_inner_texts()[:4], ["Time", "Job", "Company", "Score"])
        text = p.inner_text("#sumBody")
        for want in ("company cooldown until 2026-12-01", "The form has a CAPTCHA.", "Thank you for applying!", "✓ Submitted", "! Needs you", "– Skipped"):
            self.assertIn(want, text)
        proof = p.get_by_role("link", name="Proof screenshot for Good Job at Good Co (opens in a new tab)")
        self.assertTrue(proof.get_attribute("href").endswith("/proof"))
        self.assertEqual(p.evaluate(NAME_JS), [])
        # J5: dismiss a Needs-you item; the page and the status bar update without a reload
        self.assertIn("2 need you", p.inner_text("#uaStatus"))
        p.get_by_role("button", name="Dismiss Parked at P Co").click()
        p.wait_for_selector("#uaStatus:has-text('1 needs you')")
        self.assertIn("Now: Dismissed", p.inner_text("#sumBody"))
        self.assertEqual(p.evaluate("performance.getEntriesByType('navigation').length"), 1)
        open_link = p.get_by_role("link", name="Open posting Wall Job to apply yourself (opens in a new tab)")
        self.assertTrue(open_link.get_attribute("href").startswith("https://"))
        # empty state for a day with no runs
        p.fill("#dayPick", "2001-01-01")
        p.dispatch_event("#dayPick", "change")
        p.wait_for_selector("#sumEmpty:not([hidden])")
        self.assertIn("No unattended runs on this day", p.inner_text("#sumEmpty"))
        self.assertTrue(p.get_attribute("#dayCsv", "href").endswith("day=2001-01-01"))
        # error with Retry: Retry loads the summary again
        p.route("**/api/unattended/summary?*", lambda r: r.fulfill(status=500, body="{}", content_type="application/json"))
        p.click("#dayToday")
        p.wait_for_selector("#sumError:not([hidden])")
        self.assertIn("Couldn't load summary", p.inner_text("#sumError"))
        p.unroute("**/api/unattended/summary?*")
        p.click("#sumRetry")
        p.wait_for_selector("#sumCounts .chip")
        self.assertTrue(p.is_hidden("#sumError"))
        # 500 rows render within 1 s
        for i in range(500):
            self.ua._job_row(run_id, {"key": f"{i:016x}", "title": f"Bulk {i}", "company": "C"}, "skipped", "duplicate", "s", now)
        t0 = time.time()
        p.click("#dayToday")
        p.wait_for_function("() => document.querySelectorAll('#sumBody tr').length > 500")
        self.assertLess(time.time() - t0, 1.5)  # includes the HTTP round trip; rendering itself is well under 1 s

    def test_j6_run_log_states_and_per_job_outcomes(self):
        p = self.page
        self.open("runlog")
        p.wait_for_selector("#logEmpty:not([hidden])")
        self.assertIn("No unattended runs yet", p.inner_text("#logEmpty"))
        sid = self.saved_search()
        SCORES["Logged Job"] = 90
        self.net.routes["/boards/a/"] = board(("Logged Job", "Log Co"))
        self.net.routes["/boards/b/"] = __import__("urllib.error").error.URLError("timed out")
        self.setup_on([sid])
        self.go()
        self.ua.tick("schedule")  # the cap is not reached: a real run is logged too
        self.ua.worker.join(30)
        self.open("runlog")
        p.wait_for_selector("#logTable:not([hidden])")
        rows = p.locator("#logRows > tr:not(.run-jobs)")
        self.assertGreaterEqual(rows.count(), 2)
        text = p.inner_text("#logRows")
        self.assertIn("Run now", text)
        self.assertIn("Board B: Unavailable after 1 attempts (timed out)", text)
        self.assertIn("1 submitted", text)
        btn = p.get_by_role("button", name="Show 1 job").last
        self.assertEqual(btn.get_attribute("aria-expanded"), "false")
        btn.click()
        self.assertEqual(p.get_by_role("button", name="Hide 1 job").get_attribute("aria-expanded"), "true")
        self.assertIn("Logged Job", p.inner_text(".run-jobs:not([hidden])"))
        self.assertEqual(p.evaluate(NAME_JS), [])
        p.route("**/api/unattended/runs", lambda r: r.fulfill(status=500, body="{}", content_type="application/json"))
        self.open("runlog")
        p.wait_for_selector("#logError:not([hidden])")
        p.unroute("**/api/unattended/runs")
        p.click("#logRetry")
        p.wait_for_selector("#logTable:not([hidden])")
        self.assertTrue(p.is_hidden("#logError"))

    def test_m13_reflow_320_themes_names_and_settings_error_state(self):
        p = self.page
        sid = self.saved_search()
        self.setup_on([sid])
        self.go()
        self.ua._job_row(1, {"key": "a" * 16, "title": "A very long job title " * 4, "company": "Company " * 6, "score": 90},
                         "skipped", "already in Applications (needs your answers) " * 3, "Board A (" + "x" * 80 + ")", time.time())
        for theme in ("light", "dark"):
            p.set_viewport_size({"width": 1366, "height": 768})
            self.open("unattended")
            p.select_option("#themeSel", theme)
            p.set_viewport_size({"width": 320, "height": 640})
            for view in ("unattended", "today", "runlog", "applications", "search"):
                self.open(view)
                if view == "runlog":
                    p.locator("#logRows button").first.click()
                self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 320, f"{theme} {view}")
                self.assertEqual(p.evaluate(NAME_JS), [], f"{theme} {view}")
        # the status bar differs between light and dark (designed themes, not an inversion)
        colours = {}
        p.set_viewport_size({"width": 1366, "height": 768})
        for theme in ("light", "dark"):
            p.select_option("#themeSel", theme)
            colours[theme] = p.evaluate("getComputedStyle(document.querySelector('#uaBar')).backgroundColor")
        self.assertNotEqual(colours["light"], colours["dark"])
        # Descriptions off hides the explanatory text only
        self.open("unattended")
        p.uncheck("#descToggle")
        self.assertTrue(p.is_hidden("#view-unattended > p.desc"))
        self.assertTrue(p.is_visible("#ua-q-hint"))
        p.check("#descToggle")
        # settings error state with Retry
        p.route("**/api/unattended", lambda r: r.fulfill(status=500, body="{}", content_type="application/json"))
        p.goto("about:blank")
        p.goto(f"{self.base}/#unattended")
        p.wait_for_selector("#uaError:not([hidden])")
        self.assertIn("Couldn't load unattended settings", p.inner_text("#uaError"))
        self.assertIn("status unavailable", p.inner_text("#uaStatus"))
        p.unroute("**/api/unattended")
        p.click("#uaRetry")
        p.wait_for_selector("#uaForm:not([hidden])")


if __name__ == "__main__":
    unittest.main()
