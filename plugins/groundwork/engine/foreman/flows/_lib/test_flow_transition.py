"""Tests for flow_transition.py. Plain pytest, stdlib only, hermetic (tmp_path)."""
import json
import pathlib

import pytest

import flow_state as fs
import flow_transition as ft


def _read_log(flow_dir):
    lines = (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def test_new_flow_dict_is_schema_valid_at_idea_stage():
    flow = ft.new_flow("dsi", "payoff-books", "measure payoff",
                       "2026-07-18T09:14:03Z")
    assert flow["stage"] == "idea"
    assert flow["status"] == "running"
    assert flow["steps"] == []
    assert flow["gate"] is None
    assert fs.validate(flow) == []


def test_cmd_new_writes_valid_flow_json_and_logs_flow_created(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "measure the payoff of books",
                                now="2026-07-18T09:14:03Z")
    assert flow_dir == tmp_path / "dsi" / "measure-the-payoff-of-books"
    on_disk = json.loads((flow_dir / "flow.json").read_text(encoding="utf-8"))
    assert on_disk["stage"] == "idea"
    assert fs.validate(on_disk) == []
    log = _read_log(flow_dir)
    assert len(log) == 1
    assert log[0]["action"] == "flow_created"


def _execute_flow_with_step(tmp_path):
    """A flow parked in execute at step 2, ready for a G2 gate."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-18T09:00:00Z", slug="payoff")
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 2, "of": 4, "phase": "sprint"}
    flow["steps"] = [{"index": 2, "title": "build it", "status": "running"}]
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_persist_open_gate_writes_gate_and_logs_before_prompt(tmp_path):
    flow_dir = _execute_flow_with_step(tmp_path)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G2", 2,
                         now="2026-07-18T09:14:03Z", nonce="7QF3")
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["gate"]["id"] == "G2"
    assert on_disk["gate"]["status"] == "awaiting-owner"
    assert on_disk["gate"]["decision"] is None
    assert on_disk["gate"]["notified"] is False
    assert on_disk["gate"]["token"] == "FLOW-DSI-payoff-G2-S2-20260718T091403Z-7QF3"
    assert on_disk["status"] == "awaiting-owner"
    assert fs.validate(on_disk) == []
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "gate_opened"


def test_gate_decision_first_wins_second_ignored_and_logged(tmp_path):
    flow_dir = _execute_flow_with_step(tmp_path)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G2", 2,
                         now="2026-07-18T09:14:03Z", nonce="7QF3")

    ft.cmd_gate(flow_dir, "approve", surface="session",
                now="2026-07-18T09:20:00Z")
    after_first = ft.load_flow(flow_dir)
    assert after_first["gate"]["status"] == "decided"
    assert after_first["gate"]["decision"]["verdict"] == "approve"

    ft.cmd_gate(flow_dir, "reject", surface="email",
                now="2026-07-18T09:25:00Z", message_ref="<msg-2>")
    after_second = ft.load_flow(flow_dir)
    # First decision wins: still 'approve'.
    assert after_second["gate"]["decision"]["verdict"] == "approve"
    log = _read_log(flow_dir)
    ignored = [e for e in log if e["action"] == "reply_ignored"]
    assert ignored and "already decided" in ignored[-1]["detail"]


def test_cmd_gate_rejects_verdict_invalid_for_gate(tmp_path):
    flow_dir = _execute_flow_with_step(tmp_path)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G2", 2,
                         now="2026-07-18T09:14:03Z", nonce="7QF3")
    # 'proceed' is only valid for G3.
    import pytest
    with pytest.raises(ValueError):
        ft.cmd_gate(flow_dir, "proceed", now="2026-07-18T09:20:00Z")


def test_advance_into_sprint_step_emits_sprint_line_and_does_not_launch(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff", slug="payoff",
                                now="2026-07-18T09:00:00Z")
    # Park the flow in execute at a grill phase, with a model hint, gate decided.
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 1, "of": 3, "phase": "grill"}
    flow["model_hint"] = {"dev": "sonnet"}
    flow["gate"] = {"id": "G2", "step_index": 1, "status": "decided",
                    "decision": {"verdict": "approve", "note": "", "surface": "session",
                                 "decided_at": "2026-07-18T09:05:00Z", "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-18T10:00:00Z")

    assert updated["current_step"]["phase"] == "sprint"
    assert sprint_line is not None
    assert sprint_line.startswith("/sprint dsi ")
    assert "--model-map" in sprint_line
    # Never launched: the emitted line is the only artifact, logged as sprint_emitted.
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "sprint_emitted"
    assert log[-1]["detail"] == sprint_line


def test_advance_lightweight_stage_returns_no_sprint_line(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff", slug="payoff2",
                                now="2026-07-18T09:00:00Z")  # stage idea
    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-18T10:00:00Z")
    assert updated["stage"] == "design"
    assert sprint_line is None
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "advanced"


def test_advance_raises_when_gate_still_awaiting(tmp_path):
    flow_dir = _execute_flow_with_step(tmp_path)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G2", 2,
                         now="2026-07-18T09:14:03Z", nonce="7QF3")
    import pytest
    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-18T09:20:00Z")


def test_cmd_new_refuses_slug_collision(tmp_path):
    flow_dir, _ = ft.cmd_new(tmp_path, "proj", "first idea", slug="dup")
    with pytest.raises(FileExistsError) as exc:
        ft.cmd_new(tmp_path, "proj", "second idea", slug="dup")
    assert "already exists" in str(exc.value)
    assert ft.load_flow(flow_dir)["idea"] == "first idea"


def test_resume_clears_paused_status():
    flow = ft.new_flow("dsi", "payoff", "measure payoff", "2026-07-20T09:00:00Z")
    flow["status"] = "paused"
    resumed = ft.resume(flow, now="2026-07-20T09:05:00Z")
    assert resumed["status"] == "running"
    assert resumed["updated_at"] == "2026-07-20T09:05:00Z"


def test_resume_raises_when_not_paused():
    flow = ft.new_flow("dsi", "payoff", "measure payoff", "2026-07-20T09:00:00Z")
    with pytest.raises(ValueError):
        ft.resume(flow, now="2026-07-20T09:05:00Z")


def test_cmd_resume_persists_and_logs(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff", slug="payoff4",
                                now="2026-07-20T09:00:00Z")
    flow["status"] = "paused"
    ft.write_flow(flow_dir, flow)

    updated = ft.cmd_resume(flow_dir, now="2026-07-20T09:05:00Z")
    assert updated["status"] == "running"
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["status"] == "running"
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "resumed"


def test_cmd_resume_raises_when_flow_not_paused(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff", slug="payoff5",
                                now="2026-07-20T09:00:00Z")
    with pytest.raises(ValueError):
        ft.cmd_resume(flow_dir, now="2026-07-20T09:05:00Z")


def _breakdown_flow(tmp_path, slug="fx"):
    """A fresh flow parked at stage 'breakdown', gate null, ready for G1."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-20T09:00:00Z", slug=slug)
    flow["stage"] = "breakdown"
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_advance_at_breakdown_opens_g1_and_refuses_while_awaiting(tmp_path):
    flow_dir = _breakdown_flow(tmp_path, slug="g1open")
    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    assert updated["stage"] == "breakdown"
    assert sprint_line is None
    assert updated["gate"]["id"] == "G1"
    assert updated["gate"]["step_index"] is None
    assert updated["gate"]["status"] == "awaiting-owner"
    assert fs.validate(updated) == []
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "gate_opened"
    assert "G1" in log[-1]["detail"]

    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:06:00Z")


def test_advance_g1_approve_populates_steps_and_sets_execute(tmp_path):
    flow_dir = _breakdown_flow(tmp_path, slug="g1approve")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")  # opens G1
    ft.cmd_gate(flow_dir, "approve", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(
        flow_dir, now="2026-07-20T09:15:00Z",
        steps=["scaffold module", "wire tests", "ship"])

    assert sprint_line is None
    assert updated["stage"] == "execute"
    assert updated["gate"] is None
    assert [s["status"] for s in updated["steps"]] == ["running", "pending", "pending"]
    assert updated["current_step"] == {"index": 0, "of": 3, "phase": "grill"}
    assert fs.validate(updated) == []


def test_advance_g1_approve_without_steps_raises(tmp_path):
    flow_dir = _breakdown_flow(tmp_path, slug="g1nosteps")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "approve", now="2026-07-20T09:10:00Z")
    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")


def test_advance_g1_reject_returns_to_design(tmp_path):
    flow_dir = _breakdown_flow(tmp_path, slug="g1reject")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "reject", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["stage"] == "design"
    assert updated["gate"] is None
    assert updated["steps"] == []
    assert sprint_line is None


def test_advance_g1_park_pauses_and_resume_reopens_fresh_gate(tmp_path):
    flow_dir = _breakdown_flow(tmp_path, slug="g1park")
    first_open, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    first_token = first_open["gate"]["token"]
    ft.cmd_gate(flow_dir, "park", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["status"] == "paused"
    assert updated["stage"] == "breakdown"
    assert updated["gate"] is None
    assert sprint_line is None

    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:16:00Z")

    ft.cmd_resume(flow_dir, now="2026-07-20T09:20:00Z")
    reopened, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:25:00Z")
    assert reopened["status"] == "awaiting-owner"
    assert reopened["gate"]["id"] == "G1"
    assert reopened["gate"]["token"] != first_token


def _execute_flow_two_steps(tmp_path, slug="two"):
    """A flow at stage 'execute', step 0 of 2, phase grill, gate null."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-20T09:00:00Z", slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "step one", "status": "running"},
                     {"index": 1, "title": "step two", "status": "pending"}]
    flow["current_step"] = {"index": 0, "of": 2, "phase": "grill"}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def _execute_flow_at_learn(tmp_path, slug, index=0, of=2):
    """A flow at stage 'execute', current step at phase 'learn', gate null."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-20T09:00:00Z", slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": i, "title": f"step {i}",
                      "status": "running" if i == index else "pending"}
                     for i in range(of)]
    flow["current_step"] = {"index": index, "of": of, "phase": "learn"}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_advance_at_grill_opens_g2_and_refuses_while_awaiting(tmp_path):
    flow_dir = _execute_flow_two_steps(tmp_path, slug="g2open")
    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    assert updated["current_step"]["phase"] == "grill"
    assert sprint_line is None
    assert updated["gate"]["id"] == "G2"
    assert updated["gate"]["step_index"] == 0
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "gate_opened"

    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:06:00Z")


def test_advance_g2_approve_emits_sprint_line(tmp_path):
    flow_dir = _execute_flow_two_steps(tmp_path, slug="g2approve")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "approve", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["current_step"]["phase"] == "sprint"
    assert sprint_line is not None
    assert sprint_line.startswith("/sprint dsi ")
    log = _read_log(flow_dir)
    assert log[-1]["action"] == "sprint_emitted"


