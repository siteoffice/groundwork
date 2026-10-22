"""Tests for flow_state.py. Plain pytest, stdlib only, hermetic."""
import copy

import flow_state as fs


def _good_flow() -> dict:
    """A structurally well-formed flow.json (idea stage, no open gate)."""
    return {
        "schema_version": 1,
        "flow_id": "dsi__payoff-books__20260718T091403Z",
        "project": "dsi",
        "slug": "payoff-books",
        "idea": "measure payoff of the books",
        "stage": "idea",
        "status": "running",
        "current_step": {"index": 0, "of": 0, "phase": None},
        "steps": [],
        "model_hint": {"plan": "opus", "dev": "sonnet"},
        "gate": None,
        "created_at": "2026-07-18T09:14:03Z",
        "updated_at": "2026-07-18T09:14:03Z",
    }


def _good_flow_with_decided_gate() -> dict:
    flow = _good_flow()
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 2, "of": 4, "phase": "sprint"}
    flow["steps"] = [{"index": 2, "title": "build it", "status": "running"}]
    flow["gate"] = {
        "id": "G2",
        "step_index": 2,
        "status": "decided",
        "decision": {"verdict": "approve", "note": "", "surface": "session",
                     "decided_at": "2026-07-18T10:00:00Z", "message_ref": None},
        "notified": True,
    }
    return flow


def test_validate_accepts_well_formed_flow():
    assert fs.validate(_good_flow()) == []


def test_validate_accepts_decided_gate_flow():
    assert fs.validate(_good_flow_with_decided_gate()) == []


def test_validate_rejects_bad_stage():
    flow = _good_flow()
    flow["stage"] = "brainstorm"
    assert any("stage" in e for e in fs.validate(flow))


def test_validate_rejects_bad_status():
    flow = _good_flow()
    flow["status"] = "in-progress"
    assert any("status" in e for e in fs.validate(flow))


def test_validate_rejects_bad_phase():
    flow = _good_flow()
    flow["current_step"]["phase"] = "review"
    assert any("phase" in e for e in fs.validate(flow))


def test_validate_rejects_bad_step_status():
    flow = _good_flow()
    flow["steps"] = [{"index": 0, "title": "x", "status": "wip"}]
    assert any("status" in e for e in fs.validate(flow))


def test_validate_rejects_bad_gate_id():
    flow = _good_flow_with_decided_gate()
    flow["gate"]["id"] = "G9"
    assert any("gate.id" in e for e in fs.validate(flow))


def test_validate_rejects_bad_gate_status():
    flow = _good_flow_with_decided_gate()
    flow["gate"]["status"] = "open"
    assert any("gate.status" in e for e in fs.validate(flow))


def test_validate_rejects_non_model_class_in_model_hint():
    flow = _good_flow()
    flow["model_hint"] = {"dev": "gpt-4o"}
    assert any("model_hint" in e for e in fs.validate(flow))


def test_validate_rejects_bad_verdict_for_gate():
    flow = _good_flow_with_decided_gate()  # G2 gate
    flow["gate"]["decision"]["verdict"] = "proceed"  # only valid for G3
    assert any("verdict" in e for e in fs.validate(flow))


def test_validate_rejects_g1_with_step_index():
    flow = _good_flow()
    flow["gate"] = {"id": "G1", "step_index": 3, "status": "awaiting-owner",
                    "decision": None, "notified": False}
    assert any("step_index" in e for e in fs.validate(flow))


def test_validate_is_lenient_about_extra_keys():
    flow = _good_flow()
    flow["gate"] = {"id": "G1", "step_index": None, "status": "awaiting-owner",
                    "decision": None, "notified": False,
                    "token": "FLOW-DSI-payoff-books-G1-20260718T091403Z-7QF3",
                    "opened_at": "2026-07-18T09:14:03Z"}
    assert fs.validate(flow) == []


def test_make_flow_id_format():
    fid = fs.make_flow_id("dsi", "payoff-books", "2026-07-18T09:14:03Z")
    assert fid == "dsi__payoff-books__20260718T091403Z"


def test_gate_token_includes_step_for_g2():
    tok = fs.gate_token("DSI", "payoff-books", "G2", 2,
                        "2026-07-18T09:14:03Z", "7QF3")
    assert tok == "FLOW-DSI-payoff-books-G2-S2-20260718T091403Z-7QF3"


