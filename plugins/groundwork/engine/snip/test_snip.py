"""Tests for snip.py: the switch, the header grammar, quoting, the git recipes and refusals."""
import json
import os
import pathlib
import shlex
import shutil
import subprocess
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
    """One sample per command kind for the target system."""
    cmd = "py -3 -m pytest -q" if target == "win" else "python3 -m pytest -q"
    engine = ("D:\\demo\\groundwork\\plugins\\groundwork\\engine\\sync\\git_flow.py" if target == "win"
              else "/Users/demo/groundwork/plugins/groundwork/engine/sync/git_flow.py")
    return [
        ("run", {"cmd": [cmd], "expect": "all tests pass"}),
        ("git save", {"files": ["a.txt", "docs/it's.md"], "message": "fix the user's page",
                      "body": "Why: the page broke on save.",
                      "co_author": "Demo Person <demo@example.com>"}),
        ("git share", {}),
        ("git land", {"subject": "Merge the user's page", "engine": engine}),
        ("git tag", {"tag": "v1.2.0", "message": "Release 1.2.0"}),
        ("git sync", {}),
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
    for target in ("win", "mac"):
        save = dict(kind_args(target))["git save"]
        lines = block_lines(snip.build("git save", os=target, cwd=root_for(target), **save))
        add, commit = lines[5], lines[6]
        if target == "mac":
            assert shlex.split(add) == ["git", "add", "--", "a.txt", "docs/it's.md"]
            assert shlex.split(commit)[3] == "fix the user's page"
        else:
            assert add == "git add -- 'a.txt' 'docs/it''s.md'"
            assert commit.startswith("git commit -m 'fix the user''s page' -m ")


def test_paste_block():
    text = snip.build("paste", into="the review session, folder D:\\demo\\repo",
                      text="line one\n" + F + "\nline three")
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "````text"
    assert lines[1] == "# paste into: the review session, folder D:\\demo\\repo"
    assert lines[2:5] == ["line one", F, "line three"]
    assert lines[-1] == "````"


def refused(capsys, argv, reason):
    code = snip.main(argv)
    out, err = capsys.readouterr()
    assert code == 2, (argv, out, err)
    assert out == "", argv
    assert len(err.strip().splitlines()) == 1, (argv, err)
    assert err.startswith("snip: refused: "), err
    assert reason in err, (reason, err)
    return err


def test_refusals(capsys, tmp_path, monkeypatch):
    w = ["--os", "win", "--cwd", WIN_ROOT]
    m = ["--os", "mac", "--cwd", MAC_ROOT]
    refused(capsys, ["run", *w, "--cmd", "echo a " + EM + " b", "--expect", "x"], "em dash")
    refused(capsys, ["run", *w, "--cmd", "dir", "--expect", "done " + EM], "em dash")
    refused(capsys, ["run", *w, "--cmd", "", "--expect", "x"], "empty")
    refused(capsys, ["run", *w, "--cmd", "dir", "--expect", ""], "empty")
    refused(capsys, ["run", *w, "--cmd", "dir\ndir", "--expect", "x"], "line break")
    refused(capsys, ["run", *w, "--cmd", "git push", "--expect", "x"], "git recipe")
    refused(capsys, ["run", *m, "--cmd", "cd /tmp", "--expect", "x"], "sets the folder")
    refused(capsys, ["run", *w, "--cmd", "Set-Location D:\\other", "--expect", "x"], "sets the folder")
    refused(capsys, ["run", "--os", "mac", "--cwd", WIN_ROOT, "--cmd", "ls", "--expect", "x"],
            "start with / or ~/")
    refused(capsys, ["run", "--os", "win", "--cwd", MAC_ROOT, "--cmd", "dir", "--expect", "x"],
            "Windows path")
    refused(capsys, ["run", "--os", "win", "--cwd", "demo\\repo", "--cmd", "dir", "--expect", "x"],
            "Windows path")
    for bad in ('~/demo/"x"', "~/demo/$x", "~/demo/`x`", "~/demo/a!b"):
        refused(capsys, ["run", "--os", "mac", "--cwd", bad, "--cmd", "ls", "--expect", "x"],
                "must not hold")
    # no --cmd: still one line
    refused(capsys, ["run", *w, "--expect", "x"], "--cmd is required")
    refused(capsys, ["paste", "--into", "", "--text", "hi"], "empty")
    refused(capsys, ["paste", "--into", "a session", "--text", "a " + EM], "em dash")
    refused(capsys, ["git", "save", *w, "--files", "a.txt", "--message", "one\ntwo"], "line break")
    refused(capsys, ["git", "save", *w, "--files", "a.txt", "--message", "fix " + EM + " it"],
            "em dash")
    refused(capsys, ["git", "tag", *m, "--tag", "", "--message", "x"], "empty")
    refused(capsys, ["git", "land", *m], "--engine is required")  # not the host, no --engine
    monkeypatch.setattr(snip, "HOST_OS", REAL_HOST)
    refused(capsys, ["run", "--cwd", str(tmp_path / "missing"), "--cmd", "ls", "--expect", "x"],
            "folder not found")
    # control: a good call prints one block on stdout and the switch state on stderr
    assert snip.main(["run", "--cwd", str(tmp_path), "--cmd", "ls", "--expect", "a listing"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith(F) and out.rstrip("\n").endswith(F)
    assert err.strip() == "output standards: off"


def save(capsys, *extra):
    code = snip.main(["git", "save", "--os", "mac", "--cwd", MAC_ROOT, *extra])
    out, err = capsys.readouterr()
    return code, out, err


def test_save_form(capsys):
    code, out, _ = save(capsys, "--files", "a.txt", "b c.txt", "--message", "fix the login page")
    assert code == 0
    lines = block_lines(out)
    assert lines[3] == "# recipe: git save"
    assert lines[5] == "git add -- 'a.txt' 'b c.txt'"
    assert lines[6] == "git commit -m 'fix the login page'"
    assert lines[7] == "git status --short"
    code, out, _ = save(capsys, "--files", "a.txt", "--message", "fix it", "--body", "Why: it broke.",
                        "--co-author", "Demo Person <demo@example.com>")
    assert code == 0
    assert block_lines(out)[6] == ("git commit -m 'fix it' -m 'Why: it broke.' "
                                   "-m 'Co-Authored-By: Demo Person <demo@example.com>'")
    def save_refused(reason, *extra):
        code, out, err = save(capsys, *extra)
        assert code == 2 and out == "" and len(err.strip().splitlines()) == 1, extra
        assert reason in err, (reason, err)

    for bad in (".", "*"):
        save_refused("list each file", "--files", bad, "--message", "fix it")
    for bad in ("-A", "--all"):  # argparse takes these for flags, so the guard is checked on build
        with pytest.raises(snip.Refusal, match="list each file"):
            snip.build("git save", os="mac", cwd=MAC_ROOT, files=[bad], message="fix it")
        save_refused("expected at least one argument", "--files", bad, "--message", "fix it")
    save_refused("72", "--files", "a.txt", "--message", "x" * 73)
    assert save(capsys, "--files", "a.txt", "--message", "x" * 72)[0] == 0
    save_refused("period", "--files", "a.txt", "--message", "fix it.")
    save_refused("relative to the cwd", "--files", "/Users/demo/repo/a.txt", "--message", "fix it")
    save_refused("Name <email>", "--files", "a.txt", "--message", "fix it", "--co-author", "nobody")


def test_land_engine_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(snip, "HOST_OS", REAL_HOST)
    code = snip.main(["git", "land", "--cwd", str(tmp_path), "--subject", "Merge the login page"])
    out, err = capsys.readouterr()
    assert code == 0, err
    line = block_lines(out)[5]
    if REAL_HOST == "win":
        assert line.startswith("py -3 '")
        engine = ps_unquote(line[len("py -3 "):line.index(" land --apply")])
        assert line.endswith(" land --apply --repo " + snip.quote("win", str(tmp_path))
                             + " --subject 'Merge the login page'")
    else:
        parts = shlex.split(line)
        assert parts[0] == "python3"
        engine = parts[1]
        assert parts[2:] == ["land", "--apply", "--repo", str(tmp_path), "--subject",
                             "Merge the login page"]
    assert pathlib.Path(engine).is_file()
    assert pathlib.Path(engine) == HERE.parent / "sync" / "git_flow.py"


def test_every_kind_passes_the_lint(tmp_path, capsys, monkeypatch):
    import snippet_lint

    (tmp_path / "siteoffice.json").write_bytes(
        json.dumps({"settings": {"outputStandards": True}}).encode("utf-8"))
    parts = ["# Generated blocks", ""]
    for target in ("win", "mac"):
        for kind, args in kind_args(target):
            parts += [snip.build(kind, os=target, cwd=root_for(target), **args), ""]
    parts += [snip.build("run", os="mac", cwd="~/demo/repo", cmd=["ls"], expect="a listing"), ""]
    parts += [snip.build("git land", os="mac", cwd="~/demo/repo",
                         engine="/Users/demo/groundwork/plugins/groundwork/engine/sync/git_flow.py"), ""]
    parts += [snip.build("paste", into="the review session, folder D:\\demo\\repo",
                         text="line one\n" + F + "\nline three"), ""]
    # the host target: a real folder (tmp_path), land takes the engine path from snip itself
    monkeypatch.setattr(snip, "HOST_OS", REAL_HOST)
    for kind, args in kind_args(REAL_HOST):
        if kind == "git land":
            args = {"subject": args["subject"]}
        parts += [snip.build(kind, os=REAL_HOST, cwd=str(tmp_path), **args), ""]
    path = tmp_path / "blocks.md"
    path.write_bytes("\n".join(parts).encode("utf-8"))
    assert snippet_lint.main([str(path)]) == 0
    assert capsys.readouterr() == ("", "")


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh on the PATH")
def test_sh_syntax():
    sh = shutil.which("sh")
    samples = [snip.build(kind, os="mac", cwd=MAC_ROOT, **args) for kind, args in kind_args("mac")]
    samples.append(snip.build("run", os="mac", cwd="~/demo/repo", cmd=["ls"], expect="a listing"))
    for text in samples:
        body = "\n".join(block_lines(text)) + "\n"
        result = subprocess.run([sh, "-n"], input=body, text=True, capture_output=True)
        assert result.returncode == 0, (text, result.stderr)


def test_quoting_tag_and_land_lines():
    """Every quoted line of the tag and land blocks splits cleanly for sh."""
    kinds = dict(kind_args("mac"))
    tag = block_lines(snip.build("git tag", os="mac", cwd=MAC_ROOT, tag="v1.2.0's", message="it's out"))
    tag_lines = [ln for ln in tag if ln.startswith("git ")]
    assert len(tag_lines) == 3
    assert shlex.split(tag_lines[0]) == ["git", "tag", "-a", "v1.2.0's", "-m", "it's out"]
    assert shlex.split(tag_lines[1]) == ["git", "push", "origin", "v1.2.0's"]
    assert shlex.split(tag_lines[2]) == ["git", "ls-remote", "--tags", "origin", "v1.2.0's"]
    land = block_lines(snip.build("git land", os="mac", cwd=MAC_ROOT, **kinds["git land"]))
    parts = shlex.split([ln for ln in land if ln.startswith("python3 ")][0])
    assert parts[:2] == ["python3", kinds["git land"]["engine"]]
    assert parts[-1] == "Merge the user's page"


@pytest.mark.parametrize("kind,extra", [
    ("git save", {"files": ["a.txt"], "message": "fix it"}),
    ("git share", {}),
    ("git land", {"engine": "/Users/demo/groundwork/engine/sync/git_flow.py"}),
    ("git tag", {"tag": "v1.0.0", "message": "Release"}),
    ("git sync", {}),
])
def test_empty_expect_refused(kind, extra):
    """An explicit empty or blank --expect is refused, not replaced by the default."""
    for bad in ("", "   "):
        with pytest.raises(snip.Refusal):
            snip.build(kind, os="mac", cwd=MAC_ROOT, expect=bad, **extra)
    assert snip.build(kind, os="mac", cwd=MAC_ROOT, **extra).count("# expect: ") == 1


def test_powershell_curly_quotes_doubled():
    """PowerShell treats four Unicode quotes as single quotes: each is doubled."""
    for ch in ("\u2018", "\u2019", "\u201a", "\u201b"):
        assert snip.quote("win", "a" + ch + "b") == "'a" + ch + ch + "b'"
    assert snip.quote("win", "it's") == "'it''s'"
    text = snip.build("run", os="win", cwd="D:\\demo\\it\u2019s", cmd=["dir"], expect="a listing")
    assert "'D:\\demo\\it\u2019\u2019s'" in text
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh not on PATH")
    line = block_lines(text)[3]
    script = ("$e=$null;[void][System.Management.Automation.Language.Parser]::ParseInput("
              "$env:SNIP_LINE,[ref]$null,[ref]$e);$e.Count")
    env = dict(os.environ, SNIP_LINE=line)
    done = subprocess.run([pwsh, "-NoProfile", "-Command", script], capture_output=True,
                          text=True, env=env)
    assert done.stdout.strip() == "0", done


def test_mac_home_backslash_refused():
    """A backslash in a ~/ path would break the double-quoted cd."""
    with pytest.raises(snip.Refusal):
        snip.check_cwd("mac", "~/foo\\", exists=False)
    with pytest.raises(snip.Refusal):
        snip.build("run", os="mac", cwd="~/foo\\bar", cmd=["ls"], expect="x")


@pytest.mark.parametrize("cmd", ["cd..", "cd\\", "cd;x", "cd ~", "CD /tmp", "Set-Location;dir",
                                 "  set-location D:\\x", "cd", "Set-Location"])
def test_run_refuses_folder_change(cmd):
    """The folder change is caught even without a space after the word."""
    with pytest.raises(snip.Refusal):
        snip.build("run", os="win", cwd=WIN_ROOT, cmd=[cmd], expect="x")


def _siteoffice_repo(tmp_path):
    repo = tmp_path / "repo"
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    (repo / "siteoffice.json").write_bytes(
        json.dumps({"settings": {"outputStandards": True}}).encode("utf-8"))
    (deep / "x.md").write_bytes(b"hello\n")
    return repo, deep


def test_find_siteoffice_relative_path(tmp_path, monkeypatch, capsys):
    import snippet_lint

    repo, deep = _siteoffice_repo(tmp_path)
    monkeypatch.chdir(deep)
    assert snip.standards_on("x.md") is True
    assert snip.standards_on(".") is True
    assert snippet_lint.main(["x.md"]) == 0
    assert "skipped" not in capsys.readouterr().err


def test_find_siteoffice_home_form(tmp_path, monkeypatch):
    repo, deep = _siteoffice_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert snip.standards_on("~/repo/a/b/x.md") is True
    assert snip.standards_on("~/repo/a/b") is True


def test_main_home_cwd_reports_on(tmp_path, monkeypatch, capsys):
    repo, deep = _siteoffice_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(snip, "HOST_OS", "mac")
    code = snip.main(["run", "--os", "mac", "--cwd", "~/repo/a/b", "--cmd", "ls", "--expect", "x"])
    out, err = capsys.readouterr()
    assert code == 0, err
    assert err.startswith("output standards: on ("), err


@pytest.mark.parametrize("argv,reason", [
    (["run", "--cmd", "echo \x07", "--expect", "x"], "control character"),
    (["run", "--cmd", "echo \x85", "--expect", "x"], "control character"),
    (["run", "--cmd", "echo \x1b[0m", "--expect", "x"], "control character"),
    (["run", "--cmd", "echo " + F, "--expect", "x"], "fence"),
    (["run", "--cmd", "echo ~~~~", "--expect", "x"], "fence"),
    (["run", "--cmd", "ls", "--expect", "ok " + F], "fence"),
])
def test_unsafe_one_line_values_refused(capsys, argv, reason):
    refused(capsys, [argv[0], "--os", "win", "--cwd", WIN_ROOT] + argv[1:], reason)


def test_unsafe_files_and_paste_refused(capsys):
    w = ["--os", "win", "--cwd", WIN_ROOT]
    refused(capsys, ["git", "save", *w, "--files", "a\x08.txt", "--message", "fix it"],
            "control character")
    refused(capsys, ["paste", "--into", "a session", "--text", "a\nb\x07c"], "control character")
    refused(capsys, ["paste", "--into", "a session", "--text", "a\n\x85"], "control character")
    text = snip.build("run", os="win", cwd=WIN_ROOT, cmd=["echo a\tb"], expect="x")
    assert "echo a\tb" in text
    assert "a\tb" in snip.build("paste", into="s", text="a\tb\nc")


def test_paste_fence_counts_indented_fence_lines(tmp_path, capsys):
    import snippet_lint

    text = snip.build("paste", into="a session", text="a\n  " + F + "\nb")
    assert text.startswith("`" * 4 + "text\n")
    fences = snippet_lint.parse_fences(text.rstrip("\n").split("\n"))
    assert len(fences) == 1 and fences[0].close_line == len(text.rstrip("\n").split("\n"))
    deep = snip.build("paste", into="a session", text="a\n   ~~~\n   " + "`" * 5 + "\nb")
    assert deep.startswith("`" * 6 + "text\n")


@pytest.mark.parametrize("cmd", ["cdk deploy", "cd-tool run", "cd_x", "Set-LocationX"])
def test_run_allows_lookalikes(cmd):
    assert cmd in snip.build("run", os="win", cwd=WIN_ROOT, cmd=[cmd], expect="x")
