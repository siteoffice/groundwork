"""Every skill's frontmatter must parse as YAML, or the skill loads with its description dropped.

A plain value that holds a colon followed by a space ends the value early. That broke
siteoffice-migrate once: "Modes: audit (...". The first test needs no YAML library; the second
uses PyYAML when it is installed.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "plugins" / "groundwork" / "skills").glob("*/SKILL.md"))


def frontmatter(path):
    m = re.match(r"---\r?\n(.*?)\r?\n---", path.read_text(encoding="utf-8"), re.S)
    assert m, "%s has no frontmatter" % path
    return m.group(1)


def test_there_are_skills_to_check():
    assert len(SKILLS) >= 7


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_plain_values_hold_no_colon_space(path):
    for line in frontmatter(path).splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if not value or value[0] in "\"'>|[{":
            continue
        assert ": " not in value and " #" not in value, "%s: unquoted value of '%s' breaks YAML" % (path.parent.name, key)


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter_parses_with_a_yaml_parser(path):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(frontmatter(path))
    assert data["name"] == path.parent.name
    assert data["description"]
