"""HTTP server (standard library) bound to 127.0.0.1: JSON API + static UI."""
import csv
import io
import json
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ai, apply as applying, audit, autoapply, config, policy, details, portals, search, sources, unattended
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


def job_url_ok(url):
    url = url or ""
    if url.startswith("https://"):
        return True
    return config.ALLOW_LOCAL_HTTP and re.match(r"^http://(127\.0\.0\.1|localhost)[:/]", url) is not None


def settings_error(b):
    """(field, message) for an invalid job-portal setting, else None."""
    if "fallback_portals" in b:
        v = b["fallback_portals"]
        if not isinstance(v, list) or not all(k in portals.PORTALS for k in v):
            return "fallback_portals", "Choose job portals from the list."
    if "ai_answers" in b and not isinstance(b["ai_answers"], bool):
        return "ai_answers", "Choose on or off for Claude answers."
    if "portal_pause_s" in b:
        lo, hi = config.PORTAL_PAUSE_RANGE
        v = b["portal_pause_s"]
        if isinstance(v, bool) or not isinstance(v, (int, float, str)) or not re.fullmatch(r"\s*\d+\s*", str(v)) \
                or not lo <= int(v) <= hi:
            return "portal_pause_s", f"Enter a whole number of seconds from {lo} to {hi}."
    return None


