import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

from retro_touch import classify_touch, is_finished_record

SHELL = "PS /home/demo> py -3 -m pytest\nFAILED tests/test_x.py::test_a\nTraceback (most recent call last):\n  File \"x.py\", line 3\n1 failed, 2 passed"


@pytest.mark.parametrize("text,expected", [
    ("yes", "approval_word"), ("OK.", "approval_word"), ("go ahead", "approval_word"),
    ("2", "permission_answer"), ("Allow once", "permission_answer"),
    ("approve", "gate_verdict"), ("ship it, looks right", "gate_verdict"),
    ("continue", "continue_restart"), ("Try again please", "continue_restart"),
    ("the other session says the build is green", "relay"),
    (SHELL, "pasted_shell_output"),
    ("Add a login page", None), ("[Request interrupted by user]", None),
    ("<command-name>/handover</command-name>", None), ("", None),
    ("close the modal on the settings page and also fix the header spacing on mobile please", None),
])
def test_classify(text, expected):
    assert classify_touch(text, []) == expected


def test_user_gate_pattern_excludes_and_bad_pattern_is_skipped():
    assert classify_touch("LGTM-G2", [r"LGTM-G\d"]) == "gate_verdict"
    assert classify_touch("yes", ["("]) == "approval_word"


def _bash(cmd):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}


@pytest.mark.parametrize("record,expected", [
    (_bash('git commit -m "x"'), True), (_bash("git status"), False),
    (_bash("gh pr create --fill"), True),
    (_bash("git_flow.ps1 save --type feat --scope x --subject y --apply"), True),
    (_bash("git_flow.ps1 save --type feat --scope x --subject y"), False),
    ({"type": "assistant", "message": {"content": [{"type": "text", "text": "## Close-out\n- DO x"}]}}, True),
    ({"type": "user", "message": {"content": [{"type": "tool_result", "content": "wrote flows/demo/a/receipts/step-01.json"}]}}, True),
])
def test_finished(record, expected):
    assert is_finished_record(record) is expected
