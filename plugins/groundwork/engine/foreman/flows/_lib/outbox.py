"""Notification outbox for the /flow command (flow-system slice d).

Per-flow `outbox.ndjson` in C:\\work\\10_holding\\flows\\<project>\\<slug>\\
(flow-system-design decision #11). One JSON object per line. This module owns:

  - `compose(gate, flow)`: pure. Renders the Outlook email subject and body from
    the loaded gate/flow dicts. No I/O, no network, no send (spec line 61).
  - `append_gate_open(flow_dir, flow, now)`: append a `pending` entry when a gate
    opens, with `send_after = gate.opened_at + 15 min` (spec lines 42-44). Rerun
    safe: a token already in the outbox is not appended twice (spec lines 49-50).
  - `classify` / `mark_sent` / `mark_voided`: the pure send-vs-void-vs-wait
    decision plus the terminal-state writers. A gate decided in-session is
    `voided`, never sent (spec lines 46-48).

This module MIRRORS the state-to-compose-to-dispatch shape of
C:\\work\\10_holding\\04_engineering\\command_center\\notify.py but dispatch
is OUT OF SCOPE here: the live `outlook_send_mail` call and the scheduled sweep
belong to slice e. This module imports NO network library and sends NO email.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
from typing import List, Optional, Tuple

import flow_state as fs
import roots

# Per flow-system-design decision #11 (lines 104-109): flow state lives under
# <hub_root>\flows\<project>\<slug>\ . `FLOWS_ROOT` is the import-time default
# (overridable via ZND_HUB_ROOT / znd_hub.json, see roots.py); `dashboard_link`
# also takes an explicit `flows_root` override so a caller holding a
# CLI-resolved root (e.g. a --flows-root flag) can pass it straight through
# instead of relying on the module default.
FLOWS_ROOT = roots.flows_root()

# Gate display names (flow-system-design lines 28-30).
GATE_NAMES = {
    "G1": "design approval",
    "G2": "spec approval",
    "G3": "learnings review",
    "G4": "ship approval",
}

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SEND_AFTER_MINUTES = 15  # the escalation ladder (spec line 43)

OUTBOX_STATUSES = ("pending", "sent", "voided", "failed")


# --- pure message composition ----------------------------------------------

def gate_name(gate_id: str) -> str:
    """Human gate name for the subject line; unknown ids fall back to the id."""
    return GATE_NAMES.get(gate_id, gate_id)


def dashboard_link(flow: dict, flows_root: Optional[pathlib.Path] = None) -> str:
    """file:// URI of the per-flow dashboard.html (pure; no filesystem access).

    `flows_root` defaults to the module-level `FLOWS_ROOT`; pass an explicit
    value (e.g. a caller's already-resolved --flows-root) to override it.
    """
    base = pathlib.Path(flows_root) if flows_root is not None else FLOWS_ROOT
    p = base / flow["project"] / flow["slug"] / "dashboard.html"
    return "file:///" + str(p).replace("\\", "/")


def next_command(flow: dict) -> str:
    """The exact next command: resume the flow in-session (design decision #7)."""
    return f"/flow {flow['project']} {flow['slug']}"


def reply_verbs_line(gate_id: str) -> str:
    """Gate-appropriate reply verbs, e.g. 'approve | reject | park'.

    Sourced from flow_state.valid_verdicts so this never drifts from the schema.
    The G3 'adapt' verb is rendered as 'adapt: <note>' (spec line 58).
    """
    parts = []
    for verb in fs.valid_verdicts(gate_id):
        parts.append("adapt: <note>" if verb == "adapt" else verb)
    return " | ".join(parts)


def compose(gate: dict, flow: dict,
            flows_root: Optional[pathlib.Path] = None) -> Tuple[str, str]:
    """Pure: gate + flow dicts in, (subject, body) strings out.

    Subject convention: [FLOW <project>/<slug>] gate: <name> (spec line 55).
    Body carries flow ID, gate token, one-paragraph context, the file:// dashboard
    link, the exact next command, and the gate-appropriate reply verbs (spec
    lines 56-59). No I/O, no send. `flows_root` overrides the dashboard link's
    base path; see `dashboard_link`.
    """
    gid = gate["id"]
    name = gate_name(gid)
    subject = f"[FLOW {flow['project']}/{flow['slug']}] gate: {name}"

    link = dashboard_link(flow, flows_root=flows_root)
    cmd = next_command(flow)
    verbs = reply_verbs_line(gid)
    token = gate["token"]
    idea = flow.get("idea", "")

    context = (
        f"Flow {flow['flow_id']} ({flow['project']}/{flow['slug']}) has reached the "
        f"{name} gate ({gid}) in stage {flow.get('stage')}. Idea: {idea}. "
        f"It is waiting on your decision before it can proceed."
    )
    body = (
        f"{context}\n\n"
        f"Flow ID: {flow['flow_id']}\n"
        f"Gate token: {token}\n"
        f"Dashboard: {link}\n"
        f"Next command: {cmd}\n\n"
        f"Reply with: {verbs}\n"
        f"Reply in-thread, or send a fresh mail with the same subject, "
        f"quoting the token {token}.\n"
    )

    sweep = gate.get("sweep_context")
    if sweep:
        verify = sweep.get("verify_summary") or "(none)"
        test = sweep.get("test_summary") or "(none)"
        suggested = ", ".join(sweep.get("suggested_verbs", []))
        body += (
            f"\n"
            f"Sprint status: {sweep.get('status')} "
            f"({sweep.get('class')})\n"
            f"Verify: {verify}\n"
            f"Test: {test}\n"
            f"Suggested: {suggested}\n"
        )

    return subject, body


# --- send-after ladder (pure) ----------------------------------------------

def send_after_for(opened_at: str, minutes: int = _SEND_AFTER_MINUTES) -> str:
    """gate.opened_at + <minutes>, as an ISO 8601 Z stamp (spec lines 42-44)."""
    t = _dt.datetime.strptime(opened_at, _TS_FMT).replace(tzinfo=_dt.timezone.utc)
    return (t + _dt.timedelta(minutes=minutes)).strftime(_TS_FMT)


# --- outbox.ndjson I/O ------------------------------------------------------

def _outbox_path(flow_dir) -> pathlib.Path:
    return pathlib.Path(flow_dir) / "outbox.ndjson"


def read_outbox(flow_dir) -> List[dict]:
    """Read every outbox record; missing file or blank/bad lines yield []."""
    p = _outbox_path(flow_dir)
    if not p.exists():
        return []
    out: List[dict] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# The fixed tail of a gate token: -<GATE>-S<step>-<opened_at compact>-<nonce>.
# Anchored at the end and matched as a whole because a slug may itself contain
# hyphens (flow_state.slugify joins up to five hyphen-separated tokens), so
# splitting the token by position is not safe.
_STEP_TOKEN_RE = re.compile(r"-(G\d+)-S(\d+)-(\d{8}T\d{6}Z)-([A-Za-z0-9]+)$")


def token_step_index(token: str) -> Optional[int]:
    """The step index encoded in a gate token, or None.

    None for a G1 token (flow_state.gate_token omits the -S<step> part when
    step_index is None) and for anything that is not a gate token. Pure.
    """
    m = _STEP_TOKEN_RE.search(token or "")
    return int(m.group(2)) if m else None


def find_step_parent_ts(flow_dir, step_index) -> Optional[str]:
    """The earliest Slack thread parent ts recorded for this flow's step.

    One step can open several gates (a G2 spec gate, then a G4 ship gate, then
    a G3 learn gate). Without this lookup each gate opens a fresh Slack thread
    and the step's conversation is scattered across three of them; the connector
    host calls this before composing a new prompt and threads the prompt into
    the found ts instead.

    Scoped to one flow implicitly: read_outbox only ever reads this flow_dir's
    outbox.ndjson. Entries whose token encodes a different step, and entries
    never posted to Slack (slack_parent_ts still None), are skipped. Ordering is
    by `created_at` (the record's append time, the record has no `opened_at`
    field), with the file's own append order preserved on a tie by Python's
    stable sort. Returns None when nothing matches. Reads only, writes nothing.
    """
    if step_index is None:
        return None
    wanted = int(step_index)
    matches = []
    for entry in read_outbox(flow_dir):
        parent_ts = entry.get("slack_parent_ts")
        if not parent_ts:
            continue
        if token_step_index(entry.get("token", "")) != wanted:
            continue
        matches.append((entry.get("created_at") or "", parent_ts))
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0])
    return matches[0][1]


