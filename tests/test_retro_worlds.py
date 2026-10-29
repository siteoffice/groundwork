import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

import retro_digest as rd
from retro_fixture_util import materialize_fixtures


def test_world_match_ignores_case_and_slash_style():
    assert rd.is_under("c:/work/world/notes", "C:\\Work\\World")
    assert rd.is_under("C:\\Work\\World", "c:/work/world/")
    assert not rd.is_under("C:\\Work\\Worldwide\\x", "C:\\Work\\World")


def _collect(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    world = {"folder": EXP["world_folder"], "report_dir": str(tmp_path / "world-out")}
    month_dir = tmp_path / "root" / "retro" / "2026-10"
    res = rd.collect(rd.discover_sources(code, cowork, None, inner), month_dir, "2026-10", [world], [])
    return res, month_dir


def test_world_session_lands_only_in_world_location(tmp_path):
    res, month_dir = _collect(tmp_path)
    assert {d["session_id"] for d in res["main"]} == set(EXP["sessions"]) - {"s-world"}
    assert [d["session_id"] for d in res["worlds"][0]["digests"]] == ["s-world"]
    assert (tmp_path / "world-out" / "2026-10" / "digests" / "code-s-world.json").is_file()
    assert not any("s-world" in p.name for p in (month_dir / "digests").iterdir())


def test_world_strings_never_reach_main_digests(tmp_path):
    _, month_dir = _collect(tmp_path)
    for p in (month_dir / "digests").rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert EXP["world_marker"] not in text and "private-world" not in text, p


def test_transcript_bytes_count_main_only(tmp_path):
    res, _ = _collect(tmp_path)
    assert res["transcript_bytes"] == sum(d["transcript_bytes"] for d in res["main"])
    assert res["worlds"][0]["transcript_bytes"] == res["worlds"][0]["digests"][0]["transcript_bytes"]
    assert (tmp_path / "root" / "retro" / "2026-10" / "opened.log").is_file()
