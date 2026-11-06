"""Parallel search across configured sources with per-source progress, cancel, partial failure (M2, M5, M7)."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import matching, sources

RUNS = {}  # search id -> SearchRun (in memory, for progress polling)


def normalise_params(raw: dict, settings: dict) -> dict:
    def lst(v):
        if isinstance(v, str):
            v = v.split(",")
        return [x.strip() for x in (v or []) if str(x).strip()]
    mode = (raw.get("mode") or settings.get("mode") or "any").lower()
    if mode not in ("remote", "hybrid", "onsite", "any"):
        mode = "any"
    return {"countries": lst(raw.get("countries")), "mode": mode, "skills": lst(raw.get("skills")),
            "keywords": (raw.get("keywords") or "").strip()[:200], "companies": lst(raw.get("companies")),
            "max_age_days": int(raw.get("max_age_days") or settings.get("max_age_days") or 30)}


class SearchRun:
    def __init__(self, db, params, profile, level):
        self.id = uuid.uuid4().hex[:12]
        self.db, self.params, self.profile, self.level = db, params, profile, level
        self.cancel = threading.Event()
        self.started = time.time()
        self.status = {}  # source id -> {label, state, count, error}
        self.done = False
        self.cancelled = False
        self.result = None
        self.lock = threading.Lock()

    def _run_source(self, src):
        sid = str(src["id"])
        adapter = sources.ADAPTERS.get(src["kind"])
        with self.lock:
            self.status[sid]["state"] = "running"
        try:
            if adapter is None:
                raise sources.SourceError("Unknown source type")
            out = []
            for url in adapter.urls(src, self.params):
                body = sources.http_get(url, self.db, self.cancel)
                out.extend(adapter.parse(body, src))
            with self.lock:
                self.status[sid].update(state="done", count=len(out))
            return out
        except Exception as e:  # one source failing never fails the search
            msg = str(e) if isinstance(e, sources.SourceError) else f"Unexpected response ({type(e).__name__})"
            with self.lock:
                self.status[sid].update(state="cancelled" if self.cancel.is_set() else "failed",
                                        error=sources.redact(msg))
            return []

    def run(self):
        srcs = []
        for s in self.db.sources(enabled_only=True):
            adapter = sources.ADAPTERS.get(s["kind"])
            # A source that needs credentials which were never set up is skipped, not failed:
            # it is an optional source, so it must not mark results as incomplete.
            if adapter is not None and hasattr(adapter, "configured") and not adapter.configured():
                self.status[str(s["id"])] = {"id": s["id"], "label": s["label"], "state": "skipped", "count": 0,
                                             "error": "No API key set up (optional)"}
                continue
            srcs.append(s)
            self.status[str(s["id"])] = {"id": s["id"], "label": s["label"], "state": "queued", "count": 0, "error": ""}
        raw = []
        with ThreadPoolExecutor(max_workers=max(1, min(10, len(srcs)))) as ex:
            for part in ex.map(self._run_source, srcs):
                raw.extend(part)
        if self.cancel.is_set():
            self.cancelled = True
            self.done = True
            return
        self.result = finalize(self.db, raw, self.params, self.profile, self.level,
                               [v for v in self.status.values() if v["state"] == "failed"])
        self.done = True

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "done": self.done, "cancelled": self.cancelled,
                    "sources": list(self.status.values()), "elapsed": round(time.time() - self.started, 1)}


def finalize(db, raw, params, profile, level, failed):
    enriched = [matching.enrich(p) for p in raw]
    filtered = [p for p in enriched if matching.passes_search_filters(p, params)]
    merged = matching.dedupe(filtered)
    params_json = json.dumps(params, sort_keys=True)
    prev = db.last_search(params_json)
    prev_keys = set(json.loads(prev["result_keys"])) if prev else None
    for p in merged:
        p["countries"] = sorted(p["countries"])
        p.update(matching.score(p, matching.effective_profile(profile, params), level))
    merged.sort(key=lambda p: (-p["score"], p.get("posted") or ""), reverse=False)
    db.upsert_postings(merged)
    keys = [p["key"] for p in merged]
    db.save_search(params_json, keys, failed)
    for p in merged:
        p["is_new"] = prev_keys is not None and p["key"] not in prev_keys
    new_keys = [k for k in keys if prev_keys is not None and k not in prev_keys]
    db.set_setting("new_keys", new_keys)
    return {"params": params, "count": len(merged), "failed": failed}


def start(db, raw_params, sync=False):
    settings = db.settings()
    params = normalise_params(raw_params, settings)
    run = SearchRun(db, params, db.get_profile(), settings.get("seniority", "mid"))
    RUNS[run.id] = run
    if sync:
        run.run()
    else:
        threading.Thread(target=run.run, daemon=True).start()
    return run


def latest_results(db):
    """Rebuild the last search's results from SQLite (works offline and after restart)."""
    s = db.last_search()
    if not s:
        return None
    states = db.job_states()
    profile = matching.effective_profile(db.get_profile(), json.loads(s["params"]))
    level = db.get_setting("seniority", "mid")
    new_keys = set(db.get_setting("new_keys", []))
    jobs = []
    for k in json.loads(s["result_keys"]):
        p = db.posting(k)
        if not p:
            continue
        p.update(matching.score(p, profile, level))  # re-score if the profile was edited
        p["state"] = states.get(k, "")
        p["is_new"] = k in new_keys
        jobs.append(p)
    jobs.sort(key=lambda p: -p["score"])
    return {"params": json.loads(s["params"]), "created": s["created"], "failed": json.loads(s["failed"]),
            "jobs": jobs}
