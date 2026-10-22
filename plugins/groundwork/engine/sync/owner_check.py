"""The refuse-rule resolution: which owner-id, if any, claims a changed
path, by longest-prefix match against the owners map inherited exactly
like board\\render_board.py's load_manifest() inherits `owners` (root
merged with a node's own map, node's own key wins). Deliberately does not
import board\\render_board.py (sync\\ never imports board\\; the two
folders are separate trust domains under AGENTS.md's amended network rule)
so this re-implements the same small parent-walk algorithm against raw
siteoffice.json files directly. Stdlib only, no pip installs, no network
calls.
"""
from __future__ import annotations

import json
import pathlib
from typing import Dict, Optional

_MAX_ANCESTOR_WALK = 8


def _read_raw_owners(node_dir: pathlib.Path) -> dict:
    """This node's own manifest dict, or {} if none/unreadable. Only the
    keys owner_check needs ('parent', 'owners') are relied on; anything
    else is ignored."""
    path = node_dir / "siteoffice.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_root_dir(node_dir: pathlib.Path, repo_root: pathlib.Path) -> pathlib.Path:
    """Mirror of render_board._find_root_raw's ancestor walk, capped the
    same way, but stops at repo_root (never walks above the repo this
    helper is scoped to)."""
    current_dir = node_dir
    current_raw = _read_raw_owners(current_dir)
    steps = 0
    while (current_raw.get("parent") or "") and steps < _MAX_ANCESTOR_WALK and current_dir != repo_root:
        probe = current_dir.parent
        found_dir = None
        for _ in range(_MAX_ANCESTOR_WALK):
            if probe == probe.parent:
                break
            if (probe / "siteoffice.json").exists():
                found_dir = probe
                break
            if probe == repo_root:
                break
            probe = probe.parent
        if found_dir is None:
            break
        current_dir = found_dir
        current_raw = _read_raw_owners(current_dir)
        steps += 1
    return current_dir


def merged_owners(node_dir: pathlib.Path, repo_root: pathlib.Path) -> Dict[str, str]:
    """root's owners map merged with node_dir's own (own wins per key),
    exactly like board\\render_board.py:load_manifest()'s owners block.

    Deliberate, ruled behavior, not a bug: root's owners keys are relative
    to the ROOT manifest's own directory, and are inherited into every
    descendant unchanged (same model as labels), never reinterpreted as
    relative to whichever child manifest they end up merged into. So a
    root key like "docs" also matches a same-named "docs" subfolder inside
    a child node's own directory, not only the root's own "docs" folder.
    A per-manifest-relative reading (each node's keys scoped only to that
    node's own directory) was considered and explicitly rejected in favor
    of keeping this simpler, labels-like model. Pinned by
    sync\\tests\\test_owner_check.py's cross-directory case.
    """
    own_raw = _read_raw_owners(node_dir)
    root_dir = find_root_dir(node_dir, repo_root)
    owners: Dict[str, str] = {}
    if root_dir != node_dir:
        root_raw = _read_raw_owners(root_dir)
        owners.update(root_raw.get("owners") or {})
    owners.update(own_raw.get("owners") or {})
    return owners


def nearest_manifest_dir(start_dir: pathlib.Path, repo_root: pathlib.Path) -> pathlib.Path:
    """First directory at or above start_dir (down to repo_root) carrying
    a siteoffice.json; repo_root itself if none is found above it."""
    probe = start_dir
    for _ in range(_MAX_ANCESTOR_WALK):
        if (probe / "siteoffice.json").exists():
            return probe
        if probe == repo_root or probe == probe.parent:
            break
        probe = probe.parent
    return repo_root


def owner_for_path(repo_root: pathlib.Path, changed_path: str) -> Optional[str]:
    """changed_path: POSIX-style, relative to repo_root (as `git status`
    reports it). Returns the owning id, or None if no owners entry's
    prefix matches (the "never refuse" default)."""
    abs_path = repo_root / pathlib.PurePosixPath(changed_path)
    manifest_dir = nearest_manifest_dir(abs_path.parent, repo_root)
    owners = merged_owners(manifest_dir, repo_root)
    rel = pathlib.PurePosixPath(abs_path.relative_to(manifest_dir).as_posix())
    rel_str = str(rel)

    best_key, best_len = None, -1
    for key in owners:
        matches = rel_str == key or rel_str.startswith(key.rstrip("/") + "/")
        if matches and len(key) > best_len:
            best_key, best_len = key, len(key)
    return owners.get(best_key) if best_key is not None else None


def refusal_message(path: str, owner: str, this_owner: str) -> str:
    return f"`{path}` is owned by {owner}, not {this_owner}; use the funnel or a request file instead"