def test_gate_token_omits_step_for_g1():
    tok = fs.gate_token("DSI", "payoff-books", "G1", None,
                        "2026-07-18T09:14:03Z", "7QF3")
    assert tok == "FLOW-DSI-payoff-books-G1-20260718T091403Z-7QF3"


def test_slugify_collapses_and_truncates():
    assert fs.slugify("Measure the Payoff of Books!! now really soon") == \
        "measure-the-payoff-of-books"


def test_valid_verdicts_per_gate():
    assert set(fs.valid_verdicts("G1")) == {"approve", "reject", "park"}
    assert set(fs.valid_verdicts("G3")) == {"proceed", "adapt", "close", "park"}


def test_validate_rejects_non_phase_key_in_model_hint():
    flow = _good_flow()
    flow["model_hint"] = {"design": "opus"}   # 'design' is a flow STAGE, not a /sprint phase
    errors = fs.validate(flow)
    assert any("model_hint['design'] is not a /sprint phase" in e for e in errors)


def test_validate_accepts_phase_keyed_model_hint():
    flow = _good_flow()
    flow["model_hint"] = {"dev": "opus", "report": "sonnet"}
    assert fs.validate(flow) == []


def test_validate_accepts_every_default_table_phase_as_a_hint_key():
    flow = _good_flow()
    flow["model_hint"] = {phase: "opus" for phase in fs.mm.DEFAULT_TABLE}
    assert fs.validate(flow) == []


# --- spec_path: null or a .md string, validated purely (spec scope A) --------

SPEC_MD = r"C:\work\10_holding\05_docs\superpowers\specs\2026-08-13-x.md"


def test_validate_accepts_absent_null_and_md_spec_path():
    flow = _good_flow()
    assert "spec_path" not in flow
    assert fs.validate(flow) == []          # absent is fine
    flow["spec_path"] = None
    assert fs.validate(flow) == []          # explicit null is fine
    flow["spec_path"] = SPEC_MD
    assert fs.validate(flow) == []          # a .md string is fine


def test_validate_rejects_a_spec_path_that_is_not_null_and_not_a_md_string():
    for bad in (42, ["x.md"], {"path": "x.md"}, True, "",
                r"C:\work\10_holding\05_docs\superpowers\specs\x.txt",
                r"C:\work\10_holding\05_docs\superpowers\specs\x.md.bak"):
        flow = _good_flow()
        flow["spec_path"] = bad
        errors = fs.validate(flow)
        assert any("spec_path" in e for e in errors), repr(bad)


def _shape(stage, phase, gate_spec, status):
    """One flow shape: stage, execute-phase (or None), gate spec, status.

    `gate_spec` is None, or '<gate id>-awaiting', or '<gate id>-decided'.
    """
    flow = _good_flow()
    flow["stage"] = stage
    flow["status"] = status
    if phase is None:
        flow["current_step"] = {"index": 0, "of": 0, "phase": None}
        flow["steps"] = []
    else:
        flow["current_step"] = {"index": 0, "of": 1, "phase": phase}
        flow["steps"] = [{"index": 0, "title": "only step", "status": "running"}]
    if gate_spec is None:
        flow["gate"] = None
    else:
        gate_id, gate_state = gate_spec.split("-")
        verdict = {"G1": "approve", "G2": "approve", "G3": "proceed"}[gate_id]
        gate = {
            "id": gate_id,
            "step_index": None if gate_id == "G1" else 0,
            "status": "awaiting-owner",
            "decision": None,
            "notified": False,
            "token": "FLOW-DSI-payoff-books-{}-20260718T091403Z-7QF3".format(gate_id),
            "opened_at": "2026-07-18T09:14:03Z",
        }
        if gate_state == "decided":
            gate["status"] = "decided"
            gate["notified"] = True
            gate["decision"] = {"verdict": verdict, "note": "",
                                "surface": "session",
                                "decided_at": "2026-07-18T10:00:00Z",
                                "message_ref": None}
        flow["gate"] = gate
    return flow


