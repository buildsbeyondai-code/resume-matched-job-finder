# Deployment profiles (2.0)

## Profile A: personal, single-user (implemented, the only supported profile)
- One OS user and one computer. The server binds to `127.0.0.1` only. The data lives under `data/` and the user's own job-search folder.
- **Trust boundary:** the OS account. Any local process running as the user can call the API (THREAT_MODEL.md R2).
- **Packaging:** `./run.sh` with a virtualenv built from `requirements-lock.txt`. A `Dockerfile` exists for the same profile, but it **has not been built or run (NOT VERIFIED)**.
- **Health:** `/healthz` (liveness) and `/readyz` (database check and schema version).

## Profile B: multi-user enterprise (designed, NOT IMPLEMENTED)
The 2.0 server is **not** multi-tenant and must not be exposed on a network or shared between people. A real enterprise mode needs the following. None of it exists in the code today.

| Need | Design direction (to be validated in a design review) |
|---|---|
| Identity | OIDC/SAML SSO through a maintained identity provider; no local passwords |
| Authorization | roles (candidate, recruiter-assistant, admin); every query scoped by `tenant_id` and `user_id`, enforced in one data-access layer |
| Storage | PostgreSQL with row-level security per tenant instead of SQLite; object storage for resumes and proofs with per-tenant keys |
| Secrets | the cloud KMS or vault; portal sessions encrypted at rest and bound to one user |
| Workers | a queue with leases, bounded concurrency, a dead-letter queue, one isolated browser container per user session (no shared profiles, no host mounts), and egress allow-lists |
| Audit | an append-only store (WORM bucket or a ledger table with external anchoring) |
| Policy | the organisation's policy layered over the per-user application policy |
| Privacy | DPIA, retention schedules, data-subject export and delete jobs, and a legal review of portal automation |

**Migration path:** the 2.0 data model (applications, submissions, audit, ai_usage) already keeps `job_key` and `submission_key` separate. Adding `tenant_id` and `user_id` columns is the first step. Before Profile B is built: no SSO, tenant isolation, encrypted secret store or worker isolation is claimed.

## Go / no-go checklist
| Item | Personal profile | Enterprise |
|---|---|---|
| All release tests pass, safety modules at 100% branch coverage, mutation tests killed | required ✅ (see TEST_MATRIX.md) | required |
| Policy default SEARCH_ONLY, consent flow, OUTCOME_UNKNOWN handling | ✅ | ✅ |
| Legal review of each portal's terms (Naukri, LinkedIn) | **needed before enabling those channels** | needed |
| Independent security test (external penetration test) | recommended | **required** |
| Privacy and legal sign-off (DPDP/GDPR) | user's own responsibility | **required** |
| SSO, tenant isolation, KMS, worker isolation | n/a | **not built: NO-GO** |
| Production operations history and SLOs | n/a | none: NO-GO |

**Decision:** GO for personal single-user use in SEARCH_ONLY or PREPARE_FOR_REVIEW. AUTHORIZED_AUTO_SUBMIT is GO only on company websites after the user confirms the scope. Naukri and LinkedIn wait for the user's own terms review. **NO-GO for enterprise or multi-user deployment.**
