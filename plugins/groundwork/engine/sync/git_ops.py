"""Git plumbing for the sync helper: status, pull, push, commit. Stdlib
only (subprocess), Python 3.8+. Every function takes an explicit repo_dir;
nothing here assumes it is running against the real siteoffice-private
clone, which is what makes it testable against a throwaway temp repo
(Global Constraint). No network calls beyond `git fetch/pull/push` against
whatever remote the caller's repo_dir has configured (localhost bare repos
in tests, the one configured `origin` in production).

T10 (R02, C03): every git subprocess call now carries a time limit, since
the sync helper is a single-threaded HTTP server (sync\\helper.py); a git
command left to hang forever (an expired credential prompting
interactively, a dead network) used to block every other request on this
process, not just the one that triggered it. push()/pull() also now check
for a configured remote before attempting anything and report their OWN
success/failure explicitly (opOk/opMessage), rather than silently falling
back to whatever get_status() computes afterward, which does not
necessarily reflect whether the push/pull itself actually worked.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

import owner_check

STATE_FILENAME = "siteoffice_sync_state.json"
# A local git op (status/commit) should never take long; network ops
# (fetch/pull/push) get their own, more generous limit below. Both exist so
# a hung credential prompt or a dead network cannot block the single
# HTTP server thread indefinitely (R02, C03).
GIT_TIMEOUT_SECONDS = 15
GIT_NETWORK_TIMEOUT_SECONDS = 30
TIMEOUT_RETURNCODE = 124  # conventional shell "command timed out" code


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_git(
    repo_dir: pathlib.Path, args: List[str], timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    # encoding="utf-8" explicitly: git always writes paths and messages as
    # UTF-8 (i18n.logOutputEncoding/core.quotepath defaults), but
    # text=True alone decodes with the platform's locale encoding, which
    # on Windows is often a codepage other than UTF-8 and silently
    # mangles any non-ASCII path (I2: relevant since this is a
    # Dutch-language estate).
    try:
        return subprocess.run(
            ["git", *args], cwd=str(repo_dir), capture_output=True, text=True,
            encoding="utf-8", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # A synthetic CompletedProcess, same shape every caller already
        # checks (.returncode/.stdout/.stderr): a timeout is a distinct,
        # named failure, never silently read as "0 changes" or "ok".
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=TIMEOUT_RETURNCODE, stdout="",
            stderr=f"git {' '.join(args)} timed out after {timeout}s",
        )


def has_remote(repo_dir: pathlib.Path, remote: str = "origin") -> bool:
    """False when no such remote is configured at all (R02: "P01, P02 and
    P11 returned success without a usable remote"), distinct from a
    configured-but-unreachable remote, which push()/pull() surface as their
    own git-command failure instead."""
    result = run_git(repo_dir, ["remote", "get-url", remote])
    return result.returncode == 0 and bool(result.stdout.strip())



def _git_dir(repo_dir: pathlib.Path) -> pathlib.Path:
    """The real gitdir for repo_dir. In an ordinary clone that is
    repo_dir/.git, but in a git worktree .git is a FILE (a pointer to the
    real gitdir elsewhere), not a directory, so writing under it directly
    would raise NotADirectoryError. `git rev-parse --absolute-git-dir`
    resolves either case correctly. Falls back to repo_dir / ".git" if the
    rev-parse call itself fails, so a caller still gets a path rather than
    a crash."""
    result = run_git(repo_dir, ["rev-parse", "--absolute-git-dir"])
    if result.returncode == 0 and result.stdout.strip():
        return pathlib.Path(result.stdout.strip())
    return repo_dir / ".git"


def _state_path(repo_dir: pathlib.Path) -> pathlib.Path:
    # Lives under the real gitdir (see _git_dir), which is never committed
    # and always exists per clone or worktree, so this needs no extra
    # .gitignore entry of its own.
    return _git_dir(repo_dir) / STATE_FILENAME


def _read_state(repo_dir: pathlib.Path) -> Tuple[Optional[str], Optional[str]]:
    path = _state_path(repo_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("lastPull"), data.get("lastPush")
    except (OSError, ValueError):
        return None, None


def _write_state(repo_dir: pathlib.Path, **fields) -> None:
    # Best-effort persistence: losing lastPull/lastPush on a write failure
    # is not worth crashing the caller over, the same "never crash on a
    # side concern" posture the rest of this module takes.
    try:
        path = _state_path(repo_dir)
        last_pull, last_push = _read_state(repo_dir)
        data = {"lastPull": last_pull, "lastPush": last_push}
        data.update(fields)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def ahead_behind(repo_dir: pathlib.Path) -> Tuple[int, int]:
    """(ahead, behind) against the upstream (@{u}). (0, 0) if no upstream
    is configured (a repo with no remote at all, e.g. a synthetic fixture
    that never pushed): rev-list against a missing @{u} fails, treated as
    "nothing to report" rather than an error, since a repo need not have
    an upstream to be a valid, unambiguous local state.
    """
    ahead_r = run_git(repo_dir, ["rev-list", "--count", "@{u}..HEAD"])
    behind_r = run_git(repo_dir, ["rev-list", "--count", "HEAD..@{u}"])
    if ahead_r.returncode != 0 or behind_r.returncode != 0:
        return 0, 0
    return int(ahead_r.stdout.strip()), int(behind_r.stdout.strip())


def _incoming_name(original_name: str) -> str:
    """<name>.incoming.<ext> when original_name has an extension, else
    <name>.incoming. The one place this naming rule is decided."""
    stem = original_name.rsplit(".", 1)
    return f"{stem[0]}.incoming.{stem[1]}" if len(stem) == 2 else f"{original_name}.incoming"


def _original_name_from_incoming(incoming_name: str) -> Optional[str]:
    """Reverse of _incoming_name, or None if incoming_name does not match
    the pattern at all."""
    if incoming_name.endswith(".incoming"):
        return incoming_name[: -len(".incoming")]
    marker = ".incoming."
    idx = incoming_name.rfind(marker)
    if idx == -1:
        return None
    return incoming_name[:idx] + "." + incoming_name[idx + len(marker):]


def find_incoming_files(repo_dir: pathlib.Path) -> List[str]:
    """POSIX-style relative paths (from repo_dir) of every original file
    that has a live "<name>.incoming[.<ext>]" sibling, skipping .git\\."""
    conflicts = []
    for path in repo_dir.rglob("*.incoming*"):
        if ".git" in path.relative_to(repo_dir).parts:
            continue
        original_name = _original_name_from_incoming(path.name)
        if original_name is None:
            continue
        original = path.with_name(original_name)
        conflicts.append(original.relative_to(repo_dir).as_posix())
    return sorted(conflicts)


def get_status(repo_dir: pathlib.Path, node_label: str, clone_label: str) -> dict:
    written_at = _now_iso()
    try:
        ahead, behind = ahead_behind(repo_dir)
        conflicts = find_incoming_files(repo_dir)
        if conflicts:
            state = "conflict"
            message = f"{len(conflicts)} unresolved conflict copy(ies); resolve the .incoming.md files."
        elif behind > 0:
            state = "behind"
            message = f"{behind} commit(s) behind the remote; pull to catch up."
        else:
            state = "ok"
            message = "Up to date."
    except Exception as exc:  # never let a status check crash the caller
        ahead, behind, conflicts = 0, 0, []
        state = "error"
        message = f"git status check failed: {exc}"
    last_pull, last_push = _read_state(repo_dir)
    return {
        "node": node_label, "clone": clone_label,
        "lastPull": last_pull, "lastPush": last_push,
        "behind": behind, "ahead": ahead, "conflicts": conflicts,
        "state": state, "message": message, "writtenAt": written_at,
    }


def _current_branch(repo_dir: pathlib.Path) -> str:
    r = run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip()


def _porcelain_records(
    repo_dir: pathlib.Path, extra_args: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """(code, path) pairs from `git status --porcelain -z`. -z leaves
    paths unquoted and un-octal-escaped (relevant for non-ASCII filenames,
    e.g. a Dutch-language estate, unless core.quotePath=false) and
    NUL-terminates records instead of newline-terminating them, so a
    rename record never contains the plain-format's " -> " arrow text
    embedded in a single line: under -z a rename/copy record (code
    starting with "R") is two consecutive NUL-separated fields, the new
    path first and the old path second. The changed path returned for such
    a record is the new path; the old path is consumed and dropped.
    """
    args = ["status", "--porcelain", "-z"]
    if extra_args:
        args.extend(extra_args)
    output = run_git(repo_dir, args).stdout
    tokens = output.split("\0")
    records = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "":
            i += 1
            continue
        code = token[:2]
        path = token[3:]
        if code[0] == "R":
            i += 2  # this record's old-path field follows; skip it
        else:
            i += 1
        records.append((code, path))
    return records


def _conflicted_paths(repo_dir: pathlib.Path) -> List[str]:
    """Paths git reports as unmerged: any porcelain code containing "U"
    (UU both modified, AU/UA add/add-with-rename, UD/DU modify/delete)
    plus AA and DD (both added, both deleted; neither carries a "U" but
    both are still real conflicts requiring operator attention)."""
    return [
        path for code, path in _porcelain_records(repo_dir)
        if "U" in code or code in ("AA", "DD")
    ]


def _status_with_op(
    repo_dir: pathlib.Path, node_label: str, clone_label: str,
    op_ok: bool, op_message: str, error_state: str = "error",
) -> dict:
    """get_status()'s own dict, plus opOk/opMessage naming whether THIS
    push/pull call itself actually succeeded (T10, R02): get_status()
    alone cannot tell a caller that, e.g. a repo with no remote configured
    at all still reports state "ok" ("0 ahead, 0 behind", the same shape a
    genuinely up-to-date repo has). A failed op overrides state/message
    too, so a client reading only the old fields still sees the truth,
    not a stale "Up to date."."""
    status = get_status(repo_dir, node_label, clone_label)
    status["opOk"] = op_ok
    status["opMessage"] = op_message
    if not op_ok:
        status["state"] = error_state
        status["message"] = op_message
    return status


def pull(repo_dir: pathlib.Path, node_label: str, clone_label: str) -> dict:
    """Never leaves the repo in an in-progress merge. A conflicting path
    gets its incoming (remote) version written to <name>.incoming.md next
    to the local file; the local file and git state are left exactly as
    they were before this call otherwise.
    """
    if not has_remote(repo_dir):
        return _status_with_op(repo_dir, node_label, clone_label, False, "no remote configured")

    fetch = run_git(repo_dir, ["fetch", "origin"], timeout=GIT_NETWORK_TIMEOUT_SECONDS)
    if fetch.returncode == TIMEOUT_RETURNCODE:
        return _status_with_op(repo_dir, node_label, clone_label, False, fetch.stderr, error_state="timeout")
    if fetch.returncode != 0:
        return _status_with_op(
            repo_dir, node_label, clone_label, False, f"fetch failed: {fetch.stderr.strip()}",
        )

    branch = _current_branch(repo_dir)
    merge = run_git(repo_dir, ["merge", "--no-commit", "--no-ff", f"origin/{branch}"])
    conflicted = _conflicted_paths(repo_dir)
    if merge.returncode != 0 or conflicted:
        # The write loop below must never leave a merge in progress, even
        # if a write raises (disk full, permission error, odd path): the
        # abort always runs, in the finally, so pull() never raises and
        # never leaves a live MERGE_HEAD/UU state behind. A redundant
        # abort on an already-clean state is a harmless no-op.
        write_error: Optional[Exception] = None
        try:
            for rel_path in conflicted:
                local_path = repo_dir / rel_path
                incoming = run_git(repo_dir, ["show", f"origin/{branch}:{rel_path}"])
                incoming_name = _incoming_name(local_path.name)
                # A non-zero returncode here means the path does not exist
                # on origin/<branch> at all (the remote side deleted it in
                # a delete/modify conflict): there is then no incoming
                # content to show, so an empty .incoming file is written
                # rather than silently skipping the write. Local state
                # (whatever this side has, deleted or modified) is never
                # touched either way.
                incoming_text = incoming.stdout if incoming.returncode == 0 else ""
                (local_path.parent / incoming_name).write_text(incoming_text, encoding="utf-8")
        except OSError as exc:
            write_error = exc
        finally:
            run_git(repo_dir, ["merge", "--abort"])
        if write_error is not None:
            last_pull, last_push = _read_state(repo_dir)
            return {
                "node": node_label, "clone": clone_label,
                "lastPull": last_pull, "lastPush": last_push,
                "behind": 0, "ahead": 0, "conflicts": [],
                "state": "error", "message": f"conflict write failed: {write_error}",
                "writtenAt": _now_iso(), "opOk": False, "opMessage": f"conflict write failed: {write_error}",
            }
        # Conflicts were found and written out as .incoming copies: a real,
        # visible outcome (state "conflict" from get_status), not a plain
        # failure, so opOk is true (the pull itself completed as designed).
        status = get_status(repo_dir, node_label, clone_label)
        status["opOk"] = True
        status["opMessage"] = "pulled; conflicts written as .incoming copies"
        return status

    # No conflicts: finish the merge (--no-commit left MERGE_HEAD staged).
    run_git(repo_dir, ["commit", "--no-edit"])
    _write_state(repo_dir, lastPull=_now_iso())
    return _status_with_op(repo_dir, node_label, clone_label, True, "pulled")


def push(repo_dir: pathlib.Path, node_label: str, clone_label: str) -> dict:
    if not has_remote(repo_dir):
        return _status_with_op(repo_dir, node_label, clone_label, False, "no remote configured")

    branch = _current_branch(repo_dir)
    result = run_git(repo_dir, ["push", "origin", branch], timeout=GIT_NETWORK_TIMEOUT_SECONDS)
    if result.returncode == TIMEOUT_RETURNCODE:
        return _status_with_op(repo_dir, node_label, clone_label, False, result.stderr, error_state="timeout")
    if result.returncode != 0:
        # A rejected push (non-fast-forward, expired/revoked credential,
        # branch protection) must never be reported as if it had gone
        # through (R02/R03): get_status()'s own ahead/behind still reflect
        # reality (this commit is still only local), but state/message are
        # overridden too so a caller reading only those sees the failure.
        return _status_with_op(
            repo_dir, node_label, clone_label, False, f"push rejected: {result.stderr.strip()}",
        )
    _write_state(repo_dir, lastPush=_now_iso())
    return _status_with_op(repo_dir, node_label, clone_label, True, "pushed")


def layout_shape_error(layout) -> Optional[str]:
    """None if layout has the shape board\\render_board.py's own
    settings.layout schema requires; an error string otherwise. Re-checked
    here rather than imported (sync\\ never imports board\\, separate trust
    domains, same posture as owner_check.py's own docstring); deliberately
    looser than render_board.py's read-side validation, no PANEL_KEYS check,
    since an unrecognised id still round-trips safely: render_board.py's own
    validation falls back to its generated default if a written id turns
    out to be unknown.
    """
    if not isinstance(layout, dict):
        return "layout must be an object"
    columns = layout.get("columns")
    if not isinstance(columns, int) or isinstance(columns, bool) or columns not in (1, 2, 3, 4):
        return "layout.columns must be an integer 1-4"
    panels = layout.get("panels")
    if not isinstance(panels, list):
        return "layout.panels must be an array"
    for entry in panels:
        if not isinstance(entry, dict):
            return "each layout.panels entry must be an object"
        if not all(k in entry for k in ("id", "col", "row", "w", "h")):
            return "each layout.panels entry needs id, col, row, w, h"
        if not isinstance(entry["id"], str) or not entry["id"]:
            return "layout.panels[].id must be a non-empty string"
        for key in ("col", "w"):
            value = entry[key]
            if not isinstance(value, int) or isinstance(value, bool) or value not in (1, 2, 3, 4):
                return f"layout.panels[].{key} must be an integer 1-4"
        for key in ("row", "h"):
            value = entry[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                return f"layout.panels[].{key} must be an integer >= 1"
    return None


def write_layout(repo_dir: pathlib.Path, layout: dict) -> dict:
    """Rewrite settings.layout in repo_dir's own siteoffice.json, in place,
    every other key untouched (drag-and-drop writeback, plan item 1.10;
    layout is a whole-object override, never merged panel-by-panel, same
    posture render_board.py's load_manifest() already reads it with).
    {"ok": True} or {"ok": False, "message": <str>}; never raises."""
    manifest_path = repo_dir / "siteoffice.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"could not read siteoffice.json: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "message": "siteoffice.json is not a JSON object"}
    settings = data.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        data["settings"] = settings
    settings["layout"] = layout
    # T10 (R11, "restart during a pending write"): write to a sibling temp
    # file first, then atomically replace the real manifest. A crash or
    # kill between these two steps leaves either the OLD manifest intact
    # (temp file never renamed) or the NEW one fully written (os.replace is
    # a single filesystem rename, never a partial one); it can never leave
    # siteoffice.json half-written or truncated.
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp_path, manifest_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "message": f"could not write siteoffice.json: {exc}"}
    return {"ok": True, "message": ""}


def _write_receipt(
    repo_dir: pathlib.Path, sha: str, message: str, node_label: str, clone_label: str,
) -> None:
    # Best-effort, same posture as _write_state: losing a receipt is not
    # worth failing an otherwise-successful commit over.
    try:
        log_dir = repo_dir / "_log"
        log_dir.mkdir(parents=True, exist_ok=True)
        committed_at = _now_iso()
        record = {
            "sha": sha, "message": message,
            "node": node_label, "clone": clone_label,
            "committedAt": committed_at,
        }
        ts = committed_at.replace(":", "-")
        (log_dir / f"sync-receipt-{ts}.json").write_text(
            json.dumps(record), encoding="utf-8",
        )
    except OSError:
        pass


def list_changed_paths(repo_dir: pathlib.Path, split_renames: bool = False) -> List[str]:
    # --untracked-files=all so a wholly-new directory is listed one file
    # per line, not collapsed to a single "?? dir/" line, which would
    # otherwise be checked against the owners map at the wrong granularity.
    # split_renames (--no-renames) lists a rename as its old path (deleted) and
    # its new path (added), so a caller that vets or commits paths sees both. The
    # default keeps the historic view: a rename is its new path only.
    args = ["--untracked-files=all"] + (["--no-renames"] if split_renames else [])
    return [path for _code, path in _porcelain_records(repo_dir, args)]


def commit(
    repo_dir: pathlib.Path,
    message: str,
    this_owner: str,
    resolve_owner: Callable[[pathlib.Path, str], Optional[str]],
    node_label: str = "",
    clone_label: str = "",
    only_paths: Optional[List[str]] = None,
) -> dict:
    if not message or not message.strip():
        status = get_status(repo_dir, node_label, clone_label)
        status["committed"] = []
        status["refused"] = []
        status["state"] = "error"
        status["message"] = "commit message is required"
        return status

    # only_paths is git_flow.py's mode: it commits exactly the paths it has
    # vetted (owner check, denylist, secrets scan) and leaves anything else that
    # changed alone. The three extras below (split renames, literal pathspecs,
    # skipping `git add` for a vanished path) belong to that mode only. With
    # only_paths=None this function behaves exactly as it did before git_flow.
    flow_mode = only_paths is not None
    changed = list_changed_paths(repo_dir, split_renames=flow_mode)
    if flow_mode:
        wanted = set(only_paths)
        changed = [path for path in changed if path in wanted]
    allowed, refused = [], []
    for path in changed:
        owner = resolve_owner(repo_dir, path)
        if owner and owner != this_owner:
            refused.append({
                "path": path,
                "message": owner_check.refusal_message(path, owner, this_owner),
            })
        else:
            allowed.append(path)

    if allowed:
        if flow_mode:
            # A path gone from the working tree needs no `git add`: `commit
            # --only` records its deletion itself, and `git add` refuses a
            # deletion that is already staged (the old side of `git mv`).
            to_add = [path for path in allowed if os.path.lexists(repo_dir / path)]
            add_result = run_git(repo_dir, ["--literal-pathspecs", "add", "--", *to_add]) if to_add else None
        else:
            add_result = run_git(repo_dir, ["add", "--", *allowed])
        if add_result is not None and add_result.returncode != 0:
            status = get_status(repo_dir, node_label, clone_label)
            status["committed"] = []
            status["refused"] = refused
            status["state"] = "error"
            status["message"] = f"git add failed: {add_result.stderr.strip()}"
            return status

        # --only commits exactly the given paths' working-tree content,
        # disregarding anything else that happens to be staged in the
        # index (a path pre-staged by the user, or left over from a prior
        # failed call): otherwise a bare `git commit` would commit the
        # entire index, silently including a refused path the response
        # reports as refused.
        commit_prefix = ["--literal-pathspecs"] if flow_mode else []
        commit_result = run_git(repo_dir, [*commit_prefix, "commit", "--only", "-m", message, "--", *allowed])
        if commit_result.returncode != 0:
            status = get_status(repo_dir, node_label, clone_label)
            status["committed"] = []
            status["refused"] = refused
            status["state"] = "error"
            status["message"] = f"git commit failed: {commit_result.stderr.strip()}"
            return status

        sha_result = run_git(repo_dir, ["rev-parse", "--short", "HEAD"])
        sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

    status = get_status(repo_dir, node_label, clone_label)
    status["committed"] = allowed
    status["refused"] = refused
    if refused:
        status["state"] = "refused"
        status["message"] = f"{len(refused)} path(s) refused; see 'refused' for detail."
    elif allowed:
        status["sha"] = sha
        _write_receipt(repo_dir, sha, message, node_label, clone_label)
    return status
