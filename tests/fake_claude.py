#!/usr/bin/env python3
"""Stands in for the Claude Code CLI (`claude -p … --json-schema …`) in tests: no model is ever called.

Reads the prompt on stdin, records it to $FAKE_CLAUDE_LOG (if set) and answers like `--output-format json`.
FAKE_CLAUDE_MODE: answer (default) | error | garbage | nostruct | sleep | notdict | wrongmodel | flaky (a 529
the first time, using $FAKE_CLAUDE_STATE as the counter) | uncited | lowconf | overloaded (always 529).
In answer mode: "Why…" questions get a motivation sentence citing a resume fact, authorisation questions get
"Yes" citing the Country fact, a decline option is chosen for self-identification, questions with other options
get "Maybe" (not an option: the app must keep asking), anything else gets null."""
import json
import os
import re
import sys
import time

MODEL = "claude-opus-5-5"
prompt = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "answer")


def emit(obj, code=0):
    print(json.dumps(obj))
    sys.exit(code)


usage = {"input_tokens": 1200, "output_tokens": 300}
if mode == "sleep":
    time.sleep(5)
if mode == "garbage":
    print("not json")
    sys.exit(1)
if mode == "notdict":
    emit([])
if mode == "error":
    emit({"is_error": True, "result": "Credit balance is too low", "total_cost_usd": 0}, 1)
if mode == "nostruct":
    emit({"is_error": False, "result": "hello", "total_cost_usd": 0.01, "modelUsage": {MODEL: {}}})
if mode == "overloaded":
    emit({"is_error": True, "api_error_status": 529, "result": "Overloaded", "total_cost_usd": 0}, 1)
if mode == "flaky":
    state = os.environ["FAKE_CLAUDE_STATE"]
    n = int(open(state).read()) if os.path.exists(state) else 0
    open(state, "w").write(str(n + 1))
    if n == 0:
        emit({"is_error": True, "api_error_status": 529, "result": "Overloaded", "total_cost_usd": 0}, 1)

facts = dict(re.findall(r"^([DR]\d+): (.*)$", prompt.split("## Candidate facts\n", 1)[1].split("\n\n## ", 1)[0], re.M))
block = prompt.split("## Questions\n", 1)[1]
questions = json.loads(block.split("<untrusted_employer_content>\n", 1)[1].rsplit("\n</untrusted_employer_content>", 1)[0])


def cite(word):
    return [k for k, v in facts.items() if word.lower() in v.lower()][:1] or list(facts)[:1]


answers = []
for q in questions:
    text, opts = q["question"].lower(), q.get("options") or []
    if text.startswith("why"):
        v, ids, basis = "I lead AI programme delivery at Contoso, which matches this role.", cite("Contoso"), "current role"
    elif "authorized" in text:
        v, ids, basis = "Yes", cite("Country"), "lives in the job's country"
    elif any("prefer not" in o.lower() for o in opts):
        v, ids, basis = next(o for o in opts if "prefer not" in o.lower()), [], "voluntary self-identification declined"
    elif opts:
        v, ids, basis = "Maybe", cite(""), "a value that is not one of the options"
    else:
        v, ids, basis = None, [], "not stated in the facts"
    if mode == "uncited" and v:
        ids = ["R999"]
    answers.append({"id": q["id"], "value": v, "fact_ids": ids, "confidence": 0.3 if mode == "lowconf" else 0.9,
                    "basis": basis})
answers.append({"id": "not-a-question", "value": "x", "fact_ids": [], "confidence": 1, "basis": "ignored"})
answers.append("junk")
emit({"is_error": False, "result": "", "structured_output": {"answers": answers}, "total_cost_usd": 0.0421,
      "usage": usage, "modelUsage": {("claude-sonnet-5-5" if mode == "wrongmodel" else MODEL): {}}})
