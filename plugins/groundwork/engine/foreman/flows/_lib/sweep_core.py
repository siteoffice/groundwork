"""Reply sweep + escalation ladder core for the /flow command (flow-system slice e).

Two pure decision functions over fixtures/loaded dicts, no I/O, no connector call:

  - plan_sends(outboxes, now, window): which escalation emails to send vs void
    this tick, layering the send-window predicate on top of the already-tested
    outbox.classify send/void/wait decision.
  - plan_decisions(messages, flows): turn fetched mailbox replies into /flow gate
    decisions, enforcing the four-part anti-confusion validity rule (token,
    thread, after-open, not-processed) and per-gate verbs, and reporting invalid
    replies with a reason.

This module NEVER reads a mailbox and NEVER sends email. The live
outlook_email_search / outlook_send_mail wiring and the recurring host are an
explicit OPEN dependency, out of scope (see
(design spec, not shipped)
lines 24-27, 61-78). Decisions produced here are applied by a host that calls
flow_transition.cmd_gate; this module never reimplements or executes them.

Stdlib only. Python 3.8+ (zoneinfo is used only inside in_default_send_window).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import flow_state as fs
import outbox

# --- reply-ignored reasons (module constants so tests assert on them) -------
WRONG_TOKEN = "wrong-token"
NOT_THREADED = "not-threaded-and-wrong-subject"
BEFORE_OPEN = "before-open"
ALREADY_PROCESSED = "already-processed"
ALREADY_DECIDED = "already-decided"
INVALID_VERB_FOR_GATE = "invalid-verb-for-gate"
NO_RECOGNIZED_VERB = "no-recognized-verb"

# Every verb the parser recognizes at body start (union across all gates).
# ship / hold are G4's, added by slice c (spec 2026-08-17 item 9): the terminal
# sweep now stops at the ship gate, so a reply to that notification has to parse.
RECOGNIZED_VERBS = ("approve", "reject", "proceed", "adapt", "close", "park",
                    "ship", "hold")

# Read-only verbs. These answer a question and NEVER decide a gate, so they are
# deliberately a separate tuple: adding them to RECOGNIZED_VERBS would make
# plan_decisions build a decision out of "status" and hand it to cmd_gate.
READ_VERBS = ("status", "queue", "next")

_ADAPT_RE = re.compile(r"^adapt\b[:\s]*(.*)$", re.IGNORECASE | re.MULTILINE)
_FIRST_WORD_RE = re.compile(r"^\s*([a-zA-Z]+)")

# Slack (and generic Markdown) wrapper characters a person might put around the
# verb itself, e.g. replying with `proceed` in backticks to match the styling
# of the quoted token. These carry no meaning for verb recognition and must be
# stripped from the body's leading edge before the first-word check, otherwise
# a perfectly valid verb is missed entirely (silently, as NO_RECOGNIZED_VERB).
_LEADING_MARKDOWN_RE = re.compile(r"^[\s`*_~]+")


def _strip_leading_markdown(body: str) -> str:
    """Strip leading whitespace and Markdown emphasis/code wrapper chars.

    Only the leading edge is touched (e.g. "`proceed` token `...`" ->
    "proceed` token `..."); this is enough for the first-word verb check,
    which never looks past the first recognized word anyway.
    """
    return _LEADING_MARKDOWN_RE.sub("", body)


def parse_verb(body: str) -> Tuple[Optional[str], str]:
    """First recognized verb at the body start, case-insensitive (spec lines 50-52).

    Returns (verb, note). `verb` is None when the body does not start with a
    recognized verb. Leading Markdown wrapper characters (backticks, `*`, `_`,
    `~`) around the verb are ignored, so a reply like "`proceed` token `...`"
    still parses as `proceed` rather than silently failing to match. For
    `adapt`, `note` is the remaining text after `adapt` (an optional ':' and
    whitespace are stripped); for all other verbs `note` is "".
    """
    if not body:
        return None, ""
    stripped = _strip_leading_markdown(body)
    m = _FIRST_WORD_RE.match(stripped)
    if not m:
        return None, ""
    word = m.group(1).lower()
    if word not in RECOGNIZED_VERBS:
        return None, ""
    if word == "adapt":
        # Drop the matched verb, then any immediate closing Markdown wrapper
        # char left over from stripping the opening one (e.g. the closing
        # backtick in "`adapt`: ..."), before extracting the note.
        rest = re.sub(r"^[`*_~]+", "", stripped[m.end():])
        am = _ADAPT_RE.match("adapt" + rest)
        note = am.group(1).strip() if am else ""
        return "adapt", note
    return word, ""


def is_read_verb(body: str) -> Optional[str]:
    """The READ_VERBS verb this body starts with, lowercased, else None.

    Reuses parse_verb's Markdown stripping and first-word regex so a reply of
    "`status`" matches exactly like a bare "status" does. Pure.
    """
    if not body:
        return None
    m = _FIRST_WORD_RE.match(_strip_leading_markdown(body))
    if not m:
        return None
    word = m.group(1).lower()
    return word if word in READ_VERBS else None


def parse_verbs(body: str) -> List[Tuple[str, str]]:
    """Every verb in a compound reply, in order, or [] if the reply is invalid.

    Splits on '+' (surrounding whitespace tolerated: "ship + proceed" and
    "ship+proceed" both parse) and validates each element through parse_verb,
    so recognition never drifts from the single-verb path.

    All-or-nothing: one unrecognized element makes the WHOLE reply invalid and
    returns [], never a partial list. Filtering the bad element out instead
    would silently apply half of what was typed.

    `adapt` is valid only as the sole verb. A note meaningfully attached to one
    gate of a multi-gate compound is ambiguous, so any compound containing
    `adapt` returns [] (spec scope item 1). A single "adapt: <note>" returns
    exactly what parse_verb returns for it, note included.

    Pure: no I/O, no clock.
    """
    if not body or not body.strip():
        return []
    elements = [part for part in body.split("+")]
    if len(elements) == 1:
        verb, note = parse_verb(body)
        return [] if verb is None else [(verb, note)]
    parsed: List[Tuple[str, str]] = []
    for element in elements:
        verb, note = parse_verb(element.strip())
        if verb is None or verb == "adapt":
            return []
        parsed.append((verb, note))
    return parsed


# --- plan_sends (escalation ladder: send vs void vs skip) ------------------

def plan_sends(outboxes, now: str, window) -> dict:
    """Decide which escalation emails to send and which to void this tick.

    outboxes: iterable of (flow, entries) pairs, where `flow` is a loaded flow
      dict (with its current gate) and `entries` is that flow's list of outbox
      records (outbox.read_outbox shape).
    now: current time, ISO 8601 Z.
    window: a predicate window(now) -> bool that is True when escalation emails
      may be sent (production passes in_default_send_window; spec lines 54-59).

    Returns {"send": [...], "void": [...]} of outbox record dicts. An entry is:
      - void  if outbox.classify says the gate was decided/consumed meanwhile
              (not sent), regardless of the send-window;
      - send  if it is due (now >= send_after), still awaiting, AND now is inside
              the window;
      - skipped (in neither list) if not due, out of window, or already terminal.

    Pure: reuses the already-tested outbox.classify; sends nothing.
    """
    in_window = bool(window(now))
    send: List[dict] = []
    void: List[dict] = []
    for flow, entries in outboxes:
        for entry in entries:
            status = outbox.classify(entry, flow, now)
            if status == "void":
                void.append(entry)
            elif status == "send" and in_window:
                send.append(entry)
            # "wait", out-of-window "send", and terminal states: skip this tick.
    return {"send": send, "void": void}


def in_default_send_window(now_iso: str, start_hour: int = 8,
                           end_hour: int = 22,
                           tz_name: str = "Europe/Amsterdam") -> bool:
    """Production send-window predicate: True inside [start_hour, end_hour) local.

    Converts the UTC `now_iso` stamp to `tz_name` (Europe/Amsterdam handles the
    CET/CEST DST switch) and returns whether the local hour is in the window
    (spec lines 54-59). Used ONLY by the host; the two core decision functions
    never call it, so the core stays 3.8-safe and clock-free.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    t = _dt.datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)
    local = t.astimezone(ZoneInfo(tz_name))
    return start_hour <= local.hour < end_hour