def test_advance_g2_reject_stays_in_grill_then_opens_fresh_g2(tmp_path):
    flow_dir = _execute_flow_two_steps(tmp_path, slug="g2reject")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    first_token = ft.load_flow(flow_dir)["gate"]["token"]
    ft.cmd_gate(flow_dir, "reject", now="2026-07-20T09:10:00Z")

    consumed, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert consumed["current_step"]["phase"] == "grill"
    assert consumed["gate"] is None
    assert sprint_line is None

    reopened, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:20:00Z")
    assert reopened["gate"]["id"] == "G2"
    assert reopened["gate"]["token"] != first_token


def test_advance_g2_park_pauses_and_resume_reopens_g2(tmp_path):
    flow_dir = _execute_flow_two_steps(tmp_path, slug="g2park")
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "park", now="2026-07-20T09:10:00Z")

    updated, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["status"] == "paused"
    assert updated["current_step"]["phase"] == "grill"

    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:16:00Z")

    ft.cmd_resume(flow_dir, now="2026-07-20T09:20:00Z")
    reopened, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:25:00Z")
    assert reopened["gate"]["id"] == "G2"


def test_advance_at_phase_sprint_opens_g4_rather_than_moving_straight_to_learn(tmp_path):
    """Slice c: the sprint boundary is gated now. This replaces
    test_advance_sprint_to_learn_has_no_gate, which asserted the
    pre-G4 unconditional transition that spec item 6 removes.
    """
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-20T09:00:00Z", slug="s2l")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "step one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    assert updated["gate"]["id"] == "G4"
    assert updated["gate"]["status"] == "awaiting-owner"
    assert updated["gate"]["step_index"] == 0
    assert updated["status"] == "awaiting-owner"
    assert updated["current_step"]["phase"] == "sprint"
    assert sprint_line is None


def test_advance_consumes_a_decided_g4_ship_into_phase_learn(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-07-20T09:00:00Z", slug="s2lship")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "step one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")          # opens G4
    ft.cmd_gate(flow_dir, "ship", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["current_step"]["phase"] == "learn"
    assert updated["status"] == "running"
    assert updated["gate"] is None
    assert sprint_line is None


def test_advance_g3_proceed_mid_flow_advances_to_next_step_grill(tmp_path):
    flow_dir = _execute_flow_at_learn(tmp_path, "g3proceed", index=0, of=2)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")  # opens G3
    ft.cmd_gate(flow_dir, "proceed", now="2026-07-20T09:10:00Z")

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["stage"] == "execute"
    assert updated["current_step"] == {"index": 1, "of": 2, "phase": "grill"}
    assert updated["steps"][0]["status"] == "done"
    assert updated["steps"][1]["status"] == "running"
    assert sprint_line is None


def test_advance_g3_proceed_on_last_step_closes_flow(tmp_path):
    flow_dir = _execute_flow_at_learn(tmp_path, "g3lastclose", index=1, of=2)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "proceed", now="2026-07-20T09:10:00Z")

    updated, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["stage"] == "closed"
    assert updated["status"] == "closed"
    assert updated["steps"][1]["status"] == "done"
    assert fs.validate(updated) == []


def test_advance_g3_adapt_reenters_grill_same_step(tmp_path):
    flow_dir = _execute_flow_at_learn(tmp_path, "g3adapt", index=0, of=2)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "adapt", now="2026-07-20T09:10:00Z")

    updated, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["current_step"] == {"index": 0, "of": 2, "phase": "grill"}
    assert updated["steps"][0]["status"] == "running"


def test_advance_g3_close_closes_flow_regardless_of_index(tmp_path):
    flow_dir = _execute_flow_at_learn(tmp_path, "g3directclose", index=0, of=2)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "close", now="2026-07-20T09:10:00Z")

    updated, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["stage"] == "closed"
    assert updated["status"] == "closed"


def test_advance_g3_park_pauses_and_resume_reopens_g3_same_step(tmp_path):
    flow_dir = _execute_flow_at_learn(tmp_path, "g3park", index=0, of=2)
    ft.cmd_advance(flow_dir, now="2026-07-20T09:05:00Z")
    ft.cmd_gate(flow_dir, "park", now="2026-07-20T09:10:00Z")

    updated, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:15:00Z")
    assert updated["status"] == "paused"
    assert updated["current_step"]["phase"] == "learn"

    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now="2026-07-20T09:16:00Z")

    ft.cmd_resume(flow_dir, now="2026-07-20T09:20:00Z")
    reopened, _ = ft.cmd_advance(flow_dir, now="2026-07-20T09:25:00Z")
    assert reopened["gate"]["id"] == "G3"
    assert reopened["current_step"]["index"] == 0


def test_full_happy_path_idea_to_closed_two_step_flow(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "ship the thing",
                                now="2026-07-20T09:00:00Z", slug="happy")
    now = "2026-07-20T09:00:00Z"

    updated, _ = ft.cmd_advance(flow_dir, now=now)          # idea -> design
    assert updated["stage"] == "design"
    updated, _ = ft.cmd_advance(flow_dir, now=now)          # design -> breakdown
    assert updated["stage"] == "breakdown"

    ft.cmd_advance(flow_dir, now=now)                       # opens G1
    ft.cmd_gate(flow_dir, "approve", now=now)
    updated, _ = ft.cmd_advance(flow_dir, now=now, steps=["alpha", "beta"])
    assert updated["stage"] == "execute"
    assert updated["current_step"] == {"index": 0, "of": 2, "phase": "grill"}

    for _ in (0, 1):
        ft.cmd_advance(flow_dir, now=now)                   # opens G2
        ft.cmd_gate(flow_dir, "approve", now=now)
        updated, sprint_line = ft.cmd_advance(flow_dir, now=now)  # grill -> sprint
        assert updated["current_step"]["phase"] == "sprint"
        assert sprint_line is not None

        ft.cmd_advance(flow_dir, now=now)                   # opens G4 (spec item 6)
        ft.cmd_gate(flow_dir, "ship", now=now)
        updated, _ = ft.cmd_advance(flow_dir, now=now)      # sprint -> learn
        assert updated["current_step"]["phase"] == "learn"

        ft.cmd_advance(flow_dir, now=now)                   # opens G3
        ft.cmd_gate(flow_dir, "proceed", now=now)
        updated, _ = ft.cmd_advance(flow_dir, now=now)      # consume G3

    assert updated["stage"] == "closed"
    assert updated["status"] == "closed"
    assert [s["status"] for s in updated["steps"]] == ["done", "done"]

    log = _read_log(flow_dir)
    gate_sequence = [e["detail"].split()[0] for e in log if e["action"] == "gate_opened"]
    assert gate_sequence == ["G1", "G2", "G4", "G3", "G2", "G4", "G3"]
    assert fs.validate(updated) == []


def test_emit_sprint_line_raises_on_non_phase_model_hint_key():
    """Backstop that should never fire once flow_state.validate is in place."""
    flow = ft.new_flow("dsi", "payoff", "measure payoff", "2026-07-25T09:00:00Z",
                       model_hint={"design": "opus"})
    with pytest.raises(ValueError) as exc:
        ft.emit_sprint_line(flow)
    assert "design" in str(exc.value)


# --- emit_sprint_line: always a complete map in DEFAULT_TABLE order --------

import model_map as mm  # noqa: E402  (testpaths puts sprints/_lib on the path)

FULL_DEFAULT_MAP = (
    "spec=opus,plan=opus,dev=sonnet,test=haiku,"
    "verify=opus,iterate=sonnet,report=haiku"
)


def _flow_for_emit(model_hint=None, spec_path=None):
    flow = ft.new_flow("dsi", "payoff-books", "measure payoff",
                       "2026-07-25T09:00:00Z", model_hint=model_hint)
    if spec_path:
        flow["spec_path"] = spec_path
    return flow


def _emitted_map(line):
    """The map string after the single ' --model-map ' flag."""
    assert " --model-map " in line
    return line.split(" --model-map ", 1)[1]


def test_emit_sprint_line_with_empty_hint_emits_the_full_default_map():
    line = ft.emit_sprint_line(_flow_for_emit({}))
    assert line.endswith(" --model-map " + FULL_DEFAULT_MAP)


def test_emit_sprint_line_with_missing_hint_key_emits_the_full_default_map():
    flow = _flow_for_emit({})
    del flow["model_hint"]
    assert ft.emit_sprint_line(flow).endswith(" --model-map " + FULL_DEFAULT_MAP)


def test_emit_sprint_line_with_partial_hint_emits_complete_merged_map():
    line = ft.emit_sprint_line(_flow_for_emit({"dev": "opus"}))
    assert _emitted_map(line) == (
        "spec=opus,plan=opus,dev=opus,test=haiku,"
        "verify=opus,iterate=sonnet,report=haiku"
    )


def test_emit_sprint_line_with_full_hint_emits_all_seven_overrides():
    hint = {phase: "haiku" for phase in mm.DEFAULT_TABLE}
    line = ft.emit_sprint_line(_flow_for_emit(hint))
    assert _emitted_map(line) == ",".join(
        f"{phase}=haiku" for phase in mm.DEFAULT_TABLE
    )


def test_emit_sprint_line_never_returns_a_bare_line():
    for hint in ({}, {"dev": "opus"}, {"spec": "haiku", "report": "opus"},
                 {phase: "sonnet" for phase in mm.DEFAULT_TABLE}):
        line = ft.emit_sprint_line(_flow_for_emit(hint))
        assert " --model-map " in line, f"bare line for hint {hint!r}"
        assert len(_emitted_map(line).split(",")) == len(mm.DEFAULT_TABLE)


def test_emit_sprint_line_serializes_in_default_table_order_not_alphabetically():
    keys = [pair.split("=")[0]
            for pair in _emitted_map(ft.emit_sprint_line(_flow_for_emit({}))).split(",")]
    assert keys == list(mm.DEFAULT_TABLE)
    assert keys != sorted(keys)   # the old behaviour was sorted()


def test_emit_sprint_line_is_byte_stable_for_a_given_hint():
    flow = _flow_for_emit({"dev": "opus"})
    assert ft.emit_sprint_line(flow) == ft.emit_sprint_line(flow)


def test_emitted_map_round_trips_through_model_map_parse():
    """The assertion that would have caught the stage-key defect."""
    for hint in ({}, {"dev": "opus"},
                 {phase: "haiku" for phase in mm.DEFAULT_TABLE}):
        line = ft.emit_sprint_line(_flow_for_emit(hint))
        parsed = mm.parse(_emitted_map(line))
        assert parsed == {**mm.DEFAULT_TABLE, **hint}
        assert set(parsed) == set(mm.DEFAULT_TABLE)


def test_emit_sprint_line_keeps_the_recorded_spec_path_with_spaces():
    spec = r"C:\work\10_holding\05_docs\superpowers\specs\a spec with spaces.md"
    line = ft.emit_sprint_line(_flow_for_emit({}, spec_path=spec))
    assert line == f"/sprint dsi {spec} --model-map {FULL_DEFAULT_MAP}"


