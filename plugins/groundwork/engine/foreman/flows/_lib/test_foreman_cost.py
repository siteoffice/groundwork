"""Tests for foreman_cost.py. Plain pytest, stdlib only, hermetic.

Every case builds its own sessions/turns fixtures. The single test that reads
C:\\work\\50_projects\\burnrate\\ is a drift guard and skips when that repo is absent.
"""
import json
import pathlib
import re

import pytest

import foreman_cost as fc


def _session(session_id, cwd):
    return {"session_id": session_id, "cwd": cwd, "project": "hub"}


def _turn(session_id, request_id, model="claude-sonnet-5", role="assistant",
          input_tokens=1000, cache_creation_tokens=0, cache_read_tokens=0,
          output_tokens=1000):
    return {"session_id": session_id, "request_id": request_id, "role": role,
            "model": model, "input_tokens": input_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "output_tokens": output_tokens}


WT = r"C:\work\10_holding\.worktrees\sprint-x"


def test_model_family_maps_the_four_classes():
    assert fc.model_family("claude-opus-4-7") == "opus"
    assert fc.model_family("claude-sonnet-5") == "sonnet"
    assert fc.model_family("claude-haiku-4-5") == "haiku"
    assert fc.model_family("fable-5") == "fable-5"


def test_turn_cost_usd_prices_sonnet_at_the_published_rate():
    # 1e6 input at $2/MTok plus 1e6 output at $10/MTok
    assert fc.turn_cost_usd("claude-sonnet-5", 1_000_000, 0, 0, 1_000_000) == 12.0


def test_an_exact_model_id_is_priced_per_id():
    # 1e6 input plus 1e6 output: Opus 5 is 5 + 25, Opus 5.5 is 4 + 20
    assert fc.turn_cost_usd("claude-opus-5", 1_000_000, 0, 0, 1_000_000) == 30.0
    assert fc.turn_cost_usd("claude-opus-5-5", 1_000_000, 0, 0, 1_000_000) == 24.0
    assert fc.turn_cost_usd("claude-sonnet-5-5", 1_000_000, 0, 0, 1_000_000) == 12.0
    # cache read 0.50 and cache write 6.25 per MTok for Opus 5
    assert fc.turn_cost_usd("claude-opus-5", 0, 1_000_000, 1_000_000, 0) == 6.75
    assert fc.turn_cost_usd("CLAUDE-OPUS-5", 1_000_000, 0, 0, 0) == 5.0


def test_an_unknown_model_id_falls_back_to_its_family_price():
    # claude-opus-4-7 has no per-id price, so it is the opus family's 4/20
    assert fc.turn_cost_usd("claude-opus-4-7", 1_000_000, 0, 0, 1_000_000) == 24.0
    assert fc.turn_cost_usd("claude-fable-6", 1_000_000, 0, 0, 1_000_000) == 60.0
    assert fc.model_price("claude-opus-4-7") == fc.CATEGORY_PRICE["opus"]


def test_a_dated_snapshot_id_is_priced_as_its_base_id():
    assert fc.model_price("claude-opus-5-20260601") == fc.MODEL_PRICE["claude-opus-5"]
    assert fc.model_price("CLAUDE-SONNET-5-5-20261001") == fc.MODEL_PRICE["claude-sonnet-5-5"]


def test_a_dated_id_with_an_unlisted_base_falls_back_to_its_family():
    assert fc.model_price("claude-opus-4-7-20250101") == fc.CATEGORY_PRICE["opus"]


def test_a_version_number_is_not_mistaken_for_a_date():
    assert fc.model_price("claude-opus-5-5") == fc.MODEL_PRICE["claude-opus-5-5"]
    assert fc.model_price("claude-opus-5-5") != fc.MODEL_PRICE["claude-opus-5"]


def test_fable_5_1_is_priced_by_its_own_id_not_the_family():
    assert fc.model_price("claude-fable-5-1") == fc.MODEL_PRICE["claude-fable-5-1"]
    assert fc.model_price("claude-fable-5-1")[:2] == fc.CATEGORY_PRICE["fable-5"][:2]
    assert fc.turn_cost_usd("claude-fable-5-1", 0, 0, 1_000_000, 0) == 0.25
    assert fc.turn_cost_usd("claude-fable-6", 0, 0, 1_000_000, 0) == 1.0