# The sixteen shapes the sixteen live flows under C:\work\10_holding\flows
# occupy: every stage, every execute phase, every gate state, plus paused,
# escalated and closed. Built here rather than read from that tree on purpose:
# the live tree is mutable operational data, and a test coupled to it passes in
# a worktree and fails after merge (see the comment block above
# test_board_regeneration_is_deterministic_given_the_same_inputs in
# C:\work\10_holding\foreman\_lib\test_render_foreman.py).
SIXTEEN_SHAPES = (
    ("idea", None, None, "running"),
    ("design", None, None, "running"),
    ("breakdown", None, None, "running"),
    ("breakdown", None, "G1-awaiting", "awaiting-owner"),
    ("breakdown", None, "G1-decided", "running"),
    ("execute", "grill", None, "running"),
    ("execute", "grill", "G2-awaiting", "awaiting-owner"),
    ("execute", "grill", "G2-decided", "running"),
    ("execute", "grill", None, "paused"),
    ("execute", "sprint", None, "running"),
    ("execute", "sprint", None, "paused"),
    ("execute", "learn", None, "running"),
    ("execute", "learn", "G3-awaiting", "awaiting-owner"),
    ("execute", "learn", "G3-decided", "running"),
    ("execute", "learn", None, "escalated"),
    ("closed", None, None, "closed"),
)


def test_validate_stays_green_on_all_sixteen_current_flow_shapes():
    assert len(SIXTEEN_SHAPES) == 16
    for stage, phase, gate_spec, status in SIXTEEN_SHAPES:
        flow = _shape(stage, phase, gate_spec, status)
        assert "spec_path" not in flow, (stage, phase, gate_spec, status)
        assert fs.validate(flow) == [], (stage, phase, gate_spec, status)


# --- slice c: G4, the ship gate ----------------------------------------------

def test_g4_is_a_recognised_gate_id():
    assert "G4" in fs.GATE_IDS


def test_g4_verdicts_are_exactly_ship_hold_park():
    assert fs.VERDICTS_BY_GATE["G4"] == ("ship", "hold", "park")
    assert fs.valid_verdicts("G4") == ("ship", "hold", "park")


def test_the_g4_addition_leaves_every_existing_verdict_tuple_alone():
    assert fs.VERDICTS_BY_GATE["G1"] == ("approve", "reject", "park")
    assert fs.VERDICTS_BY_GATE["G2"] == ("approve", "reject", "park")
    assert fs.VERDICTS_BY_GATE["G3"] == ("proceed", "adapt", "close", "park")


def test_a_well_formed_decided_g4_gate_validates():
    flow = _good_flow_with_decided_gate()
    flow["gate"]["id"] = "G4"
    flow["gate"]["decision"]["verdict"] = "ship"
    assert fs.validate(flow) == []


def test_a_g3_verdict_is_rejected_at_g4():
    flow = _good_flow_with_decided_gate()
    flow["gate"]["id"] = "G4"
    flow["gate"]["decision"]["verdict"] = "proceed"
    assert any("not valid for G4" in e for e in fs.validate(flow))


def test_a_g4_verdict_is_rejected_at_g3():
    flow = _good_flow_with_decided_gate()
    flow["gate"]["id"] = "G3"
    flow["gate"]["decision"]["verdict"] = "ship"
    assert any("not valid for G3" in e for e in fs.validate(flow))


def test_a_g4_gate_still_needs_an_integer_step_index():
    """G4 falls into _validate_gate's non-G1 branch, which is correct. The
    message literal still says 'G2/G3' and is deliberately left alone, so this
    asserts the substring rather than the whole sentence."""
    flow = _good_flow_with_decided_gate()
    flow["gate"]["id"] = "G4"
    flow["gate"]["decision"]["verdict"] = "ship"
    flow["gate"]["step_index"] = None
    assert any("step_index" in e for e in fs.validate(flow))


def test_outbox_names_the_ship_gate():
    import outbox
    assert outbox.GATE_NAMES["G4"] == "ship approval"
    assert outbox.gate_name("G4") == "ship approval"


def test_outbox_reply_verbs_for_g4_come_from_flow_state():
    """reply_verbs_line goes through fs.valid_verdicts, so before this task it
    renders 'park' alone. This is the assertion that pins the two together."""
    import outbox
    assert outbox.reply_verbs_line("G4") == "ship | hold | park"
