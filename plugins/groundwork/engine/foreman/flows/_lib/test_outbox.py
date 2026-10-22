"""Tests for outbox.py. Plain pytest, stdlib only, hermetic (tmp_path).

Covers the four spec assertions (spec lines 75-84):
  1. gate-open append shape          -> Task 2
  2. compose subject + body contents -> Task 1 (this block)
  3. rerun non-duplication           -> Task 2
  4. voided-not-sent + compose purity -> purity here (Task 1), void in Task 2
"""
import json

import flow_state as fs
import outbox


def _flow(project="dsi", slug="payoff", gate_id="G2", step_index=2,
          now="2026-07-18T09:14:03Z", nonce="7QF3"):
    """A flow parked in execute with a fresh awaiting-owner gate (Task-1 fixture).

    Shape matches flow_transition.open_gate exactly.
    """
    token = fs.gate_token(project, slug, gate_id, step_index, now, nonce)
    return {
        "schema_version": fs.SCHEMA_VERSION,
        "flow_id": fs.make_flow_id(project, slug, now),
        "project": project,
        "slug": slug,
        "idea": "measure the payoff of books",
        "stage": "execute",
        "status": "awaiting-owner",
        "current_step": {"index": step_index, "of": 4, "phase": "sprint"},
        "steps": [{"index": step_index, "title": "build it", "status": "running"}],
        "model_hint": {"execute": "sonnet"},
        "gate": {
            "id": gate_id,
            "step_index": step_index,
            "status": "awaiting-owner",
            "decision": None,
            "notified": False,
            "token": token,
            "opened_at": now,
        },
        "created_at": now,
        "updated_at": now,
    }


def test_compose_subject_uses_flow_gate_name_convention():
    flow = _flow(project="dsi", slug="payoff", gate_id="G2")
    subject, _body = outbox.compose(flow["gate"], flow)
    assert subject == "[FLOW dsi/payoff] gate: spec approval"


def test_compose_body_contains_token_link_command_and_verbs():
    flow = _flow(gate_id="G2")
    gate = flow["gate"]
    _subject, body = outbox.compose(gate, flow)
    # flow ID and gate token
    assert flow["flow_id"] in body
    assert gate["token"] in body
    # dashboard link is file:// safe
    assert outbox.dashboard_link(flow) in body
    assert body.count("file:///") >= 1
    # the exact next command
    assert "/flow dsi payoff" in body
    # gate-appropriate verbs for G2: approve | reject | park
    assert "approve" in body and "reject" in body and "park" in body


def test_compose_g3_body_offers_proceed_adapt_close_verbs():
    flow = _flow(gate_id="G3", step_index=1)
    _subject, body = outbox.compose(flow["gate"], flow)
    assert "proceed" in body
    assert "adapt: <note>" in body
    assert "close" in body
    assert "park" in body


def test_compose_is_pure_same_inputs_same_outputs():
    flow = _flow()
    first = outbox.compose(flow["gate"], flow)
    second = outbox.compose(flow["gate"], flow)
    assert first == second


def test_compose_does_no_file_io(tmp_path, monkeypatch):
    # Run with cwd inside an empty tmp dir; compose must not create any file.
    monkeypatch.chdir(tmp_path)
    flow = _flow()
    outbox.compose(flow["gate"], flow)
    assert list(tmp_path.iterdir()) == []


# --- Task 2: append + status transitions -----------------------------------

