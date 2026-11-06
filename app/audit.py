"""Append-only, hash-chained audit log (table `audit`, schema 2).

Each event stores kind, job key, a JSON payload and hash = sha256(prev_hash + canonical event). Changing,
deleting or reordering any earlier row breaks `verify()`. This is tamper-evident, not tamper-proof: someone with
write access to the database file can rebuild the whole chain. Export the head hash (`/api/audit`) to detect that.

Payloads never hold secrets, cookies, resume text or answer values: CV identity is a SHA-256 checksum and each
answer is its field label plus the SHA-256 of the value (docs/PRIVACY_AND_DATA_FLOW.md).
"""
import hashlib
import json
import time

GENESIS = "0" * 64


def _digest(prev, ts, kind, job_key, data):
    body = json.dumps({"ts": ts, "kind": kind, "job_key": job_key, "data": data}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()


def record(db, kind, job_key=None, data=None):
    data = data or {}
    with db.lock:
        r = db.conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = r["hash"] if r else GENESIS
        ts = round(time.time(), 6)
        h = _digest(prev, ts, kind, job_key, data)
        db.conn.execute("INSERT INTO audit(ts, kind, job_key, data, prev_hash, hash) VALUES(?,?,?,?,?,?)",
                        (ts, kind, job_key, json.dumps(data, sort_keys=True), prev, h))
        db.conn.commit()
    return h


def events(db, job_key=None, limit=500):
    sql, args = "SELECT * FROM audit", ()
    if job_key:
        sql, args = sql + " WHERE job_key=?", (job_key,)
    rows = db.q(sql + " ORDER BY seq DESC LIMIT ?", args + (limit,))
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def verify(db):
    """(ok, number of events, head hash, first broken seq or None)."""
    prev, n = GENESIS, 0
    for r in db.q("SELECT * FROM audit ORDER BY seq"):
        n += 1
        if r["prev_hash"] != prev or _digest(prev, r["ts"], r["kind"], r["job_key"], json.loads(r["data"])) != r["hash"]:
            return False, n, prev, r["seq"]
        prev = r["hash"]
    return True, n, prev, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(v):
    return hashlib.sha256(str(v).encode()).hexdigest()