# --- plan_decisions (reply ingestion: four-part validity + verbs) ----------

def _expected_subject(flow: dict) -> str:
    """The exact fresh-mail subject for this flow's gate (spec line 42).

    [FLOW <project>/<slug>] gate: <name>, name via outbox.gate_name so it never
    drifts from the compose path.
    """
    gid = flow["gate"]["id"]
    return "[FLOW {}/{}] gate: {}".format(
        flow["project"], flow["slug"], outbox.gate_name(gid))


def _match_flow(message: dict, flows) -> Optional[dict]:
    """The flow context whose open/decided gate token appears in the body.

    Token match (spec line 40): a message belongs to a flow when that flow's
    gate.token is a verbatim substring of the message body. Returns the ctx, or
    None if no flow's gate token is present. Tokens are globally unique per
    gate-open, so the first match is unambiguous.
    """
    body = message.get("body", "") or ""
    for ctx in flows:
        gate = (ctx.get("flow") or {}).get("gate")
        if isinstance(gate, dict) and gate.get("token") and gate["token"] in body:
            return ctx
    return None


def _is_threaded(message: dict, ctx: dict) -> bool:
    """Thread match (spec lines 41-42): reply-into-thread OR exact fresh subject."""
    in_reply_to = message.get("in_reply_to")
    conv = message.get("conversation_id")
    if in_reply_to and in_reply_to in ctx.get("sent_message_ids", []):
        return True
    if conv and conv in ctx.get("sent_conversation_ids", []):
        return True
    if message.get("subject", "") == _expected_subject(ctx["flow"]):
        return True
    return False


