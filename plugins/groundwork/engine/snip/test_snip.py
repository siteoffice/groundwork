"""Tests for snip.py: the switch, the header grammar, quoting, the git recipes and refusals."""
import json
import pathlib
import shlex
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snip  # noqa: E402

REAL_HOST = snip.HOST_OS
WIN_ROOT = "D:\\demo\\repo"
MAC_ROOT = "/Users/demo/repo"
EM = "\u2014"
F = "`" * 3


@pytest.fixture(autouse=True)
def no_host(monkeypatch):
    """Made-up roots never exist: no target is the host unless a test says so."""
    monkeypatch.setattr(snip, "HOST_OS", "none")


def root_for(target):
    return WIN_ROOT if target == "win" else MAC_ROOT


def block_lines(text):
    lines = text.rstrip("\n").split("\n")
    assert lines[0].startswith(F) and lines[-1].startswith(F), text
    return lines[1:-1]


def ps_unquote(token):
    assert token[0] == token[-1] == "'", token
    return token[1:-1].replace("''", "'")


def kind_args(target):
    """One sample per command kind for the target system (Task 4 adds the git recipes)."""
    cmd = "py -3 -m pytest -q" if target == "win" else "python3 -m pytest -q"
    return [
        ("run", {"cmd": [cmd], "expect": "all tests pass"}),
    ]


def test_standards_on(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert snip.standards_on(sub) is False
    cfg = tmp_path / "siteoffice.json"
    cfg.write_bytes(json.dumps({"settings": {}}).encode("utf-8"))
    assert snip.standards_on(sub) is False
    cfg.write_bytes(json.dumps({"settings": {"outputStandards": False}}).encode("utf-8"))
    assert snip.standards_on(sub / "notes.md") is False
    cfg.write_bytes(json.dumps({"settings": {"outputStandards": True}}).encode("utf-8"))
    assert snip.standards_on(sub / "notes.md") is True
    cfg.write_bytes(b"{not json")
    assert snip.standards_on(sub) is False
    cfg.write_bytes(json.dumps({"settings": {"outputStandards": True}}).encode("utf-8"))
    (tmp_path / "a" / "siteoffice.json").write_bytes(b"{}")
    assert snip.standards_on(sub) is False  # the nearest file wins


@pytest.mark.parametrize("target", ["win", "mac"])
def test_header_order(target):
    for kind, args in kind_args(target):
        text = snip.build(kind, os=target, cwd=root_for(target), **args)
        assert text.startswith(F + snip.FENCE[target] + "\n")
        lines = block_lines(text)
        assert lines[0] == "# runs in: " + snip.DEFAULT_RUNS_IN[target]
        assert lines[1] == "# cwd: " + root_for(target)
        assert lines[2] == "# needs: nothing"
        n = 3
        if kind.startswith("git "):
            assert lines[3] == "# recipe: " + kind
            n = 4
        assert lines[n] == snip.cwd_command(target, root_for(target))
        assert lines[-1].startswith("# expect: ") and lines[-1] != "# expect: "
        assert not any(line.startswith("# ") for line in lines[n:-1]), kind
    text = snip.build("run", os=target, cwd=root_for(target), cmd=["ls"], expect="a listing",
                      runs_in="a second window", needs="the repo cloned")
    assert block_lines(text)[0] == "# runs in: a second window"
    assert block_lines(text)[2] == "# needs: the repo cloned"


def test_quoting():
    win = snip.build("run", os="win", cwd="D:\\demo\\it's repo", cmd=["dir"], expect="a listing")
    line = block_lines(win)[3]
    assert line == "Set-Location -LiteralPath 'D:\\demo\\it''s repo'"
    assert ps_unquote(line.split(" ", 2)[2]) == "D:\\demo\\it's repo"
    mac = snip.build("run", os="mac", cwd="/Users/demo/it's repo", cmd=["ls"], expect="a listing")
    assert shlex.split(block_lines(mac)[3]) == ["cd", "--", "/Users/demo/it's repo"]
    home = snip.build("run", os="mac", cwd="~/demo/repo", cmd=["ls"], expect="a listing")
    assert block_lines(home)[1] == "# cwd: ~/demo/repo"
    assert block_lines(home)[3] == 'cd -- "$HOME/demo/repo"'


def test_paste_block():
    text = snip.build("paste", into="the review session, folder D:\\demo\\repo",
                      text="line one\n" + F + "\nline three")
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "````text"
    assert lines[1] == "# paste into: the review session, folder D:\\demo\\repo"
    assert lines[2:5] == ["line one", F, "line three"]
    assert lines[-1] == "````"


def refused(capsys, argv):
    code = snip.main(argv)
    out, err = capsys.readouterr()
    assert code == 2, (argv, out, err)
    assert out == "", argv
    assert len(err.strip().splitlines()) == 1, (argv, err)
    assert err.startswith("snip: refused: "), err
    return err


def test_refusals(capsys, tmp_path, monkeypatch):
    w = ["--os", "win", "--cwd", WIN_ROOT]
    m = ["--os", "mac", "--cwd", MAC_ROOT]
    refused(capsys, ["run", *w, "--cmd", "echo a " + EM + " b", "--expect", "x"])
    refused(capsys, ["run", *w, "--cmd", "dir", "--expect", "done " + EM])
    refused(capsys, ["run", *w, "--cmd", "", "--expect", "x"])
    refused(capsys, ["run", *w, "--cmd", "dir", "--expect", ""])
    refused(capsys, ["run", *w, "--cmd", "dir\ndir", "--expect", "x"])
    refused(capsys, ["run", *w, "--cmd", "git push", "--expect", "x"])
    refused(capsys, ["run", *m, "--cmd", "cd /tmp", "--expect", "x"])
    refused(capsys, ["run", *w, "--cmd", "Set-Location D:\\other", "--expect", "x"])
    refused(capsys, ["run", "--os", "mac", "--cwd", WIN_ROOT, "--cmd", "ls", "--expect", "x"])
    refused(capsys, ["run", "--os", "win", "--cwd", MAC_ROOT, "--cmd", "dir", "--expect", "x"])
    refused(capsys, ["run", "--os", "win", "--cwd", "demo\\repo", "--cmd", "dir", "--expect", "x"])
    for bad in ('~/demo/"x"', "~/demo/$x", "~/demo/`x`"):
        refused(capsys, ["run", "--os", "mac", "--cwd", bad, "--cmd", "ls", "--expect", "x"])
    refused(capsys, ["run", *w, "--expect", "x"])  # no --cmd: an argparse error, still one line
    refused(capsys, ["paste", "--into", "", "--text", "hi"])
    refused(capsys, ["paste", "--into", "a session", "--text", "a " + EM])
    monkeypatch.setattr(snip, "HOST_OS", REAL_HOST)
    refused(capsys, ["run", "--cwd", str(tmp_path / "missing"), "--cmd", "ls", "--expect", "x"])
    # control: a good call prints one block on stdout and the switch state on stderr
    assert snip.main(["run", "--cwd", str(tmp_path), "--cmd", "ls", "--expect", "a listing"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith(F) and out.rstrip("\n").endswith(F)
    assert err.strip() == "output standards: off"
