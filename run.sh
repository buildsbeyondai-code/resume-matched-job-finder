#!/usr/bin/env bash
# Start Resume-Matched Job Finder on http://127.0.0.1:5070 (standard library only; Python 3.11+).
set -euo pipefail
cd "$(dirname "$0")"
PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"
exec "$PY" -m app
