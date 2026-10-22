"""Tests for foreman_plan.py. Plain pytest, stdlib only, hermetic.

Every state is a literal fixture and every call is pure: no tmp_path, no clock,
no file is read or written anywhere in this module or in the module under test.
"""
import dataclasses

import foreman_plan as fp


# --- fixtures ---------------------------------------------------------------

TS = "2026-07-18T09:14:03Z"


def _flow(stage="idea", status="running", phase=None, index=0, of=0,
          gate=None, steps=None, spec_path=None):
    flow = {
        "schema_version": 1,
        "flow_id": "dsi__payoff-books__20260718T091403Z",
        "project": "dsi",
        "slug": "payoff-books",
        "idea": "measure payoff of the books",
        "stage": stage,
        "status": status,
        "current_step": {"index": index, "of": of, "phase": phase},
        "steps": steps if steps is not None else [],
        "model_hint": {},
        "gate": gate,
        "created_at": TS,
        "updated_at": TS,
    }
    if spec_path:
        flow["spec_path"] = spec_path
    return flow


def _gate(gate_id="G1", step_index=None, decided=None, note=""):
    """An awaiting-owner gate, or a decided one when `decided` is a verdict."""
    gate = {
        "id": gate_id,
        "step_index": step_index,
        "status": "awaiting-owner",
        "decision": None,
        "notified": False,
        "token": f"FLOW-DSI-payoff-books-{gate_id}-20260718T091403Z-7QF3",
        "opened_at": TS,
    }
    if decided is not None:
        gate["status"] = "decided"
        gate["decision"] = {"verdict": decided, "note": note,
                            "surface": "session", "decided_at": TS,
                            "message_ref": None}
    return gate


def _execute_flow(phase, index=0, of=3, gate=None, spec_path=None):
    steps = [{"index": i, "title": f"step {i}", "status": "pending"}
             for i in range(of)]
    steps[index]["status"] = "running"
    return _flow(stage="execute", phase=phase, index=index, of=of, gate=gate,
                 steps=steps, spec_path=spec_path)


# --- the record itself ------------------------------------------------------

def test_next_action_is_a_frozen_dataclass_with_the_eight_spec_fields():
    fields = [f.name for f in dataclasses.fields(fp.NextAction)]
    assert fields == ["kind", "why", "blocking", "command", "gate_id",
                      "step_index", "role", "guidance_key"]
    action = fp.next_action(_flow(), None, {})
    try:
        action.kind = "halt"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("NextAction must be frozen")


def test_action_kinds_tuple_matches_the_spec_verbatim():
    assert fp.ACTION_KINDS == (
        "flow_advance", "gate_open", "gate_await", "gate_consume",
        "sprint_launch", "sprint_await", "sprint_recover",
        "ship_preflight", "ship_execute",
        "brief_write", "hub_ingest", "hub_refresh",
        "flow_close", "halt",
    )


def test_every_returned_field_is_in_its_declared_domain():
    action = fp.next_action(_flow(), None, {})
    assert action.kind in fp.ACTION_KINDS
    assert action.blocking in fp.BLOCKING_KINDS
    assert action.role in fp.ROLES


# --- flow_advance -----------------------------------------------------------

def test_idea_stage_advances():
    action = fp.next_action(_flow(stage="idea"), None, {})
    assert action.kind == "flow_advance"
    assert action.blocking == ""
    assert action.command == "/flow advance dsi/payoff-books"
    assert action.gate_id is None
    assert action.guidance_key == "flow_advance.idea"
    assert "idea" in action.why


def test_design_stage_advances():
    action = fp.next_action(_flow(stage="design"), None, {})
    assert action.kind == "flow_advance"
    assert action.guidance_key == "flow_advance.design"


# --- gate_open --------------------------------------------------------------

def test_breakdown_with_no_gate_opens_g1():
    action = fp.next_action(_flow(stage="breakdown"), None, {})
    assert action.kind == "gate_open"
    assert action.gate_id == "G1"
    assert action.step_index is None
    assert action.blocking == ""
    assert action.guidance_key == "gate_open.g1"


