# Decisions (non-interactive build; every default is reversible)

| ID | Decision | Why / how to reverse |
|---|---|---|
| D1 | Python standard library only (http.server, sqlite3, zipfile, zlib, urllib); no Flask, pypdf or pytest | Installing packages was blocked in the build environment. This also meets the "setup ≤ 5 min" goal. To reverse, swap in Flask and pypdf behind the same modules. |
| D2 | Our own minimal PDF text extractor (FlateDecode + Tj/TJ operators, hex strings) | No PDF library was available. **Limitation:** PDFs using CID or Type0 fonts without readable strings, and scanned PDFs, give a clear "no readable text" error that suggests a DOCX. Upgrade path: pypdf. |
| D3 | Tests use `unittest`. "E2E" means API-level journeys over real HTTP with a mocked network. No real browser tests | Neither a browser nor Playwright was installable. The browser-based UI gate items are marked UNVERIFIED. |
| D4 | Sources: Greenhouse, Lever and SmartRecruiters public board APIs plus Adzuna (key from env) | These are official public or licensed APIs, so no scraping (M3). |
| D5 | Default boards: Bosch and Visa (SmartRecruiters), Cloudflare (Greenhouse), Palantir (Lever) | Most of the requested MNCs (Microsoft, Google, Amazon and others) do not publish public ATS board APIs. They are reached through Adzuna plus the Companies filter, and the target-company list sits in Settings. Boards can be edited in Settings. Visa returned 0 postings in the live check. |
| D6 | Match score = 70 × skill coverage + 15 × title similarity + 15 × seniority fit | Simple, explainable and local. Seniority defaults to Mid (3-7 years). |
| D7 | Uploading saves the extracted profile right away, and the user then edits and saves it | One fewer step. The edit form opens with focus on Skills. |
| D8 | Duplicate key = normalised company + title + first location token | Meets M5. It can miss duplicates where companies are named differently across sources. |
| D9 | Hide uses undo (no confirmation). Delete-all uses a named confirmation dialog. Removing a source uses a confirm prompt | UEUIS §11. |
| D10 | Optional features: O2 CSV export is built. O1 (LLM explanation) and O3 (scheduled refresh) are not built | Time budget. Refresh is manual, as the spec chose. |
| D11 | Cache identical source URLs for 1 h. Prune postings older than 60 days that are not saved | Spec limits. |
| D12 | Palette "Pine & Paper" (light) and "Pine Night" (dark), with solid pine-green primary #1d6b57 / #62c3a6 | The app's own identity, deliberately not generic blue. |
| D13 | Writes require a local Host header and Origin (blocks DNS rebinding and cross-site requests). A strict CSP is set | Security for a local server. |
