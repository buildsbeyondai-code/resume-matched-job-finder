#!/usr/bin/env bash
# Full test suite with branch coverage. The apply feature (ai, apply, autoapply, details, direct, portals) must stay at 100%.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m coverage run -m unittest discover -s tests -t .
.venv/bin/python -m coverage report --include="app/ai.py,app/apply.py,app/autoapply.py,app/details.py,app/direct.py,app/portals.py" --fail-under=100
.venv/bin/python -m coverage report
