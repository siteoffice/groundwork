"""Funnel kanban panel: 00_funnel\\ stage folders plus a dual-read of loose
files sitting directly in the holding's inbox (PoC plan 10.1). One column
per numbered stage folder (01_inbox, 02_shaped, ... per sync note section
7), a leading virtual "inbox" column for loose files when any exist. No
interaction (architecture note 6): the kanban is a rendered view, moves
happen through skills. Collector reads; render only formats what collect()
found. waiting_on_you.py imports collect_columns/find_funnel_dir from here
so the two panels never parse 00_funnel differently.
"""
from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, is_stale  # noqa: E402

STATE_UNAVAILABLE = "unavailable"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

# 00_funnel sits at the true holding root (the workspace root C:\work in the
# real estate), which is not always the node being rendered (the hub node
# C:\work\10_holding has no 00_funnel of its own). Walk up the physical tree
# to find it, same bound as the manifest ancestor walk in render_board.py.
_MAX_ANCESTOR_WALK = 8

_STAGE_DIR_RE = re.compile(r"^(\d+)_(.+)$")
_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)\.md$")
_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def find_funnel_dir(node_dir: pathlib.Path):
    probe = node_dir
    for _ in range(_MAX_ANCESTOR_WALK):
        candidate = probe / "00_funnel"
        if candidate.is_dir():
            return candidate
        if probe == probe.parent:
            break
        probe = probe.parent
    return None


def _card_from_file(path: pathlib.Path, gate):
    m = _FILENAME_RE.match(path.name)
    if m:
        card_date, author = m.group(1), m.group(2)
    else:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            card_date = mtime.date().isoformat()
        except OSError:
            card_date = ""
        author = "unknown"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    heading = _HEADING_RE.search(text)
    title = heading.group(1).strip() if heading else path.stem

    request = ""
    owner = ""
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("request:"):
            request = s.split(":", 1)[1].strip()
        elif low.startswith("owner:"):
            owner = s.split(":", 1)[1].strip()

    return {
        "file": path.name,
        "title": title,
        "author": author,
        "date": card_date,
        "request": request,
        "owner": owner or author,
        "gate": gate,
    }


def collect_columns(funnel_dir: pathlib.Path):
    """[(stage_name, label_key, [card, ...]), ...]. A card's "gate" is the
    gate between its stage and the next (G1..G{n-1} for n stages); the last
    stage's cards carry no gate, they are already through kickoff. Loose
    files render as a leading "inbox" column, gated on G1 when stage folders
    exist (they are waiting to be shaped into stage 1), gate-less when the
    holding has no stage folders at all yet."""
    stage_dirs = sorted(
        (p for p in funnel_dir.iterdir() if p.is_dir() and _STAGE_DIR_RE.match(p.name)),
        key=lambda p: int(_STAGE_DIR_RE.match(p.name).group(1)),
    )
    loose_files = sorted(
        p for p in funnel_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".md" and p.name.lower() != "readme.md"
    )

    columns = []
    if loose_files:
        gate = "G1" if stage_dirs else None
        cards = [_card_from_file(p, gate) for p in loose_files]
        columns.append(("inbox", "inbox", cards))

    stage_count = len(stage_dirs)
    for i, stage_dir in enumerate(stage_dirs):
        suffix = _STAGE_DIR_RE.match(stage_dir.name).group(2)
        gate = f"G{i + 1}" if i + 1 < stage_count else None
        cards = [_card_from_file(p, gate) for p in sorted(stage_dir.glob("*.md"))]
        columns.append((stage_dir.name, suffix, cards))
    return columns


def collect(manifest: dict):
    node_dir = manifest["_dir"]
    refresh_days = manifest["settings"]["refreshDays"]
    funnel_dir = find_funnel_dir(node_dir)

    if funnel_dir is None:
        return STATE_UNAVAILABLE, {"columns": [], "columnLimit": None}

    stale = is_stale(funnel_dir, refresh_days)
    columns = collect_columns(funnel_dir)
    column_limit = (manifest["settings"].get("funnel") or {}).get("columnLimit")

    data = {
        "columns": [
            {
                "stage": stage_name,
                "labelKey": label_key,
                "cards": cards,
                "overLimit": bool(column_limit) and len(cards) > column_limit,
            }
            for stage_name, label_key, cards in columns
        ],
        "columnLimit": column_limit,
    }

    if stale:
        return STATE_STALE, data
    if not columns:
        return STATE_EMPTY, data
    return STATE_NORMAL, data


def _render_card(card: dict) -> str:
    chips = []
    if card.get("gate"):
        chips.append(f'<span class="chip mono">{esc(card["gate"])}</span>')
    if card.get("request"):
        chips.append(f'<span class="chip">{esc(card["request"])}</span>')
    chips_html = "".join(chips)
    meta = f'{esc(card.get("owner", ""))} &middot; {esc(card.get("date", ""))}'
    return (
        f'<div class="funnel-card">'
        f'<p class="ct">{esc(card.get("title", ""))}</p>'
        f'<p class="cm">{chips_html}{meta}</p>'
        f"</div>"
    )


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("funnel", "funnel")

    if state == STATE_UNAVAILABLE:
        body = (
            f'<p class="empty warn"><span class="msg" data-i18n="state_unavailable">'
            f'{esc(labels.get("state_unavailable", "state_unavailable"))}</span></p>'
        )
    elif state == STATE_STALE:
        body = (
            f'<p class="empty warn"><span class="msg" data-i18n="state_stale">'
            f'{esc(labels.get("state_stale", "state_stale"))}</span></p>'
        )
    elif state == STATE_EMPTY:
        body = (
            f'<p class="empty"><span class="msg" data-i18n="state_empty">'
            f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
        )
    else:
        columns = data.get("columns") or []
        col_html = []
        for col in columns:
            stage_label = labels.get(col["labelKey"], col["labelKey"])
            count_chip = (
                f'<span class="chip dirty">{esc(len(col["cards"]))}</span>'
                if col.get("overLimit") else ""
            )
            cards_html = "".join(_render_card(c) for c in col["cards"])
            if not cards_html:
                cards_html = (
                    f'<p class="empty"><span class="msg" data-i18n="state_empty">'
                    f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
                )
            col_html.append(
                f'<div class="funnel-col">'
                f'<div class="funnel-col-head"><h3>{esc(stage_label)}</h3>{count_chip}</div>'
                f'<div class="funnel-col-cards">{cards_html}</div>'
                f"</div>"
            )
        body = f'<div class="funnel-board">{"".join(col_html)}</div>'

    return (
        f'<section class="panel" data-panel="funnel">'
        f'<div class="head"><h2 data-i18n="funnel">{esc(title)}</h2></div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
