"""Test package. Tests never call the real Claude: ai.binary() gets a path that does not exist unless a test
points RMJF_CLAUDE_BIN at tests/fake_claude.py itself."""
import os

os.environ["RMJF_CLAUDE_BIN"] = os.path.join(os.path.dirname(__file__), "no-real-claude-in-tests")
