"""Task 9 (D4): the first spec card opens only after spec_lint PASS and shows the breakdown."""
import flow_state as fs
import flow_transition as ft
import foreman_plan as fp
import foreman_run as fr
import outbox
import slack_channel
import pytest
import spec_lint

NOW = "2026-11-03T12:00:00Z"
STEPS = ["build the parser", "wire the parser in", "document it"]


@pytest.fixture(autouse=True)
def _dev_root(tmp_path, monkeypatch):
    proj = tmp_path / "dev" / "50_projects" / "demoproj"
    proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(spec_lint, "DEV_ROOT", tmp_path / "dev")


def _spec(path, good=True, project="demoproj"):
    lines = [
        "# Spec: demo feature", "", "Source: docs/roadmap.md lines 1-5", "Run mode: Unattended", "",
        "## Problem", "The thing is broken.", "", "## Scope (locked)", "Fix the thing.", "",
        "## New tests (required pattern)", "A test that checks the thing.", "",
        "## Verification", "pytest -q is green.", "", "## Explicitly out of scope", "Everything else.", ""]
    if good:
        lines += ["## Success criteria", "The thing works.", ""]
    lines += ["## /sprint invocation", "", "```", "/sprint %s %s" % (project, path), "```", ""]
    path.write_text("\n".join(lines), "utf-8")


def _flow_at_grill(tmp_path, spec, steps=STEPS, index=0):
    flow_dir, flow = ft.cmd_new(tmp_path / "flows", "dsi", "payoff books", now=NOW)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": i, "title": t, "status": "pending"} for i, t in enumerate(steps)]
    flow["current_step"] = {"index": index, "of": len(steps), "phase": "grill"}
    flow["spec_path"] = str(spec)
    ft.write_flow(flow_dir, flow)
    return flow_dir


def _bytes(flow_dir):
    return ((flow_dir / "flow.json").read_bytes(),
            (flow_dir / "flow-log.ndjson").read_bytes())


def test_failing_spec_refuses_advance_and_writes_nothing(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec, good=False)
    flow_dir = _flow_at_grill(tmp_path, spec)
    before = _bytes(flow_dir)
    with pytest.raises(ft.SpecLintFailError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert str(exc.value).startswith("spec_lint FAIL: ")
    assert _bytes(flow_dir) == before


def test_missing_spec_refuses_with_path(tmp_path):
    flow_dir = _flow_at_grill(tmp_path, tmp_path / "nope.md")
    with pytest.raises(ft.SpecLintFailError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert str(exc.value).startswith("spec missing: ")


def test_driver_stops_spec_precondition_on_fail(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec, good=False)
    flow_dir = _flow_at_grill(tmp_path, spec)
    flow = ft.load_flow(flow_dir)
    ok, detail = ft.first_spec_precondition(flow)
    assert not ok
    action = fp.next_action(flow, None, {})
    assert (action.kind, fs.canonical_gate(action.gate_id)) == ("gate_open", "spec")
    facts = fr.host_facts(flow, [], {}, precondition_detail=detail)
    decision = fr.decide(action, facts)
    assert (decision.execute, decision.reason) == (False, "spec_precondition")
    assert detail in decision.detail
    assert detail.startswith("spec_lint FAIL")
    assert "spec_precondition" in fr.STOP_REASONS
    assert "spec_precondition" in fr._HOST_FENCE_REASONS


def test_passing_spec_opens_spec_gate_with_breakdown(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec, good=True)
    flow_dir = _flow_at_grill(tmp_path, spec)
    assert ft.first_spec_precondition(ft.load_flow(flow_dir)) == (True, "spec_lint PASS")
    flow, _line = ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert flow["gate"]["id"] == "spec" and flow["gate"]["step_index"] == 0
    _subject, body = outbox.compose(flow["gate"], flow)
    assert "Breakdown:\n" in body
    for i, title in enumerate(STEPS):
        assert "%d. %s\n" % (i + 1, title) in body


def test_one_step_flow_prints_one_step(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec)
    flow_dir = _flow_at_grill(tmp_path, spec, steps=["only"])
    flow, _ = ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert "Breakdown: one step\n" in outbox.compose(flow["gate"], flow)[1]


def test_later_step_is_not_gated(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec, good=False)
    flow_dir = _flow_at_grill(tmp_path, spec, index=1)
    assert ft.first_spec_precondition(ft.load_flow(flow_dir)) == (True, "")
    flow, _ = ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert flow["gate"]["id"] == "spec" and flow["gate"]["step_index"] == 1
    assert "Breakdown" not in outbox.compose(flow["gate"], flow)[1]


def test_slack_gate_prompt_carries_the_breakdown(tmp_path):
    spec = tmp_path / "spec.md"
    _spec(spec, good=True)
    flow_dir = _flow_at_grill(tmp_path, spec)
    flow, _ = ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"), now=NOW)
    assert "Breakdown:\n" in slack_channel.compose_gate_prompt(flow["gate"], flow)
