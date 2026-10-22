"""Tests for render_guidance.py. Plain pytest, stdlib only, hermetic.

Every unit test injects its own guidance_root under tmp_path, so nothing here
depends on the content of the ten shipped fragments. The single real-file test
at the bottom reads the real directory on purpose: it proves the shipped
fragment and the renderer actually meet.
"""
import pathlib

import pytest

import render_guidance as rg

WHY = "G1 has no open gate and step 1 of 3 needs a G1 decision"

FLOW = {
    "flow_id": "dsi__payoff-books__20260718T091403Z",
    "project": "dsi",
    "slug": "payoff-books",
    "steps": [
        {"index": 0, "title": "shape the idea", "status": "done"},
        {"index": 1, "title": "build the reader", "status": "running"},
        {"index": 2, "title": "measure the payoff", "status": "pending"},
    ],
}


@pytest.fixture(autouse=True)
def _clear_road_ahead():
    """The road-ahead set is module-level and process-lived by design, so every
    test starts from a clean one rather than inheriting the previous test's."""
    rg._ROAD_AHEAD_SEEN.clear()
    yield
    rg._ROAD_AHEAD_SEEN.clear()


def _root(tmp_path, **fragments):
    root = tmp_path / "guidance"
    root.mkdir()
    for key, text in fragments.items():
        (root / (key + ".md")).write_text(text, encoding="utf-8")
    return root


# --- the four levels --------------------------------------------------------

