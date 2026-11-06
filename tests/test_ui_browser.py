"""Browser UI tests (UEUIS §17) in real Chromium against the app's own server (no external sites):
the Settings portal journey (M7: defaults, inline error that keeps the input, aria-invalid, nothing saved, persisted
values), the Applications tab "via" labels, progress panel and empty state (M11, J5), keyboard reachability,
accessible names, visible focus, no horizontal scroll at 320 px, light and dark themes visibly different in the
header and navigation, and no script errors."""
import threading
import time
import unittest

from app import server
from app.db import DB
from tests.test_apply import _chromium_ok

NAME_JS = """() => [...document.querySelectorAll('button, a[href], input, select, textarea')].filter((e) => {
  const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden';
}).filter((e) => {
  const lab = (e.labels && [...e.labels].map((l) => l.innerText).join(' ')) || '';
  const by = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).map((id) => (document.getElementById(id) || {}).innerText || '').join(' ');
  return !(e.getAttribute('aria-label') || lab || by || e.innerText || e.value || e.title || '').trim();
}).map((e) => e.outerHTML.slice(0, 120))"""


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class UIBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.db = DB(":memory:")
        cls.srv = server.make_server(cls.db, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}/"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()

    def setUp(self):
        self.ctx = self.browser.new_context(viewport={"width": 1366, "height": 768})
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.external = []
        self.page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.base) else
                        (self.external.append(r.request.url), r.abort()))

    def tearDown(self):
        self.ctx.close()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def open(self, tab):
        self.page.goto("about:blank")
        self.page.goto(self.base + "#" + tab)
        self.page.wait_for_selector(f"#view-{tab}:not([hidden])")
        self.page.wait_for_timeout(300)

    def test_m7_settings_portal_journey(self):
        p = self.page
        self.open("settings")
        self.assertTrue(p.is_checked("#s-naukri") and p.is_checked("#s-linkedin"))
        self.assertEqual(p.input_value("#s-pause"), "20")
        self.assertEqual(p.get_by_label("Pause between portal applications").get_attribute("id"), "s-pause")
        self.assertIn("seconds", p.inner_text("#s-pause-unit"))
        self.assertIn("not checked yet", p.inner_text("#portalStatus"))
        for bad in ("3", "", "4.5", "601"):  # "" is what a number field reports for typed letters
            p.fill("#s-pause", bad)
            p.click("text=Save settings")
            p.wait_for_selector("#s-pause-err:not([hidden])")
            self.assertEqual(p.get_attribute("#s-pause", "aria-invalid"), "true")
            self.assertIn("5 to 600", p.inner_text("#s-pause-err"))
            self.assertEqual(p.evaluate("document.activeElement.id"), "s-pause")
            self.assertEqual(p.input_value("#s-pause"), bad)  # the input is kept for correction
            self.assertEqual(self.db.settings()["portal_pause_s"], 20)  # nothing saved
        # keyboard only: uncheck Naukri, type 45, submit with Enter
        p.focus("#s-naukri")
        p.keyboard.press("Space")
        p.fill("#s-pause", "45")
        p.focus("#s-pause")
        p.keyboard.press("Enter")
        p.wait_for_selector("#settingsSaved:has-text('Settings saved.')")
        self.assertIsNone(p.get_attribute("#s-pause", "aria-invalid"))
        self.assertTrue(p.is_hidden("#s-pause-err"))
        s = self.db.settings()
        self.assertEqual((s["fallback_portals"], s["portal_pause_s"]), (["linkedin"], 45))
        p.reload()
        p.wait_for_selector("#view-settings:not([hidden])")
        p.wait_for_function("document.querySelector('#s-pause').value === '45'")
        self.assertFalse(p.is_checked("#s-naukri"))
        self.db.set_setting("fallback_portals", ["naukri", "linkedin"])
        self.db.set_setting("portal_pause_s", 20)

    def test_m11_applications_via_labels_progress_and_empty_state(self):
        p = self.page
        self.db.x("DELETE FROM applications")
        self.open("applications")
        self.assertTrue(p.is_visible("#appEmpty"))
        self.assertIn("No applications yet", p.inner_text("#appEmpty"))
        self.assertTrue(p.is_hidden("#appRun"))
        rows = [("a" * 16, "submitted", "company", "Company job"), ("b" * 16, "submitted", "naukri", "Naukri job"),
                ("c" * 16, "submitted", "linkedin", "LinkedIn job")]
        for k, st, ch, t in rows:
            self.db.app_set(k, st, title=t, company="Acme", channel=ch, site="x")
        self.db.app_set("d" * 16, "needs_you", title="Walled job", company="Echo", url="https://www.naukri.com/job-listings-1",
                        reason="Naukri needs a sign-in. Open Settings, select Sign in to Naukri, then press Retry.")
        self.db.app_set("e" * 16, "running", title="Waiting job", company="Bolt", wait_until=time.time() + 30,
                        step="Waiting 30 s before the next job-portal application (rate limit)")
        self.open("applications")
        items = p.inner_text("#appList")
        for want in ("via company site", "via Naukri", "via LinkedIn", "Naukri needs a sign-in"):
            self.assertIn(want, items)
        self.assertTrue(p.is_visible("#appRun"))
        self.assertIn("rate limit", p.inner_text("#appProgress"))
        self.assertRegex(p.inner_text("#appCountdown"), r"^\d+ s left$")
        self.assertEqual(p.get_attribute("#appProgress", "aria-live"), "polite")
        link = p.get_by_role("link", name="Open in browser to finish Walled job yourself (opens in a new tab)")
        self.assertEqual(link.get_attribute("href"), "https://www.naukri.com/job-listings-1")
        # Cancel stops the run: the running job is flagged, the panel hides when nothing is active
        p.click("#appCancel")
        self.db.app_set("e" * 16, "cancelled", reason="Cancelled by you. Press Retry to apply.", wait_until=None)
        p.wait_for_selector("#appRun", state="hidden", timeout=5000)
        self.assertIn("Cancelled", p.inner_text("#appList"))
        self.assertEqual(p.locator("#appList .chip.st-cancelled").count(), 1)
        self.db.x("DELETE FROM applications")

    def test_keyboard_names_focus_reflow_and_themes(self):
        p = self.page
        self.open("settings")
        # keyboard reachability: Tab from the top reaches the portal controls and Save, in order
        seen = []
        p.focus("body")
        for _ in range(80):
            p.keyboard.press("Tab")
            seen.append(p.evaluate("document.activeElement.id || document.activeElement.textContent.trim().slice(0, 30)"))
        for want in ("s-naukri", "s-linkedin", "s-pause", "Save settings", "signNaukri", "signLinkedin", "checkPortals"):
            self.assertIn(want, seen)
        self.assertLess(seen.index("s-naukri"), seen.index("s-pause"))
        self.assertLess(seen.index("s-pause"), seen.index("Save settings"))
        # visible focus indicator of at least 2 px
        p.focus("#s-pause")
        p.keyboard.press("Shift+Tab")
        p.keyboard.press("Tab")
        width = p.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)")
        self.assertGreaterEqual(width, 2)
        # every visible control has an accessible name, on every tab
        for tab in ("search", "profile", "saved", "applications", "settings"):
            self.open(tab)
            self.assertEqual(p.evaluate(NAME_JS), [], tab)
        # targets: the portal checkboxes' labels are at least 24 px tall
        for sel in ("label[for=s-naukri]", "label[for=s-linkedin]", "#signNaukri", "#checkPortals"):
            self.open("settings")
            self.assertGreaterEqual(p.locator(sel).bounding_box()["height"], 24, sel)
        # reflow at 320 px: no horizontal scroll on any tab
        p.set_viewport_size({"width": 320, "height": 640})
        for tab in ("search", "profile", "saved", "applications", "settings"):
            self.open(tab)
            self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 320, tab)
        # light and dark are both designed and visibly different in the header and navigation
        p.set_viewport_size({"width": 1366, "height": 768})
        colours = {}
        for theme in ("light", "dark"):
            p.select_option("#themeSel", theme)
            colours[theme] = p.evaluate("""() => [getComputedStyle(document.querySelector('.topbar')).backgroundColor,
                getComputedStyle(document.querySelector('nav.tabs')).backgroundColor,
                getComputedStyle(document.body).backgroundColor]""")
        self.assertTrue(all(a != b for a, b in zip(colours["light"], colours["dark"])), colours)
        p.reload()
        self.assertEqual(p.evaluate("document.documentElement.dataset.theme"), "dark")  # the choice is remembered


if __name__ == "__main__":
    unittest.main()
