"""slack_sweep_host.py - phase-1 polling connector host for /flow Slack gates.

This is the thin host behind the "znd-slack-sweep" Cowork task: it discovers
open /flow gates and due escalation reminders from the local flow tree, then
drives the Slack connector tools and the already-tested pure modules
(slack_channel, sweep_core, flow_transition, outbox) to post prompts, poll
threads, and apply decisions.

Fence (spec: 2026-07-20-slack-channel-adapter-design.md, Guardrails): this
host reads and posts on demand only, on the shared 30-minute sweep cycle. It
holds no persistent connection to Slack and subscribes to no push-based
message stream; every call is a single request/response made when this
module's main() is invoked by the scheduler. There is no long-running process
here and no listener of any kind.

Connector tools consumed (Cowork Slack connector; not importable Python
modules, so they are never referenced at module import time, only accepted as
parameters by the functions below):
  - slack_send_message(channel_id, text, thread_ts=None) -> {"ts": ..., "channel_id": ...}
    Used to post a NEW gate prompt to #znd-gates (thread_ts=None), and to post
    an escalation reminder as a reply in an existing gate thread
    (thread_ts=<slack_parent_ts>). Never used to edit or delete a message.
    ASSUMPTION TO VERIFY: this return shape (a dict with a "ts" key) is not
    part of the real Cowork slack_send_message tool's declared contract; its
    documented behavior is closer to "returns a message link". The Controller
    must verify the live tool's actual return shape during the fenced
    rehearsal and, if it differs, wrap the real call in a small shim that
    normalizes the return into {"ts": ...} before passing send_message in,
    since process_open_gate's posted["ts"] read (and mark_slack_posted) depend
    on that key existing.
  - slack_read_thread(channel_id, message_ts) -> list of reply messages
    Used to poll an already-posted gate prompt's thread for replies.
  - slack_read_channel(channel_id, ...) -> available to the caller for future
    phase-2 use; NOT called anywhere in this phase-1 module.

Channel target: GATE_CHANNEL_ID below, "#znd-gates" (spec decision 3), used
for every new gate prompt. Escalation replies always target the gate's own
thread (entry["slack_channel_id"] / entry["slack_parent_ts"]), never a fresh
top-level message.

Writes are local flow files only, via the already-tested pure writers
(outbox.append_gate_open, outbox.mark_slack_posted, flow_transition.cmd_gate).
This module never edits Slack beyond posting prompts and escalation replies,
and never re-implements persist-then-prompt or first-decision-wins: cmd_gate
already enforces both (an already-decided gate's cmd_gate call is a no-op).

NOT unit-tested: connector I/O is fenced and verified manually (Controller
rehearsal), same as the email send path. This module's only automated gates
are the import-smoke check (no connector runtime required) and a source-level
check that no push-based transport has crept in.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import pathlib
from typing import Callable, Dict, List, Optional, Tuple

import flow_state as fs
import flow_transition as ft
import foreman_status as fst
import foreman_lock
import outbox
import os
import roots
import slack_channel
import sweep_core

DEFAULT_FLOWS_ROOT = str(roots.flows_root())

# Phase-1 target channel for new gate prompts (spec decision 3: split
# #znd-gates for actionable items from #znd-alerts for informational ones).
# Unset by default; the operator sets ZND_GATE_CHANNEL_ID to their own gate
# channel. slack_sweep_runner.py reads the same env var and overrides this at
# runtime.
# Default is the ZND deployment's #znd-gates channel id (C0BK70BTFQ9, live
# since 2026-07-22). The empty-string default before 2026-08-18 meant that any
# environment without ZND_GATE_CHANNEL_ID set posted to channel "" and every
# gate prompt failed with channel_not_found, silently, for as long as the env
# var was missing (found via the step 6 G3 that never reached Slack). The env
# var still wins for other deployments of the public harness.
GATE_CHANNEL_ID = os.environ.get("ZND_GATE_CHANNEL_ID", "C0BK70BTFQ9")


# =============================================================================
# Flow discovery (mirrors terminal_sweep.discover_flows; no log-reading here,
# this host does not need flow-log.ndjson, only flow.json + outbox.ndjson).
# =============================================================================

def discover_flows(flows_root) -> list:
    """Walk flows_root/<project>/<slug>/flow.json. Malformed entries skipped.

    Returns [{"flow": <dict>, "flow_dir": <Path>}, ...]. A flow.json that
    fails to parse is silently skipped (not fatal): this host isolates
    per-flow errors in sweep_once, but a flow that never loads can never be
    matched in the first place.
    """
    import json

    root = pathlib.Path(flows_root)
    flows = []
    if not root.exists():
        return flows
    for fj in root.glob("*/*/flow.json"):
        flow_dir = fj.parent
        try:
            flow = json.loads(fj.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        flows.append({"flow": flow, "flow_dir": flow_dir})
    return flows


def _find_entry(flow_dir, token: str) -> Optional[dict]:
    """The outbox record for this gate's token, or None if not yet appended."""
    for entry in outbox.read_outbox(flow_dir):
        if entry.get("token") == token:
            return entry
    return None


