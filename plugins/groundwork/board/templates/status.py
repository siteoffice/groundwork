"""Status panel: overdue work, failed/blocked flows and the last time this
data actually refreshed. Was a permanently empty shell (collect_all_panels
hardcoded ("empty", {})); T09/R13/N07 gives it a real collector. Reuses the
same declared "flows" root waiting_on_you.py and receipts.py already read.
A missing or unreadable DECLARED source is unavailable, never rendered as
healthy; no declaration at all is the ordinary empty case, same distinction
the other two panels already make (T09: "missing refresh never looks
healthy").
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, load_module_from_path  # noqa: E402

# next_weeks.py is also imported as a top-level module named "next_weeks"
# elsewhere; loaded here under a private name for the same reason
# board\board_common.load_module_from_path exists (avoid a sys.modules
# collision), reusing its own deadline-row parsing rather than a duplicate.
_next_weeks = load_module_from_path("_siteoffice_status_next_weeks", HERE / "next_weeks.py")

STATE_UNAVAILABLE = "unavailable"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"


def _flows_root(manifest: dict):
    """Same declaration waiting_on_you.py and receipts.py already read."""
    declared = manifest.get("flows")
    if not declared:
        return None
    p = pathlib.Path(declared)
    if not p.is_absolute():
        p = (manifest["_dir"] / declared).resolve()
    return p


def _overdue_rows(manifest: dict):
    """Rows next_weeks.py already parses from DEADLINES.md/roadmap.md but
    excludes (its own horizon window starts at today); overdue is the
    complement, date strictly before today. Reuses its private row reader
    rather than re-parsing the same files a second way."""
    node_dir = manifest["_dir"]
    deadlines_path = node_dir / "DEADLINES.md"
    if not deadlines_path.is_file():
        return []
    try:
        rows = _next_weeks._deadline_rows(deadlines_path)
    except OSError:
        return []
    roadmap_path = node_dir / "02_roadmap" / "roadmap.md"
    if roadmap_path.is_file():
        try:
            priorities, _ = _next_weeks.dateparse.extract_roadmap(
                roadmap_path.read_text(encoding="utf-8")
            )
        except OSError:
            priorities = []
        for p in priorities:
            if p["date"]:
                rows.append({"date": p["date"], "item": p["text"]})
    today = datetime.now(timezone.utc).date()
    return sorted((r for r in rows if r["date"] < today), key=lambda r: r["date"])


def _failed_flows(root: pathlib.Path):
    """Flows whose own status is "blocked": a real, already-used status
    value (flow_transition raises on it) distinct from "awaiting-owner"
    (waiting on a human decision) and "closed" (done)."""
    failed = []
    last_refresh = None
    for flow_path in sorted(root.glob("*/*/flow.json")):
        try:
            flow = json.loads(flow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        updated_at = flow.get("updated_at")
        if isinstance(updated_at, str) and (last_refresh is None or updated_at > last_refresh):
            last_refresh = updated_at
        if flow.get("status") == "blocked":
            failed.append(f'{flow.get("project", "")}/{flow.get("slug", "")}')
    return failed, last_refresh


def collect(manifest: dict):
    overdue = _overdue_rows(manifest)
    root = _flows_root(manifest)

    if root is not None and not root.is_dir():
        # A declared source that cannot be read must not silently look
        # healthy just because it also happens to have no overdue rows.
        return STATE_UNAVAILABLE, {"overdue": overdue, "failed": [], "last_refresh": None}

    failed, last_refresh = ([], None) if root is None else _failed_flows(root)

    data = {"overdue": overdue, "failed": failed, "last_refresh": last_refresh}
    if not overdue and not failed:
        return STATE_EMPTY, data
    return STATE_NORMAL, data


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("status", "status")

    if state == STATE_UNAVAILABLE:
        body = (
            f'<p class="empty warn"><span class="msg" data-i18n="state_unavailable">'
            f'{esc(labels.get("state_unavailable", "state_unavailable"))}</span></p>'
        )
    elif state == STATE_EMPTY:
        body = (
            f'<p class="empty"><span class="msg" data-i18n="state_empty">'
            f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
        )
    else:
        items = []
        for r in data.get("overdue", []):
            d = r["date"]
            d_text = d.isoformat() if isinstance(d, date) else str(d)
            items.append(f'<li class="warn">overdue: {esc(d_text)} {esc(r["item"])}</li>')
        for slug in data.get("failed", []):
            items.append(f'<li class="warn">failed: {esc(slug)}</li>')
        body = f'<ul class="rows">{"".join(items)}</ul>'

    refresh = data.get("last_refresh")
    refresh_text = esc(refresh) if refresh else esc(labels.get("state_unavailable", "unknown"))
    footer = f'<p class="note">last refresh: {refresh_text}</p>'

    return (
        f'<section class="panel" data-panel="status">'
        f'<div class="head"><h2 data-i18n="status">{esc(title)}</h2></div>'
        f'<div class="body">{body}{footer}</div>'
        f"</section>"
    )
