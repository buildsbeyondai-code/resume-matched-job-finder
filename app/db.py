"""SQLite storage (WAL) for profile, sources, searches, postings, saved/hidden state and HTTP cache."""
import json
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK (id=1), filename TEXT, raw_text TEXT,
  skills TEXT, titles TEXT, years REAL, updated REAL);
CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, token TEXT, label TEXT,
  enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS postings (key TEXT PRIMARY KEY, data TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY AUTOINCREMENT, params TEXT, created REAL,
  result_keys TEXT, failed TEXT);
CREATE TABLE IF NOT EXISTS job_state (key TEXT PRIMARY KEY, state TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS applications (key TEXT PRIMARY KEY, status TEXT, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, body TEXT, fetched REAL);
"""


class DB:
    def __init__(self, path=None):
        self.path = str(path or config.db_path())
        if self.path != ":memory:":
            from pathlib import Path
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        if not self.q("SELECT 1 FROM sources LIMIT 1"):
            self.reset_sources()
        self.prune()

    def q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # settings
    def get_setting(self, key, default=None):
        r = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r[0]["value"]) if r else default

    def set_setting(self, key, value):
        self.x("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))

    def settings(self):
        out = dict(config.DEFAULTS)
        for r in self.q("SELECT key,value FROM settings"):
            out[r["key"]] = json.loads(r["value"])
        return out

    # profile
    def get_profile(self):
        r = self.q("SELECT * FROM profile WHERE id=1")
        if not r:
            return None
        r = r[0]
        return {"filename": r["filename"], "skills": json.loads(r["skills"]), "titles": json.loads(r["titles"]),
                "years": r["years"], "updated": r["updated"], "text_chars": len(r["raw_text"] or "")}

    def get_profile_text(self):
        r = self.q("SELECT raw_text FROM profile WHERE id=1")
        return r[0]["raw_text"] if r else ""

    def save_profile(self, skills, titles, years, filename=None, raw_text=None):
        cur = self.q("SELECT filename, raw_text FROM profile WHERE id=1")
        if cur:
            filename = filename if filename is not None else cur[0]["filename"]
            raw_text = raw_text if raw_text is not None else cur[0]["raw_text"]
        self.x("INSERT OR REPLACE INTO profile(id,filename,raw_text,skills,titles,years,updated) VALUES(1,?,?,?,?,?,?)",
               (filename or "", raw_text or "", json.dumps(skills), json.dumps(titles), years, time.time()))

    # sources
    def reset_sources(self):
        self.x("DELETE FROM sources")
        for s in config.DEFAULT_SOURCES:
            self.x("INSERT INTO sources(kind,token,label,enabled) VALUES(?,?,?,?)",
                   (s["kind"], s["token"], s["label"], s["enabled"]))

    def sources(self, enabled_only=False):
        sql = "SELECT * FROM sources" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
        return [dict(r) for r in self.q(sql)]

    # postings / searches
    def upsert_postings(self, postings):
        now = time.time()
        new_keys = set()
        with self.lock:
            for p in postings:
                r = self.conn.execute("SELECT first_seen FROM postings WHERE key=?", (p["key"],)).fetchone()
                if r:
                    self.conn.execute("UPDATE postings SET data=?, last_seen=? WHERE key=?",
                                      (json.dumps(p), now, p["key"]))
                else:
                    new_keys.add(p["key"])
                    self.conn.execute("INSERT INTO postings(key,data,first_seen,last_seen) VALUES(?,?,?,?)",
                                      (p["key"], json.dumps(p), now, now))
            self.conn.commit()
        return new_keys

    def last_search(self, params_json=None):
        if params_json is None:
            r = self.q("SELECT * FROM searches ORDER BY id DESC LIMIT 1")
        else:
            r = self.q("SELECT * FROM searches WHERE params=? ORDER BY id DESC LIMIT 1", (params_json,))
        return dict(r[0]) if r else None

    def save_search(self, params_json, results, failed):
        return self.x("INSERT INTO searches(params,created,result_keys,failed) VALUES(?,?,?,?)",
                      (params_json, time.time(), json.dumps(results), json.dumps(failed)))

    def job_states(self):
        return {r["key"]: r["state"] for r in self.q("SELECT key,state FROM job_state")}

    def set_job_state(self, key, state):
        if state in (None, "", "none"):
            self.x("DELETE FROM job_state WHERE key=?", (key,))
        else:
            self.x("INSERT OR REPLACE INTO job_state(key,state,updated) VALUES(?,?,?)", (key, state, time.time()))

    def posting(self, key):
        r = self.q("SELECT data FROM postings WHERE key=?", (key,))
        return json.loads(r[0]["data"]) if r else None

    # applications (auto apply)
    def app_get(self, key):
        r = self.q("SELECT status, data, updated FROM applications WHERE key=?", (key,))
        if not r:
            return None
        d = json.loads(r[0]["data"])
        d.update(key=key, status=r[0]["status"], updated=r[0]["updated"])
        return d

    def app_set(self, key, status, **data):
        cur = self.app_get(key) or {}
        cur.update(data)
        for k in ("key", "status", "updated"):
            cur.pop(k, None)
        self.x("INSERT OR REPLACE INTO applications(key,status,data,updated) VALUES(?,?,?,?)",
               (key, status, json.dumps(cur), time.time()))
        return self.app_get(key)

    def app_list(self):
        out = []
        for r in self.q("SELECT key FROM applications ORDER BY updated DESC"):
            out.append(self.app_get(r["key"]))
        return out

    # cache
    def cache_get(self, url):
        r = self.q("SELECT body,fetched FROM http_cache WHERE url=?", (url,))
        if r and time.time() - r[0]["fetched"] < config.CACHE_TTL_S:
            return r[0]["body"]
        return None

    def cache_put(self, url, body):
        self.x("INSERT OR REPLACE INTO http_cache(url,body,fetched) VALUES(?,?,?)", (url, body, time.time()))

    def prune(self):
        cutoff = time.time() - config.PRUNE_DAYS * 86400
        saved = "SELECT key FROM job_state WHERE state='saved'"
        self.x(f"DELETE FROM postings WHERE last_seen < ? AND key NOT IN ({saved})", (cutoff,))
        self.x("DELETE FROM http_cache WHERE fetched < ?", (time.time() - config.CACHE_TTL_S,))

    def delete_all(self):
        with self.lock:
            for t in ("profile", "postings", "searches", "job_state", "http_cache", "settings", "applications"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.commit()
        self.reset_sources()
        with self.lock:
            self.conn.execute("VACUUM")
