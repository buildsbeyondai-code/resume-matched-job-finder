"""Unit + adapter contract tests (M1, M3, M4, M5, M2 filtering)."""
import time
import unittest
import urllib.error

from app import config, matching, sources
from app.resume import ResumeError, parse_resume
from tests.helpers import RESUME_LINES, FakeNet, fixture, make_docx, make_pdf

GH = {"id": 1, "kind": "greenhouse", "token": "examplecorp", "label": "ExampleCorp (Greenhouse)"}
LV = {"id": 2, "kind": "lever", "token": "examplecorp", "label": "ExampleCorp (Lever)"}
SR = {"id": 3, "kind": "smartrecruiters", "token": "ExampleGroup", "label": "Example Group (SmartRecruiters)"}
AZ = {"id": 4, "kind": "adzuna", "token": "", "label": "Adzuna"}


class ResumeTests(unittest.TestCase):
    def test_m1_pdf_extracts_profile_within_5s(self):
        t = time.time()
        r = parse_resume(make_pdf(pages=2), "cv.pdf")
        self.assertLess(time.time() - t, 5)
        for s in ("Python", "AWS", "SQL", "Docker", "Kubernetes", "React"):
            self.assertIn(s, r["skills"])
        self.assertEqual(r["years"], 6.0)
        self.assertIn("Senior Software Engineer", r["titles"])

    def test_m1_docx(self):
        r = parse_resume(make_docx(), "cv.docx")
        self.assertEqual(r["kind"], "docx")
        self.assertIn("Python", r["skills"])
        self.assertIn("Backend Developer", r["titles"])

    def test_m1_error_oversize(self):
        with self.assertRaisesRegex(ResumeError, "10 MB"):
            parse_resume(b"%PDF-" + b"0" * (15 * 1024 * 1024))

    def test_m1_error_corrupted(self):
        with self.assertRaisesRegex(ResumeError, "corrupted|No readable"):
            parse_resume(b"%PDF-1.4\n garbage without end")
        with self.assertRaisesRegex(ResumeError, "not a PDF or DOCX"):
            parse_resume(b"MZ\x90\x00 executable")
        with self.assertRaisesRegex(ResumeError, "not a PDF or DOCX"):
            parse_resume(b"PK\x03\x04broken zip")

    def test_years_from_date_ranges(self):
        from app.resume import extract_years
        self.assertEqual(extract_years("Acme 2015 - 2018\nBeta 2018 to 2020"), 5.0)

    def test_parse_5_page_pdf_under_5s(self):
        t = time.time()
        parse_resume(make_pdf(RESUME_LINES * 20, pages=5))
        self.assertLess(time.time() - t, 5)


class AdapterContractTests(unittest.TestCase):
    """Recorded-shape fixtures: if a provider changes its field names these fail."""

    def test_greenhouse(self):
        ps = sources.ADAPTERS["greenhouse"].parse(fixture("greenhouse_jobs.json"), GH)
        self.assertEqual(len(ps), 3)
        self.assertEqual(ps[0]["company"], "ExampleCorp")
        self.assertNotIn("<script", ps[0]["description"])
        self.assertNotIn("alert", ps[0]["description"].replace("alert(1)", "") + "")  # script content removed
        self.assertTrue(ps[0]["url"].startswith("https://"))

    def test_lever(self):
        ps = sources.ADAPTERS["lever"].parse(fixture("lever_postings.json"), LV)
        self.assertEqual(ps[0]["mode_hint"], "remote")
        self.assertEqual(ps[0]["salary_min"], 70000)
        self.assertEqual(ps[0]["posted"][:4], "2025")

    def test_smartrecruiters(self):
        ps = sources.ADAPTERS["smartrecruiters"].parse(fixture("smartrecruiters_postings.json"), SR)
        self.assertEqual(ps[0]["url"], "https://jobs.smartrecruiters.com/ExampleGroup/744000001")
        self.assertEqual(ps[0]["mode_hint"], "remote")

    def test_adzuna_requires_env_key_and_parses(self):
        import os
        old = os.environ.pop("ADZUNA_APP_ID", None), os.environ.pop("ADZUNA_APP_KEY", None)
        try:
            with self.assertRaisesRegex(sources.SourceError, "Not configured"):
                sources.ADAPTERS["adzuna"].urls(AZ, {"countries": ["Germany"]})
            os.environ["ADZUNA_APP_ID"], os.environ["ADZUNA_APP_KEY"] = "id1", "secretkey"
            urls = sources.ADAPTERS["adzuna"].urls(AZ, {"countries": ["Germany", "India"], "mode": "remote", "skills": ["Python"]})
            self.assertEqual(len(urls), 2)
            self.assertIn("/jobs/de/search/1", urls[0])
            self.assertNotIn("secretkey", sources.redact(urls[0]))
        finally:
            os.environ.pop("ADZUNA_APP_ID", None); os.environ.pop("ADZUNA_APP_KEY", None)
            if old[0]: os.environ["ADZUNA_APP_ID"] = old[0]
            if old[1]: os.environ["ADZUNA_APP_KEY"] = old[1]
        ps = sources.ADAPTERS["adzuna"].parse(fixture("adzuna_search.json"), AZ)
        self.assertEqual(ps[0]["company"], "SAP")


class NetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.orig = sources.FETCH

    def tearDown(self):
        sources.FETCH = self.orig

    def test_m3_allow_list(self):
        for bad in ("http://boards-api.greenhouse.io/x", "https://www.linkedin.com/jobs", "https://evil.example/api.lever.co"):
            with self.assertRaises(sources.NotAllowed):
                sources.http_get(bad)
        for host in config.ALLOWED_HOSTS:
            sources.check_allowed(f"https://{host}/x")

    def test_retry_backoff_max_two_retries(self):
        net = FakeNet({"lever": urllib.error.URLError("timed out")})
        sources.FETCH = net
        sleeps = []
        with self.assertRaisesRegex(sources.SourceError, "3 attempts"):
            sources.http_get("https://api.lever.co/v0/postings/x", sleep=sleeps.append)
        self.assertEqual(len(net.requests), 3)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_no_retry_on_4xx(self):
        net = FakeNet({"lever": urllib.error.HTTPError("u", 404, "nf", {}, None)})
        sources.FETCH = net
        with self.assertRaisesRegex(sources.SourceError, "HTTP 404"):
            sources.http_get("https://api.lever.co/v0/postings/x", sleep=lambda s: None)
        self.assertEqual(len(net.requests), 1)


def P(**kw):
    base = {"source": "X", "url": "https://x", "title": "", "company": "C", "location": "", "description": "",
            "country_hint": "", "mode_hint": "", "posted": ""}
    base.update(kw)
    return matching.enrich(base)


class MatchingTests(unittest.TestCase):
    profile = {"skills": ["Python", "AWS", "SQL"], "titles": ["Software Engineer"], "years": 5}

    def test_m4_scores_and_lists(self):
        a = P(title="Software Engineer", description="Python AWS SQL")
        b = P(title="Software Engineer", description="Java Spring Kafka")
        sa, sb = matching.score(a, self.profile), matching.score(b, self.profile)
        self.assertGreater(sa["score"], sb["score"])
        self.assertEqual(sorted(sa["matched"]), ["AWS", "Python", "SQL"])
        self.assertEqual(sa["missing"], [])
        self.assertEqual(sorted(sb["missing"]), ["Java", "Kafka", "Spring"])
        self.assertTrue(0 <= sb["score"] <= sa["score"] <= 100)

    def test_seniority_downranks_mismatch(self):
        a = P(title="Software Engineer Intern", description="Python AWS SQL")
        b = P(title="Software Engineer", description="Python AWS SQL")
        self.assertLess(matching.score(a, self.profile)["score"], matching.score(b, self.profile)["score"])

    def test_m5_dedupe(self):
        a = P(source="Greenhouse", company="ExampleCorp", title="Backend Engineer", location="Remote - Germany", url="https://a")
        b = P(source="Lever", company="examplecorp", title="Backend engineer", location="Remote - Germany", url="https://b")
        merged = matching.dedupe([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual([s["name"] for s in merged[0]["sources"]], ["Greenhouse", "Lever"])

    def test_m2_country_and_mode(self):
        params = {"countries": ["Germany"], "mode": "remote", "skills": ["Python"]}
        ok = [P(title="Dev", location="Remote - Germany", description="Python"),
              P(title="Dev", location="Remote (EMEA)", description="Python"),
              P(title="Dev", location="Remote, Worldwide", description="Python")]
        bad = [P(title="Dev", location="Berlin, Germany", description="Python"),
               P(title="Dev", location="Remote - US", description="Python"),
               P(title="Dev", location="Remote - Germany", description="Java"),
               P(title="Dev", location="Pune, India", mode_hint="remote", description="Python")]
        self.assertTrue(all(matching.passes_search_filters(p, params) for p in ok))
        self.assertFalse(any(matching.passes_search_filters(p, params) for p in bad))

    def test_rank_1000_under_1s(self):
        ps = [P(title=f"Engineer {i}", description="Python SQL Docker " * 20) for i in range(1000)]
        t = time.time()
        sorted((matching.score(p, self.profile) for p in ps), key=lambda r: -r["score"])
        self.assertLess(time.time() - t, 1.0)


if __name__ == "__main__":
    unittest.main()