# =============================================================================
# Open-gate handling: post a new prompt, or poll an existing thread and apply
# decisions. Exactly the two connector calls named in the module docstring.
# =============================================================================

def render_read_verb(verb: str, flows_root, now: str) -> str:
    """Render one READ_VERB against the live board. Read-only, no gate work.

    status -> the seven-line banner for the focus flow
    queue  -> the manager table of every non-idle flow
    next   -> the single highest-priority action

    `flows_root` is passed in rather than read from roots so a test's tmp tree
    is rendered, never the operator's real one. Returns "" for anything that is
    not a read verb, which the caller treats as nothing to post.
    """
    statuses = fst.collect(flows_root, roots.sprints_root(), now)
    if verb == "status":
        return fst.render_banner(statuses, now, str(roots.foreman_board_path()),
                                 flows_root=str(flows_root))
    if verb == "queue":
        return fst.render_queue(statuses, now)
    if verb == "next":
        return fst.render_next(statuses)
    return ""


def _answer_read_verb(flow_dir, messages: List[dict], channel_id: str,
                      parent_ts: str, send_message: Callable, now: str) -> int:
    """Answer the newest READ_VERB reply in this thread. Returns 0 or 1.

    Writes NOTHING: no flow.json, no flow-log.ndjson, no outbox entry, no gate
    lookup. Read verbs are not in sweep_core.RECOGNIZED_VERBS, so plan_decisions
    already discards them; this only turns that silence into an answer.

    At most one per sweep, deliberately. This host does not persist
    processed_ids (see the comment in process_open_gate), so the same reply is
    re-seen on every 30-minute sweep for as long as the gate stays open.
    Answering only the newest one bounds that to one message per tick, matching
    what the courtesy-reply path in the same function already does. Durable
    per-reply dedupe needs persisted processed_ids and is out of scope here.
    """
    candidates = [(m.get("received_at", ""), m) for m in messages
                  if sweep_core.is_read_verb(m.get("body", ""))]
    if not candidates:
        return 0
    candidates.sort(key=lambda pair: pair[0])
    newest = candidates[-1][1]
    verb = sweep_core.is_read_verb(newest.get("body", ""))
    flows_root = pathlib.Path(flow_dir).parent.parent
    text = render_read_verb(verb, flows_root, now)
    if not text.strip():
        return 0
    send_message(channel_id, text, thread_ts=parent_ts)
    return 1


