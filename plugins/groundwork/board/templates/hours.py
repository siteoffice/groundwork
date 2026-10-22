"""Hours panel: per-node WBSO-grade time registration (architecture note
section 11, schema in board\\HOURS_SCHEMA.md). A node with
settings.timeRegistration false renders no panel at all, not an empty
state: render_board.py's caller skips a "disabled" state entirely, no
shell. Collector reads; render only formats what collect() found.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, is_stale, load_module_from_path  # noqa: E402

# Loaded by explicit path, not `import hours`: this file is itself named
# hours.py and gets imported elsewhere as a top-level module also called
# "hours" (render_board.py's tpl_hours); a plain import here would race
# with that on sys.modules['hours'].
hours = load_module_from_path("_siteoffice_hours_reader", BOARD_DIR / "hours.py")

STATE_DISABLED = "disabled"
STATE_UNAVAILABLE = "unavailable"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

# Urencriterium (VERIFY, see 04_assets\_grill_state.md): the bar the
# per-person progress line is measured against for the calendar year.
URENCRITERIUM_HOURS = 1225


def _resolve_child_dirs(manifest: dict):
    node_dir = manifest["_dir"]
    dirs = []
    for child in manifest.get("children") or []:
        p = pathlib.Path(child)
        child_dir = p if p.is_absolute() else node_dir / child
        if child_dir.is_dir():
            dirs.append(child_dir)
    return dirs


def collect(manifest: dict):
    settings = manifest["settings"]
    if not settings.get("timeRegistration"):
        return STATE_DISABLED, {}

    node_dir = manifest["_dir"]
    refresh_days = settings["refreshDays"]
    time_dir = node_dir / "03_logs" / "time"

    if time_dir.exists() and not time_dir.is_dir():
        return STATE_UNAVAILABLE, {"summary": None, "malformed": 0}

    today = datetime.now(timezone.utc).date()
    entries, malformed = hours.load_entries(node_dir, today.year)
    newest_source = hours._month_files(node_dir, today.year)
    newest_source = newest_source[-1] if newest_source else None

    # Holding and hub boards show the same figures summed over every child
    # that has hours of its own (guide step 8): read the child's own
    # 03_logs\time\ directly, never the child's manifest (no cross-module
    # dependency on render_board.load_manifest from inside a template).
    if manifest.get("type") in ("holding", "hub"):
        for child_dir in _resolve_child_dirs(manifest):
            child_entries, child_malformed = hours.load_entries(child_dir, today.year)
            entries = entries + child_entries
            malformed += child_malformed
            child_files = hours._month_files(child_dir, today.year)
            if child_files:
                if newest_source is None or (
                    child_files[-1].stat().st_mtime > newest_source.stat().st_mtime
                ):
                    newest_source = child_files[-1]

    summary = hours.summarize(entries, today)
    data = {"summary": summary, "malformed": malformed}

    if newest_source is not None and is_stale(newest_source, refresh_days):
        return STATE_STALE, data
    if not entries:
        return STATE_EMPTY, data
    return STATE_NORMAL, data


def _render_person_group(person: str, hrs: float, n: int, labels: dict) -> str:
    key = "entries_one" if n == 1 else "entries_other"
    count_text = (labels.get(key) or "{n} entries").replace("{n}", str(n))
    return (
        f'<div class="group" data-owner="{esc(person)}">'
        f'<div class="group-head"><h3>{esc(person)}</h3>'
        f'<span class="chip mono">{hrs:.1f} / {URENCRITERIUM_HOURS} &middot; {esc(count_text)}</span>'
        f"</div></div>"
    )


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("hours", "hours")

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
        summary = data["summary"]
        so_pct = round(summary["soShare"] * 100)
        rows = (
            f'<li>{esc(labels.get("hours", "hours"))}: '
            f'{summary["monthHours"]:.1f} / {summary["yearHours"]:.1f} '
            f'({esc(labels.get("so_hours", "so_hours"))} {so_pct}%)</li>'
        )
        per_person_count = summary.get("perPersonCount") or {}
        person_groups = "".join(
            _render_person_group(person, hrs, per_person_count.get(person, 0), labels)
            for person, hrs in sorted(summary["perPerson"].items())
        )
        person_html = (
            f'<p class="note">{esc(labels.get("hours_criterion", "hours_criterion"))}</p>'
            # Fixed height, scrolling body: person count is unbounded, the
            # panel's grid footprint must not grow with it.
            f'<div class="panel-scroll"><div class="groups">{person_groups}</div></div>'
        ) if person_groups else ""

        unlogged = summary["unloggedDays"]
        unlogged_html = ""
        if unlogged:
            n = len(unlogged)
            key = "unlogged_days_one" if n == 1 else "unlogged_days_other"
            text = (labels.get(key) or labels.get("unlogged_days", "unlogged_days")).replace(
                "{n}", str(n)
            )
            unlogged_html = f'<p class="note warn">{esc(text)}</p>'

        body = f'<ul class="rows">{rows}</ul>{person_html}{unlogged_html}'

    return (
        f'<section class="panel" data-panel="hours">'
        f'<div class="head"><h2 data-i18n="hours">{esc(title)}</h2></div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