def test_an_unknown_family_still_prices_as_sonnet():
    assert fc.turn_cost_usd("gpt-9", 1_000_000, 0, 0, 1_000_000) == 12.0
    assert fc.turn_cost_usd(None, 1_000_000, 0, 0, 1_000_000) == 12.0


def test_cost_for_worktree_prices_each_turn_by_its_own_model_id():
    sessions = [_session("a", WT)]
    turns = [_turn("a", "r1", model="claude-opus-5",
                   input_tokens=1_000_000, output_tokens=0),
             _turn("a", "r2", model="claude-opus-5-5",
                   input_tokens=1_000_000, output_tokens=0),
             _turn("a", "r3", model="claude-opus-4-7",
                   input_tokens=1_000_000, output_tokens=0)]
    assert fc.cost_for_worktree(WT, sessions, turns) == pytest.approx(5.0 + 4.0 + 4.0)


def test_dedup_turns_collapses_one_request_seen_twice():
    turns = [_turn("s", "req-1", output_tokens=100),
             _turn("s", "req-1", output_tokens=900)]
    kept = fc.dedup_turns(turns)
    assert len(kept) == 1
    assert kept[0]["output_tokens"] == 900


def test_session_ids_for_worktree_matches_case_insensitively():
    sessions = [_session("a", WT), _session("b", WT.lower()),
                _session("c", r"C:\work\other")]
    assert fc.session_ids_for_worktree(WT, sessions) == {"a", "b"}


def test_cost_for_worktree_sums_every_session_under_that_worktree():
    sessions = [_session("main", WT), _session("agent-1", WT)]
    turns = [_turn("main", "r1", model="claude-sonnet-5",
                   input_tokens=1_000_000, output_tokens=0),
             _turn("agent-1", "r2", model="claude-sonnet-5",
                   input_tokens=1_000_000, output_tokens=0)]
    assert fc.cost_for_worktree(WT, sessions, turns) == pytest.approx(4.0)


def test_cost_for_worktree_returns_none_when_no_session_matches():
    assert fc.cost_for_worktree(WT, [_session("a", r"C:\work\elsewhere")], []) is None


def test_cost_for_worktree_returns_none_for_a_missing_worktree_path():
    assert fc.cost_for_worktree("", [_session("a", WT)], []) is None


def test_cost_for_worktree_returns_zero_when_a_matched_session_has_no_turns():
    """Matched but silent is a known zero, not an unknown."""
    assert fc.cost_for_worktree(WT, [_session("a", WT)], []) == 0.0


def test_cost_for_worktree_ignores_user_turns():
    sessions = [_session("a", WT)]
    turns = [_turn("a", "r1", role="user", input_tokens=1_000_000)]
    assert fc.cost_for_worktree(WT, sessions, turns) == 0.0


def test_load_store_returns_two_empty_lists_when_the_db_dir_is_absent(tmp_path):
    assert fc.load_store(tmp_path / "nope") == ([], [])


