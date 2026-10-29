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
    assert classify_touch("LGTM-G2", ["lgtm-g?"]) == "gate_verdict"
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


def test_measured_patterns_act_as_globs_and_are_fast():
    import time
    for pat in [r"\w*\w*\w*\w*\w*!", "[ab]*[ab]*[ab]*[ab]*[ab]*c", "a*a*a*a*a*b", ".*.*.*.*.*x",
                "*" * 40 + "z"]:
        t0 = time.perf_counter()
        assert classify_touch("a" * 200, [pat]) is None
        assert time.perf_counter() - t0 < 0.2


def test_glob_semantics():
    for pat, text, hit in [("lgtm", "LGTM", True), ("lgtm", "lgtm please", False),
                           ("ok to ship", "OK to ship", True), ("ok*ship*", "ok then ship it now", True),
                           ("ok*ship*", "ok then hold", False), ("g?", "g1", True), ("g?", "g", False),
                           ("v1.0", "v1x0", False), ("v1.0", "v1.0", True), ("(x)", "(x)", True),
                           ("(x)", "x", False)]:
        assert (classify_touch(text, [pat]) == "gate_verdict") is hit, (pat, text)


def test_pattern_length_cap_and_rejected_list():
    from retro_touch import load_gate_patterns
    accepted, rejected = load_gate_patterns(["LGTM*", "x" * 81, "", "ok"])
    assert accepted == ["lgtm*", "ok"] and rejected == ["x" * 81, ""]


def test_safe_pattern_still_excludes_and_long_turn_never_does():
    assert classify_touch("ok to ship", ["ok to ship", "lgtm"]) == "gate_verdict"
    long_text = "a" * 201
    assert classify_touch(long_text, ["a*"]) is None
    assert classify_touch("a" * 200, ["a*"]) == "gate_verdict"


def test_relay_only_on_short_turns():
    assert classify_touch("the other session says it is green", []) == "relay"
    long_req = "please refactor the parser module so that " + "it handles every edge case " * 10 + "the other session agreed"
    assert len(long_req) > 200 and classify_touch(long_req, []) is None
