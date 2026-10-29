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


def _sources(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    return rd.discover_sources(code, cowork, None, inner)


def test_normalisation_collapses_dot_segments():
    assert rd.is_under("c:/work/other/../world/x", "C:/Work/World")
    assert rd.is_under("C:/Work//World/./x", "C:/Work/World")
    assert not rd.is_under("C:/Work/World/../Other", "C:/Work/World")


def test_report_dir_equal_to_main_root_is_refused(tmp_path):
    month_dir = tmp_path / "root" / "retro" / "2026-10"
    world = {"folder": EXP["world_folder"], "report_dir": str(tmp_path / "root" / "retro")}
    with pytest.raises(ValueError):
        rd.collect(_sources(tmp_path), month_dir, "2026-10", [world], [])


def test_report_dir_inside_main_month_dir_is_refused(tmp_path):
    month_dir = tmp_path / "root" / "retro" / "2026-10"
    world = {"folder": EXP["world_folder"], "report_dir": str(month_dir / "sub")}
    with pytest.raises(ValueError):
        rd.collect(_sources(tmp_path), month_dir, "2026-10", [world], [])


def test_report_dir_that_resolves_to_main_digests_is_refused(tmp_path):
    month_dir = tmp_path / "root" / "retro" / "2026-10"
    world = {"folder": EXP["world_folder"], "report_dir": str(month_dir / ".." / "2026-10" / "..")}
    with pytest.raises(ValueError):
        rd.collect(_sources(tmp_path), month_dir, "2026-10", [world], [])


def test_main_month_dir_inside_world_location_is_refused(tmp_path):
    month_dir = tmp_path / "wout" / "inner" / "2026-10"
    world = {"folder": EXP["world_folder"], "report_dir": str(tmp_path / "wout")}
    with pytest.raises(ValueError):
        rd.collect(_sources(tmp_path), month_dir, "2026-10", [world], [])


def test_empty_or_blank_world_folder_is_refused(tmp_path):
    for bad in ("", "   ", "/", "\\"):
        world = {"folder": bad, "report_dir": str(tmp_path / "w")}
        with pytest.raises(ValueError):
            rd.collect(_sources(tmp_path), tmp_path / "m", "2026-10", [world], [])


def test_write_digest_sanitises_session_id(tmp_path):
    out = tmp_path / "out"
    bs = chr(92)
    for sid in ("../../evil", "C:" + bs + "evil" + bs + "x", "/abs/evil", "...", ""):
        p = rd.write_digest({"source": "code", "session_id": sid}, out)
        assert p.resolve().parent == out.resolve()
        assert p.name.startswith("code-") and p.name.endswith(".json")
    assert rd.write_digest({"source": "code", "session_id": "../x"}, out).name == "code-_x.json"
    assert rd.write_digest({"source": "code", "session_id": ""}, out).name == "code-session.json"


def test_duplicate_world_entries_do_not_merge(tmp_path):
    w1 = {"folder": EXP["world_folder"], "report_dir": str(tmp_path / "w1")}
    w2 = dict(w1)
    res = rd.collect(_sources(tmp_path), tmp_path / "m" / "2026-10", "2026-10", [w1, w2], [])
    assert len(res["worlds"]) == 2
    assert [d["session_id"] for d in res["worlds"][0]["digests"]] == ["s-world"]


def test_cowork_selected_folder_routes_to_world(tmp_path):
    world = {"folder": "/home/demo/projects/acme", "report_dir": str(tmp_path / "wout")}
    res = rd.collect(_sources(tmp_path), tmp_path / "m" / "2026-10", "2026-10", [world], [])
    assert "cw1" in {d["session_id"] for d in res["worlds"][0]["digests"]}
    assert "cw1" not in {d["session_id"] for d in res["main"]}


def test_worldwide_sibling_stays_in_main_through_collect(tmp_path):
    world = {"folder": "/home/demo/private", "report_dir": str(tmp_path / "wout")}
    res = rd.collect(_sources(tmp_path), tmp_path / "m" / "2026-10", "2026-10", [world], [])
    assert res["worlds"][0]["digests"] == []
    assert {d["session_id"] for d in res["main"]} == set(EXP["sessions"])
