#!/usr/bin/env bash
# Fails when tracked files contain likely secrets or real personal data patterns. Fictional test data
# ("Alex Sample", example.com, 9000000000) is allowed.
set -euo pipefail
cd "$(dirname "$0")/.."
pat='(sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|li_at=[A-Za-z0-9]|nauk_at=[A-Za-z0-9]|[A-Za-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail)\.com|/mnt/c/Users/)'
if git ls-files -z | xargs -0 grep -nIE "$pat" -- 2>/dev/null | grep -v '^tools/scan_secrets.sh:'; then
  echo "Possible secret or personal data found (above)."; exit 1
fi
echo "No secrets or personal data patterns in tracked files."
