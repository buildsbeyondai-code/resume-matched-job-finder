"""Finding an aggregator job's original posting on the company's own careers board (no real network)."""
import json
import unittest

from app import direct, sources
from tests.helpers import FakeNet


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.orig = sources.FETCH

    def tearDown(self):
        sources.FETCH = self.orig

    def test_slugs_and_title_match(self):
        self.assertEqual(direct.slugs("Allshore Talent Pvt Ltd"), ["allshoretalent", "allshore-talent", "allshore"])
        self.assertEqual(direct.slugs("Simbian AI"), ["simbianai", "simbian-ai", "simbian"])
        self.assertGreaterEqual(direct.title_match(
            "Principal Program Manager - Generative AI Implementation (100% Remote)",
            "Principal Program Manager, Generative AI Implementation"), 0.8)
        self.assertLess(direct.title_match("Principal Program Manager", "Senior Data Engineer"), 0.2)
        self.assertTrue(direct.is_aggregator("https://www.adzuna.in/details/5919137947?utm_medium=api"))
        self.assertFalse(direct.is_aggregator("https://jobs.lever.co/x/1"))

    def test_finds_posting_on_company_board(self):
        err = sources.SourceError("HTTP 404")
        net = FakeNet({
            "boards-api.greenhouse.io/v1/boards/allshoretalent/": err,
            "boards-api.greenhouse.io": err, "api.lever.co": err, "api.smartrecruiters.com": {"content": []},
            "api.ashbyhq.com/posting-api/job-board/allshoretalent": {"jobs": [
                {"title": "Senior Recruiter", "jobUrl": "https://jobs.ashbyhq.com/allshoretalent/1"},
                {"title": "Principal Program Manager, Generative AI Implementation",
                 "jobUrl": "https://jobs.ashbyhq.com/allshoretalent/2"}]},
            "api.ashbyhq.com": err, "apply.workable.com": err})
        sources.FETCH = net
        hit = direct.find_on_company_board("Allshore Talent",
                                           "Principal Program Manager - Generative AI Implementation (100% Remote)")
        self.assertEqual(hit["url"], "https://jobs.ashbyhq.com/allshoretalent/2")
        self.assertEqual(hit["board"], "Ashby")
        for u in net.requests:  # every request stays on the HTTPS allow-list
            sources.check_allowed(u)

    def test_none_when_company_has_no_public_board(self):
        sources.FETCH = FakeNet({"": sources.SourceError("HTTP 404")})
        self.assertIsNone(direct.find_on_company_board("Techbliss Digital Solution Pvt Ltd", "QA Engineer"))
        u = direct.careers_search_url("Techbliss", "Senior QA Engineer (Remote)")
        self.assertTrue(u.startswith("https://duckduckgo.com/?q=Techbliss+careers"))


if __name__ == "__main__":
    unittest.main()
