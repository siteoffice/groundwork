"""GW14 (issue #105, item 2): a node whose siteoffice.json declares no `flows`
key still lists an open gate when `<node>\\flows` exists, which is where the
Groundwork engine writes. A declared key still wins; no folder stays empty.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
for _p in (BOARD_DIR, BOARD_DIR / "templates"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import render_board  # noqa: E402
import waiting_on_you as tpl  # noqa: E402


def _node(tmp, flows_key=None, gate_status="awaiting-owner", make_flows=True):
    node = pathlib.Path(tmp)
    raw = {
        "schemaVersion": 2, "type": "project", "name": node.name, "parent": "",
        "children": [], "language": "en",
        "settings": {"planningHorizonWeeks": 2, "languages": ["en"],
                     "timeRegistration": False},
    }
    if flows_key:
        raw["flows"] = flows_key
    (node / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")
    if make_flows:
        fdir = node / "flows" / "proj" / "slug"
        fdir.mkdir(parents=True)
        (fdir / "flow.json").write_text(json.dumps({
            "project": "proj", "slug": "slug", "status": "awaiting-owner",
            "gate": {"id": "G1", "status": gate_status, "token": "tok"},
        }), encoding="utf-8")
    return render_board.load_manifest(node)


class TestUndeclaredFlowsFolder(unittest.TestCase):
    def test_open_gate_in_undeclared_flows_folder_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, data = tpl.collect(_node(tmp))
            self.assertEqual(state, tpl.STATE_NORMAL)
            self.assertEqual([i["text"] for i in data["items"]], ["proj/slug: gate open"])

    def test_decided_gate_in_undeclared_flows_folder_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, _data = tpl.collect(_node(tmp, gate_status="decided"))
            self.assertEqual(state, tpl.STATE_EMPTY)

    def test_no_flows_folder_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, _data = tpl.collect(_node(tmp, make_flows=False))
            self.assertEqual(state, tpl.STATE_EMPTY)

    def test_a_declared_missing_root_is_still_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, _data = tpl.collect(_node(tmp, flows_key="no_such_dir"))
            self.assertEqual(state, tpl.STATE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
