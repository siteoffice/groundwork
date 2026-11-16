"""Gate vocabulary: names, read-only alias table, positions, verdicts, validation."""
import copy
import re

import pytest

import flow_state as fs


def _flow_with_gate(gate):
    return {
        "schema_version": 1,
        "flow_id": "p__s__20261103T000000Z",
        "project": "p",
        "slug": "s",
        "idea": "x",
        "stage": "execute",
        "status": "awaiting-owner",
        "current_step": {"index": 0, "of": 1, "phase": "grill"},
        "steps": [{"index": 0, "title": "t", "status": "running"}],
        "model_hint": {"plan": "opus", "dev": "sonnet"},
        "gate": gate,
        "created_at": "2026-11-03T00:00:00Z",
        "updated_at": "2026-11-03T00:00:00Z",
    }


def _tok(gid):
    return "-".join(["FLOW", "P", "s", gid, "S0", "20261103T000000Z", "AB12"])


def _gate(gid, step_index=0, **extra):
    gate = {"id": gid, "step_index": step_index, "status": "awaiting-owner",
            "notified": False, "token": _tok(gid)}
    gate.update(extra)
    return gate


def test_gate_order_and_no_legacy_constant():
    assert fs.GATE_ORDER == ("decide", "spec", "accept")
    assert fs.ACTIVE_GATES == ("spec", "accept")
    assert not hasattr(fs, "GATE_IDS")


def test_positions_and_labels():
    assert fs.gate_position("spec") == 1
    assert fs.gate_position("accept") == 2
    assert fs.gate_position("decide") is None
    assert fs.gate_position("G2") == 1
    assert fs.gate_position("G4") == 2
    assert fs.gate_position("G3") is None
    assert fs.gate_label("accept") == "gate 2 of 2"
    assert fs.gate_label("spec") == "gate 1 of 2"
    assert fs.gate_label("decide") == ""


@pytest.mark.parametrize("raw,want", [
    ("G1", "spec"), ("G2", "spec"), ("G4", "accept"), ("H3", "accept"),
    ("G3", "learnings"), ("H4", "learnings"), ("g2", "spec"),
    ("decide", "decide"), ("spec", "spec"), ("accept", "accept"),
    ("G9", "G9"), (None, None),
])
def test_canonical_gate(raw, want):
    assert fs.canonical_gate(raw) == want


def test_is_retired_gate():
    assert fs.is_retired_gate("G3") and fs.is_retired_gate("H4")
    assert not fs.is_retired_gate("G2") and not fs.is_retired_gate("spec")
    assert not fs.is_retired_gate(None)


def test_no_stored_value_carries_a_number():
    values = list(fs.GATE_ORDER) + list(fs.ACTIVE_GATES)
    for key, verbs in fs.VERDICTS_BY_GATE.items():
        values.append(key)
        values.extend(verbs)
    assert not [v for v in values if re.search(r"\d", v)]


def test_normalize_gate():
    tok = _tok("G1")
    gate = {"id": "G1", "step_index": None, "token": tok}
    out = fs.normalize_gate(gate)
    assert out is gate
    assert gate["id"] == "spec" and gate["step_index"] == 0 and gate["token"] == tok
    retired = fs.normalize_gate({"id": "G3", "step_index": 1})
    assert retired["id"] == "G3"
    assert fs.normalize_gate({"id": "G4", "step_index": 2})["id"] == "accept"
    assert fs.normalize_gate(None) is None


def test_normalize_flow():
    flow = _flow_with_gate(_gate("G2"))
    assert fs.normalize_flow(flow) is flow
    assert flow["gate"]["id"] == "spec"
    assert fs.normalize_flow(_flow_with_gate(None))["gate"] is None


def test_validate_accepts_old_and_new_ids():
    assert fs.validate(_flow_with_gate(_gate("G2"))) == []
    assert fs.validate(_flow_with_gate(_gate("spec"))) == []
    assert fs.validate(_flow_with_gate(_gate("accept"))) == []


def test_validate_rejects_retired_and_unknown():
    errs = fs.validate(_flow_with_gate(_gate("G3")))
    assert any("retired" in e for e in errs)
    errs = fs.validate(_flow_with_gate(_gate("G9")))
    assert any("not in ('spec', 'accept')" in e for e in errs)


def test_validate_null_step_only_for_literal_g1():
    assert any("step_index" in e for e in fs.validate(_flow_with_gate(_gate("spec", None))))
    assert fs.validate(_flow_with_gate(_gate("G1", None))) == []


def test_validate_verdict_uses_canonical_gate():
    gate = _gate("G4", status="decided")
    gate["decision"] = {"verdict": "ship", "note": "", "surface": "cli",
                        "decided_at": "2026-11-03T00:00:00Z", "message_ref": None}
    flow = _flow_with_gate(gate)
    assert fs.validate(flow) == []
    bad = copy.deepcopy(flow)
    bad["gate"]["decision"]["verdict"] = "approve"
    assert any("not valid" in e for e in fs.validate(bad))


def test_valid_verdicts():
    assert fs.valid_verdicts("G4") == ("ship", "hold", "park")
    assert fs.valid_verdicts("G2") == fs.valid_verdicts("spec") == ("approve", "reject", "park")
    assert fs.valid_verdicts("G3") == ("park",)
    assert fs.valid_verdicts("decide") == ("park",)


def test_verdict_meanings():
    assert fs.VERDICT_MEANINGS["reject"] == "Reject (do not build this step)"
    assert fs.VERDICT_MEANINGS["ship"] == "Ship (accept and merge)"
    assert fs.VERDICT_MEANINGS["hold"] == "Hold (send back)"

def _board_common():
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[4]
            / "board" / "board_common.py")
    spec = importlib.util.spec_from_file_location("_board_common_drift", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DRIFT_NAMES = ("GATE_ALIASES", "RETIRED_GATES", "GATE_ORDER", "ACTIVE_GATES",
                "LEARNINGS")


def test_board_copy_of_the_gate_tables_matches_the_engine():
    bc = _board_common()
    for name in _DRIFT_NAMES:
        assert getattr(bc, name) == getattr(fs, name), name


def test_the_drift_guard_fails_when_one_table_changes(monkeypatch):
    monkeypatch.setitem(fs.GATE_ALIASES, "G1", "accept")
    with pytest.raises(AssertionError):
        test_board_copy_of_the_gate_tables_matches_the_engine()