# --- relaunch guard: emit_sprint_line(existing_terminal=...) ---------------

DSI_SPEC = r"C:\work\dsi\docs\superpowers\specs\payoff-books.md"


def _terminal_sprint(sprint_id="dsi__payoff-books__20260801T090000Z",
                     status="passing", spec_path=DSI_SPEC,
                     created_at="2026-08-01T09:00:00Z"):
    """A terminal sprint.json dict. `spec_path` must MATCH the flow's own
    spec_path for has_terminal_sprint to pair them, so a host test using a
    real on-disk spec (see _live_spec) has to pass that same path here.
    `created_at` defaults to the original fixed value, so every existing
    caller that omits it is unaffected; the sprint-not-terminal guard tests
    pass it explicitly to control staleness relative to a flow's
    sprint_emitted receipt.
    """
    return {"schema_version": 1, "sprint_id": sprint_id, "project": "dsi",
            "slug": "payoff-books", "spec_path": spec_path,
            "status": status, "created_at": created_at,
            "updated_at": created_at}


def test_emit_sprint_line_default_call_is_byte_identical_to_the_golden_line():
    """The no-guard-match case must be unchanged by the new parameter."""
    flow = _flow_for_emit({})
    assert ft.emit_sprint_line(flow) == (
        "/sprint dsi " + DSI_SPEC + " --model-map " + FULL_DEFAULT_MAP)
    # explicit None must behave exactly like omitting the argument
    assert ft.emit_sprint_line(flow, None) == ft.emit_sprint_line(flow)
    assert ft.emit_sprint_line(flow, existing_terminal=None) == \
        ft.emit_sprint_line(flow)


def test_emit_sprint_line_raises_relaunch_guard_naming_sprint_id_and_status():
    flow = _flow_for_emit({})
    sprint = _terminal_sprint()
    with pytest.raises(ft.RelaunchGuardError) as exc:
        ft.emit_sprint_line(flow, existing_terminal=sprint)
    message = str(exc.value)
    assert sprint["sprint_id"] in message
    assert "passing" in message


def test_emit_sprint_line_relaunch_guard_names_every_terminal_status():
    flow = _flow_for_emit({})
    for status in ("passing", "done", "capped", "failed", "escalated"):
        with pytest.raises(ft.RelaunchGuardError) as exc:
            ft.emit_sprint_line(flow,
                                existing_terminal=_terminal_sprint(status=status))
        assert status in str(exc.value)


def test_relaunch_guard_error_is_not_a_valueerror():
    """The guard must be distinguishable from the model_hint backstop, which
    raises ValueError, so a caller can catch exactly one of them."""
    assert issubclass(ft.RelaunchGuardError, RuntimeError)
    assert not issubclass(ft.RelaunchGuardError, ValueError)


def test_relaunch_guard_refuses_before_it_builds_anything():
    """Refuse-and-explain: no launch line is returned when a match is supplied,
    not even for a flow whose model_hint would otherwise raise ValueError."""
    flow = _flow_for_emit({})
    flow["model_hint"] = {"not_a_phase": "opus"}
    with pytest.raises(ft.RelaunchGuardError):
        ft.emit_sprint_line(flow, existing_terminal=_terminal_sprint())


# --- cmd_advance_guarded: the relaunch guard wired into a host caller ------
#
# Task 4 (2026-08-01 design, Resolved contract 2): the spec's own "host-side
# caller that already loads sprints from disk" did not exist anywhere in this
# repo, so cmd_advance_guarded is that caller. It wraps cmd_advance unchanged
# and adds a read-only has_terminal_sprint check afterward.

import terminal_sweep as ts  # noqa: E402  (testpaths puts flows/_lib on the path)


def _live_spec(tmp_path):
    """The spec path a host test's flow points at, as a real file on disk.

    cmd_advance_guarded stats the resolved spec path and refuses to emit a
    line naming a spec nobody wrote (SpecMissingError), so a HOST test needs a
    spec that genuinely exists. The pure emit_sprint_line tests above keep
    using the fabricated DSI_SPEC on purpose: that function never touches the
    filesystem, and pinning it to a real file would hide exactly that.
    """
    return str(tmp_path / "payoff-books.md")


def _live_flow_ready_to_emit(tmp_path, slug, spec_exists=True):
    """A flow on disk at execute/grill with a decided G2 'approve': the next
    cmd_advance_guarded call consumes G2, moves grill -> sprint, and emits a
    '/sprint dsi <_live_spec(tmp_path)> ...' line.

    The spec file is written for real unless spec_exists is False, which is
    how a test exercises the SpecMissingError refusal.
    """
    spec = pathlib.Path(_live_spec(tmp_path))
    if spec_exists:
        spec.write_text("# payoff books spec\n", encoding="utf-8")
    flow_dir, flow = ft.cmd_new(tmp_path / "flows", "dsi", "payoff books",
                                slug=slug, now="2026-08-01T09:00:00Z")
    flow["stage"] = "execute"
    flow["spec_path"] = str(spec)
    flow["current_step"] = {"index": 0, "of": 1, "phase": "grill"}
    flow["steps"] = [{"index": 0, "title": "build it", "status": "running"}]
    flow["gate"] = {"id": "G2", "step_index": 0, "status": "decided",
                    "decision": {"verdict": "approve", "note": "",
                                 "surface": "session",
                                 "decided_at": "2026-08-01T09:05:00Z",
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def _write_terminal_sprint_json(sprints_root,
                                sprint_id="dsi__payoff-books__20260801T090000Z",
                                status="passing", spec_path=DSI_SPEC,
                                created_at="2026-08-01T09:00:00Z"):
    sprint_dir = sprints_root / "dsi" / "payoff-books"
    sprint_dir.mkdir(parents=True, exist_ok=True)
    (sprint_dir / "sprint.json").write_text(
        json.dumps(_terminal_sprint(sprint_id=sprint_id, status=status,
                                    spec_path=spec_path, created_at=created_at)),
        encoding="utf-8")
    return sprint_dir


def _flow_at_sprint_phase(tmp_path, slug, sprints_root, spec_exists=True):
    """Drive a flow from grill to phase == 'sprint' via one real
    cmd_advance_guarded call (which emits and logs a sprint_emitted receipt
    at now='2026-08-01T09:10:00Z'), returning flow_dir. A second
    cmd_advance_guarded call on the returned flow_dir is what exercises the
    new sprint -> learn guard: the flow is now at exactly the state
    is_sweep_eligible expects (execute/sprint/gate=None).
    """
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug, spec_exists=spec_exists)
    ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                           now="2026-08-01T09:10:00Z")
    return flow_dir


def _gate_bypass_sprint_phase_flow(tmp_path, slug, sprints_root,
                                   opened_at="2026-08-01T09:10:00Z"):
    """A flow at execute/phase=sprint, like _flow_at_sprint_phase, but with a
    non-null gate hand-attached afterward.

    terminal_sweep.is_sweep_eligible requires gate is None; advance()'s own
    phase=="sprint" branch does not check gate at all. So this is the one
    shape of flow NoGenuineTerminalSprintError's pre-call guard never
    evaluates (is_sweep_eligible is False on the gate alone), while
    cmd_advance still performs the sprint -> learn crossing when called.

    The gate's status is deliberately "decided", not "awaiting-owner":
    advance() itself raises ValueError up front for an awaiting-owner gate
    (a separate, pre-existing guard), which would mask the one this test
    targets. A leftover decided gate that was never cleared is the
    realistic shape of staleness this represents.

    Only reachable in practice via a hand-edited flow.json or a bug
    elsewhere; exists here purely to exercise SprintNotTerminalError's
    backstop role. See the 2026-08-23 decision record:
    docs/superpowers/2026-08-23_proposal_terminal-sprint-guard-decision.md.
    """
    flow_dir = _flow_at_sprint_phase(tmp_path, slug, sprints_root)
    flow = ft.load_flow(flow_dir)
    assert flow["gate"] is None  # sanity: _flow_at_sprint_phase's own contract
    # verdict is "ship", not "approve": since the G4 ship gate landed,
    # advance()'s phase=="sprint" branch with a decided gate calls
    # _consume_g4, which only flips cs["phase"] to "learn" for a "ship" or
    # "hold" verdict. "approve" (the pre-G4 shape of this fixture) matches no
    # branch in _consume_g4 and leaves the crossing this test depends on from
    # ever happening.
    flow["gate"] = {
        "id": "G_STALE", "step_index": 0, "status": "decided",
        "decision": {"verdict": "ship", "note": "", "surface": "test"},
        "notified": True, "token": "STALE-0000", "opened_at": opened_at,
    }
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_cmd_advance_guarded_passthrough_when_no_sprint_line_emitted(tmp_path, monkeypatch):
    """Gate-opening transition (no sprint line): must return exactly what
    cmd_advance would, and must never even consult terminal_sweep."""
    flow_dir = _breakdown_flow(tmp_path, slug="guard-noemit")

    def _boom(*_a, **_k):
        raise AssertionError("terminal_sweep must not be consulted when no "
                             "sprint line is emitted")
    monkeypatch.setattr(ts, "discover_sprints", _boom)
    monkeypatch.setattr(ts, "has_terminal_sprint", _boom)

    plain_flow_dir = _breakdown_flow(tmp_path, slug="plain-noemit")
    expected, expected_line = ft.cmd_advance(plain_flow_dir,
                                             now="2026-08-01T09:05:00Z")

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, now="2026-08-01T09:05:00Z")

    assert sprint_line is None
    assert sprint_line == expected_line
    # Compare everything but the token (it embeds the slug, which differs
    # between the two flow dirs by construction).
    assert updated["gate"]["id"] == expected["gate"]["id"] == "G1"
    assert updated["gate"]["status"] == expected["gate"]["status"] == "awaiting-owner"
    assert updated["gate"]["step_index"] == expected["gate"]["step_index"]
    assert updated["stage"] == expected["stage"] == "breakdown"


