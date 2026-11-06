#!/usr/bin/env python3
"""Mutation tests for the safety invariants (docs/TEST_MATRIX.md).

Each mutant breaks one safety rule in a throw-away copy of the app (authorization, duplicate protection,
OUTCOME_UNKNOWN handling, request-origin checks, consent expiry, AI citation and redaction, audit verification)
and runs the fast safety tests against it. A mutant must make at least one test fail ("killed"); a surviving
mutant means the tests would not notice that rule breaking. Exit code 1 if any mutant survives.

Usage: .venv/bin/python tools/mutation.py   (about 5-8 minutes; no network, no browser)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ["tests.test_policy", "tests.test_security", "tests.test_ai", "tests.test_properties"]

MUTANTS = [
    ("gate allows SEARCH_ONLY", "app/policy.py", "    if mode == SEARCH_ONLY:\n        reason =", "    if False:\n        reason ="),
    ("claim ignores earlier attempts", "app/policy.py", 'if row and row["state"] in BLOCKING:', "if False:"),
    ("unknown outcome can be re-queued", "app/states.py", '"outcome_unknown": {"applied_manually", "failed"}',
     '"outcome_unknown": {"applied_manually", "failed", "queued", "running"}'),
    ("writes need no custom header", "app/server.py", 'return self.headers.get("X-RMJF-Request") == "1"', "return True"),
    ("other localhost ports accepted", "app/server.py",
     'if origin and origin.lower() not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):',
     'if origin and "127.0.0.1" not in origin and "localhost" not in origin:'),
    ("Submit is not gated", "app/apply.py",
     'risky = b["kind"] == "submit" or AMBIGUOUS_RE.match(b["text"] or "") is not None', "risky = False"),
    ("no gate means allowed", "app/apply.py", "        if fn is None:\n            raise policy.Blocked(",
     "        if fn is None:\n            return\n            raise policy.Blocked("),
    ("consent never expires", "app/policy.py", 'c.get("expires", 0) > (now or time.time())', "True"),
    ("consent ignores scope changes", "app/policy.py",
     'and c.get("summary_hash") == summary_hash(summary(p)))', ")"),
    ("crash after Submit is retryable", "app/autoapply.py",
     "if st not in (policy.SUBMITTING, policy.CONFIRMING):", "if True:"),
    ("uncited AI answers accepted", "app/ai.py", "cited = bool(ids) and all(i in fact_map for i in ids)", "cited = True"),
    ("wrong model accepted", "app/ai.py", "if ran and MODEL not in ran:", "if False:"),
    ("contact details sent to Claude", "app/ai.py",
     'return PHONE.sub("[phone removed]", EMAIL.sub("[email removed]", text or ""))', 'return text or ""'),
    ("employer text not fenced", "app/ai.py", 'FENCE.sub("[tag removed]", text)', "text"),
    ("audit chain not checked", "app/audit.py", 'return False, n, prev, r["seq"]', "pass"),
    ("daily cap ignored", "app/policy.py", 'elif submitted_today(db, now) >= p["daily_cap"]:', "elif False:"),
    ("excluded employers allowed", "app/policy.py", 'elif excluded(job.get("company"), p["excluded_companies"]):',
     "elif False:"),
]


def run(tmp):
    p = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "unittest", *TESTS], cwd=tmp,
                       capture_output=True, text=True, timeout=900)
    return p.returncode


def main():
    with tempfile.TemporaryDirectory(prefix="rmjf-mut-") as tmp:
        for d in ("app", "tests", "static"):
            shutil.copytree(ROOT / d, Path(tmp) / d, ignore=shutil.ignore_patterns("__pycache__"))
        if run(tmp) != 0:
            print("Baseline tests fail; fix them before mutation testing.")
            return 2
        survived = []
        for name, rel, old, new in MUTANTS:
            f = Path(tmp) / rel
            src = f.read_text()
            if src.count(old) != 1:
                print(f"STALE   {name}: pattern not found once in {rel}")
                survived.append(name)
                continue
            f.write_text(src.replace(old, new))
            killed = run(tmp) != 0
            f.write_text(src)
            print(f"{'KILLED ' if killed else 'SURVIVED'} {name}")
            if not killed:
                survived.append(name)
        print(f"\n{len(MUTANTS) - len(survived)}/{len(MUTANTS)} mutants killed")
        return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
