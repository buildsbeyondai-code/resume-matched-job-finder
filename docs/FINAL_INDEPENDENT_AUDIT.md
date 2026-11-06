# Final audit: Resume-Matched Job Finder 2.0.0

**Who wrote this:** the same agent that wrote the code (Claude Opus 5.5 in Claude Code), so it is **not independent**. Treat it as a self-assessment against the fixed rubric in the master prompt. An external review is still required for higher assurance.

- **Base:** `e3efe37` (origin/master), recovered locally as `7689edd`.
- **Head:** branch `v2-enterprise-hardening` (the SHAs are listed in ENTERPRISE_HARDENING_PROGRESS.md). Not pushed and not merged.

## Rubric (weights fixed before implementation)
| Category | Weight | Verified points | Evidence | Why not higher |
|---|---:|---:|---|---|
| Security, privacy, tenancy, compliance controls | 20 | **12** | strict origin/host/header checks; parser limits; data minimisation; delete-all fixed; SAST 0 High; deps clean; threat model | no tenancy, SSO or encryption at rest (enterprise not built); local processes unauthenticated (R2); no external pentest; no legal (DPDP/GDPR) review |
| Submission correctness, consent and anti-duplicate safety | 20 | **17** | default SEARCH_ONLY; hash-bound expiring consent; fail-closed gate on every possibly-sending click; transactional claims; OUTCOME_UNKNOWN never retried; enforced state machine; 17/17 mutants killed | a submit disguised as "Next" is caught only after the fact (R1); no live verification of confirmation detection on real sites |
| Discovery accuracy and matching intelligence | 12 | **6** | deterministic scoring with matched and missing skills; dedupe across sources; score bounds property-tested | no labelled evaluation set, no Precision@K or recall, no semantic matching, no compensation normalisation, no closed or stale detection |
| Browser/ATS robustness and portal policy conformance | 10 | **5** | E2E against local fakes for Greenhouse- and Lever-style forms, multi-step, walls, CAPTCHA, unknown outcome, synthetic Naukri and LinkedIn; portal policy matrix | **no live verification**; no SmartRecruiters, Ashby or Workable form fixtures; Naukri and LinkedIn terms not legally reviewed; selectors are heuristic, not per-ATS adapters |
| Reliability, recovery and operations | 10 | **6.5** | versioned migrations with backup; health and readiness; crash and restart recovery; outage table; runbook | single worker and single browser; no lease-based queue or dead-letter; Docker image unbuilt; no restore drill on real data; no metrics or traces |
| Automated tests, CI and independent verification | 12 | **9.5** | 187 tests; 100% line and branch on 10 safety modules; property, fuzz and mutation tests; CI workflow with SHA-pinned actions; secret scan; SAST; pip-audit | CI never run on GitHub yet (push needs permission); no independent verification; no type checking or lint gate |
| UX, accessibility and user control | 6 | **4.5** | policy panel; consent dialog showing the exact scope; clear Outcome-unknown actions; browser tests for names and focus | manual WCAG 2.2 AA review of the new screens NOT VERIFIED; no notifications |
| Performance and cost efficiency | 5 | **4** | benchmark p50/p95/p99, throughput, RSS; Claude daily budget and usage log | no long-running load or soak test; Claude cost is the CLI's reported list-price equivalent |
| Architecture, documentation, maintainability | 5 | **4.5** | all 13 required documents, migration guide, changelog, decisions D55–D64, architecture updated | some older docs (REQUIREMENTS, UI_STATES) not fully revised for 2.0 |
| **Total** | **100** | **69** | | |

**Engineering quality score: 6.9 / 10.** That is well below the 9.8 target, and it is reported as is. The rubric was not weakened. Hard caps:
- no critical unauthorized-submission or cross-tenant exposure is known;
- unknown-state retries are prevented;
- browser E2E exists (against fakes).

None of those caps applies. The missing points come from enterprise features that were not built, from the lack of live and independent verification, and from matching quality that hasn't been measured.

## Deployment authorization (separate from the score)
- **Personal single-user:** GO in Search only and Prepare for review. Submit automatically: GO for company websites after the user confirms the scope.
- **Naukri / LinkedIn:** NO-GO until the user reviews each portal's terms (PORTAL_POLICY_MATRIX.md).
- **Enterprise or multi-user:** **NO-GO**. Not built, and no security, privacy, legal or operations sign-off.
- No "unrestricted enterprise deployment approved" claim is made.

## What would raise the score (in order of value)
1. Run CI on GitHub, then get an external security review (+2–3 in security and tests).
2. Build a labelled job-match evaluation set and report Precision@K and false-positive rates (+3–4 in discovery).
3. Live, authorized, non-submitting verification on Greenhouse, Lever, SmartRecruiters, Ashby and Workable sandboxes, plus per-ATS adapters (+3 in browser/ATS).
4. Enterprise profile B (SSO, tenancy, KMS, worker isolation, queue leases) (+6–8 across security and operations).
5. A live prompt-injection and answer-faithfulness evaluation of Claude Opus 5.5 (needs paid calls and the user's approval).