def test_cmd_advance_guarded_passthrough_when_no_terminal_sprint_matches(tmp_path):
    """Sprint line emitted, but no sprint.json anywhere matches: passthrough."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-nomatch")
    sprints_root = tmp_path / "sprints"  # never created: no sprint.json exists

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:10:00Z")

    assert sprint_line is not None
    assert sprint_line.startswith("/sprint dsi " + _live_spec(tmp_path))
    assert updated["current_step"]["phase"] == "sprint"


def test_cmd_advance_guarded_raises_relaunch_guard_when_terminal_sprint_matches(tmp_path):
    """Sprint line emitted, a terminal sprint.json on disk matches (project,
    spec_path): raises RelaunchGuardError naming the sprint_id and status."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-match")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root,
                                sprint_id="dsi__payoff-books__20260801T090000Z",
                                status="passing",
                                spec_path=_live_spec(tmp_path))

    with pytest.raises(ft.RelaunchGuardError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-01T09:10:00Z")
    message = str(exc.value)
    assert "dsi__payoff-books__20260801T090000Z" in message
    assert "passing" in message


def test_cmd_advance_guarded_mutation_proof_guard_actually_fires(tmp_path, monkeypatch):
    """Mutation proof: if has_terminal_sprint is broken (always returns None,
    as it would if the guard were never wired in), the relaunch case above
    stops raising. This proves the guard genuinely depends on that check
    rather than always raising or never raising."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-mutation")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path))

    monkeypatch.setattr(ts, "has_terminal_sprint", lambda *a, **k: None)
    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:10:00Z")
    # With the check neutered, the guard is silent: this is the broken
    # behaviour the real (unpatched) test above proves does NOT happen.
    assert sprint_line is not None
    assert updated["current_step"]["phase"] == "sprint"


def _sweep_eligible_flow(tmp_path, slug, spec_path,
                         emitted_ts="2026-08-02T09:30:00Z"):
    """A flow on disk at execute/phase=sprint/gate=null (sweep-eligible), with
    a sprint_emitted log entry naming spec_path -- the exact precondition
    match_terminal_sprints requires before it will even look for a match.
    Mirrors test_foreman_run.py:594-610's construction.
    """
    flow_dir, flow = ft.cmd_new(tmp_path / "flows", "dsi", "payoff books",
                                slug=slug, now="2026-08-02T09:00:00Z")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    flow["spec_path"] = spec_path
    ft.write_flow(flow_dir, flow)
    ft.append_log(flow_dir, ft._log_entry(
        emitted_ts, "execute", "orchestrator", "sprint_emitted",
        detail="/sprint dsi " + spec_path + " --model-map dev=sonnet"))
    return flow_dir


def test_cmd_advance_guarded_refuses_when_no_sprint_json_matches_at_all(tmp_path):
    """Sweep-eligible flow, sprint_emitted receipt on disk, but NO sprint.json
    anywhere under sprints_root: refuses before cmd_advance runs, nothing
    mutated, nothing logged."""
    flow_dir = _sweep_eligible_flow(tmp_path, "guard-nothing",
                                    r"C:\work\dsi\docs\superpowers\specs\x.md")
    sprints_root = tmp_path / "sprints"  # never created

    before_log = _read_log(flow_dir)
    with pytest.raises(ft.NoGenuineTerminalSprintError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-02T09:35:00Z")
    message = str(exc.value)
    assert "dsi" in message
    assert "guard-nothing" in message

    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"
    assert _read_log(flow_dir) == before_log


def test_cmd_advance_guarded_crosses_sprint_to_learn_with_genuine_terminal_sprint(tmp_path):
    """Sweep-eligible flow with a genuine terminal sprint.json (terminal
    status, matching project+spec_path, created_at AFTER sprint_emitted, not
    swept): the pre-call guard does not fire, and cmd_advance_guarded opens
    G4 (not a direct crossing to phase=learn: since the G4 ship gate landed,
    a sprint reaching terminal status opens G4 first, and only a decided
    ship/hold verdict there crosses to learn). sprint_line is still None,
    because opening a gate never emits one."""
    spec_path = r"C:\work\dsi\docs\superpowers\specs\x.md"
    flow_dir = _sweep_eligible_flow(tmp_path, "guard-genuine", spec_path,
                                    emitted_ts="2026-08-02T09:30:00Z")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root,
                                sprint_id="dsi__x__20260802T093500Z",
                                status="passing", spec_path=spec_path)
    # _write_terminal_sprint_json's default created_at is baked into
    # _terminal_sprint: "2026-08-01T09:00:00Z", which PREDATES emitted_ts
    # above. Overwrite it directly so this sprint genuinely postdates the emit.
    sprint_dir = sprints_root / "dsi" / "payoff-books"
    sprint_json = json.loads((sprint_dir / "sprint.json").read_text(encoding="utf-8"))
    sprint_json["created_at"] = "2026-08-02T09:35:00Z"
    (sprint_dir / "sprint.json").write_text(json.dumps(sprint_json), encoding="utf-8")

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-02T09:40:00Z")

    assert updated["current_step"]["phase"] == "sprint"
    assert updated["gate"]["id"] == "G4"
    assert updated["gate"]["status"] == "awaiting-owner"
    assert sprint_line is None


def test_cmd_advance_guarded_refuses_when_terminal_sprint_predates_emit(tmp_path):
    """Only matching sprint.json is terminal but its created_at is BEFORE the
    flow's sprint_emitted ts: match_terminal_sprints excludes it (stale relaunch
    case), so the guard refuses, and the message carries the verbatim
    'predates_emit' detail string from diagnose_unmatched -- proving the
    message is genuinely sourced, not a generic string."""
    spec_path = r"C:\work\dsi\docs\superpowers\specs\x.md"
    flow_dir = _sweep_eligible_flow(tmp_path, "guard-stale", spec_path,
                                    emitted_ts="2026-08-02T09:30:00Z")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root,
                                sprint_id="dsi__x__20260801T090000Z",
                                status="passing", spec_path=spec_path)
    # _terminal_sprint's default created_at ("2026-08-01T09:00:00Z") already
    # predates emitted_ts ("2026-08-02T09:30:00Z"): left as-is on purpose.

    with pytest.raises(ft.NoGenuineTerminalSprintError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-02T09:40:00Z")
    message = str(exc.value)
    assert "dsi__x__20260801T090000Z" in message
    assert "stale run of an earlier launch" in message  # the diagnose_unmatched detail text


def test_cmd_advance_guarded_refuses_when_terminal_sprint_already_swept(tmp_path):
    """Only matching sprint.json is terminal and postdates the emit, but its
    sprint_id already appears in a sprint_swept log entry for this flow: the
    swept index excludes it from match_terminal_sprints, so the guard refuses."""
    spec_path = r"C:\work\dsi\docs\superpowers\specs\x.md"
    flow_dir = _sweep_eligible_flow(tmp_path, "guard-swept", spec_path,
                                    emitted_ts="2026-08-02T09:30:00Z")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root,
                                sprint_id="dsi__x__20260802T093500Z",
                                status="passing", spec_path=spec_path)
    sprint_dir = sprints_root / "dsi" / "payoff-books"
    sprint_json = json.loads((sprint_dir / "sprint.json").read_text(encoding="utf-8"))
    sprint_json["created_at"] = "2026-08-02T09:35:00Z"  # postdates the emit
    (sprint_dir / "sprint.json").write_text(json.dumps(sprint_json), encoding="utf-8")
    ft.append_log(flow_dir, ft._log_entry(
        "2026-08-02T09:36:00Z", "execute", "orchestrator", "sprint_swept",
        outcome="info", detail="dsi__x__20260802T093500Z status=passing",
        ref="dsi__x__20260802T093500Z"))

    with pytest.raises(ft.NoGenuineTerminalSprintError):
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-02T09:40:00Z")


def test_cmd_advance_guarded_precheck_never_runs_when_not_sweep_eligible(tmp_path, monkeypatch):
    """A flow at stage='breakdown' (G1 boundary, not phase='sprint'): the new
    pre-check must add zero terminal_sweep calls, proven by monkeypatching
    match_terminal_sprints and diagnose_unmatched to raise if called."""
    flow_dir = _breakdown_flow(tmp_path, slug="guard-notsweep")

    def _boom(*_a, **_k):
        raise AssertionError("pre-check must not run when is_sweep_eligible "
                             "is False")
    monkeypatch.setattr(ts, "match_terminal_sprints", _boom)
    monkeypatch.setattr(ts, "diagnose_unmatched", _boom)

    plain_flow_dir = _breakdown_flow(tmp_path, slug="plain-notsweep")
    expected, expected_line = ft.cmd_advance(plain_flow_dir,
                                             now="2026-08-02T09:05:00Z")
    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, now="2026-08-02T09:05:00Z")

    assert sprint_line == expected_line
    assert updated["gate"]["id"] == expected["gate"]["id"] == "G1"
    assert updated["stage"] == expected["stage"] == "breakdown"


def test_cmd_advance_guarded_mutation_proof_new_guard_actually_fires(tmp_path, monkeypatch):
    """Mutation proof: if match_terminal_sprints is broken (always returns a
    non-empty list, as it would if the guard were never wired in), the refusal
    in test 1 stops firing. This proves the guard genuinely depends on that
    check rather than always refusing or never refusing."""
    flow_dir = _sweep_eligible_flow(tmp_path, "guard-mutation",
                                    r"C:\work\dsi\docs\superpowers\specs\x.md")
    sprints_root = tmp_path / "sprints"  # still nothing on disk

    monkeypatch.setattr(ts, "match_terminal_sprints",
                        lambda *a, **k: [({"flow": "stub"}, {"sprint": "stub"})])
    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-02T09:40:00Z")

    # With the check neutered, the guard is silent: this is the broken
    # behaviour the real (unpatched) test 1 proves does NOT happen. G4 opens
    # (not a direct crossing to learn) since that gate now sits in between.
    assert updated["current_step"]["phase"] == "sprint"
    assert updated["gate"]["id"] == "G4"
    assert sprint_line is None


def test_no_genuine_terminal_sprint_error_is_a_distinct_runtimeerror(tmp_path):
    """Mirrors test_relaunch_guard_error_is_not_a_valueerror (line 617-621): a
    caller must be able to catch each guard independently."""
    assert issubclass(ft.NoGenuineTerminalSprintError, RuntimeError)
    assert ft.NoGenuineTerminalSprintError is not ft.RelaunchGuardError
    assert ft.NoGenuineTerminalSprintError is not ft.SpecMissingError


def test_cmd_advance_guarded_writes_nothing_beyond_plain_cmd_advance(tmp_path):
    """Even when the guard fires, no extra flow.json write or log entry is
    produced beyond what plain cmd_advance already writes for this
    transition: cmd_advance_guarded is refuse-and-explain only, never a
    second write."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-nowrite")
    sprints_root = tmp_path / "sprints"
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path))

    before_flow_json = (flow_dir / "flow.json").read_text(encoding="utf-8")
    log_before = _read_log(flow_dir)

    with pytest.raises(ft.RelaunchGuardError):
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-01T09:10:00Z")

    log_after = _read_log(flow_dir)
    # cmd_advance itself writes flow.json and appends exactly one
    # 'sprint_emitted' log line for this transition; the guard adds nothing
    # further to either file.
    assert len(log_after) == len(log_before) + 1
    assert log_after[-1]["action"] == "sprint_emitted"
    after_flow_json = (flow_dir / "flow.json").read_text(encoding="utf-8")
    assert after_flow_json != before_flow_json  # cmd_advance's own write happened
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["current_step"]["phase"] == "sprint"


