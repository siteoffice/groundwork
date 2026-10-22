"""Tests for slack_sweep_host.py. Plain pytest, stdlib only, hermetic.

No live Slack anywhere: send_message and read_thread are always injected
stubs, the same callable-injection fence the module's own docstring names.
Every timestamp is injected so the tests are deterministic.
"""
import pathlib

import flow_transition as ft
import outbox
import slack_sweep_host as host

GATE_OPENED_AT = "2026-07-18T09:14:03Z"
# Epoch seconds for GATE_OPENED_AT is 1784538843; anything above it is a reply
# that lands AFTER the gate opened, which plan_decisions requires.
TS_AFTER_OPEN = "1784538900.000100"
TS_LATER = "1784539000.000200"
TS_BEFORE_OPEN = "1700000000.000000"
PARENT_TS = "1784538850.000000"


def _flow_dir(tmp_path, project="dsi", slug="payoff", gate_id="G2",
              step_index=2, now=GATE_OPENED_AT, nonce="7QF3"):
    """A real flow tree parked in execute with one open gate."""
    flow_dir, flow = ft.cmd_new(tmp_path, project, "measure the payoff",
                                now="2026-07-18T09:00:00Z", slug=slug)
    flow["stage"] = "execute"
    flow["current_step"] = {"index": step_index, "of": 4, "phase": "sprint"}
    flow["steps"] = [{"index": step_index, "title": "build it",
                      "status": "running"}]
    ft.write_flow(flow_dir, flow)
    ft.persist_open_gate(flow_dir, flow, gate_id, step_index, now=now,
                         nonce=nonce)
    return flow_dir


def _stub_send(ts="1784538850.000000"):
    """send_message stub. Returns (callable, calls) where each call is
    (channel_id, text, thread_ts)."""
    calls = []

    def send_message(channel_id, text, thread_ts=None):
        calls.append((channel_id, text, thread_ts))
        return {"ts": ts, "channel_id": channel_id}

    return send_message, calls


def _stub_read(replies):
    """read_thread stub returning a fixed reply list."""
    def read_thread(channel_id, message_ts):
        return list(replies)

    return read_thread


def _reply(ts, text, thread_ts=PARENT_TS):
    return {"ts": ts, "text": text, "thread_ts": thread_ts}


def _snapshot(flow_dir):
    """Raw bytes of the two files a gate decision would touch."""
    out = {}
    for name in ("flow.json", "flow-log.ndjson"):
        p = pathlib.Path(flow_dir) / name
        out[name] = p.read_bytes() if p.exists() else None
    return out


def _assert_unchanged(flow_dir, snapshot):
    after = _snapshot(flow_dir)
    assert after == snapshot, "flow state changed but should not have"


# --- characterization: the two branches that exist today --------------------

def test_process_open_gate_posts_a_new_prompt_when_never_posted(tmp_path):
    flow_dir = _flow_dir(tmp_path)
    flow = ft.load_flow(flow_dir)
    send_message, calls = _stub_send()

    result = host.process_open_gate(flow_dir, flow, send_message,
                                    _stub_read([]), now="2026-07-18T09:15:00Z")

    assert result["action"] == "posted"
    assert len(calls) == 1
    entry = outbox.read_outbox(flow_dir)[0]
    assert entry["slack_parent_ts"] == "1784538850.000000"


def test_process_open_gate_polls_and_decides_a_single_verb(tmp_path):
    flow_dir = _flow_dir(tmp_path)
    flow = ft.load_flow(flow_dir)
    send_message, _calls = _stub_send()
    host.process_open_gate(flow_dir, flow, send_message, _stub_read([]),
                           now="2026-07-18T09:15:00Z")

    flow = ft.load_flow(flow_dir)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, flow, send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "approve")]),
        now="2026-07-18T09:20:00Z")

    assert result["action"] == "polled"
    assert result["decided"] == 1
    decided = ft.load_flow(flow_dir)
    assert decided["gate"]["status"] == "decided"
    assert decided["gate"]["decision"]["verdict"] == "approve"
    assert calls == []


# --- one Slack thread per step ----------------------------------------------

def _open_next_gate(flow_dir, gate_id, step_index, now, nonce):
    flow = ft.load_flow(flow_dir)
    flow["gate"] = None
    ft.write_flow(flow_dir, flow)
    ft.persist_open_gate(flow_dir, flow, gate_id, step_index, now=now,
                         nonce=nonce)
    return ft.load_flow(flow_dir)


