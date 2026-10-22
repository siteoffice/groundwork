"""Tests for sweep_core.py. Plain pytest, stdlib only, fixtures only.

No live mailbox is read and no email is sent anywhere in these tests or in the
module under test (spec lines 82-94). Every timestamp and the escalation window
are injected so the tests are deterministic.
"""
import flow_state as fs
import outbox
import sweep_core as sc


# --- shared fixture builders (used by Tasks 1-3) ---------------------------

def _gate(project="dsi", slug="payoff", gate_id="G2", step_index=2,
          status="awaiting-owner", opened_at="2026-07-18T09:14:03Z",
          nonce="7QF3", verdict=None):
    token = fs.gate_token(project, slug, gate_id, step_index, opened_at, nonce)
    decision = None
    if verdict is not None:
        decision = {"verdict": verdict, "note": "", "surface": "session",
                    "decided_at": "2026-07-18T09:20:00Z", "message_ref": None}
    return {
        "id": gate_id, "step_index": step_index, "status": status,
        "decision": decision, "notified": False,
        "token": token, "opened_at": opened_at,
    }


def _flow(project="dsi", slug="payoff", gate=None, stage="execute"):
    return {
        "schema_version": fs.SCHEMA_VERSION,
        "flow_id": fs.make_flow_id(project, slug, "2026-07-18T09:14:03Z"),
        "project": project, "slug": slug, "idea": "measure payoff",
        "stage": stage, "status": "awaiting-owner",
        "current_step": {"index": 2, "of": 4, "phase": "sprint"},
        "steps": [{"index": 2, "title": "build it", "status": "running"}],
        "model_hint": {"execute": "sonnet"},
        "gate": gate if gate is not None else _gate(project, slug),
        "created_at": "2026-07-18T09:14:03Z",
        "updated_at": "2026-07-18T09:14:03Z",
    }


def _msg(message_id="<AAA>", body="approve", subject=None,
         received_at="2026-07-18T09:31:00Z", in_reply_to="<sent-99>",
         conversation_id=None):
    if subject is None:
        subject = "[FLOW dsi/payoff] gate: spec approval"
    return {"message_id": message_id, "subject": subject, "body": body,
            "received_at": received_at, "in_reply_to": in_reply_to,
            "conversation_id": conversation_id}


def _flow_ctx(flow=None, sent_message_ids=("<sent-99>",),
              sent_conversation_ids=(), processed_ids=()):
    return {"flow": flow if flow is not None else _flow(),
            "sent_message_ids": list(sent_message_ids),
            "sent_conversation_ids": list(sent_conversation_ids),
            "processed_ids": list(processed_ids)}


def _pair(flow, entries):
    return (flow, entries)


# --- parse_verb ------------------------------------------------------------

def test_parse_verb_first_word_case_insensitive():
    assert sc.parse_verb("Approve\n\nthanks") == ("approve", "")
    assert sc.parse_verb("REJECT") == ("reject", "")


def test_parse_verb_adapt_captures_note():
    verb, note = sc.parse_verb("adapt: tighten the window to 5 minutes")
    assert verb == "adapt"
    assert note == "tighten the window to 5 minutes"


def test_parse_verb_adapt_without_colon_still_captures_note():
    verb, note = sc.parse_verb("Adapt drop the caching step")
    assert verb == "adapt"
    assert note == "drop the caching step"


def test_parse_verb_unrecognized_start_returns_none():
    assert sc.parse_verb("please approve this when you can") == (None, "")
    assert sc.parse_verb("") == (None, "")


def test_parse_verb_strips_leading_backtick_wrapper():
    # Real-world case (2026-08-01 G3 thread): Wilco wrapped the verb in
    # backticks to match the token's styling, and the reply was silently
    # dropped as NO_RECOGNIZED_VERB because the body didn't start with a letter.
    assert sc.parse_verb("`proceed` token `FLOW-...-7D60`") == ("proceed", "")


