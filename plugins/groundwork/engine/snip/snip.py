"""snip.py: print one command block or paste block in one fixed form.

It prints Markdown code blocks for Windows (PowerShell) and Mac (sh): a header
(runs in, cwd, needs), the folder command, the body and an expect line. The
snippet lint imports the header grammar from here, so the two cannot drift.

Standard library only, Python 3.9 or newer, pure ASCII. Calling it is a choice:
it reports whether a repo set "outputStandards": true in the settings of its
siteoffice.json, but it never refuses on that switch.
"""
import argparse
import os
import pathlib
import re
import sys
from typing import Optional

HOST_OS = "win" if os.name == "nt" else "mac"
EM_DASH = "\u2014"

PS_LANGS = ("powershell", "pwsh", "ps1", "ps")
SH_LANGS = ("sh", "bash", "zsh", "shell")
HEADER_PREFIXES = ("# runs in: ", "# cwd: ", "# needs: ")
RECIPE_PREFIX = "# recipe: git "
RECIPES = ("save", "share", "land", "tag", "sync")
EXPECT_PREFIX = "# expect: "
PASTE_PREFIX = "# paste into: "
FENCE = {"win": "powershell", "mac": "sh"}
DEFAULT_RUNS_IN = {
    "win": "PowerShell 7 on Windows, normal window",
    "mac": "Terminal on the Mac (zsh)",
}
DEFAULT_NEEDS = "nothing"
ENGINE_PATH = pathlib.Path(__file__).resolve().parent.parent / "sync" / "git_flow.py"
DEFAULT_EXPECT = {
    "save": "one new commit, and git status --short no longer lists the saved files",
    "share": "the branch is on origin and git status -sb shows no ahead count",
    "land": "the engine reports the pull request merged and the work branch removed",
    "tag": "git ls-remote prints one line ending in refs/tags/%s",
    "sync": "git status -sb shows the branch level with its upstream",
}

_TICKS = "`" * 3


class Refusal(ValueError):
    """A call the generator will not turn into a block."""


def find_siteoffice(path) -> Optional[pathlib.Path]:
    p = pathlib.Path(path)
    start = p if p.is_dir() else p.parent
    for folder in (start,) + tuple(start.parents):
        candidate = folder / "siteoffice.json"
        if candidate.is_file():
            return candidate
    return None


def standards_on(path) -> bool:
    import json

    cfg = find_siteoffice(path)
    if cfg is None:
        return False
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return False
    return settings.get("outputStandards") is True


def quote(target, value) -> str:
    if target == "win":
        return "'" + value.replace("'", "''") + "'"
    return "'" + value.replace("'", "'\\''") + "'"


def one_line(name, value) -> str:
    if value is None or value.strip() == "":
        raise Refusal("%s is empty" % name)
    if "\n" in value or "\r" in value:
        raise Refusal("%s holds a line break" % name)
    if EM_DASH in value:
        raise Refusal("%s holds an em dash (U+2014)" % name)
    return value


def check_cwd(target, path, exists=None) -> None:
    one_line("--cwd", path)
    if target == "win":
        if not re.match(r"^[A-Za-z]:\\", path):
            raise Refusal("--cwd must be a Windows path like X:\\... for --os win")
    else:
        if not (path.startswith("/") or path.startswith("~/")):
            raise Refusal("--cwd must start with / or ~/ for --os mac")
        if path.startswith("~/") and any(c in path for c in ('"', "$", "`")):
            raise Refusal("--cwd with ~/ must not hold a double quote, $ or a backtick")
    if exists is True or (exists is None and target == HOST_OS):
        real = os.path.expanduser(path) if path.startswith("~/") else path
        if not os.path.isdir(real):
            raise Refusal("--cwd folder not found: %s" % path)


def cwd_command(target, path) -> str:
    if target == "win":
        return "Set-Location -LiteralPath " + quote("win", path)
    if path.startswith("~/"):
        return 'cd -- "$HOME/' + path[2:] + '"'
    return "cd -- " + quote("mac", path)


def _target(args):
    target = args.get("os") or HOST_OS
    if target not in ("win", "mac"):
        raise Refusal("--os must be win or mac")
    return target


