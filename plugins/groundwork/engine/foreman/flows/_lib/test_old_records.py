"""Gate rename D3: a flow written before the rename (G codes in flow.json, the
flow log and the outbox) still reads, renders, answers and advances. The log
is append-only, so its original bytes stay an exact prefix after a full
advance. Pure pytest on tmp_path, no clock, no network, no live flows."""
import json

import flow_state as fs
import flow_transition as ft
import foreman_status as fst
import outbox  # noqa: F401  (old-token outbox entries are plain dicts here)
import render_flows as rf
import sweep_core as sc

T0 = "2026-10-20T09:00:00Z"
T1 = "2026-10-20T09:05:00Z"
T2 = "2026-11-03T09:10:00Z"
OLD_TOKEN = "-".join(["FLOW", "DSI", "legacy", "G2", "S0", "20261020T090000Z", "7QF3"])


OLD_G1_TOKEN = "-".join(["FLOW", "DSI", "legacy", "G1", "20261020T090000Z", "7QF3"])


def _old_gate(gid="G2", step=0, verdict="approve", tok=OLD_TOKEN):
    gate = {"id": gid, "step_index": step,
            "status": "decided" if verdict else "awaiting-owner",
            "decision": None, "notified": True, "token": tok,
            "opened_at": T0}
    if verdict:
        gate["decision"] = {"verdict": verdict, "note": "", "surface": "email",
                            "decided_at": T1, "message_ref": "<m1>"}
    return gate


def _old_flow_dir(tmp_path, slug="legacy", gate=None, phase="grill",
                  steps_of=1):
    flow_dir, flow = ft.cmd_new(tmp_path, "dsi", "legacy idea", now=T0,
                                slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": i, "title": "step %d" % i, "status": "running"
                      if i == 0 else "pending"} for i in range(steps_of)]
    flow["current_step"] = {"index": 0, "of": steps_of, "phase": phase}
    flow["gate"] = gate
    # raw write on purpose: ft.write_flow would normalize, and this fixture
    # models a file written by the old engine.
    # bytes, not write_text(newline=): that keyword needs Python 3.10, the engine runs on 3.9.
    (flow_dir / "flow.json").write_bytes((json.dumps(flow, indent=2) + "\n").encode("utf-8"))
    log = flow_dir / "flow-log.ndjson"
    lines = [
        {"ts": T0, "stage": "execute", "actor": "orchestrator",
         "action": "gate_opened", "outcome": "info",
         "detail": "G2 token " + OLD_TOKEN, "ref": OLD_TOKEN},
        {"ts": T1, "stage": "execute", "actor": "owner",
         "action": "gate_decided", "outcome": "info",
         "detail": "G2 verdict=approve", "ref": OLD_TOKEN},
    ]
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        for e in lines:
            fh.write(json.dumps(e) + "\n")
    return flow_dir


def _raw_flow(flow_dir):
    return (flow_dir / "flow.json").read_text(encoding="utf-8")


def test_it_reads_through_the_alias(tmp_path):
    flow_dir = _old_flow_dir(tmp_path, gate=_old_gate())
    flow = ft.load_flow(flow_dir)
    assert flow["gate"]["id"] == "spec"
    assert flow["gate"]["token"] == OLD_TOKEN
    assert fs.validate(flow) == []
    assert '"G2"' in _raw_flow(flow_dir)        # disk is untouched by a read


def test_it_renders_under_the_name(tmp_path):
    flow_dir = _old_flow_dir(tmp_path, gate=_old_gate())
    text = rf.render_status_text(flow_dir)
    assert "spec" in text and "G2" not in text.split("[")[0]
    rf.render_flow(flow_dir)
    rf.render_index(tmp_path)

    statuses = fst.collect(tmp_path, tmp_path / "no-sprints", now=T2)
    row = [s for s in statuses if s.slug == "legacy"][0]
    gates = dict(row.gates)
    assert gates["spec"].startswith("approve")
    assert fst._gates_line_content(row.gates).startswith(
        "spec, gate 1 of 2: approve")


def _ctx(flow):
    return {"flow": flow, "sent_message_ids": ["<sent-1>"],
            "sent_conversation_ids": [], "processed_ids": []}


def _reply(body):
    return {"message_id": "<r1>", "subject": "re", "body": body,
            "received_at": "2026-10-20T10:00:00Z", "in_reply_to": "<sent-1>",
            "conversation_id": None}


def test_the_old_token_still_answers(tmp_path):
    decided = ft.load_flow(_old_flow_dir(tmp_path, gate=_old_gate()))
    out = sc.plan_decisions([_reply("approve token " + OLD_TOKEN)],
                            [_ctx(decided)])
    assert out["decisions"] == []
    assert out["ignored"] and out["ignored"][0]["reason"] == "already-decided"

    awaiting = ft.load_flow(_old_flow_dir(tmp_path, slug="legacy2",
                                          gate=_old_gate(verdict=None)))
    out = sc.plan_decisions([_reply("approve token " + OLD_TOKEN)],
                            [_ctx(awaiting)])
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["verdict"] == "approve"


def test_a_full_advance_appends_and_never_rewrites_the_log(tmp_path):
    flow_dir = _old_flow_dir(tmp_path, gate=_old_gate())
    log = flow_dir / "flow-log.ndjson"
    original = log.read_bytes()

    # grill: consume the decided spec gate, the sprint line is emitted
    flow, line = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"]["phase"] == "sprint" and line
    assert flow["gate"] is None
    assert '"G2"' not in _raw_flow(flow_dir)     # the engine's save wrote it

    # sprint: no gate yet, so the engine opens accept by name
    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["gate"]["id"] == "accept"
    assert "-accept-S0-" in flow["gate"]["token"]
    assert '"G4"' not in _raw_flow(flow_dir)
    flow["gate"]["status"] = "decided"
    flow["gate"]["decision"] = {"verdict": "ship", "note": "", "surface": "session",
                                "decided_at": T2, "message_ref": None}
    ft.write_flow(flow_dir, flow)

    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["current_step"]["phase"] == "learn"
    flow, _ = ft.cmd_advance(flow_dir, now=T2)
    assert flow["stage"] == "closed"

    after = log.read_bytes()
    assert after.startswith(original) and len(after) > len(original)


def test_the_stored_token_is_byte_identical_after_the_first_save(tmp_path):
    flow_dir = _old_flow_dir(tmp_path, gate=_old_gate(verdict=None))
    flow = ft.load_flow(flow_dir)
    ft.write_flow(flow_dir, flow)
    raw = _raw_flow(flow_dir)
    assert '"G2"' not in raw and '"id": "spec"' in raw
    assert json.loads(raw)["gate"]["token"] == OLD_TOKEN


def test_a_legacy_g1_with_a_null_step_reads_as_spec_step_zero(tmp_path):
    g1 = _old_gate(gid="G1", step=None, verdict=None,
                   tok=OLD_G1_TOKEN)
    flow_dir = _old_flow_dir(tmp_path, slug="legacyg1", gate=g1)
    flow = ft.load_flow(flow_dir)
    assert flow["gate"]["id"] == "spec" and flow["gate"]["step_index"] == 0
    assert flow["gate"]["token"].endswith("G1-20261020T090000Z-7QF3")
