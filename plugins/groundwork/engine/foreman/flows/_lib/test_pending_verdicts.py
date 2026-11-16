"""The five gates that were decided but unconsumed when the rename landed keep
consuming under the new engine. Fixtures are copied in shape from the live
flows, built on tmp_path; the live files are never read or written. Old G codes
stay as literals on purpose: these are stored records."""
import json

import pytest

import flow_state as fs
import flow_transition as ft
import foreman_plan as fp

T0 = "2026-11-02T09:00:00Z"
T1 = "2026-11-03T09:00:00Z"
T2 = "2026-11-03T09:05:00Z"


def _decided(gid, verdict, step):
    gate = {
        "id": gid, "step_index": step, "status": "decided",
        "decision": {"verdict": verdict, "note": "", "surface": "slack",
                     "decided_at": T1, "message_ref": None},
        "notified": True, "opened_at": T0,
    }
    gate["token"] = "-".join(["FLOW", "DSI", "pending", gid, "S%d" % step,
                             "20261102T090000Z", "7QF3"])
    return gate


def _fixture(tmp_path, slug, phase, of, index, gate, spec_path=None):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "pending verdict", now=T0,
                                slug=slug)
    flow["stage"] = "execute"
    flow["status"] = "running"
    flow["steps"] = [{"index": i, "title": "step %d" % i,
                      "status": "running" if i == index else "pending"}
                     for i in range(of)]
    flow["current_step"] = {"index": index, "of": of, "phase": phase}
    flow["gate"] = gate
    if spec_path:
        flow["spec_path"] = spec_path
    ft.write_flow(flow_dir, flow)
    return flow_dir


@pytest.mark.parametrize("slug,of,index", [
    ("g2-one-a", 1, 0), ("g2-one-b", 1, 0), ("g2-multi-a", 3, 0),
    ("g2-multi-b", 3, 1),
])
def test_a_decided_g2_approve_consumes_to_sprint_with_the_recorded_spec(
        tmp_path, slug, of, index):
    spec = "C:\\work\\10_holding\\docs\\superpowers\\specs\\%s.md" % slug
    flow_dir = _fixture(tmp_path, slug, "grill", of, index,
                        _decided("G2", "approve", index), spec_path=spec)
    flow = ft.load_flow(flow_dir)
    assert fs.validate(flow) == []
    action = fp.next_action(flow, None, {})
    assert (action.kind, action.gate_id) == ("gate_consume", "spec")

    flow, line = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"]["phase"] == "sprint"
    assert flow["current_step"]["index"] == index
    assert flow["gate"] is None
    assert line.startswith("/sprint dsi " + spec + " ")


def test_a_decided_g4_ship_waits_for_the_ship_then_consumes_then_learns(tmp_path):
    flow_dir = _fixture(tmp_path, "g4-ship", "sprint", 2, 0,
                        _decided("G4", "ship", 0))
    flow = ft.load_flow(flow_dir)
    assert fs.validate(flow) == []

    action = fp.next_action(flow, None, {})
    assert action.kind == "ship_execute"
    action = fp.next_action(flow, None, {"shipped": {"sha": "abc"}})
    assert (action.kind, action.gate_id) == ("gate_consume", "accept")

    flow, _ = ft.cmd_advance(flow_dir, now=T2, driver={"shipped": {"sha": "abc"}})
    assert flow["current_step"]["phase"] == "learn"
    assert flow["gate"] is None

    action = fp.next_action(flow, None, {})
    assert (action.kind, action.guidance_key) == (
        "flow_advance", "flow_advance.learn")
    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"] == {"index": 1, "of": 2, "phase": "grill"}
    last = ft._read_flow_log(flow_dir)[-1]
    assert (last["actor"], last["action"]) == ("auto", "learn_auto")

@pytest.mark.parametrize("gid,verdict,phase,gate_name", [
    ("G2", "approve", "grill", "spec"),
    ("G4", "ship", "sprint", "accept"),
])
def test_a_raw_old_coded_flow_json_is_recognised_as_a_pending_verdict(
        tmp_path, gid, verdict, phase, gate_name):
    # Written with plain json.dump, so the stored gate id stays the old code.
    flow_dir = _fixture(tmp_path, "raw-" + gid.lower(), phase, 1, 0,
                        _decided(gid, verdict, 0))
    flow = ft.load_flow(flow_dir)
    flow["gate"] = _decided(gid, verdict, 0)
    path = flow_dir / "flow.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(flow, fh)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["gate"]["id"] == gid

    loaded = ft.load_flow(flow_dir)
    assert fs.validate(loaded) == []
    action = fp.next_action(loaded, None, {"shipped": {"sha": "abc"}})
    assert (action.kind, action.gate_id) == ("gate_consume", gate_name)
