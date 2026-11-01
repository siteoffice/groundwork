#!/usr/bin/env python3
"""CI helper for the Groundwork install matrix: suite floors, skip lists, a Claude-login probe
and a "Tested on" row. Stdlib only, Python 3.9+, runs on Windows, macOS and Linux runners.

Usage:
  ci_report.py floor --suite NAME --floor N --log FILE
  ci_report.py probe --name NAME [--out FILE] -- <cmd...>
  ci_report.py row --os IMAGE

Output goes to the file named by GITHUB_STEP_SUMMARY when set (appended), else stdout.
"""
from __future__ import annotations

import argparse
import datetime
import os
import platform
import re
import shutil
import subprocess
import sys

COUNT_RE = re.compile(
    r"\b(\d+)\s+(passed|pass|skipped|skip|failed|fail|errors|error|deselected|xfailed|xpassed|warnings|warning)\b"
)
KIND = {"passed": "passed", "pass": "passed", "skipped": "skipped", "skip": "skipped",
        "failed": "failed", "fail": "failed", "error": "failed", "errors": "failed"}
SKIP_RE = re.compile(r"^SKIPPED \[\d+\] (.+)$")
LOGIN_RE = re.compile(r"(?i)log ?in|authenticat|api key|ANTHROPIC_API_KEY")
PROBE_TIMEOUT = 600


def parse_counts(text: str) -> dict:
    """Counts from the last line that carries a passed/skipped/failed/error count."""
    for line in reversed(text.splitlines()):
        found = [(int(n), KIND[w]) for n, w in COUNT_RE.findall(line) if w in KIND]
        if found:
            counts = {"passed": 0, "skipped": 0, "failed": 0}
            for n, kind in found:
                counts[kind] += n
            return counts
    raise ValueError("no test count found")


def parse_skips(text: str) -> list:
    """Entries 'file:line: reason' from pytest -rs lines."""
    skips = []
    for line in text.splitlines():
        m = SKIP_RE.match(line.strip())
        if m:
            skips.append(m.group(1).strip())
    return skips


def check_floor(counts: dict, floor: int) -> bool:
    return counts["failed"] == 0 and counts["passed"] + counts["skipped"] >= floor


def floor_line(suite: str, counts: dict, floor: int, ok: bool) -> str:
    return "%s: %d passed, %d skipped, %d failed (floor %d): %s" % (
        suite, counts["passed"], counts["skipped"], counts["failed"], floor, "ok" if ok else "BELOW FLOOR")


def classify_probe(rc: int, output: str) -> str:
    if rc == 0:
        return "proven"
    if LOGIN_RE.search(output or ""):
        return "skipped: needs a Claude login"
    return "failed"


def tested_on_row(os_image, python, node, claude, date, run_url) -> str:
    claude = str(claude).strip()
    if claude.endswith("(Claude Code)"):
        claude = claude[: -len("(Claude Code)")].strip()
    cells = [os_image, python, node, claude, date, "[run](%s)" % run_url]
    return "| " + " | ".join(str(c).strip() for c in cells) + " |"


def emit(lines) -> None:
    """Append markdown lines to the step summary when set, else print them."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    else:
        for line in lines:
            print(line)


def set_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (key, value))


def cmd_floor(args) -> int:
    with open(args.log, encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        counts = parse_counts(text)
    except ValueError as e:
        emit(["%s: %s in %s (floor %d): BELOW FLOOR" % (args.suite, e, args.log, args.floor)])
        return 1
    ok = check_floor(counts, args.floor)
    lines = [floor_line(args.suite, counts, args.floor, ok)]
    skips = parse_skips(text)
    if skips:
        lines.append("")
        lines.append("Skipped (%s):" % args.suite)
        lines.extend("- " + s for s in skips)
    lines.append("")
    emit(lines)
    return 0 if ok else 1


def run_probe(cmd) -> tuple:
    exe = shutil.which(cmd[0])
    if exe is None:
        return 127, "%s: command not found on PATH" % cmd[0]
    try:
        r = subprocess.run([exe] + list(cmd[1:]), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "%s: timed out after %d s" % (cmd[0], PROBE_TIMEOUT)
    except OSError as e:
        return 126, "%s: could not start: %s" % (cmd[0], e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def cmd_probe(args) -> int:
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("probe: no command given after --", file=sys.stderr)
        return 2
    rc, output = run_probe(cmd)
    print(output)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
    result = classify_probe(rc, output)
    emit(["%s: %s" % (args.name, result), ""])
    set_output("result", result.split(":")[0])
    return 1 if result == "failed" else 0


def version_of(names) -> str:
    for name in names:
        exe = shutil.which(name)
        if exe is None:
            continue
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0 and text:
            return text.splitlines()[0].strip()
    return "n/a"


def cmd_row(args) -> int:
    python = version_of(["python", "python3"])
    if python == "n/a":
        python = platform.python_version()
    if python.startswith("Python "):
        python = python[len("Python "):]
    node = version_of(["node"])
    claude = version_of(["claude"])
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    run_url = "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", ""),
                                          os.environ.get("GITHUB_REPOSITORY", ""),
                                          os.environ.get("GITHUB_RUN_ID", ""))
    emit(["### Tested on row", "", tested_on_row(args.os, python, node, claude, date, run_url), ""])
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command")
    f = sub.add_parser("floor", help="check a suite log against its floor")
    f.add_argument("--suite", required=True)
    f.add_argument("--floor", required=True, type=int)
    f.add_argument("--log", required=True)
    pr = sub.add_parser("probe", help="run a command and classify the result")
    pr.add_argument("--name", required=True)
    pr.add_argument("--out")
    pr.add_argument("cmd", nargs=argparse.REMAINDER)
    r = sub.add_parser("row", help="append the Tested on row")
    r.add_argument("--os", required=True)
    args = p.parse_args(argv)
    if args.command == "floor":
        return cmd_floor(args)
    if args.command == "probe":
        return cmd_probe(args)
    if args.command == "row":
        return cmd_row(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
