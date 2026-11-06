# Operations and rollback (2.0, personal profile)

## Start, stop and health
- `./run.sh` serves on 127.0.0.1:5070 (`RMJF_PORT`). Stop it with Ctrl+C or by killing the process. A running auto-apply job is interrupted safely (see below).
- `curl -s http://127.0.0.1:5070/healthz` returns `{"status":"ok","version":"2.0.0"}`.
- `/readyz` checks the database and reports `schema`. It returns 503 if the database isn't usable.

## Upgrade from 1.x to 2.0
1. Stop the app.
2. Check out 2.0.
3. Start the app. On first start the database is copied with SQLite's online backup to `data/backups/jobfinder-schema1-<time>.sqlite3`. Schema 2 is then added in one transaction (new tables only; existing data is untouched).
4. The application policy starts as **SEARCH_ONLY**. To send applications again, choose a mode in Settings → Application policy. Automatic submission needs the confirmation dialog.

## Rollback to 1.x
1. Stop the app.
2. `git checkout <1.x commit>`.
3. Either restore the backup (`cp data/backups/jobfinder-schema1-*.sqlite3 data/jobfinder.sqlite3`), or keep the 2.0 file. 1.x ignores the extra tables, but `PRAGMA user_version` stays 2 and the 2.0 policy and audit data are kept for a later upgrade.
4. Start the app. **Warning:** 1.x has no policy gate. It submits whenever Auto apply is pressed.

Tested in `tests/test_security.Migration`: upgrade, data kept, backup is the 1.x file, migrate is idempotent.

## Crash and restart behaviour
- **Auto apply interrupted before Submit:** the job becomes "failed: Interrupted …" and Retry applies again.
- **Interrupted while Submit was being pressed** (the claim is still SUBMITTING): the job becomes **Outcome unknown**. It is never retried automatically; record it with "It was sent" or "It was not sent".
- **Unattended runs:** an `attempting` row becomes Needs you on restart, and the job isn't applied to again by itself.

## Backups and restore drill
- **Back up** `data/jobfinder.sqlite3` and `data/applications/` with the app stopped, or use `sqlite3 data/jobfinder.sqlite3 ".backup backup.sqlite3"` while it runs. The resume folder is the user's own (OneDrive).
- **Restore drill:**
  1. Stop the app.
  2. Copy the backup over `data/jobfinder.sqlite3`.
  3. Start the app.
  4. `GET /readyz` should report the schema.
  5. `GET /api/audit` should show `"intact": true`.
- **Drill status:** the automated part is covered by the migration test. A manual drill on the user's real data is **NOT VERIFIED**.

## Deletion
- Settings → Delete all data clears the database tables and proof screenshots, and stops unattended mode.
- Separately, delete `data/backups/`, `data/browser-profile/` (portal sessions) and the job-search folder if wanted.

## Monitoring
- Applications tab: statuses, Outcome unknown, Ready for review.
- `/api/audit`: chain status plus recent events.
- `/api/ai/usage`: Claude calls, failures and reported cost per day.
- There are no external metrics or traces in the personal profile (no PII leaves the machine through telemetry).

## Outage behaviour
| Dependency down | Behaviour |
|---|---|
| Claude / Anthropic | the questions are asked of the user (the reason is shown); transient errors are retried twice |
| A job API | that source is shown as failed in search progress; the other sources still return |
| Employer site or portal | the job fails or goes to Needs you with the reason. Retry is manual. Never re-submitted after a timeout during Submit |
| Database | `/readyz` returns 503; writes fail with an error |
| Browser cannot start | Apply returns the install hint |