def test_parse_verb_strips_other_leading_markdown_wrappers():
    assert sc.parse_verb("*approve* please") == ("approve", "")
    assert sc.parse_verb("_reject_") == ("reject", "")
    assert sc.parse_verb("~close~") == ("close", "")


def test_parse_verb_adapt_with_backtick_wrapper_still_captures_note():
    verb, note = sc.parse_verb("`adapt`: tighten the window to 5 minutes")
    assert verb == "adapt"
    assert note == "tighten the window to 5 minutes"


def test_recognized_verbs_cover_every_gate_verdict():
    for gid in fs.GATE_IDS:
        for verdict in fs.valid_verdicts(gid):
            assert verdict in sc.RECOGNIZED_VERBS


def test_recognized_verbs_cover_the_ship_gate_verdicts():
    """Spec item 9: a reply to the sweep's G4 notification has to parse."""
    for verdict in fs.valid_verdicts("G4"):
        assert verdict in sc.RECOGNIZED_VERBS


def test_parse_verb_reads_ship_and_hold():
    assert sc.parse_verb("ship") == ("ship", "")
    assert sc.parse_verb("hold") == ("hold", "")


def test_parse_verb_reads_ship_through_markdown_wrappers():
    """Same leading-wrapper tolerance every other verb already gets."""
    assert sc.parse_verb("`ship`") == ("ship", "")
    assert sc.parse_verb("**hold**") == ("hold", "")


def test_parse_verb_is_case_insensitive_for_the_new_verbs():
    assert sc.parse_verb("SHIP") == ("ship", "")
    assert sc.parse_verb("Hold") == ("hold", "")


# --- parse_verbs (compound replies) ----------------------------------------

def test_parse_verbs_compound_valid_table():
    cases = (
        ("ship+proceed", [("ship", ""), ("proceed", "")]),
        ("ship + proceed", [("ship", ""), ("proceed", "")]),
        ("  SHIP  +  Proceed  ", [("ship", ""), ("proceed", "")]),
        ("`ship` + `proceed`", [("ship", ""), ("proceed", "")]),
        ("approve+proceed+close", [("approve", ""), ("proceed", ""),
                                   ("close", "")]),
    )
    for body, expected in cases:
        assert sweep_core_parse(body) == expected, body


def sweep_core_parse(body):
    return sc.parse_verbs(body)


def test_parse_verbs_single_verb_matches_parse_verb_contract():
    cases = ("proceed", "APPROVE", "`reject`", "ship")
    for body in cases:
        verb, note = sc.parse_verb(body)
        assert sc.parse_verbs(body) == [(verb, note)], body


def test_parse_verbs_single_adapt_keeps_its_note():
    assert sc.parse_verbs("adapt: tighten the window") == [
        ("adapt", "tighten the window")]


def test_parse_verbs_all_or_nothing_on_one_bad_element():
    cases = (
        "ship+bogus",
        "bogus+ship",
        "ship+proceed+nonsense",
        "ship+",
        "+proceed",
        "ship++proceed",
    )
    for body in cases:
        assert sc.parse_verbs(body) == [], body


def test_parse_verbs_rejects_adapt_inside_a_compound():
    cases = ("ship+adapt", "adapt+proceed", "proceed+adapt: note", "adapt: a+b")
    for body in cases:
        assert sc.parse_verbs(body) == [], body


def test_parse_verbs_empty_body_is_empty_list():
    assert sc.parse_verbs("") == []
    assert sc.parse_verbs("   ") == []


# --- READ_VERBS --------------------------------------------------------------

def test_read_verbs_are_separate_from_recognized_verbs():
    assert sc.READ_VERBS == ("status", "queue", "next")
    for verb in sc.READ_VERBS:
        assert verb not in sc.RECOGNIZED_VERBS
        assert sc.parse_verb(verb) == (None, "")


