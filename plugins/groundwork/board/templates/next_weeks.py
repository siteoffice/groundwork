"""Next-N-weeks panel: dated rows from DEADLINES.md within the node's
planning horizon, plus dated roadmap priority rows that also fall in that
window. Collector reads; render only formats what collect() found
(architecture note 5.3, 9).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import dateparse  # noqa: E402
from board_common import esc, is_stale  # noqa: E402

STATE_UNAVAILABLE = "unavailable"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

_HUB_PARSER_DIR = pathlib.Path(r"C:\work\10_holding\04_engineering\command_center")


def _hub_parse(text: str):
    """Try the hub's NOW-block parser first: it filters resolved rows and
    strips markdown, so it is the better read when a DEADLINES.md matches
    its shape. Returns None on any mismatch or when the hub tree itself is
    not present (a customer repo will not have it), same contract as the
    hub module: never raises."""
    if not _HUB_PARSER_DIR.is_dir():
        return None
    if str(_HUB_PARSER_DIR) not in sys.path:
        sys.path.insert(0, str(_HUB_PARSER_DIR))
    try:
        import deadlines_parse  # noqa: E402
    except ImportError:
        return None
    try:
        return deadlines_parse.parse_now_deadlines(text)
    except Exception:
        return None


def _deadline_rows(deadlines_path: pathlib.Path):
    text = deadlines_path.read_text(encoding="utf-8")
    hub_rows = _hub_parse(text)
    if hub_rows is not None:
        out = []
        for r in hub_rows:
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                continue
            out.append({"date": d, "item": r.get("item", "")})
        return out
    return [
        {"date": r["date"], "item": r["item"]}
        for r in dateparse.extract_deadline_rows(text)
    ]


def collect(manifest: dict):
    node_dir = manifest["_dir"]
    settings = manifest["settings"]
    horizon_weeks = settings["planningHorizonWeeks"]
    refresh_days = settings["refreshDays"]
    deadlines_path = node_dir / "DEADLINES.md"

    if not deadlines_path.is_file():
        return STATE_UNAVAILABLE, {"rows": [], "horizonWeeks": horizon_weeks}

    stale = is_stale(deadlines_path, refresh_days)

    try:
        rows = _deadline_rows(deadlines_path)
    except OSError:
        return STATE_UNAVAILABLE, {"rows": [], "horizonWeeks": horizon_weeks}

    roadmap_path = node_dir / "02_roadmap" / "roadmap.md"
    if roadmap_path.is_file():
        try:
            priorities, _ = dateparse.extract_roadmap(
                roadmap_path.read_text(encoding="utf-8")
            )
        except OSError:
            priorities = []
        for p in priorities:
            if p["date"]:
                rows.append({"date": p["date"], "item": p["text"]})

    today = datetime.now(timezone.utc).date()
    horizon_end = today + timedelta(weeks=horizon_weeks)
    in_horizon = sorted(
        (r for r in rows if today <= r["date"] <= horizon_end),
        key=lambda r: r["date"],
    )

    data = {
        "rows": in_horizon,
        "horizonWeeks": horizon_weeks,
        "horizonNote": settings.get("_horizon_note"),
    }
    if stale:
        return STATE_STALE, data
    if not in_horizon:
        return STATE_EMPTY, data
    return STATE_NORMAL, data


def render(data: dict, labels: dict, state: str) -> str:
    n = data.get("horizonWeeks", 2)
    title_key = "next_weeks_one" if n == 1 else "next_weeks_other"
    title_tpl = labels.get(title_key) or labels.get("next_weeks", "next_weeks")
    title = title_tpl.replace("{n}", str(n))
    title_html = (
        f'<h2 data-i18n-template="{esc(title_key)}" data-n="{esc(n)}">{esc(title)}</h2>'
    )

    note = data.get("horizonNote")
    note_html = f'<p class="note warn">{esc(note)}</p>' if note else ""

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
        items = "".join(
            f'<li><span class="chip mono">{esc(r["date"].isoformat())}</span> '
            f'{esc(r["item"])}</li>'
            for r in data.get("rows", [])
        )
        body = f'<ul class="rows">{items}</ul>'

    return (
        f'<section class="panel" data-panel="next_weeks">'
        f'<div class="head">{title_html}</div>'
        f'<div class="body">{note_html}{body}</div>'
        f"</section>"
    )
