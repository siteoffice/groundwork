"""snippet_lint.py: refuse hand-written or damaged command blocks in Markdown files.

Usage: snippet_lint.py <file>... [--force]

It reads Markdown, finds fenced code blocks (CommonMark: three or more backticks
or tildes, closed by the same character at least as long) and checks that every
PowerShell and shell block has the header snip.py prints, that git blocks name
a recipe, that fences with commands have a language, and that paste blocks name
where they go. The header grammar is imported from snip.py, never redefined.

Exit codes: 0 pass (no output), 1 one stdout line per failure
(<path>:<line>: L<n>: <message>), 2 usage error.

Off by default: a file whose repo has no "outputStandards": true in the settings
of its nearest siteoffice.json is skipped with one stderr line; --force checks
it anyway. It checks files, not chat replies.

Standard library only, Python 3.9 or newer, pure ASCII.
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snip  # noqa: E402

Fence = collections.namedtuple("Fence", "open_line close_line lang lines")

LANGLESS_PREFIXES = ("git ", "Set-Location", "cd ", "py ", "python", "& ")
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def parse_fences(lines):
    fences, i = [], 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
            i += 1
            continue
        char, size = m.group(1)[0], len(m.group(1))
        info = m.group(2).strip()
        lang = info.split()[0].lower() if info else ""
        close = re.compile(r"^ {0,3}" + re.escape(char) + "{%d,}[ \t]*$" % size)
        body, j = [], i + 1
        while j < len(lines) and not close.match(lines[j]):
            body.append((j + 1, lines[j]))
            j += 1
        fences.append(Fence(i + 1, j + 1 if j < len(lines) else None, lang, body))
        i = j + 1
    return fences


def _value(text, prefix):
    """The value after prefix, or None when the line does not start with it or has no value."""
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix):]
    return rest if rest.strip() else None


def check_command(fence, target, rule):
    lines = list(fence.lines)
    while lines and not lines[0][1].strip():
        lines.pop(0)
    while lines and not lines[-1][1].strip():
        lines.pop()
    if not lines:
        return [(fence.open_line, "%s: empty snippet" % rule)]
    fails = []
    cwd_value = None
    header_ok = True
    for i, prefix in enumerate(snip.HEADER_PREFIXES):
        text = lines[i][1] if i < len(lines) else None
        value = _value(text, prefix) if text is not None else None
        if value is None:
            line_no = lines[i][0] if i < len(lines) else lines[-1][0]
            fails.append((line_no, "%s: expected '%s' with a value" % (rule, prefix)))
            header_ok = False
            break
        if i == 1:
            cwd_value = value
    if header_ok:
        idx = len(snip.HEADER_PREFIXES)
        if idx < len(lines) and lines[idx][1].startswith(snip.RECIPE_PREFIX):
            idx += 1
        cwd_line_no = lines[1][0]
        try:
            snip.check_cwd(target, cwd_value, exists=False)
        except snip.Refusal as exc:
            fails.append((cwd_line_no, "%s: %s" % (rule, exc)))
        else:
            expected = snip.cwd_command(target, cwd_value)
            if idx >= len(lines) or lines[idx][1] != expected:
                line_no = lines[idx][0] if idx < len(lines) else lines[-1][0]
                fails.append((line_no, "%s: cwd command must be: %s" % (rule, expected)))
    if _value(lines[-1][1], snip.EXPECT_PREFIX) is None:
        fails.append((lines[-1][0], "%s: last line must be '%s' with a value"
                      % (rule, snip.EXPECT_PREFIX)))
    return fails


def _git_line(text):
    words = text.split()
    return bool(words) and words[0] == "git" or "git_flow" in text


def _check_git(fence):
    fails = []
    has_recipe = False
    for n, text in fence.lines:
        if text.startswith(snip.RECIPE_PREFIX):
            has_recipe = True
            name = text[len(snip.RECIPE_PREFIX):].strip()
            if name not in snip.RECIPES:
                fails.append((n, "L3: unknown recipe '%s', use save, share, land, tag or sync"
                              % name))
    if not has_recipe:
        for n, text in fence.lines:
            if _git_line(text):
                fails.append((n, "L3: git block needs '# recipe: git <save|share|land|tag|sync>'"))
                break
    return fails


DRIVE_PAREN = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\[^\s`]*?[A-Za-z0-9_]\(")


def strip_inline_code(line):
    return re.sub(r"(`+)(.+?)\1", lambda m: " " * len(m.group(0)), line)


def damaged_chars(text):
    """One (line_number, message) per line with a damaged character, at its first one."""
    found = []
    line_no = 1
    reported = set()
    for i, c in enumerate(text):
        code = ord(c)
        if c == "\n":
            line_no += 1
            continue
        if line_no in reported:
            continue
        if code < 32 and c != "\t" and not (c == "\r" and text[i + 1:i + 2] == "\n"):
            msg = "L6: damaged text: control character U+%04X" % code
        elif 0x80 <= code <= 0x9F:
            msg = "L6: damaged text: control character U+%04X" % code
        elif 0xDC80 <= code <= 0xDC9F:
            msg = "L6: damaged text: byte 0x%02X is not UTF-8" % (code - 0xDC00)
        else:
            continue
        reported.add(line_no)
        found.append((line_no, msg))
    return found


def lint_text(text):
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    fails = list(damaged_chars(text))
    fenced = set()
    for fence in parse_fences(lines):
        fenced.add(fence.open_line)
        if fence.close_line:
            fenced.add(fence.close_line)
        fenced.update(n for n, _ in fence.lines)
    for n, line in enumerate(lines, 1):
        if n not in fenced and DRIVE_PAREN.search(strip_inline_code(line)):
            fails.append((n, "L7: damaged path: '(' right after a name, "
                             "a backslash was probably eaten"))
    for fence in parse_fences(lines):
        if fence.lang in snip.PS_LANGS:
            fails.extend(check_command(fence, "win", "L1"))
            fails.extend(_check_git(fence))
        elif fence.lang in snip.SH_LANGS:
            fails.extend(check_command(fence, "mac", "L2"))
            fails.extend(_check_git(fence))
        elif fence.lang == "":
            for n, body in fence.lines:
                if body.lstrip().startswith(LANGLESS_PREFIXES):
                    fails.append((n, "L4: snippet fence needs a language"))
                    break
        elif fence.lang == "text" and fence.lines:
            n, first = fence.lines[0]
            if first.startswith("# paste into:") and not first[len("# paste into:"):].strip():
                fails.append((n, "L5: '# paste into:' needs a value"))
    return sorted(fails, key=lambda f: (f[0], f[1][:2]))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("snippet_lint: %s\n" % message)
        raise SystemExit(2)


def main(argv=None):
    parser = _Parser(prog="snippet_lint.py")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--force", action="store_true")
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if not ns.files:
        sys.stderr.write("snippet_lint: give at least one file\n")
        return 2
    usage_error = False
    failed = False
    for path in ns.files:
        if not os.path.isfile(path):
            sys.stderr.write("snippet_lint: file not found: %s\n" % path)
            usage_error = True
            continue
        if not ns.force and not snip.standards_on(path):
            sys.stderr.write("skipped (output standards off): %s\n" % path)
            continue
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", errors="surrogateescape")
        for line_no, message in lint_text(text):
            sys.stdout.write("%s:%d: %s\n" % (path, line_no, message))
            failed = True
    if usage_error:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
