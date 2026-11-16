# Old codes (G1 to G4) below are stored old records kept on purpose (OF30).
"""Tests for gate_watch.py. Plain pytest, stdlib only, fixtures only.

Nothing here advances a flow, decides a gate, writes flow.json or launches a
sprint, and neither does the module under test (spec Invariants). Every
timestamp is injected so the tests are deterministic.
"""
import foreman_plan as fp
import gate_watch as gw

DECIDED_AT = "2026-08-01T09:00:00Z"
NOW = "2026-08-01T11:05:00Z"


def _decided_gate(gate_id="spec", verdict="approve", decided_at=DECIDED_AT):
    return {"id": gate_id, "step_index": 0, "status": "decided",
            "decision": {"verdict": verdict, "note": "", "surface": "slack",
                         "decided_at": decided_at, "message_ref": None},
            "notified": True, "token": "TOK", "opened_at": "2026-08-01T08:00:00Z"}


def _awaiting_gate(gate_id="spec"):
    return {"id": gate_id, "step_index": 0, "status": "awaiting-owner",
            "decision": None, "notified": True, "token": "TOK",
            "opened_at": "2026-08-01T08:00:00Z"}


def _flow(gate, project="ZeroNonsense.dev", slug="x"):
    return {"schema_version": 1, "flow_id": f"{project}__{slug}__20260801T080000Z",
            "project": project, "slug": slug, "idea": "x",
            "stage": "execute", "status": "running",
            "current_step": {"index": 0, "of": 1, "phase": "grill"},
            "steps": [{"index": 0, "title": "build", "status": "running"}],
            "model_hint": {}, "gate": gate,
            "created_at": "2026-08-01T08:00:00Z", "updated_at": DECIDED_AT}


def _ctx(gate, project="ZeroNonsense.dev", slug="x"):
    return {"flow": _flow(gate, project, slug), "log": [], "flow_dir": None}


# --- find_unconsumed_gates -------------------------------------------------

def test_decided_gate_is_returned_with_project_slug_gate_verdict_and_stamp():
    out = gw.find_unconsumed_gates([_ctx(_decided_gate())])
    assert out == [{"project": "ZeroNonsense.dev", "slug": "x",
                    "gate_id": "spec", "verdict": "approve",
                    "decided_at": DECIDED_AT}]


def test_awaiting_owner_gate_is_not_returned():
    assert gw.find_unconsumed_gates([_ctx(_awaiting_gate())]) == []


def test_null_gate_is_not_returned():
    assert gw.find_unconsumed_gates([_ctx(None)]) == []


def test_load_error_context_is_skipped_not_raised():
    assert gw.find_unconsumed_gates(
        [{"flow": None, "log": [], "flow_dir": None, "load_error": True}]) == []


def test_malformed_decided_gate_is_skipped_not_raised():
    no_decision = _decided_gate()
    no_decision["decision"] = None
    assert gw.find_unconsumed_gates([_ctx(no_decision)]) == []


def test_returns_one_entry_per_decided_flow_and_ignores_the_rest():
    out = gw.find_unconsumed_gates([
        _ctx(_decided_gate(gate_id="spec"), slug="a"),
        _ctx(_awaiting_gate(), slug="b"),
        _ctx(None, slug="c"),
        _ctx(_decided_gate(gate_id="accept", verdict="ship"), slug="d"),
    ])
    assert [(e["slug"], e["gate_id"], e["verdict"]) for e in out] == [
        ("a", "spec", "approve"), ("d", "accept", "ship")]


def test_is_pure_and_does_not_mutate_its_input():
    ctx = _ctx(_decided_gate())
    before = dict(ctx["flow"]["gate"])
    gw.find_unconsumed_gates([ctx])
    assert ctx["flow"]["gate"] == before


# --- cross-check: the malformed rule must agree with foreman_plan ----------

def test_malformed_gate_rule_agrees_with_foreman_plan():
    """gate_watch returns an entry for exactly the decided gates foreman_plan
    considers well-formed, so the two rules can never silently diverge."""
    unknown_id = _decided_gate(gate_id="GX")
    empty_verdict = _decided_gate(verdict="")
    no_decision = _decided_gate()
    no_decision["decision"] = None
    for gate in (_decided_gate(), _decided_gate(gate_id="accept", verdict="ship"),
                 unknown_id, empty_verdict, no_decision):
        returned = bool(gw.find_unconsumed_gates([_ctx(gate)]))
        well_formed = fp._malformed_gate_reason(gate) is None
        assert returned == well_formed, gate


def test_a_decided_old_ship_gate_is_reported_by_name():
    out = gw.find_unconsumed_gates([_ctx(_decided_gate(gate_id="accept", verdict="ship"))])
    assert [e["gate_id"] for e in out] == ["accept"]


def test_a_decided_retired_gate_is_skipped():
    assert gw.find_unconsumed_gates(
        [_ctx(_decided_gate(gate_id="G3", verdict="proceed"))]) == []


# --- format_age (host helper, clock injected) ------------------------------

def test_format_age_renders_hours_and_zero_padded_minutes():
    assert gw.format_age(DECIDED_AT, NOW) == "2h05m"


def test_format_age_under_one_hour():
    assert gw.format_age(DECIDED_AT, "2026-08-01T09:42:00Z") == "0h42m"


def test_format_age_unparsable_or_negative_is_named_not_raised():
    assert gw.format_age("not-a-stamp", NOW) == "age-unknown"
    assert gw.format_age(DECIDED_AT, "") == "age-unknown"
    assert gw.format_age(NOW, DECIDED_AT) == "age-unknown"


# --- host main() -----------------------------------------------------------

def test_main_exits_zero_on_an_empty_tree(tmp_path, capsys):
    assert gw.main(["--flows-root", str(tmp_path)]) == 0
    assert "unconsumed=0" in capsys.readouterr().out


def test_main_exits_zero_and_names_the_gate_on_a_live_tree(tmp_path, capsys):
    import json
    flow_dir = tmp_path / "ZeroNonsense.dev" / "x"
    flow_dir.mkdir(parents=True)
    (flow_dir / "flow.json").write_text(
        json.dumps(_flow(_decided_gate())), encoding="utf-8")
    assert gw.main(["--flows-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ZeroNonsense.dev/x" in out and "spec" in out and "approve" in out
    assert "unconsumed=1" in out
    assert "run: /flow advance ZeroNonsense.dev/x" in out


def _live_tree_output(tmp_path, capsys, verdict, gate_id="accept"):
    import json
    flow_dir = tmp_path / "ZeroNonsense.dev" / "x"
    flow_dir.mkdir(parents=True)
    (flow_dir / "flow.json").write_text(
        json.dumps(_flow(_decided_gate(gate_id=gate_id, verdict=verdict))),
        encoding="utf-8")
    assert gw.main(["--flows-root", str(tmp_path)]) == 0
    return capsys.readouterr().out


def test_main_hint_for_hold_names_the_driver(tmp_path, capsys):
    out = _live_tree_output(tmp_path, capsys, "hold")
    assert "run: python foreman_run.py run ZeroNonsense.dev/x" in out


def test_main_hint_for_park_names_the_driver(tmp_path, capsys):
    out = _live_tree_output(tmp_path, capsys, "park", gate_id="spec")
    assert "run: python foreman_run.py run ZeroNonsense.dev/x" in out


def test_main_exits_zero_when_the_root_does_not_exist(tmp_path, capsys):
    assert gw.main(["--flows-root", str(tmp_path / "nope")]) == 0
