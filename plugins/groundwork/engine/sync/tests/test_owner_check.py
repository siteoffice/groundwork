"""owner_check.py: longest-prefix-match resolution and its own independent
re-implementation of the owners-inheritance walk (root merged with a node's
own map, node's own key wins), against synthetic siteoffice.json fixtures
built fresh per test. Never touches the real siteoffice-private repo.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SYNC_DIR = HERE.parent
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))
import owner_check  # noqa: E402


def _write_manifest(node_dir: pathlib.Path, **fields) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "schemaVersion": 2, "type": "project", "name": node_dir.name,
        "parent": "", "children": [], "language": "en",
    }
    base.update(fields)
    (node_dir / "siteoffice.json").write_text(json.dumps(base), encoding="utf-8")


class TestOwnerCheck(unittest.TestCase):
    def test_no_owners_map_anywhere_never_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(repo_root)
            owner = owner_check.owner_for_path(repo_root, "some/file.md")
            self.assertIsNone(owner)

    def test_root_owners_inherited_by_a_path_with_no_child_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(repo_root, parent="", owners={"projects/alpha": "wilco"})
            (repo_root / "projects" / "alpha").mkdir(parents=True)
            owner = owner_check.owner_for_path(repo_root, "projects/alpha/notes.md")
            self.assertEqual(owner, "wilco")

    def test_longest_prefix_match_wins_over_a_shorter_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(
                repo_root,
                parent="",
                owners={"projects": "team-a", "projects/alpha": "wilco"},
            )
            owner = owner_check.owner_for_path(repo_root, "projects/alpha/notes.md")
            self.assertEqual(owner, "wilco")
            owner_other = owner_check.owner_for_path(repo_root, "projects/beta/notes.md")
            self.assertEqual(owner_other, "team-a")

    def test_child_manifest_own_owners_key_overrides_root_same_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(
                repo_root,
                parent="",
                children=["child"],
                owners={"shared.md": "root-owner"},
            )
            child_dir = repo_root / "child"
            _write_manifest(
                child_dir,
                parent=repo_root.name,
                owners={"shared.md": "child-owner"},
            )
            owner = owner_check.owner_for_path(repo_root, "child/shared.md")
            self.assertEqual(owner, "child-owner")

    def test_path_with_no_matching_prefix_is_unowned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(repo_root, owners={"projects/alpha": "wilco"})
            owner = owner_check.owner_for_path(repo_root, "projects/gamma/file.md")
            self.assertIsNone(owner)

    def test_root_owners_key_is_root_relative_not_reinterpreted_per_child(self):
        """Ruling (I5, not a bug to fix): owners keys are always relative
        to the ROOT manifest's directory, inherited into child manifests
        unchanged, same model as labels. A child node with its own
        siteoffice.json and no owners key of its own still lets the root's
        "docs" key claim the child's own "docs" subfolder, since the key
        is never rescoped to be relative to the child instead of the
        root. This pins that actual, deliberate behavior rather than the
        per-manifest-relative reading the spec's prose could also support.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = pathlib.Path(tmp)
            _write_manifest(
                repo_root, parent="", children=["child"], owners={"docs": "alice"},
            )
            child_dir = repo_root / "child"
            _write_manifest(child_dir, parent=repo_root.name)  # no owners key of its own
            owner = owner_check.owner_for_path(repo_root, "child/docs/x.md")
            self.assertEqual(owner, "alice")


if __name__ == "__main__":
    unittest.main()
