import datetime as _dt

import flow_state as fs
import sweep_core as sc
import slack_channel as slk


PARENT_TS = "1721640000.000000"   # 2024-... parent; exact value irrelevant


def _gate(project="dsi", slug="payoff", gate_id="G2", step_index=2,
          status="awaiting-owner", opened_at="2026-07-18T09:14:03Z", nonce="7QF3"):
    token = fs.gate_token(project, slug, gate_id, step_index, opened_at, nonce)
    return {"id": gate_id, "step_index": step_index, "status": status,
            "decision": None, "notified": False, "token": token,
            "opened_at": opened_at}


def _flow(gate):
    return {"schema_version": fs.SCHEMA_VERSION,
            "flow_id": fs.make_flow_id("dsi", "payoff", "2026-07-18T09:14:03Z"),
            "project": "dsi", "slug": "payoff", "idea": "measure payoff",
            "stage": "execute", "status": "awaiting-owner",
            "current_step": {"index": 2, "of": 4, "phase": "sprint"},
            "steps": [], "model_hint": {}, "gate": gate,
            "created_at": "2026-07-18T09:14:03Z",
            "updated_at": "2026-07-18T09:14:03Z"}


def _ctx(flow, processed_ids=()):
    return {"flow": flow, "sent_message_ids": [],
            "sent_conversation_ids": [], "processed_ids": list(processed_ids)}


def _ts_after_open():
    # a ts strictly after opened_at 2026-07-18T09:14:03Z
    t = _dt.datetime(2026, 7, 18, 9, 31, 0, tzinfo=_dt.timezone.utc)
    return "{:.6f}".format(t.timestamp())


def _ts_before_open():
    t = _dt.datetime(2026, 7, 18, 9, 0, 0, tzinfo=_dt.timezone.utc)
    return "{:.6f}".format(t.timestamp())


def _reply(ts, text, thread_ts=PARENT_TS):
    return {"ts": ts, "thread_ts": thread_ts, "text": text, "user": "U0B9L0Z4B5W"}


def test_normalize_injects_token_so_plain_verb_reply_decides():
    # Changed 2026-08-01: the token used to be required verbatim in the reply
    # body; now normalize_thread injects it, since being in the gate's own
    # thread already identifies it unambiguously. A bare "approve" (no token
    # typed at all) must now decide, not fall into WRONG_TOKEN.
    flow = _flow(_gate())
    after_open_later = "{:.6f}".format(
        _dt.datetime(2026, 7, 18, 9, 45, 0, tzinfo=_dt.timezone.utc).timestamp())
    replies = [
        _reply(PARENT_TS, "gate prompt body"),           # parent, excluded
        _reply(_ts_before_open(), "approve"),             # before open
        _reply(after_open_later, "approve please"),      # valid verb, no token typed
    ]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies, _ctx(flow))
    assert ctx2["sent_conversation_ids"] == [PARENT_TS]
    assert all(m["message_id"] != PARENT_TS for m in msgs)     # parent excluded
    assert all(flow["gate"]["token"] in m["body"] for m in msgs)  # token injected
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert len(out["decisions"]) == 1                          # only the after-open one
    assert out["decisions"][0]["surface"] == "slack"
    assert out["decisions"][0]["verdict"] == "approve"
    assert any(ig["reason"] == sc.BEFORE_OPEN for ig in out["ignored"])


def test_normalize_dedups_duplicate_ts_before_deciding():
    flow = _flow(_gate())
    replies = [
        _reply(_ts_after_open(), "approve"),
        _reply(_ts_after_open(), "approve"),   # duplicate ts, must collapse to one
    ]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies, _ctx(flow))
    assert len(msgs) == 1
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert len(out["decisions"]) == 1


def test_normalize_thread_wrong_thread_ts_not_threaded():
    flow = _flow(_gate())
    tok = flow["gate"]["token"]
    replies = [_reply(_ts_after_open(), f"approve token {tok}", thread_ts="other.ts")]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies, _ctx(flow))
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.NOT_THREADED


def test_normalize_before_open_ignored():
    flow = _flow(_gate())
    tok = flow["gate"]["token"]
    replies = [_reply(_ts_before_open(), f"approve token {tok}")]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies, _ctx(flow))
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.BEFORE_OPEN


def test_normalize_already_processed_is_noop():
    flow = _flow(_gate())
    tok = flow["gate"]["token"]
    ts = _ts_after_open()
    replies = [_reply(ts, f"approve token {tok}")]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies,
                                      _ctx(flow, processed_ids=(ts,)))
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.ALREADY_PROCESSED


def test_normalize_invalid_verb_for_gate_yields_courtesy():
    flow = _flow(_gate(gate_id="G1", step_index=None))   # G1: approve|reject|park
    tok = flow["gate"]["token"]
    replies = [_reply(_ts_after_open(), f"proceed token {tok}")]
    msgs, ctx2 = slk.normalize_thread(PARENT_TS, "C123", replies, _ctx(flow))
    out = sc.plan_decisions(msgs, [ctx2], surface="slack")
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.INVALID_VERB_FOR_GATE
    assert len(out["courtesy_replies"]) == 1


def test_compose_gate_prompt_contains_token_verbatim():
    flow = _flow(_gate())
    tok = flow["gate"]["token"]
    body = slk.compose_gate_prompt(flow["gate"], flow)
    assert tok in body                                   # still shown, for reference
    assert "spec approval" in body                       # outbox.gate_name("G2")
    assert "approve" in body and "reject" in body        # outbox.reply_verbs_line
    assert "file:///" in body                            # dashboard link
    assert "measure payoff" in body                      # flow idea (more context)
    assert "dsi/payoff" in body                           # project/slug context
    assert "no need to include the token" in body        # token no longer required
    assert "—" not in body                          # no em dash (U+2014)


def test_compose_reminder_repeats_context_and_verbs():
    flow = _flow(_gate())
    tok = flow["gate"]["token"]
    body = slk.compose_reminder(flow["gate"], flow)
    assert "dsi/payoff" in body                          # project/slug context
    assert "spec approval" in body                       # gate name
    assert "measure payoff" in body                      # idea
    assert "approve" in body and "reject" in body        # reply verbs, not just a token
    assert tok in body
    assert "file:///" in body
    assert "—" not in body


def test_compose_alert_digest_has_gates_and_week_plan():
    body = slk.compose_alert_digest(
        open_gates=["dsi/payoff G2 (spec approval)"],
        week_plan=["WED: DSI S1 calls", "THU: DSI build behind S1"],
        generated_at="2026-07-22T08:00:00Z")
    assert "dsi/payoff G2 (spec approval)" in body      # open-gate list
    assert "WED: DSI S1 calls" in body                  # this-week plan
    assert "—" not in body


def test_compose_alert_digest_empty_gates_states_nothing_waiting():
    body = slk.compose_alert_digest(open_gates=[],
                                    week_plan=["FRI: LinkedIn post"],
                                    generated_at="2026-07-22T08:00:00Z")
    assert "FRI: LinkedIn post" in body
    assert "nothing waiting on you" in body.lower()


def test_compose_alert_delta_lists_only_new():
    body = slk.compose_alert_delta(["new flag X"], generated_at="2026-07-22T13:00:00Z")
    assert "new flag X" in body
