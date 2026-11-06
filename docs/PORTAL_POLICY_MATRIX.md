# Portal and source policy matrix (2.0)

"Mock-tested" means tested end to end against the local fakes in `tests/fake_careers.py` with real Chromium. That **does not** show that the live site works or that automating it is allowed. No live application was submitted while 2.0 was developed.

| Provider | How it is used | Automation allowed? (needs review) | Tested features | Live verification | Restrictions in the app |
|---|---|---|---|---|---|
| Greenhouse (boards API) | public job-board JSON API for search; the browser for the employer-hosted form | public API documented for job boards. Browser form filling: employer's terms apply, legal review needed | search adapter (fixtures); E2E form fill, upload, questions, submit, confirmation (mock) | **NOT VERIFIED** | HTTPS allow-list; gate before Submit |
| Lever (postings API) | API for search; browser form | as above | search (fixtures); E2E Apply link, form, submit (mock) | **NOT VERIFIED** | same |
| SmartRecruiters (postings API) | API for search; browser form | as above | search (fixtures); company-board lookup (unit) | **NOT VERIFIED**; no E2E form fixture (gap) | same |
| Ashby, Workable | company-board lookup (public JSON) to find the original posting | public endpoints | lookup unit tests | **NOT VERIFIED**; no E2E form fixture (gap) | same |
| Adzuna | licensed aggregator API (key required) for search only; never applied through | API terms, with a developer key | search (fixtures); "never apply on the aggregator" E2E (mock) | search NOT VERIFIED in 2.0 runs | applications go to the company site only |
| Naukri | last-resort channel: its own search plus the Apply / chat drawer in the user's signed-in browser | **terms restrict automated use: legal and policy review required before enabling**. Opt-in (channel in Policy and Settings) | E2E search, match, one-click Apply, chat drawer, walls, CAPTCHA, unknown outcome (mock) | **NOT VERIFIED** | one job at a time, a pause of ≥ 5 s, no CAPTCHA solving, sign-in by the user only, gate before Apply/Save/Submit |
| LinkedIn | last-resort channel: Easy Apply in the user's signed-in browser | **the user agreement prohibits many kinds of automation: legal review required; default is not to enable without it** | E2E Easy Apply steps, no-confirmation, walls (mock) | **NOT VERIFIED** | same as Naukri |

Never done, on any provider:
- solving CAPTCHAs;
- spoofing browser fingerprints;
- rotating proxies;
- creating accounts;
- storing passwords;
- scraping pages behind a login, except the user's own signed-in application pages while applying.

Recommendation before any live portal use: review each provider's current terms, enable at most one channel, and use a small daily cap in PREPARE_FOR_REVIEW mode first.
