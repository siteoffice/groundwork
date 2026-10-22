"""Pure Slack channel adapter for /flow gates + ZND OS alerts (phase 1).

Maps Slack thread replies onto the existing sweep_core.plan_decisions message
contract so gate decisions can be answered from Slack through the SAME four-part
rule as email (token, thread, after-open, not-processed). Also composes the gate
prompt and the ZND OS alert digest/delta.

STDLIB ONLY. This module makes NO network calls and imports no HTTP client
library of any kind; every connector call lives in the slack_sweep_host. All
functions here are pure: fixtures in, strings/dicts out, no I/O.

Reuses outbox.gate_name / outbox.reply_verbs_line / outbox.dashboard_link so the
Slack compose path never drifts from the email compose path.

Python 3.8+.
"""
from __future__ import annotations

import datetime as _dt
from typing import List, Tuple

import outbox

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _received_at(ts: str) -> str:
    """Slack ts (epoch seconds string) -> ISO 8601 Z, string-comparable.

    utcfromtimestamp keeps the existing lexical after-open compare at
    sweep_core.py:218 working unchanged (spec scope item 1).
    """
    return _dt.datetime.utcfromtimestamp(float(ts)).strftime(_TS_FMT)


def normalize_thread(parent_ts: str, channel_id: str, replies: list,
                     ctx: dict) -> Tuple[List[dict], dict]:
    """Turn Slack thread replies into plan_decisions messages + a matched ctx.

    Excludes the parent (a reply is any thread message whose ts != parent_ts).
    Also collapses duplicate ts values to a single message: plan_decisions has
    no in-batch dedup (it only tracks processed_ids across sweep runs), so a
    Slack API result that lists the same reply twice must be deduped here or
    it would double-decide the gate.

    The gate token IS injected here (changed 2026-08-01; previously never
    injected, requiring Wilco to paste the long token into every reply). Each
    gate opens its own dedicated Slack thread (verified: distinct
    slack_parent_ts per gate in outbox.ndjson), so a message already being in
    THIS thread unambiguously identifies the gate; requiring the token too was
    pure typing friction, not an extra safety check (the thread-match and
    after-open checks in sweep_core still apply independently). The token is
    appended after the reply text so it never interferes with parse_verb's
    first-word check; a reply that also includes the token by hand (harmless
    duplicate substring) still works exactly as before.

    Returns (messages, ctx2) where ctx2 carries sent_conversation_ids =
    [parent_ts] so sweep_core._is_threaded matches via its conversation branch
    (no core edit).
    """
    token = ((ctx.get("flow") or {}).get("gate") or {}).get("token", "")
    messages: List[dict] = []
    seen_ts = set()
    for r in replies:
        ts = r.get("ts")
        if ts is None or ts == parent_ts or ts in seen_ts:
            continue
        seen_ts.add(ts)
        text = r.get("text", "") or ""
        body = f"{text} {token}".strip() if token else text
        messages.append({
            "message_id": ts,
            "received_at": _received_at(ts),
            "body": body,
            "conversation_id": r.get("thread_ts"),
            "channel_id": channel_id,
            "in_reply_to": None,
            "subject": "",
        })
    ctx2 = dict(ctx)
    ctx2["sent_conversation_ids"] = [parent_ts]
    return messages, ctx2


def compose_gate_prompt(gate: dict, flow: dict) -> str:
    """Slack-markdown gate prompt: context first, then the exact reply verbs.

    Reuses outbox.gate_name / reply_verbs_line / dashboard_link so the Slack
    prompt never drifts from the email compose path (spec scope item 1).

    Context is deliberately fuller than a one-liner (project/slug, gate name,
    stage, and the flow's idea) so Wilco does not have to scroll back to an
    earlier message to remember what he is deciding on (fixed 2026-08-01).
    The token is still shown, for reference and for the email fallback path,
    but replying no longer requires quoting it: normalize_thread injects it
    automatically for any reply in this thread (fixed 2026-08-01, see its
    docstring), so Wilco can just reply with the bare verb.
    """
    gid = gate["id"]
    name = outbox.gate_name(gid)
    token = gate["token"]
    verbs = outbox.reply_verbs_line(gid)
    link = outbox.dashboard_link(flow)
    idea = flow.get("idea", "")
    context = (
        f"*{flow['project']}/{flow['slug']}* reached the *{name}* gate ({gid}), "
        f"stage {flow.get('stage')}.\n"
        f"Idea: {idea}"
    )
    return (
        f"{context}\n\n"
        f"Reply in this thread with just: *{verbs}*\n"
        f"(no need to include the token, replying in this thread is enough)\n\n"
        f"Gate token: `{token}` (reference only)\n"
        f"Dashboard: {link}"
    )


def compose_reminder(gate: dict, flow: dict) -> str:
    """Slack-markdown escalation reminder: same context + verbs as the
    original prompt, condensed to stay a short nudge.

    Added 2026-08-01: the previous reminder was just the token and a
    dashboard link, so a reminder hours later gave Wilco no way to recall
    which gate or decision it was about without scrolling back to the
    original prompt. This repeats the same context (project/slug, gate name,
    idea) and the exact reply verbs every time.
    """
    gid = gate["id"]
    name = outbox.gate_name(gid)
    token = gate["token"]
    verbs = outbox.reply_verbs_line(gid)
    link = outbox.dashboard_link(flow)
    idea = flow.get("idea", "")
    return (
        f"Still waiting on your decision: *{flow['project']}/{flow['slug']}*, "
        f"the *{name}* gate ({gid}). Idea: {idea}\n"
        f"Reply in this thread with: *{verbs}*\n"
        f"Gate token: `{token}` (reference only)\n"
        f"Dashboard: {link}"
    )


def compose_courtesy_reply(courtesy: dict) -> str:
    """Slack-markdown courtesy reply for an invalid-verb-for-gate reply.

    `courtesy` is one entry of sweep_core.plan_decisions()["courtesy_replies"]:
    {"message_id", "project", "slug", "gate_id", "valid_verbs", "reason"}.
    Posted as a thread reply so a mistyped verb (e.g. "approve" against a
    proceed/adapt/close/park gate) gets an immediate, visible nudge instead of
    silence until the next escalation reminder (the email path already does
    this; the Slack path did not until 2026-08-01).
    """
    return (
        f"That verb isn't valid for gate {courtesy['gate_id']}. "
        f"Reply in this thread with: {courtesy['valid_verbs']}"
    )


def compose_alert_digest(open_gates: List[str], week_plan: List[str],
                         generated_at: str) -> str:
    """Once-daily 'where you stand' digest: open gates + this-week plan.

    open_gates and week_plan are pre-extracted human lines (the notifier glue
    builds them; spec 'Notifier redesign'). Empty open_gates renders an explicit
    'nothing waiting on you' line so the digest is unambiguous.
    """
    lines = [f"*ZND OS daily digest* ({generated_at})", "", "*Waiting on you*"]
    if open_gates:
        lines += ["- " + g for g in open_gates]
    else:
        lines.append("- nothing waiting on you")
    lines += ["", "*This week*"]
    if week_plan:
        lines += ["- " + w for w in week_plan]
    else:
        lines.append("- (no items)")
    return "\n".join(lines)


def compose_alert_delta(new_alerts: List[str], generated_at: str) -> str:
    """Intra-day delta: only the newly-added alert lines.

    Caller guarantees new_alerts is non-empty (enforced in notify.py, not here).
    """
    header = f"*ZND OS new alerts* ({generated_at})"
    body = "\n".join("- " + a for a in new_alerts)
    return header + "\n" + body
