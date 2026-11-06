"""Claude answers (app/ai.py) and how the apply worker uses them (apply.convert / ai_actions / Assistant._ai).
The Claude Code CLI is replaced by tests/fake_claude.py; no model is called."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import ai, apply, config, details
from tests.helpers import make_pdf

FAKE = str(Path(__file__).with_name("fake_claude.py"))
Q = [{"id": "q1", "label": "Why do you want to work here?", "kind": "textarea", "options": []},
     {"id": "q2", "label": "Are you authorized to work in India?", "kind": "radio", "options": ["Yes", "No"]},
     {"id": "q3", "label": "Expected salary", "kind": "text", "options": []},
     {"id": "q4", "label": "Preferred shift", "kind": "select", "options": ["Day", "Night"]}]
JOB = {"title": "AI Program Manager", "company": "Acme", "location": "Pune", "description": "Lead AI delivery."}


class Env:
    """Set environment variables for one block."""
    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class Binary(unittest.TestCase):
    def test_explicit_fake_missing_and_test_mode(self):
        with Env(RMJF_CLAUDE_BIN=FAKE):
            self.assertEqual(ai.binary(), FAKE)
            self.assertTrue(ai.available())
        with Env(RMJF_CLAUDE_BIN="/nonexistent/claude"):
            self.assertIsNone(ai.binary())
        with Env(RMJF_CLAUDE_BIN=None), mock.patch.object(config, "ALLOW_LOCAL_HTTP", True):
            self.assertIsNone(ai.binary())  # tests never reach the real CLI
            self.assertFalse(ai.available())
        with Env(RMJF_CLAUDE_BIN=None), mock.patch.object(config, "ALLOW_LOCAL_HTTP", False), \
                mock.patch("shutil.which", return_value="/usr/bin/claude"):
            self.assertEqual(ai.binary(), "/usr/bin/claude")


class Answer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_ai(self, mode="answer", qs=Q):
        with Env(RMJF_CLAUDE_BIN=FAKE, FAKE_CLAUDE_MODE=mode, FAKE_CLAUDE_LOG=str(self.log)):
            return ai.answer(qs, JOB, "- Full name: Alex", "Contoso, AI programme lead " + "x" * 30000)

    def test_answers_only_what_the_facts_support(self):
        got = self.run_ai()
        self.assertEqual(set(got), {"q1", "q2", "q4"})  # salary is null; unknown ids and junk entries ignored
        self.assertEqual(got["q2"], ("Yes", "resume: lives in the job's country"))
        self.assertEqual(got["q4"][0], "Maybe")  # not an option: ai_actions keeps it as a question
        call = json.loads(self.log.read_text().splitlines()[0])
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--tools") + 1], "")  # no tools: Claude only answers
        self.assertIn("--json-schema", argv)
        p = call["prompt"]
        for text in ("AI Program Manager", "Lead AI delivery.", "- Full name: Alex", "Expected salary", "[…truncated]",
                     '"Night"'):
            self.assertIn(text, p)

    def test_no_questions_makes_no_call(self):
        self.assertEqual(self.run_ai(qs=[]), {})
        self.assertFalse(self.log.exists())

    def test_errors_become_ai_errors(self):
        for mode, text in (("error", "Credit balance is too low"), ("garbage", "not json"),
                           ("nostruct", "hello"), ("notdict", "no structured answer")):
            with self.subTest(mode=mode), self.assertRaises(ai.AIError) as e:
                self.run_ai(mode)
            self.assertIn(text, str(e.exception))
        with mock.patch.object(ai, "TIMEOUT_S", 0.5), self.assertRaises(ai.AIError) as e:
            self.run_ai("sleep")
        self.assertIn("did not answer within", str(e.exception))
        with Env(RMJF_CLAUDE_BIN="/nonexistent"), self.assertRaises(ai.AIError) as e:
            ai.answer(Q, JOB, "", "")
        self.assertIn("not installed", str(e.exception))

    def test_empty_output_and_answers_not_a_list(self):
        done = mock.Mock(stdout="", stderr="")
        with Env(RMJF_CLAUDE_BIN=FAKE), mock.patch("subprocess.run", return_value=done), \
                self.assertRaises(ai.AIError) as e:
            ai.answer(Q, JOB, "", "")
        self.assertIn("no output", str(e.exception))
        done = mock.Mock(stdout=json.dumps({"structured_output": {"answers": "x"}}), stderr="")
        with Env(RMJF_CLAUDE_BIN=FAKE), mock.patch("subprocess.run", return_value=done):
            self.assertEqual(ai.answer(Q, JOB, "", ""), {})


def field(fid, kind, options=(), label="Q"):
    return {"id": fid, "kind": kind, "options": list(options), "label": label, "name": fid, "required": True,
            "filled": False}


class Convert(unittest.TestCase):
    def test_convert_each_kind(self):
        self.assertEqual(apply.convert(field("a", "select", ["India", "US"]), "india"), ("fill", "India"))
        self.assertEqual(apply.convert(field("a", "radio", ["Yes", "No"]), "Maybe"), ("bad", None))
        self.assertEqual(apply.convert(field("a", "checkboxes", ["Python", "Go"]), "Python; Rust"), ("fill", ["Python"]))
        self.assertEqual(apply.convert(field("a", "checkboxes", ["Python"]), "Rust"), ("bad", None))
        self.assertEqual(apply.convert(field("a", "checkbox"), "Yes"), ("fill", "Yes"))
        self.assertEqual(apply.convert(field("a", "checkbox"), "No"), ("skip", None))
        self.assertEqual(apply.convert(field("a", "checkbox"), "Maybe"), ("bad", None))
        self.assertEqual(apply.convert(field("a", "text"), "16"), ("fill", "16"))

    def test_ai_actions(self):
        fields = [field("f1", "textarea"), field("f2", "radio", ["Yes", "No"]), field("f3", "checkbox"),
                  field("f4", "text")]
        missing = [{"id": f["id"], "label": f"L{f['id']}"} for f in fields] + [{"id": "gone", "label": "x"}]
        got = {"f1": ("Because.", "resume"), "f2": ("Maybe", "?"), "f3": ("No", "consent not given"), "gone": ("v", "b")}
        actions, rest, used = apply.ai_actions(fields, missing, got)
        self.assertEqual([(f["id"], v) for f, v in actions], [("f1", "Because.")])
        self.assertEqual([q["id"] for q in rest], ["f2", "f4", "gone"])
        self.assertEqual([u["label"] for u in used], ["Lf1", "Lf3"])  # an unticked checkbox counts as answered


class FakePage:
    url = "https://jobs.example.com/apply"
    frames = ()


class WorkerAI(unittest.TestCase):
    """Assistant._ai: when Claude is asked, and what it does with the answers."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Env(RMJF_JOBSEARCH_DIR=self.tmp.name, RMJF_CLAUDE_BIN=FAKE, FAKE_CLAUDE_MODE="answer",
                       FAKE_CLAUDE_LOG=str(Path(self.tmp.name) / "log"))
        self.env.__enter__()
        self.a = apply.Assistant()
        self.fields = [field("f1", "textarea", label="Why do you want to work here?"), field("f2", "text", label="Salary")]
        self.missing = [{"id": "f1", "label": "Why do you want to work here?", "kind": "textarea", "options": []},
                        {"id": "f2", "label": "Salary", "kind": "text", "options": []}]

    def tearDown(self):
        self.env.__exit__()
        self.tmp.cleanup()

    def session(self, **job):
        return apply.Session(dict({"title": "PM", "company": "Acme"}, **job), FakePage())

    def calls(self):
        p = Path(self.tmp.name) / "log"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def test_answers_once_per_question_with_progress(self):
        steps = []
        s = self.session(hooks=type("H", (), {"step": lambda _self, t: steps.append(t)})())
        actions, rest = self.a._ai(s, self.fields, self.missing)
        self.assertEqual([f["id"] for f, _ in actions], ["f1"])
        self.assertEqual([q["id"] for q in rest], ["f2"])
        self.assertEqual(steps, ["Claude is answering 2 questions"])
        self.assertEqual(s.ai_used[0]["label"], "Why do you want to work here?")
        # the next pass reuses the answer and does not ask Claude again
        actions, rest = self.a._ai(s, self.fields, self.missing)
        self.assertEqual((len(actions), self.calls(), len(s.ai_used)), (1, 1, 1))
        # one new question: asked alone
        s2 = self.session()
        self.a._ai(s2, self.fields, self.missing[:1])
        self.assertEqual(self.calls(), 2)

    def test_uses_details_and_resume_files(self):
        t = Path(self.tmp.name)
        (t / details.FILENAME).write_text("- Full name: Alex\n- Resume file: cv.pdf\n", encoding="utf-8")
        (t / "cv.pdf").write_bytes(make_pdf())
        self.a._ai(self.session(), self.fields, self.missing)
        p = json.loads((t / "log").read_text().splitlines()[0])["prompt"]
        self.assertIn("- Full name: Alex", p)
        self.assertIn(apply._resume_text(details.resume_file())[:20], p)
        self.assertEqual(apply._resume_text(None), "")
        bad = t / "bad_resume.pdf"
        bad.write_bytes(b"")
        self.assertEqual(apply._resume_text(bad), "")  # unreadable resume: Claude gets resume_details.md only

    def test_not_asked_when_off_unavailable_signin_or_nothing_missing(self):
        self.assertEqual(self.a._ai(self.session(ai=False), self.fields, self.missing), ([], self.missing))
        self.assertEqual(self.a._ai(self.session(), self.fields, []), ([], []))
        with mock.patch.object(apply.Assistant, "_signin_page", return_value=True):
            self.assertEqual(self.a._ai(self.session(), self.fields, self.missing), ([], self.missing))
        with Env(RMJF_CLAUDE_BIN="/nonexistent"):
            self.assertEqual(self.a._ai(self.session(), self.fields, self.missing), ([], self.missing))
        self.assertEqual(self.calls(), 0)

    def test_error_is_reported_and_questions_stay(self):
        s = self.session()
        with Env(FAKE_CLAUDE_MODE="error"):
            actions, rest = self.a._ai(s, self.fields, self.missing)
        self.assertEqual((actions, rest), ([], self.missing))
        self.assertIn("Credit balance is too low", s.ai_error)


if __name__ == "__main__":
    unittest.main()