def _command_block(target, args, recipe, body, expect):
    cwd = args.get("cwd")
    check_cwd(target, cwd)
    runs_in = args.get("runs_in")
    needs = args.get("needs")
    runs_in = DEFAULT_RUNS_IN[target] if runs_in is None else one_line("--runs-in", runs_in)
    needs = DEFAULT_NEEDS if needs is None else one_line("--needs", needs)
    expect = one_line("--expect", expect)
    lines = [_TICKS + FENCE[target],
             HEADER_PREFIXES[0] + runs_in,
             HEADER_PREFIXES[1] + cwd,
             HEADER_PREFIXES[2] + needs]
    if recipe:
        lines.append(RECIPE_PREFIX + recipe)
    lines.append(cwd_command(target, cwd))
    lines.extend(body)
    lines.append(EXPECT_PREFIX + expect)
    lines.append(_TICKS)
    return "\n".join(lines) + "\n"


def _build_run(target, args):
    cmds = args.get("cmd") or []
    if not cmds:
        raise Refusal("--cmd is required")
    body = []
    for c in cmds:
        one_line("--cmd", c)
        word = c.split()[0].lower()
        if word == "git":
            raise Refusal("--cmd starts with git: use a git recipe")
        if word in ("set-location", "cd"):
            raise Refusal("--cmd sets the folder: the header does that")
        body.append(c)
    if args.get("expect") is None:
        raise Refusal("--expect is required")
    return _command_block(target, args, None, body, args["expect"])


def _build_paste(args):
    text, file = args.get("text"), args.get("file")
    if (text is None) == (file is None):
        raise Refusal("give --text or --file, not both")
    into = one_line("--into", args.get("into"))
    if file is not None:
        try:
            with open(file, "rb") as fh:
                text = fh.read().decode("utf-8")
        except (OSError, ValueError) as exc:
            raise Refusal("--file cannot be read: %s" % exc)
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]
    if text.strip() == "":
        raise Refusal("paste text is empty")
    if EM_DASH in text:
        raise Refusal("paste text holds an em dash (U+2014)")
    body = text.replace("\r\n", "\n").split("\n")
    longest = 0
    for line in body:
        m = re.match(r"^`+", line)
        if m:
            longest = max(longest, len(m.group(0)))
    fence = "`" * max(3, longest + 1)
    return "\n".join([fence + "text", PASTE_PREFIX + into] + body + [fence]) + "\n"


def _abs_in(target, name, value):
    one_line(name, value)
    if target == "win":
        return re.match(r"^[A-Za-z]:\\", value) is not None
    return value.startswith("/")


def _build_save(target, args):
    files = args.get("files") or []
    if not files:
        raise Refusal("--files is required")
    for f in files:
        one_line("--files", f)
        if f in (".", "*", "-A", "--all"):
            raise Refusal("--files names %s: list each file" % f)
        if f.startswith("/") or f.startswith("~/") or re.match(r"^[A-Za-z]:\\", f):
            raise Refusal("--files must be relative to the cwd: %s" % f)
    message = one_line("--message", args.get("message"))
    if len(message) > 72:
        raise Refusal("--message is longer than 72 characters")
    if message.endswith("."):
        raise Refusal("--message ends with a period")
    commit = "git commit -m " + quote(target, message)
    if args.get("body") is not None:
        commit += " -m " + quote(target, one_line("--body", args["body"]))
    co = args.get("co_author")
    if co is not None:
        one_line("--co-author", co)
        if not re.match(r"^[^<>]+ <[^<>\s]+@[^<>\s]+>$", co):
            raise Refusal("--co-author must look like: Name <email>")
        commit += " -m " + quote(target, "Co-Authored-By: " + co)
    body = ["git add -- " + " ".join(quote(target, f) for f in files), commit,
            "git status --short"]
    expect = args.get("expect") or DEFAULT_EXPECT["save"]
    return _command_block(target, args, "save", body, expect)


