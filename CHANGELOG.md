# Changelog

## 2.0.0 — 2026-11-06 (major: breaking default)
**Breaking:** applications are no longer filled or sent until you choose a mode in Settings → Application policy. The default after upgrading is **Search only**.

### Safety: authorization and duplicates
- Automatic submission is allowed only on company websites by default. Naukri and LinkedIn must be added to the policy on purpose (their terms restrict automation).
- Application policy with three modes: Search only, Prepare for review, Submit automatically. Automatic submission needs you to confirm the exact scope (daily cap, channels, excluded employers, lifetime). The consent expires, and it ends when the scope changes.
- One deterministic gate before every click that may send an application. That covers Submit, a portal's Apply (one-click), and an ambiguous Continue, Proceed or Save. It fails closed.
- Submission keys (employer + role + location) are claimed in a transaction. The same job through another board is never sent twice, and neither is the same job retried.
- **Outcome unknown:** Submit without a confirmation, a failed click, a CAPTCHA after Submit, or a crash, cancel or restart during Submit. Never retried by itself; you record "It was sent" or "It was not sent".
- An enforced state machine for application statuses.
- A hash-chained audit log (consent, every gate decision, attempts with a resume checksum, outcomes). `GET /api/audit` verifies it.

### Security
- Writes accepted only from this app's page: exact origin including the port, the `X-RMJF-Request` header, `Sec-Fetch-Site`. Host checked on every request, including the port.
- Resume parsing: DOCX entry and size limits (streamed), XML DOCTYPE/ENTITY refused, a total PDF inflate cap. Fuzzing found and fixed a crash on corrupted DOCX files.

### Claude Opus 5.5
- Answers must cite numbered candidate facts and carry a confidence of at least 0.6, or they are discarded and the question is asked.
- Contact and sensitive fields are not sent. Employer text is fenced as untrusted (prompt injection).
- The model that ran is verified (no silent fallback). Retries with backoff and jitter. Daily budget. Usage log with reported cost (`/api/ai/usage`).

### Operations
- `/healthz`, `/readyz`, versioned schema migrations with an automatic pre-upgrade backup, `requirements-lock.txt`, CycloneDX SBOM (`tools/sbom.py`), CI workflow with SHA-pinned actions, secret scan, mutation tests (`tools/mutation.py`), benchmark (`tools/bench.py`), Dockerfile (not built).

### Also included since 1.x
- Unattended auto-apply (scheduled saved searches with guard rails), merged from ReuseFirst build 20261106-114538.
- Claude Opus 5.5 answers (D54).
- Naukri and LinkedIn last-resort channels.

## 1.x
Resume-ranked job search, company-site auto apply, portal fallback (see git history).
