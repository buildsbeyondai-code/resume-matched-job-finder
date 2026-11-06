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

## Auto apply
Select **Auto apply** above the results (or **Apply** on one job). For each job, the app finds the application on the company's own website. For Adzuna jobs it follows Adzuna's company link or checks the company's own Greenhouse, Lever, SmartRecruiters, Ashby or Workable board. It then fills the form from `resume_details.md`, uploads your resume, steps through multi-page forms, presses **Submit** and keeps a screenshot of the confirmation page. The **Applications** tab shows each job's status. Required questions that your details don't answer go to **Claude Opus 5.5**, which answers them from your resume and resume_details.md (the answers and the fact behind each are shown; turn this off in Settings → AI answers). Questions Claude can't support from your facts (salary, notice period, visa, legal consents) are collected there, asked once and saved. CAPTCHAs, sign-in walls and aggregator-only jobs are marked **Needs you** or **No company website**, never guessed. Test coverage: `tools/coverage.sh`.

### Job portals: the last resort (Naukri, then LinkedIn)
When a company has no application on its own website, auto apply can apply on a job portal instead. It uses the portal's own search and accepts a result only when the company matches and the title is close (similarity ≥ 0.6). If the portal shows **Apply on company site**, the app follows that link and applies there. Otherwise it fills Naukri's question drawer or LinkedIn's Easy Apply dialog (Next, Review, Submit application). A job counts as **Submitted** only after the portal's confirmation appears ("You have successfully applied" or "Your application was sent"), and a screenshot is kept as proof. The Applications tab shows how each job was applied for: "via company site", "via Naukri" or "via LinkedIn".

- **Settings → Job portals** turns Naukri and LinkedIn on or off (both on by default) and sets the **pause between portal applications** (default 20 seconds, 5 to 600). Portal applications run one at a time. While the app waits, the Applications tab shows a countdown and a **Cancel auto apply** button.
- **Settings → Portal sign-in** opens the portal's sign-in page in the app's own browser window. Sign in once. The session stays in `data/browser-profile` (owner-only permissions), and the app stores no passwords.
- The app never solves CAPTCHAs, never creates accounts and never applies through Adzuna. A sign-in wall, CAPTCHA, no matching job, or no confirmation parks the job as **Needs you** with the reason and an **Open in browser** link.
- Automated applying may break Naukri's and LinkedIn's terms of use and could get your account restricted. Leave both portals off if that matters to you.

## Apply assistant
Press **Apply** on a job. A browser window opens the application and fills it from `resume_details.md` in the JOB SEARCH folder (`RMJF_JOBSEARCH_DIR`). It also uploads the resume file named there. If that file doesn't exist, it is created from the resume PDF in the folder. Required questions the file can't answer are asked once in the app and saved to the file. The assistant never presses Submit: review the form in the browser and submit it yourself. One-time setup:

```
.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
sudo .venv/bin/python -m playwright install-deps chromium   # system libraries (WSL/Linux)
```

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
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Licensed aggregator credentials (free developer key at developer.adzuna.com). Without them the Adzuna source is skipped quietly (shown as "Skipped (no API key)" in progress, never as a failure) and the other sources still work. The key is never logged or sent to the UI. |
| `RMJF_PORT` | Port (default 5070). The server always binds to 127.0.0.1. |
| `RMJF_AI_MODEL` | Model for AI answers (default `claude-opus-5-5`), called through the Claude Code CLI with your Claude sign-in. |
| `RMJF_CLAUDE_BIN` | Path of the `claude` CLI (default: found on PATH). Tests point it at `tests/fake_claude.py`. |
| `RMJF_DB` | SQLite path (default `data/jobfinder.sqlite3`). |

## Tests
```bash
python3 -m unittest discover -s tests -t .
```
The suite has 92 tests: unit tests, adapter contract tests against recorded-shape fixtures, integration and journey tests over real HTTP with a mocked network, static UI conformance checks, end-to-end auto-apply tests in real Chromium against local fake career sites and fake Naukri/LinkedIn pages (`tests/fake_careers.py`, never the real sites), and browser UI tests (`tests/test_ui_browser.py`). The browser tests are skipped when Playwright Chromium is not installed. `tools/coverage.sh` runs everything and fails unless `app/apply.py`, `autoapply.py`, `details.py`, `direct.py` and `portals.py` have 100% line and branch coverage. See docs/REQUIREMENTS.md.

## Docs
See docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/REQUIREMENTS.md, docs/UI_STATES.md, docs/ACCESSIBILITY.md, docs/REUSE.md, docs/LEARNINGS.md and docs/COMPLETION_REPORT.md.

## Credits
Reuse candidates were reviewed (see docs/REUSE.md). No code was copied. Ideas were taken from FoundRole jobs-mcp-proxy (ISC) and workopia-mcp (MIT): report only what the parser actually extracted, and give job search a filter-first shape. The upstream projects without a license were read for orientation only.