def _read_outbox_lines(flow_dir):
    p = flow_dir / "outbox.ndjson"
    lines = p.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def test_append_gate_open_writes_one_pending_entry_with_send_after(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")

    assert rec is not None
    rows = _read_outbox_lines(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "pending"
    assert row["sent_at"] is None
    assert row["sent_message_id"] is None
    # send_after == opened_at + 15 min (spec lines 42-44)
    assert row["send_after"] == "2026-07-18T09:29:03Z"
    # record carries the composed subject/body and the derived links
    assert row["subject"] == "[FLOW dsi/payoff] gate: spec approval"
    assert row["gate_id"] == "G2"
    assert row["token"] == flow["gate"]["token"]
    assert row["dashboard_link"] == outbox.dashboard_link(flow)
    assert row["next_command"] == "/flow dsi payoff"
    # every spec-required field is present
    for key in ("id", "flow_id", "project", "slug", "gate_id", "token",
                "subject", "body", "dashboard_link", "next_command",
                "created_at", "send_after", "sent_at", "sent_message_id",
                "status"):
        assert key in row


def test_append_gate_open_is_rerun_safe_no_duplicate(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    first = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")
    # A rerun re-invokes append with a fresh now; must NOT add a second entry.
    second = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:40:00Z")

    assert first is not None
    assert second is None
    rows = _read_outbox_lines(tmp_path)
    assert len(rows) == 1


def test_classify_returns_wait_before_send_after(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")
    # now is before send_after (09:29:03) and the gate is still awaiting.
    assert outbox.classify(rec, flow, now="2026-07-18T09:20:00Z") == "wait"


def test_classify_returns_send_when_due_and_still_awaiting(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")
    # now is past send_after and the gate is still awaiting.
    assert outbox.classify(rec, flow, now="2026-07-18T09:35:00Z") == "send"


def test_decided_in_session_gate_is_voided_not_sent(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")

    # Wilco answered in-session before send_after: the gate is now decided.
    flow["gate"]["status"] = "decided"
    flow["gate"]["decision"] = {
        "verdict": "approve", "note": "", "surface": "session",
        "decided_at": "2026-07-18T09:18:00Z", "message_ref": None,
    }
    # The notifier's pure decision: void, not send.
    assert outbox.classify(rec, flow, now="2026-07-18T09:35:00Z") == "void"

    outbox.mark_voided(tmp_path, rec["id"], now="2026-07-18T09:35:00Z")
    row = _read_outbox_lines(tmp_path)[0]
    assert row["status"] == "voided"
    assert row["sent_at"] is None          # voided is NOT sent
    assert row["sent_message_id"] is None


def test_append_gate_open_has_null_slack_fields(tmp_path):
    flow = _flow(now="2026-07-22T09:00:00Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-22T09:00:00Z")
    assert rec["slack_channel_id"] is None
    assert rec["slack_parent_ts"] is None


def test_gate_idempotency_one_prompt_parent_ts_set_once(tmp_path):
    flow = _flow(now="2026-07-22T09:00:00Z")
    first = outbox.append_gate_open(tmp_path, flow, now="2026-07-22T09:00:00Z")
    # second sweep over the same open gate: no duplicate entry
    dup = outbox.append_gate_open(tmp_path, flow, now="2026-07-22T09:30:00Z")
    assert dup is None
    assert len(_read_outbox_lines(tmp_path)) == 1
    # host posts once and records the parent ts
    outbox.mark_slack_posted(tmp_path, first["id"], "C123", "1721640843.000100")
    rows = _read_outbox_lines(tmp_path)
    assert rows[0]["slack_channel_id"] == "C123"
    assert rows[0]["slack_parent_ts"] == "1721640843.000100"
    # a second post attempt does not overwrite (set-once)
    outbox.mark_slack_posted(tmp_path, first["id"], "C999", "9999999999.000000")
    rows = _read_outbox_lines(tmp_path)
    assert rows[0]["slack_channel_id"] == "C123"
    assert rows[0]["slack_parent_ts"] == "1721640843.000100"


def test_mark_sent_sets_sent_fields(tmp_path):
    flow = _flow(now="2026-07-18T09:14:03Z")
    rec = outbox.append_gate_open(tmp_path, flow, now="2026-07-18T09:14:03Z")

    outbox.mark_sent(tmp_path, rec["id"], message_id="<msg-99>",
                     now="2026-07-18T09:35:00Z")
    row = _read_outbox_lines(tmp_path)[0]
    assert row["status"] == "sent"
    assert row["sent_at"] == "2026-07-18T09:35:00Z"
    assert row["sent_message_id"] == "<msg-99>"


# --- Task 3: additive sweep_context guidance block --------------------------

def test_compose_body_unchanged_without_sweep_context():
    """Regression guard: a normal (non-swept) gate composes exactly as before."""
    flow = _flow(gate_id="G3", step_index=2)
    gate = flow["gate"]
    assert "sweep_context" not in gate
    subject, body = outbox.compose(gate, flow)
    # No guidance block markers leak into a normal gate body.
    assert "Sprint status:" not in body
    assert "Suggested:" not in body
    # Standard G3 verbs still present.
    assert "proceed" in body


def test_compose_body_renders_guidance_block_when_sweep_context_present():
    flow = _flow(gate_id="G3", step_index=2)
    gate = flow["gate"]
    gate["sweep_context"] = {
        "status": "failed", "class": "needs-guidance",
        "verify_summary": "latency 780ms vs target 500ms",
        "test_summary": "3 failed, 47 passed",
        "suggested_verbs": ["adapt: <note>", "close",
                            "(proceed available but not suggested)"],
    }
    subject, body = outbox.compose(gate, flow)
    assert "Sprint status: failed" in body
    assert "780ms" in body
    assert "3 failed" in body
    assert "adapt" in body and "close" in body


# --- find_step_parent_ts (one Slack thread per step) ------------------------

def _entry(token, created_at, parent_ts, entry_id=None):
    """A minimal outbox record: only the fields find_step_parent_ts reads."""
    return {
        "id": entry_id or ("OBX-" + token),
        "token": token,
        "created_at": created_at,
        "slack_channel_id": "C0BK70BTFQ9" if parent_ts else None,
        "slack_parent_ts": parent_ts,
        "status": "pending",
    }


def _write_entries(flow_dir, entries):
    flow_dir.mkdir(parents=True, exist_ok=True)
    with (flow_dir / "outbox.ndjson").open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return flow_dir


def test_token_step_index_table():
    cases = (
        ("FLOW-DSI-payoff-G2-S2-20260718T091403Z-7QF3", 2),
        ("FLOW-DSI-a-b-c-slug-G3-S11-20260718T091403Z-AB12", 11),
        ("FLOW-DSI-payoff-G1-20260718T091403Z-7QF3", None),
        ("", None),
        ("not-a-token", None),
    )
    for token, expected in cases:
        assert outbox.token_step_index(token) == expected, token


def test_find_step_parent_ts_returns_earliest_matching_thread(tmp_path):
    flow_dir = _write_entries(tmp_path / "dsi" / "payoff", [
        _entry("FLOW-DSI-payoff-G2-S2-20260718T091403Z-AAAA",
               "2026-07-18T09:14:03Z", "1700000100.000100"),
        _entry("FLOW-DSI-payoff-G3-S2-20260718T101403Z-BBBB",
               "2026-07-18T10:14:03Z", "1700000900.000900"),
    ])
    assert outbox.find_step_parent_ts(flow_dir, 2) == "1700000100.000100"


def test_find_step_parent_ts_ignores_other_steps_and_unposted(tmp_path):
    flow_dir = _write_entries(tmp_path / "dsi" / "payoff", [
        _entry("FLOW-DSI-payoff-G2-S1-20260718T081403Z-AAAA",
               "2026-07-18T08:14:03Z", "1700000000.000000"),
        _entry("FLOW-DSI-payoff-G2-S2-20260718T091403Z-BBBB",
               "2026-07-18T09:14:03Z", None),
        _entry("FLOW-DSI-payoff-G3-S2-20260718T101403Z-CCCC",
               "2026-07-18T10:14:03Z", "1700000900.000900"),
    ])
    assert outbox.find_step_parent_ts(flow_dir, 2) == "1700000900.000900"
    assert outbox.find_step_parent_ts(flow_dir, 1) == "1700000000.000000"


def test_find_step_parent_ts_none_cases(tmp_path):
    empty = tmp_path / "dsi" / "empty"
    empty.mkdir(parents=True)
    assert outbox.find_step_parent_ts(empty, 2) is None

    flow_dir = _write_entries(tmp_path / "dsi" / "payoff", [
        _entry("FLOW-DSI-payoff-G2-S2-20260718T091403Z-AAAA",
               "2026-07-18T09:14:03Z", "1700000100.000100"),
    ])
    assert outbox.find_step_parent_ts(flow_dir, 3) is None
    assert outbox.find_step_parent_ts(flow_dir, None) is None


def test_find_step_parent_ts_is_scoped_to_one_flow(tmp_path):
    ours = _write_entries(tmp_path / "dsi" / "payoff", [
        _entry("FLOW-DSI-payoff-G2-S2-20260718T091403Z-AAAA",
               "2026-07-18T09:14:03Z", "1700000100.000100"),
    ])
    _write_entries(tmp_path / "dsi" / "other", [
        _entry("FLOW-DSI-other-G2-S2-20260718T071403Z-ZZZZ",
               "2026-07-18T07:14:03Z", "1600000000.000000"),
    ])
    assert outbox.find_step_parent_ts(ours, 2) == "1700000100.000100"
