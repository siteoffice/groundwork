"""A reply to a retired (G3 or H4) gate token gets a plain 'gate retired' ignore."""
import flow_state as fs
import sweep_core as sc
from test_sweep_core import _flow, _flow_ctx, _gate, _msg

def _retired(gate):
    return "-".join(["FLOW", "DSI", "payoff", gate, "S2", "20260718T091403Z", "7QF3"])


G3_TOKEN = _retired("G3")
H4_TOKEN = _retired("H4")


def _plan(body, flows):
    return sc.plan_decisions([_msg(body=body)], flows)


def test_g3_token_with_no_matching_flow_is_gate_retired():
    out = _plan(f"approve\n> {G3_TOKEN}", [])
    assert out["decisions"] == []
    assert len(out["ignored"]) == 1
    rec = out["ignored"][0]
    assert rec["reason"] == sc.WRONG_TOKEN
    assert rec["detail"] == sc.GATE_RETIRED == "gate retired"
    assert rec["token"] == G3_TOKEN
    assert out["mark_processed"] == []


def test_h4_token_is_gate_retired_even_when_wrapped():
    out = _plan(f"ship it `{H4_TOKEN}`.", [])
    assert out["ignored"][0]["detail"] == "gate retired"
    assert out["ignored"][0]["token"] == H4_TOKEN


def test_flow_with_stored_g3_gate_is_gate_retired_and_ledgered():
    gate = _gate(gate_id="G3")
    flow = _flow(gate=gate)
    flow["gate"]["id"] = "G3"
    out = _plan(f"approve {gate['token']}", [_flow_ctx(flow)])
    assert out["decisions"] == []
    rec = out["ignored"][0]
    assert rec["reason"] == sc.WRONG_TOKEN
    assert rec["detail"] == "gate retired"
    assert out["mark_processed"] == ["<AAA>"]


def test_live_spec_token_still_decides():
    flow = _flow()
    out = _plan(f"approve\n{flow['gate']['token']}", [_flow_ctx(flow)])
    assert [d["verdict"] for d in out["decisions"]] == ["approve"]


def test_unrelated_token_is_plain_wrong_token():
    other = "FLOW-DSI-other-spec-S1-20260718T091403Z-ZZZZ"
    out = _plan(f"approve {other}", [_flow_ctx(_flow())])
    assert out["decisions"] == []
    rec = out["ignored"][0]
    assert rec["reason"] == sc.WRONG_TOKEN
    assert "detail" not in rec
