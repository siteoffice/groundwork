"""Tests for the flows/_lib/guidance fragment directory. Plain pytest, stdlib
only, hermetic.

These are the contract tests for the fragments themselves as data: they read
the real directory, not a tmp_path copy, because the point is to prove the ten
shipped files exist and are well formed. render_guidance's own behaviour is
tested against an injected guidance_root in test_render_guidance.py.
"""
import pathlib
import re

GUIDANCE_ROOT = pathlib.Path(__file__).resolve().parent / "guidance"

EXPECTED_KEYS = (
    "gate_open.spec", "gate_await.spec", "gate_consume.spec.approve",
    "gate_open.accept", "gate_await.accept",
    "sprint_await", "sprint_launch", "ship_execute",
)


def test_the_guidance_directory_holds_the_expected_fragments():
    for key in EXPECTED_KEYS:
        assert (GUIDANCE_ROOT / (key + ".md")).is_file(), key


def test_the_guidance_directory_holds_exactly_the_eight_expected_fragments():
    assert sorted(p.name for p in GUIDANCE_ROOT.glob("*.md")) == sorted(
        k + ".md" for k in EXPECTED_KEYS)


def test_no_gate_coded_file_remains():
    leftovers = [p.name for p in GUIDANCE_ROOT.iterdir()
                 if re.search(r"\.g[1-4]\.", p.name)]
    assert leftovers == []


def test_no_fragment_names_an_old_gate_code():
    for key in EXPECTED_KEYS:
        text = (GUIDANCE_ROOT / (key + ".md")).read_text(encoding="utf-8")
        assert not re.search(r"G[1-4]", text), key


def test_every_fragment_is_non_empty():
    for key in EXPECTED_KEYS:
        text = (GUIDANCE_ROOT / (key + ".md")).read_text(encoding="utf-8")
        assert text.strip(), key


def test_no_fragment_contains_an_em_dash():
    """House hard rule, 2026-07-08: no U+2014 in any file."""
    for key in EXPECTED_KEYS:
        text = (GUIDANCE_ROOT / (key + ".md")).read_text(encoding="utf-8")
        assert "\u2014" not in text, key


def test_every_fragment_is_pure_ascii():
    for key in EXPECTED_KEYS:
        raw = (GUIDANCE_ROOT / (key + ".md")).read_bytes()
        raw.decode("ascii")  # raises UnicodeDecodeError if it is not


def test_no_fragment_carries_yaml_frontmatter():
    """Plain markdown, no frontmatter: the spec's Scope item 1."""
    for key in EXPECTED_KEYS:
        text = (GUIDANCE_ROOT / (key + ".md")).read_text(encoding="utf-8")
        assert not text.lstrip().startswith("---"), key
