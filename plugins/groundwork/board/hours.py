"""WBSO-grade time registration reader (architecture note section 11).
Schema: board\\HOURS_SCHEMA.md. Reads 03_logs\\time\\YYYY-MM.ndjson, one
JSON object per line, append-only; a correction is a new line with a
negative `hours` and a `note`. Stdlib only, never raises on a malformed
line, only skips and counts it.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, timedelta

_REQUIRED_FIELDS = ("date", "person", "node", "hours")
_UNLOGGED_WORKING_DAYS = 10


def _month_files(node_dir: pathlib.Path, year: int):
    time_dir = node_dir / "03_logs" / "time"
    if not time_dir.is_dir():
        return []
    return sorted(time_dir.glob(f"{year}-*.ndjson"))


def load_entries(node_dir: pathlib.Path, year: int):
    """(entries, malformed_count). entries carry `date` as a date object and
    `hours` as a float; every other field is passed through as read. A line
    that is not valid JSON, not an object, missing a required field, or
    whose `date`/`hours` will not parse is skipped and counted, never
    raises."""
    entries = []
    malformed = 0
    for path in _month_files(node_dir, year):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(obj, dict) or any(k not in obj for k in _REQUIRED_FIELDS):
                malformed += 1
                continue
            try:
                obj = dict(obj)
                obj["date"] = date.fromisoformat(obj["date"])
                obj["hours"] = float(obj["hours"])
            except (TypeError, ValueError):
                malformed += 1
                continue
            entries.append(obj)
    return entries, malformed


def _is_working_day(d: date) -> bool:
    return d.weekday() < 5  # Monday=0 .. Friday=4; no holiday calendar in v1


def _nth_working_day_before(d: date, n: int) -> date:
    cur = d
    count = 0
    while count < n:
        cur -= timedelta(days=1)
        if _is_working_day(cur):
            count += 1
    return cur


def unlogged_days(entries, today: date):
    """Working days with no entry at all, from the earliest entry on record
    through the day exactly 10 working days before today (inclusive):
    anything more recent is still inside the WBSO grace window, and with no
    entries at all there is nothing to compare against (an empty log is the
    panel's empty state, not a warning)."""
    if not entries:
        return []
    logged_dates = {e["date"] for e in entries}
    start = min(logged_dates)
    cutoff = _nth_working_day_before(today, _UNLOGGED_WORKING_DAYS)
    if start > cutoff:
        return []
    out = []
    cur = start
    while cur <= cutoff:
        if _is_working_day(cur) and cur not in logged_dates:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def summarize(entries, today: date) -> dict:
    """This month, this year, S&O share, per-person totals (all for
    today's calendar year) and unlogged_days. Corrections (negative hours)
    net out naturally in every sum."""
    year, month = today.year, today.month

    month_total = 0.0
    year_total = 0.0
    so_total = 0.0
    per_person: dict = {}
    per_person_count: dict = {}
    for e in entries:
        if e["date"].year != year:
            continue
        year_total += e["hours"]
        per_person[e["person"]] = per_person.get(e["person"], 0.0) + e["hours"]
        per_person_count[e["person"]] = per_person_count.get(e["person"], 0) + 1
        if e.get("so"):
            so_total += e["hours"]
        if e["date"].month == month:
            month_total += e["hours"]

    return {
        "monthHours": round(month_total, 2),
        "yearHours": round(year_total, 2),
        "soShare": (so_total / year_total) if year_total else 0.0,
        "perPerson": {k: round(v, 2) for k, v in per_person.items()},
        "perPersonCount": per_person_count,
        "unloggedDays": unlogged_days(entries, today),
    }
