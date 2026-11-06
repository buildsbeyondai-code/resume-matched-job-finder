# Privacy and data flow (2.0)

**Legal review required:** this document describes the software's behaviour. It isn't a legal assessment, and no DPDP (India) or GDPR certification is claimed.

## Personal data held
| Data | Location | Retention | Deletion |
|---|---|---|---|
| Resume file and `resume_details.md` (contact details, history, saved answers) | user's folder `RMJF_JOBSEARCH_DIR` (outside the repo) | until the user deletes it | the user's file manager |
| Parsed profile (skills, titles, years, raw text) | SQLite `profile` | until replaced or deleted | Settings → Delete all data (`DELETE /api/data`) |
| Applications, unattended run logs, audit log, Claude usage | SQLite | postings and cache pruned after 60 days; applications and audit kept | Delete all data. Note: deleting the audit log removes the record of consent |
| Proof screenshots | `data/applications/` (files 0600) | kept | Delete all data |
| Portal sessions (cookies) | `data/browser-profile/` (0700) | until expiry or sign-out | sign out on the portal, or delete the folder |
| Pre-upgrade database backups | `data/backups/` | kept | delete the files |

Nothing personal is committed to git. `.env` holds the folder path and any Adzuna keys and is git-ignored. Test fixtures use the fictional "Alex Sample".

## Where data goes
| Recipient | What | When | Control |
|---|---|---|---|
| Job APIs (allow-listed HTTPS hosts) | search keywords and filters only, never the resume | search | the source list in Settings |
| The employer's application site, or Naukri / LinkedIn | the answers the user's details give, the resume file, and the answers the user typed or approved | only in PREPARE (filled, not sent) or AUTHORIZED_AUTO_SUBMIT (sent) | Settings → Application policy. Every send is audited with a resume checksum |
| Anthropic, through Claude Code | the open form questions, the job description, and minimised facts (CLAUDE_MODEL_INTEGRATION.md) | only while Settings → AI answers is on | the switch. Anthropic's retention terms for the user's Claude account apply |

## Rights support
- **Access and export:** `GET /api/applications`, `GET /api/audit`, CSV export of unattended summaries, plus the files in the user's own folder.
- **Deletion:** Delete all data, plus deleting the folder and backups.
- **Consent:** automatic submission and AI answers are opt-in, can be withdrawn at any time and are recorded in the audit log.

## Logs
- The HTTP access log records only the method and path (no query strings, bodies or form values).
- Unattended logs record the job, company, score, source, status and reason.
- Audit payloads hold hashes, labels and ids, never resume text or answer values.
