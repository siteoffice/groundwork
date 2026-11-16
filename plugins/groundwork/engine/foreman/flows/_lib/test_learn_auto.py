"""Gate rename D2: learnings run without a gate, a hold stays put. Pure pytest on
tmp_path, no clock, no network, no live flows."""
import json

import pytest

import flow_state as fs
import flow_transition as ft

T0 = "2026-11-03T09:00:00Z"
T1 = "2026-11-03T09:05:00Z"
T2 = "2026-11-03T09:10:00Z"


# G1 and G3 literals below are retired ids kept on purpose: they prove a stored old record still reads (OF30).
def _gate(gid, verdict=None, step=0, status=None):
    gate = {
        "id": gid,
        "step_index": step,
        "status": status or ("decided" if verdict else "awaiting-owner"),
        "decision": None,
        "notified": False,
        "opened_at": T0,
    }
    if step is None:
        gate["token"] = "-".join(["FLOW", "DSI", "learn", "G1", "20261103T090000Z", "7QF3"])
    else:
        gate["token"] = fs.gate_token("dsi", "learn", gid, step, T0, "7QF3")
    if verdict:
        gate["decision"] = {"verdict": verdict, "note": "", "surface": "session",
                            "decided_at": T1, "message_ref": None}
    return gate


def _flow_dir(tmp_path, slug, phase, index=0, of=2, gate=None):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "learn idea", now=T0, slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": i, "title": "step %d" % i,
                      "status": "running" if i == index else "pending"}
                     for i in range(of)]
    flow["current_step"] = {"index": index, "of": of, "phase": phase}
    flow["gate"] = gate
    ft.write_flow(flow_dir, flow)
    return flow_dir


def _log(flow_dir):
    return ft._read_flow_log(flow_dir)


def test_accept_ship_then_learn_runs_without_a_gate_through_both_steps(tmp_path):
    flow_dir = _flow_dir(tmp_path, "twostep", "sprint", index=0, of=2,
                         gate=_gate("accept", "ship"))
    flow, line = ft.cmd_advance(flow_dir, now=T1)
    assert flow["current_step"]["phase"] == "learn"
    assert flow["gate"] is None and line is None

    flow, line = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"] == {"index": 1, "of": 2, "phase": "grill"}
    assert flow["steps"][0]["status"] == "done"
    assert flow["steps"][1]["status"] == "running"
    assert flow["gate"] is None and line is None
    last = _log(flow_dir)[-1]
    assert (last["actor"], last["action"], last["outcome"]) == (
        "auto", "learn_auto", "info")
    assert last["detail"] == "learn step crossed, no learnings.md"

    # step 1 reaches learn the same way and the next crossing closes the flow
    flow = ft.load_flow(flow_dir)
    flow["current_step"]["phase"] = "learn"
    ft.write_flow(flow_dir, flow)
    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["stage"] == "closed" and flow["status"] == "closed"
    assert flow["steps"][1]["status"] == "done"
    assert _log(flow_dir)[-1]["detail"] == (
        "learn step crossed, no learnings.md")
    assert fs.validate(flow) == []
    assert not [e for e in _log(flow_dir) if e["action"] == "gate_opened"]


def test_learn_crossing_clears_the_spec_path_only_when_a_new_grill_starts(tmp_path):
    flow_dir = _flow_dir(tmp_path, "specclr", "learn", index=0, of=2)
    flow = ft.load_flow(flow_dir)
    flow["spec_path"] = "C:\\work\\x\\spec.md"
    ft.write_flow(flow_dir, flow)
    flow, _ = ft.cmd_advance(flow_dir, now=T1)
    assert "spec_path" not in flow or not flow.get("spec_path")


def test_a_pending_hold_makes_advance_refuse(tmp_path):
    flow_dir = _flow_dir(tmp_path, "held", "learn", index=0, of=2)
    driver = {"held": {"reason": "not yet", "ts": T1}}
    with pytest.raises(ValueError, match="hold is pending"):
        ft.cmd_advance(flow_dir, now=T2, driver=driver)
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "learn"


