# Resume-Matched Job Finder

A local web app that searches official public job APIs (Greenhouse, Lever and SmartRecruiters company boards, plus the licensed Adzuna aggregator) by country, work mode (remote, hybrid or on-site), skills and keywords. It then ranks every posting 0-100 against your resume. Matching happens on your computer, and your resume is never sent anywhere.

## Features
- Upload a resume as a PDF or DOCX (up to 10 MB). The app reads it locally and pulls out skills, job titles and years of experience, which you can edit.
- Search runs across all configured sources in parallel. You can see each source's progress and cancel the search. If one source fails, results from the other sources still show, and a banner names the failed source and offers a retry.
- Each posting gets a match score from 0 to 100. Results are sorted by score, and each one lists the matched and missing skills. If the same job appears on more than one source, it shows once with every source listed.
- "View posting" opens the original posting in a new tab. You apply on the employer's site.
- You can save a job, or hide it and undo that. "Refresh last search" marks postings that are new since the last run. You can narrow results by minimum score, posting age, company and salary, and export them to CSV.
- Everything is stored in local SQLite (WAL mode). Deleting all your data takes one action.
- Light and dark themes follow your system setting, and you can override it (the choice is remembered). A Descriptions switch hides explanatory text. The UI targets WCAG 2.2 AA.

## Run
```bash
git clone <this repo> && cd resume-matched-job-finder
cp .env.example .env      # optional: add Adzuna keys
./run.sh                  # or: python3 -m app
# open http://127.0.0.1:5070
```
Requires Python 3.11 or newer. There are **no third-party dependencies**; it uses the standard library only.

## Environment variables
| Variable | Purpose |
|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Licensed aggregator credentials (free developer key at developer.adzuna.com). Without them the Adzuna source reports "Not configured" and the other sources still work. The key is never logged or sent to the UI. |
| `RMJF_PORT` | Port (default 5070). The server always binds to 127.0.0.1. |
| `RMJF_DB` | SQLite path (default `data/jobfinder.sqlite3`). |

## Tests
```bash
python3 -m unittest discover -s tests -t .
```
The suite has 42 tests: unit tests, adapter contract tests against recorded-shape fixtures, integration and journey tests over real HTTP with a mocked network, and static UI conformance checks. See docs/REQUIREMENTS.md.

## Docs
See docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/REQUIREMENTS.md, docs/UI_STATES.md, docs/ACCESSIBILITY.md, docs/REUSE.md, docs/LEARNINGS.md and docs/COMPLETION_REPORT.md.

## Credits
Reuse candidates were reviewed (see docs/REUSE.md). No code was copied. Ideas were taken from FoundRole jobs-mcp-proxy (ISC) and workopia-mcp (MIT): report only what the parser actually extracted, and give job search a filter-first shape. The upstream projects without a license were read for orientation only.
