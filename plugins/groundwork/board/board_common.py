"""Shared helpers for the panel collectors, templates and board kinds: HTML
escaping, a staleness check against a manifest's settings.refreshDays, ndjson
reading and JSON-safe serialisation. Stdlib only.

read_ndjson and json_safe moved here from render_board.py (decision 13,
2026-09-12 resume guide, step 10b): both board\\kinds\\flow.py and
board\\kinds\\sprint.py need them and must not import render_board itself
(no import cycle: render_board imports the kinds, never the reverse).
"""
from __future__ import annotations

import html as _html
import importlib.util
import json
import pathlib
from datetime import date, datetime, timezone


def esc(x) -> str:
    return _html.escape(str(x), quote=True)


def load_module_from_path(name: str, path: pathlib.Path):
    """Load a module from an explicit file path under a private internal
    name, bypassing the sys.modules bare-name cache. Needed because
    board\\hours.py (the ndjson reader) and board\\templates\\hours.py (the
    panel) are both, by design, literally named hours.py: a plain `import
    hours` from either side would silently return whichever of the two some
    earlier import already cached under sys.modules['hours']."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flow_gate_is_open(flow: dict) -> bool:
    """The one shared pending-gate predicate (T09, R05): a flow is waiting on
    a human only while its GATE's own status is "awaiting-owner". The gate is
    authoritative when one exists (flow_transition.cmd_gate leaves
    flow["status"] at "awaiting-owner" too, but only the gate itself is
    cleared back by an actual decision); a flow with no gate object falls
    back to its own top-level status. Used by both board\\kinds\\flow.py and
    board\\templates\\waiting_on_you.py so a decided gate disappears from
    every count and row alike, not just some of them."""
    gate = flow.get("gate")
    if isinstance(gate, dict):
        return gate.get("status") == "awaiting-owner"
    return flow.get("status") == "awaiting-owner"


def is_stale(path: pathlib.Path, refresh_days: int) -> bool:
    """True when path's mtime is older than refresh_days ago. False (never
    stale) when the path cannot be stat'd; the caller already knows to
    treat a missing source as unavailable, not stale."""
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    age_days = (datetime.now(timezone.utc) - mtime).total_seconds() / 86400
    return age_days > refresh_days


def read_ndjson(path: pathlib.Path):
    """Every well-formed JSON line in path; a malformed line is skipped, not
    fatal. Empty list when the file does not exist."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def json_safe(obj):
    """Recursively convert an object to something json.dumps can serialise:
    only extra case beyond dict/list/tuple is a date/datetime, ISO-formatted."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
