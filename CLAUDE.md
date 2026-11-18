# CLAUDE.md
- Start with `docs/LEARNINGS.md` (decisions, traps, task state) before changing anything.
- Standard-library-only Python web app: `app/` (server.py routes, search.py orchestration, sources.py adapters + allow-listed HTTP, matching.py scoring/dedupe/filters, resume.py PDF/DOCX parsing, db.py SQLite), `static/` (index.html, app.css tokens, app.js).
- Run: `./run.sh` (127.0.0.1:5070). Test: `python3 -m unittest discover -s tests -t .`.
- Rules: outbound hosts only via `config.ALLOWED_HOSTS` + `sources.http_get`; never send resume text out, except to Claude (app/ai.py) while Settings "AI answers" is on (D54); never use innerHTML; keep theme tokens passing tests/test_ui_static.py contrast checks; record choices in docs/DECISIONS.md.
- `upstream/` holds reviewed reuse candidates and is git-ignored.
- 2.0 safety rules: every click that may send an application goes through `policy.gate` and `policy.claim` (fail closed); never re-queue `outcome_unknown`; statuses change only through `db.app_set` (state machine in app/states.py); tests seed fixtures with `tests.helpers.seed` and call `tests.helpers.authorize` when they expect submissions; tests never reach real employers, portals or Claude.
- Release gates: `tools/coverage.sh` (100% on ai/apply/audit/autoapply/details/direct/policy/portals/states/unattended), `tools/mutation.py` (all mutants killed), `tools/scan_secrets.sh`.
