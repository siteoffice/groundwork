"""Roadmap panel: the numbered priority list and an optional collapsed
backlog from 02_roadmap\\roadmap.md (architecture note section 10). Plan
item 1.11 (GitHub Projects scan, architecture note section 15) adds three
patterns: date-range bars with a month/quarter/year zoom (gate dates as
vertical lines), group headers with sums on the backlog, and a slice-by
rail that filters the backlog by gate via data- attributes. A priority or
backlog line opts into a gate by ending with a `[gate:G1]`..`[gate:G4]` tag
(decision 2026-09-13); untagged text is never read as a gate marker.
"""
from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

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

# A priority or backlog item is read as a gate marker via a structured
# suffix tag, `[gate:G1]` .. `[gate:G4]` (the same G1..G4 vocabulary the
# funnel/waiting_on_you panels already use, decision 2026-09-13: formalized
# from plan item 1.11's own bare-substring heuristic, which false-positived
# on any incidental "G1"-shaped text). No separate gate-date source exists
# in roadmap.md today, so this tag is the one point of contact between free
# text and the two grouped/vertical-line patterns below.
_GATE_TAG_RE = re.compile(r"\s*\[gate:(G[1-4])\]\s*$", re.IGNORECASE)

# Grouping and the slice rail follow this order, not a numeric G1<G2<G3<G4
# sort: Foreman's own gate sequence is G1 (breakdown) -> G2 (grill) -> G4
# (ship approval) -> G3 (learn/close), G4 firing before G3 (deliberate,
# documented in docs\znd_os_flow_operator_guide.html section 06, G4 was
# added later and kept its number). Sorting numerically would show G3
# before G4 and read as the wrong order; this constant is the fix.
_GATE_ORDER = ("G1", "G2", "G4", "G3")

# (zoom key, window in days from today). The roadmap looks forward, so each
# variant's window starts today rather than centring on it.
_ZOOM_DAYS = (("month", 31), ("quarter", 92), ("year", 366))


def _extract_gate(text: str):
    """Split off a trailing `[gate:G1]`..`[gate:G4]` tag. Returns
    (display_text, gate_or_None); display_text has the tag stripped so it
    never leaks into rendered prose."""
    if not text:
        return text, None
    m = _GATE_TAG_RE.search(text)
    if not m:
        return text, None
    return text[: m.start()].rstrip(), m.group(1).upper()


def _gate_sort_key(gate: str) -> int:
    try:
        return _GATE_ORDER.index(gate)
    except ValueError:
        return len(_GATE_ORDER)


def collect(manifest: dict):
    node_dir = manifest["_dir"]
    refresh_days = manifest["settings"]["refreshDays"]
    path = node_dir / "02_roadmap" / "roadmap.md"

    if not path.is_file():
        return STATE_UNAVAILABLE, {"priorities": [], "backlog": []}

    stale = is_stale(path, refresh_days)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return STATE_UNAVAILABLE, {"priorities": [], "backlog": []}

    priorities, backlog = dateparse.extract_roadmap(text)

    data = {"priorities": priorities, "backlog": backlog}
    if stale:
        return STATE_STALE, data
    if not priorities:
        return STATE_EMPTY, data
    return STATE_NORMAL, data