def _apply_decision(flow_dir, decision: dict, body: str,
                    now: str) -> Tuple[int, Optional[str]]:
    """Apply one reply's verb or compound of verbs. Returns (applied, stopped).

    `stopped` is None when everything applied, otherwise the operator-facing
    sentence naming the verb that could not be applied and why. Verbs applied
    before a stop STAY applied: cmd_gate calls are individually durable and
    idempotent, which is the existing single-verb contract, and rolling one
    back would need a decision-reversal path that does not exist.

    Ordering matters: the gate is re-read from disk before EVERY verb, because
    the previous cmd_gate wrote flow.json. Note that cmd_gate only records a
    decision; it does NOT consume the gate or open the next one (advance does,
    and this host never advances). A compound whose second verb needs the
    driver to run first therefore stops with an explanation, by design.
    """
    verbs = sweep_core.parse_verbs(body)
    if not verbs:
        # A single adapt whose note happens to contain '+' is not a compound.
        # parse_verbs rejects it (adapt is invalid in a compound, spec scope
        # item 1) but the reply is a perfectly valid single adapt, so fall back
        # to the single-verb parse rather than regressing it into a rejection.
        single, note = sweep_core.parse_verb(body)
        if single == "adapt":
            # normalize_thread appends the gate token to the body on the same
            # line (not a newline) so plan_decisions' token match still works;
            # adapt's note capture then swallows that trailing token too. Trim
            # it back off here, the only place that both knows the token
            # (decision["token"]) and owns the note that reaches cmd_gate.
            token = decision.get("token", "")
            if token and note.endswith(token):
                note = note[:-len(token)].rstrip()
            verbs = [(single, note)]
        else:
            return 0, ("That compound reply was not applied: every verb in it "
                       "has to be a valid verb on its own, and 'adapt' cannot "
                       "be combined with another verb. Nothing was decided. "
                       "Reply again with one verb, or with valid verbs joined "
                       "by '+'.")

    applied = 0
    for verb, note in verbs:
        current = ft.load_flow(flow_dir)
        gate = current.get("gate")
        if not isinstance(gate, dict) or gate.get("status") != "awaiting-owner":
            return applied, (
                "Stopped at `{}`: there is no open gate for it right now. "
                "The {} verb(s) before it were applied and stay applied. "
                "Reply `{}` again once the next gate is open.".format(
                    verb, applied, verb))
        if verb not in fs.valid_verdicts(gate.get("id")):
            return applied, (
                "Stopped at `{}`: gate {} accepts {}. The {} verb(s) before it "
                "were applied and stay applied.".format(
                    verb, gate.get("id"), outbox.reply_verbs_line(
                        gate.get("id")), applied))
        try:
            ft.cmd_gate(flow_dir, verb, note, surface="slack", now=now,
                        message_ref=decision["message_ref"])
        except Exception as exc:  # report, never roll back
            return applied, (
                "Stopped at `{}`: {}. The {} verb(s) before it were applied "
                "and stay applied.".format(verb, exc, applied))
        applied += 1
    return applied, None