def test_advance_reads_foreman_json_read_only_when_no_driver_is_passed(tmp_path):
    flow_dir = _flow_dir(tmp_path, "heldfile", "learn", index=0, of=2)
    path = flow_dir / "foreman.json"
    path.write_text(json.dumps({"held": {"reason": "r", "ts": T1}}),
                    encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="hold is pending"):
        ft.cmd_advance(flow_dir, now=T2)
    assert path.read_bytes() == before

    path.write_text("{not json", encoding="utf-8")   # bad file reads as no hold
    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"]["phase"] == "grill"


def test_a_hold_verdict_consumed_at_accept_leaves_the_step_at_learn_held(tmp_path):
    flow_dir = _flow_dir(tmp_path, "holdflow", "sprint", index=0, of=2,
                         gate=_gate("accept", "hold"))
    driver = {}
    flow, _ = ft.cmd_advance(flow_dir, now=T1, driver=driver)
    assert flow["current_step"]["phase"] == "learn"
    assert driver["held"]
    with pytest.raises(ValueError):
        ft.cmd_advance(flow_dir, now=T2, driver=driver)


def test_a_retired_g3_gate_at_learn_is_dropped_and_the_log_says_so(tmp_path):
    flow_dir = _flow_dir(tmp_path, "g3drop", "learn", index=0, of=2,
                         gate=_gate("G3", status="awaiting-owner"))
    flow, _ = ft.cmd_advance(flow_dir, now=T1)
    assert flow["gate"] is None
    assert flow["current_step"]["phase"] == "grill"
    assert _log(flow_dir)[-1]["detail"].endswith("(retired gate G3 dropped)")


# A reopened accept sits at phase learn (foreman_run.cmd_ship clears held, then
# opens accept while the phase is still learn). Its verdict must be honoured.
def _reopened(tmp_path, slug, verdict, of=2):
    gate = _gate("accept", verdict)
    return _flow_dir(tmp_path, slug, "learn", index=0, of=of, gate=gate)


def test_reopened_accept_at_learn_park_pauses_and_does_not_advance(tmp_path):
    flow_dir = _reopened(tmp_path, "rpark", "park")
    flow, _ = ft.cmd_advance(flow_dir, now=T2, driver={})
    assert flow["status"] == "paused"
    assert flow["current_step"] == {"index": 0, "of": 2, "phase": "learn"}
    assert flow["steps"][0]["status"] == "running"
    assert flow["gate"] is None
    assert not [e for e in _log(flow_dir) if e["action"] == "learn_auto"]


def test_reopened_accept_at_learn_hold_records_pending_and_does_not_advance(tmp_path):
    flow_dir = _reopened(tmp_path, "rhold", "hold")
    driver = {}
    flow, _ = ft.cmd_advance(flow_dir, now=T2, driver=driver)
    assert driver["held"]
    assert flow["current_step"]["phase"] == "learn"
    assert flow["current_step"]["index"] == 0
    assert flow["gate"] is None
    assert not [e for e in _log(flow_dir) if e["action"] == "learn_auto"]
    with pytest.raises(ValueError, match="hold is pending"):
        ft.cmd_advance(flow_dir, now=T2, driver=driver)


def test_reopened_accept_at_learn_ship_proceeds_then_learn_crosses(tmp_path):
    flow_dir = _reopened(tmp_path, "rship", "ship")
    driver = {"shipped": {"sha": "x"}, "preflight": {"ok": True}}
    flow, _ = ft.cmd_advance(flow_dir, now=T1, driver=driver)
    assert flow["current_step"] == {"index": 0, "of": 2, "phase": "learn"}
    assert flow["gate"] is None
    assert "shipped" not in driver and "preflight" not in driver
    assert not [e for e in _log(flow_dir) if e["action"] == "learn_auto"]
    flow, _ = ft.cmd_advance(flow_dir, now=T2, driver=driver)
    assert flow["current_step"] == {"index": 1, "of": 2, "phase": "grill"}
    assert _log(flow_dir)[-1]["action"] == "learn_auto"


def test_reopened_accept_at_learn_clears_stale_driver_flags_on_hold(tmp_path):
    flow_dir = _reopened(tmp_path, "rflags", "hold")
    driver = {"shipped": {"sha": "x"}, "preflight": {"ok": True}, "ship": {"a": 1}}
    ft.cmd_advance(flow_dir, now=T2, driver=driver)
    assert "shipped" not in driver and "preflight" not in driver
    assert "ship" not in driver


