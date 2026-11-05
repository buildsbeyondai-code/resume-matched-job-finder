# Reuse report (UAISES §12)

All candidates were shallow-cloned into `upstream/` (git-ignored) and reviewed.

| Candidate | License | What it is | Reused | Why not more |
|---|---|---|---|---|
| Yaswanth-reddy-gunupati8712/online-job-portals | None | Job portal frontend + backend (employer posting, CRUD) | Nothing (orientation only) | No license, so no code may be copied. It is an employer job board, which is out of scope here. |
| numerodiciannove/scrape_vacancies_bot | None | Telegram bot that scrapes vacancy sites | Nothing | No license, and scraping is out of scope (M3). |
| mahi-verma-19/AI-Resume-Screening-System | None | Streamlit + ML classifier of resumes into categories | Idea only: keyword-based skill extraction | No license, and it depends on a trained model. We use a deterministic skill vocabulary instead. |
| FoundRole jobs-mcp-proxy (claude-plugin) | ISC | MCP proxy to FoundRole's hosted job search and resume-check service | Idea only: the resume check reports exactly what the parser extracted, so we show the extracted profile for review and edit | Its functionality is a remote service. Using it would send resume data to a third party, which violates M8. This app is a web app, not a Claude plugin. |
| workopia-mcp | MIT | MCP plugin for a hosted job search | Idea only: filter-first search parameters (country, remote, keywords) | Hosted service, same M8 problem. |

**Base chosen:** none. No candidate could serve as a code base with a compatible license that also met the local-only privacy requirement. Built new: everything in `app/`, `static/` and `tests/`.
