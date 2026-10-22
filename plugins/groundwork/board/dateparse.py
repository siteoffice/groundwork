"""Small, lenient date and table parser for a node's own DEADLINES.md and
02_roadmap\\roadmap.md, used when the hub's NOW-block parser
(C:\\work\\10_holding\\04_engineering\\command_center\\deadlines_parse.py) does
not fit the node's file shape (no "## ... NOW" heading, or a different
table shape). Stdlib only. Never raises on prose it cannot date: a row or
line it cannot parse is simply skipped by the caller.
"""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_BACKLOG_HEADING = re.compile(r"^##\s+Backlog\b")
_NUMBERED_ITEM = re.compile(r"^(\d+)\.\s+(.*)$")
_BULLET_ITEM = re.compile(r"^[-*]\s+(.*)$")


def find_date(text: str):
    """First date found in text: YYYY-MM-DD, else D Mon YYYY. None if none
    found, or the numbers found do not form a real calendar date."""
    m = _ISO.search(text or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DMY.search(text or "")
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                pass
    return None


def _table_rows(md_text: str):
    """Yield stripped-cell lists for every pipe-delimited row, skipping
    separator rows (---)."""
    for line in (md_text or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        parts = s.split("|")
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]
        cells = [p.strip() for p in parts]
        if cells and all(_SEPARATOR_CELL.match(c) for c in cells):
            continue
        yield cells


def extract_deadline_rows(md_text: str):
    """Every table row whose first cell carries a date, anywhere in the
    file (no NOW-heading requirement, unlike the hub parser). Skips the
    header row (its first cell will not date) and any row it cannot date;
    never raises."""
    rows = []
    for cells in _table_rows(md_text):
        if not cells:
            continue
        d = find_date(cells[0])
        if d is None:
            continue
        item = cells[1] if len(cells) > 1 else ""
        notes = cells[2] if len(cells) > 2 else ""
        rows.append({"date": d, "item": item, "notes": notes})
    rows.sort(key=lambda r: r["date"])
    return rows


def extract_roadmap(md_text: str):
    """(priorities, backlog) from a roadmap.md written to the Siteoffice
    convention (architecture note section 10, siteoffice-setup SKILL.md):
    a numbered priority list, then an optional '## Backlog' heading (any
    suffix) with bullet items. Only the FIRST contiguous numbered list in
    the file counts as the priorities (indented continuation lines of a
    wrapped item are tolerated, a later, separately-introduced numbered
    list in the file's prose is not picked up). Each priority carries an
    optional date chip, the first date found in the item's own opening
    line."""
    lines = (md_text or "").splitlines()

    backlog_at = None
    for i, line in enumerate(lines):
        if _BACKLOG_HEADING.match(line.strip()):
            backlog_at = i
            break
    priority_lines = lines[:backlog_at] if backlog_at is not None else lines

    priorities = []
    started = False
    for line in priority_lines:
        stripped = line.strip()
        m = _NUMBERED_ITEM.match(stripped)
        if m:
            started = True
            item_text = m.group(2).strip()
            priorities.append({"text": item_text, "date": find_date(item_text)})
            continue
        if started:
            if stripped and (line.startswith(" ") or line.startswith("\t")):
                continue  # indented continuation of the item above
            break  # first numbered list is over

    backlog = []
    if backlog_at is not None:
        for line in lines[backlog_at + 1:]:
            s = line.strip()
            if re.match(r"^##\s+", s):
                break
            m = _BULLET_ITEM.match(s)
            if m:
                backlog.append(m.group(1).strip())

    return priorities, backlog
