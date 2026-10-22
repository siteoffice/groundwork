"""Activity strip: newest entries from _log\\, newest first, capped at 20.
Collector reads; render only formats what collect() found.

Ordering uses each file's own real time, not raw filesystem mtime: `git
checkout` (as `board\\render_board.py`'s `cmd_as_of` uses for a historical
render) resets every checked-out file's own mtime to checkout time, so
mtime alone cannot recover the true order of files whose real history
predates the checkout. Many `_log\\` filenames already embed their own
real, authoritative timestamp (for example
`sync-receipt-2026-09-13T19-40-46Z.json`, dashes standing in for the
colons a filename cannot contain); `_effective_time` prefers that when
present, falling back to mtime only for a name that carries no such
timestamp (an unrelated convention, or an append-only log file modified
in place rather than recreated). Staleness (`is_stale`, board_common)
still reads raw mtime, unchanged and out of this fix's own scope: for a
live, non-checked-out render mtime is the file's own real modification
time and already correct there.
"""
from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, is_stale  # noqa: E402

STATE_UNAVAILABLE = "unavailable"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

_CAP = 20

# An ISO-8601 UTC instant with dashes in place of the colons a filename
# cannot contain, e.g. "2026-09-13T19-40-46Z" inside
# "sync-receipt-2026-09-13T19-40-46Z.json".
_TIMESTAMP_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z")


def _effective_time(p: pathlib.Path) -> float:
    """POSIX epoch seconds: parsed from a timestamp embedded in the
    file's own name when present (authoritative, survives a git
    checkout), otherwise the file's own mtime."""
    match = _TIMESTAMP_IN_NAME_RE.search(p.name)
    if match:
        date_part, hh, mm, ss = match.groups()
        try:
            return datetime.fromisoformat(f"{date_part}T{hh}:{mm}:{ss}+00:00").timestamp()
        except ValueError:
            pass  # an unparsable near-match falls back to mtime below, never raises
    return p.stat().st_mtime


def collect(manifest: dict):
    node_dir = manifest["_dir"]
    refresh_days = manifest["settings"]["refreshDays"]
    log_dir = node_dir / "_log"

    if not log_dir.is_dir():
        return STATE_UNAVAILABLE, {"entries": []}

    entries = sorted(
        (p for p in log_dir.iterdir() if p.is_file()),
        key=_effective_time,
        reverse=True,
    )
    if not entries:
        return STATE_EMPTY, {"entries": []}

    stale = is_stale(entries[0], refresh_days)
    capped = [{"name": p.name} for p in entries[:_CAP]]

    if stale:
        return STATE_STALE, {"entries": capped}
    return STATE_NORMAL, {"entries": capped}


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("activity", "activity")
    count_html = ""

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
        entries = data.get("entries") or []
        count_html = f'<span class="meta">{len(entries)}</span>'
        items = "".join(f"<li>{esc(e['name'])}</li>" for e in entries)
        note_html = ""
        if state == STATE_STALE:
            note_html = (
                f'<p class="note warn" data-i18n="state_stale">'
                f'{esc(labels.get("state_stale", "state_stale"))}</p>'
            )
        # Fixed height, scrolling body: item count is unbounded (up to the
        # _CAP), the panel's grid footprint must not grow with it.
        body = f'<div class="panel-scroll"><ul class="rows">{items}</ul></div>{note_html}'

    return (
        f'<section class="panel strip" data-panel="activity">'
        f'<div class="head"><h2 data-i18n="activity">{esc(title)}</h2>{count_html}</div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
