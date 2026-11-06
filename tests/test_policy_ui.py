"""The Application policy panel and consent dialog, and the Outcome unknown actions, in real Chromium against the
app's own server (no external sites)."""
import unittest

from app import audit, policy
from tests.helpers import seed
from tests.test_apply import _chromium_ok
from tests import test_ui_browser as base
from tests.test_ui_browser import NAME_JS


@unittest.skipUnless(_chromium_ok(), "Chromium is not available (run playwright install-deps)")
class PolicyUI(base.UIBrowser):
    test_m7_settings_portal_journey = test_m11_applications_via_labels_progress_and_empty_state = None
    test_keyboard_names_focus_reflow_and_themes = None

    def test_consent_dialog_grants_exactly_the_shown_scope(self):
        p = self.page
        self.open("settings")
        p.wait_for_function("() => document.querySelector('#policyNow').textContent.length > 0")
        self.assertIn("Search only", p.inner_text("#policyNow"))
        self.assertTrue(p.is_checked("#pm-search"))
        p.check("#pm-auto")
        p.fill("#p-cap", "3")
        p.fill("#p-excl", "Contoso, Northwind")
        p.uncheck("#p-ch-linkedin")
        p.click("#policyForm button[type=submit]")
        p.wait_for_selector("#consentDlg[open]")
        text = p.inner_text("#consentText")
        self.assertIn("at most 3 per day", text)
        self.assertIn("Contoso, Northwind", text)
        self.assertNotIn("LinkedIn", text)
        self.assertEqual(self.page.evaluate(NAME_JS), [])
        p.click("#consentCancel")  # cancel: nothing changes
        self.assertEqual(policy.view(self.db)["mode"], "SEARCH_ONLY")
        p.click("#policyForm button[type=submit]")
        p.wait_for_selector("#consentDlg[open]")
        p.click("#consentOk")
        p.wait_for_function("() => document.querySelector('#policyNow').dataset.mode === 'AUTHORIZED_AUTO_SUBMIT'")
        v = policy.view(self.db)
        self.assertEqual((v["effective_mode"], v["daily_cap"], v["channels"]), ("AUTHORIZED_AUTO_SUBMIT", 3, ["company", "naukri"]))
        self.assertIn("0 of 3 sent today", p.inner_text("#policyNow"))
        self.assertTrue(audit.verify(self.db)[0])
        # back to Prepare for review: no dialog, consent ended
        p.check("#pm-prepare")
        p.click("#policyForm button[type=submit]")
        p.wait_for_function("() => document.querySelector('#policyNow').dataset.mode === 'PREPARE_FOR_REVIEW'")
        self.assertIsNone(policy.view(self.db)["consent"])
        # an invalid cap shows the server's message and saves nothing
        p.fill("#p-cap", "0")
        p.click("#policyForm button[type=submit]")
        p.wait_for_selector("#policyErr:not([hidden])")
        self.assertIn("1 to 100", p.inner_text("#policyErr"))
        self.assertEqual(policy.view(self.db)["daily_cap"], 3)

    def test_outcome_unknown_offers_only_sent_or_not_sent(self):
        seed(self.db, "a" * 16, "outcome_unknown", title="Unknown job", company="Acme", reason="Confirmation not found.",
             url="https://jobs.example.test/1")
        self.open("applications")
        item = self.page.locator(".app-item", has_text="Unknown job")
        item.wait_for()
        self.assertIn("Outcome unknown", item.inner_text())
        names = item.locator("button").all_inner_texts()
        self.assertIn("It was sent: Unknown job", [n.replace("\n", "") for n in names])
        self.assertFalse(any(n.startswith("Retry") for n in names))
        item.locator("button", has_text="It was not sent").click()
        self.page.wait_for_function("() => !document.body.innerText.includes('Outcome unknown')")
        self.assertEqual(self.db.app_get("a" * 16)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