def process_open_gate(flow_dir, flow: dict,
                      send_message: Callable, read_thread: Callable,
                      now: Optional[str] = None) -> dict:
    """Post-or-poll one flow's open gate. Returns a summary dict for the caller.

    send_message(channel_id, text, thread_ts=None) -> dict with "ts".
    read_thread(channel_id, message_ts) -> list of reply dicts (ts/text/thread_ts).
    """
    now = now or fs.iso_utc_now()
    gate = flow["gate"]
    token = gate["token"]

    entry = outbox.append_gate_open(flow_dir, flow, now)
    if entry is None:
        entry = _find_entry(flow_dir, token)
    if entry is None:
        return {"action": "error", "reason": "no outbox entry for open gate"}

    if not entry.get("slack_parent_ts"):
        text = slack_channel.compose_gate_prompt(gate, flow)
        # One thread per STEP, not per gate: a step's G2, G4 and G3 prompts all
        # belong to the same conversation. find_step_parent_ts returns the
        # earliest thread already opened for this step (None for a G1 gate,
        # whose step_index is None, and None for the step's first gate).
        step_parent = outbox.find_step_parent_ts(flow_dir,
                                                 gate.get("step_index"))
        if step_parent:
            send_message(GATE_CHANNEL_ID, text, thread_ts=step_parent)
            parent_ts = step_parent
        else:
            posted = send_message(GATE_CHANNEL_ID, text)
            # posted["ts"] assumes send_message returns a dict with a "ts" key.
            # That is not a documented guarantee of the real slack_send_message
            # tool (see module docstring); Controller must verify/adapt during
            # the fenced rehearsal, since mark_slack_posted below needs this key.
            parent_ts = posted["ts"]
        # The REUSED parent is what gets recorded, never the threaded reply's
        # own ts: the polling branch below reads the thread by this value, and
        # a third gate in the same step finds it here the same way.
        outbox.mark_slack_posted(flow_dir, entry["id"], GATE_CHANNEL_ID,
                                 parent_ts, now)
        return {"action": "posted", "threaded": bool(step_parent)}

    channel_id = entry["slack_channel_id"]
    parent_ts = entry["slack_parent_ts"]
    replies = read_thread(channel_id, parent_ts)
    # ctx is rebuilt fresh every sweep; plan_decisions' mark_processed (the
    # not-processed arm of the four-part rule) is still not persisted here,
    # since first-decision-wins already holds via ALREADY_DECIDED plus
    # cmd_gate's own idempotency (a re-seen reply against a decided gate is a
    # no-op, just re-ledgered as ignored on every sweep; harmless, only
    # wasted work). courtesy_replies IS consumed below (fixed 2026-08-01;
    # previously silently discarded, so an invalid Slack reply got zero
    # feedback while the email path already sent a courtesy note).
    ctx = {"flow": flow, "processed_ids": []}
    messages, ctx2 = slack_channel.normalize_thread(parent_ts, channel_id,
                                                    replies, ctx)
    answered = _answer_read_verb(flow_dir, messages, channel_id, parent_ts,
                                 send_message, now)
    plan = sweep_core.plan_decisions(messages, [ctx2], surface="slack")
    # The advisory flow lock (Foreman slice b). Two writers deciding the same
    # gate in the same window is exactly the hole the spec names. LockBusy is
    # a skip, not an error: the next 30-minute sweep re-reads the same thread,
    # and cmd_gate is already first-decision-wins, so nothing is lost.
    bodies = {m["message_id"]: m.get("body", "") for m in messages}
    applied = 0
    stopped: Optional[str] = None
    try:
        with foreman_lock.acquire(flow_dir, "slack-sweep", now=now,
                                  stage=flow.get("stage", "")):
            for d in plan["decisions"]:
                # One reply may carry several verbs ("ship+proceed"). The body
                # is re-parsed here rather than in plan_decisions so the email
                # path and plan_decisions' signature stay untouched.
                count, stopped = _apply_decision(
                    flow_dir, d, bodies.get(d["message_ref"], ""), now)
                applied += count
                if stopped:
                    break
    except foreman_lock.LockBusy:
        return {"action": "polled", "decided": 0, "courtesy": 0,
                "answered": answered, "reason": "lock_busy", "stopped": None}
    # Courtesy nudge for a recognized-but-invalid verb (e.g. "approve" against
    # a proceed/adapt/close/park gate), posted in-thread so a mistyped reply
    # is not met with silence until the next escalation reminder. Added
    # 2026-08-01: previously this branch was a documented, intentional gap
    # (see the comment this replaced) and left Wilco's mistyped verdicts
    # completely unacknowledged.
    for courtesy in plan["courtesy_replies"]:
        send_message(channel_id, slack_channel.compose_courtesy_reply(courtesy),
                     thread_ts=parent_ts)
    # Posted OUTSIDE the lock: the lock covers writes, not Slack I/O. A stopped
    # compound is never silent; the operator is told which verb did not land.
    if stopped:
        send_message(channel_id, stopped, thread_ts=parent_ts)
    return {"action": "polled", "decided": applied,
            "courtesy": len(plan["courtesy_replies"]), "answered": answered,
            "stopped": stopped}


# =============================================================================
# Escalation reminders: reuse sweep_core.plan_sends, post in-thread only.
# =============================================================================

