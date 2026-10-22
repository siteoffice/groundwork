"""Children strip: one card per child manifest, its own _board\\board.json
read when present (status colour, freshness). Collector reads; render only
formats what collect() found.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, is_stale  # noqa: E402

STATE_UNAVAILABLE = "unavailable"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"


def collect(manifest: dict):
    node_dir = manifest["_dir"]
    children = manifest.get("children") or []
    refresh_days = manifest["settings"]["refreshDays"]

    if not children:
        return STATE_EMPTY, {"cards": [], "missing": []}

    cards = []
    missing = []
    any_stale = False
    for child in children:
        child_path = pathlib.Path(child)
        child_dir = child_path if child_path.is_absolute() else node_dir / child
        if not child_dir.is_dir():
            missing.append(child)
            continue
        board_json_path = child_dir / "_board" / "board.json"
        card = {"name": child, "displayName": child}
        if board_json_path.is_file():
            try:
                board_data = json.loads(board_json_path.read_text(encoding="utf-8"))
                card["displayName"] = board_data.get("displayName", child)
            except (OSError, ValueError):
                pass
            if is_stale(board_json_path, refresh_days):
                any_stale = True
        cards.append(card)

    data = {"cards": cards, "missing": missing}
    if missing:
        return STATE_UNAVAILABLE, data
    if any_stale:
        return STATE_STALE, data
    return STATE_NORMAL, data


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("children", "children")

    if state == STATE_EMPTY:
        body = (
            f'<p class="empty"><span class="msg" data-i18n="state_empty">'
            f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
        )
    else:
        cards = data.get("cards") or []
        chip_html = "".join(
            f'<span class="chip">{esc(c["displayName"])}</span>' for c in cards
        )
        note_html = ""
        if state == STATE_UNAVAILABLE:
            note_html = (
                f'<p class="note warn" data-i18n="state_unavailable">'
                f'{esc(labels.get("state_unavailable", "state_unavailable"))}</p>'
            )
        elif state == STATE_STALE:
            note_html = (
                f'<p class="note warn" data-i18n="state_stale">'
                f'{esc(labels.get("state_stale", "state_stale"))}</p>'
            )
        body = f'<div class="children-cards">{chip_html}</div>{note_html}'

    return (
        f'<section class="panel strip" data-panel="children">'
        f'<div class="head"><h2 data-i18n="children">{esc(title)}</h2></div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
