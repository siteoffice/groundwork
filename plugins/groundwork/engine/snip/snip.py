"""snip.py: print one command block or paste block in one fixed form.

It prints Markdown code blocks for Windows (PowerShell) and Mac (sh): a header
(runs in, cwd, needs), the folder command, the body and an expect line. The
snippet lint imports the header grammar from here, so the two cannot drift.

Standard library only, Python 3.9 or newer, pure ASCII. Calling it is a choice:
it reports whether a repo set "outputStandards": true in the settings of its
siteoffice.json, but it never refuses on that switch.
"""
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


def build(kind, **args) -> str:
    if kind == "run":
        return _build_run(_target(args), args)
    if kind == "paste":
        return _build_paste(args)
    raise Refusal("unknown kind: %s" % kind)


if __name__ == "__main__":
    sys.exit(0)
