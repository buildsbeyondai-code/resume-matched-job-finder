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

## 2.0 additions (application safety)
```
static/app.js ── Settings → Application policy (mode, limits, consent dialog), Applications (Outcome unknown actions)
app/server.py ── + /healthz, /readyz, /api/policy[/preview], /api/applications/<key>/resolve, /api/audit, /api/ai/usage
   ├─ app/autoapply.py  Runner: policy.can_start → apply.Assistant.auto(job with hooks)
   │                     hooks.before_submit → policy.gate → policy.claim (BEGIN IMMEDIATE) → audit
   │                     hooks.after_submit  → policy.move (SUBMITTED / NOT_SUBMITTED / OUTCOME_UNKNOWN)
   ├─ app/apply.py      browser worker (Playwright, one thread); _gate before Submit / portal Apply /
   │                     ambiguous Continue|Proceed|Save (fail closed); outcome rules after the click
   ├─ app/unattended.py scheduled runs; skipped unless the effective mode is AUTHORIZED_AUTO_SUBMIT
   ├─ app/policy.py     modes, consent bound to the scope-summary hash, gate, submission keys and claims
   ├─ app/states.py     application status state machine (enforced in db.app_set)
   ├─ app/audit.py      hash-chained audit log
   ├─ app/ai.py         Claude Opus 5.5 answers: cited facts, fenced employer text, model check, budget, usage log
   └─ app/db.py         + schema versioning (PRAGMA user_version) and pre-upgrade backup; tables submissions,
                          audit, ai_usage
```
See STATE_MACHINE_AND_IDEMPOTENCY.md, APPLICATION_AUTHORIZATION.md, CLAUDE_MODEL_INTEGRATION.md and THREAT_MODEL.md.
