"""The skeleton matches the unit that siteoffice-setup describes, and the board reads it."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKELETON = ROOT / "templates" / "skeleton"
MOLENWERK = ROOT / "templates" / "molenwerk"
sys.path.insert(0, str(ROOT / "plugins" / "groundwork" / "board"))
import render_board  # noqa: E402

UNIT_FILES = [
    "AGENTS.md", "CLAUDE.md", "STATUS.md", "DEADLINES.md", "SESSION_LOG.md", "siteoffice.json",
    "GLOSSARY.md", "00_context/brief.md", "02_roadmap/roadmap.md", "03_logs/decisions.md",
]
UNIT_DIRS = ["03_logs/_history", "04_assets"]


def test_skeleton_has_every_file_of_the_unit():
    for rel in UNIT_FILES:
        assert (SKELETON / rel).is_file(), rel
    for rel in UNIT_DIRS:
        assert (SKELETON / rel).is_dir(), rel


def test_templates_ignore_the_policy_folder_and_the_board_output():
    for node in (SKELETON, MOLENWERK):
        ignored = (node / ".gitignore").read_text(encoding="utf-8").split()
        assert ".groundwork/" in ignored and "_board/" in ignored, node.name
    root_ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".groundwork/" in root_ignored


def test_claude_md_is_one_import_line():
    for node in (SKELETON, MOLENWERK):
        assert (node / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"


def test_no_root_folder_breaks_the_naming_rule():
    for node in (SKELETON, MOLENWERK):
        for p in node.iterdir():
            if p.is_dir():
                assert p.name[0].isdigit() or p.name.startswith(("_", ".")), p.name


def test_the_board_reads_both_manifests():
    for node in (SKELETON, MOLENWERK):
        manifest = render_board.load_manifest(node)
        assert manifest["schemaVersion"] == 2
        assert manifest["name"]


def test_no_em_dash_in_any_template():
    dash = chr(0x2014)
    for f in (ROOT / "templates").rglob("*"):
        if f.is_file():
            assert dash not in f.read_text(encoding="utf-8"), f
