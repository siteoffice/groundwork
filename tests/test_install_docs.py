"""Checks on the install docs: setup guide per OS, install matrix page, README and CHANGELOG."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "setup-guide.md"
MATRIX = ROOT / "docs" / "install-matrix.md"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"

TESTED_ON_HEADER = "| OS image | Python | Node | Claude Code | Date | Run |"
TESTED_ON_PLACEHOLDER = "| pending first green run | | | | | |"
PROBE_STEPS = ("marketplace add", "install groundwork", "install groundwork-mods", "plugin list")
SUITES = ("engine", "board", "root", "mods plugin test")
CELL_VALUES = {"proven by CI", "skipped: needs a Claude login", "not tested", "pending first green run"}


def _read(path: Path) -> str:
    assert path.is_file(), "missing %s" % path
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_tested_on_table():
    lines = _read(GUIDE).splitlines()
    assert TESTED_ON_HEADER in lines
    assert "## Tested on" in lines
    start = lines.index(TESTED_ON_HEADER) + 2
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        rows.append(line)
    assert rows, "the Tested on table has no rows"
    if rows != [TESTED_ON_PLACEHOLDER]:
        # A recorded run: every row names a system and links its run.
        for row in rows:
            cells = _cells(row)
            assert len(cells) == 6, row
            assert cells[0] in ("windows-latest", "macos-latest", "ubuntu-24.04"), row
            assert cells[5].startswith("[run](https://"), row


def test_tested_on_header_matches_ci_row():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import ci_report
    finally:
        sys.path.pop(0)
    row = ci_report.tested_on_row("ubuntu-24.04", "3.9", "22", "2.0.0", "2026-11-01", "https://x")
    assert len(_cells(row)) == len(_cells(TESTED_ON_HEADER))


def test_guide_has_three_os():
    text = _read(GUIDE)
    for name in ("Windows", "macOS", "Linux"):
        assert name in text, name
    blocks = re.findall(r"```bash\n([^\n]*)\n", text)
    assert any("Linux" in first for first in blocks), blocks
    assert "macOS is untested" not in text


def test_guide_says_what_ci_proves():
    text = _read(GUIDE)
    assert "## What CI proves" in text
    assert "CI proves the command-line install and the tests" in text
    assert "Claude Desktop app install is not tested by CI" in text


def test_install_matrix_per_os_and_step():
    lines = _read(MATRIX).splitlines()
    header_idx = next(i for i, l in enumerate(lines) if l.startswith("|") and "Windows" in l)
    header = _cells(lines[header_idx])
    assert header[1:] == ["Windows", "macOS", "Linux"], header
    rows = {}
    for line in lines[header_idx + 2:]:
        if not line.startswith("|"):
            break
        cells = _cells(line)
        rows[cells[0].strip("`")] = cells[1:]
    for name in PROBE_STEPS + SUITES + ("Claude Desktop app install",):
        assert name in rows, name
    for name, cells in rows.items():
        assert len(cells) == 3, name
        for cell in cells:
            assert cell in CELL_VALUES, (name, cell)
    assert rows["Claude Desktop app install"] == ["not tested"] * 3


def test_readme_points_to_the_guide():
    text = _read(README)
    assert "docs/setup-guide.md" in text
    assert "docs/install-matrix.md" in text
    assert "539" not in text


def test_changelog_040():
    text = _read(CHANGELOG)
    headings = [h for h in re.findall(r"^## .*$", text, flags=re.M) if h != "## Unreleased"]
    assert headings and headings[0] == "## 0.4.0", headings[:1]
    section = text.split("## 0.4.0", 1)[1].split("\n## ", 1)[0]
    assert "Python 3.9" in section
