"""End to end with Claude answers: the real app, a real Chromium and the fake careers web, with the Claude Code CLI
replaced by tests/fake_claude.py. Claude answers what the facts support; everything else is still asked; the
user's answers finish the application; the Settings switch turns Claude off."""
import json
import os
import tempfile
from pathlib import Path

from tests import fake_careers, test_autoapply_integration as base

FAKE = str(Path(__file__).with_name("fake_claude.py"))


class ClaudeAutoApply(base.AutoApplyIntegration):
    test_auto_apply_journey = None  # the shared journey runs in its own module

    @classmethod
    def setUpClass(cls):
        cls.ai_tmp = tempfile.TemporaryDirectory()
        cls.ai_log = Path(cls.ai_tmp.name) / "claude.jsonl"
        cls.ai_env = {k: os.environ.get(k) for k in ("RMJF_CLAUDE_BIN", "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_MODE")}
        os.environ.update(RMJF_CLAUDE_BIN=FAKE, FAKE_CLAUDE_LOG=str(cls.ai_log), FAKE_CLAUDE_MODE="answer")
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for k, v in cls.ai_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.ai_tmp.cleanup()

    def calls(self):
        return [json.loads(x) for x in self.ai_log.read_text().splitlines()] if self.ai_log.exists() else []

    def test_claude_answers_what_it_can_and_the_rest_is_asked(self):
        gh = self.key("gh")
        self.assertEqual(self.call("POST", "/api/autoapply", {"keys": [gh]})[0], 202)
        apps, snap = self.wait()
        a = apps[gh]
        self.assertEqual(a["status"], "needs_answers")
        self.assertEqual({u["label"].rstrip(" *") for u in a["ai"]},
                         {"Why do you want to work here?",
                          "Are you legally authorized to work in the country for which you are applying?"})
        # Claude's "Maybe" fits neither the visa radio nor the consent checkbox, so both are asked
        self.assertEqual({q["label"].rstrip(" *") for q in snap["questions"]},
                         {"Will you now or in the future require visa sponsorship?", "I agree to the privacy policy"})
        calls = self.calls()
        self.assertEqual(len(calls), 1)  # one call for the whole form
        prompt = calls[0]["prompt"]
        for text in ("Senior AI Program Manager", "Greenhouse Corp", "Current company: Contoso"):
            self.assertIn(text, prompt)
        self.assertNotIn("First Name", prompt)  # questions resume_details.md answers are not sent

        qs = {q["label"].rstrip(" *"): q["qid"] for q in snap["questions"]}
        code, r = self.call("POST", "/api/applications/answers", {"answers": {
            qs["Will you now or in the future require visa sponsorship?"]: "No",
            qs["I agree to the privacy policy"]: "Yes"}})
        self.assertEqual((code, r["retried"]), (200, [gh]))
        apps, _ = self.wait()
        self.assertEqual(apps[gh]["status"], "submitted")
        got = fake_careers.fields(self.fake.submissions["gh"])
        self.assertEqual((got["why"], got["auth"], got["spon"], got["privacy"], got["first_name"]),
                         ("I lead AI programme delivery at Contoso, which matches this role.", "yes", "no", "on", "Alex"))

        # Settings: off means Claude is not called; a non-boolean is refused
        self.assertEqual(self.call("PUT", "/api/settings", {"ai_answers": "yes"})[0], 400)
        code, st = self.call("PUT", "/api/settings", {"ai_answers": False})
        self.assertEqual((code, st["settings"]["ai_answers"]), (200, False))
        n = len(self.calls())
        multi = self.key("multi")
        self.call("POST", "/api/autoapply", {"keys": [multi]})
        apps, _ = self.wait()
        self.assertEqual(apps[multi]["status"], "needs_answers")
        self.assertEqual((len(self.calls()), apps[multi]["ai"]), (n, []))
        self.call("PUT", "/api/settings", {"ai_answers": True})