def test_silent_renders_nothing(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    assert rg.render("gate_open.g1", "silent", WHY, guidance_root=root) == ""


def test_terse_renders_the_why_verbatim(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    assert rg.render("gate_open.g1", "terse", WHY, guidance_root=root) == WHY


def test_coached_renders_the_fragment_on_a_hit(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY\n"})
    assert rg.render("gate_open.g1", "coached", WHY,
                     guidance_root=root) == "FRAGMENT BODY"


def test_coached_falls_back_to_the_why_on_a_miss(tmp_path):
    root = _root(tmp_path)
    assert rg.render("halt.chain_cap", "coached", WHY,
                     guidance_root=root) == WHY


def test_the_four_levels_render_distinctly_for_one_input(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    rendered = [rg.render("gate_open.g1", level, WHY, flow=FLOW,
                          guidance_root=root)
                for level in ("silent", "terse", "coached", "teaching")]
    assert len(set(rendered)) == 4, rendered
    assert rendered[0] == ""
    assert rendered[1] == WHY
    assert rendered[2] == "FRAGMENT BODY"
    assert rendered[3].startswith("FRAGMENT BODY")
    assert len(rendered[3]) > len(rendered[2])


def test_an_unknown_level_is_treated_as_silent(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    assert rg.render("gate_open.g1", "loud", WHY, guidance_root=root) == ""


# --- the miss warning -------------------------------------------------------

def test_a_miss_logs_exactly_one_warning_line_and_never_raises(tmp_path, capsys):
    root = _root(tmp_path)
    rg.render("halt.chain_cap", "coached", WHY, guidance_root=root)
    err = capsys.readouterr().err
    assert len([ln for ln in err.splitlines() if ln.strip()]) == 1
    assert "render_guidance" in err
    assert "halt.chain_cap" in err


def test_a_hit_logs_no_warning(tmp_path, capsys):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    rg.render("gate_open.g1", "coached", WHY, guidance_root=root)
    assert capsys.readouterr().err == ""


def test_a_missing_guidance_root_directory_is_a_miss_not_a_crash(tmp_path):
    root = tmp_path / "does-not-exist"
    assert rg.render("gate_open.g1", "coached", WHY, guidance_root=root) == WHY


def test_a_key_that_would_escape_the_root_is_a_miss_not_a_read(tmp_path, capsys):
    """guidance_key reaches this function from NextAction and is composed
    straight into a path, so a key carrying a separator is refused rather than
    resolved."""
    root = _root(tmp_path)
    for bogus in ("../foreman_plan", "a/b", "a\\b", ""):
        assert rg.render(bogus, "coached", WHY, guidance_root=root) == WHY


def test_an_empty_fragment_file_falls_back_to_the_why(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "   \n\n"})
    assert rg.render("gate_open.g1", "coached", WHY, guidance_root=root) == WHY


# --- the teaching road-ahead block ------------------------------------------

def test_teaching_adds_the_road_ahead_block_the_first_time_only(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    first = rg.render("gate_open.g1", "teaching", WHY, flow=FLOW,
                      guidance_root=root)
    second = rg.render("gate_open.g1", "teaching", WHY, flow=FLOW,
                       guidance_root=root)
    third = rg.render("gate_await.g1", "teaching", WHY, flow=FLOW,
                      guidance_root=root)
    assert "road ahead" in first.lower()
    assert "build the reader" in first
    assert "road ahead" not in second.lower()
    assert "road ahead" not in third.lower()
    assert second == "FRAGMENT BODY"


def test_a_second_flow_id_gets_its_own_first_time_block(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    other = dict(FLOW, flow_id="znd__other-flow__20260801T090000Z",
                 steps=[{"index": 0, "title": "only step", "status": "pending"}])
    rg.render("gate_open.g1", "teaching", WHY, flow=FLOW, guidance_root=root)
    second_flow = rg.render("gate_open.g1", "teaching", WHY, flow=other,
                            guidance_root=root)
    assert "road ahead" in second_flow.lower()
    assert "only step" in second_flow
    assert rg._ROAD_AHEAD_SEEN == {FLOW["flow_id"], other["flow_id"]}


def test_teaching_on_a_miss_is_the_why_plus_the_block(tmp_path):
    root = _root(tmp_path)
    out = rg.render("halt.chain_cap", "teaching", WHY, flow=FLOW,
                    guidance_root=root)
    assert out.startswith(WHY)
    assert "road ahead" in out.lower()


def test_teaching_without_a_flow_is_just_the_fragment(tmp_path):
    """No flow_id means no key to dedupe on, so the block is skipped rather
    than printed on every single call."""
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    assert rg.render("gate_open.g1", "teaching", WHY, flow=None,
                     guidance_root=root) == "FRAGMENT BODY"
    assert rg.render("gate_open.g1", "teaching", WHY, flow={"steps": []},
                     guidance_root=root) == "FRAGMENT BODY"


def test_a_flow_with_no_steps_still_renders_a_block_once(tmp_path):
    root = _root(tmp_path, **{"gate_open.g1": "FRAGMENT BODY"})
    empty = {"flow_id": "znd__no-steps__20260801T090000Z", "steps": []}
    out = rg.render("gate_open.g1", "teaching", WHY, flow=empty,
                    guidance_root=root)
    assert "road ahead" in out.lower()
    assert "no steps recorded" in out.lower()


# --- against the real shipped fragments -------------------------------------

REAL_KEYS = (
    "gate_open.g1", "gate_open.g2", "gate_open.g3", "gate_open.g4",
    "gate_await.g1", "gate_await.g2", "gate_await.g3", "gate_await.g4",
    "sprint_launch", "ship_execute",
)


@pytest.mark.parametrize("key", REAL_KEYS)
def test_every_shipped_fragment_renders_at_coached_from_the_default_root(
        key, capsys):
    """Integration-shaped: no guidance_root injected, so this reads
    render_guidance.GUIDANCE_ROOT, the real directory Task 1 shipped. Proves
    the file exists, parses as plain text, and is not silently the fallback."""
    out = rg.render(key, "coached", WHY)
    assert out.strip()
    assert out != WHY
    assert capsys.readouterr().err == ""


def test_the_default_guidance_root_is_the_lib_directorys_guidance_folder():
    assert rg.GUIDANCE_ROOT == pathlib.Path(
        rg.__file__).resolve().parent / "guidance"