def test_load_store_returns_two_empty_lists_on_malformed_json(tmp_path):
    (tmp_path / "sessions.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "turns.json").write_text("[]", encoding="utf-8")
    assert fc.load_store(tmp_path) == ([], [])


def test_load_store_returns_two_empty_lists_on_an_unreadable_file(tmp_path):
    """sessions.json exists but is a directory, not a file: read_text raises
    IsADirectoryError (a subclass of OSError), caught by the same handler
    that catches a permission error on a real unreadable file."""
    (tmp_path / "sessions.json").mkdir()
    (tmp_path / "turns.json").write_text("[]", encoding="utf-8")
    assert fc.load_store(tmp_path) == ([], [])


def test_sprint_cost_usd_reads_the_store_and_joins(tmp_path):
    (tmp_path / "sessions.json").write_text(
        json.dumps([_session("a", WT)]), encoding="utf-8")
    (tmp_path / "turns.json").write_text(json.dumps(
        [_turn("a", "r1", model="claude-sonnet-5",
               input_tokens=1_000_000, output_tokens=0)]), encoding="utf-8")
    sprint = {"worktree_path": WT}
    assert fc.sprint_cost_usd(sprint, tmp_path) == pytest.approx(2.0)


def test_sprint_cost_usd_is_none_without_a_sprint():
    assert fc.sprint_cost_usd(None, "anything") is None


def test_sprint_cost_usd_is_none_when_the_sprint_has_no_worktree_path(tmp_path):
    assert fc.sprint_cost_usd({"slug": "s"}, tmp_path) is None


def test_the_vendored_price_table_has_not_drifted_from_burnrate():
    """Drift guard for interpretation decision 10. Skips when BurnRate is not
    checked out on this machine, which is the whole point of vendoring."""
    src = pathlib.Path(r"C:\work\50_projects\burnrate\src\cowork_estimator.py")
    if not src.exists():
        pytest.skip("C:\\work\\50_projects\\burnrate is not present on this machine")
    text = src.read_text(encoding="utf-8")
    for family, prices in fc.CATEGORY_PRICE.items():
        assert '"{}"'.format(family) in text, family
        for price in prices:
            assert str(price) in text, (family, price)


def _doc_prices(label, text):
    """(input, output, cache_read, cache_write) for one model, read from
    BurnRate's pricing doc: the 2026-09-29 update paragraph when it names the
    model (it supersedes the August table), else the August table row ("<label> at 4/20 (cache write 5.00, cache
    read 0.20)"). None when the doc has neither."""
    flat = " ".join(text.split())
    upd = re.search(r"{}(?: and Sonnet 5\.5)? at (\d+)/(\d+) \(cache write ([\d.]+), "
                    r"cache read ([\d.]+)\)".format(re.escape(label)), flat)
    if upd:
        p_in, p_out, p_write, p_read = (float(x) for x in upd.groups())
        return (p_in, p_out, p_read, p_write)
    row = re.search(r"\| {} \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|"
                    .format(re.escape(label)), flat)
    if row:
        p_in, p_out, p_write, p_read = (float(x) for x in row.groups())
        return (p_in, p_out, p_read, p_write)
    return None


def test_the_vendored_model_price_table_has_not_drifted_from_burnrate():
    """Per-model-id drift guard: each MODEL_PRICE row against the doc label it
    was taken from. Skips when BurnRate is not on this machine."""
    doc = pathlib.Path(r"C:\work\50_projects\burnrate\05_docs\pricing_and_cost_basis.md")
    if not doc.exists():
        pytest.skip("C:\\work\\50_projects\\burnrate is not present on this machine")
    text = doc.read_text(encoding="utf-8")
    labels = {"claude-opus-5": "Opus 5", "claude-opus-5-5": "Opus 5.5",
              "claude-sonnet-5": "Sonnet 5", "claude-sonnet-5-5": "Sonnet 5"}
    # BurnRate prices Fable only as a family, so claude-fable-5-1 rests on BurnMon alone
    assert set(labels) == set(fc.MODEL_PRICE) - {"claude-fable-5-1"}
    for model_id, label in labels.items():
        assert _doc_prices(label, text) == pytest.approx(fc.MODEL_PRICE[model_id]), model_id
    # the one sourced id that differs from its family: Opus 5 is 5/25, not 4/20
    assert fc.MODEL_PRICE["claude-opus-5"][:2] == (5, 25)
    assert fc.MODEL_PRICE["claude-opus-5"] != fc.CATEGORY_PRICE["opus"]


def test_every_model_price_agrees_with_the_burnmon_book():
    """BurnMon is the per-model-id authority named in BurnRate's estimator.
    Skips when BurnMon is not on this machine."""
    book = pathlib.Path(r"C:\work\50_projects\burnmon\internal\pricing\books"
                        r"\anthropic_api.json")
    if not book.exists():
        pytest.skip("C:\\work\\50_projects\\burnmon is not present on this machine")
    models = json.loads(book.read_text(encoding="utf-8"))["models"]
    for model_id, prices in fc.MODEL_PRICE.items():
        rate = models[model_id]
        assert (rate["in"], rate["out"], rate["cache_read"],
                rate["cache_write_5m"]) == pytest.approx(prices), model_id
