"""owners map inheritance in load_manifest(): root default, node override,
no-owners-anywhere default. Mirrors the labels-inheritance test pattern in
test_render_board.py (test_v2_root_with_child_override_resolves_override).
Run with `python -m pytest board\\tests -q` from C:\\work\\50_projects\\siteoffice.
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

PROJECT_ROOT = BOARD_DIR.parent.parent.parent / "templates" / "molenwerk"  # the Molenwerk example tree


class TestOwnersInheritance(unittest.TestCase):
    def test_no_owners_anywhere_defaults_to_empty_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            raw = {
                "schemaVersion": 2, "type": "project", "name": "solo-node",
                "parent": "", "children": [], "language": "en",
            }
            (node_dir / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            self.assertEqual(manifest["owners"], {})

    def test_child_inherits_root_owners_map_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            root_raw = {
                "schemaVersion": 2, "type": "holding", "name": "root-node",
                "parent": "", "children": ["child"], "language": "en",
                "owners": {"projects/alpha": "wilco", "projects/beta": "someone-else"},
            }
            (root_dir / "siteoffice.json").write_text(json.dumps(root_raw), encoding="utf-8")
            child_dir = root_dir / "child"
            child_dir.mkdir()
            child_raw = {
                "schemaVersion": 2, "type": "project", "name": "child-node",
                "parent": "root-node", "children": [], "language": "en",
            }
            (child_dir / "siteoffice.json").write_text(json.dumps(child_raw), encoding="utf-8")
            manifest = render_board.load_manifest(child_dir)
            self.assertEqual(
                manifest["owners"],
                {"projects/alpha": "wilco", "projects/beta": "someone-else"},
            )

    def test_child_own_key_overrides_root_same_key_others_still_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            root_raw = {
                "schemaVersion": 2, "type": "holding", "name": "root-node",
                "parent": "", "children": ["child"], "language": "en",
                "owners": {"projects/alpha": "wilco", "projects/beta": "someone-else"},
            }
            (root_dir / "siteoffice.json").write_text(json.dumps(root_raw), encoding="utf-8")
            child_dir = root_dir / "child"
            child_dir.mkdir()
            child_raw = {
                "schemaVersion": 2, "type": "project", "name": "child-node",
                "parent": "root-node", "children": [], "language": "en",
                "owners": {"projects/alpha": "child-overrides-this-one"},
            }
            (child_dir / "siteoffice.json").write_text(json.dumps(child_raw), encoding="utf-8")
            manifest = render_board.load_manifest(child_dir)
            self.assertEqual(manifest["owners"]["projects/alpha"], "child-overrides-this-one")
            self.assertEqual(manifest["owners"]["projects/beta"], "someone-else")  # still inherited

    def test_real_estate_siteoffice_json_declares_no_owners_map(self):
        manifest = render_board.load_manifest(PROJECT_ROOT)
        self.assertEqual(manifest["owners"], {})


if __name__ == "__main__":
    unittest.main()
