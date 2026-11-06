#!/usr/bin/env python3
"""Print a CycloneDX 1.5 SBOM for the pinned Python dependencies (requirements-lock.txt) and the app itself."""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

comps = []
for line in (ROOT / "requirements-lock.txt").read_text().splitlines():
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
    if m:
        comps.append({"type": "library", "name": m.group(1), "version": m.group(2),
                      "purl": f"pkg:pypi/{m.group(1).lower()}@{m.group(2)}"})
print(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
                  "metadata": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "component": {"type": "application", "name": "resume-matched-job-finder",
                                             "version": config.VERSION}},
                  "components": comps}, indent=1))
