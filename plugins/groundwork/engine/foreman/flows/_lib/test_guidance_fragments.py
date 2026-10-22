"""Tests for the flows/_lib/guidance fragment directory. Plain pytest, stdlib
only, hermetic.

These are the contract tests for the fragments themselves as data: they read
the real directory, not a tmp_path copy, because the point is to prove the ten
shipped files exist and are well formed. render_guidance's own behaviour is
tested against an injected guidance_root in test_render_guidance.py.
"""
import pathlib

GUIDANCE_ROOT = pathlib.Path(__file__).resolve().parent / "guidance"

EXPECTED_KEYS = (
    "gate_open.g1", "gate_open.g2", "gate_open.g3", "gate_open.g4",
    "gate_await.g1", "gate_await.g2", "gate_await.g3", "gate_await.g4",
    "gate_consume.g1.approve", "sprint_await", "sprint_launch", "ship_execute",
)


def test_the_guidance_directory_holds_exactly_the_twelve_expected_fragments():
    found = sorted(p.name for p in GUIDANCE_ROOT.glob("*.md"))
    assert found == sorted(key + ".md" for key in EXPECTED_KEYS)


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
