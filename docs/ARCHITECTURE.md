# Architecture

```
Browser (static/index.html + app.js, textContent only)
   │ JSON over http://127.0.0.1:5070 (CSP, same-origin writes)
app/server.py ── routes: /api/state, /api/resume, /api/profile, /api/search[/id[/cancel]], /api/results,
   │              /api/jobs/<key>/state, /api/saved, /api/settings, /api/sources, /api/data, /api/export.csv
   ├─ app/resume.py   PDF/DOCX → text (local) → skills/titles/years (app/skills.py vocabulary)
   ├─ app/search.py   SearchRun: ThreadPoolExecutor per source, per-source status, cancel Event,
   │                  finalize → enrich → filter → dedupe → score → persist → "New" marking
   ├─ app/sources.py  Greenhouse / Lever / SmartRecruiters / Adzuna adapters; http_get = HTTPS allow-list,
   │                  10 s timeout, 2 retries with exponential backoff on 5xx/timeout, 1 h SQLite cache
   ├─ app/matching.py country/region + work-mode detection, filters, dedupe, 0-100 score
   └─ app/db.py       SQLite (WAL): profile, sources, settings, postings, searches, job_state, http_cache
```
Data flow: the resume text stays in the `profile` table. Outbound URLs contain only the board tokens and the user-entered filters (keywords, up to 3 skills, mode, countries), and only for Adzuna. Results are rebuilt from SQLite on load, so they can be viewed offline and after a restart.