def test_grill_phase_with_no_gate_opens_g2_for_the_current_step():
    action = fp.next_action(_execute_flow("grill", index=1), None, {})
    assert action.kind == "gate_open"
    assert action.gate_id == "G2"
    assert action.step_index == 1
    assert action.guidance_key == "gate_open.g2"


def test_learn_phase_with_no_gate_opens_g3_for_the_current_step():
    action = fp.next_action(_execute_flow("learn", index=2), None, {})
    assert action.kind == "gate_open"
    assert action.gate_id == "G3"
    assert action.step_index == 2


# --- gate_await -------------------------------------------------------------

def test_open_undecided_gate_blocks_on_human():
    flow = _flow(stage="breakdown", status="awaiting-owner", gate=_gate("G1"))
    action = fp.next_action(flow, None, {})
    assert action.kind == "gate_await"
    assert action.blocking == "human"
    assert action.gate_id == "G1"
    assert action.command == "/flow gate dsi/payoff-books <verdict>"
    assert action.guidance_key == "gate_await.g1"


def test_gate_await_why_names_every_allowed_verdict():
    flow = _flow(stage="breakdown", status="awaiting-owner", gate=_gate("G1"))
    why = fp.next_action(flow, None, {}).why
    for verdict in ("approve", "reject", "park"):
        assert verdict in why


def test_gate_await_at_g3_names_the_g3_verdicts():
    flow = _execute_flow("learn", gate=_gate("G3", step_index=0))
    why = fp.next_action(flow, None, {}).why
    for verdict in ("proceed", "adapt", "close", "park"):
        assert verdict in why


# --- gate_consume -----------------------------------------------------------

def test_decided_gate_is_consumed():
    flow = _flow(stage="breakdown", gate=_gate("G1", decided="approve"))
    action = fp.next_action(flow, None, {})
    assert action.kind == "gate_consume"
    assert action.blocking == ""
    assert action.gate_id == "G1"
    assert action.command == "/flow advance dsi/payoff-books"
    assert action.guidance_key == "gate_consume.g1.approve"


def test_every_verdict_at_every_gate_is_consumed_and_keyed():
    cases = [
        ("G1", None, ("approve", "reject", "park")),
        ("G2", 0, ("approve", "reject", "park")),
        ("G3", 0, ("proceed", "adapt", "close", "park")),
    ]
    for gate_id, step_index, verdicts in cases:
        for verdict in verdicts:
            gate = _gate(gate_id, step_index=step_index, decided=verdict)
            if gate_id == "G1":
                flow = _flow(stage="breakdown", gate=gate)
            else:
                phase = "grill" if gate_id == "G2" else "learn"
                flow = _execute_flow(phase, gate=gate)
            action = fp.next_action(flow, None, {})
            assert action.kind == "gate_consume", (gate_id, verdict)
            assert action.gate_id == gate_id
            assert action.guidance_key == \
                "gate_consume.{}.{}".format(gate_id.lower(), verdict)
            assert verdict in action.why


# --- halt -------------------------------------------------------------------

def test_halt_file_flag_halts_before_anything_else():
    action = fp.next_action(_flow(stage="idea"), None, {"halt": True})
    assert action.kind == "halt"
    assert action.blocking == "fence"
    assert action.command == ""
    assert action.guidance_key == "halt.halt_file"


def test_paused_reason_halts():
    action = fp.next_action(_flow(stage="idea"), None,
                            {"paused_reason": "waiting on the partner call"})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.paused"
    assert "waiting on the partner call" in action.why


