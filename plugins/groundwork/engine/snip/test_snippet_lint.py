"""Tests for snippet_lint.py: the switch, fences, rules L1 to L7, damage samples, pure ASCII."""
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snippet_lint  # noqa: E402

F = "`" * 3
RUNS_PS = "# runs in: PowerShell 7 on Windows, normal window"
RUNS_SH = "# runs in: Terminal on the Mac (zsh)"
GOOD_PS = [F + "powershell", RUNS_PS, "# cwd: D:\\demo\\repo", "# needs: nothing",
           "Set-Location -LiteralPath 'D:\\demo\\repo'", "py -3 -m pytest -q",
           "# expect: all tests pass", F]
GOOD_SH = [F + "sh", RUNS_SH, "# cwd: /Users/demo/repo", "# needs: nothing", "# recipe: git sync",
           "cd -- '/Users/demo/repo'", "git fetch --prune origin", "git merge --ff-only '@{u}'",
           "git status -sb", "# expect: the branch is level with its upstream", F]
GOOD_PASTE = [F + "text", "# paste into: the review session", "hello", F]
BAD = ["# Notes", "", F, "git push", F, ""]  # L4 on line 4


def repo(tmp_path, on=True):
    cfg = {"settings": {"outputStandards": on}}
    (tmp_path / "siteoffice.json").write_bytes(json.dumps(cfg).encode("utf-8"))
    return tmp_path


def write(path, text):
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


def doc(lines):
    """Two lines of prose, then the fixture: fixture line i (0-based) is file line i + 3."""
    return "\n".join(["# Fixture", ""] + lines + [""])


def lint(capsys, *argv):
    code = snippet_lint.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("setting", ["key missing", "false", "no file"])
def test_switch(tmp_path, capsys, setting):
    if setting == "key missing":
        write(tmp_path / "siteoffice.json", json.dumps({"settings": {}}))
    elif setting == "false":
        repo(tmp_path, on=False)
    path = write(tmp_path / "notes.md", "\n".join(BAD))
    code, out, err = lint(capsys, path)
    assert (code, out) == (0, "")
    assert err.strip() == "skipped (output standards off): " + str(path)
    code, out, err = lint(capsys, path, "--force")
    assert code == 1 and out.startswith(str(path) + ":4: L4: ")
    repo(tmp_path, on=True)
    code, out, err = lint(capsys, path)
    assert code == 1 and err == "" and out.startswith(str(path) + ":4: L4: ")


RULE_CASES = [
    ("L1", [F + "powershell", RUNS_PS, "# cwd: D:\\demo\\repo", "# needs: nothing",
            "Set-Location -LiteralPath 'D:\\demo\\other'", "dir", "# expect: a listing", F], 7),
    ("L2", [F + "sh", RUNS_SH, "# cwd: /Users/demo/repo", "# needs: nothing",
            "cd -- '/Users/demo/repo'", "ls", F], 8),
    ("L3", [F + "powershell", RUNS_PS, "# cwd: D:\\demo\\repo", "# needs: nothing",
            "Set-Location -LiteralPath 'D:\\demo\\repo'", "git status --short",
            "# expect: a short status", F], 8),
    ("L3", [F + "powershell", RUNS_PS, "# cwd: D:\\demo\\repo", "# needs: nothing",
            "# recipe: git push", "Set-Location -LiteralPath 'D:\\demo\\repo'",
            "git push", "# expect: pushed", F], 7),
    ("L4", [F, "git push", F], 4),
    ("L5", [F + "text", "# paste into:", "hello", F], 4),
]


@pytest.mark.parametrize("rule,lines,line_no", RULE_CASES)
def test_lint_rules(tmp_path, capsys, rule, lines, line_no):
    path = write(repo(tmp_path) / "case.md", doc(lines))
    code, out, _ = lint(capsys, path)
    assert code == 1
    assert len(out.splitlines()) == 1, out
    assert out.startswith("%s:%d: %s: " % (path, line_no, rule)), out


def test_lint_rules_pass(tmp_path, capsys):
    text = doc(GOOD_PS + [""] + GOOD_SH + [""] + GOOD_PASTE)
    path = write(repo(tmp_path) / "good.md", text)
    assert lint(capsys, path) == (0, "", "")


def test_fence_rules(tmp_path, capsys):
    text = "\n".join(["````markdown", F + "powershell", "git push", F, "````", ""])
    fences = snippet_lint.parse_fences(text.split("\n"))
    assert len(fences) == 1 and fences[0].lang == "markdown"
    assert [t for _, t in fences[0].lines] == [F + "powershell", "git push", F]
    assert lint(capsys, write(repo(tmp_path) / "four.md", text))[0] == 0
    tilde = "\n".join(line.replace(F, "~~~") for line in GOOD_PS) + "\n"
    assert lint(capsys, write(tmp_path / "tilde.md", tilde))[0] == 0
    bad = write(tmp_path / "tilde_bad.md", "~~~powershell\ngit push\n~~~\n")
    code, out, _ = lint(capsys, bad)
    assert code == 1 and (str(bad) + ":1: L1: ") not in out and (str(bad) + ":2: L1: ") in out
    mixed = "\n".join([F + "powershell", "~~~", "git push", F, ""])  # tildes do not close backticks
    assert len(snippet_lint.parse_fences(mixed.split("\n"))) == 1