# --- sprint-not-terminal guard: the sprint -> learn crossing ---------------
#
# 2026-08-23 spec (sprint-not-terminal-guard-design): cmd_advance_guarded's
# only guard logic ran when cmd_advance emitted a NEW /sprint line (the grill
# -> sprint boundary). When cmd_advance instead crosses phase == "sprint" ->
# "learn" (the inline, disk-free transition inside advance()), no sprint line
# is emitted, so the function returned (flow, None) with no check at all. A
# flow stuck at phase == "sprint" could be hand-advanced to "learn" (and then
# a fresh G3) by a hand-typed /flow advance even with no genuinely finished
# terminal sprint behind it. These tests exercise the new guard branch that
# closes that gap.
#
# 2026-08-23 decision (docs/superpowers/2026-08-23_proposal_terminal-sprint-guard-decision.md,
# verdict: keep both guards): main independently grew NoGenuineTerminalSprintError,
# a pre-call guard on the same is_sweep_eligible predicate, which now catches
# every normal (sweep-eligible) case in this section before cmd_advance ever
# runs, strictly better than this guard's post-mutation catch. The tests below
# that construct a normal sweep-eligible flow now expect
# NoGenuineTerminalSprintError instead; SprintNotTerminalError's own coverage
# moved to the gate-bypass tests, the one shape of flow (non-null gate at
# phase == "sprint", which advance() does not check) the pre-call guard
# cannot see.


def test_cmd_advance_guarded_raises_sprint_not_terminal_when_no_terminal_sprint_matches(tmp_path):
    """No sprint.json anywhere: the sprint -> learn crossing must refuse.

    Sweep-eligible flow (gate is None): NoGenuineTerminalSprintError's
    pre-call guard catches this before cmd_advance ever runs, so nothing is
    mutated. See the 2026-08-23 decision note above this section."""
    sprints_root = tmp_path / "sprints"
    flow_dir = _flow_at_sprint_phase(tmp_path, "sprint-notterm-nomatch", sprints_root)
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"

    with pytest.raises(ft.NoGenuineTerminalSprintError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                              now="2026-08-01T10:00:00Z")
    message = str(exc.value)
    assert "dsi" in message
    assert _live_spec(tmp_path) in message
    # Pre-call guard: refused before cmd_advance ran, nothing mutated.
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"


def test_cmd_advance_guarded_raises_sprint_not_terminal_when_only_stale_sprint_matches(tmp_path):
    """A terminal sprint exists for (project, spec_path), but its created_at
    PREDATES the current sprint_emitted receipt: plain has_terminal_sprint
    would wrongly find it, but the postdate check in match_terminal_sprints
    must still refuse. This is the test that proves the fix.

    Sweep-eligible flow (gate is None): same reasoning as the test above,
    NoGenuineTerminalSprintError's pre-call guard is what actually fires."""
    sprints_root = tmp_path / "sprints"
    flow_dir = _flow_at_sprint_phase(tmp_path, "sprint-notterm-stale", sprints_root)
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path),
                                created_at="2026-08-01T09:05:00Z")  # before 09:10

    with pytest.raises(ft.NoGenuineTerminalSprintError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                              now="2026-08-01T10:00:00Z")
    assert "2026-08-01T09:10:00Z" in str(exc.value)
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"


def test_cmd_advance_guarded_crosses_sprint_to_learn_when_a_new_terminal_sprint_matches(tmp_path):
    """A terminal sprint postdating sprint_emitted exists: the crossing goes
    through normally, no exception. It lands on G4 opened (awaiting-owner),
    not phase=learn directly, since the G4 ship gate now sits between a
    terminal sprint and learn."""
    sprints_root = tmp_path / "sprints"
    flow_dir = _flow_at_sprint_phase(tmp_path, "sprint-notterm-fresh", sprints_root)
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path),
                                created_at="2026-08-01T09:15:00Z")  # after 09:10

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T10:00:00Z")
    assert sprint_line is None
    assert updated["current_step"]["phase"] == "sprint"
    assert updated["gate"]["id"] == "G4"
    assert updated["gate"]["status"] == "awaiting-owner"


def test_cmd_advance_guarded_sprint_not_terminal_mutation_proof_guard_actually_fires(tmp_path, monkeypatch):
    """Mutation proof for SprintNotTerminalError's backstop role: with the
    pre-call guard bypassed (non-null gate at phase == sprint, the one shape
    is_sweep_eligible excludes), force match_terminal_sprints's result both
    ways and confirm the guard's raise genuinely tracks it, rather than
    always raising or never raising on this crossing."""
    sprints_root = tmp_path / "sprints"

    # A genuine, postdating match on disk; force match_terminal_sprints to
    # report none anyway: must still raise.
    flow_dir_a = _gate_bypass_sprint_phase_flow(tmp_path, "sprint-notterm-mut-a", sprints_root)
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path),
                                created_at="2026-08-01T09:15:00Z")
    monkeypatch.setattr(ts, "match_terminal_sprints", lambda *a, **k: [])
    with pytest.raises(ft.SprintNotTerminalError):
        ft.cmd_advance_guarded(flow_dir_a, sprints_root=str(sprints_root),
                              now="2026-08-01T10:00:00Z")

    # A different bypass flow, empty sprints root (nothing genuinely
    # matches); force match_terminal_sprints to report a match anyway: must
    # NOT raise.
    empty_root = tmp_path / "sprints_empty"  # never created
    flow_dir_b = _gate_bypass_sprint_phase_flow(tmp_path, "sprint-notterm-mut-b", empty_root)
    monkeypatch.setattr(ts, "match_terminal_sprints",
                        lambda *a, **k: [({}, {})])
    updated, _ = ft.cmd_advance_guarded(flow_dir_b, sprints_root=str(empty_root),
                                        now="2026-08-01T10:00:00Z")
    assert updated["current_step"]["phase"] == "learn"


def test_cmd_advance_guarded_sprint_not_terminal_writes_nothing_beyond_plain_cmd_advance(tmp_path):
    """Even when the backstop guard fires (pre-call guard bypassed via a
    non-null gate), no extra flow.json write or log entry is produced beyond
    what plain cmd_advance already writes for this crossing: refuse-and-
    explain only, never a second write."""
    sprints_root = tmp_path / "sprints"
    flow_dir = _gate_bypass_sprint_phase_flow(tmp_path, "sprint-notterm-nowrite", sprints_root)

    before_flow_json = (flow_dir / "flow.json").read_text(encoding="utf-8")
    log_before = _read_log(flow_dir)

    with pytest.raises(ft.SprintNotTerminalError):
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                              now="2026-08-01T10:00:00Z")

    log_after = _read_log(flow_dir)
    assert len(log_after) == len(log_before) + 1
    assert log_after[-1]["action"] == "advanced"
    after_flow_json = (flow_dir / "flow.json").read_text(encoding="utf-8")
    assert after_flow_json != before_flow_json
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["current_step"]["phase"] == "learn"


# --- regression: decided-G4 consumption must not false-positive ------------
#
# 2026-09-12 bug (docs/superpowers/2026-09-12_bug_cmd_advance_guarded_g4_false_positive.md):
# the post-crossing check above re-validated a G4 ship/hold consumption via
# match_terminal_sprints against the PRE-crossing flow snapshot, whose gate is
# a decided G4 dict (not None) by definition. is_sweep_eligible requires gate
# is None, so match_terminal_sprints always returned [] and the guard always
# raised SprintNotTerminalError on this path, even for a genuinely terminal
# sprint the sweep had already matched once (when it opened G4 in the first
# place). Fixed by skipping the re-check specifically when the pre-crossing
# gate is a decided dict with id "G4".

def test_cmd_advance_guarded_consumes_decided_g4_ship_without_raising_when_sprint_was_genuinely_terminal(tmp_path):
    """Full genuine sequence: G2 approve, sprint reaches terminal status, G4
    opens via the sweep, G4 decided ship, then cmd_advance_guarded consumes
    it. Must succeed (crossing to phase=learn) instead of raising
    SprintNotTerminalError, and the flow must be able to continue normally
    afterward (the next call opens G3)."""
    sprints_root = tmp_path / "sprints"
    flow_dir = _flow_at_sprint_phase(tmp_path, "g4-fix-happy", sprints_root)
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"

    # A genuine terminal sprint postdating the sprint_emitted receipt
    # (09:10:00Z, set by _flow_at_sprint_phase): the sweep can match it.
    _write_terminal_sprint_json(sprints_root, spec_path=_live_spec(tmp_path),
                                created_at="2026-08-01T09:15:00Z")

    opened_g4, _ = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:20:00Z")
    assert opened_g4["gate"]["id"] == "G4"
    assert opened_g4["gate"]["status"] == "awaiting-owner"

    ft.cmd_gate(flow_dir, "ship", now="2026-08-01T09:25:00Z")

    # This is the call the bug made raise SprintNotTerminalError unconditionally.
    consumed, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:30:00Z")
    assert consumed["current_step"]["phase"] == "learn"
    assert consumed["status"] == "running"
    assert consumed["gate"] is None
    assert sprint_line is None

    # The flow is not left stuck: the next call opens G3 normally.
    opened_g3, _ = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:35:00Z")
    assert opened_g3["gate"]["id"] == "G3"
    assert opened_g3["gate"]["status"] == "awaiting-owner"


def test_cmd_advance_guarded_still_raises_on_a_hand_typed_advance_past_an_unfinished_sprint(tmp_path):
    """Legitimate guard behavior untouched: a sweep-eligible flow (phase=sprint,
    gate=None) with no genuinely matching terminal sprint on disk must still
    refuse, exactly as before this fix. This is the shape the fix must NOT
    weaken: it has gate_before is None, never id=='G4', so it always goes
    through the (unchanged) re-check path."""
    sprints_root = tmp_path / "sprints"  # never created: no sprint.json anywhere
    flow_dir = _flow_at_sprint_phase(tmp_path, "g4-fix-stillraises", sprints_root)
    assert ft.load_flow(flow_dir)["gate"] is None

    with pytest.raises(ft.NoGenuineTerminalSprintError):
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-01T09:20:00Z")
    # Pre-call guard: refused before cmd_advance ran, nothing mutated.
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"


# --- spec_path and model_hint at creation (spec scope A) --------------------

SPEC_X = r"C:\work\10_holding\05_docs\superpowers\specs\2026-08-13-spec-x.md"
SPEC_Y = r"C:\work\10_holding\05_docs\superpowers\specs\2026-08-13-spec-y.md"

# The exact flow.json cmd_new writes today for this call, key order included.
# This is the byte-identity anchor for "no spec_path means no change at all".
EXPECTED_NO_SPEC_PATH = {
    "schema_version": 1,
    "flow_id": "dsi__payoff__20260718T091403Z",
    "project": "dsi",
    "slug": "payoff",
    "idea": "payoff books",
    "stage": "idea",
    "status": "running",
    "current_step": {"index": 0, "of": 0, "phase": None},
    "steps": [],
    "model_hint": {},
    "gate": None,
    "created_at": "2026-07-18T09:14:03Z",
    "updated_at": "2026-07-18T09:14:03Z",
}


