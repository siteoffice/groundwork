"""Tests for foreman_routing.py: the pure resolver (Task 1) and the CLI/render
section (Task 2) of the routing sprint.

Plain pytest, stdlib only, hermetic. The CLI cases build a real flow tree with
flow_transition.cmd_new the same way test_foreman_run.py does.
"""
import json
import pathlib

import pytest

import flow_transition as ft
import foreman_routing as fr
import model_map as mm

NOW = "2026-08-03T09:00:00Z"

NONE_ROLES = {
    "plan_next_action",
    "render_status",
    "render_guidance",
    "ship_preflight",
    "hub_refresh",
    "slack_compose",
}


def test_every_role_resolves_and_none_roles_never_get_a_model():
    table = fr.resolve_table({}, None)
    assert set(table) == set(fr.ROLES)
    for role in NONE_ROLES:
        routed = table[role]
        assert routed.cls == fr.NONE_CLASS
        assert routed.model is None


def test_resolve_table_with_no_hints_is_all_defaults():
    table = fr.resolve_table({}, None)
    for role in fr.ROLES:
        assert table[role].source == "default"
        assert table[role].cls == fr.ROLE_CLASS[role]


def test_model_hint_entry_overrides_default_with_source_hint():
    table = fr.resolve_table({"dev": "opus"}, None)
    assert table["dev"].source == "hint"
    assert table["dev"].cls == "opus"
    assert table["dev"].model == mm.resolve_model("opus")
    # every other role stays at its default
    for role in fr.ROLES:
        if role != "dev":
            assert table[role].source == "default"


def test_cli_hint_overrides_both_default_and_model_hint():
    table = fr.resolve_table({"dev": "opus"}, {"dev": "haiku"})
    assert table["dev"].source == "flag"
    assert table["dev"].cls == "haiku"
    assert table["dev"].model == mm.resolve_model("haiku")


def test_unknown_role_in_model_hint_raises_value_error_naming_role():
    with pytest.raises(ValueError, match="not_a_role"):
        fr.resolve_table({"not_a_role": "opus"}, None)


def test_unknown_role_in_cli_hint_raises_value_error_naming_role():
    with pytest.raises(ValueError, match="not_a_role"):
        fr.resolve_table({}, {"not_a_role": "opus"})


def test_unknown_class_value_raises_value_error():
    with pytest.raises(ValueError, match="not_a_class"):
        fr.resolve_table({"dev": "not_a_class"}, None)
    with pytest.raises(ValueError, match="not_a_class"):
        fr.resolve_table({}, {"dev": "not_a_class"})


def test_parse_hint_round_trips_and_rejects_malformed_pair():
    assert fr.parse_hint("breakdown=sonnet,grill=opus") == {
        "breakdown": "sonnet",
        "grill": "opus",
    }
    with pytest.raises(ValueError):
        fr.parse_hint("breakdown-sonnet")


def test_no_string_literal_in_source_contains_an_em_dash():
    import inspect

    source = inspect.getsource(fr)
    assert "\u2014" not in source


def test_role_class_reads_default_table_live_not_copied():
    for phase in mm.DEFAULT_TABLE:
        assert fr.ROLE_CLASS[phase] == mm.DEFAULT_TABLE[phase]


# --- Task 2: render_table -----------------------------------------------------

def test_render_table_class_column_never_dash_or_blank_for_none_roles():
    table = fr.resolve_table({}, None)
    text = fr.render_table(table)
    lines = text.splitlines()[1:]
    by_role = {line.split()[0]: line.split()[1] for line in lines}
    for role in fr.ROLES:
        if table[role].cls == fr.NONE_CLASS:
            assert by_role[role] == "none"
            assert by_role[role] not in ("-", "")


def test_render_table_model_column_is_literal_none_not_dash_for_none_roles():
    table = fr.resolve_table({}, None)
    text = fr.render_table(table)
    lines = text.splitlines()[1:]
    by_role = {line.split()[0]: line.split() for line in lines}
    for role in fr.ROLES:
        if table[role].cls == fr.NONE_CLASS:
            model_cell = by_role[role][2]
            assert model_cell == "None"


def test_render_table_source_column_differs_between_hinted_and_default_rows():
    table = fr.resolve_table({"dev": "opus"}, None)
    text = fr.render_table(table)
    rows = {line.split()[0]: line for line in text.splitlines()[1:]}
    assert "hint" in rows["dev"]
    assert "default" in rows["test"]
    assert rows["dev"] != rows["test"]


# --- Task 2: the CLI -----------------------------------------------------------

def _tree(tmp_path, project="dsi", slug="routing-flow"):
    flows_root = tmp_path / "flows"
    flows_root.mkdir(exist_ok=True)
    flow_dir, _flow = ft.cmd_new(flows_root, project, slug + " idea",
                                 slug=slug, now=NOW)
    return flows_root, flow_dir


def _log(flow_dir):
    path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_flow_for_ref_wraps_resolve_ref_and_reads_flow_json(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    flow = fr._load_flow_for_ref(
        "dsi/routing-flow", str(_flows_root), str(tmp_path / "sprints"))
    assert flow["project"] == "dsi"
    assert flow["slug"] == "routing-flow"


def test_load_flow_for_ref_raises_lookup_error_naming_the_absence_of_a_match(
        tmp_path):
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    with pytest.raises(LookupError, match="not-a-flow"):
        fr._load_flow_for_ref(
            "not-a-flow", str(flows_root), str(tmp_path / "sprints"))


def test_cli_routing_prints_all_roles_and_writes_no_receipt_or_driver_file(
        tmp_path, capsys):
    flows_root, flow_dir = _tree(tmp_path)
    log_before = _log(flow_dir)
    code = fr.main(["routing", "dsi/routing-flow",
                    "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints")])
    assert code == 0
    out = capsys.readouterr().out
    for role in fr.ROLES:
        assert role in out
    assert _log(flow_dir) == log_before
    assert not (pathlib.Path(flow_dir) / "foreman.json").exists()


def test_cli_routing_hint_flag_overrides_stdout_without_writing_model_hint(
        tmp_path, capsys):
    flows_root, flow_dir = _tree(tmp_path)
    code = fr.main(["routing", "dsi/routing-flow", "--hint", "dev=opus",
                    "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints")])
    assert code == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines()[1:]}
    assert "flag" in rows["dev"]
    assert "opus" in rows["dev"]
    flow = ft.load_flow(flow_dir)
    assert flow.get("model_hint", {}) == {}


def test_cli_routing_unresolvable_ref_prints_lookup_error_and_exits_nonzero(
        tmp_path, capsys):
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    code = fr.main(["routing", "not-a-flow",
                    "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints")])
    assert code != 0
    assert "not-a-flow" in capsys.readouterr().out


def test_cli_routing_unknown_hint_role_prints_value_error_and_no_partial_table(
        tmp_path, capsys):
    flows_root, _flow_dir = _tree(tmp_path)
    code = fr.main(["routing", "dsi/routing-flow", "--hint", "not_a_role=opus",
                    "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints")])
    assert code != 0
    out = capsys.readouterr().out
    assert "not_a_role" in out
    for role in fr.ROLES:
        assert role not in out
