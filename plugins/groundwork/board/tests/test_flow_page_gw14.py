"""GW14 (issue #105) flow page defects: the Next line must offer the verdicts
the open gate actually takes, and the steps table must number from 1.
Plain flow.json fixtures, no engine import (board never imports flows\\_lib).
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import render_board  # noqa: E402


def _render_flow_page(gate_id=None, steps=None, log=None, sprints_dir=False) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "siteoffice.json").write_text(json.dumps({
            "schemaVersion": 2, "type": "holding", "name": "t", "parent": "",
            "children": [], "language": "en", "settings": {"languages": ["en"]},
        }), encoding="utf-8")
        flow_dir = root / "flows" / "proj" / "slug"
        flow_dir.mkdir(parents=True)
        gate = None
        if gate_id:
            gate = {"id": gate_id, "status": "awaiting-owner", "token": "tok"}
        flow = {
            "project": "proj", "slug": "slug", "stage": "execute",
            "status": "awaiting-owner" if gate else "running", "gate": gate,
            "updated_at": "2026-11-04T09:00:00Z", "idea": "an idea",
            "steps": steps if steps is not None else [],
        }
        (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
        if log:
            (flow_dir / "flow-log.ndjson").write_text(
                "".join(json.dumps(e) + "\n" for e in log), encoding="utf-8")
        if sprints_dir:
            (root / "sprints").mkdir()
        render_board.main(["flows", str(root / "flows"), "--root", str(root)])
        return (root / "_board" / "flows" / "proj" / "slug" / "index.html").read_text(
            encoding="utf-8")


class TestNextLineMatchesGate(unittest.TestCase):
    def test_g4_offers_ship_hold_park(self):
        page = _render_flow_page("G4")
        self.assertIn("ship|hold|park", page)
        self.assertNotIn("approve|reject", page)

    def test_g3_offers_proceed_adapt_close_park(self):
        page = _render_flow_page("G3")
        self.assertIn("proceed|adapt|close|park", page)
        self.assertNotIn("approve|reject", page)

    def test_g2_still_offers_approve_reject(self):
        self.assertIn("approve|reject", _render_flow_page("G2"))


class TestStepsNumberFromOne(unittest.TestCase):
    def test_first_step_is_shown_as_1(self):
        page = _render_flow_page(steps=[
            {"index": 0, "title": "First step", "status": "done"},
            {"index": 1, "title": "Second step", "status": "pending"},
        ])
        self.assertIn("<td>1</td><td>First step</td>", page)
        self.assertIn("<td>2</td><td>Second step</td>", page)
        self.assertNotIn("<td>0</td>", page)


SPRINT_LOG = [{
    "ts": "2026-11-04T09:00:00Z", "stage": "execute", "actor": "orchestrator",
    "action": "sprint_emitted", "outcome": "info",
    "detail": "/sprint proj specs/s.md --model-map spec=opus,dev=sonnet",
}]


class TestSprintLineNeedsASprintRunner(unittest.TestCase):
    def test_no_sprints_folder_hides_the_sprint_line(self):
        page = _render_flow_page(log=SPRINT_LOG)
        self.assertNotIn("--model-map", page)
        self.assertIn("sprint_emitted", page)
        self.assertIn("work step ready", page)

    def test_a_sprints_folder_keeps_the_sprint_line(self):
        page = _render_flow_page(log=SPRINT_LOG, sprints_dir=True)
        self.assertIn("--model-map", page)
        self.assertNotIn("work step ready", page)


if __name__ == "__main__":
    unittest.main()