def test_second_gate_of_a_step_posts_into_the_first_gates_thread(tmp_path):
    flow_dir = _flow_dir(tmp_path, gate_id="G2", step_index=2)
    send_message, calls = _stub_send(ts=PARENT_TS)
    host.process_open_gate(flow_dir, ft.load_flow(flow_dir), send_message,
                           _stub_read([]), now="2026-07-18T09:15:00Z")
    assert calls[0][2] is None  # first gate opens the thread

    flow = _open_next_gate(flow_dir, "G3", 2, "2026-07-18T11:00:00Z", "BBBB")
    send_message, calls = _stub_send(ts="1784550000.000000")
    result = host.process_open_gate(flow_dir, flow, send_message,
                                    _stub_read([]), now="2026-07-18T11:01:00Z")

    assert result == {"action": "posted", "threaded": True}
    assert calls[0][2] == PARENT_TS
    entries = {e["gate_id"]: e for e in outbox.read_outbox(flow_dir)}
    assert entries["G3"]["slack_parent_ts"] == PARENT_TS


def test_third_gate_of_a_step_finds_the_same_thread(tmp_path):
    flow_dir = _flow_dir(tmp_path, gate_id="G2", step_index=2)
    send_message, _calls = _stub_send(ts=PARENT_TS)
    host.process_open_gate(flow_dir, ft.load_flow(flow_dir), send_message,
                           _stub_read([]), now="2026-07-18T09:15:00Z")
    for gate_id, opened, nonce in (("G4", "2026-07-18T11:00:00Z", "BBBB"),
                                   ("G3", "2026-07-18T12:00:00Z", "CCCC")):
        flow = _open_next_gate(flow_dir, gate_id, 2, opened, nonce)
        send_message, calls = _stub_send(ts="1784550000.000000")
        host.process_open_gate(flow_dir, flow, send_message, _stub_read([]),
                               now=opened)
        assert calls[0][2] == PARENT_TS, gate_id

    parents = {e["slack_parent_ts"] for e in outbox.read_outbox(flow_dir)}
    assert parents == {PARENT_TS}


def test_a_different_step_opens_its_own_thread(tmp_path):
    flow_dir = _flow_dir(tmp_path, gate_id="G2", step_index=2)
    send_message, _calls = _stub_send(ts=PARENT_TS)
    host.process_open_gate(flow_dir, ft.load_flow(flow_dir), send_message,
                           _stub_read([]), now="2026-07-18T09:15:00Z")

    flow = _open_next_gate(flow_dir, "G2", 3, "2026-07-18T13:00:00Z", "DDDD")
    send_message, calls = _stub_send(ts="1784560000.000000")
    result = host.process_open_gate(flow_dir, flow, send_message,
                                    _stub_read([]), now="2026-07-18T13:01:00Z")

    assert result == {"action": "posted", "threaded": False}
    assert calls[0][2] is None


def test_stale_reply_in_a_reused_thread_does_not_decide_the_new_gate(tmp_path):
    flow_dir = _flow_dir(tmp_path, gate_id="G2", step_index=2)
    send_message, _calls = _stub_send(ts=PARENT_TS)
    host.process_open_gate(flow_dir, ft.load_flow(flow_dir), send_message,
                           _stub_read([]), now="2026-07-18T09:15:00Z")

    # G3 opens at 11:00 and reuses the thread. The old "approve" reply landed
    # before that, so the after-open check must drop it.
    flow = _open_next_gate(flow_dir, "G3", 2, "2026-07-18T11:00:00Z", "BBBB")
    send_message, _calls = _stub_send(ts="1784550000.000000")
    host.process_open_gate(flow_dir, flow, send_message, _stub_read([]),
                           now="2026-07-18T11:01:00Z")

    flow = ft.load_flow(flow_dir)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, flow, send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "approve")]),
        now="2026-07-18T11:05:00Z")

    assert result["decided"] == 0
    assert ft.load_flow(flow_dir)["gate"]["status"] == "awaiting-owner"


# --- read verbs: status / queue / next --------------------------------------

def _posted_thread(tmp_path, **kw):
    """A flow whose gate prompt is already in a thread, ready to be polled."""
    flow_dir = _flow_dir(tmp_path, **kw)
    send_message, _calls = _stub_send(ts=PARENT_TS)
    host.process_open_gate(flow_dir, ft.load_flow(flow_dir), send_message,
                           _stub_read([]), now="2026-07-18T09:15:00Z")
    return flow_dir


def test_read_verb_answers_in_thread_and_writes_nothing(tmp_path):
    flow_dir = _posted_thread(tmp_path)
    for verb in ("status", "queue", "next"):
        before = _snapshot(flow_dir)
        send_message, calls = _stub_send()
        result = host.process_open_gate(
            flow_dir, ft.load_flow(flow_dir), send_message,
            _stub_read([_reply(TS_AFTER_OPEN, verb)]),
            now="2026-07-18T09:20:00Z")

        assert result["answered"] == 1, verb
        assert result["decided"] == 0, verb
        assert len(calls) == 1, verb
        assert calls[0][2] == PARENT_TS, verb
        assert calls[0][1].strip() != "", verb
        _assert_unchanged(flow_dir, before)