def test_cmd_new_without_spec_path_writes_flow_json_byte_identically_to_today(tmp_path):
    flow_dir, _flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug="payoff",
                                 now="2026-07-18T09:14:03Z")
    text = (flow_dir / "flow.json").read_text(encoding="utf-8")
    assert text == json.dumps(EXPECTED_NO_SPEC_PATH, indent=2) + "\n"
    assert "spec_path" not in text


def test_cmd_new_with_spec_path_persists_it(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug="payoff",
                                now="2026-07-18T09:14:03Z", spec_path=SPEC_X)
    assert flow["spec_path"] == SPEC_X
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["spec_path"] == SPEC_X
    assert on_disk["updated_at"] == "2026-07-18T09:14:03Z"
    assert fs.validate(on_disk) == []
    # Criterion 1: the emitted line names the recorded spec, not the slug.
    assert ft.emit_sprint_line(on_disk) == (
        "/sprint dsi {} --model-map {}".format(SPEC_X, FULL_DEFAULT_MAP))


def test_cmd_new_with_model_hint_persists_it_and_reaches_the_emitted_map(tmp_path):
    """Proves the line-68 / line-106 passthrough gap is closed: new_flow always
    accepted model_hint, cmd_new never passed it."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug="payoff",
                                now="2026-07-18T09:14:03Z",
                                model_hint={"dev": "sonnet"})
    assert flow["model_hint"] == {"dev": "sonnet"}
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["model_hint"] == {"dev": "sonnet"}
    assert fs.validate(on_disk) == []
    emitted = _emitted_map(ft.emit_sprint_line(on_disk))
    assert "dev=sonnet" in emitted
    assert emitted == FULL_DEFAULT_MAP   # dev is sonnet in the default table too


def test_cmd_new_with_a_model_hint_that_overrides_a_default(tmp_path):
    """Criterion 3 with a value that actually differs from the default table,
    so the assertion cannot pass by accident."""
    flow_dir, _flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug="payoff",
                                 now="2026-07-18T09:14:03Z",
                                 model_hint={"dev": "opus"})
    emitted = _emitted_map(ft.emit_sprint_line(ft.load_flow(flow_dir)))
    assert emitted == (
        "spec=opus,plan=opus,dev=opus,test=haiku,"
        "verify=opus,iterate=sonnet,report=haiku")


# --- the G2 consume records spec_path through the same shared setter --------

def _g2_decided_flow(tmp_path, slug="payoff"):
    """A flow parked at execute/step 0/phase grill with a DECIDED G2 approve,
    so the next cmd_advance call consumes it. No spec_path recorded."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug=slug,
                                now="2026-07-18T09:00:00Z")
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 0, "of": 1, "phase": "grill"}
    flow["steps"] = [{"index": 0, "title": "only step", "status": "running"}]
    flow["gate"] = {"id": "G2", "step_index": 0, "status": "decided",
                    "decision": {"verdict": "approve", "note": "",
                                 "surface": "session",
                                 "decided_at": "2026-07-18T09:05:00Z",
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_g2_consume_records_spec_path_on_a_flow_created_without_one(tmp_path):
    flow_dir = _g2_decided_flow(tmp_path)
    assert "spec_path" not in ft.load_flow(flow_dir)

    updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-18T10:00:00Z",
                                          spec_path=SPEC_Y)

    assert updated["spec_path"] == SPEC_Y
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["spec_path"] == SPEC_Y
    assert on_disk["current_step"]["phase"] == "sprint"
    assert fs.validate(on_disk) == []
    # Recorded BEFORE the line is built, so the line names the new spec.
    assert sprint_line == (
        "/sprint dsi {} --model-map {}".format(SPEC_Y, FULL_DEFAULT_MAP))


def test_g2_consume_without_spec_path_is_unchanged(tmp_path):
    flow_dir = _g2_decided_flow(tmp_path)
    _updated, sprint_line = ft.cmd_advance(flow_dir, now="2026-07-18T10:00:00Z")
    assert "spec_path" not in ft.load_flow(flow_dir)
    fallback = r"C:\work\dsi\docs\superpowers\specs\payoff.md"
    assert sprint_line == (
        "/sprint dsi {} --model-map {}".format(fallback, FULL_DEFAULT_MAP))


def test_both_spec_path_write_points_call_the_one_shared_setter(tmp_path,
                                                                monkeypatch):
    """Spec test 5: cmd_new and the G2 consume must not each have their own
    write. Recording wrapper over the module-level name; both write points look
    it up at call time, so a private second write path would show up here as a
    missing entry."""
    calls = []
    real_setter = ft.set_spec_path

    def recording(flow, spec_path, now):
        calls.append((spec_path, now))
        return real_setter(flow, spec_path, now)

    monkeypatch.setattr(ft, "set_spec_path", recording)

    ft.cmd_new(tmp_path, "dsi", "created with a spec", slug="with-spec",
               now="2026-07-18T09:00:00Z", spec_path=SPEC_X)
    flow_dir = _g2_decided_flow(tmp_path, slug="recorded-at-g2")
    ft.cmd_advance(flow_dir, now="2026-07-18T10:00:00Z", spec_path=SPEC_Y)

    assert calls == [(SPEC_X, "2026-07-18T09:00:00Z"),
                     (SPEC_Y, "2026-07-18T10:00:00Z")]


def test_cmd_advance_guarded_records_spec_path_at_a_g2_approve(tmp_path):
    """Spec success criterion 2, proved through the production entry point.

    C:\\work\\10_holding\\04_engineering\\commands\\flow.md documents the
    /flow advance CLI path as calling ft.cmd_advance_guarded(<flow_dir>), not
    cmd_advance directly, so the G2 spec_path write point must be reachable
    through the guarded wrapper too, not only through cmd_advance itself.

    The spec is written for real because the guarded wrapper also refuses to
    emit a line naming a spec that is not on disk; here the subject under test
    is the recording, not the refusal."""
    flow_dir = _g2_decided_flow(tmp_path, slug="recorded-at-g2-guarded")
    assert "spec_path" not in ft.load_flow(flow_dir)
    sprints_root = tmp_path / "sprints"  # never created: no sprint.json exists
    spec_y = tmp_path / "2026-08-13-spec-y.md"
    spec_y.write_text("# spec y\n", encoding="utf-8")

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root),
        now="2026-07-18T10:00:00Z", spec_path=str(spec_y))

    assert updated["spec_path"] == str(spec_y)
    on_disk = ft.load_flow(flow_dir)
    assert on_disk["spec_path"] == str(spec_y)
    assert fs.validate(on_disk) == []
    assert sprint_line == (
        "/sprint dsi {} --model-map {}".format(spec_y, FULL_DEFAULT_MAP))