def make_handler(db: DB):
    ai.configure(db)  # Claude usage log and daily budget
    runner = autoapply.Runner(db)
    ua = unattended.Unattended(db, runner)  # after the Runner: interrupted unattended jobs become Needs you

    class H(BaseHTTPRequestHandler):
        auto = runner
        unattended = ua
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

        def local_host(self):
            """Host must name this server on loopback, with this server's port (blocks DNS rebinding)."""
            host = (self.headers.get("Host") or "").lower()
            port = self.server.server_address[1]
            return host in (f"127.0.0.1:{port}", f"localhost:{port}") or (port == 80 and host in ("127.0.0.1", "localhost"))

        def same_origin(self):
            """Writes are accepted only from this app's own page (THREAT_MODEL.md T1-T3):
            - Host is this server (loopback + port);
            - Origin, when sent, is exactly this server's origin (another localhost port is another site);
            - Sec-Fetch-Site, when sent, is same-origin (or none: typed by the user);
            - the custom header X-RMJF-Request is present. Browsers can't add it cross-site without a CORS
              preflight, which this server never approves, so a forged form or fetch from any other origin fails."""
            if not self.local_host():
                return False
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            if origin and origin.lower() not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return False
            if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                return False
            return self.headers.get("X-RMJF-Request") == "1"

        # ---------- routing ----------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            if not self.local_host():
                return self.err(403, "Forbidden host")
            if path == "/healthz":  # liveness: the process serves requests
                return self.send(200, {"status": "ok", "version": config.VERSION})
            if path == "/readyz":  # readiness: the database answers
                try:
                    db.q("SELECT 1")
                except Exception:  # noqa: BLE001
                    return self.send(503, {"status": "not ready"})
                return self.send(200, {"status": "ready", "version": config.VERSION, "schema": db.schema_version()})
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
                last = db.last_search()
                prof = search.matching.effective_profile(db.get_profile(), json.loads(last["params"]) if last else {})
                for k, st in db.job_states().items():
                    if st == "saved":
                        p = db.posting(k)
                        if p:
                            p.update(search.matching.score(p, prof, db.get_setting("seniority", "mid")))
                            p["state"] = st
                            res.append(p)
                res.sort(key=lambda p: -p["score"])
                return self.send(200, {"jobs": res})
            m = re.fullmatch(r"/api/search/([0-9a-f]{12})", path)
            if m:
                run = search.RUNS.get(m.group(1))
                return self.send(200, run.snapshot()) if run else self.err(404, "Search not found")
            if path == "/api/applications":
                return self.send(200, runner.snapshot())
            if path == "/api/policy":
                return self.send(200, policy.view(db))
            if path == "/api/ai/usage":  # what Claude Code reported per call (list-price equivalent, not a bill)
                rows = db.q("SELECT day, COUNT(*) AS calls, SUM(ok) AS ok, ROUND(SUM(cost_usd), 4) AS cost_usd, "
                            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens FROM ai_usage "
                            "GROUP BY day ORDER BY day DESC LIMIT 60")
                return self.send(200, {"model": ai.MODEL, "prompt_version": ai.PROMPT_VERSION,
                                       "daily_budget_usd": ai.DAILY_BUDGET_USD, "spent_today_usd": ai.spent_today(),
                                       "days": [dict(r) for r in rows]})
            if path == "/api/audit":
                ok, n, head, broken = audit.verify(db)
                return self.send(200, {"intact": ok, "events": n, "head": head, "broken_at": broken,
                                       "recent": audit.events(db, limit=200)})
            if path == "/api/portals":
                return self.send(200, applying.ASSISTANT.portal_status())
            m = re.fullmatch(r"/api/applications/([0-9a-f]{16})(/proof)?", path)
            if m:
                a = db.app_get(m.group(1))
                if not a:
                    return self.err(404, "No application for this job yet.")
                if not m.group(2):
                    return self.send(200, a)
                proof = a.get("proof")
                folder = config.APPLICATIONS_DIR.resolve()
                from pathlib import Path
                if not proof or Path(proof).resolve().parent != folder or not Path(proof).is_file():
                    return self.err(404, "No proof screenshot.")
                return self.send(200, Path(proof).read_bytes(), "image/png")
            if path == "/api/apply/details":
                p = details.path()
                d = details.load()
                rf = details.resume_file()
                return self.send(200, {"exists": d is not None, "path": str(p), "fields": len(d or {}),
                                       "resume_file": rf.name if rf else None})
            if path == "/api/export.csv":
                return self.export()
            if path == "/api/unattended":
                return self.send(200, ua.status())
            if path in ("/api/unattended/summary", "/api/unattended/summary.csv"):
                day = (parse_qs(urlparse(self.path).query).get("day") or [unattended.day_of(ua.clock())])[0]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return self.err(400, "Choose a day as YYYY-MM-DD.", "day")
                if path.endswith(".csv"):
                    return self.send(200, ua.summary_csv(day), "text/csv; charset=utf-8",
                                     {"Content-Disposition": f'attachment; filename="unattended-{day}.csv"'})
                return self.send(200, ua.summary(day))
            if path == "/api/unattended/runs":
                return self.send(200, {"runs": ua.runs()})
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
                m = re.fullmatch(r"/api/jobs/([0-9a-f]{16})/apply", path)
                if m:
                    job = db.posting(m.group(1))
                    if not job:
                        return self.err(404, "Job not found. Run the search again.")
                    if not job_url_ok(job.get("url")):
                        return self.err(400, "This job has no application link.")
                    return self.apply_call(applying.ASSISTANT.open, dict(job, ai=db.settings().get("ai_answers", True)))
                if path == "/api/autoapply":
                    keys = self.body_json().get("keys") or []
                    if not isinstance(keys, list) or not all(isinstance(k, str) and re.fullmatch(r"[0-9a-f]{16}", k) for k in keys):
                        return self.err(400, "Choose the jobs to apply to.")
                    keys = [k for k in keys if job_url_ok((db.posting(k) or {}).get("url"))]
                    return self.send(202, {"queued": runner.enqueue(keys), **runner.snapshot()})
                if path == "/api/autoapply/cancel":
                    return self.send(200, {"cancelled": runner.cancel(), **runner.snapshot()})
                m = re.fullmatch(r"/api/portals/(check|naukri/signin|linkedin/signin)", path)
                if m:
                    if runner.busy():
                        return self.err(409, "Auto apply is using the browser. Try again when it has finished, or cancel it.")
                    if m.group(1) == "check":
                        return self.apply_call(applying.ASSISTANT.check_portals)
                    return self.apply_call(applying.ASSISTANT.signin, m.group(1).split("/")[0])
                if path == "/api/applications/answers":
                    answers = self.body_json().get("answers", {})
                    if not isinstance(answers, dict):
                        return self.err(400, "The request could not be read.")
                    answers = {str(k)[:20]: str(v)[:4000] for k, v in answers.items()}
                    return self.send(200, {**runner.answer(answers), **runner.snapshot()})
                if path == "/api/policy/preview":
                    out, errors = policy.preview_summary(db, self.body_json())
                    return self.send(400, {"error": errors[0]["message"], "errors": errors}) if errors else self.send(200, out)
                m = re.fullmatch(r"/api/applications/([0-9a-f]{16})/resolve", path)
                if m:
                    if not runner.resolve(m.group(1), self.body_json().get("outcome")):
                        return self.err(409, "Only an application whose outcome is unknown can be recorded this way.")
                    return self.send(200, runner.snapshot())
                m = re.fullmatch(r"/api/applications/([0-9a-f]{16})/(retry|mark)", path)
                if m:
                    ok = runner.retry(m.group(1)) if m.group(2) == "retry" else runner.mark_applied(m.group(1))
                    if not ok:
                        return self.err(409, "That application can't be changed now.")
                    return self.send(200, runner.snapshot())
                m = re.fullmatch(r"/api/apply/([0-9a-f]{12})/(answers|rescan)", path)
                if m:
                    if m.group(2) == "rescan":
                        return self.apply_call(applying.ASSISTANT.rescan, m.group(1))
                    answers = self.body_json().get("answers", {})
                    if not isinstance(answers, dict):
                        return self.err(400, "The request could not be read.")
                    answers = {str(k)[:40]: str(v)[:4000] for k, v in answers.items()}
                    return self.apply_call(applying.ASSISTANT.answer, m.group(1), answers)
                if path.startswith("/api/unattended/"):
                    return self.unattended_post(path)
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
                if path == "/api/policy":
                    out, errors = policy.update(db, b)
                    return self.send(400, {"error": errors[0]["message"], "errors": errors}) if errors else self.send(200, out)
                if path == "/api/settings":
                    bad = settings_error(b)  # validated first: on any error nothing is saved
                    if bad:
                        return self.err(400, bad[1], bad[0])
                    if "fallback_portals" in b:
                        db.set_setting("fallback_portals", [k for k in portals.PORTALS if k in b["fallback_portals"]])
                    if "ai_answers" in b:
                        db.set_setting("ai_answers", b["ai_answers"])
                    if "portal_pause_s" in b:
                        db.set_setting("portal_pause_s", int(b["portal_pause_s"]))
                    allowed = {"min_score": int, "max_age_days": int, "mode": str, "seniority": str,
                               "countries": list, "target_companies": list}
                    for k, typ in allowed.items():
                        if k in b:
                            db.set_setting(k, typ(b[k]))
                    return self.send(200, state(db))
                if path == "/api/unattended/settings":
                    out, errors = ua.update(b)
                    if errors:  # nothing is saved; every problem is listed with its field
                        return self.send(400, {"error": errors[0]["message"], "field": errors[0]["field"],
                                               "errors": errors})
                    return self.send(200, out)
                m = re.fullmatch(r"/api/unattended/searches/([0-9a-f]{12})", path)
                if m:
                    if not ua.set_search(m.group(1), isinstance(b, dict) and b.get("enabled") is True):
                        return self.err(404, "That saved search no longer exists.")
                    return self.send(200, ua.status())
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
                ua.wipe()  # stops any unattended run and removes the proof screenshots (S4)
                db.delete_all()
                return self.send(200, {"ok": True})
            m = re.fullmatch(r"/api/sources/(\d+)", path)
            if m:
                db.x("DELETE FROM sources WHERE id=?", (int(m.group(1)),))
                return self.send(200, {"sources": db.sources()})
            return self.err(404, "Not found")

        # ---------- handlers ----------
        def unattended_post(self, path):
            b = self.body_json()
            b = b if isinstance(b, dict) else {}
            if path == "/api/unattended/pause":
                ua.pause()
                return self.send(200, ua.status())
            if path == "/api/unattended/resume":
                if b.get("confirm") is not True:
                    return self.err(400, "Confirm that unattended mode should start applying again.", "confirm")
                if not ua.settings()["enabled"]:
                    return self.err(409, "Unattended mode is off. Set it up and turn it on first.")
                ua.resume()
                return self.send(200, ua.status())
            if path == "/api/unattended/off":
                ua.turn_off()
                return self.send(200, ua.status())
            if path in ("/api/unattended/run", "/api/unattended/preview"):
                if not ua.settings()["configured"]:
                    return self.err(409, "Set up unattended mode first.")
                preview = path.endswith("preview")
                run_id, why = ua.tick("preview" if preview else "manual", dry=True if preview else None)
                body = {"run_id": run_id, "skipped": why, **ua.status()}
                if why:
                    body["error"] = f"Not started: {why}."
                return self.send(409 if why else 202, body)
            m = re.fullmatch(r"/api/unattended/jobs/(\d+)/dismiss", path)
            if m:
                if not ua.dismiss(int(m.group(1))):
                    return self.err(404, "That item is not waiting for you.")
                return self.send(200, ua.status())
            return self.err(404, "Not found")

        def apply_call(self, fn, *args):
            try:
                return self.send(200, fn(*args))
            except policy.Blocked as e:
                return self.err(409, str(e))
            except applying.ApplyError as e:
                return self.err(422, str(e))
            except TimeoutError:
                return self.err(504, "The application page took too long. Check the browser window, then press Scan again.")
            except Exception as e:  # noqa: BLE001 - browser errors must reach the user, not kill the server
                sys.stderr.write(f"apply error: {e!r}\n")
                return self.err(500, "The browser step failed: " + str(e).splitlines()[0][:200] if str(e) else "The browser step failed.")

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
