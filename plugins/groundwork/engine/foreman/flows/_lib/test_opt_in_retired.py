"""opt_in_gates is read and ignored (G1 and G3 retired). tmp_path only."""
import json
import logging

import pytest

import flow_state as fs
import flow_transition as ft
import foreman_run as fr

NOW = "2026-11-03T10:00:00Z"
LINE = "opt_in_gates ignored: G1 and G3 retired"


def _load(tmp_path, text):
    path = tmp_path / "operator.json"
    if text is not None:
        path.write_text(text, "utf-8")
    return fr.load_operator(path)


def _ignore_lines(caplog):
    return [r for r in caplog.records if LINE in r.getMessage()]


def test_operator_with_g1_g3_loads_and_logs_the_ignore_line_once(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="flow_state"):
        op = _load(tmp_path, '{"opt_in_gates": ["G1", "G3"], "completions": 2}')
    assert op["completions"] == 2
    assert len(_ignore_lines(caplog)) == 1


@pytest.mark.parametrize("text", ['{"opt_in_gates": []}', '{"completions": 1}', None])
def test_operator_empty_or_missing_key_loads_and_logs_nothing(tmp_path, caplog, text):
    with caplog.at_level(logging.WARNING, logger="flow_state"):
        op = _load(tmp_path, text)
    assert "completions" in op
    assert _ignore_lines(caplog) == []


@pytest.mark.skip(reason="Groundwork ships no operator.json")
def test_operator_json_in_the_repo_has_no_opt_in_gates_key():
    data = json.loads(fr.OPERATOR_PATH.read_text("utf-8"))
    assert "opt_in_gates" not in data


def test_defaults_do_not_add_the_key(tmp_path):
    assert "opt_in_gates" not in _load(tmp_path, "{}")


@pytest.mark.parametrize("value", [["G1", "G3"], [], "junk", None])
def test_validate_accepts_any_opt_in_value(value):
    flow = dict(ft.new_flow("p", "s", "idea", NOW), opt_in_gates=value)
    assert fs.validate(flow) == []


def test_read_opt_in_gates_returns_empty_and_logs_once_per_call(caplog):
    with caplog.at_level(logging.WARNING, logger="flow_state"):
        assert fs.read_opt_in_gates({"opt_in_gates": ["G1"]}) == ()
        assert fs.read_opt_in_gates({"opt_in_gates": []}) == ()
        assert fs.read_opt_in_gates({}) == ()
        assert fs.read_opt_in_gates(None) == ()
        assert fs.read_opt_in_gates({"opt_in_gates": "G1"}) == ()
    assert len(_ignore_lines(caplog)) == 2
    assert LINE == fs.OPT_IN_IGNORED_LINE


def test_flow_state_drops_the_old_opt_in_symbols():
    for name in ("OPTIONAL_GATES", "MANDATORY_GATES", "GATE_IDS",
                 "_check_opt_in_ids", "resolve_opt_in_gates"):
        assert not hasattr(fs, name)
