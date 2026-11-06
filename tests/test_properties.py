"""Property-based and fuzz tests (stdlib random with fixed seeds, so every run is reproducible):
resume parsers under mutated and hostile input, URL validation, submission-key identity, score bounds and the
state machine under random walks."""
import io
import random
import string
import time
import unittest
import zipfile

from app import matching, policy, server, states
from app.resume import ResumeError, parse_resume
from tests.helpers import make_pdf

SEED = 20261106


def make_docx(body_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", body_xml)
    return buf.getvalue()


DOC = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
       '<w:p><w:r><w:t>Alex Sample, Senior Engineer. Python, AWS, SQL. 2015 - present</w:t></w:r></w:p>'
       '</w:body></w:document>')


class Parsers(unittest.TestCase):
    def assert_safe(self, data, name):
        t = time.time()
        try:
            out = parse_resume(data, name)
            self.assertIsInstance(out["text"], str)
        except ResumeError:
            pass  # a clear user-facing error is the only allowed failure
        self.assertLess(time.time() - t, 5, name)

    def test_mutated_pdfs_and_docx_never_crash(self):
        rnd = random.Random(SEED)
        seeds = [make_pdf(), make_docx(DOC)]
        for i in range(300):
            data = bytearray(rnd.choice(seeds))
            for _ in range(rnd.randint(1, 20)):
                op = rnd.random()
                pos = rnd.randrange(len(data))
                if op < 0.4:
                    data[pos] = rnd.randrange(256)
                elif op < 0.7:
                    del data[pos:pos + rnd.randint(1, 64)]
                else:
                    data[pos:pos] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 64)))
            with self.subTest(i=i):
                self.assert_safe(bytes(data), "x.pdf" if data[:4] == b"%PDF" else "x.docx")
        for junk in (b"", b"%PDF-1.4", b"PK\x03\x04", b"\x00" * 1000, bytes(range(256)) * 10):
            self.assert_safe(junk, "junk")

    def test_docx_bombs_and_entities_are_refused(self):
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<a>" + " " * (60 * 1024 * 1024) + "</a>")  # 60 MB of spaces
        with self.assertRaises(ResumeError) as e:
            parse_resume(bomb.getvalue(), "bomb.docx")
        self.assertIn("too large", str(e.exception))
        laughs = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;">]>'
                  '<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>')
        with self.assertRaises(ResumeError) as e:
            parse_resume(make_docx(laughs), "laughs.docx")
        self.assertIn("XML features", str(e.exception))
        many = io.BytesIO()
        with zipfile.ZipFile(many, "w") as z:
            for i in range(2100):
                z.writestr(f"p{i}", "")
            z.writestr("word/document.xml", DOC)
        with self.assertRaises(ResumeError) as e:
            parse_resume(many.getvalue(), "many.docx")
        self.assertIn("too many parts", str(e.exception))

    def test_pdf_with_many_expanding_streams_is_bounded(self):
        import zlib
        stream = zlib.compress(b"BT (x) Tj ET " + b" " * (8 * 1024 * 1024))
        body = b"".join(b"<< /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\n" for _ in range(12))
        data = b"%PDF-1.4\n" + body + b"%%EOF"
        t = time.time()
        try:
            parse_resume(data, "bomb.pdf")
        except ResumeError:
            pass
        self.assertLess(time.time() - t, 10)


class Urls(unittest.TestCase):
    def test_only_https_job_links(self):
        rnd = random.Random(SEED)
        alphabet = string.ascii_letters + string.digits + ":/.@%?#&=-_ \\\t\n"
        for _ in range(2000):
            u = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
            self.assertEqual(server.job_url_ok(u), u.startswith("https://"))
        for bad in ("javascript:alert(1)", "http://evil.example/x", "file:///etc/passwd", "HTTPS://x", " https://x",
                    "data:text/html,x", "ftp://x"):
            self.assertFalse(server.job_url_ok(bad), bad)


class SubmissionKeys(unittest.TestCase):
    def test_identity_ignores_formatting_and_board(self):
        rnd = random.Random(SEED)
        for _ in range(500):
            company = rnd.choice(["Acme", "Bolt Labs", "Contoso", "Northwind Traders"])
            title = rnd.choice(["Release Manager", "AI Engineer", "Program Manager"])
            city = rnd.choice(["Pune", "Remote", "Berlin"])
            a = {"company": company, "title": title, "location": city + ", Somewhere", "url": "https://a/1"}
            noisy = {"company": rnd.choice(["", " "]) + company.upper() + rnd.choice(["", " Pvt Ltd", " Inc.", " GmbH"]),
                     "title": title.replace(" ", rnd.choice([" ", "  ", "-"])).lower(),
                     "location": city.lower() + rnd.choice(["", " (hybrid)", ", X"]), "url": "https://b/2"}
            self.assertEqual(policy.submission_key(a), policy.submission_key(noisy), (a, noisy))
            other = dict(a, title=title + " II")
            self.assertNotEqual(policy.submission_key(a), policy.submission_key(other))


class Scores(unittest.TestCase):
    def test_scores_stay_in_range(self):
        rnd = random.Random(SEED)
        words = ["Python", "SQL", "AWS", "Docker", "lead", "manager", "remote", "Java", "Go", "R", "AI", "x" * 50]
        for _ in range(400):
            p = {"title": " ".join(rnd.choices(words, k=rnd.randint(0, 4))),
                 "description": " ".join(rnd.choices(words, k=rnd.randint(0, 60))), "skills": []}
            prof = {"skills": rnd.sample(words, rnd.randint(0, 6)), "titles": [], "years": rnd.choice([0, 3, 12, None])}
            r = matching.score(p, prof)
            self.assertTrue(0 <= r["score"] <= 100, r)
            self.assertEqual(set(r["matched"]) & set(r["missing"]), set())


class StateWalks(unittest.TestCase):
    def test_random_walks_never_leave_a_terminal_state_or_requeue_unknown(self):
        rnd = random.Random(SEED)
        for _ in range(2000):
            cur = None
            for _ in range(12):
                new = rnd.choice(states.STATUSES)
                try:
                    states.check(cur, new)
                except states.IllegalTransition:
                    continue
                if cur == "outcome_unknown":
                    self.assertIn(new, ("outcome_unknown", "applied_manually", "failed"))
                if cur in ("applied_manually",):
                    self.assertEqual(new, cur)
                if cur == "submitted":
                    self.assertIn(new, ("submitted", "outcome_unknown"))
                cur = new


if __name__ == "__main__":
    unittest.main()
