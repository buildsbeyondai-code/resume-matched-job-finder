#!/usr/bin/env python3
"""Stands in for the Claude Code CLI (`claude -p … --json-schema …`) in tests: no model is ever called.

Reads the prompt on stdin, records it to $FAKE_CLAUDE_LOG (if set) and answers like `--output-format json`.
FAKE_CLAUDE_MODE: answer (default) | error | garbage | nostruct | sleep | notdict.
In answer mode: "Why…" questions get a motivation sentence, Yes/No questions about authorisation get "Yes",
questions with options not otherwise known get "Maybe" (not an option: the app must keep asking), anything
else gets null."""
import json
import os
import sys
import time

prompt = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "answer")
if mode == "sleep":
    time.sleep(5)
if mode == "garbage":
    print("not json")
    sys.exit(1)
if mode == "notdict":
    print("[]")
    sys.exit(0)
if mode == "error":
    print(json.dumps({"is_error": True, "result": "Credit balance is too low"}))
    sys.exit(1)
if mode == "nostruct":
    print(json.dumps({"is_error": False, "result": "hello"}))
    sys.exit(0)

questions = json.loads(prompt.split("## Questions\n", 1)[1])
answers = []
for q in questions:
    text = q["question"].lower()
    if text.startswith("why"):
        v, basis = "I lead AI programme delivery at Contoso, which matches this role.", "resume: current role"
    elif "authorized" in text:
        v, basis = "Yes", "resume: lives in the job's country"
    elif q.get("options"):
        v, basis = "Maybe", "a value that is not one of the options"
    else:
        v, basis = None, "not stated in the facts"
    answers.append({"id": q["id"], "value": v, "basis": basis})
answers.append({"id": "not-a-question", "value": "x", "basis": "ignored"})
answers.append("junk")
print(json.dumps({"is_error": False, "result": "", "structured_output": {"answers": answers}}))
