"""Tests for board\\hours.py: the ndjson reader and summarizer (guide step
8). Synthetic ndjson in temp dirs only, nothing real seeded. Stdlib
compatible: run with `python -m pytest board\\tests -q` or plain unittest.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import load_module_from_path  # noqa: E402

# Loaded by explicit path, not a plain `import hours`: once render_board.py
# has been imported anywhere in this process it leaves TEMPLATES_DIR ahead
# of BOARD_DIR on sys.path, so a later bare `import hours` would silently
# resolve to board\templates\hours.py (the panel) instead of this file.
hours = load_module_from_path("_siteoffice_hours_reader_test", BOARD_DIR / "hours.py")


def _write_month(node_dir: pathlib.Path, year_month: str, lines):
    time_dir = node_dir / "03_logs" / "time"
    time_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines)
    (time_dir / f"{year_month}.ndjson").write_text(text + "\n", encoding="utf-8")


class TestLoadEntries(unittest.TestCase):
    def test_good_and_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_month(node_dir, "2026-09", [
                {"date": "2026-09-10", "person": "wilco", "node": "projects/siteoffice",
                 "hours": 3.5, "so": True, "tag": "build", "note": "", "loggedOn": "2026-09-10"},
                "not json at all",
                {"date": "2026-09-11", "person": "wilco"},  # missing required fields
                {"date": "not-a-date", "person": "wilco", "node": "x", "hours": 1},
                "",  # blank line, skipped silently, not counted malformed
            ])
            entries, malformed = hours.load_entries(node_dir, 2026)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["date"], date(2026, 9, 10))
            self.assertEqual(malformed, 3)

    def test_correction_line_nets_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_month(node_dir, "2026-09", [
                {"date": "2026-09-10", "person": "wilco", "node": "x", "hours": 3.5,
                 "so": True, "tag": "build", "note": "", "loggedOn": "2026-09-10"},
                {"date": "2026-09-10", "person": "wilco", "node": "x", "hours": -1.0,
                 "so": True, "tag": "build", "note": "over-logged by an hour",
                 "loggedOn": "2026-09-12"},
            ])
            entries, malformed = hours.load_entries(node_dir, 2026)
            self.assertEqual(malformed, 0)
            summary = hours.summarize(entries, date(2026, 9, 15))
            self.assertEqual(summary["yearHours"], 2.5)

    def test_no_time_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries, malformed = hours.load_entries(pathlib.Path(tmp), 2026)
            self.assertEqual((entries, malformed), ([], 0))


class TestSummarize(unittest.TestCase):
    def test_month_and_year_sums_and_so_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_month(node_dir, "2026-08", [
                {"date": "2026-08-05", "person": "wilco", "node": "x", "hours": 4.0,
                 "so": False, "tag": "", "note": "", "loggedOn": "2026-08-05"},
            ])
            _write_month(node_dir, "2026-09", [
                {"date": "2026-09-10", "person": "wilco", "node": "x", "hours": 3.0,
                 "so": True, "tag": "", "note": "", "loggedOn": "2026-09-10"},
                {"date": "2026-09-11", "person": "sam", "node": "x", "hours": 1.0,
                 "so": False, "tag": "", "note": "", "loggedOn": "2026-09-11"},
            ])
            entries, _ = hours.load_entries(node_dir, 2026)
            summary = hours.summarize(entries, date(2026, 9, 15))
            self.assertEqual(summary["monthHours"], 4.0)  # September only: 3.0 + 1.0
            self.assertEqual(summary["yearHours"], 8.0)  # August + September
            self.assertEqual(summary["soShare"], 0.375)  # 3.0 so-hours / 8.0 total
            self.assertEqual(summary["perPerson"]["wilco"], 7.0)
            self.assertEqual(summary["perPerson"]["sam"], 1.0)

    def test_so_share_zero_when_no_hours(self):
        summary = hours.summarize([], date(2026, 9, 15))
        self.assertEqual(summary["soShare"], 0.0)
        self.assertEqual(summary["yearHours"], 0.0)
        self.assertEqual(summary["unloggedDays"], [])


class TestUnloggedDays(unittest.TestCase):
    def test_flags_working_days_skips_weekend(self):
        # Monday 2026-08-31 then the next Monday 2026-09-07 logged; the
        # working days in between (Tue-Fri) should be flagged, the weekend
        # (Sat 09-05, Sun 09-06) never flagged. "today" is far enough past
        # both weeks that the whole gap is older than 10 working days.
        entries = [
            {"date": date(2026, 8, 31), "person": "wilco", "node": "x", "hours": 1.0,
             "so": False, "tag": "", "note": "", "loggedOn": "2026-08-31"},
            {"date": date(2026, 9, 7), "person": "wilco", "node": "x", "hours": 1.0,
             "so": False, "tag": "", "note": "", "loggedOn": "2026-09-07"},
        ]
        today = date(2026, 9, 21)  # exactly 10 working days after 09-07
        gaps = hours.unlogged_days(entries, today)
        self.assertEqual(gaps, [
            date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4),
        ])

    def test_no_gap_flagged_inside_the_10_working_day_grace(self):
        entries = [
            {"date": date(2026, 9, 10), "person": "wilco", "node": "x", "hours": 1.0,
             "so": False, "tag": "", "note": "", "loggedOn": "2026-09-10"},
        ]
        gaps = hours.unlogged_days(entries, date(2026, 9, 11))
        self.assertEqual(gaps, [])

    def test_empty_entries_no_gaps(self):
        self.assertEqual(hours.unlogged_days([], date(2026, 9, 15)), [])


if __name__ == "__main__":
    unittest.main()