def test_read_verb_answers_only_the_newest_reply_per_sweep(tmp_path):
    flow_dir = _posted_thread(tmp_path)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "status"),
                    _reply(TS_LATER, "queue")]),
        now="2026-07-18T09:20:00Z")

    assert result["answered"] == 1
    assert len(calls) == 1


def test_read_verb_and_a_verdict_in_one_sweep_both_happen(tmp_path):
    flow_dir = _posted_thread(tmp_path)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "status"),
                    _reply(TS_LATER, "approve")]),
        now="2026-07-18T09:20:00Z")

    assert result["answered"] == 1
    assert result["decided"] == 1
    assert ft.load_flow(flow_dir)["gate"]["decision"]["verdict"] == "approve"
    assert len(calls) == 1


def test_render_read_verb_table(tmp_path):
    flow_dir = _posted_thread(tmp_path)
    flows_root = pathlib.Path(flow_dir).parent.parent
    for verb in ("status", "queue", "next"):
        text = host.render_read_verb(verb, flows_root, "2026-07-18T09:20:00Z")
        assert isinstance(text, str) and text.strip() != "", verb
    assert host.render_read_verb("bogus", flows_root,
                                 "2026-07-18T09:20:00Z") == ""


# --- compound verdicts -------------------------------------------------------

def test_compound_stops_at_the_verb_it_cannot_apply_and_says_so(tmp_path):
    # G4 accepts ship/hold/park. cmd_gate records the ship decision but never
    # opens G3, so "proceed" has no open gate and the compound stops there.
    flow_dir = _posted_thread(tmp_path, gate_id="G4", step_index=2)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "ship+proceed")]),
        now="2026-07-18T09:20:00Z")

    assert result["decided"] == 1
    assert result["stopped"] is not None
    assert "proceed" in result["stopped"]
    assert len(calls) == 1
    assert calls[0][2] == PARENT_TS
    assert "proceed" in calls[0][1]
    # No rollback: the ship decision stays applied.
    decided = ft.load_flow(flow_dir)
    assert decided["gate"]["decision"]["verdict"] == "ship"


def test_compound_with_an_invalid_element_applies_nothing(tmp_path):
    flow_dir = _posted_thread(tmp_path, gate_id="G4", step_index=2)
    before_gate = ft.load_flow(flow_dir)["gate"]["status"]
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "ship+bogus")]),
        now="2026-07-18T09:20:00Z")

    assert result["decided"] == 0
    assert result["stopped"] is not None
    assert len(calls) == 1
    assert ft.load_flow(flow_dir)["gate"]["status"] == before_gate


def test_compound_containing_adapt_is_rejected(tmp_path):
    flow_dir = _posted_thread(tmp_path, gate_id="G3", step_index=2)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "proceed+adapt: tighten it")]),
        now="2026-07-18T09:20:00Z")

    assert result["decided"] == 0
    assert result["stopped"] is not None
    assert ft.load_flow(flow_dir)["gate"]["status"] == "awaiting-owner"
    assert len(calls) == 1


def test_single_adapt_with_a_plus_in_its_note_still_decides(tmp_path):
    flow_dir = _posted_thread(tmp_path, gate_id="G3", step_index=2)
    send_message, calls = _stub_send()
    result = host.process_open_gate(
        flow_dir, ft.load_flow(flow_dir), send_message,
        _stub_read([_reply(TS_AFTER_OPEN, "adapt: rename a+b")]),
        now="2026-07-18T09:20:00Z")

    assert result["decided"] == 1
    assert result["stopped"] is None
    decision = ft.load_flow(flow_dir)["gate"]["decision"]
    assert decision["verdict"] == "adapt"
    assert decision["note"] == "rename a+b"
    assert calls == []


def test_single_verb_reply_is_unchanged_by_the_compound_path(tmp_path):
    cases = (("G2", "approve"), ("G3", "proceed"), ("G4", "ship"))
    for gate_id, verb in cases:
        flow_dir = _posted_thread(tmp_path / gate_id, gate_id=gate_id,
                                  step_index=2)
        send_message, calls = _stub_send()
        result = host.process_open_gate(
            flow_dir, ft.load_flow(flow_dir), send_message,
            _stub_read([_reply(TS_AFTER_OPEN, verb)]),
            now="2026-07-18T09:20:00Z")

        assert result["decided"] == 1, gate_id
        assert result["stopped"] is None, gate_id
        assert calls == [], gate_id
        assert ft.load_flow(flow_dir)["gate"]["decision"]["verdict"] == verb
