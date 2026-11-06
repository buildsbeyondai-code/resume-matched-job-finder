"""Unit and route tests for the job-portal fallback that need no browser: portal URLs and result matching
(portals.py), the Settings validation and persistence of fallback_portals / portal_pause_s (M7), the database
migration of an existing install, the rate-limit gate and cancel of the auto-apply runner (M8), and the portal
HTTP routes."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from app import apply, autoapply, config, portals, server
from app.db import DB
from tests.helpers import seed


class PortalRules(unittest.TestCase):
    def test_urls_hosts_and_session_cookies(self):
        self.assertEqual(portals.portal_of("https://www.naukri.com/job-listings-1"), "naukri")
        self.assertEqual(portals.portal_of("https://linkedin.com/jobs/view/1/"), "linkedin")
        self.assertIsNone(portals.portal_of("https://boards.greenhouse.io/x"))
        self.assertEqual(portals.search_url("naukri", "Senior Release Manager (Remote)", "Acme Ltd"),
                         "https://www.naukri.com/senior-release-manager-jobs?k=senior+release+manager%2C+Acme+Ltd")
        self.assertEqual(portals.search_url("naukri", "(Remote)", "Acme"), "https://www.naukri.com/jobs-jobs?k=%2C+Acme")
        self.assertEqual(portals.search_url("linkedin", "Data Engineer", "Acme"),
                         "https://www.linkedin.com/jobs/search/?keywords=data+engineer+Acme")
        self.assertEqual(portals.signin_url("linkedin"), "https://www.linkedin.com/login")
        self.assertEqual(portals.signed_in([{"name": "li_at", "domain": ".www.linkedin.com"},
                                            {"name": "nauk_at", "domain": ".example.com"}]),
                         {"naukri": False, "linkedin": True})
        # the portal hosts are for the browser only, never for sources.http_get
        self.assertFalse(config.BROWSER_PORTAL_HOSTS & config.ALLOWED_HOSTS)

    def test_enabled_order_and_test_mode_guard(self):
        self.assertEqual(portals.enabled(None), ["naukri", "linkedin"])
        self.assertEqual(portals.enabled(["linkedin", "indeed"]), ["linkedin"])
        self.assertEqual(portals.enabled([]), [])
        with mock.patch.object(config, "ALLOW_LOCAL_HTTP", True):  # test mode: real portals are never used
            self.assertEqual(portals.enabled(None), [])
            with mock.patch.dict(portals.PORTALS["naukri"], {"base": "http://127.0.0.1:9/naukri"}):
                self.assertEqual(portals.enabled(None), ["naukri"])

    def test_age(self):
        self.assertEqual([portals.age_days(t) for t in ("Just now", "Posted today", "5 hours ago", "3 weeks ago",
                                                         "30+ days ago", "1 month ago", "", None)],
                         [0, 0, 0, 21, 30, 30, 9999, 9999])

    def test_best_result_company_title_location_and_age(self):
        r = lambda t, card: {"href": "https://x/" + t + card, "title": t, "card": card}  # noqa: E731
        rows = [r("Release Manager", "Other Corp Hyderabad 1 day ago"),             # wrong company
                r("Office Manager", "Acme Analytics Hyderabad 1 day ago"),           # title too far
                r("Release Manager", "Acme Analytics Pune 1 day ago"),             # other city
                r("Senior Release Manager", "Acme Analytics Hyderabad 9 days ago"),  # in the city, older
                r("Release Manager", "Acme Analytics Hyderabad 2 days ago")]         # in the city, newest
        self.assertIs(portals.best_result(rows, "Release Manager", "Acme Analytics Pvt Ltd", "Hyderabad, India"), rows[4])
        self.assertIs(portals.best_result(rows[:3], "Release Manager", "Acme Analytics", "Pune"), rows[2])
        self.assertIs(portals.best_result(rows[2:3] * 2, "Release Manager", "Acme Analytics"), rows[2])  # tie: first
        self.assertIsNone(portals.best_result(rows, "Release Manager", "Nowhere Foods"))
        self.assertIsNone(portals.best_result(rows, "Release Manager", ""))
        self.assertIsNone(portals.best_result(rows[:2], "Chef", "Acme Analytics"))


class SettingsAndRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite3"
        self.start(DB(self.path))

    def start(self, db):
        self.db = db
        self.srv = server.make_server(db, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-RMJF-Request": "1", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_m7_defaults_validation_and_persistence(self):
        s = self.call("GET", "/api/state")[1]["settings"]
        self.assertEqual((s["fallback_portals"], s["portal_pause_s"]), (["naukri", "linkedin"], 20))
        for bad in ({"portal_pause_s": "abc"}, {"portal_pause_s": 4}, {"portal_pause_s": 601}, {"portal_pause_s": True},
                    {"portal_pause_s": "4.5"}, {"portal_pause_s": None}, {"portal_pause_s": -5},
                    {"fallback_portals": "naukri"}, {"fallback_portals": ["indeed"]},
                    {"portal_pause_s": 30, "min_score": 10, "fallback_portals": ["x"]}):
            code, d = self.call("PUT", "/api/settings", bad)
            self.assertEqual(code, 400, bad)
            self.assertIn(d["field"], ("portal_pause_s", "fallback_portals"))
        s = self.call("GET", "/api/state")[1]["settings"]
        self.assertEqual((s["portal_pause_s"], s["min_score"]), (20, 40))  # nothing was saved
        code, d = self.call("PUT", "/api/settings", {"fallback_portals": ["linkedin", "naukri"], "portal_pause_s": "45"})
        self.assertEqual((code, d["settings"]["fallback_portals"], d["settings"]["portal_pause_s"]),
                         (200, ["naukri", "linkedin"], 45))  # the order is always Naukri, then LinkedIn
        self.call("PUT", "/api/settings", {"fallback_portals": ["linkedin"]})
        # restart: a new server on the same database file keeps the values
        self.srv.shutdown()
        self.start(DB(self.path))
        s = self.call("GET", "/api/state")[1]["settings"]
        self.assertEqual((s["fallback_portals"], s["portal_pause_s"]), (["linkedin"], 45))

    def test_migration_of_an_existing_install(self):
        old = Path(self.tmp.name) / "old.sqlite3"
        con = sqlite3.connect(old)  # the schema and rows as an earlier version left them
        con.executescript("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
                          "CREATE TABLE applications (key TEXT PRIMARY KEY, status TEXT, data TEXT, updated REAL);")
        con.execute("INSERT INTO settings VALUES('min_score','55')")
        con.execute("INSERT INTO applications VALUES(?,?,?,?)", ("a" * 16, "submitted", json.dumps({"title": "T", "via": None}), 1.0))
        con.commit()
        con.close()
        db = DB(old)
        s = db.settings()
        self.assertEqual((s["min_score"], s["fallback_portals"], s["portal_pause_s"]), (55, ["naukri", "linkedin"], 20))
        a = db.app_get("a" * 16)
        self.assertEqual((a["status"], a.get("channel")), ("submitted", None))  # shown as "via company site"

    def test_portal_routes_cancel_and_busy(self):
        A = apply.ASSISTANT
        self.assertEqual([p["key"] for p in self.call("GET", "/api/portals")[1]["portals"]], ["naukri", "linkedin"])
        with mock.patch.object(A, "check_portals", return_value={"portals": []}), \
                mock.patch.object(A, "signin", return_value={"opened": "Naukri"}) as si:
            self.assertEqual(self.call("POST", "/api/portals/check"), (200, {"portals": []}))
            self.assertEqual(self.call("POST", "/api/portals/naukri/signin")[1], {"opened": "Naukri"})
            si.assert_called_once_with("naukri")
            self.db.upsert_postings([{"key": "b" * 16, "title": "T", "company": "C", "url": "https://x/1"}])
            seed(self.db, "b" * 16, "queued", queued_at=1)  # a run is active: the browser is not lent out
            self.assertEqual(self.call("POST", "/api/portals/linkedin/signin")[0], 409)
        code, d = self.call("POST", "/api/autoapply/cancel")
        self.assertEqual((code, d["cancelled"]), (200, 1))
        self.assertEqual(self.db.app_get("b" * 16)["status"], "cancelled")


class ProfilePermissions(unittest.TestCase):
    def test_browser_profile_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            a = apply.Assistant()
            a.pw = mock.Mock()
            a.pw.chromium.launch_persistent_context.side_effect = Exception("boom")
            prof = Path(d) / "profile"
            with mock.patch.object(config, "BROWSER_PROFILE", prof), \
                    mock.patch.dict("sys.modules", {"playwright": mock.Mock(), "playwright.sync_api": mock.Mock()}):
                with self.assertRaises(apply.ApplyError):
                    a._context()
            self.assertEqual(prof.stat().st_mode & 0o777, 0o700)


class RunnerGate(unittest.TestCase):
    def test_pause_countdown_and_idle_cancel(self):
        db = DB(":memory:")
        r = autoapply.Runner(db, assistant=object())
        self.assertEqual(r.cancel(), 0)  # nothing running: nothing to stop
        self.assertFalse(r.cancelled.is_set())
        self.assertFalse(r.busy())
        r.portal_gate("k" * 16)  # the first portal application of the session does not wait
        db.set_setting("portal_pause_s", 0.4)
        r.last_portal_end = time.time()
        t0 = time.time()
        r.portal_gate("k" * 16)
        self.assertGreaterEqual(time.time() - t0, 0.35)
        a = db.app_get("k" * 16)
        self.assertIn("rate limit", a["step"])
        self.assertGreater(a["wait_until"], t0)
        db.set_setting("portal_pause_s", 9999)
        self.assertEqual(r.pause_s(), config.PORTAL_PAUSE_RANGE[1])
        r.cancelled.set()
        with self.assertRaises(autoapply.Cancelled):
            r.portal_gate("k" * 16)


if __name__ == "__main__":
    unittest.main()
