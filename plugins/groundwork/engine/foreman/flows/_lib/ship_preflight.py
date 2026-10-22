"""ship_preflight.py - the nine deterministic pre-flight checks behind G4.

Slice c of the Foreman build order. Design:
(design spec, not shipped)

Split the way terminal_sweep.py splits: a pure section that takes
already-gathered facts and returns the results dict, and one public host
function, run_preflight(), that gathers those facts with git.

Two hard rules from the parent spec. A failing check NEVER raises, and a
failing check NEVER blocks the gate from opening: this module annotates a ship
decision, the operator makes it. And scope is never guessed: a spec with no
fenced path list yields scope_respected None, not a pass and not a fail.

Stdlib only, plus brief_composer for the secret patterns. Python 3.8+.
"""
from __future__ import annotations

import fnmatch
import pathlib
import re
import subprocess
from typing import List, Optional, Tuple

import brief_composer as bc
import flow_transition as ft

EM_DASH = "\u2014"
DEFAULT_DIFF_BUDGET_LINES = 800
SCOPE_HEADING = "## Scope (locked)"

_WORKTREES_MARKER = "\\.worktrees\\"
_SHORTSTAT_RE = re.compile(r"(\d+) insertion|(\d+) deletion")
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


# --- pure section: no I/O, no clock ------------------------------------------

def expected_branch(flow: dict, sprint: Optional[dict] = None) -> str:
    """`sprint/<slug>`, the branch a sprint for this flow is contracted to use.

    `sprint` is accepted and ignored: the contract is the flow's, and reading
    the branch back off the sprint would make the check compare a value to
    itself.
    """
    return "sprint/{}".format((flow or {}).get("slug", ""))


def repo_root_for(flow: dict, sprint: Optional[dict] = None,
                  dev_root: Optional[str] = None) -> str:
    """The repository the merge lands in (interpretation decision 1).

    A sprint records `worktree_path` as `<repo>\\.worktrees\\<name>`
    (sprints\\_lib\\schema.md line 15), so the text before the marker IS the
    repo root and needs no convention. Without one, fall back to
    `<dev_root>\\<project>`. Returns "" when neither resolves; the caller
    reports that rather than merging into a guessed repository.
    """
    path = str((sprint or {}).get("worktree_path") or "").replace("/", "\\")
    if path:
        idx = path.find(_WORKTREES_MARKER)
        if idx > 0:
            return path[:idx]
    project = (flow or {}).get("project", "")
    if dev_root and project:
        return str(pathlib.Path(dev_root) / project)
    return ""


def scope_globs(spec_text: Optional[str]) -> Optional[List[str]]:
    """The allowed path globs a spec's `## Scope (locked)` section ends with.

    The convention (spec item 4): the LAST fenced code block inside that
    section, one glob per line. None means the spec declares no such list,
    which is unknown and never trips a check. Never raises.
    """
    if not spec_text:
        return None
    start = spec_text.find(SCOPE_HEADING)
    if start < 0:
        return None
    rest = spec_text[start + len(SCOPE_HEADING):]
    end = rest.find("\n## ")
    section = rest if end < 0 else rest[:end]
    blocks = _FENCE_RE.findall(section)
    if not blocks:
        return None
    globs = [ln.strip() for ln in blocks[-1].splitlines() if ln.strip()]
    return globs or None


def out_of_scope(paths, globs) -> List[str]:
    """Paths matching none of `globs`, compared with forward slashes.

    `globs` of None means no list was declared, so nothing is out of scope.
    """
    if globs is None:
        return []
    patterns = [str(g).replace("\\", "/") for g in globs]
    out = []
    for raw in paths or []:
        p = str(raw).replace("\\", "/").strip()
        if not p:
            continue
        if not any(fnmatch.fnmatch(p, pat) for pat in patterns):
            out.append(p)
    return out


def added_lines(diff_text: str) -> List[str]:
    """The added lines of a unified diff, without the '+' and without the
    '+++' file headers."""
    return [ln[1:] for ln in (diff_text or "").splitlines()
            if ln.startswith("+") and not ln.startswith("+++")]


def diff_line_total(shortstat_text: str) -> int:
    """insertions + deletions from `git diff --shortstat`. An empty diff
    prints nothing at all, which is 0."""
    total = 0
    for ins, dele in _SHORTSTAT_RE.findall(shortstat_text or ""):
        total += int(ins or dele or 0)
    return total


