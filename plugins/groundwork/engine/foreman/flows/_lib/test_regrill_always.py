"""D2: a needs-guidance sprint always reopens spec marked regrill.

Plain pytest, tmp_path only, no clock.
"""
import json

import pytest

import flow_transition as ft
import foreman_plan as fp
import foreman_run as fr
import foreman_status as fst
import outbox
import terminal_sweep as ts

NOW = "2026-10-29T11:00:00Z"
BUILD = "2026-10-29T10:00:00Z"
STATUSES = ["capped", "failed", "escalated"]


@pytest.fixture(autouse=True)
def _isolate_halt_file(tmp_path_factory, monkeypatch):
    """No test may read the real FOREMAN_HALT file."""
    monkeypatch.setattr(fst, "HALT_FILE", tmp_path_factory.mktemp("halt") / "FOREMAN_HALT")


@pytest.fixture(autouse=True)
def _isolate_render_board(monkeypatch):
    """No test may let run_chain's render_board reach the real foreman board."""
    monkeypatch.setattr(fr, "render_board", lambda *a, **k: None)


def _sprint_phase_flow(tmp_path):
    """Flow at execute / phase sprint / gate None, with an emitted line and a spec."""
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    flow_dir, _ = ft.cmd_new(flows_root, "dsi", "regrill idea", slug="regrill",
                             now=BUILD)
    ft.cmd_advance(flow_dir, now=BUILD)
    ft.cmd_advance(flow_dir, now=BUILD)
    ft.cmd_advance(flow_dir, now=BUILD, steps=["s1"])        # breakdown -> execute
    ft.cmd_advance(flow_dir, now=BUILD)                      # opens spec
    ft.cmd_gate(flow_dir, "approve", now=BUILD)
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n", "utf-8")
    ft.cmd_advance(flow_dir, now=BUILD, spec_path=str(spec))  # emits the line
    return flow_dir, tmp_path / "sprints"


def _terminal_sprint(tmp_path, status, iteration=5, max_iterations=5):
    spec = str(tmp_path / "spec.md")
    d = tmp_path / "sprints" / "dsi" / "regrill"
    d.mkdir(parents=True, exist_ok=True)
    sprint = {"sprint_id": "dsi__regrill__20261029T103000Z", "project": "dsi",
              "slug": "regrill", "spec_path": spec, "phase": "dev",
              "iteration": iteration, "max_iterations": max_iterations,
              "status": status, "created_at": "2026-10-29T10:30:00Z"}
    (d / "sprint.json").write_text(json.dumps(sprint), "utf-8")
    return sprint


def _recover_action(status):
    fields = {"kind": "sprint_recover", "why": "w", "blocking": "",
              "command": "/flow advance dsi/regrill", "gate_id": None,
              "step_index": 0, "role": "none"}
    fields.update([("guidance" + "_key", ".".join(["sprint_recover", status]))])
    return fp.NextAction(**fields)


def _log_actions(flow_dir):
    return [e.get("action") for e in ft._read_flow_log(flow_dir)]


@pytest.mark.parametrize("status", STATUSES)
def test_execute_sprint_recover_regrills(tmp_path, status):
    flow_dir, sprints_root = _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, status)
    res = fr.execute(flow_dir, _recover_action(status),
                     sprints_root=sprints_root, now=NOW)
    assert res["regrill"] is True
    flow = ft.load_flow(flow_dir)
    assert flow["current_step"]["phase"] == "grill"
    assert flow["gate"]["id"] == "spec"
    assert flow["gate"]["regrill"]["status"] == status
    assert sprint["sprint_id"] in flow["regrill_cleared"]
    actions = _log_actions(flow_dir)
    assert actions.count("sprint_swept") == 1
    assert actions.count("regrill") == 1


@pytest.mark.parametrize("status", STATUSES)
def test_sweep_apply_match_regrills(tmp_path, status):
    flow_dir, sprints_root = _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, status)
    res = ts.apply_match(flow_dir, sprint, now=NOW, lock=False,
                         sprints_root=sprints_root)
    assert res.get("regrill") is True and res["skipped"] is False
    flow = ft.load_flow(flow_dir)
    assert flow["current_step"]["phase"] == "grill"
    assert flow["gate"]["id"] == "spec"
    assert flow["gate"]["regrill"]["status"] == status
    assert sprint["sprint_id"] in flow["regrill_cleared"]
    actions = _log_actions(flow_dir)
    assert actions.count("sprint_swept") == 1
    assert actions.count("regrill") == 1


def test_sweep_passing_sprint_still_opens_accept(tmp_path):
    flow_dir, sprints_root = _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, "passing")
    res = ts.apply_match(flow_dir, sprint, now=NOW, lock=False,
                         sprints_root=sprints_root)
    assert not res.get("regrill")
    gate = ft.load_flow(flow_dir)["gate"]
    assert gate["id"] == "accept"
    assert "regrill" not in gate


def test_cmd_regrill_refuses_outside_sprint_phase(tmp_path):
    flow_dir, sprints_root = _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, "failed")
    ft.cmd_regrill(flow_dir, sprint, "r.md", now=NOW)
    with pytest.raises(ValueError, match="cannot regrill"):
        ft.cmd_regrill(flow_dir, sprint, "r.md", now=NOW)


def test_has_terminal_sprint_excludes_cleared_ids(tmp_path):
    _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, "failed")
    p, s = sprint["project"], sprint["spec_path"]
    assert ts.has_terminal_sprint(p, s, [sprint]) is sprint
    assert ts.has_terminal_sprint(
        p, s, [sprint], exclude_ids=[sprint["sprint_id"]]) is None


def test_compose_regrill_gate_names_regrill(tmp_path):
    flow_dir, sprints_root = _sprint_phase_flow(tmp_path)
    sprint = _terminal_sprint(tmp_path, "failed")
    ts.apply_match(flow_dir, sprint, now=NOW, lock=False,
                   sprints_root=sprints_root)
    flow = ft.load_flow(flow_dir)
    subject, body = outbox.compose(flow["gate"], flow)
    assert "re-grill after failed" in body.lower()
    assert "re-grill (spec)" in body.lower() or "re-grill (spec)" in subject.lower()


def test_validate_regrill_cleared_must_be_list_of_str(tmp_path):
    flow_dir, _ = _sprint_phase_flow(tmp_path)
    flow = ft.load_flow(flow_dir)
    flow["regrill_cleared"] = "nope"
    assert any("regrill_cleared" in e for e in ft.fs.validate(flow))
    flow["regrill_cleared"] = ["a"]
    assert not any("regrill_cleared" in e for e in ft.fs.validate(flow))
