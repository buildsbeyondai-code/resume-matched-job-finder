# Claude model integration (2.0)

Code: `app/ai.py`. Tests: `tests/test_ai.py`, `tests/test_ai_integration.py` (the fake CLI is `tests/fake_claude.py`).

## What Claude does and doesn't do
| Claude may | Claude may not (deterministic code decides) |
|---|---|
| Draft answers to required form questions that `resume_details.md` doesn't answer, from numbered candidate facts | Decide whether to submit, or check consent, identity or policy (`policy.gate`) |
| Pick an option to decline voluntary self-identification | Decide whether an application went through (`apply.confirmed`, proof, OUTCOME_UNKNOWN) |
| Write a 2–4 sentence motivation answer grounded in cited facts | Answer salary, notice period, visa or work authorisation not stated, citizenship, health, criminal record, legal consents or references (these always go to the user) |

Candidate facts always win. Claude only sees questions the details file couldn't answer. Its answers are shown under each job (value, the fact ids cited, confidence, basis). They are **not** saved to `resume_details.md`; only the user's own answers are saved.

## Invocation
- `claude -p --model claude-opus-5-5 --tools "" --setting-sources "" --no-session-persistence --output-format json --max-budget-usd 1.00 --json-schema <schema>`.
- It runs in an empty temporary working directory, with the prompt on stdin and the user's own Claude Code sign-in. No API key is stored by the app.
- **Model check.** The `modelUsage` keys in the reply must include `RMJF_AI_MODEL`, or the answers are discarded with an error. There is no silent fallback. Changing the model needs the user to set `RMJF_AI_MODEL`.
- **Verified identifier.** On 2026-11-06 a live call through Claude Code 2.1.318 returned `modelUsage` = `{"claude-opus-5-5": …}`, about 11 s for 6 questions. That ran an earlier prompt version (`answers-2026-11-06.1`). The current prompt version `answers-2026-11-06.2` was evaluated live with the user's approval on a fictional candidate (`tools/ai_eval.py`, results in AI_EVAL_RESULTS.md): 3 calls, `claude-opus-5-5` confirmed, **33/33 checks passed**, reported cost $0.18.

## Output contract (enforced in code)
- The schema is `{answers: [{id, value|null, fact_ids[], confidence, basis}]}`.
- An answer is kept only when:
  - its id is one of the questions asked;
  - its value is a non-empty string;
  - **every** cited fact id exists;
  - its confidence is at least 0.6.
- The one exception: declining self-identification needs no citation, but only when both the value and one of the options are decline wording.
- Option answers must match an option on the field (`apply.convert`). Otherwise the question stays open for the user.

## Data minimisation
- `resume_details.md` lines become `D<n>` facts. Fields that look like contact data or sensitive personal data (salary, visa, citizenship, gender, health, address, phone, email and similar) are dropped.
- Resume text becomes `R<n>` lines, with emails and phone numbers replaced and the total capped at 20,000 characters. The resume file itself is never sent.
- Settings → AI answers turns the whole feature off, and then nothing is sent.

## Prompt injection
- The job description, the question labels and the options are wrapped in `<untrusted_employer_content>`.
- Fence tags inside employer text are replaced with "[tag removed]", so the text can't close the fence early.
- The rules tell the model to treat that text as data.
- What an injected instruction could achieve is limited by the code:
  - only listed question ids are used;
  - answers must cite real facts;
  - Claude has no tools.
- The worst case left is a misleading free-text answer, which is shown to the user.
- **Live injection evaluation:** 3 attack placements were tested (job description, question label, option text). Opus 5.5 ignored all of them in its raw output, and the app's checks would also have caught them (AI_EVAL_RESULTS.md). This is a small sample, not a guarantee.

## Reliability and cost
- **Timeout:** 180 s per call. A timeout is not retried.
- **Retries:** at most 2, only for API status 429, 500, 502, 503, 504 or 529. Backoff is 2 s then 4 s, × jitter 0.5–1.5, capped at 30 s.
- **Daily budget:** `RMJF_AI_DAILY_BUDGET_USD`, default $3.00. When it is used up, no further calls are made and the questions are asked instead.
- **Usage log:** table `ai_usage` records time, model, cost, tokens, ok or error, prompt version and number of questions. `GET /api/ai/usage` gives daily totals.
- **About the cost figure.** It is the `total_cost_usd` Claude Code reports. On a Claude subscription this is a list-price equivalent, **not an invoice**.
- **Errors:** every failure (not installed, timeout, error reply, unreadable output, wrong model, budget) leaves the questions for the user, with the reason shown.

## Versioning
`PROMPT_VERSION = "answers-2026-11-06.2"` is stored with each usage row. Any change to `RULES` or `SCHEMA` must change it.