def test_cmd_advance_guarded_raises_spec_missing_when_the_spec_is_not_on_disk(tmp_path):
    """The host half of scope B: without this wiring SpecMissingError would be
    dead code, because nothing else in the repo computes the flag. This is the
    exact failure that wasted the start of the 2026-08-13 session, a flow
    emitting a line naming a spec nobody had written."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-nospec",
                                        spec_exists=False)
    sprints_root = tmp_path / "sprints"

    with pytest.raises(ft.SpecMissingError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(sprints_root),
                               now="2026-08-01T09:10:00Z")
    message = str(exc.value)
    assert "dsi" in message
    assert "guard-nospec" in message
    assert _live_spec(tmp_path) in message


def test_cmd_advance_guarded_does_not_raise_when_the_spec_does_exist(tmp_path):
    """Mutation proof for the guard above: the only difference between this
    test and the previous one is whether the spec file exists, so the guard
    genuinely depends on the stat rather than always firing."""
    flow_dir = _live_flow_ready_to_emit(tmp_path, slug="guard-hasspec",
                                        spec_exists=True)
    sprints_root = tmp_path / "sprints"

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(sprints_root), now="2026-08-01T09:10:00Z")

    assert sprint_line.startswith("/sprint dsi " + _live_spec(tmp_path))
    assert updated["current_step"]["phase"] == "sprint"


def test_cmd_advance_guarded_spec_guard_silent_when_no_sprint_line_emitted(tmp_path):
    """A gate-opening transition emits no line, so neither guard may fire even
    though this flow has no spec on disk at all."""
    flow_dir = _breakdown_flow(tmp_path, slug="guard-nospec-noemit")

    updated, sprint_line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(tmp_path / "sprints"),
        now="2026-08-01T09:05:00Z")

    assert sprint_line is None
    assert updated["gate"]["id"] == "G1"


# --- cross-step staleness: a new grill must not inherit the old spec --------
#
# Found by the blind verifier on 2026-08-13, and predicted in writing at line
# 32 of C:\work\10_holding\04_assets\hub_agent_update_2026-08-03_
# ZeroNonsense.dev_foreman-routing-reconciliation-and-shim-grill.md:
# spec_path is a flow-level singleton while the grilled artifact is per-step.
# Without clear_spec_path, step 1 emitted step 0's spec, and SpecMissingError
# could not catch it because step 0's spec exists on disk.


def _two_step_flow_at_learn_with_decided_g3(tmp_path, slug, spec_path,
                                            verdict="proceed"):
    """A 2-step flow on disk at execute/learn, step 0, with a decided G3 and
    `spec_path` recorded, so the next advance consumes G3 into step 1's grill.
    """
    flow_dir, flow = ft.cmd_new(tmp_path / "flows", "dsi", "two step",
                                slug=slug, now="2026-08-01T09:00:00Z",
                                spec_path=spec_path)
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 0, "of": 2, "phase": "learn"}
    flow["steps"] = [{"index": 0, "title": "step zero", "status": "running"},
                     {"index": 1, "title": "step one", "status": "pending"}]
    flow["gate"] = {"id": "G3", "step_index": 0, "status": "decided",
                    "decision": {"verdict": verdict, "note": "",
                                 "surface": "session",
                                 "decided_at": "2026-08-01T09:05:00Z",
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_g3_proceed_clears_spec_path_so_the_next_step_cannot_inherit_it(tmp_path):
    spec = tmp_path / "step-zero.md"
    spec.write_text("# step zero\n", encoding="utf-8")
    flow_dir = _two_step_flow_at_learn_with_decided_g3(
        tmp_path, "clears-on-proceed", str(spec))

    flow, _line = ft.cmd_advance(flow_dir, now="2026-08-01T09:10:00Z")

    assert flow["current_step"]["index"] == 1
    assert flow["current_step"]["phase"] == "grill"
    assert "spec_path" not in flow
    assert "spec_path" not in ft.load_flow(flow_dir)
    assert fs.validate(ft.load_flow(flow_dir)) == []


def test_g3_adapt_clears_spec_path_because_a_regrill_produces_a_new_spec(tmp_path):
    spec = tmp_path / "step-zero.md"
    spec.write_text("# step zero\n", encoding="utf-8")
    flow_dir = _two_step_flow_at_learn_with_decided_g3(
        tmp_path, "clears-on-adapt", str(spec), verdict="adapt")

    flow, _line = ft.cmd_advance(flow_dir, now="2026-08-01T09:10:00Z")

    assert flow["current_step"]["phase"] == "grill"
    assert "spec_path" not in flow


def test_step_one_does_not_emit_step_zeros_spec(tmp_path):
    """The whole point, end to end: after G3 proceed, a step-1 G2 approve that
    supplies no spec_path must NOT name step 0's spec. It falls back to the
    slug path, which does not exist, so the guard refuses instead of quietly
    launching against the wrong spec."""
    spec = tmp_path / "step-zero.md"
    spec.write_text("# step zero\n", encoding="utf-8")
    flow_dir = _two_step_flow_at_learn_with_decided_g3(
        tmp_path, "no-inherit", str(spec))
    ft.cmd_advance(flow_dir, now="2026-08-01T09:10:00Z")   # G3 -> step 1 grill

    flow = ft.load_flow(flow_dir)
    flow["gate"] = {"id": "G2", "step_index": 1, "status": "decided",
                    "decision": {"verdict": "approve", "note": "",
                                 "surface": "session",
                                 "decided_at": "2026-08-01T09:15:00Z",
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)

    _flow, line = ft.cmd_advance(flow_dir, now="2026-08-01T09:20:00Z")
    assert str(spec) not in line
    assert line.startswith(
        "/sprint dsi C:\\work\\dsi\\docs\\superpowers\\specs\\no-inherit.md")

    # And the host guard refuses that fallback, because it is not on disk.
    with pytest.raises(ft.SpecMissingError):
        ft.emit_sprint_line(ft.load_flow(flow_dir), spec_missing=True)


def test_g3_close_and_park_keep_spec_path_as_a_record(tmp_path):
    """Only a re-entry to grill clears it. A closed or parked flow keeps its
    last spec_path, and the terminal sweep still needs it through learn."""
    for verdict in ("close", "park"):
        spec = tmp_path / (verdict + ".md")
        spec.write_text("# spec\n", encoding="utf-8")
        flow_dir = _two_step_flow_at_learn_with_decided_g3(
            tmp_path, "keeps-" + verdict, str(spec), verdict=verdict)

        flow, _line = ft.cmd_advance(flow_dir, now="2026-08-01T09:10:00Z")

        assert flow["current_step"]["phase"] == "learn", verdict
        assert flow["spec_path"] == str(spec), verdict


# --- spec_missing: refuse to name a spec that is not on disk (scope B) ------

def test_emit_sprint_line_prefers_a_recorded_spec_path_over_the_slug_fallback():
    """Spec test 6: the slug is deliberately nothing like the spec stem."""
    flow = _flow_for_emit({}, spec_path=SPEC_X)
    flow["slug"] = "a-slug-that-matches-no-spec-file"
    line = ft.emit_sprint_line(flow)
    assert line == "/sprint dsi {} --model-map {}".format(SPEC_X, FULL_DEFAULT_MAP)
    assert "a-slug-that-matches-no-spec-file" not in line


def test_emit_sprint_line_still_returns_the_fallback_line_when_spec_path_is_null():
    """Spec test 7: the fallback is kept exactly as it is (non-goal 6). Both
    shapes of 'no spec' behave identically: key absent, and key set to None."""
    absent = _flow_for_emit({})
    assert "spec_path" not in absent
    expected = "/sprint dsi {} --model-map {}".format(DSI_SPEC, FULL_DEFAULT_MAP)
    assert ft.emit_sprint_line(absent) == expected

    explicit_null = _flow_for_emit({})
    explicit_null["spec_path"] = None
    assert ft.emit_sprint_line(explicit_null) == expected


def test_emit_sprint_line_spec_missing_raises_naming_project_slug_and_path():
    flow = _flow_for_emit({})
    with pytest.raises(ft.SpecMissingError) as exc:
        ft.emit_sprint_line(flow, spec_missing=True)
    message = str(exc.value)
    assert "dsi" in message
    assert "payoff-books" in message
    assert DSI_SPEC in message


def test_emit_sprint_line_spec_missing_names_a_recorded_spec_path_too():
    flow = _flow_for_emit({}, spec_path=SPEC_X)
    with pytest.raises(ft.SpecMissingError) as exc:
        ft.emit_sprint_line(flow, spec_missing=True)
    assert SPEC_X in str(exc.value)


def test_spec_missing_error_is_a_runtimeerror_and_not_a_valueerror():
    """Same discipline as RelaunchGuardError: catchable separately from the
    model_hint ValueError backstop."""
    assert issubclass(ft.SpecMissingError, RuntimeError)
    assert not issubclass(ft.SpecMissingError, ValueError)


def test_emit_sprint_line_spec_missing_false_is_identical_to_the_no_argument_call():
    """Spec test 9: the new parameter cannot change a single byte of the
    existing output."""
    for hint in ({}, {"dev": "opus"},
                 {phase: "haiku" for phase in mm.DEFAULT_TABLE}):
        flow = _flow_for_emit(hint)
        plain = ft.emit_sprint_line(flow)
        assert ft.emit_sprint_line(flow, spec_missing=False) == plain
        assert ft.emit_sprint_line(flow, None, False) == plain


def test_emit_sprint_line_spec_missing_writes_nothing_and_returns_nothing(tmp_path):
    """Refuse-and-explain: the raising call produces no line and no file."""
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books", slug="payoff",
                                now="2026-07-18T09:00:00Z")
    before = sorted(p.name for p in flow_dir.iterdir())
    with pytest.raises(ft.SpecMissingError):
        ft.emit_sprint_line(ft.load_flow(flow_dir), spec_missing=True)
    assert sorted(p.name for p in flow_dir.iterdir()) == before


# --- local_spec_path: the guard must not invert off Windows -----------------
#
# Carried as an UNVERIFIED residual out of the 2026-08-13 sprint and fixed
# 2026-08-13. _resolve_spec_path's fallback is the literal Windows path
# C:\work\<project>\docs\superpowers\specs\<slug>.md, because the emitted
# /sprint line is pasted into a Windows session by hand. Stat'ing that string
# on POSIX makes it one filename component, so .exists() is always False and
# the guard fires on EVERY flow using the fallback. The old suite could not
# catch it: every fixture spec lives under tmp_path, which is native.

WIN_FALLBACK_SPEC = r"C:\work\dsi\docs\superpowers\specs\nope.md"


def test_local_spec_path_is_the_identity_on_windows():
    """No translation on the platform the path is already written for."""
    assert ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=True) == (
        WIN_FALLBACK_SPEC)
    # And it does not consult the environment to decide that.
    assert ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=True,
                              dev_root="/nonexistent") == WIN_FALLBACK_SPEC


def test_local_spec_path_translates_a_dev_root_path_on_posix(tmp_path):
    """C:\\work\\<tail> becomes <dev_root>/<tail>, backslashes and all."""
    got = ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=False,
                             dev_root=str(tmp_path))
    assert got == str(tmp_path) + "/dsi/docs/superpowers/specs/nope.md"


def test_local_spec_path_is_case_insensitive_about_the_drive(tmp_path):
    """Windows paths arrive with either case; the prefix match must not care."""
    got = ft.local_spec_path(r"C:\work\dsi\x.md", is_windows=False,
                             dev_root=str(tmp_path))
    assert got == str(tmp_path) + "/dsi/x.md"


def test_local_spec_path_passes_a_native_posix_path_straight_through():
    """A POSIX absolute path is already stat-able: translating it would be the
    bug, not the fix. This is the shape every tmp_path fixture spec takes when
    the suite runs on Linux, which is why the guard keeps working there.

    Hardcoded rather than built from tmp_path on purpose: on Windows tmp_path
    is C:\\Users\\..., a drive-letter path, so this assertion would be about
    the wrong branch on the machine that matters most.
    """
    native = "/home/runner/work/specs/spec.md"
    assert ft.local_spec_path(native, is_windows=False) == native


def test_local_spec_path_abstains_when_no_dev_root_is_mounted():
    """The third outcome, and the reason the guard stopped inverting: with no
    mount behind C:\\work, a missing spec and a missing mount are the same
    observation, so this host must not claim either."""
    assert ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=False,
                              dev_root="/definitely/not/mounted/here") is None


def test_local_spec_path_abstains_for_a_windows_path_outside_dev(tmp_path):
    """Mapping D:\\ or C:\\Users would be a guess about this host's layout."""
    assert ft.local_spec_path(r"D:\other\tree\spec.md", is_windows=False,
                              dev_root=str(tmp_path)) is None
    assert ft.local_spec_path(r"C:\Users\someuser\spec.md",
                              is_windows=False, dev_root=str(tmp_path)) is None


def test_local_spec_path_reads_the_dev_root_from_the_environment(tmp_path, monkeypatch):
    """ZND_DEV_ROOT wins over the WSL default, same convention as
    ZND_ARMORY_PATH: the Cowork sandbox mounts C:\\work somewhere else."""
    monkeypatch.setenv(ft.POSIX_DEV_ROOT_ENV, str(tmp_path))
    assert ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=False) == (
        str(tmp_path) + "/dsi/docs/superpowers/specs/nope.md")
    monkeypatch.delenv(ft.POSIX_DEV_ROOT_ENV)
    assert ft.DEFAULT_POSIX_DEV_ROOT == "/mnt/c/work"