def test_is_read_verb_table():
    cases = (
        ("status", "status"),
        ("Status", "status"),
        ("  QUEUE  ", "queue"),
        ("`next`", "next"),
        ("next please", "next"),
        ("approve", None),
        ("statuses", None),
        ("", None),
        ("ship+proceed", None),
    )
    for body, expected in cases:
        assert sc.is_read_verb(body) == expected, body


# --- plan_sends ------------------------------------------------------------

def _entry(flow, now="2026-07-18T09:14:03Z"):
    """A real pending outbox record for `flow`, built via outbox.compose."""
    gate = flow["gate"]
    subject, body = outbox.compose(gate, flow)
    return {
        "id": "OBX-" + gate["token"], "flow_id": flow["flow_id"],
        "project": flow["project"], "slug": flow["slug"],
        "gate_id": gate["id"], "token": gate["token"],
        "subject": subject, "body": body,
        "dashboard_link": outbox.dashboard_link(flow),
        "next_command": outbox.next_command(flow),
        "created_at": now,
        "send_after": outbox.send_after_for(gate["opened_at"]),
        "sent_at": None, "sent_message_id": None, "status": "pending",
    }


_ALWAYS = lambda _now: True
_NEVER = lambda _now: False


def test_plan_sends_selects_due_in_window_awaiting_entry():
    flow = _flow()  # gate awaiting, opened 09:14:03 -> send_after 09:29:03
    entry = _entry(flow)
    out = sc.plan_sends([_pair(flow, [entry])], now="2026-07-18T09:35:00Z",
                        window=_ALWAYS)
    assert out["send"] == [entry]
    assert out["void"] == []


def test_plan_sends_voids_decided_meanwhile_entry():
    flow = _flow(gate=_gate(status="decided", verdict="approve"))
    entry = _entry(flow)
    out = sc.plan_sends([_pair(flow, [entry])], now="2026-07-18T09:35:00Z",
                        window=_ALWAYS)
    assert out["void"] == [entry]
    assert out["send"] == []


def test_plan_sends_skips_out_of_window_entry():
    flow = _flow()  # due and still awaiting
    entry = _entry(flow)
    out = sc.plan_sends([_pair(flow, [entry])], now="2026-07-18T09:35:00Z",
                        window=_NEVER)
    assert out["send"] == []      # out of window: deferred, not sent
    assert out["void"] == []


def test_plan_sends_skips_not_yet_due_entry():
    flow = _flow()
    entry = _entry(flow)
    # now is before send_after (09:29:03): still inside the 15-min ladder.
    out = sc.plan_sends([_pair(flow, [entry])], now="2026-07-18T09:20:00Z",
                        window=_ALWAYS)
    assert out["send"] == []
    assert out["void"] == []


def test_default_send_window_gates_by_amsterdam_hour():
    # 07:00Z = 09:00 CEST (summer, DST) -> inside 08-22.
    assert sc.in_default_send_window("2026-07-18T07:00:00Z") is True
    # 21:30Z = 23:30 CEST -> outside 08-22.
    assert sc.in_default_send_window("2026-07-18T21:30:00Z") is False


# --- plan_decisions --------------------------------------------------------

def _token_of(flow):
    return flow["gate"]["token"]


def test_plan_decisions_accepts_valid_threaded_reply():
    flow = _flow()
    tok = _token_of(flow)
    msg = _msg(message_id="<AAA>", body=f"approve\n\ntoken {tok}",
               in_reply_to="<sent-99>", received_at="2026-07-18T09:31:00Z")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["ignored"] == []
    assert len(out["decisions"]) == 1
    d = out["decisions"][0]
    assert d["project"] == "dsi" and d["slug"] == "payoff"
    assert d["verdict"] == "approve"
    assert d["surface"] == "email"
    assert d["message_ref"] == "<AAA>"
    assert "<AAA>" in out["mark_processed"]


def test_plan_decisions_accepts_fresh_mail_by_exact_subject():
    flow = _flow()
    tok = _token_of(flow)
    # Not a thread reply (in_reply_to unknown), but exact gate subject + token.
    msg = _msg(message_id="<FRESH>", body=f"approve token {tok}",
               subject="[FLOW dsi/payoff] gate: spec approval",
               in_reply_to="<unrelated>")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["verdict"] == "approve"


