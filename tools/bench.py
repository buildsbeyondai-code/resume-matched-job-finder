#!/usr/bin/env python3
"""Local performance benchmark (no third-party traffic): API latency p50/p95/p99 under concurrent clients,
ranking throughput and memory. Uses an in-memory database seeded with synthetic postings.

Usage: .venv/bin/python tools/bench.py [--postings 2000] [--requests 400] [--clients 8]
"""
import argparse
import contextlib
import io
import json
import resource
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import matching, server  # noqa: E402
from app.db import DB  # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--postings", type=int, default=2000)
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--clients", type=int, default=8)
    a = ap.parse_args()
    db = DB(":memory:")
    words = "Python SQL AWS Docker Kubernetes React Java leadership program manager AI".split()
    posts = [{"key": f"{i:016x}", "source": "Bench", "source_id": str(i), "title": f"Engineer {i}",
              "company": f"Co {i % 97}", "location": "Remote", "url": f"https://example.test/{i}", "posted": "2026-11-01",
              "description": " ".join(words[(i + k) % len(words)] for k in range(60)), "countries": [],
              "mode": "remote", "skills": []} for i in range(a.postings)]
    db.upsert_postings(posts)
    prof = {"skills": ["Python", "SQL", "AWS", "Docker"], "titles": ["Engineer"], "years": 8}
    t = time.perf_counter()
    for p in posts:
        matching.score(p, prof)
    rank_s = time.perf_counter() - t
    srv = server.make_server(db, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    paths = ["/api/state", "/api/applications", "/api/policy", "/healthz", "/readyz"]

    def one(i):
        s = time.perf_counter()
        with urllib.request.urlopen(base + paths[i % len(paths)], timeout=30) as r:
            r.read()
        return (time.perf_counter() - s) * 1000
    t = time.perf_counter()
    with contextlib.redirect_stderr(io.StringIO()), ThreadPoolExecutor(a.clients) as ex:  # quiet access log
        lat = list(ex.map(one, range(a.requests)))
    wall = time.perf_counter() - t
    srv.shutdown()
    print(json.dumps({
        "postings": a.postings, "ranking_s": round(rank_s, 3), "ranking_per_s": round(a.postings / rank_s),
        "requests": a.requests, "clients": a.clients, "throughput_rps": round(a.requests / wall, 1),
        "latency_ms": {"p50": round(pct(lat, 50), 2), "p95": round(pct(lat, 95), 2), "p99": round(pct(lat, 99), 2),
                       "mean": round(statistics.mean(lat), 2)},
        "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}, indent=1))


if __name__ == "__main__":
    main()