def _win_spec_flow_ready_to_emit(tmp_path, slug, spec_path):
    """A flow at execute/grill with a decided G2 approve, recording an
    arbitrary spec_path (here a Windows one), so the next guarded advance
    consumes G2, emits a line and runs the spec-missing check."""
    flow_dir, flow = ft.cmd_new(tmp_path / "flows", "dsi", "posix guard",
                                slug=slug, now="2026-08-01T09:00:00Z")
    flow["stage"] = "execute"
    flow["spec_path"] = spec_path
    flow["current_step"] = {"index": 0, "of": 1, "phase": "grill"}
    flow["steps"] = [{"index": 0, "title": "build it", "status": "running"}]
    flow["gate"] = {"id": "G2", "step_index": 0, "status": "decided",
                    "decision": {"verdict": "approve", "note": "",
                                 "surface": "session",
                                 "decided_at": "2026-08-01T09:05:00Z",
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_the_guard_raises_on_a_windows_path_that_is_missing_under_the_mount(
        tmp_path, monkeypatch):
    """End to end on the POSIX branch, forced regardless of the host OS: the
    translated path is checked, found absent, and the refusal still names the
    WINDOWS path, because that is the one an operator would have pasted."""
    # NOT monkeypatch.setattr(ft.os, "name", "posix"): pathlib dispatches on
    # os.name, so faking it on a Windows host makes every Path() raise
    # NotImplementedError and kills the whole pytest run (2026-08-13).
    monkeypatch.setattr(ft, "IS_WINDOWS", False)
    dev_root = tmp_path / "devroot"
    dev_root.mkdir()
    monkeypatch.setenv(ft.POSIX_DEV_ROOT_ENV, str(dev_root))
    flow_dir = _win_spec_flow_ready_to_emit(tmp_path, "posix-guard-missing",
                                            WIN_FALLBACK_SPEC)

    with pytest.raises(ft.SpecMissingError) as exc:
        ft.cmd_advance_guarded(flow_dir, sprints_root=str(tmp_path / "sprints"),
                               now="2026-08-01T09:10:00Z")
    assert WIN_FALLBACK_SPEC in str(exc.value)


def test_the_guard_does_not_raise_when_the_translated_path_is_there(
        tmp_path, monkeypatch):
    """Mutation proof for the test above AND for the inversion itself: same
    Windows path, same POSIX branch, only the file's existence under the mount
    differs. Before the fix this raised too, which is exactly the inversion."""
    # NOT monkeypatch.setattr(ft.os, "name", "posix"): pathlib dispatches on
    # os.name, so faking it on a Windows host makes every Path() raise
    # NotImplementedError and kills the whole pytest run (2026-08-13).
    monkeypatch.setattr(ft, "IS_WINDOWS", False)
    dev_root = tmp_path / "devroot"
    (dev_root / "dsi" / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (dev_root / "dsi" / "docs" / "superpowers" / "specs" / "nope.md").write_text(
        "# spec\n", encoding="utf-8")
    monkeypatch.setenv(ft.POSIX_DEV_ROOT_ENV, str(dev_root))
    flow_dir = _win_spec_flow_ready_to_emit(tmp_path, "posix-guard-present",
                                            WIN_FALLBACK_SPEC)

    _flow, line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(tmp_path / "sprints"),
        now="2026-08-01T09:10:00Z")

    assert line.startswith("/sprint dsi " + WIN_FALLBACK_SPEC + " --model-map")


def test_the_guard_abstains_when_the_host_cannot_check_at_all(
        tmp_path, monkeypatch):
    """No mount, so no verdict. The line is emitted rather than refused: an
    unverifiable guard must not block work it cannot actually judge."""
    # NOT monkeypatch.setattr(ft.os, "name", "posix"): pathlib dispatches on
    # os.name, so faking it on a Windows host makes every Path() raise
    # NotImplementedError and kills the whole pytest run (2026-08-13).
    monkeypatch.setattr(ft, "IS_WINDOWS", False)
    monkeypatch.setenv(ft.POSIX_DEV_ROOT_ENV, "/definitely/not/mounted/here")
    flow_dir = _win_spec_flow_ready_to_emit(tmp_path, "posix-guard-abstain",
                                            WIN_FALLBACK_SPEC)

    _flow, line = ft.cmd_advance_guarded(
        flow_dir, sprints_root=str(tmp_path / "sprints"),
        now="2026-08-01T09:10:00Z")

    assert line.startswith("/sprint dsi " + WIN_FALLBACK_SPEC + " --model-map")


def test_is_windows_is_the_seam_the_guard_reads():
    """The guard calls local_spec_path with no arguments, so IS_WINDOWS is what
    decides its branch. Pinned as a test because the obvious alternative,
    patching os.name, breaks pathlib on Windows: it raised
    NotImplementedError('cannot instantiate PosixPath on your system') inside
    pytest's own reporting and lost an entire run on 2026-08-13.
    """
    import os as _os
    assert ft.IS_WINDOWS == (_os.name == "nt")
    assert ft.local_spec_path(WIN_FALLBACK_SPEC, is_windows=True) == (
        WIN_FALLBACK_SPEC)


# --- slice c: _consume_g4 ----------------------------------------------------

def _g4_gate(verdict, note="", decided_at="2026-08-17T10:00:00Z"):
    return {
        "id": "G4", "step_index": 0, "status": "decided", "notified": True,
        "token": "FLOW-DSI-demo-G4-S0-20260817T095900Z-7QF3",
        "opened_at": "2026-08-17T09:59:00Z",
        "decision": {"verdict": verdict, "note": note, "surface": "session",
                     "decided_at": decided_at, "message_ref": None},
    }


def _sprint_phase_flow():
    return {
        "schema_version": 1, "flow_id": "dsi__demo__20260817T090000Z",
        "project": "dsi", "slug": "demo", "idea": "ship it",
        "stage": "execute", "status": "awaiting-owner",
        "current_step": {"index": 0, "of": 2, "phase": "sprint"},
        "steps": [{"index": 0, "title": "one", "status": "running"},
                  {"index": 1, "title": "two", "status": "pending"}],
        "model_hint": {}, "gate": None,
        "created_at": "2026-08-17T09:00:00Z",
        "updated_at": "2026-08-17T09:00:00Z",
    }


def test_consume_g4_ship_clears_the_gate_and_moves_the_step_to_learn():
    flow = _sprint_phase_flow()
    gate = _g4_gate("ship")
    cs = dict(flow["current_step"])
    flow, driver = ft._consume_g4(flow, gate, cs, {})
    assert flow["current_step"]["phase"] == "learn"
    assert flow["status"] == "running"
    assert flow["gate"] is None
    assert "held" not in driver


def test_consume_g4_hold_transitions_exactly_like_ship_and_marks_the_driver():
    flow = _sprint_phase_flow()
    gate = _g4_gate("hold", note="tests are flaky, look again tomorrow")
    cs = dict(flow["current_step"])
    flow, driver = ft._consume_g4(flow, gate, cs, {})
    assert flow["current_step"]["phase"] == "learn"
    assert flow["status"] == "running"
    assert flow["gate"] is None
    assert driver["held"]["reason"] == "tests are flaky, look again tomorrow"
    assert driver["held"]["ts"] == "2026-08-17T10:00:00Z"


def test_consume_g4_park_pauses_the_flow_and_leaves_the_phase_alone():
    flow = _sprint_phase_flow()
    flow, driver = ft._consume_g4(flow, _g4_gate("park"),
                                  dict(flow["current_step"]), {})
    assert flow["status"] == "paused"
    assert flow["current_step"]["phase"] == "sprint"
    assert flow["gate"] is None
    assert "held" not in driver


def test_consume_g4_tolerates_a_driver_of_none():
    flow = _sprint_phase_flow()
    flow, driver = ft._consume_g4(flow, _g4_gate("hold"),
                                  dict(flow["current_step"]), None)
    assert isinstance(driver, dict)
    assert "held" in driver


def test_consume_g4_ship_clears_preflight_and_shipped_and_ship_from_the_driver():
    flow = _sprint_phase_flow()
    driver = {"preflight": {"clean_tree": True}, "shipped": True,
              "ship": {"merge_sha": "abc123"}}
    flow, driver = ft._consume_g4(flow, _g4_gate("ship"),
                                  dict(flow["current_step"]), driver)
    assert "preflight" not in driver
    assert "shipped" not in driver
    assert "ship" not in driver


def test_consume_g4_hold_also_clears_preflight_and_shipped_and_ship_from_the_driver():
    flow = _sprint_phase_flow()
    driver = {"preflight": {"clean_tree": True}, "shipped": True,
              "ship": {"merge_sha": "abc123"}}
    flow, driver = ft._consume_g4(flow, _g4_gate("hold", note="not yet"),
                                  dict(flow["current_step"]), driver)
    assert "preflight" not in driver
    assert "shipped" not in driver
    assert "ship" not in driver
    assert driver["held"]["reason"] == "not yet"


def test_consume_g4_park_leaves_preflight_and_shipped_and_ship_untouched():
    flow = _sprint_phase_flow()
    driver = {"preflight": {"clean_tree": True}, "shipped": True,
              "ship": {"merge_sha": "abc123"}}
    flow, driver = ft._consume_g4(flow, _g4_gate("park"),
                                  dict(flow["current_step"]), driver)
    assert driver["preflight"] == {"clean_tree": True}
    assert driver["shipped"] is True
    assert driver["ship"] == {"merge_sha": "abc123"}


def test_consume_g4_ship_with_no_preexisting_driver_state_still_works():
    flow = _sprint_phase_flow()
    driver = {}
    flow, driver = ft._consume_g4(flow, _g4_gate("ship"),
                                  dict(flow["current_step"]), driver)
    assert isinstance(driver, dict)
    assert "preflight" not in driver
    assert "shipped" not in driver
    assert "ship" not in driver


def test_advance_threads_the_callers_driver_dict_through_a_g4_hold(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-08-17T09:00:00Z", slug="g4hold")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-08-17T09:05:00Z")           # opens G4
    ft.cmd_gate(flow_dir, "hold", note="not yet", now="2026-08-17T09:10:00Z")

    driver = {}
    updated, _line = ft.cmd_advance(flow_dir, now="2026-08-17T09:15:00Z",
                                    driver=driver)
    assert updated["current_step"]["phase"] == "learn"
    assert driver["held"]["reason"] == "not yet"


def test_cmd_advance_without_a_driver_argument_still_consumes_a_g4_ship(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-08-17T09:00:00Z", slug="g4nodrv")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-08-17T09:05:00Z")
    ft.cmd_gate(flow_dir, "ship", now="2026-08-17T09:10:00Z")

    updated, _line = ft.cmd_advance(flow_dir, now="2026-08-17T09:15:00Z")
    assert updated["current_step"]["phase"] == "learn"


def test_cmd_gate_rejects_a_g3_verdict_while_g4_is_open(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-08-17T09:00:00Z", slug="g4verd")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-08-17T09:05:00Z")           # opens G4

    with pytest.raises(ValueError):
        ft.cmd_gate(flow_dir, "proceed", now="2026-08-17T09:10:00Z")


def test_after_a_g4_ship_the_next_advance_opens_g3_as_normal(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-08-17T09:00:00Z", slug="g4theng3")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-08-17T09:05:00Z")
    ft.cmd_gate(flow_dir, "ship", now="2026-08-17T09:10:00Z")
    ft.cmd_advance(flow_dir, now="2026-08-17T09:15:00Z")           # -> learn

    updated, _line = ft.cmd_advance(flow_dir, now="2026-08-17T09:20:00Z")
    assert updated["gate"]["id"] == "G3"


def test_a_g4_park_pauses_the_flow_end_to_end(tmp_path):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "payoff books",
                                now="2026-08-17T09:00:00Z", slug="g4park")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now="2026-08-17T09:05:00Z")
    ft.cmd_gate(flow_dir, "park", now="2026-08-17T09:10:00Z")

    updated, _line = ft.cmd_advance(flow_dir, now="2026-08-17T09:15:00Z")
    assert updated["status"] == "paused"
    assert updated["gate"] is None