def test_parked_flow_halts():
    action = fp.next_action(_flow(stage="breakdown", status="paused"), None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.flow_paused"


def test_chain_cap_halts_at_exactly_the_cap():
    at_cap = fp.next_action(_flow(), None, {"chain_depth": 12, "chain_cap": 12})
    assert at_cap.kind == "halt"
    assert at_cap.guidance_key == "halt.chain_cap"
    under = fp.next_action(_flow(), None, {"chain_depth": 11, "chain_cap": 12})
    assert under.kind == "flow_advance"


def test_chain_cap_defaults_to_twelve():
    assert fp.DEFAULT_CHAIN_CAP == 12
    action = fp.next_action(_flow(), None, {"chain_depth": 12})
    assert action.kind == "halt"


def test_halt_beats_an_open_gate():
    flow = _flow(stage="breakdown", status="awaiting-owner", gate=_gate("G1"))
    action = fp.next_action(flow, None, {"halt": True})
    assert action.kind == "halt"


def test_unknown_stage_halts_rather_than_guessing():
    action = fp.next_action(_flow(stage="brainstorm"), None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.unknown_stage"


def test_unknown_phase_halts_rather_than_guessing():
    flow = _execute_flow("grill")
    flow["current_step"]["phase"] = "review"
    action = fp.next_action(flow, None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.unknown_phase"


def test_decided_gate_with_no_decision_halts_rather_than_a_malformed_key():
    # flow_state._validate_gate does not require `decision` to be non-null
    # when status == "decided", so this is a valid flow.json. Feeding it
    # through used to produce guidance_key "gate_consume.g1." (a trailing
    # dot, from an empty verdict string) instead of halting.
    gate = _gate("G1")
    gate["status"] = "decided"
    gate["decision"] = None
    flow = _flow(stage="breakdown", gate=gate)
    action = fp.next_action(flow, None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.malformed_gate"


def test_gate_with_no_id_halts_rather_than_a_question_mark_key():
    gate = _gate("G1", decided="approve")
    gate["id"] = None
    flow = _flow(stage="breakdown", gate=gate)
    action = fp.next_action(flow, None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.malformed_gate"


def test_unrecognised_gate_status_halts_rather_than_guessing():
    gate = _gate("G1")
    gate["status"] = "opening"
    flow = _flow(stage="breakdown", gate=gate)
    action = fp.next_action(flow, None, {})
    assert action.kind == "halt"
    assert action.guidance_key == "halt.malformed_gate"


def test_empty_driver_and_empty_flow_do_not_raise():
    assert fp.next_action({}, None, {}).kind == "halt"
    assert fp.next_action(_flow(), None, None).kind == "flow_advance"


# --- conservative sprint default (refined in Task 2) ------------------------

def test_sprint_phase_defaults_to_waiting_on_the_agent():
    action = fp.next_action(_execute_flow("sprint"), {"status": "running"}, {})
    assert action.kind == "sprint_await"
    assert action.blocking == "agent"


# --- Task 2: the sprint branch ---------------------------------------------

import terminal_sweep as ts  # anti-drift check only

SPEC_PATH = r"C:\work\dsi\docs\superpowers\specs\2026-07-18-payoff-books-design.md"


def _sprint_flow(gate=None):
    return _execute_flow("sprint", index=0, of=3, gate=gate,
                         spec_path=SPEC_PATH)


def _sprint_json(status="running", sprint_id="dsi__payoff-books__20260718T100000Z",
                 branch="sprint/payoff-books"):
    return {"schema_version": 2, "sprint_id": sprint_id, "project": "dsi",
            "slug": "payoff-books", "spec_path": SPEC_PATH,
            "worktree_branch": branch, "status": status, "phase": "dev",
            "iteration": 1, "max_iterations": 5}


def test_terminal_status_sets_match_terminal_sweep():
    assert fp.SUCCESS_STATUSES == ts.SUCCESS_STATUSES
    assert fp.NEEDS_GUIDANCE_STATUSES == ts.NEEDS_GUIDANCE_STATUSES


def test_no_sprint_yet_means_launch_it_with_the_exact_emitted_line():
    action = fp.next_action(_sprint_flow(), None, {})
    assert action.kind == "sprint_launch"
    assert action.blocking == ""
    assert action.command == (
        "/sprint dsi " + SPEC_PATH +
        " --model-map spec=opus,plan=opus,dev=sonnet,test=haiku,"
        "verify=opus,iterate=sonnet,report=haiku"
    )
    assert action.guidance_key == "sprint_launch"


def test_running_sprint_blocks_on_the_agent():
    action = fp.next_action(_sprint_flow(), _sprint_json("running"), {})
    assert action.kind == "sprint_await"
    assert action.blocking == "agent"
    assert action.command == ""


def test_paused_sprint_still_waits_rather_than_recovering():
    action = fp.next_action(_sprint_flow(), _sprint_json("paused"), {})
    assert action.kind == "sprint_await"


def test_every_needs_guidance_status_recovers_non_blocking():
    for status in ("capped", "failed", "escalated"):
        action = fp.next_action(_sprint_flow(), _sprint_json(status), {})
        assert action.kind == "sprint_recover", status
        assert action.blocking == ""
        assert action.command == "/flow advance dsi/payoff-books"
        assert action.guidance_key == "sprint_recover.{}".format(status)
        assert status in action.why


def test_every_success_status_runs_preflight_first():
    for status in ("passing", "done"):
        action = fp.next_action(_sprint_flow(), _sprint_json(status), {})
        assert action.kind == "ship_preflight", status
        assert action.blocking == ""
        assert action.command == ""
        assert action.role == "ship_preflight"
        assert action.guidance_key == "ship_preflight"


def test_recorded_preflight_opens_the_ship_gate():
    driver = {"preflight": {"clean_tree": True, "tests_pass": True}}
    action = fp.next_action(_sprint_flow(), _sprint_json("passing"), driver)
    assert action.kind == "gate_open"
    assert action.gate_id == "G4"
    assert action.guidance_key == "gate_open.g4"


def test_ship_verdict_at_g4_executes_the_ship():
    gate = _gate("G4", step_index=0, decided="ship")
    action = fp.next_action(_sprint_flow(gate=gate), _sprint_json("passing"), {})
    assert action.kind == "ship_execute"
    assert action.blocking == ""
    assert action.command == ""
    assert action.gate_id == "G4"
    assert action.guidance_key == "ship_execute"
    assert "sprint/payoff-books" in action.why


def test_ship_execute_is_not_repeated_once_shipped():
    gate = _gate("G4", step_index=0, decided="ship")
    action = fp.next_action(_sprint_flow(gate=gate), _sprint_json("passing"),
                            {"shipped": True})
    assert action.kind == "gate_consume"
    assert action.gate_id == "G4"


def test_hold_and_park_at_g4_are_plain_consumes():
    for verdict in ("hold", "park"):
        gate = _gate("G4", step_index=0, decided=verdict)
        action = fp.next_action(_sprint_flow(gate=gate),
                                _sprint_json("passing"), {})
        assert action.kind == "gate_consume", verdict
        assert action.guidance_key == "gate_consume.g4.{}".format(verdict)


def test_open_g4_awaits_and_names_the_ship_verdicts():
    gate = _gate("G4", step_index=0)
    action = fp.next_action(_sprint_flow(gate=gate), _sprint_json("passing"), {})
    assert action.kind == "gate_await"
    assert action.gate_id == "G4"
    for verdict in ("ship", "hold", "park"):
        assert verdict in action.why


def test_ship_gate_constants_agree_with_flow_state_once_it_knows_g4():
    # Slice c adds G4 to flow_state. Until it does, this asserts nothing about
    # flow_state; after it does, it fails the moment the two disagree.
    if fp.SHIP_GATE_ID in fs_gate_ids():
        assert tuple(fs_verdicts(fp.SHIP_GATE_ID)) == fp.SHIP_VERDICTS


def fs_gate_ids():
    import flow_state
    return flow_state.GATE_IDS


def fs_verdicts(gate_id):
    import flow_state
    return flow_state.VERDICTS_BY_GATE.get(gate_id, ())


# --- Task 3: the tail -------------------------------------------------------

import re


def _closed_flow():
    return _flow(stage="closed", status="closed", phase="learn", index=2, of=3,
                 steps=[{"index": i, "title": "step {}".format(i),
                         "status": "done"} for i in range(3)])


def test_closed_flow_writes_the_brief_first():
    action = fp.next_action(_closed_flow(), None, {})
    assert action.kind == "brief_write"
    assert action.blocking == ""
    assert action.command == ""
    assert action.role == "learn_summarise"
    assert action.guidance_key == "brief_write"


def test_written_brief_is_ingested_next():
    driver = {"tail": {"brief_written": True}}
    action = fp.next_action(_closed_flow(), None, driver)
    assert action.kind == "hub_ingest"
    assert action.role == "hub_ingest"


def test_ingested_brief_refreshes_the_hub_next():
    driver = {"tail": {"brief_written": True, "hub_ingested": True}}
    action = fp.next_action(_closed_flow(), None, driver)
    assert action.kind == "hub_refresh"
    assert action.role == "hub_refresh"


def test_complete_tail_closes_the_flow():
    driver = {"tail": {"brief_written": True, "hub_ingested": True,
                       "hub_refreshed": True}}
    action = fp.next_action(_closed_flow(), None, driver)
    assert action.kind == "flow_close"
    assert action.blocking == ""
    assert action.guidance_key == "flow_close"


# --- the state corpus, one entry per ACTION_KINDS value ---------------------

def _corpus():
    """(kind, flow, sprint, driver) covering every ACTION_KINDS value once."""
    ship_gate = _gate("G4", step_index=0, decided="ship")
    full_tail = {"tail": {"brief_written": True, "hub_ingested": True,
                          "hub_refreshed": True}}
    return [
        ("flow_advance", _flow(stage="idea"), None, {}),
        ("gate_open", _flow(stage="breakdown"), None, {}),
        ("gate_await", _flow(stage="breakdown", status="awaiting-owner",
                             gate=_gate("G1")), None, {}),
        ("gate_consume", _flow(stage="breakdown",
                               gate=_gate("G1", decided="approve")), None, {}),
        ("sprint_launch", _sprint_flow(), None, {}),
        ("sprint_await", _sprint_flow(), _sprint_json("running"), {}),
        ("sprint_recover", _sprint_flow(), _sprint_json("failed"), {}),
        ("ship_preflight", _sprint_flow(), _sprint_json("passing"), {}),
        ("ship_execute", _sprint_flow(gate=ship_gate),
         _sprint_json("passing"), {}),
        ("brief_write", _closed_flow(), None, {}),
        ("hub_ingest", _closed_flow(), None,
         {"tail": {"brief_written": True}}),
        ("hub_refresh", _closed_flow(), None,
         {"tail": {"brief_written": True, "hub_ingested": True}}),
        ("flow_close", _closed_flow(), None, full_tail),
        ("halt", _flow(stage="idea"), None, {"halt": True}),
    ]


def test_the_corpus_covers_every_action_kind_exactly_once():
    kinds = [kind for kind, _f, _s, _d in _corpus()]
    assert sorted(kinds) == sorted(fp.ACTION_KINDS)
    assert len(kinds) == len(set(kinds))


def test_every_corpus_state_returns_its_expected_kind():
    for kind, flow, sprint, driver in _corpus():
        assert fp.next_action(flow, sprint, driver).kind == kind, kind


# --- the full happy path for a three-step flow ------------------------------

import flow_transition as ft

TS_HP = "2026-07-18T09:14:03Z"


def _consume_g4(flow, driver):
    """Clear a decided G4 via the real transition function.

    Was a hand-simulated stand-in (flow["gate"] = None; phase = "learn")
    before the 2026-08-24 ship-flag-reset fix. Now calls the production
    ft._consume_g4 directly, so this scenario proves the real host code
    clears driver["preflight"]/["shipped"]/["ship"] between steps, not a
    test-side simulation of it. Mutates flow and driver in place (both are
    the same dict objects the caller holds), matching flow_transition's own
    pure-function contract of returning the same objects it was given.
    """
    gate = dict(flow["gate"])
    cs = dict(flow["current_step"])
    ft._consume_g4(flow, gate, cs, driver)


def _drive_happy_path():
    """Walk a three-step flow from idea to closed, applying the real transition
    functions between states, and return the (kind, gate_id) series."""
    titles = ["step one", "step two", "step three"]
    flow = ft.new_flow("dsi", "payoff-books", "three steps", TS_HP)
    flow["spec_path"] = SPEC_PATH
    driver = {}
    seen = []

    def step(sprint=None):
        action = fp.next_action(flow, sprint, driver)
        seen.append((action.kind, action.gate_id))
        return action

    step()
    ft.advance(flow, TS_HP)                                  # idea -> design
    step()
    ft.advance(flow, TS_HP)                                  # design -> breakdown
    step()
    ft.open_gate(flow, "G1", None, TS_HP, "7QF3")
    step()
    ft.apply_gate_decision(flow, "approve", "", "session", TS_HP)
    step()
    ft.advance(flow, TS_HP, steps=titles)                    # -> execute, grill

    running = {"status": "running", "worktree_branch": "sprint/payoff-books"}
    passing = {"status": "passing", "worktree_branch": "sprint/payoff-books"}

    for index in range(3):
        step()
        ft.open_gate(flow, "G2", index, TS_HP, "7QF3")
        step()
        ft.apply_gate_decision(flow, "approve", "", "session", TS_HP)
        step()
        ft.advance(flow, TS_HP)                              # -> phase sprint
        step(sprint=None)                                    # sprint_launch
        step(sprint=running)                                 # sprint_await
        step(sprint=passing)                                 # ship_preflight
        driver["preflight"] = {"clean_tree": True}
        step(sprint=passing)                                 # gate_open G4
        ft.open_gate(flow, "G4", index, TS_HP, "7QF3")
        step(sprint=passing)                                 # gate_await G4
        ft.apply_gate_decision(flow, "ship", "", "session", TS_HP)
        step(sprint=passing)                                 # ship_execute
        driver["shipped"] = True
        step(sprint=passing)                                 # gate_consume G4
        _consume_g4(flow, driver)
        assert not driver.get("shipped")
        assert "preflight" not in driver
        step()                                               # gate_open G3
        ft.open_gate(flow, "G3", index, TS_HP, "7QF3")
        step()                                               # gate_await G3
        ft.apply_gate_decision(flow, "proceed", "", "session", TS_HP)
        step()                                               # gate_consume G3
        ft.advance(flow, TS_HP)                              # next step, or closed

    assert flow["stage"] == "closed"

    step()
    driver["tail"] = {"brief_written": True}
    step()
    driver["tail"]["hub_ingested"] = True
    step()
    driver["tail"]["hub_refreshed"] = True
    step()
    return seen


_PER_STEP = [
    ("gate_open", "G2"), ("gate_await", "G2"), ("gate_consume", "G2"),
    ("sprint_launch", None), ("sprint_await", None), ("ship_preflight", None),
    ("gate_open", "G4"), ("gate_await", "G4"), ("ship_execute", "G4"),
    ("gate_consume", "G4"), ("gate_open", "G3"), ("gate_await", "G3"),
    ("gate_consume", "G3"),
]

HAPPY_PATH = (
    [("flow_advance", None), ("flow_advance", None), ("gate_open", "G1"),
     ("gate_await", "G1"), ("gate_consume", "G1")]
    + _PER_STEP * 3
    + [("brief_write", None), ("hub_ingest", None), ("hub_refresh", None),
       ("flow_close", None)]
)


def test_the_full_happy_path_for_a_three_step_flow():
    assert len(HAPPY_PATH) == 48
    assert _drive_happy_path() == HAPPY_PATH


def test_the_happy_path_stops_for_a_human_exactly_at_the_gates():
    waits = [pair for pair in _drive_happy_path() if pair[0] == "gate_await"]
    expected = [("gate_await", "G1")]
    for _index in range(3):
        expected += [("gate_await", "G2"), ("gate_await", "G4"),
                     ("gate_await", "G3")]
    assert waits == expected


# --- properties -------------------------------------------------------------

def test_next_action_is_idempotent_for_a_given_state():
    for kind, flow, sprint, driver in _corpus():
        first = fp.next_action(flow, sprint, driver)
        second = fp.next_action(flow, sprint, driver)
        assert first == second, kind


def test_next_action_does_not_mutate_its_inputs():
    import copy
    for kind, flow, sprint, driver in _corpus():
        flow_before = copy.deepcopy(flow)
        sprint_before = copy.deepcopy(sprint)
        driver_before = copy.deepcopy(driver)
        fp.next_action(flow, sprint, driver)
        assert flow == flow_before, kind
        assert sprint == sprint_before, kind
        assert driver == driver_before, kind


def test_an_awaiting_gate_never_yields_a_non_blocking_action():
    for gate_id, phase, step_index in (("G1", None, None), ("G2", "grill", 0),
                                       ("G3", "learn", 0), ("G4", "sprint", 0)):
        gate = _gate(gate_id, step_index=step_index)
        if phase is None:
            flow = _flow(stage="breakdown", status="awaiting-owner", gate=gate)
        else:
            flow = _execute_flow(phase, gate=gate, spec_path=SPEC_PATH)
        action = fp.next_action(flow, _sprint_json("passing"), {})
        assert action.blocking == "human", gate_id
        assert action.kind == "gate_await", gate_id


def test_no_state_in_the_corpus_returns_the_reserved_clock_blocking():
    for kind, flow, sprint, driver in _corpus():
        assert fp.next_action(flow, sprint, driver).blocking != "clock", kind


# --- house rules ------------------------------------------------------------

_KEY_RE = re.compile(r"[a-z0-9]+(?:[._][a-z0-9]+)*")


def test_every_guidance_key_is_a_safe_filename_stem():
    for kind, flow, sprint, driver in _corpus():
        key = fp.next_action(flow, sprint, driver).guidance_key
        assert _KEY_RE.fullmatch(key), (kind, key)


def test_key_re_rejects_leading_trailing_and_doubled_separators():
    # A trailing dot is a broken filename stem once slice e composes
    # `<guidance_key>.L<level>.md`; a leading dot or a doubled separator is
    # just as unsafe. The old charset test `[a-z0-9_.]+` full-matched all of
    # these because fullmatch only requires the whole string be covered by
    # the charset, not that separators are bracketed by alnums.
    assert not _KEY_RE.fullmatch("gate_consume.g1.")
    assert not _KEY_RE.fullmatch(".gate_open")
    assert not _KEY_RE.fullmatch("gate..open")


def test_no_emitted_string_contains_an_em_dash():
    for kind, flow, sprint, driver in _corpus():
        action = fp.next_action(flow, sprint, driver)
        for field in (action.why, action.command, action.guidance_key):
            assert "\u2014" not in field, (kind, field)


def test_module_source_contains_no_em_dash():
    import pathlib
    src = pathlib.Path(fp.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in src


def test_every_why_is_a_non_empty_sentence():
    for kind, flow, sprint, driver in _corpus():
        why = fp.next_action(flow, sprint, driver).why
        assert why and why.strip() == why, kind
        assert len(why) > 20, kind


# --- scope B: the launch record and the double-launch window -------------------

LAUNCH_LINE = ("/sprint dsi C:\\work\\dsi\\docs\\superpowers\\specs\\x.md "
               "--model-map spec=opus,plan=opus,dev=sonnet,test=haiku,"
               "verify=opus,iterate=sonnet,report=haiku")


def _launch_record(line=LAUNCH_LINE):
    """One owner for the launch seed data. Stable values, no generator."""
    return {"line": line,
            "spec_path": "C:\\work\\dsi\\docs\\superpowers\\specs\\x.md",
            "ts": TS,
            "session_id": "foreman-20260817T0914-a1b2c3"}


def test_sprint_launch_when_there_is_no_sprint_and_no_launch_record():
    action = fp.next_action(_execute_flow("sprint"), None, {})
    assert action.kind == "sprint_launch"
    assert action.blocking == ""
    assert action.guidance_key == "sprint_launch"
    assert action.command.startswith("/sprint ")


def test_sprint_await_when_there_is_no_sprint_but_a_launch_record_is_present():
    driver = {"launch": _launch_record()}
    action = fp.next_action(_execute_flow("sprint"), None, driver)
    assert action.kind == "sprint_await"
    assert action.blocking == "agent"
    assert action.guidance_key == "sprint_await"
    assert action.command == LAUNCH_LINE
    assert action.step_index == 0
    assert action.role == "none"
    assert action.gate_id is None


def test_the_launch_record_does_not_survive_into_a_step_whose_sprint_resolves():
    """A resolved sprint wins over the advisory record: the record only answers
    the question the sprint cannot answer yet."""
    driver = {"launch": _launch_record()}
    sprint = {"sprint_id": "dsi__x__20260817T091403Z", "status": "running"}
    action = fp.next_action(_execute_flow("sprint"), sprint, driver)
    assert action.kind == "sprint_await"
    assert action.command == ""          # the running-sprint branch, not the record
    for status, kind in (("passing", "ship_preflight"), ("failed", "sprint_recover")):
        action = fp.next_action(_execute_flow("sprint"),
                                {"sprint_id": "s", "status": status}, driver)
        assert action.kind == kind, status


def test_both_launch_branches_are_idempotent():
    flow = _execute_flow("sprint")
    for driver in ({}, {"launch": _launch_record()}):
        first = fp.next_action(flow, None, driver)
        second = fp.next_action(flow, None, driver)
        assert first == second


def test_a_falsy_launch_record_still_offers_the_launch():
    """None and {} both mean "no launch outstanding". Deleting foreman.json is
    the documented recovery, and it must land on sprint_launch, not on a wait."""
    for driver in ({"launch": None}, {"launch": {}}):
        assert fp.next_action(_execute_flow("sprint"), None, driver).kind == \
            "sprint_launch"


def test_action_kinds_is_still_fourteen():
    assert len(fp.ACTION_KINDS) == 14


# --- slice e: resolve_guide_level -------------------------------------------

def test_guide_levels_tuple_matches_the_spec_verbatim():
    assert fp.GUIDE_LEVELS == ("silent", "terse", "coached", "teaching")


def test_resolve_guide_level_walks_the_completion_boundary_exactly():
    """The 0/1/2/3 boundary is the whole rung: 2 stays coached, 3 flips terse.

    Mutation proof: changing the comparison in resolve_guide_level from >= to >
    turns the completions == 3 case red here and nothing else.
    """
    cases = (
        (0, "coached"),
        (1, "coached"),
        (2, "coached"),
        (3, "terse"),
        (4, "terse"),
        (99, "terse"),
    )
    for completions, expected in cases:
        got = fp.resolve_guide_level({"completions": completions}, None)
        assert got == expected, completions


def test_resolve_guide_level_defaults_to_coached_on_an_empty_operator():
    assert fp.resolve_guide_level({}, None) == "coached"
    assert fp.resolve_guide_level({"completions": None}, None) == "coached"
    assert fp.resolve_guide_level({"completions": "three"}, None) == "coached"


def test_an_explicit_level_wins_over_the_stored_completion_count():
    """Both directions: explicit quiets a novice and explicit un-quiets a
    fluent operator."""
    cases = (
        # completions, explicit, expected
        (0, "silent", "silent"),
        (0, "terse", "terse"),
        (0, "teaching", "teaching"),
        (9, "coached", "coached"),
        (9, "teaching", "teaching"),
        (9, "silent", "silent"),
    )
    for completions, explicit, expected in cases:
        got = fp.resolve_guide_level({"completions": completions}, explicit)
        assert got == expected, (completions, explicit)


def test_an_unrecognised_explicit_level_falls_back_to_the_stored_rung():
    for bogus in ("", None, "loud", "COACHED", "verbose", 3):
        assert fp.resolve_guide_level({"completions": 0}, bogus) == "coached"
        assert fp.resolve_guide_level({"completions": 5}, bogus) == "terse"


def test_resolve_guide_level_always_returns_a_declared_level():
    for operator in ({}, {"completions": 0}, {"completions": 3},
                     {"completions": -1}):
        assert fp.resolve_guide_level(operator, None) in fp.GUIDE_LEVELS


def test_resolve_guide_level_does_not_mutate_the_operator_it_is_handed():
    operator = {"completions": 3, "guide": None}
    fp.resolve_guide_level(operator, "teaching")
    assert operator == {"completions": 3, "guide": None}
