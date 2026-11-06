"""Integration / end-to-end API journeys against the real HTTP server with a mocked network (J1-J5, M2-M8)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from app import search, sources
from app.db import DB
from app.server import make_server
from tests.helpers import RESUME_LINES, FakeNet, fixture, make_pdf


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def req(self, method, path, body=None, headers=None, raw=False):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b or b"{}")), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbfile = os.path.join(self.tmp.name, "t.sqlite3")
        self.start()
        self.db.x("DELETE FROM sources")
        for kind, token, label in [("greenhouse", "examplecorp", "ExampleCorp (Greenhouse)"),
                                   ("lever", "examplecorp", "ExampleCorp (Lever)"),
                                   ("smartrecruiters", "ExampleGroup", "Example Group (SmartRecruiters)")]:
            self.db.x("INSERT INTO sources(kind,token,label,enabled) VALUES(?,?,?,1)", (kind, token, label))
        self.net = FakeNet({"greenhouse": fixture("greenhouse_jobs.json"), "lever": fixture("lever_postings.json"),
                            "smartrecruiters": fixture("smartrecruiters_postings.json")})
        self.orig = sources.FETCH
        sources.FETCH = self.net

    def start(self):
        self.db = DB(self.dbfile)
        self.srv = make_server(self.db, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.c = Client(self.srv.server_address[1])

    def stop(self):
        self.srv.shutdown(); self.srv.server_close(); self.db.conn.close()

    def tearDown(self):
        sources.FETCH = self.orig
        self.stop()
        self.tmp.cleanup()

    def upload(self, data=None, name="cv.pdf"):
        return self.c.req("POST", "/api/resume", data or make_pdf(), {"X-Filename": name, "Content-Type": "application/pdf"})

    def search(self, params):
        code, r, _ = self.c.req("POST", "/api/search", params)
        self.assertEqual(code, 202, r)
        for _ in range(200):
            code, s, _ = self.c.req("GET", f"/api/search/{r['id']}")
            if s["done"]:
                break
            time.sleep(0.05)
        self.assertTrue(s["done"])
        return s, self.c.req("GET", "/api/results")[1]


class Journeys(AppTestCase):
    def test_j1_upload_edit_profile(self):
        self.assertIsNone(self.c.req("GET", "/api/state")[1]["profile"])  # first-run empty state
        code, r, _ = self.upload()
        self.assertEqual(code, 200)
        self.assertIn("Python", r["profile"]["skills"])
        self.assertNotIn("raw_text", r["profile"])
        code, r, _ = self.c.req("PUT", "/api/profile", {"skills": ["python", "Go"], "titles": ["Engineer"], "years": 4})
        self.assertEqual(r["profile"]["skills"], ["Python", "Go"])
        code, r, _ = self.c.req("PUT", "/api/profile", {"skills": ["Python"], "years": "abc"})
        self.assertEqual((code, r["field"]), (400, "years"))
        code, r, _ = self.c.req("PUT", "/api/profile", {"skills": [], "years": 3})
        self.assertEqual((code, r["field"]), (400, "skills"))

    def test_j1_upload_errors(self):
        code, r, _ = self.upload(b"%PDF-" + b"x" * (10 * 1024 * 1024 + 10))
        self.assertEqual(code, 413); self.assertIn("10 MB", r["error"]); self.assertEqual(r["field"], "resume")
        code, r, _ = self.upload(b"not a resume at all", "cv.pdf")
        self.assertEqual(code, 422); self.assertIn("PDF or DOCX", r["error"])

    def test_j2_j3_m2_m4_m5_m6_search_rank_dedupe(self):
        self.upload()
        self.c.req("PUT", "/api/profile", {"skills": ["Python", "AWS", "SQL"], "titles": ["Backend Engineer"], "years": 5})
        s, res = self.search({"countries": ["Germany"], "mode": "remote", "skills": ["Python"]})
        jobs = res["jobs"]
        self.assertGreater(len(jobs), 0)
        for j in jobs:  # M2
            self.assertEqual(j["mode"], "remote")
            self.assertTrue("de" in j["countries"] or "Germany" in j["location"], j["location"])
        scores = [j["score"] for j in jobs]
        self.assertEqual(scores, sorted(scores, reverse=True))  # M4 order
        top = next(j for j in jobs if j["title"] == "Backend Engineer (Python)")
        self.assertGreaterEqual(top["score"], 70)
        self.assertEqual(sorted(top["matched"]), ["AWS", "Python", "SQL"])
        self.assertEqual(sorted(s["name"] for s in top["sources"]), ["Greenhouse", "Lever"])  # M5
        self.assertEqual(sum(1 for j in jobs if j["title"] == "Backend Engineer (Python)"), 1)
        self.assertTrue(all(j["url"].startswith("https://") for j in jobs))  # M6
        self.assertIn("matched", top); self.assertIn("missing", top)

    def test_m2_empty(self):
        _, res = self.search({"countries": ["Germany"], "mode": "remote", "keywords": "zzznomatch"})
        self.assertEqual(res["jobs"], [])

    def test_m7_partial_failure(self):
        self.net.routes["lever"] = urllib.error.URLError("timed out")
        sources_sleep = sources.time.sleep
        sources.time.sleep = lambda s: None
        try:
            s, res = self.search({"countries": ["Germany"], "mode": "remote"})
        finally:
            sources.time.sleep = sources_sleep
        states = {x["label"]: x["state"] for x in s["sources"]}
        self.assertEqual(states["ExampleCorp (Lever)"], "failed")
        self.assertEqual(states["ExampleCorp (Greenhouse)"], "done")
        self.assertGreater(len(res["jobs"]), 0)
        self.assertEqual([f["label"] for f in res["failed"]], ["ExampleCorp (Lever)"])

    def test_unconfigured_adzuna_is_skipped_not_failed(self):
        old = os.environ.pop("ADZUNA_APP_ID", None), os.environ.pop("ADZUNA_APP_KEY", None)
        self.db.x("INSERT INTO sources(kind,token,label,enabled) VALUES('adzuna','','Adzuna',1)")
        try:
            s, res = self.search({"countries": ["Germany"], "mode": "remote"})
        finally:
            if old[0]: os.environ["ADZUNA_APP_ID"] = old[0]
            if old[1]: os.environ["ADZUNA_APP_KEY"] = old[1]
        states = {x["label"]: x["state"] for x in s["sources"]}
        self.assertEqual(states["Adzuna"], "skipped")
        self.assertEqual(res["failed"], [])  # no "results may be incomplete" banner
        self.assertGreater(len(res["jobs"]), 0)

    def test_cancel(self):
        gate = threading.Event()
        def slow(url, timeout):
            gate.wait(2); return fixture("greenhouse_jobs.json")
        sources.FETCH = slow
        code, r, _ = self.c.req("POST", "/api/search", {"countries": ["Germany"]})
        self.c.req("POST", f"/api/search/{r['id']}/cancel")
        gate.set()
        for _ in range(60):
            s = self.c.req("GET", f"/api/search/{r['id']}")[1]
            if s["done"]:
                break
            time.sleep(0.05)
        self.assertTrue(s["cancelled"])

    def test_m3_m8_only_allowlisted_and_no_resume_text(self):
        self.upload()
        self.search({"countries": ["Germany"], "mode": "remote", "skills": ["Python"]})
        self.assertTrue(self.net.requests)
        for url in self.net.requests:
            sources.check_allowed(url)  # raises if not allow-listed
            for line in RESUME_LINES:
                self.assertNotIn(line.lower()[:30], url.lower())
            self.assertNotIn("7f3a9c", url)

    def test_m8_persistence_after_restart_and_j4_j5(self):
        self.upload()
        _, res = self.search({"countries": ["Germany"], "mode": "remote"})
        key = res["jobs"][0]["key"]
        self.c.req("POST", f"/api/jobs/{key}/state", {"state": "saved"})
        hidden = res["jobs"][-1]["key"]
        self.c.req("POST", f"/api/jobs/{hidden}/state", {"state": "hidden"})
        self.stop(); self.start()  # restart against the same SQLite file
        st = self.c.req("GET", "/api/state")[1]
        self.assertIn("Python", st["profile"]["skills"])
        self.assertEqual([j["key"] for j in self.c.req("GET", "/api/saved")[1]["jobs"]], [key])
        res2 = self.c.req("GET", "/api/results")[1]
        self.assertEqual({j["key"]: j["state"] for j in res2["jobs"]}[hidden], "hidden")
        self.c.req("POST", f"/api/jobs/{hidden}/state", {"state": ""})  # undo hide
        self.assertEqual({j["key"]: j["state"] for j in self.c.req("GET", "/api/results")[1]["jobs"]}[hidden], "")
        # J5 refresh: a new posting appears and is marked New
        data = json.loads(fixture("lever_postings.json"))
        data.append({**data[0], "id": "zzz-999", "text": "Platform Engineer", "hostedUrl": "https://jobs.lever.co/e/zzz"})
        self.net.routes["lever"] = json.dumps(data)
        self.db.x("DELETE FROM http_cache")
        _, res3 = self.search(res["params"])
        new = [j["title"] for j in res3["jobs"] if j["is_new"]]
        self.assertEqual(new, ["Platform Engineer"])

    def test_identical_query_cached(self):
        self.search({"countries": ["Germany"]})
        n = len(self.net.requests)
        self.search({"countries": ["Germany"]})
        self.assertEqual(len(self.net.requests), n)

    def test_delete_all_data(self):
        self.upload()
        code, _, _ = self.c.req("DELETE", "/api/data")
        self.assertEqual(code, 200)
        self.assertIsNone(self.c.req("GET", "/api/state")[1]["profile"])
        self.assertEqual(self.db.get_profile_text(), "")

    def test_security_headers_origin_and_host(self):
        code, r, h = self.c.req("GET", "/api/state")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        code, r, _ = self.c.req("DELETE", "/api/data", headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        code, r, _ = self.c.req("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)
        code, r, _ = self.c.req("GET", "/static/../app/config.py")
        self.assertEqual(code, 404)
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_settings_never_expose_keys(self):
        os.environ["ADZUNA_APP_KEY"] = "topsecretvalue"
        try:
            code, r, _ = self.c.req("GET", "/api/state", raw=True)
            self.assertNotIn(b"topsecretvalue", r)
        finally:
            del os.environ["ADZUNA_APP_KEY"]

    def test_sources_crud_and_csv(self):
        code, r, _ = self.c.req("POST", "/api/sources", {"kind": "lever", "token": "bad token!"})
        self.assertEqual((code, r["field"]), (400, "token"))
        code, r, _ = self.c.req("POST", "/api/sources", {"kind": "greenhouse", "token": "newboard", "label": "New"})
        self.assertEqual(code, 201)
        sid = r["sources"][-1]["id"]
        self.c.req("PUT", f"/api/sources/{sid}", {"enabled": False})
        self.assertEqual(self.db.sources()[-1]["enabled"], 0)
        self.c.req("DELETE", f"/api/sources/{sid}")
        self.search({"countries": ["Germany"], "mode": "remote"})
        code, body, h = self.c.req("GET", "/api/export.csv", raw=True)
        self.assertEqual(code, 200)
        self.assertTrue(body.decode().startswith("score,title"))

    def test_ui_served(self):
        code, body, h = self.c.req("GET", "/", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"Resume-Matched Job Finder", body)
        for f in ("app.css", "app.js", "theme.js"):
            self.assertEqual(self.c.req("GET", f"/static/{f}", raw=True)[0], 200)


if __name__ == "__main__":
    unittest.main()