def process_escalations(flow_dir, flow: dict, entries: List[dict],
                        send_message: Callable, now: str) -> int:
    """Post a reminder reply for every entry plan_sends says is due.

    Only entries already posted to Slack (slack_parent_ts set) can receive a
    reminder; anything else is skipped (nothing to reply into yet). After a
    reminder is posted, the entry is flipped to "sent" via outbox.mark_sent
    (one-shot per gate-open, the same one-shot contract the email path
    already has: mark_sent's own docstring says marking sent is what
    prevents a repeat send on rerun). Without this, outbox.classify would
    keep returning "send" for the same still-open, still-pending entry every
    30-minute sweep tick inside the 08:00-22:00 window, forever. Returns the
    count of reminders posted.

    The reminder text is slack_channel.compose_reminder(flow["gate"], flow),
    which repeats full context (project/slug, gate name, idea) and the exact
    reply verbs (fixed 2026-08-01; previously a bare token + dashboard link
    with no context or verbs). This assumes entry.token == flow["gate"]["token"]
    for every entry plan_sends returns as "send", which holds: plan_sends only
    ever emits "send" for an entry whose token still matches the flow's
    currently-awaiting gate (see outbox.classify's still_awaiting check).
    """
    plan = sweep_core.plan_sends([(flow, entries)], now,
                                 sweep_core.in_default_send_window)
    count = 0
    for entry in plan["send"]:
        if not entry.get("slack_parent_ts"):
            continue
        text = slack_channel.compose_reminder(flow["gate"], flow)
        posted = send_message(entry["slack_channel_id"], text,
                    thread_ts=entry["slack_parent_ts"])
        # posted.get("ts", ...) defensively: a reminder reply has no natural
        # "message-id" the way an email does, and the real connector's
        # return shape is still an unverified assumption (see module
        # docstring). This value is informational bookkeeping only, nothing
        # downstream branches on it the way it does on slack_parent_ts.
        outbox.mark_sent(flow_dir, entry["id"], posted.get("ts", ""), now)
        count += 1
    return count


# =============================================================================
# Thin host: sweep_once() / main()
# =============================================================================

def sweep_once(flows_root, send_message: Callable, read_thread: Callable,
               now: Optional[str] = None) -> dict:
    """One pass over the flow tree: open gates first, then escalations.

    Per-flow isolation: an error in one flow is recorded and does not stop the
    rest of the tree from being processed.
    """
    now = now or fs.iso_utc_now()
    posted = polled = decided = reminders = 0
    errors: List[Dict] = []

    for ctx in discover_flows(flows_root):
        flow = ctx["flow"]
        flow_dir = ctx["flow_dir"]
        try:
            gate = flow.get("gate")
            if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
                result = process_open_gate(flow_dir, flow, send_message,
                                           read_thread, now)
                if result["action"] == "posted":
                    posted += 1
                elif result["action"] == "polled":
                    polled += 1
                    decided += result["decided"]
                # process_open_gate may have called ft.cmd_gate and written a
                # newly-decided flow.json to disk this same tick. Reload from
                # disk before escalation voiding below, so plan_sends/classify
                # never sees the stale in-memory gate status (mirrors
                # terminal_sweep.apply_match's current = ft.load_flow(...) reload).
                flow = ft.load_flow(flow_dir)
            entries = outbox.read_outbox(flow_dir)
            reminders += process_escalations(flow_dir, flow, entries,
                                             send_message, now)
        except Exception as exc:  # per-flow isolation
            errors.append({"flow_id": flow.get("flow_id"), "error": str(exc)})

    return {"posted": posted, "polled": polled, "decided": decided,
            "reminders": reminders, "errors": errors}


def main(send_message: Callable, read_thread: Callable,
        flows_root=DEFAULT_FLOWS_ROOT, now: Optional[str] = None) -> dict:
    """Entrypoint for the znd-slack-sweep Cowork task.

    The caller (the Cowork task, which alone has Slack connector tool access)
    supplies send_message/read_thread bound to the actual
    slack_send_message / slack_read_thread tool calls, e.g.:
        main(slack_send_message, slack_read_thread)
    This keeps the connector out of module scope entirely, so `import
    slack_sweep_host` never touches the Cowork runtime.
    """
    return sweep_once(flows_root, send_message, read_thread, now)