def evaluate(facts: dict) -> dict:
    """The pure results assembler: gathered facts in, the 13-key dict out.

    Every fact is optional and every absence degrades to a False bool or an
    empty evidence field rather than raising, so a host that could not gather
    something still produces a complete, readable dict.

    Facts: status_porcelain, current_branch, expected_branch, sprint,
    diff_text, diff_names, diff_shortstat, scope_globs, diff_budget_lines,
    revert_point.
    """
    facts = facts or {}
    sprint = facts.get("sprint") or {}
    current = str(facts.get("current_branch") or "")
    expected = str(facts.get("expected_branch") or "")
    diff_text = facts.get("diff_text") or ""
    globs = facts.get("scope_globs")
    budget = facts.get("diff_budget_lines")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        budget = DEFAULT_DIFF_BUDGET_LINES

    hits = bc.find_secrets(diff_text)
    outside = out_of_scope(facts.get("diff_names") or [], globs)
    lines = diff_line_total(facts.get("diff_shortstat") or "")

    return {
        "clean_tree": str(facts.get("status_porcelain") or "").strip() == "",
        "right_branch": bool(current) and current == expected,
        "worktree_branch": current,
        "tests_pass": (sprint.get("last_test_result") or {}).get("exit_code") == 0,
        "verify_pass":
            (sprint.get("last_verify_result") or {}).get("outcome") == "pass",
        "no_secrets": not hits,
        "secrets_found": [bc.redact_secrets(h) for h in hits],
        "scope_respected": None if globs is None else not outside,
        "out_of_scope_paths": outside,
        "diff_budget": lines <= budget,
        "diff_lines": lines,
        "no_em_dash": all(EM_DASH not in ln for ln in added_lines(diff_text)),
        "revert_point": str(facts.get("revert_point") or ""),
    }


# --- host section: the only I/O in this module -------------------------------

GIT_TIMEOUT_SECONDS = 60


def git(args: List[str], cwd) -> Tuple[int, str]:
    """Run one git command and return (exit_code, output). Never raises.

    A missing git, an unreadable cwd and a timeout all come back as a non-zero
    code with empty output, so every caller degrades to a failed check instead
    of an exception. This is the single git runner for slice c: foreman_run's
    ship executor calls this same function, which is also why the tests can
    monkeypatch one name and cover both.

    On success, `output` is stdout only, exactly as every existing caller here
    (the branch/status/diff checks) already expects. On a non-zero exit,
    stderr is appended to it: `git push` and `git merge` put nearly all of
    their diagnostic text on stderr, and foreman_run's failure messages quote
    this return value verbatim, so a caller-facing error with an empty tail
    was a review finding (2026-08-17) worth fixing narrowly rather than
    risking a successful rev-parse or status read picking up incidental
    stderr noise.
    """
    try:
        proc = subprocess.run(["git"] + list(args), cwd=str(cwd),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    if proc.returncode == 0:
        return proc.returncode, proc.stdout or ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _one_line(args: List[str], cwd) -> str:
    """git stdout stripped to one line, or "" if the command failed."""
    code, out = git(args, cwd)
    return out.strip() if code == 0 else ""


def read_spec_text(flow: dict) -> Optional[str]:
    """The flow's spec as text, or None when this host cannot see it.

    Goes through flow_transition's own two helpers rather than rebuilding the
    derivation: _resolve_spec_path (its docstring sanctions host use) and
    local_spec_path (returns None when the path is not visible from this
    machine, which is undecidable rather than missing).
    """
    try:
        local = ft.local_spec_path(ft._resolve_spec_path(flow or {}))
        if not local:
            return None
        path = pathlib.Path(local)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError):
        return None


def run_preflight(flow: dict, sprint: Optional[dict], repo_root: str,
                  diff_budget_lines: int = DEFAULT_DIFF_BUDGET_LINES,
                  spec_text: Optional[str] = None) -> dict:
    """Gather the facts with git, then evaluate them. The one public host
    function of this module.

    Where each command runs (interpretation decision 2): the branch and
    cleanliness checks run in the sprint's own worktree, because that is the
    checkout the sprint worked in; the target branch, its pre-merge sha and the
    diff run in `repo_root`, because that is where the merge will land.

    The diff is `<target>...<branch>`, three-dot (interpretation decision 3):
    plain `git diff` in a clean worktree is empty, which would make four of the
    nine checks vacuously pass.

    `diff_budget_lines` is the caller's `driver.get("diff_budget_lines", 800)`.
    `spec_text` lets a caller that already read the spec pass it in; None means
    read it here. Pass "" to skip the read and declare no scope list.
    """
    worktree = str((sprint or {}).get("worktree_path") or repo_root)
    branch = expected_branch(flow, sprint)
    current = _one_line(["rev-parse", "--abbrev-ref", "HEAD"], worktree)
    _code, status = git(["status", "--porcelain"], worktree)
    target = _one_line(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    revert_point = _one_line(["rev-parse", "HEAD"], repo_root)

    diff_text = names = shortstat = ""
    if target and (current or branch):
        rng = "{}...{}".format(target, current or branch)
        _c1, diff_text = git(["diff", rng], repo_root)
        _c2, names = git(["diff", "--name-only", rng], repo_root)
        _c3, shortstat = git(["diff", "--shortstat", rng], repo_root)

    if spec_text is None:
        spec_text = read_spec_text(flow)

    return evaluate({
        "status_porcelain": status,
        "current_branch": current,
        "expected_branch": branch,
        "sprint": sprint,
        "diff_text": diff_text,
        "diff_names": [ln for ln in (names or "").splitlines() if ln.strip()],
        "diff_shortstat": shortstat,
        "scope_globs": scope_globs(spec_text),
        "diff_budget_lines": diff_budget_lines,
        "revert_point": revert_point,
    })
