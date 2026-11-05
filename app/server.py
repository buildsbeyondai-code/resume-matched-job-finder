"""HTTP server (standard library) bound to 127.0.0.1: JSON API + static UI."""
import csv
import io
import json
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config, search, sources
from .db import DB
from .resume import ResumeError, parse_resume
from .skills import canonical_skill

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def make_handler(db: DB):
    class H(BaseHTTPRequestHandler):
        server_version = "RMJF"

        def log_message(self, fmt, *args):  # never log query strings (may contain filters) or bodies
            sys.stderr.write("%s %s\n" % (self.command, urlparse(self.path).path))

        # ---------- helpers ----------
        def send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def err(self, code, msg, field=None):
            self.send(code, {"error": msg, "field": field})

        def body_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1024 * 1024:
                raise ValueError("Request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def same_origin(self):
            """Block cross-site and DNS-rebinding writes: Host must be local, Origin (if sent) must match."""
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return False
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                return False
            return True

        # ---------- routing ----------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return self.err(403, "Forbidden host")
            if path == "/" or path == "/index.html":
                return self.static("index.html")
            if path.startswith("/static/"):
                return self.static(path[len("/static/"):])
            if path == "/api/state":
                return self.send(200, state(db))
            if path == "/api/results":
                return self.send(200, search.latest_results(db) or {"jobs": [], "params": None})
            if path == "/api/saved":
                res = []
                for k, st in db.job_states().items():
                    if st == "saved":
                        p = db.posting(k)
                        if p:
                            p.update(search.matching.score(p, db.get_profile() or {}, db.get_setting("seniority", "mid")))
                            p["state"] = st
                            res.append(p)
                res.sort(key=lambda p: -p["score"])
                return self.send(200, {"jobs": res})
            m = re.fullmatch(r"/api/search/([0-9a-f]{12})", path)
            if m:
                run = search.RUNS.get(m.group(1))
                return self.send(200, run.snapshot()) if run else self.err(404, "Search not found")
            if path == "/api/export.csv":
                return self.export()
            return self.err(404, "Not found")

        def do_POST(self):
            if not self.same_origin():
                return self.err(403, "Cross-site request blocked")
            path = urlparse(self.path).path
            try:
                if path == "/api/resume":
                    return self.upload()
                if path == "/api/search":
                    if not db.sources(enabled_only=True):
                        return self.err(400, "No job sources are enabled. Enable one in Settings.")
                    run = search.start(db, self.body_json())
                    return self.send(202, {"id": run.id, "params": run.params})
                m = re.fullmatch(r"/api/search/([0-9a-f]{12})/cancel", path)
                if m:
                    run = search.RUNS.get(m.group(1))
                    if run:
                        run.cancel.set()
                    return self.send(200, {"ok": True})
                m = re.fullmatch(r"/api/jobs/([0-9a-f]{16})/state", path)
                if m:
                    st = self.body_json().get("state", "")
                    if st not in ("saved", "hidden", ""):
                        return self.err(400, "Unknown state")
                    db.set_job_state(m.group(1), st)
                    return self.send(200, {"ok": True, "state": st})
                if path == "/api/sources":
                    b = self.body_json()
                    kind, token = b.get("kind", ""), (b.get("token") or "").strip()
                    if kind not in sources.ADAPTERS:
                        return self.err(400, "Choose a source type.", "kind")
                    if kind != "adzuna" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", token):
                        return self.err(400, "Enter the board name using letters, numbers, dots, dashes or "
                                             "underscores (for example: cloudflare).", "token")
                    label = (b.get("label") or token or kind).strip()[:80]
                    db.x("INSERT INTO sources(kind,token,label,enabled) VALUES(?,?,?,1)", (kind, token, label))
                    return self.send(201, {"sources": db.sources()})
                return self.err(404, "Not found")
            except (ValueError, json.JSONDecodeError):
                return self.err(400, "The request could not be read.")

        def do_PUT(self):
            if not self.same_origin():
                return self.err(403, "Cross-site request blocked")
            path = urlparse(self.path).path
            try:
                b = self.body_json()
                if path == "/api/profile":
                    skills = [canonical_skill(s) for s in b.get("skills", []) if str(s).strip()][:100]
                    titles = [str(t).strip()[:80] for t in b.get("titles", []) if str(t).strip()][:20]
                    try:
                        years = float(b.get("years") or 0)
                    except (TypeError, ValueError):
                        return self.err(400, "Years of experience must be a number from 0 to 50.", "years")
                    if not 0 <= years <= 50:
                        return self.err(400, "Years of experience must be a number from 0 to 50.", "years")
                    if not skills:
                        return self.err(400, "Add at least one skill.", "skills")
                    db.save_profile(list(dict.fromkeys(skills)), titles, years)
                    return self.send(200, {"profile": db.get_profile()})
                if path == "/api/settings":
                    allowed = {"min_score": int, "max_age_days": int, "mode": str, "seniority": str,
                               "countries": list, "target_companies": list}
                    for k, typ in allowed.items():
                        if k in b:
                            db.set_setting(k, typ(b[k]))
                    return self.send(200, state(db))
                m = re.fullmatch(r"/api/sources/(\d+)", path)
                if m:
                    db.x("UPDATE sources SET enabled=? WHERE id=?", (1 if b.get("enabled") else 0, int(m.group(1))))
                    return self.send(200, {"sources": db.sources()})
                return self.err(404, "Not found")
            except (ValueError, json.JSONDecodeError, TypeError):
                return self.err(400, "The request could not be read.")

        def do_DELETE(self):
            if not self.same_origin():
                return self.err(403, "Cross-site request blocked")
            path = urlparse(self.path).path
            if path == "/api/data":
                db.delete_all()
                return self.send(200, {"ok": True})
            m = re.fullmatch(r"/api/sources/(\d+)", path)
            if m:
                db.x("DELETE FROM sources WHERE id=?", (int(m.group(1)),))
                return self.send(200, {"sources": db.sources()})
            return self.err(404, "Not found")

        # ---------- handlers ----------
        def upload(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > config.MAX_UPLOAD_BYTES:
                left = min(n, 64 * 1024 * 1024)  # drain (bounded) so the client receives the error
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return self.err(413, "The file is larger than 10 MB. Upload a smaller PDF or DOCX.", "resume")
            data = self.rfile.read(n)
            name = re.sub(r"[^\w .()-]", "", self.headers.get("X-Filename", "resume"))[:120]
            try:
                parsed = parse_resume(data, name)
            except ResumeError as e:
                return self.err(422, str(e), "resume")
            db.save_profile(parsed["skills"], parsed["titles"], parsed["years"], filename=name, raw_text=parsed["text"])
            return self.send(200, {"profile": db.get_profile()})

        def static(self, rel):
            p = (config.STATIC_DIR / rel).resolve()
            if config.STATIC_DIR.resolve() not in p.parents or not p.is_file():
                return self.err(404, "Not found")
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send(200, p.read_bytes(), ctype)

        def export(self):
            res = search.latest_results(db) or {"jobs": []}
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["score", "title", "company", "location", "mode", "posted", "matched_skills",
                        "missing_skills", "sources", "url"])
            for j in res["jobs"]:
                if j.get("state") == "hidden":
                    continue
                row = [j["score"], j["title"], j["company"], j["location"], j["mode"], j.get("posted", ""),
                       "; ".join(j["matched"]), "; ".join(j["missing"]),
                       "; ".join(s["name"] for s in j.get("sources", [])), j["url"]]
                # neutralise spreadsheet formula injection
                w.writerow(["'" + str(c) if str(c)[:1] in "=+-@" else c for c in row])
            self.send(200, buf.getvalue(), "text/csv; charset=utf-8",
                      {"Content-Disposition": 'attachment; filename="job-matches.csv"'})

    return H


def state(db):
    srcs = db.sources()
    adz = sources.ADAPTERS["adzuna"].configured()
    for s in srcs:
        s["configured"] = adz if s["kind"] == "adzuna" else True
    return {"profile": db.get_profile(), "settings": db.settings(), "sources": srcs,
            "has_results": db.last_search() is not None}


def make_server(db=None, port=None):
    db = db or DB()
    return ThreadingHTTPServer((config.HOST, port if port is not None else config.PORT), make_handler(db))


def main():
    config.load_dotenv()
    srv = make_server()
    print(f"Resume-Matched Job Finder running at http://{config.HOST}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