def _render_timeline_variants(priorities, labels, today) -> str:
    dated = [p for p in priorities if p.get("date")]
    if not dated:
        return ""

    zoom_labels = {
        "month": labels.get("zoom_month", "Month"),
        "quarter": labels.get("zoom_quarter", "Quarter"),
        "year": labels.get("zoom_year", "Year"),
    }
    buttons = "".join(
        f'<button type="button" class="tl-zoom-btn{" tl-zoom-active" if zoom == "month" else ""}" '
        f'data-zoom-btn="{zoom}">{esc(zoom_labels[zoom])}</button>'
        for zoom, _span in _ZOOM_DAYS
    )

    variants = []
    for zoom, span in _ZOOM_DAYS:
        start, end = today, today + timedelta(days=span)
        marks = []
        for p in dated:
            d = p["date"]
            if d < start or d > end:
                continue
            pct = ((d - start).days / span) * 100
            clean_text, gate = _extract_gate(p["text"])
            if gate:
                marks.append(
                    f'<div class="tl-gate" style="left:{pct:.2f}%" '
                    f'title="{esc(gate)}: {esc(clean_text)} ({esc(d.isoformat())})">'
                    f'<span class="tl-gate-label">{esc(gate)}</span></div>'
                )
            else:
                marks.append(
                    f'<div class="tl-mark" style="left:{pct:.2f}%" '
                    f'title="{esc(clean_text)} ({esc(d.isoformat())})"></div>'
                )
        active = " tl-active" if zoom == "month" else ""
        variants.append(
            f'<div class="tl-variant{active}" data-zoom="{zoom}">'
            f'<div class="tl-track">{"".join(marks)}</div>'
            f'<div class="tl-range"><span>{esc(start.isoformat())}</span><span>{esc(end.isoformat())}</span></div>'
            f"</div>"
        )

    return (
        f'<div class="tl-zoom-toggle" role="group">{buttons}</div>'
        f'<div class="tl-variants">{"".join(variants)}</div>'
    )


def _render_backlog(backlog, labels) -> str:
    if not backlog:
        return ""

    groups: dict = {}
    for b in backlog:
        clean_text, gate = _extract_gate(b)
        key = gate or "none"
        groups.setdefault(key, []).append(clean_text)
    # Execution order (_GATE_ORDER), not the order gates happened to first
    # appear in the file; "none"/Ungrouped always sorts last.
    order = sorted(groups, key=lambda g: (g == "none", _gate_sort_key(g)))
    gate_keys = [g for g in order if g != "none"]

    rail_html = ""
    if gate_keys:
        slice_buttons = (
            f'<button type="button" class="slice-btn slice-active" data-slice-btn="all">'
            f'{esc(labels.get("slice_all", "All"))}</button>'
        ) + "".join(
            f'<button type="button" class="slice-btn" data-slice-btn="{esc(g)}">{esc(g)}</button>'
            for g in gate_keys
        )
        rail_html = f'<div class="slice-rail" role="group">{slice_buttons}</div>'

    group_html = []
    for gate in order:
        items = groups[gate]
        n = len(items)
        key = "backlog_items_one" if n == 1 else "backlog_items_other"
        count_text = (labels.get(key) or "{n} items").replace("{n}", str(n))
        heading = esc(gate) if gate != "none" else esc(labels.get("ungrouped", "Ungrouped"))
        items_html = "".join(f'<li data-gate="{esc(gate)}">{esc(b)}</li>' for b in items)
        group_html.append(
            f'<div class="group"><div class="group-head"><h3>{heading}</h3>'
            f'<span class="chip">{esc(count_text)}</span></div>'
            f"<ul>{items_html}</ul></div>"
        )

    backlog_title = esc(labels.get("backlog", "backlog"))
    return (
        f'<details><summary data-i18n="backlog">{backlog_title}</summary>'
        f'{rail_html}<div class="backlog-groups" data-slice="all">{"".join(group_html)}</div>'
        f"</details>"
    )


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("roadmap", "roadmap")
    count_html = ""

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
        priorities = data.get("priorities", [])
        count_html = f'<span class="meta">{len(priorities)}</span>'
        priority_items = "".join(
            (
                f'<li>{esc(_extract_gate(p["text"])[0])}'
                + (
                    f' <span class="chip mono">{esc(p["date"].isoformat())}</span>'
                    if p["date"] else ""
                )
                + "</li>"
            )
            for p in priorities
        )
        today = datetime.now(timezone.utc).date()
        timeline_html = _render_timeline_variants(priorities, labels, today)
        backlog_html = _render_backlog(data.get("backlog") or [], labels)
        # Fixed height, scrolling body: priority count is unbounded, the
        # panel's grid footprint must not grow with it. The timeline sits
        # outside the scroll container, same as the zoom toggle above it.
        body = (
            f"{timeline_html}"
            f'<div class="panel-scroll"><ol class="rows">{priority_items}</ol></div>'
            f"{backlog_html}"
        )

    return (
        f'<section class="panel" data-panel="roadmap">'
        f'<div class="head"><h2 data-i18n="roadmap">{esc(title)}</h2>{count_html}</div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
