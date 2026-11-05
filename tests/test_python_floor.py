"""The five user-facing spots state the Python floor as 3.9, and the engine count is measured."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_README = ROOT / "plugins" / "groundwork" / "engine" / "README.md"
SPOTS = [
    ROOT / "README.md",
    ROOT / "docs" / "setup-guide.md",
    ROOT / "plugins" / "groundwork" / "skills" / "one-loop" / "SKILL.md",
    ENGINE_README,
]
OLD_FLOOR = re.compile(r"(?<![\d.])3\.8(?!\d)")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_user_facing_spot_says_3_8():
    hits = []
    for path in SPOTS:
        for number, line in enumerate(_text(path).splitlines(), start=1):
            if OLD_FLOOR.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not hits, "Python 3.8 still named:\n" + "\n".join(hits)


def test_each_spot_says_3_9():
    missing = [str(p.relative_to(ROOT)) for p in SPOTS if "3.9 or newer" not in _text(p)]
    assert not missing, f"no '3.9 or newer' in: {missing}"


def test_engine_readme_count_is_measured():
    text = _text(ENGINE_README)
    assert "1021 passed, 4 skipped" in text
    assert "979" not in text