def plan_decisions(messages, flows, surface: str = "email") -> dict:
    """Turn fetched mailbox replies into /flow gate decisions (spec lines 36-52).

    messages: normalized mailbox messages (see plan "Data contracts").
    flows: one context per flow with a non-null gate (awaiting OR decided), each
      carrying the flow dict, the gate's sent-notification Message-IDs
      (sent_message_ids / sent_conversation_ids), and the per-flow processed_ids.
    surface: stamps the decision channel (default "email"; the Slack host
      passes "slack").

    Returns {"decisions": [...], "ignored": [...], "courtesy_replies": [...],
    "mark_processed": [...]}. A reply is accepted into a decision only if all
    four hold: token match, thread match, message time after gate.opened_at, and
    Message-ID not already processed. A reply against an already-decided gate is
    reported ALREADY_DECIDED (first-decision-wins). A recognized verb that is not
    valid for the gate is reported INVALID_VERB_FOR_GATE plus a courtesy reply
    listing the valid verbs. Pure: applies nothing; the host calls cmd_gate.
    """
    decisions: List[dict] = []
    ignored: List[dict] = []
    courtesy: List[dict] = []
    mark_processed: List[str] = []

    for message in messages:
        mid = message.get("message_id")

        ctx = _match_flow(message, flows)
        if ctx is None:
            # Not ours: no open/decided gate token present. Do NOT ledger.
            ignored.append({"message_id": mid, "reason": WRONG_TOKEN,
                            "token": None})
            continue

        flow = ctx["flow"]
        gate = flow["gate"]
        token = gate["token"]

        def _ignore(reason: str, ledger: bool = True):
            ignored.append({"message_id": mid, "reason": reason, "token": token})
            if ledger and mid not in mark_processed:
                mark_processed.append(mid)

        # First-decision-wins: a matched-but-decided gate is closed to replies.
        if gate.get("status") == "decided":
            _ignore(ALREADY_DECIDED)
            continue
        # Not-processed check (report before touching state; do not re-ledger).
        if mid in ctx.get("processed_ids", []):
            ignored.append({"message_id": mid, "reason": ALREADY_PROCESSED,
                            "token": token})
            continue
        # Thread match.
        if not _is_threaded(message, ctx):
            _ignore(NOT_THREADED)
            continue
        # After-open (string compare is chronological for ISO Z stamps).
        if not (message.get("received_at", "") > gate.get("opened_at", "")):
            _ignore(BEFORE_OPEN)
            continue

        # Four checks passed: parse and validate the verb.
        verb, note = parse_verb(message.get("body", ""))
        if verb is None:
            _ignore(NO_RECOGNIZED_VERB)
            continue
        if verb not in fs.valid_verdicts(gate["id"]):
            courtesy.append({
                "message_id": mid, "project": flow["project"],
                "slug": flow["slug"], "gate_id": gate["id"],
                "valid_verbs": outbox.reply_verbs_line(gate["id"]),
                "reason": INVALID_VERB_FOR_GATE,
            })
            _ignore(INVALID_VERB_FOR_GATE)
            continue

        decisions.append({
            "project": flow["project"], "slug": flow["slug"],
            "verdict": verb, "note": note, "surface": surface,
            "message_ref": mid, "token": token,
        })
        if mid not in mark_processed:
            mark_processed.append(mid)

    return {"decisions": decisions, "ignored": ignored,
            "courtesy_replies": courtesy, "mark_processed": mark_processed}