def test_reopened_accept_at_learn_undecided_awaits(tmp_path):
    flow_dir = _reopened(tmp_path, "rwait", None)
    with pytest.raises(ValueError, match="awaiting decision"):
        ft.cmd_advance(flow_dir, now=T2, driver={})
    flow = ft.load_flow(flow_dir)
    assert flow["current_step"] == {"index": 0, "of": 2, "phase": "learn"}
    assert flow["gate"]["status"] == "awaiting-owner"
    assert flow["steps"][0]["status"] == "running"
    assert not [e for e in _log(flow_dir) if e["action"] == "learn_auto"]


def test_park_on_reopened_accept_at_learn_keeps_the_hold_across_resume(tmp_path):
    flow_dir = _reopened(tmp_path, "rparkres", "park")
    driver = {"shipped": {"sha": "x"}, "preflight": {"ok": True}}
    flow, _ = ft.cmd_advance(flow_dir, now=T1, driver=driver)
    assert flow["status"] == "paused" and flow["gate"] is None
    assert driver["held"]
    assert "shipped" not in driver and "preflight" not in driver

    flow = ft.cmd_resume(flow_dir, now=T2)
    with pytest.raises(ValueError, match="hold is pending"):
        ft.cmd_advance(flow_dir, now=T2, driver=driver)
    flow = ft.load_flow(flow_dir)
    assert flow["current_step"] == {"index": 0, "of": 2, "phase": "learn"}
    assert flow["steps"][0]["status"] == "running"
    assert not [e for e in _log(flow_dir) if e["action"] == "learn_auto"]

    # the ship reopen (cmd_ship clears held, opens accept) works a second time
    driver["held"] = None
    flow = ft.persist_open_gate(flow_dir, flow, "accept", 0, T2)
    assert flow["gate"]["status"] == "awaiting-owner"


# No driver passed (the /flow advance path): a hold or a learn-phase park is
# recorded only by the Foreman driver, so advance refuses rather than lose it.
def _bytes(flow_dir):
    base = __import__("pathlib").Path(flow_dir)
    return ((base / "flow.json").read_bytes(),
            (base / "flow-log.ndjson").read_bytes())


def test_advance_without_a_driver_refuses_a_hold_and_mutates_nothing(tmp_path):
    flow_dir = _flow_dir(tmp_path, "nodrvhold", "sprint", index=0, of=2,
                         gate=_gate("accept", "hold"))
    before = _bytes(flow_dir)
    for fn in (ft.cmd_advance, ft.cmd_advance_guarded):
        with pytest.raises(ValueError, match="foreman_run.py"):
            fn(flow_dir)
        assert _bytes(flow_dir) == before
    assert not (__import__("pathlib").Path(flow_dir) / "foreman.json").exists()


def test_advance_without_a_driver_refuses_a_second_hold_after_reopen(tmp_path):
    flow_dir = _flow_dir(tmp_path, "nodrvhold2", "learn", index=0, of=2,
                         gate=_gate("accept", "hold"))
    before = _bytes(flow_dir)
    with pytest.raises(ValueError, match="foreman_run.py"):
        ft.cmd_advance_guarded(flow_dir)
    assert _bytes(flow_dir) == before


def test_advance_without_a_driver_refuses_a_learn_phase_park(tmp_path):
    flow_dir = _reopened(tmp_path, "nodrvpark", "park")
    before = _bytes(flow_dir)
    with pytest.raises(ValueError, match="foreman_run.py"):
        ft.cmd_advance_guarded(flow_dir)
    assert _bytes(flow_dir) == before


def test_advance_without_a_driver_still_ships_and_parks_at_sprint(tmp_path):
    flow_dir = _flow_dir(tmp_path, "nodrvship", "sprint", index=0, of=2,
                         gate=_gate("accept", "ship"))
    flow, _ = ft.cmd_advance_guarded(flow_dir, now=T1)
    assert flow["current_step"]["phase"] == "learn"
    flow_dir = _flow_dir(tmp_path, "nodrvpark2", "sprint", index=0, of=2,
                         gate=_gate("accept", "park"))
    flow, _ = ft.cmd_advance_guarded(flow_dir, now=T1)
    assert flow["status"] == "paused"
    assert flow["current_step"]["phase"] == "sprint"