def _write_outbox(flow_dir, records: List[dict]) -> pathlib.Path:
    """Rewrite outbox.ndjson from records (used by the mark_* transitions)."""
    p = _outbox_path(flow_dir)
    with p.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def append_gate_open(flow_dir, flow: dict, now: Optional[str] = None,
                      flows_root: Optional[pathlib.Path] = None) -> Optional[dict]:
    """Append one `pending` entry for the flow's open gate; rerun safe.

    Returns the new record, or None if an entry for this gate token already
    exists (rerun: one gate, one token, one outbox entry; spec lines 49-50).
    `flows_root` overrides the dashboard link's base path; see `dashboard_link`.
    """
    now = now or fs.iso_utc_now()
    gate = flow.get("gate")
    if not isinstance(gate, dict):
        raise ValueError("append_gate_open requires an open gate on the flow")
    token = gate["token"]

    if any(e.get("token") == token for e in read_outbox(flow_dir)):
        return None

    subject, body = compose(gate, flow, flows_root=flows_root)
    record = {
        "id": f"OBX-{token}",
        "flow_id": flow["flow_id"],
        "project": flow["project"],
        "slug": flow["slug"],
        "gate_id": gate["id"],
        "token": token,
        "subject": subject,
        "body": body,
        "dashboard_link": dashboard_link(flow, flows_root=flows_root),
        "next_command": next_command(flow),
        "created_at": now,
        "send_after": send_after_for(gate["opened_at"]),
        "sent_at": None,
        "sent_message_id": None,
        "slack_channel_id": None,
        "slack_parent_ts": None,
        "status": "pending",
    }
    with _outbox_path(flow_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


# --- status decision + transitions -----------------------------------------

def classify(entry: dict, flow: dict, now: str) -> str:
    """Pure send-vs-void-vs-wait decision for a pending entry (spec lines 44-48).

    Returns:
      - the entry's own status if it is not `pending` (already terminal);
      - "void" if the gate this entry was raised for is no longer awaiting
        (decided in-session, or consumed): do not send;
      - "wait" if now is still before send_after (inside the 15-min ladder);
      - "send" otherwise (eligible; the slice-e notifier performs the actual send).

    This module never acts on "send"; the live outlook_send_mail call is out of
    scope (spec lines 27-30, 63-71).
    """
    if entry.get("status") != "pending":
        return str(entry.get("status", "unknown"))
    gate = flow.get("gate")
    still_awaiting = (
        isinstance(gate, dict)
        and gate.get("token") == entry.get("token")
        and gate.get("status") == "awaiting-owner"
    )
    if not still_awaiting:
        return "void"
    if now < entry.get("send_after", ""):
        return "wait"
    return "send"


def mark_sent(flow_dir, entry_id: str, message_id: str,
              now: Optional[str] = None) -> List[dict]:
    """Flip a pending entry to `sent` with sent_at + sent_message_id.

    Called by the slice-e notifier AFTER outlook_send_mail succeeds; marking
    `sent` is what prevents a double-mail on rerun (spec lines 49-50).
    """
    now = now or fs.iso_utc_now()
    records = read_outbox(flow_dir)
    for r in records:
        if r.get("id") == entry_id and r.get("status") == "pending":
            r["status"] = "sent"
            r["sent_at"] = now
            r["sent_message_id"] = message_id
    _write_outbox(flow_dir, records)
    return records


def mark_voided(flow_dir, entry_id: str, now: Optional[str] = None) -> List[dict]:
    """Flip a pending entry to `voided`; sent_at/sent_message_id stay null.

    Used when the gate was decided in-session before send_after: voided is not
    sent (spec lines 46-48, 109). `now` is accepted for a symmetric signature
    and future auditing; the voided record deliberately keeps sent_at null.
    """
    _ = now
    records = read_outbox(flow_dir)
    for r in records:
        if r.get("id") == entry_id and r.get("status") == "pending":
            r["status"] = "voided"
    _write_outbox(flow_dir, records)
    return records


def mark_slack_posted(flow_dir, entry_id: str, channel_id: str,
                      parent_ts: str, now: Optional[str] = None) -> List[dict]:
    """Record the Slack thread coordinates on a gate-open entry, set-once.

    Called by the connector host AFTER slack_send_message returns the parent
    ts. Writes slack_channel_id + slack_parent_ts only when slack_parent_ts is
    still None, so a re-run never overwrites the original thread (mirrors the
    one-token-one-entry idempotency; spec scope item 3). `now` is accepted for a
    symmetric signature and future auditing.
    """
    _ = now
    records = read_outbox(flow_dir)
    for r in records:
        if r.get("id") == entry_id and r.get("slack_parent_ts") is None:
            r["slack_channel_id"] = channel_id
            r["slack_parent_ts"] = parent_ts
    _write_outbox(flow_dir, records)
    return records
