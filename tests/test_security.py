"""Request-forgery and DNS-rebinding defences of the local server (docs/THREAT_MODEL.md T1-T3), health endpoints,
and the versioned schema migration with its pre-upgrade backup."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from app import config, server
from app.db import DB, SCHEMA_VERSION


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DB(":memory:")
        cls.srv = server.make_server(cls.db, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, std=True):
        h = {"Content-Type": "application/json"}
        if std:
            h["X-RMJF-Request"] = "1"
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_writes_need_this_apps_origin_and_header(self):
        write = ("PUT", "/api/settings", {"min_score": 41})
        self.assertEqual(self.req(*write)[0], 200)
        cases = {
            "no custom header (a plain HTML form or simple fetch)": dict(std=False),
            "another localhost app on a different port": dict(headers={"Origin": "http://127.0.0.1:5057"}),
            "localhost with another port": dict(headers={"Origin": "http://localhost:1"}),
            "https lookalike": dict(headers={"Origin": f"https://127.0.0.1:{self.port}"}),
            "a remote site": dict(headers={"Origin": "https://evil.example"}),
            "null origin (sandboxed iframe, file://)": dict(headers={"Origin": "null"}),
            "browser says cross-site": dict(headers={"Sec-Fetch-Site": "cross-site"}),
            "browser says same-site (another port)": dict(headers={"Sec-Fetch-Site": "same-site"}),
            "DNS rebinding host": dict(headers={"Host": "attacker.example"}),
            "right host, wrong port": dict(headers={"Host": "127.0.0.1:1"}),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertEqual(self.req(*write, **kw)[0], 403)
        for method, path in (("POST", "/api/autoapply"), ("DELETE", "/api/data"), ("PUT", "/api/policy")):
            with self.subTest(path=path):
                self.assertEqual(self.req(method, path, {}, std=False)[0], 403)
        self.assertEqual(self.db.settings()["min_score"], 41)  # none of the forged writes changed anything
        ok = self.req(*write, headers={"Origin": f"http://localhost:{self.port}", "Host": f"localhost:{self.port}",
                                       "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(ok[0], 200)

    def test_reads_refuse_foreign_hosts(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "127.0.0.1:9"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    def test_health_endpoints(self):
        self.assertEqual(self.req("GET", "/healthz"), (200, {"status": "ok", "version": config.VERSION}))
        code, r = self.req("GET", "/readyz")
        self.assertEqual((code, r["status"], r["schema"]), (200, "ready", SCHEMA_VERSION))
        broken = DB(":memory:")
        srv = server.make_server(broken, port=0)
        real_q = broken.q

        def failing_q(sql, args=()):  # the database stops answering (never close a connection other threads use)
            if sql == "SELECT 1":
                raise sqlite3.OperationalError("disk I/O error")
            return real_q(sql, args)
        broken.q = failing_q
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/readyz")
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(r, timeout=10)
            self.assertEqual(e.exception.code, 503)
        finally:
            srv.shutdown()
            srv.server_close()


class Migration(unittest.TestCase):
    def test_upgrade_backs_up_then_migrates_and_new_db_is_not_backed_up(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "jobs.sqlite3")
            DB(p).conn.close()
            self.assertFalse(os.path.exists(os.path.join(t, "backups")))  # fresh database: nothing to protect
            c = sqlite3.connect(p)  # make it look like a 1.x database with data
            c.execute("INSERT INTO settings(key,value) VALUES('min_score','55')")
            c.execute("DROP TABLE submissions")
            c.execute("PRAGMA user_version=1")
            c.commit()
            c.close()
            d = DB(p)
            self.assertEqual(d.schema_version(), SCHEMA_VERSION)
            self.assertEqual(d.settings()["min_score"], 55)  # data kept
            d.q("SELECT * FROM submissions")
            (b,) = os.listdir(os.path.join(t, "backups"))
            old = sqlite3.connect(os.path.join(t, "backups", b))
            self.assertEqual(old.execute("PRAGMA user_version").fetchone()[0], 1)  # the backup is the 1.x file
            self.assertEqual(old.execute("SELECT value FROM settings WHERE key='min_score'").fetchone()[0], "55")
            old.close()
            self.assertIsNone(d.migrate())  # already current
            d.conn.close()


if __name__ == "__main__":
    unittest.main()
