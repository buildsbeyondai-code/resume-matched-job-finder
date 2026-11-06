# Migrating from 1.x to 2.0

1. Stop the app and pull or check out 2.0. Install the pinned dependencies: `.venv/bin/pip install -r requirements-lock.txt`.
2. Start it with `./run.sh`. The database is backed up to `data/backups/` and upgraded to schema 2 automatically.
3. **Choose what the app may do.** Settings → Application policy:
   - *Prepare for review*: the forms are filled, and you press Submit yourself.
   - *Submit automatically*: set the daily cap, channels, excluded employers and how long the permission lasts. Save, read the summary, then press "I allow this".
4. Unattended auto-apply runs only while *Submit automatically* is allowed. Otherwise each scheduled run is skipped, with the reason in the run log.
5. New statuses in Applications:
   - **Ready for your review:** the form is filled in the browser window; check it and press Submit there.
   - **Blocked by your policy:** for example the daily cap, an excluded employer, or a channel that isn't allowed.
   - **Outcome unknown:** check whether the application was sent, then press "It was sent" or "It was not sent". It is never retried automatically.
6. The API now requires the `X-RMJF-Request: 1` header on every write. The UI sends it; scripts that called the API must add it.

Rollback: see docs/OPERATIONS_AND_ROLLBACK.md.
