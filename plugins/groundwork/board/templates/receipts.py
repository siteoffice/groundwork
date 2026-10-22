"""Receipts panel: decided/overruled gate-decision counts for 30 and 90 day
windows, read from Foreman's own flow gate log (`flow-log.ndjson` under the
manifest's declared `flows` root, same declaration `waiting_on_you.py`
already reads). Collector reads; render only formats what collect() found.

Item 1.5 was designed against a different, still-unbuilt source
(`_log\\runs\\git-flow.ndjson`, the `siteoffice-git` skill, sprint 2 row
1.12; that skill's own defined receipt shape also carries no verdict/
outcome field a panel could read as "overruled" once built, a second,
independent gap; see `_grill_state.md` row A8). Redefined 2026-09-13
against real, already-existing data: a gate_decided log entry is
"overruled" when an earlier entry in that same flow's own log recorded
outcome `blocked` or `fail`, i.e. a real problem was raised and the flow
still proceeded to a decision. Self-contained per flow; a flow-to-sprint
correlation was considered and rejected (no explicit link field between a
flow and its sprint, only a fragile slug-string heuristic).

Corrected 2026-09-19 (T09, R06/C05): the prior version counted every later
gate_decided as an override once ANY earlier trouble appeared anywhere in
the flow's whole log, decided by outcome alone; the R06 finding showed even
a "reject" verdict then counted as an override. An override is now only the
human decision actually going ahead (an approve/ship/proceed verdict,
extracted from the log's own `detail` field, e.g. "G1 verdict=approve")
while a trouble raised since the PREVIOUS decision is still unresolved.
Rejecting, parking, adapting or closing the work in response to a real
problem is honouring the finding, not overriding it, so it never counts. A
later passing entry (fail-then-fix) clears the open trouble before the next
decision, so that decision is not an override either.

Split by confidence (2026-09-30, sprint 2 item 1.5b flow B): each
gate_decided takes the agent_confidence of the latest gate_opened line
before it in the same flow log (file order; never across logs, no gate-id
matching). It counts as `unrated` when it has no such line, the line has no
agent_confidence key, or the value is not exactly high, medium or low
(lowercase); a reader never raises and never coerces. The bucket is only
attached after the override rule above has decided `overruled`, so the rule
itself is unchanged.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc  # noqa: E402

STATE_UNAVAILABLE = "unavailable"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

_WINDOWS = (30, 90)
# Verdicts that mean "went ahead despite the finding": G1/G2 approve, G4
# ship, G3 proceed. reject/park/adapt/close/hold all decline or defer the
# risky path instead, so a decision using one of them is never an override.
_OVERRIDE_VERDICTS = {"approve", "ship", "proceed"}
_VERDICT_RE = re.compile(r"verdict=(\S+)")
_CONFIDENCE_LEVELS = ("high", "medium", "low")
# Fixed order: also the row order the panel renders.
_CONFIDENCE_BUCKETS = _CONFIDENCE_LEVELS + ("unrated",)


def _flows_root(manifest: dict):
    """Same declaration waiting_on_you.py reads (manifest["flows"]);
    absent means no gates to count, silently, not unavailable."""
    declared = manifest.get("flows")
    if not declared:
        return None
    p = pathlib.Path(declared)
    if not p.is_absolute():
        p = (manifest["_dir"] / declared).resolve()
    return p


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_verdict(detail: str):
    m = _VERDICT_RE.search(detail or "")
    return m.group(1) if m else None


def _confidence_bucket(value):
    """The bucket for an agent_confidence value: exactly high, medium or low
    (lowercase str) or else unrated. Never raises, never coerces."""
    if isinstance(value, str) and value in _CONFIDENCE_LEVELS:
        return value
    return "unrated"


def _flow_gate_decisions(flow_log_path: pathlib.Path):
    """[(ts, overruled, bucket)] for every gate_decided entry in one flow's
    log, in file order (append-only, already chronological). overruled is
    True only when a trouble (outcome blocked/fail) raised SINCE the previous
    decision is still unresolved AND the verdict itself went ahead
    (_OVERRIDE_VERDICTS); a later passing entry clears the trouble
    (fail-then-fix), and the trouble window resets after every decision so
    an old, already-decided problem is never charged to a later, unrelated
    one. bucket is the confidence bucket of the latest gate_opened line
    before the decision in this same log ("unrated" when there is none);
    it is read separately and never changes overruled."""
    try:
        lines = flow_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    decisions = []
    trouble_open = False
    bucket = "unrated"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        action = entry.get("action")
        outcome = entry.get("outcome")
        if action == "gate_opened":
            # A plain `if`, not part of the chain below, so the override
            # rule still sees this line's outcome exactly as before.
            bucket = _confidence_bucket(entry.get("agent_confidence"))
        if action == "gate_decided":
            ts = _parse_ts(entry.get("ts", ""))
            verdict = _extract_verdict(entry.get("detail", ""))
            overruled = trouble_open and verdict in _OVERRIDE_VERDICTS
            if ts is not None:
                decisions.append((ts, overruled, bucket))
            trouble_open = False  # each decision starts a fresh round
        elif outcome in ("blocked", "fail"):
            trouble_open = True
        elif outcome == "pass":
            trouble_open = False  # fail-then-fix: a later pass clears it
    return decisions


def _window_counts(flows_root: pathlib.Path, now: datetime, window_days: int):
    """{"decided", "overruled", "by_confidence"} for one window. The four
    by_confidence buckets always sum to the two totals."""
    cutoff = now - timedelta(days=window_days)
    counts = {
        "decided": 0,
        "overruled": 0,
        "by_confidence": {
            bucket: {"decided": 0, "overruled": 0} for bucket in _CONFIDENCE_BUCKETS
        },
    }
    for flow_log_path in sorted(flows_root.glob("*/*/flow-log.ndjson")):
        for ts, was_overruled, bucket in _flow_gate_decisions(flow_log_path):
            if ts >= cutoff:
                split = counts["by_confidence"][bucket]
                counts["decided"] += 1
                split["decided"] += 1
                if was_overruled:
                    counts["overruled"] += 1
                    split["overruled"] += 1
    return counts


def collect(manifest: dict):
    root = _flows_root(manifest)
    if root is None:
        return STATE_EMPTY, {}
    if not root.is_dir():
        return STATE_UNAVAILABLE, {}
    now = datetime.now(timezone.utc)
    counts = {f"window_{days}": _window_counts(root, now, days) for days in _WINDOWS}
    if all(counts[f"window_{days}"]["decided"] == 0 for days in _WINDOWS):
        return STATE_EMPTY, counts
    return STATE_NORMAL, counts


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("receipts", "receipts")

    if state == STATE_UNAVAILABLE:
        body = (
            f'<p class="empty warn"><span class="msg" data-i18n="state_unavailable">'
            f'{esc(labels.get("state_unavailable", "state_unavailable"))}</span></p>'
        )
    elif state == STATE_EMPTY:
        body = (
            f'<p class="empty"><span class="msg" data-i18n="state_empty">'
            f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
        )
    else:
        decided_word = esc(labels.get("decided", "decided"))
        overruled_word = esc(labels.get("overruled", "overruled"))
        confidence_words = {
            bucket: esc(labels.get(f"confidence_{bucket}", f"confidence_{bucket}"))
            for bucket in _CONFIDENCE_BUCKETS
        }

        def _count_text(counts):
            return (
                f'<strong>{counts["decided"]}</strong> {decided_word}, '
                f'<strong>{counts["overruled"]}</strong> {overruled_word}'
            )

        items = ""
        for days in _WINDOWS:
            window = data[f"window_{days}"]
            window_label = esc(
                labels.get(f"last_{days}_days", "Last {n} days").replace("{n}", str(days))
            )
            rows = "".join(
                f'<li>{confidence_words[bucket]}: {_count_text(window["by_confidence"][bucket])}</li>'
                for bucket in _CONFIDENCE_BUCKETS
            )
            items += f'<li>{window_label}: {_count_text(window)}<ul class="rows">{rows}</ul></li>'
        # Fixed height, scrolling body (the same wrapper activity, roadmap and
        # hours use): the panel now carries ten rows and must not grow the grid.
        body = f'<div class="panel-scroll"><ul class="rows">{items}</ul></div>'

    return (
        f'<section class="panel" data-panel="receipts">'
        f'<div class="head"><h2 data-i18n="receipts">{esc(title)}</h2></div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