def _build_land(target, args):
    if target == HOST_OS:
        engine = str(ENGINE_PATH)
    else:
        engine = args.get("engine")
        if engine is None:
            raise Refusal("--engine is required when --os is not this system")
        if not _abs_in(target, "--engine", engine):
            raise Refusal("--engine must be an absolute path for --os %s" % target)
    cwd = args.get("cwd")
    check_cwd(target, cwd)
    if cwd.startswith("~/"):
        repo = '"$HOME/' + cwd[2:] + '"'
    else:
        repo = quote(target, cwd)
    line = ("py -3 " if target == "win" else "python3 ") + quote(target, engine)
    line += " land --apply --repo " + repo
    if args.get("subject") is not None:
        line += " --subject " + quote(target, one_line("--subject", args["subject"]))
    expect = args.get("expect") or DEFAULT_EXPECT["land"]
    return _command_block(target, args, "land", [line], expect)


def _build_git(kind, args):
    target = _target(args)
    name = kind[4:]
    q = lambda v: quote(target, v)  # noqa: E731
    if name == "save":
        return _build_save(target, args)
    if name == "land":
        return _build_land(target, args)
    expect = args.get("expect") or DEFAULT_EXPECT.get(name)
    if name == "share":
        body = ["git push -u origin HEAD", "git status -sb"]
    elif name == "tag":
        tag = one_line("--tag", args.get("tag"))
        one_line("--message", args.get("message"))
        body = ["git tag -a " + q(tag) + " -m " + q(args["message"]),
                "git push origin " + q(tag),
                "git ls-remote --tags origin " + q(tag)]
        if not args.get("expect"):
            expect = DEFAULT_EXPECT["tag"] % tag
    else:
        body = ["git fetch --prune origin", "git merge --ff-only " + q("@{u}"),
                "git status -sb"]
    return _command_block(target, args, name, body, expect)


def build(kind, **args) -> str:
    if kind == "run":
        return _build_run(_target(args), args)
    if kind == "paste":
        return _build_paste(args)
    if kind in ("git " + r for r in RECIPES):
        return _build_git(kind, args)
    raise Refusal("unknown kind: %s" % kind)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("snip: refused: %s\n" % message)
        raise SystemExit(2)


def _shared(parser):
    parser.add_argument("--os", dest="os", choices=("win", "mac"))
    parser.add_argument("--cwd")
    parser.add_argument("--runs-in", dest="runs_in")
    parser.add_argument("--needs")
    parser.add_argument("--expect")


def _make_parser():
    parser = _Parser(prog="snip.py")
    sub = parser.add_subparsers(dest="kind", required=True, parser_class=_Parser)
    run = sub.add_parser("run")
    _shared(run)
    run.add_argument("--cmd", action="append")
    paste = sub.add_parser("paste")
    paste.add_argument("--into")
    paste.add_argument("--text")
    paste.add_argument("--file")
    git = sub.add_parser("git")
    gsub = git.add_subparsers(dest="recipe", required=True, parser_class=_Parser)
    for name in RECIPES:
        sp = gsub.add_parser(name)
        _shared(sp)
        if name == "save":
            sp.add_argument("--files", nargs="+")
            sp.add_argument("--message")
            sp.add_argument("--body")
            sp.add_argument("--co-author", dest="co_author")
        elif name == "land":
            sp.add_argument("--subject")
            sp.add_argument("--engine")
        elif name == "tag":
            sp.add_argument("--tag")
            sp.add_argument("--message")
    return parser


def main(argv=None) -> int:
    try:
        ns = _make_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    kind = ns.kind
    if kind == "git":
        kind = "git " + ns.recipe
    args = {k: v for k, v in vars(ns).items() if k not in ("kind", "recipe")}
    try:
        block = build(kind, **args)
        target = args.get("os") or HOST_OS
        if kind != "paste" and target == HOST_OS:
            where = args["cwd"]
        else:
            where = os.getcwd()
    except Refusal as exc:
        sys.stderr.write("snip: refused: %s\n" % exc)
        return 2
    cfg = find_siteoffice(where) if standards_on(where) else None
    sys.stdout.write(block)
    if cfg is not None:
        sys.stderr.write("output standards: on (%s)\n" % cfg)
    else:
        sys.stderr.write("output standards: off\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
