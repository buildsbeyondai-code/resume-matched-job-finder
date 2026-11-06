# Threat model (STRIDE) — Resume-Matched Job Finder 2.0

Scope: the personal, single-user deployment. The app runs on the user's computer, bound to 127.0.0.1. It has a SQLite database, a Playwright Chromium profile, the Claude Code CLI and resume files in the user's own folder. The enterprise multi-user mode is a separate design: it is **not implemented** (ENTERPRISE_DEPLOYMENT.md) and is not covered by the controls below.

## Assets
| Asset | Where | Why it matters |
|---|---|---|
| Resume and `resume_details.md` (PII) | user's folder (`RMJF_JOBSEARCH_DIR`), never in git | identity, contact data, employment history |
| Saved answers (salary, visa, notice period) | `resume_details.md` | sensitive personal facts |
| Portal sessions (Naukri, LinkedIn cookies) | `data/browser-profile` (0700) | account takeover, actions in the user's name |
| Application state, consent, audit log | `data/jobfinder.sqlite3` | proof of what was sent where and with what permission |
| Proof screenshots | `data/applications` (0600 files) | may show PII |
| The authority to submit applications | `policy` setting + gate | an unwanted application can't be withdrawn |

## Trust boundaries and data flows
1. The browser UI → the local HTTP API on 127.0.0.1:PORT. Other local web pages and processes sit on the same machine.
2. App → job APIs (Greenhouse, Lever, SmartRecruiters, Ashby, Workable, Adzuna). HTTPS only, allow-listed hosts (`config.ALLOWED_HOSTS`).
3. App → employer and portal websites, through the Playwright browser. The content is untrusted and may be hostile.
4. App → the Claude Code CLI → Anthropic. Only minimised facts and the employer's questions are sent (CLAUDE_MODEL_INTEGRATION.md).

## Threats and controls
| ID | STRIDE | Threat | Control (code) | Test | Residual risk |
|---|---|---|---|---|---|
| T1 | Spoofing / Tampering | A web page on another site forges writes to the API (CSRF) | Writes need `X-RMJF-Request: 1`. A cross-origin page can't send it without a CORS preflight, which the server never approves. Origin must equal this server's exact origin, and `Sec-Fetch-Site` must be same-origin (`server.same_origin`) | `test_security.Server.test_writes_need_this_apps_origin_and_header` | none known |
| T2 | Spoofing | DNS rebinding (attacker.example resolving to 127.0.0.1) | `Host` must be `127.0.0.1:PORT` or `localhost:PORT` on every request (`local_host`) | same, plus `test_reads_refuse_foreign_hosts` | none known |
| T3 | Spoofing | Another app on another localhost port (for example a dev server) calls the API | The Origin port must match (before 2.0 any localhost port was accepted) | same test, "another localhost app" case | Native local processes (not browsers) can still call the API: single-user mode trusts the OS account. **No per-user authentication** |
| T4 | Elevation | An application is submitted without the user's permission | `policy.gate` runs before every click that may send an application (Submit, portal Apply, ambiguous Continue/Proceed/Save). It fails closed when no gate exists. The default is SEARCH_ONLY. Auto-submit needs a consent whose summary hash matches the current scope and that hasn't expired | `test_policy.*`, mutation tests | A site whose button text is neither Submit-like nor ambiguous could still send the application on a "Next" click. This is then recorded as `submit.ungated` in the audit log and counted as submitted (R1 below) |
| T5 | Tampering / Repudiation | Duplicate application (retry, repost, crash between click and confirmation, two workers) | Unique `submission_key` (employer+role+location); `BEGIN IMMEDIATE` claim; SUBMITTING/UNKNOWN keys can't be claimed again; crash recovery turns them into OUTCOME_UNKNOWN; OUTCOME_UNKNOWN is never re-queued | `test_policy.Claims`, `Runner`, `StateMachine`, `test_concurrent_claims_one_winner`, E2E `agg_dup` | Two genuinely different openings of the same title in the same city at the same employer share one key, so the second is blocked (safe direction) |
| T6 | Repudiation | No record of what was sent where | Hash-chained audit log: consent, every gate decision, claims, attempts (resume SHA-256, button, channel), outcomes. `/api/audit` verifies the chain | `test_policy.Audit` | Tamper-evident only: someone with write access to the DB can rebuild the chain. Export the head hash to detect this |
| T7 | Information disclosure | Resume or PII sent to Claude | Only numbered facts are sent. Contact data and sensitive fields are removed. The switch is in Settings (off = nothing sent). An empty working directory and no tools | `test_ai.Answer.test_minimisation…` | Resume lines can contain other personal details the regexes don't recognise (for example a home address written as prose) |
| T8 | Tampering | Prompt injection from job descriptions or form labels makes Claude answer falsely or leak facts | Employer text is fenced as untrusted and fence tags inside it are neutralised. Only listed question ids are accepted, each answer must cite existing fact ids with confidence ≥ 0.6, and answers fill only fields on the form. Claude has no tools and can't navigate | `test_ai…fenced…`, `…citations…` | The model may still be persuaded within a free-text answer. **Live injection eval NOT VERIFIED** (needs paid calls, NEEDS_USER) |
| T9 | Elevation | Employer page content steers the browser (malicious redirects, file exfiltration) | Only the resume file chosen by the app is uploaded. Navigation follows page links but the app never downloads or executes content. Portal hosts are browser-only. No model-generated scripts are run | existing apply tests | A malicious site can show anything in the visible browser. The user's portal cookies are in the same profile (R3) |
| T10 | Denial of service | Malicious resume (zip bomb, XML entities, PDF stream bombs) | DOCX: entry-count limit, streaming read capped at 50 MB, DOCTYPE/ENTITY refused. PDF: per-stream 20 MB and total 60 MB inflate caps, 20 pages through pypdf. Upload capped at 10 MB | `test_properties.Parsers` (300 mutated files plus bombs) | pypdf internals are not fuzzed beyond these inputs |
| T11 | Information disclosure | Errors leak paths or PII | User-facing errors are fixed sentences. The request log holds method and path only (no query or body) | existing server tests | — |
| T12 | Elevation | Account bans or terms violations on Naukri and LinkedIn | Opt-in channels, one job at a time with a pause, no CAPTCHA solving, no fingerprint or proxy evasion. Sign-in and CAPTCHA walls park the job | portal tests (mock only) | Automated use may still breach these portals' terms. **Live behaviour NOT VERIFIED** (PORTAL_POLICY_MATRIX.md) |
| T13 | Tampering | Mass submissions | Daily cap (policy), unattended daily cap, batch ≤ 25, per-company cooldown | `test_gate_rules`, unattended tests | — |

## Residual risks (accepted for the personal deployment, tracked in FINAL_INDEPENDENT_AUDIT.md)
- **R1:** A "Next"-like button that actually submits isn't gated in advance. It's detected after the fact and flagged (`submit.ungated`).
- **R2:** There's no authentication between local processes and the API. Any program running as the user can call it.
- **R3:** Employer pages run in the same Chromium profile that holds the portal sessions. The browser's site isolation is the only separation.
- **R4:** The audit log is tamper-evident, not tamper-proof.
- **R5:** No independent penetration test has been done.
