"""Claude Opus 5.5 answers the application questions that resume_details.md does not cover.

Called during Apply and Auto apply, after the details file has filled what it can. Claude sees the
question list (with each field's options), the job, resume_details.md and the resume text. It answers only
what those facts support and returns null for the rest. A null answer, an error or a timeout leaves the
question for the user ("Needs your answers"), so nothing is guessed. See docs/DECISIONS.md D54.

Claude is reached through the Claude Code CLI (`claude -p`) with the user's own sign-in, no API key, no
tools, and structured output (`--json-schema`). In test mode (RMJF_ALLOW_LOCAL_HTTP=1) it runs only when
RMJF_CLAUDE_BIN points at a fake, so an automated test never calls the real model.
"""
import json
import os
import shutil
import subprocess
import tempfile

from . import config

MODEL = os.environ.get("RMJF_AI_MODEL", "claude-opus-5-5")
TIMEOUT_S = 180
BUDGET_USD = "1.00"  # per call; a form's questions cost about $0.05
MAX_TEXT = 20000  # characters of resume / details / job description sent

SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "value": {"type": ["string", "null"]},
                       "basis": {"type": "string"}},
        "required": ["id", "value", "basis"], "additionalProperties": False}}},
    "required": ["answers"], "additionalProperties": False,
}

RULES = """You fill in a job application form for the candidate, using only the facts below.

Rules:
- Answer from the candidate's facts (resume_details.md and the resume). Simple derivations are fine
  (years of experience from employment dates, current city from the address, highest degree).
- Never invent employers, titles, degrees, dates, numbers, licences, salaries or personal facts.
- For questions with options, "value" must be copied exactly from the options. For multi-select
  (kind "checkboxes") give the chosen options separated by "; ". For a single checkbox answer "Yes" or "No".
- Free-text motivation questions ("Why do you want to join…", "Tell us about yourself", cover letter text):
  write 2-4 truthful sentences grounded in the resume and the job. Plain text, no placeholders.
- Voluntary self-identification (gender, race, ethnicity, veteran, disability): pick the option that
  declines to answer when one exists; otherwise null.
- Return null (the candidate will be asked) when the facts do not settle it, including: salary or pay
  expectations, notice period, start date, relocation or travel willingness, work authorisation or visa
  sponsorship, criminal record, background-check or legal consents, references, and anything else
  personal that is not stated.
- "basis" says in a few words which fact the answer comes from, or why it is null.
Return one entry per question id."""


class AIError(Exception):
    """Claude could not be reached or gave no usable answer."""


def binary():
    """Path of the Claude Code CLI, or None. In test mode only an explicit fake is used."""
    explicit = os.environ.get("RMJF_CLAUDE_BIN", "")
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    if config.ALLOW_LOCAL_HTTP:
        return None
    return shutil.which("claude")


def available():
    return binary() is not None


def _clip(text):
    text = (text or "").strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n[…truncated]"


def prompt(questions, job, details_text, resume_text):
    qs = [{"id": q["id"], "question": q["label"], "kind": q["kind"],
           **({"options": q["options"]} if q.get("options") else {})} for q in questions]
    return "\n\n".join([
        RULES,
        "## Job\n" + json.dumps({"title": job.get("title", ""), "company": job.get("company", ""),
                                 "location": job.get("location", "")}, ensure_ascii=False)
        + "\n" + _clip(job.get("description", "")),
        "## resume_details.md\n" + _clip(details_text),
        "## Resume\n" + _clip(resume_text),
        "## Questions\n" + json.dumps(qs, ensure_ascii=False, indent=1),
    ])


def _run(text):
    exe = binary()
    if not exe:
        raise AIError("Claude Code is not installed, so Claude could not answer the questions.")
    cmd = [exe, "-p", "--model", MODEL, "--tools", "", "--setting-sources", "", "--no-session-persistence",
           "--output-format", "json", "--max-budget-usd", BUDGET_USD, "--json-schema", json.dumps(SCHEMA)]
    try:  # an empty working folder: no project files or instructions are in reach
        with tempfile.TemporaryDirectory(prefix="rmjf-ai-") as cwd:
            p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise AIError(f"Claude did not answer within {TIMEOUT_S} seconds.") from None
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise AIError("Claude gave no readable reply: " + ((p.stderr or p.stdout).strip().splitlines() or ["no output"])[0][:200]) from None
    if not isinstance(out, dict) or out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        msg = out.get("result") if isinstance(out, dict) else None
        raise AIError("Claude could not answer: " + str(msg or "no structured answer")[:200])
    return out["structured_output"]


def answer(questions, job, details_text, resume_text):
    """{question id: (value, basis)} for the questions Claude could answer; ids it left null are omitted."""
    if not questions:
        return {}
    ids = {q["id"] for q in questions}
    got = _run(prompt(questions, job, details_text, resume_text)).get("answers")
    out = {}
    for a in got if isinstance(got, list) else []:
        if not isinstance(a, dict) or a.get("id") not in ids:
            continue
        v = a.get("value")
        if isinstance(v, str) and v.strip():
            out[a["id"]] = (v.strip(), str(a.get("basis") or "").strip()[:200])
    return out