def test_plan_decisions_rejects_wrong_token():
    flow = _flow()
    msg = _msg(message_id="<BBB>", body="approve token FLOW-DSI-payoff-G2-S2-19990101T000000Z-XXXX")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.WRONG_TOKEN
    assert "<BBB>" not in out["mark_processed"]   # not ours: not ledgered


def test_plan_decisions_rejects_not_threaded_and_wrong_subject():
    flow = _flow()
    tok = _token_of(flow)
    msg = _msg(message_id="<CCC>", body=f"approve token {tok}",
               subject="Re: something else", in_reply_to="<unrelated>",
               conversation_id="other-conv")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.NOT_THREADED
    assert "<CCC>" in out["mark_processed"]        # matched our token: ledgered


def test_plan_decisions_rejects_before_open_time():
    flow = _flow()  # gate opened 09:14:03
    tok = _token_of(flow)
    msg = _msg(message_id="<DDD>", body=f"approve token {tok}",
               received_at="2026-07-18T09:00:00Z")   # before opened_at
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.BEFORE_OPEN


def test_plan_decisions_rejects_already_processed_id():
    flow = _flow()
    tok = _token_of(flow)
    msg = _msg(message_id="<AAA>", body=f"approve token {tok}")
    out = sc.plan_decisions([msg], [_flow_ctx(flow, processed_ids=("<AAA>",))])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.ALREADY_PROCESSED


def test_plan_decisions_invalid_verb_for_gate_yields_courtesy():
    flow = _flow(gate=_gate(gate_id="G1", step_index=None))  # G1: approve|reject|park
    tok = _token_of(flow)
    msg = _msg(message_id="<EEE>", body=f"proceed token {tok}",
               subject="[FLOW dsi/payoff] gate: design approval")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.INVALID_VERB_FOR_GATE
    assert len(out["courtesy_replies"]) == 1
    cr = out["courtesy_replies"][0]
    assert cr["gate_id"] == "G1"
    assert "approve" in cr["valid_verbs"] and "reject" in cr["valid_verbs"]
    assert "proceed" not in cr["valid_verbs"]
    assert "<EEE>" in out["mark_processed"]


def test_plan_decisions_first_decision_wins_on_decided_gate():
    flow = _flow(gate=_gate(status="decided", verdict="approve"))
    tok = _token_of(flow)
    msg = _msg(message_id="<FFF>", body=f"reject token {tok}")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert out["decisions"] == []
    assert out["ignored"][0]["reason"] == sc.ALREADY_DECIDED
    assert "<FFF>" in out["mark_processed"]


def test_plan_decisions_extracts_adapt_note_on_g3():
    flow = _flow(gate=_gate(gate_id="G3", step_index=1))  # G3: proceed|adapt|close|park
    tok = _token_of(flow)
    msg = _msg(message_id="<GGG>", body=f"adapt: shrink the batch size\n\ntoken {tok}",
               subject="[FLOW dsi/payoff] gate: learnings review")
    out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert len(out["decisions"]) == 1
    d = out["decisions"][0]
    assert d["verdict"] == "adapt"
    assert d["note"] == "shrink the batch size"


def test_plan_decisions_surface_defaults_email_and_honors_override():
    flow = _flow()
    tok = _token_of(flow)
    msg = _msg(message_id="<SFC>", body=f"approve\n\ntoken {tok}",
               in_reply_to="<sent-99>", received_at="2026-07-18T09:31:00Z")
    # default keeps "email"
    default_out = sc.plan_decisions([msg], [_flow_ctx(flow)])
    assert default_out["decisions"][0]["surface"] == "email"
    # explicit surface flows through onto the decision
    slack_out = sc.plan_decisions([msg], [_flow_ctx(flow)], surface="slack")
    assert slack_out["decisions"][0]["surface"] == "slack"
