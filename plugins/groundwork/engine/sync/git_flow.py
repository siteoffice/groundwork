"""siteoffice-git flow: the only code that runs a mutating git command for an
agent. Steps: start, save, check, propose, land, abort. Every step is
step(repo_dir, ..., apply=False): with apply=False it returns the preview only
and changes nothing; with apply=True it acts and writes one receipt line to
_log\\runs\\git-flow.ndjson. Stdlib only, Python 3.8+, no network beyond the
repo's own configured remote (through git_ops pull, push and fetch) and, for
propose and land, the GitHub API of that remote's repository through the gh
CLI. Settings schema: sync\\GIT_FLOW.md. CLI: python sync\\git_flow.py start
<slug> | save | check | propose | land | abort.
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import git_ops  # noqa: E402
import owner_check  # noqa: E402

# Module-level flag so a Windows-only branch never needs os.name patched in a test.
IS_WINDOWS = sys.platform == "win32"
EXIT_CODES = {"pass": 0, "refused": 2, "blocked": 3}
RECEIPT_PARTS = ("_log", "runs", "git-flow.ndjson")
DEFAULT_GIT_SETTINGS = {
    "profile": "code",
    "main": "main",
    "protected": ["main", "master"],
    "checks": [],
    "checkTimeoutSeconds": 600,
    "denylist": ".git-denylist",
    "pr": {"approvals": 0},
}
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _relative_path_ok(value: str) -> bool:
    if not value.strip() or re.match(r"^[A-Za-z]:", value):
        return False
    path = pathlib.PurePosixPath(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _read_manifest(node_dir: pathlib.Path) -> dict:
    """The node's own siteoffice.json as a dict; {} when missing, unreadable,
    not JSON, or not an object."""
    try:
        data = json.loads((node_dir / "siteoffice.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _git_block(raw: dict, notes: List[str]) -> dict:
    settings = raw.get("settings")
    if not isinstance(settings, dict) or "git" not in settings:
        return {}
    block = settings["git"]
    if not isinstance(block, dict):
        notes.append("settings.git is not an object; using the defaults")
        return {}
    return block


def _validate_git_settings(raw: dict, notes: List[str]) -> dict:
    """The defaults overlaid with every valid key of raw. An invalid key keeps
    its default and adds one amber note; nothing here raises."""
    settings = copy.deepcopy(DEFAULT_GIT_SETTINGS)

    def invalid(key: str, want: str) -> None:
        notes.append(f"settings.git.{key} must be {want}; using the default")

    if "profile" in raw:
        if raw["profile"] == "docs":
            notes.append('settings.git.profile "docs" is not honoured yet; using "code"')
        elif raw["profile"] != "code":
            invalid("profile", '"code"')
    if "main" in raw:
        if isinstance(raw["main"], str) and _BRANCH_RE.fullmatch(raw["main"]):
            settings["main"] = raw["main"]
        else:
            invalid("main", "a branch name")
    for key in ("protected", "checks"):
        if key in raw:
            value = raw[key]
            if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
                settings[key] = list(value)
            else:
                invalid(key, "a list of non-empty strings")
    if "checkTimeoutSeconds" in raw:
        if _is_int(raw["checkTimeoutSeconds"]) and raw["checkTimeoutSeconds"] > 0:
            settings["checkTimeoutSeconds"] = raw["checkTimeoutSeconds"]
        else:
            invalid("checkTimeoutSeconds", "a positive whole number")
    if "denylist" in raw:
        if isinstance(raw["denylist"], str) and _relative_path_ok(raw["denylist"]):
            settings["denylist"] = raw["denylist"]
        else:
            invalid("denylist", "a path relative to the repo root")
    if "pr" in raw:
        pr = raw["pr"]
        if not isinstance(pr, dict):
            invalid("pr", "an object")
        elif "approvals" in pr:
            if _is_int(pr["approvals"]) and pr["approvals"] >= 0:
                settings["pr"]["approvals"] = pr["approvals"]
            else:
                invalid("pr.approvals", "a whole number of 0 or more")
    return settings


def _fold_path(path) -> tuple:
    """The real path of path as comparable parts, case-folded where the file
    system ignores case (IS_WINDOWS)."""
    real = os.path.realpath(str(path))
    return pathlib.PurePath(real.casefold() if IS_WINDOWS else real).parts


def _manifest_inside(node_dir: pathlib.Path, repo_dir: pathlib.Path) -> bool:
    """True when node_dir's siteoffice.json really lives in repo_dir or below it.
    Both the folder and the manifest file are resolved, so a symlinked manifest
    that points out of the repo does not count."""
    repo = _fold_path(repo_dir)
    for candidate in (node_dir, node_dir / "siteoffice.json"):
        parts = _fold_path(candidate)
        if parts[:len(repo)] != repo:
            return False
    return True


def _without_outside_checks(block: dict, inside: bool, notes: List[str]) -> dict:
    """block, minus `checks` when its manifest is outside the repo. `checks` are
    shell commands that `check --apply` runs, so only a manifest inside the repo
    may supply them. One amber note when a non-empty `checks` was dropped; the
    commands themselves are never printed."""
    if inside or "checks" not in block:
        return block
    if block["checks"]:
        notes.append("inherited settings.git.checks were ignored: "
                     "they come from a manifest outside the repo, and only manifests inside it may set checks")
    return {key: value for key, value in block.items() if key != "checks"}


def load_git_settings(node_dir, repo_dir=None) -> Tuple[dict, List[str]]:
    """(settings, notes): the settings.git block of node_dir's siteoffice.json
    validated against DEFAULT_GIT_SETTINGS, plus one amber note per invalid
    value. repo_dir (default node_dir) is the repo the settings are for: `checks`
    from a manifest outside it, such as one inherited from a folder above the
    repo, are dropped with an amber note; every other inherited key still applies."""
    node_dir = pathlib.Path(node_dir).resolve()
    repo_dir = pathlib.Path(repo_dir).resolve() if repo_dir is not None else node_dir
    notes: List[str] = []
    own_block = _git_block(_read_manifest(node_dir), notes)
    own_block = _without_outside_checks(own_block, _manifest_inside(node_dir, repo_dir), notes)
    try:
        root_dir = owner_check.find_root_dir(node_dir, pathlib.Path(node_dir.anchor))
    except (AttributeError, TypeError, RecursionError):
        root_dir = node_dir  # a manifest that is not a JSON object: own keys only
    root_block = _git_block(_read_manifest(root_dir), notes) if root_dir != node_dir else {}
    root_block = _without_outside_checks(root_block, _manifest_inside(root_dir, repo_dir), notes)
    block = dict(root_block)
    block.update(own_block)
    if isinstance(root_block.get("pr"), dict) and isinstance(own_block.get("pr"), dict):
        block["pr"] = {**root_block["pr"], **own_block["pr"]}
    return _validate_git_settings(block, notes), notes


# --- secrets scan -----------------------------------------------------------

_CONTENT_RULES = [
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("pem-private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    # Only sk- keeps a leading word boundary (task-list-... and risk-assessment-... hold
    # "sk-" plus a long run); gh[pousr]_ and xox[baprs]- are long enough without one, so
    # TOKEN_ghp_... and KEY_xoxb-... are hit.
    ("sk-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    # No leading [a-z0-9_.-]*, and the name run after the keyword stops at the
    # next keyword: both kept a long line quadratic. Only the last keyword of a
    # run can match, which flags the same lines as the plain form. After the name
    # comes an optional quote plus "]" (a subscript target such as
    # config["password"] = ...). Between the name and the value: an optional
    # annotation (an optional "?", a colon, then a bounded run of type characters
    # up to the "=": spaces, pipes, commas, quotes, brackets and parentheses are allowed), the
    # operator (= : or :=) and an optional string prefix (r b u f, rb br fr rf,
    # any case). The annotation run is one non-nested character class of at most
    # 200 characters that cannot contain "=" or ":", so it ends at the first one
    # and never re-scans. The value is four alternatives (triple double, triple
    # single, double, single) so it may hold the other quote character or an
    # escaped quote; a triple quote needs no closer on the line (a multi-line
    # value opens there). The parts inside each alternative are disjoint, so it
    # stays linear.
    ("secret-assignment", re.compile(
        r"(?i)(?:key|secret|token|password)"
        r"(?:(?!key|secret|token|password)[a-z0-9_.-])*(?:[\"']\]|[\"']?)\s*"
        r"(?:\??:[\w .\[\](),|\"'<>?*&\t-]{0,200}(?::=|=)|:=|[:=])\s*(?:rb|br|fr|rf|[rbfu])?"
        r"(?:\"\"\"(?:[^\"\\\r\n]|\\.){12,}|'''(?:[^'\\\r\n]|\\.){12,}"
        r"|\"(?:[^\"\\\r\n]|\\.){12,}\"|'(?:[^'\\\r\n]|\\.){12,}')")),
]


def redact(text: str) -> str:
    """text with every secret-shaped match replaced by [redacted]."""
    for _rule, pattern in _CONTENT_RULES:
        text = pattern.sub("[redacted]", text)
    return text


def _with_nul_stripped(lines: List[str]) -> List[str]:
    """lines plus, for every line holding a NUL, a copy without its NULs: UTF-16
    (with or without a BOM) decodes as UTF-8 with a NUL between the characters
    and becomes plain ASCII once they are gone."""
    return lines + [line.replace("\x00", "") for line in lines if "\x00" in line]


def _added_lines(repo_dir: pathlib.Path, path: str) -> Optional[List[str]]:
    """The lines this change adds to path: hunk additions of `git diff HEAD -U0`
    for a tracked path, every line of an untracked file. None when the scan
    itself failed (a failed scan must never read as clean)."""
    listed = git_ops.run_git(repo_dir, ["--literal-pathspecs", "ls-files", "--", path])
    if listed.returncode != 0 or listed.stdout is None:
        return None
    if listed.stdout.strip() != "":
        # Own subprocess, bytes decoded leniently: git_ops.run_git decodes
        # strictly as UTF-8, so one Latin-1 line would lose the whole diff.
        # Colour is forced off: a user config of color.ui=always would wrap the
        # "+" and "@@" markers in ANSI codes and hide every addition from the rules.
        # diff.external, textconv and -diff/binary attributes would likewise hide the
        # added lines, so all three are switched off; literal pathspecs stop a glob-named
        # untracked file from matching a tracked one.
        try:
            diff = subprocess.run(
                ["git", "--literal-pathspecs", "-c", "color.ui=never", "diff", "--no-color",
                 "--no-ext-diff", "--no-textconv", "--text", "HEAD", "-U0", "--", path],
                cwd=str(repo_dir),
                capture_output=True, timeout=git_ops.GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if diff.returncode != 0 or not isinstance(diff.stdout, bytes):
            return None
        # Split on the newline character only: str.splitlines() also breaks on CR,
        # VT, FF, FS, NEL and the Unicode line/paragraph separators, and the
        # fragment after one no longer starts with "+", so it would be dropped.
        added, in_hunk = [], False
        for line in diff.stdout.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line.startswith("+"):
                added.append(line[1:])
        return _with_nul_stripped(added)
    full = pathlib.Path(repo_dir) / path
    link_lines: List[str] = []
    if os.path.islink(full):
        # An untracked symlink commits its target path as the blob, so the link text
        # is scanned like an added line, besides the target file's content below.
        try:
            link_lines = [os.readlink(full)]
        except OSError:
            return None
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Gone from the working tree and absent from the index: a staged deletion
        # (the old side of a rename) adds no lines. Anything else that cannot be
        # read, such as a dangling symlink, stays a failed scan.
        return [] if not os.path.lexists(full) else None
    return _with_nul_stripped(text.split("\n") + link_lines)


def content_hits(repo_dir: pathlib.Path, path: str) -> List[str]:
    """Rule ids whose pattern matches an added line of path; ["scan-failed"]
    when the added lines could not be read. Never returns the matched text."""
    lines = _added_lines(repo_dir, path)
    if lines is None:
        return ["scan-failed"]
    return _rules_matching(lines)


def _rules_matching(lines: List[str]) -> List[str]:
    """Rule ids whose pattern matches at least one of lines."""
    return [
        rule for rule, pattern in _CONTENT_RULES
        if any(pattern.search(line) for line in lines)
    ]


def text_hits(text: str) -> List[str]:
    """Rule ids whose pattern matches text (a commit subject or scope): the same
    rules, and the same NUL-stripped second look, as the added-lines scan. Never
    returns the matched text."""
    return _rules_matching(_with_nul_stripped((text or "").split("\n")))


def message_hits(message: str) -> List[Tuple[str, str]]:
    """(field, rule) for every secret rule the built commit message trips, field
    being "scope", "subject" or "body" (the diff stat, which lists file names).
    Never returns the matched text."""
    head, _sep, body = message.partition("\n\n")
    scope, subject = _header_fields(head)
    return [
        (field, rule)
        for field, text in (("subject", subject), ("scope", scope), ("body", body))
        for rule in text_hits(text)
    ]


def name_hits(paths) -> List[str]:
    """Rule ids the file names in paths trip: every path, whole, goes through the
    same content rules. Each rule appears once, in rule order. Never returns a
    name or any part of one."""
    return [
        rule for rule, pattern in _CONTENT_RULES
        if any(pattern.search(line) for path in paths for line in _with_nul_stripped([path]))
    ]


PATH_RULES = (".env", "*.pem", "id_rsa*")


class DenylistError(ValueError):
    """The denylist file exists but cannot be read; the save is refused."""


def load_denylist(repo_dir, rel: str) -> List[str]:
    """Globs from the denylist file (one per line, # comments, blank lines
    skipped). Only a missing file means an empty list; any other read or
    decode failure raises DenylistError naming the path, never the content."""
    path = pathlib.Path(repo_dir) / rel
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise DenylistError(f"denylist unreadable: {rel} ({type(exc).__name__})") from None
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def denylist_paths(settings: dict) -> List[str]:
    """The denylist files that apply: the default file at the repo root always,
    plus the configured path when it differs. A manifest (its own or an inherited
    one) can add a denylist file but never remove the default."""
    default = DEFAULT_GIT_SETTINGS["denylist"]
    configured = settings.get("denylist") or default
    same = posixpath.normpath(configured.replace("\\", "/")).casefold() == default.casefold()
    return [default] if same else [default, configured]


def load_denylists(repo_dir, rels) -> List[str]:
    """The union of the globs of every file in rels; a missing file adds nothing
    and an unreadable one raises DenylistError."""
    globs: List[str] = []
    for rel in rels:
        globs += load_denylist(repo_dir, rel)
    return globs


def _strip_dot_slash(text: str) -> str:
    return text[2:] if text.startswith("./") else text


def denylist_match(path: str, globs) -> Optional[str]:
    """The first glob matching path (a glob without a slash also matches the
    bare file name, anywhere in the tree); matching is case-insensitive. A
    leading ./ is ignored on both sides and dir/ means dir/*."""
    rel = _strip_dot_slash(path.replace("\\", "/").lower())
    base = rel.rsplit("/", 1)[-1]
    for glob in globs:
        pattern = _strip_dot_slash(glob.replace("\\", "/").lower())
        if pattern.endswith("/"):
            pattern += "*"
        pattern = pattern.lstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(rel, pattern) or ("/" not in pattern and fnmatch.fnmatchcase(base, pattern)):
            return glob
    return None


def path_rule(path: str, denylist=()) -> Optional[str]:
    """The rule that refuses path by name alone, or None."""
    base = path.replace("\\", "/").lower().rsplit("/", 1)[-1]
    for rule in PATH_RULES:
        if fnmatch.fnmatchcase(base, rule):
            return rule
    glob = denylist_match(path, denylist)
    return f"denylist:{glob}" if glob else None


def scan_added_lines(repo_dir, paths, denylist=()) -> List[Tuple[str, str]]:
    """(path, rule) for every hit: a path rule (.env, *.pem, id_rsa*, denylist
    globs) or a content rule on the lines the change adds. A refusal built
    from this names path and rule, never the matched text."""
    repo_dir = pathlib.Path(repo_dir)
    hits: List[Tuple[str, str]] = []
    for path in paths:
        rule = path_rule(path, denylist)
        if rule:
            hits.append((path, rule))
            continue
        for content_rule in content_hits(repo_dir, path):
            hits.append((path, content_rule))
    return hits


# --- protected-branch guard and branch names --------------------------------

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


class Refusal(Exception):
    """A refusal sentence a step turns into outcome "refused"."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


def effective_protected(settings: dict) -> set:
    """The branches save refuses to commit on: settings.protected, settings.main
    and the literal defaults main and master. Nothing a manifest says, its own or
    inherited from a parent outside the repo, can remove main or master."""
    return set(settings["protected"]) | {settings["main"]} | set(DEFAULT_GIT_SETTINGS["protected"])


def assert_not_protected(branch: str, protected) -> None:
    """Refuse when branch is one of protected. Where the file system ignores case
    (IS_WINDOWS) the comparison ignores it on both sides: a loose refs/heads/Main
    can shadow the packed refs/heads/main."""
    def fold(name: str) -> str:
        return name.casefold() if IS_WINDOWS else name

    if fold(branch) in {fold(name) for name in protected}:
        raise Refusal(f"`{branch}` is protected; run `start <slug>` to work on a branch of your own.")


def valid_slug(slug) -> bool:
    return isinstance(slug, str) and SLUG_RE.fullmatch(slug) is not None


def owner_slug(owner: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", owner.lower()) or "unknown"


def branch_name(owner: str, slug: str) -> str:
    return f"work/{owner_slug(owner)}/{slug}"


def this_owner(repo_dir) -> str:
    """The owner id of this clone: sync\\owner.local.json (the file the sync
    helper writes) when present, else `git config user.name`, else "unknown"."""
    local = pathlib.Path(repo_dir) / "sync" / "owner.local.json"
    try:
        owner = json.loads(local.read_text(encoding="utf-8")).get("owner")
        if isinstance(owner, str) and owner.strip():
            return owner
    except (OSError, ValueError, AttributeError):
        pass
    result = git_ops.run_git(pathlib.Path(repo_dir), ["config", "user.name"])
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else "unknown"


# --- results, receipts and the step wrapper ---------------------------------


def make_result(step: str, outcome: str, preview: str, refusal: str = "", amber: str = "",
                **extra) -> dict:
    """The result dict every step returns. outcome is pass, refused or blocked;
    extra keys (branch, checks, left_out, sha, stderr) ride along."""
    result = {
        "step": step, "outcome": outcome, "preview": preview, "refusal": refusal,
        "amber": amber, "receipt": None,
    }
    result.update(extra)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def head_sha(repo_dir) -> str:
    result = git_ops.run_git(pathlib.Path(repo_dir), ["rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else ""


def is_git_repo(repo_dir) -> bool:
    result = git_ops.run_git(pathlib.Path(repo_dir), ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def write_receipt(repo_dir, receipt: dict) -> bool:
    """Append one ndjson line to _log\\runs\\git-flow.ndjson (created on first
    write). Best effort, like git_ops._write_receipt: False on failure, never
    an exception."""
    try:
        path = pathlib.Path(repo_dir).joinpath(*RECEIPT_PARTS)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt) + "\n")
        return True
    except OSError:
        return False


def _tail(text: str) -> str:
    """The last 20 lines of text, capped to its last 2000 characters."""
    return "\n".join(redact(text or "").splitlines()[-20:])[-2000:]


def _redact_checks(checks) -> list:
    """A copy of the check records with each command string redacted."""
    return [
        {**check, "command": redact(check["command"])}
        if isinstance(check, dict) and isinstance(check.get("command"), str) else check
        for check in checks
    ]


def _receipt_pr(pr: dict) -> dict:
    """The pr object of a result and of its receipt: number (a whole number or
    None), url and merge_commit, every string redacted."""
    number = pr.get("number")
    return {
        "number": number if _is_int(number) else None,
        "url": redact(str(pr.get("url") or "")),
        "merge_commit": redact(str(pr.get("merge_commit") or "")),
    }


class RunContext:
    """One per CLI invocation: run_id is shared by every step it runs."""

    def __init__(self, harness: Optional[str] = None, model: Optional[str] = None,
                 run_id: Optional[str] = None):
        self.run_id = run_id or uuid.uuid4().hex
        self.harness = harness or os.environ.get("SITEOFFICE_HARNESS") or "unknown"
        self.model = model or os.environ.get("SITEOFFICE_MODEL") or "unknown"


def _current_branch_state(repo_dir) -> Tuple[str, str]:
    """(kind, name) for HEAD, read from `git symbolic-ref -q HEAD` so a tag with
    the same name as the branch cannot change the answer (`--short` and
    `rev-parse --abbrev-ref` both print `heads/main` then). kind is "branch"
    (name is the branch, also on an unborn branch), "detached" (quiet return
    code 1), or "unknown" (any other failure, a timeout or an odd answer). Every
    guard must treat "unknown" as a refusal."""
    result = git_ops.run_git(pathlib.Path(repo_dir), ["symbolic-ref", "-q", "HEAD"])
    if result.returncode == 1:
        return ("detached", "")
    ref = result.stdout.strip()
    prefix = "refs/heads/"
    if result.returncode == 0 and ref.startswith(prefix) and len(ref) > len(prefix):
        return ("branch", ref[len(prefix):])
    return ("unknown", "")


def _receipt_branch(repo_dir) -> str:
    """The receipt's branch field: the branch name, "HEAD" when detached, "" when unknown."""
    kind, name = _current_branch_state(repo_dir)
    return "HEAD" if kind == "detached" else name


def _run_step(step: str, repo_dir, apply: bool, ctx: Optional[RunContext], inner) -> dict:
    """Shared wrapper: loads settings, refuses a non-repo, calls
    inner(settings) -> result, folds settings notes into amber and the preview,
    and (apply only) writes one receipt."""
    repo_dir = pathlib.Path(repo_dir)
    nested = _nested_refusal(step, repo_dir)
    if nested is not None:
        return nested
    ctx = ctx or RunContext()
    settings, notes = load_git_settings(repo_dir)
    started = time.monotonic()
    sha_before = head_sha(repo_dir)
    if is_git_repo(repo_dir):
        result = inner(settings)
    else:
        result = make_result(step, "refused", "", f"`{repo_dir}` is not a git repository.")
    result["refusal"] = redact(result.get("refusal") or "")
    amber = "; ".join(part for part in [result["amber"], *notes] if part)
    result["amber"] = amber
    if amber:
        result["preview"] = (result["preview"] + "\n" if result["preview"] else "") + f"amber: {amber}"
    result["receipt_written"] = False
    if result.get("pr") is not None:
        result["pr"] = _receipt_pr(result["pr"])
    if apply:
        receipt = {
            "receipt_id": uuid.uuid4().hex,
            "run_id": ctx.run_id,
            "ts": _now_iso(),
            "profile": settings["profile"],
            "step": step,
            "branch": _receipt_branch(repo_dir),
            "harness": ctx.harness,
            "model": ctx.model,
            "sha_before": sha_before,
            "sha_after": head_sha(repo_dir),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "exit_code": EXIT_CODES[result["outcome"]],
            "outcome": result["outcome"],
            "refusal": result["refusal"],
            "checks": _redact_checks(result.get("checks") or []),
            "stderr_tail": _tail(result.get("stderr", "")),
        }
        if result.get("pr") is not None:
            receipt["pr"] = result["pr"]
        result["receipt"] = receipt
        result["receipt_written"] = write_receipt(repo_dir, receipt)
    result.pop("stderr", None)
    return result


# --- step: start ------------------------------------------------------------


def _relevant_paths(repo_dir) -> List[str]:
    """Changed paths, ignoring everything under _log/ (git_flow's own receipts
    and git_ops' sync receipts are never work)."""
    changed = git_ops.list_changed_paths(pathlib.Path(repo_dir), split_renames=True)
    return [p for p in changed if not p.startswith("_log/")]


_SYNC_RECEIPT = re.compile(r"^_log/sync-receipt-[0-9TZ-]+\.json$")


def _pending_receipts(repo_dir) -> List[str]:
    """Sync receipts (_log/sync-receipt-<timestamp>.json) that earlier commits left
    in the working tree. git_ops writes a commit's own receipt after the commit, so
    every one found while a save starts belongs to an earlier commit."""
    repo_dir = pathlib.Path(repo_dir)
    changed = git_ops.list_changed_paths(repo_dir, split_renames=True)
    return [p for p in changed if _SYNC_RECEIPT.match(p) and (repo_dir / p).is_file()]


POLICY_BASENAMES = ("siteoffice.json", ".gitignore")


def policy_files(paths: List[str], denylist_rels) -> List[str]:
    """The paths that are policy files: any siteoffice.json or .gitignore at any depth
    and every denylist file (a path, or a list of paths: the default and the
    configured one). Names are compared without regard to case (a case-insensitive
    file system reads SiteOffice.JSON as the manifest)."""
    if isinstance(denylist_rels, str):
        denylist_rels = [denylist_rels]
    denylists = {posixpath.normpath(rel.replace("\\", "/")).casefold() for rel in denylist_rels}
    found = []
    for path in paths:
        rel = path.replace("\\", "/")
        if rel.rsplit("/", 1)[-1].casefold() in POLICY_BASENAMES or posixpath.normpath(rel).casefold() in denylists:
            found.append(path)
    return found


def _worktree_holding(repo_dir, branch: str) -> Optional[str]:
    """The path of another worktree that has `branch` checked out, or None. Git allows a
    branch in one worktree only, so such a branch cannot be checked out here."""
    listed = git_ops.run_git(repo_dir, ["worktree", "list", "--porcelain"])
    if listed.returncode != 0:
        return None
    here = os.path.normcase(os.path.realpath(str(repo_dir)))
    path = None
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and path is not None:
            if os.path.normcase(os.path.realpath(path)) != here:
                return path
    return None


def start(repo_dir, slug, apply: bool = False, ctx: Optional[RunContext] = None) -> dict:
    """Create work/<owner>/<slug> from an up-to-date main. When another worktree holds main
    (the deploy checkout), fetch and branch from origin/<main> instead of checking main out."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        try:
            if not valid_slug(slug):
                raise Refusal(
                    f"`{slug}` is not a valid slug; use lower-case letters, digits and hyphens, "
                    "starting with a letter or digit, at most 41 characters."
                )
            owner = this_owner(repo_dir)
            branch = branch_name(owner, slug)
            main = settings["main"]
            # Refusals come before the preview return and before any checkout,
            # so a preview reports them and uncommitted work never reaches main.
            dirty = _relevant_paths(repo_dir)
            if dirty:
                raise Refusal(
                    f"The working tree has {len(dirty)} uncommitted change(s), first `{dirty[0]}`; "
                    "save or discard them before `start`."
                )
            exists = git_ops.run_git(repo_dir, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
            if exists.returncode == 0:
                raise Refusal(f"Branch `{branch}` already exists; pick another slug.")
            held = _worktree_holding(repo_dir, main)
            if held:
                preview = (f"Would fetch origin and create `{branch}` from `origin/{main}`; `{main}` is "
                           f"checked out in another worktree ({held}), so it is not checked out here.")
                if not apply:
                    return make_result("start", "pass", preview, branch=branch)
                fetched = git_ops.run_git(repo_dir, ["fetch", "origin"],
                                          timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
                if fetched.returncode != 0:
                    return make_result(
                        "start", "refused", preview,
                        f"Fetching origin failed: {_first_line(fetched.stderr)}. No branch was made.",
                        stderr=fetched.stderr)
                created = git_ops.run_git(repo_dir, ["checkout", "-b", branch, f"origin/{main}", "--no-track"])
                if created.returncode != 0:
                    return make_result("start", "blocked", preview,
                                       f"git could not create `{branch}` from `origin/{main}`.",
                                       branch=branch, stderr=created.stderr)
                return make_result("start", "pass", f"Fetched origin and created `{branch}` from `origin/{main}`.",
                                   branch=branch)
            preview = f"Would check out `{main}`, pull it from origin and create `{branch}`."
            if not apply:
                return make_result("start", "pass", preview, branch=branch)
            checkout = git_ops.run_git(repo_dir, ["checkout", main])
            if checkout.returncode != 0:
                return make_result(
                    "start", "refused", preview,
                    f"Could not check out `{main}`: {_first_line(checkout.stderr)}", stderr=checkout.stderr)
            pulled = git_ops.pull(repo_dir, "git-flow", owner)
            if pulled.get("state") == "conflict":
                names = ", ".join(pulled.get("conflicts", []))
                return make_result(
                    "start", "refused", preview,
                    f"Pulling `{main}` left conflict copies for {names}; resolve the .incoming files "
                    "first. No branch was made.")
            if pulled.get("state") == "error" or not pulled.get("opOk"):
                why = pulled.get("opMessage") or f"pull state {pulled.get('state')}"
                return make_result(
                    "start", "refused", preview,
                    f"Pulling `{main}` failed: {why}. No branch was made.")
            created = git_ops.run_git(repo_dir, ["checkout", "-b", branch])
            if created.returncode != 0:
                return make_result("start", "blocked", preview, f"git could not create `{branch}`.",
                                   branch=branch, stderr=created.stderr)
            return make_result("start", "pass", f"Checked out `{main}`, pulled it and created `{branch}`.",
                               branch=branch)
        except Refusal as refusal:
            return make_result("start", "refused", "", refusal.sentence)

    return _run_step("start", repo_dir, apply, ctx, inner)


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


# --- step: save -------------------------------------------------------------


def save(repo_dir, commit_type: Optional[str] = None, scope: Optional[str] = None,
         subject: Optional[str] = None, apply: bool = False,
         ctx: Optional[RunContext] = None) -> dict:
    """Commit the changed paths that pass the owner check, the denylist and the
    secrets scan, as a Conventional Commit, on a branch that is not protected."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        kind, branch = _current_branch_state(repo_dir)
        if kind == "detached":
            return make_result(
                "save", "refused", "",
                "HEAD is detached, not on a branch; run `start <slug>` to work on a branch of your own.")
        if kind != "branch" or not branch:
            return make_result(
                "save", "refused", "", "Cannot tell which branch you are on; no changes were made.")
        try:
            # main and master are always protected: a manifest that lists no
            # protected branch (its own, inherited, or edited in this save) can
            # never unguard them.
            assert_not_protected(branch, effective_protected(settings))
        except Refusal as refusal:
            return make_result("save", "refused", "", refusal.sentence, branch=branch)
        owner = this_owner(repo_dir)
        paths = _relevant_paths(repo_dir)
        left_out: List[dict] = []
        if not paths:
            return make_result(
                "save", "refused", "", "Nothing to save: the working tree has no changes.", branch=branch)
        # Policy files (manifests, the denylist, .gitignore) steer every later check,
        # and the checks read them from the working tree being saved. So a change to
        # one refuses the whole save, before any owner or denylist filtering.
        denylist_rels = denylist_paths(settings)
        policy = policy_files(paths, denylist_rels)
        if policy:
            named = ", ".join(f"`{path}`" for path in policy)
            return make_result(
                "save", "refused", "",
                f"Refusing to save: the change set includes the policy file(s) {named}. "
                "A policy change needs a deliberate commit by a human, not an agent save; "
                "nothing was staged.", branch=branch)
        # An unreadable denylist refuses the whole save (fail closed), before any
        # path is filtered and before anything is staged; only a missing file is empty.
        try:
            denylist = load_denylists(repo_dir, denylist_rels)
        except DenylistError as exc:
            return make_result(
                "save", "refused", "", f"Refusing to save: {exc}; nothing was staged.", branch=branch)
        # A path owned by someone else is left out before the message is built
        # and before git_ops.commit sees it: it never reaches the commit body
        # and is never staged.
        kept = []
        for path in paths:
            claimed = owner_check.owner_for_path(repo_dir, path.replace("\\", "/"))
            if claimed and claimed != owner:
                left_out.append({
                    "path": path, "reason": owner_check.refusal_message(path, claimed, owner)})
            else:
                kept.append(path)
        paths = kept
        if not paths:
            return _all_left_out(branch, left_out)
        # Same for a path matching the denylist (owner reason wins: an owner-failing
        # path is already out of `paths`, so each path is listed once).
        kept = []
        for path in paths:
            glob = denylist_match(path, denylist)
            if glob:
                left_out.append({"path": path, "reason": f"`{path}` matches the denylist entry `{glob}`."})
            else:
                kept.append(path)
        paths = kept
        if not paths:
            return _all_left_out(branch, left_out)
        # Sync receipts left by earlier commits ride along with this real work, never alone:
        # the guards above ran on the work paths only. This commit's own receipt is written
        # after it and waits for the next save. A denylisted receipt is skipped quietly.
        receipts = [p for p in _pending_receipts(repo_dir) if not denylist_match(p, denylist)]
        paths = paths + receipts
        # Every remaining file name (a rename shows as its old and its new path) goes
        # through the secret rules before anything else reads the paths: the commit
        # body lists only a few names, and a name is text that reaches git log, the
        # preview and the receipt. A hit names the field and the rule, never the name.
        hits_in_names = name_hits(paths)
        if hits_in_names:
            found = "; ".join(f"a file name (rule: {rule})" for rule in hits_in_names)
            return make_result(
                "save", "refused", "",
                f"Possible secret in {found}. Nothing was staged. "
                "A file name looks like a secret and must be renamed before it can be saved.",
                branch=branch)
        # The secrets scan sees only the paths that remain, before the message is
        # built and before anything is staged. Any hit refuses the WHOLE save; the
        # sentence names path and rule, never the matched text. A scan that could
        # not run (rule "scan-failed") refuses too: fail closed.
        hits = scan_added_lines(repo_dir, paths, denylist)
        if hits:
            failed = [path for path, rule in hits if rule == "scan-failed"]
            found = [(path, rule) for path, rule in hits if rule != "scan-failed"]
            parts = []
            if found:
                named = "; ".join(f"`{path}` (rule: {rule})" for path, rule in found)
                parts.append(f"Possible secret in {named}.")
            if failed:
                named = ", ".join(f"`{path}`" for path in failed)
                parts.append(f"The secrets scan could not run on {named} (rule: scan-failed).")
            sentence = " ".join(parts) + " Nothing was staged."
            if found:
                sentence += " Remove it or add the path to the denylist."
            return make_result("save", "refused", "", sentence, branch=branch, left_out=left_out)
        # build_message wants POSIX repo-relative paths; git_ops.commit gets the
        # list_changed_paths strings untouched (exact-string match on only_paths).
        try:
            message, error = build_message(
                repo_dir, [p.replace("\\", "/") for p in paths if p not in receipts],
                commit_type, scope, subject)
        except OSError as exc:
            # For example WinError 206 (command line too long) on a very large change set.
            return make_result(
                "save", "blocked", "",
                f"git could not be run to build the commit message ({type(exc).__name__}: {exc}); "
                "nothing was staged.", branch=branch, left_out=left_out)
        if error:
            return make_result("save", "refused", "", error, branch=branch)
        # The message is scanned by the same rules as the files, before anything is
        # staged: a refusal names the field and the rule, never the matched text.
        hits_in_message = message_hits(message)
        if hits_in_message:
            found = [f"the commit {field} (rule: {rule})" for field, rule in hits_in_message]
            sentence = f"Possible secret in {'; '.join(found)}. Nothing was staged."
            if any(field == "body" for field, _rule in hits_in_message):
                # The body is the diff stat: a hit there is a file name. It is not
                # named here (or anywhere): the file must be renamed.
                sentence += " A file name looks like a secret and must be renamed before it can be saved."
            else:
                sentence += " Reword it and save again."
            return make_result("save", "refused", "", sentence, branch=branch)
        if "\x00" in message:
            # Caught here so nothing is staged: git_ops.commit runs `git add`
            # before the commit call that would raise ValueError.
            return make_result(
                "save", "refused", "", "Commit subject must not contain a NUL character.", branch=branch)
        preview = _save_preview(branch, paths, left_out, message)
        if not apply:
            return make_result("save", "pass", preview, branch=branch, left_out=left_out)
        before = head_sha(repo_dir)
        try:
            status = git_ops.commit(
                repo_dir, message, owner, owner_check.owner_for_path, "git-flow", owner, only_paths=paths)
        except (ValueError, OSError) as exc:
            note = _unstage(repo_dir, paths)
            if isinstance(exc, ValueError):
                return make_result("save", "refused", preview,
                                   f"Commit message is not acceptable to git: {exc}{note}",
                                   branch=branch, left_out=left_out)
            # An OSError, for example WinError 206 (command line too long), can strike
            # after `git add`: the paths were just unstaged, nothing was committed.
            if head_sha(repo_dir) != before:
                sentence = (f"git reported {type(exc).__name__} after the commit was made; "
                            f"check `git log`.{note}")
            else:
                sentence = (f"git could not run the commit ({type(exc).__name__}: {exc}); "
                            f"nothing was committed.{note}")
            return make_result("save", "blocked", preview, sentence, branch=branch, left_out=left_out)
        if status.get("state") == "refused" and status.get("committed"):
            # HEAD moved: git_ops.commit landed the allowed paths and refused
            # others the pre-filter did not catch. Report the commit, name them.
            sha = git_ops.run_git(repo_dir, ["rev-parse", "--short", "HEAD"]).stdout.strip()
            named = "; ".join(item["message"] for item in status.get("refused", []))
            return make_result("save", "pass", preview + f"\nCommitted {sha}.",
                               amber=f"committed, but left out: {named}",
                               branch=branch, left_out=left_out, sha=sha)
        if status.get("state") in ("error", "refused") or not status.get("committed"):
            reason = status.get("message") or "git commit failed"
            if status.get("state") == "error":
                # git_ops.commit ran `git add` and then failed (a hook, no identity):
                # nothing landed, so undo the staging, only for the paths we passed.
                reason += _unstage(repo_dir, paths)
            return make_result("save", "blocked", preview, reason, branch=branch,
                               left_out=left_out, stderr=reason)
        return make_result("save", "pass", preview + f"\nCommitted {status.get('sha', '')}.",
                           branch=branch, left_out=left_out, sha=status.get("sha", ""))

    return _run_step("save", repo_dir, apply, ctx, inner)


def _all_left_out(branch: str, left_out: List[dict]) -> dict:
    sentence = "Nothing to save: every changed path was left out. " + " ".join(
        item["reason"] for item in left_out)
    return make_result("save", "refused", "", sentence, branch=branch, left_out=left_out)


# --- Conventional Commit message --------------------------------------------

CC_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
_SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*")


def infer_type(paths: List[str]) -> str:
    """docs when every path is markdown, test when every path sits under a
    tests folder, else chore."""
    if paths and all(p.lower().endswith(".md") for p in paths):
        return "docs"
    if paths and all("tests" in p.split("/")[:-1] for p in paths):
        return "test"
    return "chore"


def infer_scope(paths: List[str]) -> Optional[str]:
    """The first path segment every path shares (lower-cased), or None when
    the paths share none or one of them is a root file."""
    if not paths or not all("/" in p for p in paths):
        return None
    firsts = {p.split("/")[0].lower() for p in paths}
    return firsts.pop() if len(firsts) == 1 else None


def validate_message_fields(commit_type, scope, subject) -> Optional[str]:
    """None when type(scope): subject is acceptable, else one sentence naming
    the failing field. Imperative mood is a convention the default subject
    follows, not something this check can verify."""
    if commit_type not in CC_TYPES:
        return f"Commit type `{commit_type}` is not valid; use one of: {', '.join(CC_TYPES)}."
    if not scope:
        return "Commit scope is required and could not be inferred; pass --scope (for example `--scope sync`)."
    if not _SCOPE_RE.fullmatch(scope):
        return f"Commit scope `{scope}` is not valid; use lower-case letters, digits, . _ / and - only."
    if not subject or not subject.strip():
        return "Commit subject is required."
    if "\n" in subject or "\r" in subject:
        return "Commit subject must be one line."
    if len(subject) > 50:
        return f"Commit subject is {len(subject)} characters; the limit is 50."
    if subject.endswith("."):
        return "Commit subject must not end with a period."
    return None


def diff_stat_body(repo_dir, paths: List[str]) -> str:
    """Commit body from `git diff HEAD --stat` for paths that exist in HEAD
    plus one line per path that does not, capped at 20 lines. Paths and
    counts only. Every git call takes --literal-pathspecs, so a filename
    such as `a[1].md` or `:/` is a name, never a pattern. New versus tracked
    is decided by HEAD, never by the index, so a staged deletion is still a
    deletion; an unborn HEAD makes every path new."""
    repo_dir = pathlib.Path(repo_dir)
    in_head = set()
    if paths:
        listed = git_ops.run_git(
            repo_dir, ["--literal-pathspecs", "ls-tree", "-z", "--name-only", "HEAD", "--", *paths])
        if listed.returncode == 0:
            in_head = {name for name in listed.stdout.split("\0") if name}
    tracked = [path for path in paths if path in in_head]
    new = [path for path in paths if path not in in_head]
    lines: List[str] = []
    if tracked:
        stat = git_ops.run_git(
            repo_dir, ["--literal-pathspecs", "diff", "HEAD", "--stat", "--stat-width=72", "--", *tracked])
        lines += [line.rstrip() for line in stat.stdout.splitlines() if line.strip()]
    lines += [f" {path} | new file" for path in new]
    return "\n".join(lines[:20])


def build_message(repo_dir, paths: List[str], commit_type: Optional[str] = None,
                  scope: Optional[str] = None,
                  subject: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(message, None) or (None, error sentence)."""
    ctype = commit_type if commit_type is not None else infer_type(paths)
    cscope = scope if scope is not None else infer_scope(paths)
    csubject = (subject if subject is not None else f"update {len(paths)} file(s)").strip()
    error = validate_message_fields(ctype, cscope, csubject)
    if error:
        return None, error
    header = f"{ctype}({cscope}): {csubject}"
    body = diff_stat_body(repo_dir, paths)
    return (f"{header}\n\n{body}" if body else header), None


def _unstage(repo_dir, paths: List[str]) -> str:
    """Undo the staging a failed commit left behind, for exactly `paths` (literal
    pathspecs) and nothing else. "" on success, else a sentence fragment naming
    the failure so it is never silent. Never raises. An unborn HEAD has nothing
    to reset to, so `git rm --cached` is the fallback there."""
    repo_dir = pathlib.Path(repo_dir)
    failed: List[str] = []
    detail = ""
    # Batches keep every command line short: the same very long path list that made
    # `git commit` fail (WinError 206) must not also make the undo fail.
    for batch in _batches(paths):
        try:
            done = _reset_batch(repo_dir, batch)
            if done.returncode == 0:
                continue
            why = _first_line(done.stderr) or f"git exited {done.returncode}"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            why = type(exc).__name__
        failed += batch
        detail = detail or why
    if not failed:
        return ""
    return f" Also could not unstage {', '.join(failed)}: {detail}; run `git restore --staged` on them."


def _batches(paths: List[str], size: int = 20, budget: int = 8000):
    """paths in groups of at most `size` whose combined length stays under `budget`."""
    batch: List[str] = []
    used = 0
    for path in paths:
        if batch and (len(batch) >= size or used + len(path) + 1 > budget):
            yield batch
            batch, used = [], 0
        batch.append(path)
        used += len(path) + 1
    if batch:
        yield batch


def _reset_batch(repo_dir, batch: List[str]):
    """Unstage one batch; the `git rm --cached` fallback is for an unborn HEAD."""
    done = git_ops.run_git(repo_dir, ["--literal-pathspecs", "reset", "-q", "--", *batch])
    if done.returncode != 0:
        head = git_ops.run_git(repo_dir, ["rev-parse", "--verify", "-q", "HEAD"])
        if head.returncode == 1:
            done = git_ops.run_git(repo_dir, [
                "--literal-pathspecs", "rm", "--cached", "-q", "-r", "--ignore-unmatch", "--", *batch])
    return done


def _header_fields(message: str) -> Tuple[str, str]:
    """(scope, subject) of a built `type(scope): subject` message. A scope holds no
    parenthesis (validate_message_fields), so the first `): ` ends it."""
    head, _sep, subject = message.split("\n", 1)[0].partition("): ")
    return head.partition("(")[2], subject


def _save_preview(branch: str, paths: List[str], left_out: List[dict], message: str) -> str:
    lines = [f"Branch: {branch}", f"Would commit {len(paths)} path(s): {', '.join(paths)}"]
    lines += [f"Left out: {item['reason']}" for item in left_out]
    lines.append("Message:")
    lines += ["  " + line for line in message.splitlines()]
    return "\n".join(lines)


# --- step: check ------------------------------------------------------------

CHECK_NOT_STARTED = 127  # conventional shell "command not found" code


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the shell and every child it spawned (a bare proc.kill() would leave
    the real command running and holding the pipes open)."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        os.killpg(proc.pid, 9)  # run_check starts the shell in its own session
    except (OSError, AttributeError):
        proc.kill()


def run_check(command: str, repo_dir, timeout: float) -> Tuple[dict, str]:
    """Run one configured check command (shell=True: the commands are the
    repo's own). Returns ({command, exit, seconds}, stderr). A timeout kills
    the whole process tree and reports exit 124. Raises OSError or ValueError
    when the command cannot be started at all."""
    started = time.monotonic()
    proc = subprocess.Popen(
        command, shell=True, cwd=str(repo_dir), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=not IS_WINDOWS,
    )
    try:
        _out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            _out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            err = ""
        code = git_ops.TIMEOUT_RETURNCODE
        err = (err or "") + f"\ncheck timed out after {timeout}s"
    record = {"command": command, "exit": code, "seconds": round(time.monotonic() - started, 2)}
    return record, err or ""


def suggest_checks(repo_dir) -> str:
    repo_dir = pathlib.Path(repo_dir)
    ideas = []
    if (repo_dir / "tests").is_dir() or (repo_dir / "pyproject.toml").exists():
        ideas.append("python -m pytest -q")
    if (repo_dir / "package.json").exists():
        ideas.append("npm test")
    if not ideas:
        return "No test setup found; add a command to settings.git.checks."
    return f"Suggestion: set settings.git.checks to {json.dumps(ideas)}."


def check(repo_dir, apply: bool = False, ctx: Optional[RunContext] = None) -> dict:
    """Run settings.git.checks in order; the first non-zero exit, timeout or
    command that cannot start stops the list and is blocked. Zero checks
    configured is a pass with an amber note, never a silent green. Changes
    nothing in the repo (no staging, no commit)."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        commands, timeout = settings["checks"], settings["checkTimeoutSeconds"]
        if not commands:
            return make_result(
                "check", "pass", "0 checks configured. " + suggest_checks(repo_dir),
                amber="0 checks configured", checks=[])
        listing = redact("\n".join(f"  {n}. {command}" for n, command in enumerate(commands, 1)))
        if not apply:
            return make_result(
                "check", "pass", f"Would run {len(commands)} check(s) in order, {timeout}s each:\n{listing}",
                checks=[])
        records: List[dict] = []
        for command in commands:
            try:
                record, err = run_check(command, repo_dir, timeout)
            except (OSError, ValueError) as exc:
                records.append({"command": redact(command), "exit": CHECK_NOT_STARTED, "seconds": 0.0})
                return make_result(
                    "check", "blocked", listing, f"Check `{command}` could not be started: {exc}",
                    checks=records, stderr=str(exc))
            record["command"] = redact(command)
            records.append(record)
            if record["exit"] != 0:
                if record["exit"] == git_ops.TIMEOUT_RETURNCODE:
                    why = f"Check `{command}` timed out after {timeout}s."
                else:
                    why = f"Check `{command}` failed with exit {record['exit']}."
                return make_result("check", "blocked", listing, why, checks=records, stderr=err)
        return make_result("check", "pass", f"All {len(commands)} check(s) passed:\n{listing}", checks=records)

    return _run_step("check", repo_dir, apply, ctx, inner)


# --- GitHub CLI wrapper -----------------------------------------------------

GH_COMMAND: Optional[List[str]] = None  # None: find gh with shutil.which; tests put the stub here
GH_TIMEOUT_SECONDS = 60
GH_MISSING_SENTENCE = "Install GitHub CLI: winget install --id GitHub.cli, then run gh auth login."
GH_LOGIN_SENTENCE = "Run gh auth login."


class GhUnavailable(Exception):
    """gh is missing or not logged in; a step turns it into outcome "blocked"."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


def _gh_argv() -> Optional[List[str]]:
    if GH_COMMAND is not None:
        return list(GH_COMMAND)
    found = shutil.which("gh")
    return [found] if found else None


def _run_gh(args: List[str], repo_dir, timeout: float = GH_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run gh with args in repo_dir. stdout is returned as printed, for parsing
    only (never display it); stderr is redacted, so every sentence built from it
    is safe to show. A timeout is returncode 124. Raises GhUnavailable when gh
    cannot be started. No token is read, passed or stored here: gh keeps its own
    login."""
    base = _gh_argv()
    if base is None:
        raise GhUnavailable(GH_MISSING_SENTENCE)
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
    try:
        done = subprocess.run(
            [*base, *args], cwd=str(repo_dir), stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            [*base, *args], git_ops.TIMEOUT_RETURNCODE, "",
            f"gh {' '.join(args[:2])} timed out after {timeout}s")
    except OSError:
        raise GhUnavailable(GH_MISSING_SENTENCE) from None
    done.stdout = done.stdout or ""
    done.stderr = redact(done.stderr or "")
    return done


def ensure_gh(repo_dir) -> None:
    """Raises GhUnavailable with the sentence to show when gh is missing or
    `gh auth status` fails."""
    done = _run_gh(["auth", "status"], repo_dir, timeout=15)
    if done.returncode == git_ops.TIMEOUT_RETURNCODE:
        raise GhUnavailable("gh auth status did not answer in time; try again.")
    if done.returncode != 0:
        raise GhUnavailable(GH_LOGIN_SENTENCE)


def _gh_blocked(step: str, repo_dir, **extra) -> Optional[dict]:
    """A blocked result when gh is missing or not logged in, else None."""
    try:
        ensure_gh(repo_dir)
    except GhUnavailable as unavailable:
        return make_result(step, "blocked", "", unavailable.sentence, **extra)
    return None


def _gh_json(done: subprocess.CompletedProcess):
    """The parsed JSON of a successful gh call, None when it failed or printed
    something else."""
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except (ValueError, RecursionError):
        return None


# --- work branches: the guard propose, land and abort share ------------------

WORK_BRANCH_RE = re.compile(r"work/([a-z0-9-]+)/([a-z0-9][a-z0-9-]{0,40})")


def parse_work_branch(branch: str) -> Optional[Tuple[str, str]]:
    """(owner, slug) of a work/<owner>/<slug> branch name, else None."""
    match = WORK_BRANCH_RE.fullmatch(branch or "")
    return (match.group(1), match.group(2)) if match else None


def _on_work_branch(step: str, settings: dict, repo_dir) -> Tuple[Optional[str], Optional[dict]]:
    """(branch, None) when HEAD is on an unprotected work/<owner>/<slug> branch,
    else (None, a refused result that gives the reason)."""
    kind, branch = _current_branch_state(repo_dir)
    if kind == "detached":
        return None, make_result(
            step, "refused", "",
            "HEAD is detached, not on a branch; run `start <slug>` to work on a branch of your own.")
    if kind != "branch" or not branch:
        return None, make_result(
            step, "refused", "", "Cannot tell which branch you are on; no changes were made.")
    try:
        assert_not_protected(branch, effective_protected(settings))
    except Refusal as refusal:
        return None, make_result(step, "refused", "", refusal.sentence, branch=branch)
    if parse_work_branch(branch) is None:
        return None, make_result(
            step, "refused", "",
            f"`{branch}` is not a work branch (work/<owner>/<slug>), and `{step}` only runs on one; "
            "run `start <slug>` to make one.", branch=branch)
    return branch, None


# --- step: abort ------------------------------------------------------------


def abort(repo_dir, apply: bool = False, ctx: Optional[RunContext] = None) -> dict:
    """Park the current work branch: stash uncommitted changes (untracked files
    included) as parked/<owner>/<slug>, rename the branch to that name and check
    out main. Never deletes a branch, a commit or a file, and never touches the
    remote branch."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        branch, refused = _on_work_branch("abort", settings, repo_dir)
        if refused:
            return refused
        owner, slug = parse_work_branch(branch)
        parked = f"parked/{owner}/{slug}"
        exists = git_ops.run_git(repo_dir, ["rev-parse", "--verify", "--quiet", f"refs/heads/{parked}"])
        if exists.returncode == 0:
            return make_result(
                "abort", "refused", "",
                f"Branch `{parked}` already exists; rename or delete it first. Nothing was changed.",
                branch=branch)
        main = settings["main"]
        dirty = _relevant_paths(repo_dir)
        lines = []
        if dirty:
            lines.append(f"Would stash {len(dirty)} uncommitted change(s), untracked files included, as `{parked}`.")
        lines += [
            f"Would rename `{branch}` to `{parked}` and check out `{main}`.",
            "No branch, commit or file is deleted, and the remote branch is not touched.",
        ]
        preview = "\n".join(lines)
        if not apply:
            return make_result("abort", "pass", preview, branch=branch, parked=parked)
        kept = ""
        if dirty:
            stashed = git_ops.run_git(repo_dir, ["stash", "push", "--include-untracked", "-m", parked])
            if stashed.returncode != 0:
                return make_result(
                    "abort", "blocked", preview,
                    f"git could not stash the changes: {_first_line(stashed.stderr)}. Nothing was changed.",
                    branch=branch, stderr=stashed.stderr)
            kept = f" Your changes are in the stash `{parked}` (git stash list)."
        renamed = git_ops.run_git(repo_dir, ["branch", "-m", branch, parked])
        if renamed.returncode != 0:
            return make_result(
                "abort", "blocked", preview,
                f"git could not rename `{branch}` to `{parked}`: {_first_line(renamed.stderr)}.{kept}",
                branch=branch, stderr=renamed.stderr)
        checkout = git_ops.run_git(repo_dir, ["checkout", main])
        if checkout.returncode != 0:
            return make_result(
                "abort", "blocked", preview,
                f"Renamed `{branch}` to `{parked}` but could not check out `{main}`: "
                f"{_first_line(checkout.stderr)}. You are still on `{parked}`.{kept}",
                branch=parked, stderr=checkout.stderr)
        return make_result(
            "abort", "pass", f"Parked `{branch}` as `{parked}` and checked out `{main}`.{kept}",
            branch=parked, parked=parked)

    return _run_step("abort", repo_dir, apply, ctx, inner)


# --- receipts as input: what propose and land read back ---------------------


def read_receipts(repo_dir) -> List[dict]:
    """Every readable line of the receipt log (_log/runs/git-flow.ndjson), oldest first; a line
    that is not a JSON object is skipped, a missing file is an empty list."""
    path = pathlib.Path(repo_dir).joinpath(*RECEIPT_PARTS)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: List[dict] = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def latest_check_receipt(repo_dir, sha: str) -> Optional[dict]:
    """The newest `check` receipt that ended on commit sha (check never moves
    HEAD, so its sha_after is the commit it checked), or None."""
    if not sha:
        return None
    for item in reversed(read_receipts(repo_dir)):
        if item.get("step") == "check" and item.get("sha_after") == sha:
            return item
    return None


def _check_green(receipt: Optional[dict]) -> bool:
    return bool(receipt) and receipt.get("outcome") == "pass" and receipt.get("exit_code") == 0


def receipts_since_start(repo_dir, branch: str) -> List[dict]:
    """The receipts of branch from its newest `start` on (all of them when there
    is no start receipt)."""
    mine = [item for item in read_receipts(repo_dir) if item.get("branch") == branch]
    starts = [index for index, item in enumerate(mine) if item.get("step") == "start"]
    return mine[starts[-1]:] if starts else mine


# --- pull requests ----------------------------------------------------------

PR_FIELDS = "number,url,headRefOid,baseRefName,reviewDecision,latestReviews"
PR_TITLE_LIMIT = 240
PR_BODY_LIMIT = 8000
# A merge commit subject is one log line; git's own advice is 72, GitHub takes more, 100 is the cap here.
MERGE_SUBJECT_LIMIT = 100


def _custom_line_refusal(label: str, text: str, limit: int) -> Optional[str]:
    """The refusal sentence when a caller-given title or subject is empty once
    stripped, longer than limit (refused, never cut short) or not one line; None
    when it is fine. The secret rules are applied by the caller."""
    text = text.strip()
    if not text:
        return f"The {label} is empty; give some text or leave the option out."
    if len(text) > limit:
        return f"The {label} is {len(text)} characters, longer than the limit of {limit}. Nothing was changed."
    if "\n" in text or "\r" in text:
        return f"The {label} must be one line. Nothing was changed."
    return None


def _open_prs(repo_dir, branch: str) -> Optional[List[dict]]:
    """The open pull requests whose head is branch, as gh prints them; None when
    gh failed or printed something that is not a list of pull requests."""
    done = _run_gh(["pr", "list", "--head", branch, "--state", "open", "--json", PR_FIELDS], repo_dir)
    data = _gh_json(done)
    if not isinstance(data, list) or not all(
            isinstance(item, dict) and _is_int(item.get("number")) for item in data):
        return None
    return data


def _find_open_pr(step: str, repo_dir, branch: str) -> Tuple[Optional[dict], Optional[dict]]:
    """(pr, None) with the one open pull request of branch, or pr None when there
    is none; (None, result) with a blocked or refused result when gh failed or
    more than one is open."""
    prs = _open_prs(repo_dir, branch)
    if prs is None:
        return None, make_result(
            step, "blocked", "",
            f"gh could not list the pull requests of `{branch}`; nothing was changed.", branch=branch)
    if len(prs) > 1:
        numbers = ", ".join(f"#{item['number']}" for item in prs)
        return None, make_result(
            step, "refused", "",
            f"More than one open pull request has `{branch}` as its head ({numbers}); close the extras first.",
            branch=branch)
    return (prs[0] if prs else None), None


def _commits_ahead(repo_dir, main: str) -> Optional[List[Tuple[str, str]]]:
    """(short sha, subject) of every commit on HEAD that origin/<main> lacks,
    oldest first; None when git could not list them."""
    done = git_ops.run_git(repo_dir, ["log", "--reverse", "--format=%h%x09%s", f"origin/{main}..HEAD"])
    if done.returncode != 0:
        return None
    commits = []
    for line in done.stdout.splitlines():
        short, _tab, subject = line.partition("\t")
        if short:
            commits.append((short, subject))
    return commits


def _pr_body(commits: List[Tuple[str, str]], receipts: List[dict], no_checks: bool) -> str:
    """The pull request body: the commit subjects, then the receipts since start
    (step, outcome, each check with exit and seconds). Not redacted here: the
    caller scans it first and refuses on a hit."""
    lines = ["## Commits", ""]
    lines += [f"- {subject} ({short})" for short, subject in commits]
    lines += ["", "## Receipts since start", ""]
    for receipt in receipts:
        lines.append(f"- {receipt.get('step')}: {receipt.get('outcome')}")
        for record in receipt.get("checks") or []:
            if isinstance(record, dict):
                lines.append(f"  - `{record.get('command')}` exit {record.get('exit')}, {record.get('seconds')}s")
    if no_checks:
        lines += ["", "No checks ran: 0 checks are configured for this repo."]
    body = "\n".join(lines)
    return body if len(body) <= PR_BODY_LIMIT else body[:PR_BODY_LIMIT - 14] + "\n(truncated)"


def _pr_text_hits(title: str, body: str) -> List[Tuple[str, str]]:
    """(field, rule) for every secret rule the title or the body trips. The same
    rules as message_hits, applied to each text whole: message_hits reads only a
    `type(scope): subject` header, and a pull request title is any commit subject."""
    return [("title", rule) for rule in text_hits(title)] + [("body", rule) for rule in text_hits(body)]


# --- step: propose ----------------------------------------------------------


def propose(repo_dir, apply: bool = False, ctx: Optional[RunContext] = None,
            title: Optional[str] = None) -> dict:
    """Push the work branch and open a pull request for it (or, when one is
    open already, push the new commits to it). Needs a green `check` receipt for
    the current HEAD. The title is the newest commit subject unless title is
    given; the body lists the commits and the receipts since start."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        branch, refused = _on_work_branch("propose", settings, repo_dir)
        if refused:
            return refused
        main = settings["main"]
        dirty = _relevant_paths(repo_dir)
        if dirty:
            return make_result(
                "propose", "refused", "",
                f"The working tree has {len(dirty)} uncommitted change(s), first `{dirty[0]}`; "
                "run `save` first.", branch=branch)
        ahead = git_ops.run_git(repo_dir, ["rev-list", "--count", f"origin/{main}..HEAD"])
        if ahead.returncode != 0:
            return make_result(
                "propose", "refused", "",
                f"Cannot compare with `origin/{main}`: {_first_line(ahead.stderr)}; "
                "run `git fetch origin` first.", branch=branch)
        if ahead.stdout.strip() in ("", "0"):
            return make_result(
                "propose", "refused", "",
                f"Nothing to propose: `{branch}` has no commit on top of `origin/{main}`; run `save` first.",
                branch=branch)
        receipt = latest_check_receipt(repo_dir, head_sha(repo_dir))
        if not _check_green(receipt):
            return make_result("propose", "refused", "", "Run check on this commit first.", branch=branch)
        commits = _commits_ahead(repo_dir, main)
        if not commits:
            return make_result(
                "propose", "blocked", "", "git could not list the commits of this branch; nothing was changed.",
                branch=branch)
        if title is None:
            chosen = commits[-1][1].strip()[:PR_TITLE_LIMIT] or f"Update {branch}"
        else:
            too_long = _custom_line_refusal("pull request title", title, PR_TITLE_LIMIT)
            if too_long:
                return make_result("propose", "refused", "", too_long, branch=branch)
            chosen = title.strip()
        no_checks = receipt.get("checks") == []
        body = _pr_body(commits, receipts_since_start(repo_dir, branch), no_checks)
        hits = _pr_text_hits(chosen, body)
        if hits:
            found = "; ".join(f"the pull request {field} (rule: {rule})" for field, rule in hits)
            return make_result(
                "propose", "refused", "",
                f"Possible secret in {found}. Nothing was pushed or created. "
                "Take it out of the commit message before proposing.", branch=branch)
        blocked = _gh_blocked("propose", repo_dir, branch=branch)
        if blocked:
            return blocked
        existing, problem = _find_open_pr("propose", repo_dir, branch)
        if problem:
            return problem
        note = "No checks ran (0 checks configured)." if no_checks else ""
        if existing:
            pr = {"number": existing["number"], "url": existing.get("url", ""), "merge_commit": ""}
            preview = (f"Pull request #{pr['number']} is already open: {redact(pr['url'])}\n"
                       f"Would push `{branch}` to origin so it carries the current commits; "
                       "no new pull request is created.")
            if title is not None:
                preview += "\nThe existing title was not changed: --title applies only to a new pull request."
        else:
            pr = None
            preview = (f"Would push `{branch}` to origin and open a pull request into `{main}`.\n"
                       f"Title: {redact(chosen)}\nCommits: {len(commits)}")
        if note:
            preview += "\n" + note
        if not apply:
            return make_result("propose", "pass", preview, branch=branch, pr=pr)
        pushed = git_ops.run_git(
            repo_dir, ["push", "-u", "origin", branch], timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
        if pushed.returncode != 0:
            return make_result(
                "propose", "blocked", preview,
                f"git push of `{branch}` failed: {_first_line(pushed.stderr)}. No pull request was created.",
                branch=branch, stderr=pushed.stderr)
        if existing:
            return make_result(
                "propose", "pass", f"Pushed `{branch}`; pull request #{pr['number']} is already open: "
                f"{redact(pr['url'])}"
                + ("\nThe existing title was not changed." if title is not None else ""), branch=branch, pr=pr)
        created = _run_gh(
            ["pr", "create", "--base", main, "--head", branch, "--title", chosen, "--body", body], repo_dir)
        if created.returncode != 0:
            return make_result(
                "propose", "blocked", preview,
                f"`{branch}` was pushed but `gh pr create` failed: {_first_line(created.stderr)}.",
                branch=branch, stderr=created.stderr)
        lines = [line.strip() for line in created.stdout.splitlines() if line.strip()]
        url = lines[-1] if lines else ""
        numbered = re.search(r"/pull/(\d+)\s*$", url)
        if not numbered:
            return make_result(
                "propose", "blocked", preview,
                f"`{branch}` was pushed but gh did not print a pull request url; check GitHub.", branch=branch)
        pr = {"number": int(numbered.group(1)), "url": url, "merge_commit": ""}
        return make_result(
            "propose", "pass",
            f"Pushed `{branch}` and opened pull request #{pr['number']}: {redact(url)}"
            + (f"\n{note}" if note else ""), branch=branch, pr=pr)

    return _run_step("propose", repo_dir, apply, ctx, inner)


# --- step: land (refusals, merge check and preview) --------------------------


def _count_approvals(pr: dict) -> int:
    """Approvals of a pull request, counting only the latest review of each
    author (gh prints `latestReviews`; a repeated author is deduplicated here too)."""
    reviews = pr.get("latestReviews")
    if not isinstance(reviews, list):
        return 0
    latest = {}
    for review in reviews:
        if isinstance(review, dict):
            author = review.get("author")
            login = author.get("login") if isinstance(author, dict) else None
            latest[login or f"#{len(latest)}"] = review.get("state")
    return sum(1 for state in latest.values() if state == "APPROVED")


def _merge_tree(repo_dir, main: str) -> Tuple[Optional[str], List[str], str]:
    """(tree, conflicts, error) for merging HEAD into origin/<main> without
    touching the working tree: the merged tree id on a clean merge, the
    conflicted paths on a conflict, an error sentence when git could not run it."""
    done = git_ops.run_git(
        repo_dir, ["merge-tree", "--write-tree", "--name-only", f"origin/{main}", "HEAD"])
    lines = done.stdout.splitlines()
    if done.returncode == 0 and lines:
        return lines[0].strip(), [], ""
    if done.returncode == 1 and lines:
        names = []
        for line in lines[1:]:
            if not line.strip():
                break
            names.append(line.strip())
        return None, names, ""
    return None, [], _first_line(done.stderr) or f"git merge-tree exited {done.returncode}"


def _up_to_date(repo_dir, main: str) -> bool:
    """True when origin/<main> is already part of HEAD's history."""
    done = git_ops.run_git(repo_dir, ["merge-base", "--is-ancestor", f"origin/{main}", "HEAD"])
    return done.returncode == 0


def land(repo_dir, apply: bool = False, ctx: Optional[RunContext] = None,
         subject: Optional[str] = None) -> dict:
    """Merge the open pull request of the current work branch with a merge
    commit (its subject is gh's default unless subject is given), then return to
    main and delete the work branch locally and on origin.
    Needs a matching pushed head, no requested changes, enough approvals
    (settings.git.pr.approvals), a clean merge, and green checks."""
    repo_dir = pathlib.Path(repo_dir)

    def inner(settings: dict) -> dict:
        branch, refused = _on_work_branch("land", settings, repo_dir)
        if refused:
            return refused
        main = settings["main"]
        dirty = _relevant_paths(repo_dir)
        if dirty:
            return make_result(
                "land", "refused", "",
                f"The working tree has {len(dirty)} uncommitted change(s), first `{dirty[0]}`; "
                "run `save` or `abort` first.", branch=branch)
        merge_subject = None
        if subject is not None:
            merge_subject = subject.strip()
            bad = _custom_line_refusal("merge commit subject", subject, MERGE_SUBJECT_LIMIT)
            if not bad and text_hits(merge_subject):
                bad = (f"Possible secret in the merge commit subject (rule: {', '.join(text_hits(merge_subject))}). "
                       "Nothing was merged. Take it out of the subject.")
            if bad:
                return make_result("land", "refused", "", bad, branch=branch)
        blocked = _gh_blocked("land", repo_dir, branch=branch)
        if blocked:
            return blocked
        pr, problem = _find_open_pr("land", repo_dir, branch)
        if problem:
            return problem
        if pr is None:
            return make_result(
                "land", "refused", "", f"No open pull request for `{branch}`; run `propose` first.",
                branch=branch)
        base = pr.get("baseRefName")
        if base != main:
            return make_result(
                "land", "refused", "",
                f"Pull request #{pr['number']} targets `{redact(str(base))}`, not `{main}`; "
                f"land only merges into `{main}`. Nothing was changed.", branch=branch)
        sha = head_sha(repo_dir)
        if pr.get("headRefOid") != sha:
            return make_result("land", "refused", "", "Push first: run propose.", branch=branch)
        number = pr["number"]
        pr_info = {"number": number, "url": pr.get("url", ""), "merge_commit": ""}
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            return make_result(
                "land", "refused", "",
                f"A reviewer requested changes on pull request #{number}; resolve them before landing.",
                branch=branch)
        needed, have = settings["pr"]["approvals"], _count_approvals(pr)
        if have < needed:
            return make_result(
                "land", "refused", "",
                f"Pull request #{number} has {have} approval(s); settings.git.pr.approvals requires {needed}.",
                branch=branch)
        fetched = git_ops.run_git(
            repo_dir, ["fetch", "origin"], timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
        if fetched.returncode != 0:
            return make_result(
                "land", "blocked", "", f"git fetch failed: {_first_line(fetched.stderr)}. Nothing was merged.",
                branch=branch, stderr=fetched.stderr)
        tree, conflicts, error = _merge_tree(repo_dir, main)
        if error:
            return make_result(
                "land", "blocked", "",
                f"git merge-tree could not run ({error}); it needs git 2.38 or newer. Nothing was merged.",
                branch=branch)
        if conflicts:
            named = ", ".join(f"`{path}`" for path in conflicts)
            return make_result(
                "land", "refused", "",
                f"Merging `{branch}` into `origin/{main}` conflicts in {named}; "
                "resolve the conflicts on the branch first.", branch=branch)
        amber = ""
        current = _up_to_date(repo_dir, main)
        if current:
            receipt = latest_check_receipt(repo_dir, sha)
            if receipt is None:
                return make_result("land", "refused", "", "Run check on this commit first.", branch=branch)
            if not _check_green(receipt):
                return make_result(
                    "land", "blocked", "",
                    "The last check on this commit failed; fix it and run `check` again.", branch=branch)
            verify = f"reuse the passing check receipt {receipt.get('receipt_id')}"
        elif settings["checks"]:
            verify = (f"run {len(settings['checks'])} check(s) on the merge result in a temporary worktree "
                      f"(`origin/{main}` moved since this branch was made)")
        else:
            verify = "run no checks on the merge result"
            amber = "0 checks configured"
        preview = "\n".join([
            f"Pull request #{number}: {redact(pr_info['url'])}",
            f"Would merge `{branch}` into `{main}` with a merge commit through `gh pr merge` "
            "(never squash or rebase).",
            f"Would {verify}.",
            f"Then would check out `{main}`, fast-forward it, and delete `{branch}` locally and on origin.",
        ])
        if merge_subject is not None:
            preview += f"\nMerge commit subject: {redact(merge_subject)}"
        if not apply:
            return make_result("land", "pass", preview, amber=amber, branch=branch, pr=pr_info)
        records: List[dict] = []
        if not current:
            records, failure, err = _checks_on_merge_result(
                repo_dir, tree, main, settings["checks"], settings["checkTimeoutSeconds"])
            if failure:
                return make_result(
                    "land", "blocked", preview, failure, branch=branch, pr=pr_info, checks=records, stderr=err)
        merge_argv = ["pr", "merge", str(number), "--merge", "--match-head-commit", sha]
        if merge_subject is not None:
            merge_argv += ["--subject", merge_subject]
        merged = _run_gh(merge_argv, repo_dir)
        if merged.returncode != 0:
            return make_result(
                "land", "blocked", preview,
                f"`gh pr merge` failed: {_first_line(merged.stderr)}. "
                f"Check pull request #{number} on GitHub before landing again.",
                branch=branch, pr=pr_info, checks=records, stderr=merged.stderr)
        pr_info["merge_commit"] = _merge_commit(repo_dir, number)
        left = _after_merge(repo_dir, branch, main)
        done = f"Merged pull request #{number} into `{main}`"
        if pr_info["merge_commit"]:
            done += f" (merge commit {pr_info['merge_commit'][:7]})"
        if left:
            amber = "; ".join(part for part in [amber, "merged, but still to do: " + "; ".join(left)] if part)
            done += "."
        else:
            done += f". Checked out `{main}` and deleted `{branch}` locally and on origin."
        return make_result("land", "pass", done, amber=amber, branch=branch, pr=pr_info, checks=records)

    return _run_step("land", repo_dir, apply, ctx, inner)


# --- step: land (apply) -------------------------------------------------------


def _remove_worktree(repo_dir, path) -> None:
    """Remove a temporary worktree however far its creation got: git's own
    removal, then the folder, then the bookkeeping. Never raises."""
    git_ops.run_git(repo_dir, ["worktree", "remove", "--force", str(path)])
    shutil.rmtree(str(path), ignore_errors=True)
    git_ops.run_git(repo_dir, ["worktree", "prune"])


def _checks_on_merge_result(repo_dir, tree: str, main: str, commands: List[str],
                            timeout: float) -> Tuple[List[dict], str, str]:
    """(records, failure, stderr): run commands in order on the merge result,
    that is, in a detached temporary worktree of a merge commit built from tree
    (HEAD merged into origin/<main>). failure is "" when every command passed,
    else the sentence to show. The worktree is always removed, in a finally."""
    if not commands:
        return [], "", ""
    built = git_ops.run_git(
        repo_dir, ["commit-tree", tree, "-p", f"origin/{main}", "-p", "HEAD", "-m", "siteoffice land check"])
    oid = built.stdout.strip()
    if built.returncode != 0 or not oid:
        return [], (f"git could not build the merge result to check ({_first_line(built.stderr)}). "
                    "Nothing was merged."), built.stderr
    work = pathlib.Path(tempfile.mkdtemp(prefix="siteoffice-land-"))
    try:
        added = git_ops.run_git(repo_dir, ["worktree", "add", "--detach", str(work), oid])
        if added.returncode != 0:
            return [], (f"git could not make the temporary worktree ({_first_line(added.stderr)}). "
                        "Nothing was merged."), added.stderr
        records: List[dict] = []
        for command in commands:
            try:
                record, err = run_check(command, work, timeout)
            except (OSError, ValueError) as exc:
                records.append({"command": redact(command), "exit": CHECK_NOT_STARTED, "seconds": 0.0})
                return records, (f"Check `{command}` could not be started on the merge result: {exc}. "
                                 "Nothing was merged."), str(exc)
            record["command"] = redact(command)
            records.append(record)
            if record["exit"] != 0:
                if record["exit"] == git_ops.TIMEOUT_RETURNCODE:
                    why = f"Check `{command}` timed out after {timeout}s on the merge result."
                else:
                    why = f"Check `{command}` failed with exit {record['exit']} on the merge result."
                return records, why + " Nothing was merged.", err
        return records, "", ""
    finally:
        _remove_worktree(repo_dir, work)


def _remote_has_branch(repo_dir, branch: str) -> Optional[bool]:
    """True or False when origin answers, None when git could not ask."""
    done = git_ops.run_git(
        repo_dir, ["ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
    if done.returncode != 0:
        return None
    return bool(done.stdout.strip())


def _after_merge(repo_dir, branch: str, main: str) -> List[str]:
    """Go back to main once the pull request is merged: check out main,
    fast-forward it to origin/<main>, delete the local work branch and the
    remote one. One sentence per step that failed (the merge itself stands), an
    empty list when all went well. A remote branch GitHub already deleted is fine."""
    left: List[str] = []
    checked_out = git_ops.run_git(repo_dir, ["checkout", main])
    if checked_out.returncode != 0:
        left.append(f"could not check out `{main}` ({_first_line(checked_out.stderr)}); "
                    f"you are still on `{branch}` and it is not deleted locally")
    else:
        fetched = git_ops.run_git(
            repo_dir, ["fetch", "origin"], timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
        forwarded = git_ops.run_git(repo_dir, ["merge", "--ff-only", f"origin/{main}"])
        if fetched.returncode != 0 or forwarded.returncode != 0:
            left.append(f"could not fast-forward `{main}` to `origin/{main}`; run `git pull --ff-only`")
        deleted = git_ops.run_git(repo_dir, ["branch", "-d", branch])
        if deleted.returncode != 0:
            left.append(f"could not delete the local branch `{branch}` ({_first_line(deleted.stderr)}); "
                        f"run `git branch -d {branch}`")
    pushed = git_ops.run_git(
        repo_dir, ["push", "origin", "--delete", branch], timeout=git_ops.GIT_NETWORK_TIMEOUT_SECONDS)
    if pushed.returncode != 0 and _remote_has_branch(repo_dir, branch) is not False:
        left.append(f"could not delete `origin/{branch}` ({_first_line(pushed.stderr)}); "
                    f"run `git push origin --delete {branch}`")
    return left


def _merge_commit(repo_dir, number: int) -> str:
    """The merge commit gh reports for a merged pull request, "" when it does not say."""
    data = _gh_json(_run_gh(["pr", "view", str(number), "--json", "mergeCommit,state"], repo_dir))
    commit = data.get("mergeCommit") if isinstance(data, dict) else None
    oid = commit.get("oid") if isinstance(commit, dict) else None
    return oid if isinstance(oid, str) else ""


# --- command line -----------------------------------------------------------


def find_repo(start=None) -> Optional[pathlib.Path]:
    """The nearest directory at or above start (default: the current
    directory) that carries a siteoffice.json; None when there is none (the
    caller refuses rather than guess the cwd)."""
    start = pathlib.Path(start or os.getcwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "siteoffice.json").exists():
            return candidate
    return None


class _UsageParser(argparse.ArgumentParser):
    """argparse keeps its exit code 2 for a usage error, which is also the code
    of `refused`; the "usage error:" line on stderr tells the two apart."""

    def error(self, message):
        sys.stderr.write(f"usage error: {message}\n")
        self.print_usage(sys.stderr)
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--apply", action="store_true", help="act (default: preview only)")
    common.add_argument("--repo", help="repo directory (default: nearest siteoffice.json root)")
    common.add_argument("--harness", help="session harness for the receipt (else SITEOFFICE_HARNESS)")
    common.add_argument("--model", help="model name for the receipt (else SITEOFFICE_MODEL)")
    parser = _UsageParser(prog="git_flow.py", description="siteoffice-git: start, save, check, propose, land, abort")
    steps = parser.add_subparsers(dest="step", required=True)
    start_parser = steps.add_parser("start", parents=[common], help="create work/<owner>/<slug> from main")
    start_parser.add_argument("slug")
    save_parser = steps.add_parser("save", parents=[common], help="commit the vetted changed paths")
    save_parser.add_argument("--type", dest="commit_type")
    save_parser.add_argument("--scope")
    save_parser.add_argument("--subject")
    steps.add_parser("check", parents=[common], help="run settings.git.checks")
    propose_parser = steps.add_parser(
        "propose", parents=[common], help="push the work branch and open a pull request")
    propose_parser.add_argument("--title", help="pull request title (default: the newest commit subject)")
    land_parser = steps.add_parser("land", parents=[common], help="merge the pull request and clean up")
    land_parser.add_argument("--subject", help="merge commit subject (default: gh's own)")
    steps.add_parser("abort", parents=[common], help="park the work branch as parked/<owner>/<slug>")
    return parser


def format_result(result: dict) -> str:
    lines = [f"[{result['step']}] {result['outcome']}"]
    if result["preview"]:
        lines.append(result["preview"])
    if result["refusal"]:
        lines.append(f"Refusal: {result['refusal']}")
    if result["receipt"] is not None:
        state = "written" if result["receipt_written"] else "NOT written"
        lines.append(f"receipt {result['receipt']['receipt_id']} {state} ({'/'.join(RECEIPT_PARTS)})")
    return "\n".join(lines)


def _no_receipt(result: dict) -> dict:
    result["receipt_written"] = False
    return result


def _blocked_by_exception(step: str, repo, apply: bool, ctx: RunContext, exc: Exception) -> dict:
    """A blocked result for an exception no step handled: one redacted line
    naming the exception type. With apply, a receipt is attempted through the
    normal wrapper; if that fails as well the result simply carries none."""
    detail = " ".join(redact(str(exc)).split())[:200]
    sentence = f"The {step} step stopped on an unexpected {type(exc).__name__}"
    sentence += f": {detail}." if detail else "."
    if repo is not None:
        try:
            return _run_step(step, repo, apply, ctx, lambda settings: make_result(step, "blocked", "", sentence))
        except Exception:  # a failed receipt attempt never fails the CLI
            pass
    return _no_receipt(make_result(step, "blocked", "", redact(sentence)))


def _git_toplevel(repo) -> Optional[pathlib.Path]:
    """The git top-level of repo, or None when git cannot say (not a repo, git
    missing, timeout, empty output); None is never read as "the same"."""
    try:
        done = git_ops.run_git(pathlib.Path(repo), ["rev-parse", "--show-toplevel"])
    except OSError:
        return None
    top = done.stdout.strip() if done.returncode == 0 else ""
    return pathlib.Path(top) if top else None


def _same_directory(first, second) -> bool:
    """Same directory once symlinks, short names and (on Windows) case are resolved."""
    def normal(path):
        return os.path.normcase(os.path.realpath(str(path)))
    return normal(first) == normal(second)


def _nested_refusal(step: str, repo) -> Optional[dict]:
    """A refusal when repo sits inside a git repository but is not its top-level.
    git reports changed paths relative to the top-level, so the denylist, owner
    and secrets-scan steps only see the right paths from there. _run_step calls
    this first, so the library API and the CLI are covered by the same check."""
    top = _git_toplevel(repo)
    if top is None or _same_directory(repo, top):
        return None
    return _no_receipt(make_result(
        step, "refused", "",
        f"`{repo}` is inside the git repository at `{top}`, not at its root; "
        f"run from the git root or pass --repo {top}."))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ctx = RunContext(harness=args.harness, model=args.model)
    repo = None
    try:
        repo = pathlib.Path(args.repo).resolve() if args.repo else find_repo()
        if repo is None:
            result = _no_receipt(make_result(
                args.step, "refused", "",
                f"No siteoffice.json found in `{os.getcwd()}` or any folder above it; "
                "run from inside a Siteoffice repo or pass --repo <dir>."))
        elif not repo.is_dir():
            result = _no_receipt(make_result(args.step, "refused", "", f"`{repo}` is not a directory."))
        elif args.step == "start":
            result = start(repo, args.slug, apply=args.apply, ctx=ctx)
        elif args.step == "save":
            result = save(repo, args.commit_type, args.scope, args.subject, apply=args.apply, ctx=ctx)
        elif args.step == "check":
            result = check(repo, apply=args.apply, ctx=ctx)
        elif args.step == "propose":
            result = propose(repo, apply=args.apply, ctx=ctx, title=args.title)
        elif args.step == "land":
            result = land(repo, apply=args.apply, ctx=ctx, subject=args.subject)
        else:
            result = abort(repo, apply=args.apply, ctx=ctx)
    except Exception as exc:
        result = _blocked_by_exception(args.step, repo, args.apply, ctx, exc)
    print(redact(format_result(result)))
    return EXIT_CODES[result["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
