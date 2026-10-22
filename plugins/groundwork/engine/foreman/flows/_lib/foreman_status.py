"""foreman_status.py - the unified Foreman status surface (slice d).

Spec: (design spec, not shipped)

Three renderings of one model, all fed by foreman_plan.next_action: the banner,
the watch view, and the queue/next/why commands. The HTML board is the fourth
and lives in C:\\work\\10_holding\\foreman\\_lib\\render_foreman.py, which
imports this module rather than re-deriving anything.

READ-ONLY. This module never writes flow.json, sprint.json, foreman.json, a flow
log or a sprint log, never takes the flow lock (there is nothing to protect),
never launches, merges or pushes, and never calls a model. Every string it emits
is a template fill, which is why the spec routes `render_status` to model class
none.

Split, matching terminal_sweep.py: a pure section (no I/O, no clock, every input
an argument) and a thin host section below the banner comment that does all the
filesystem work and all the printing.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import dataclasses
import json
from typing import List, Optional, Tuple

import flow_state as fs
import foreman_lock as fl
import foreman_plan as fp
import roots
import terminal_sweep as ts

# --- contracts ----------------------------------------------------------------

# G4 is listed defensively for the same reason foreman_plan.SHIP_GATE_ID exists:
# slice c has not added it to flow_state.GATE_IDS yet.
GATE_ORDER: tuple = ("G1", "G2", "G3", "G4")

BLOCKING_RANK: dict = {"human": 0, "fence": 1, "clock": 2, "agent": 3, "": 4}

# The banner is the surface the UserPromptSubmit hook injects: its width, line
# count and speed are contract, not taste (task 3 brief).
LABEL_WIDTH = 8
BANNER_MAX_LINES = 7

# A hard cap applied to every rendered line (banner and receipt alike) so a
# pathological title or detail cannot blow the hook's injected text past a
# sane size. Not part of the documented contract; just a safety net behind it.
_MAX_LINE_WIDTH = 120

# NEXT is action.why compressed to one line: cut at the first sentence
# boundary or this many characters, whichever comes first.
_WHY_LIMIT = 92


@dataclasses.dataclass(frozen=True)
class FlowStatus:
    """One frozen record per flow: the render model and the JSON payload.

    Flattens the six NextAction fields (kind, why, blocking, command, gate_id,
    guidance_key) the surfaces use rather than nesting the record, because
    dataclasses.asdict on a nested frozen dataclass produces a two-level JSON
    that every consumer then has to unwrap. Those six fields are only ever
    assigned from a next_action return value (see build_status).
    """
    project: str
    slug: str
    flow_dir: str                 # absolute, for why/board links
    stage: str
    status: str                   # flow.json status, not the action
    step_index: Optional[int]
    step_of: Optional[int]
    step_title: str               # "" when there are no steps
    phase: str                    # "" when current_step.phase is None
    gates: tuple                  # (("G1", "approve 07-31"), ("G2", "-"), ...) for G1..G4
    sprint_slug: str
    sprint_status: str
    iteration: Optional[int]
    max_iterations: Optional[int]
    test_outcome: str             # "pass" | "fail" | ""
    verify_outcome: str           # "pass" | "fail" | ""
    cost_usd: Optional[float]     # always None until slice b wires BurnRate
    kind: str                     # NextAction.kind
    why: str                      # NextAction.why
    blocking: str                 # NextAction.blocking
    command: str                  # NextAction.command
    gate_id: Optional[str]
    guidance_key: str
    blocked_since: str            # ISO 8601 Z, "" when unknown
    blocked_seconds: Optional[int]
    chain_depth: Optional[int]
    chain_cap: Optional[int]
    guide: str                    # driver["guide"] or "coached"
    rung: Optional[int]
    updated_at: str                # flow.json's own updated_at, ISO 8601 Z, "" when unknown
    error: str                    # "" normally; the exception text for a row that failed to build


# --- resolve_sprint -----------------------------------------------------------

def resolve_sprint(flow: dict, log: list, sprints: list) -> Optional[dict]:
    """The sprint.json this flow's current step launched, matched by the
    emitted /sprint line, or None.

    Differs from terminal_sweep.match_terminal_sprints on purpose: no terminal
    status filter (the status surface must show a sprint that is still
    running, which the sweep matcher deliberately excludes), no swept index,
    and one flow at a time rather than a batch. Slice b's driver host
    consumes this directly.
    """
    flow = flow or {}
    emitted = ts.latest_sprint_emitted(log)
    if emitted is None:
        return None
    _project, spec_path = ts.parse_emitted_spec_path(emitted.get("detail", ""))
    if not spec_path:
        return None
    emitted_ts = emitted.get("ts", "")
    candidates = [
        s for s in (sprints or [])
        if s.get("project") == flow.get("project")
        and s.get("spec_path") == spec_path
        and s.get("created_at", "") > emitted_ts
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.get("created_at", ""))


# --- short, shared formatting --------------------------------------------------

def _short_wait(seconds) -> str:
    """'-' for None, '<n>s' under a minute, '<n>m' under a day, '<n>d' beyond.

    Used by the banner, the queue and the board so three surfaces cannot
    disagree about what 22 minutes looks like.
    """
    if seconds is None:
        return "-"
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "{}s".format(seconds)
    if seconds < 86400:
        return "{}m".format(seconds // 60)
    return "{}d".format(seconds // 86400)


def _gate_label(verdict: str, ts_: str, now: str) -> str:
    """'<verdict> MM-DD' for a decision on a different UTC date than `now`,
    '<verdict> MM-DD HH:MM' for one on the same date."""
    date_part = ts_[5:10]
    if ts_[:10] == now[:10]:
        return "{} {} {}".format(verdict, date_part, ts_[11:16])
    return "{} {}".format(verdict, date_part)


# --- gate_history ---------------------------------------------------------------

def gate_history(log: list, gate: Optional[dict], now: str) -> tuple:
    """One (gate_id, label) pair per id in GATE_ORDER.

    One pass over the log collects the newest gate_decided receipt per gate
    id (the verdict parsed from detail with a 'verdict=' split; a receipt
    whose detail does not carry one is ignored), then the currently open,
    undecided gate is overlaid on top of that.
    """
    decided = {}
    for entry in (log or []):
        if entry.get("action") != "gate_decided":
            continue
        detail = entry.get("detail") or ""
        if " verdict=" not in detail:
            continue
        gate_id, verdict = detail.split(" verdict=", 1)
        gate_id = gate_id.strip()
        entry_ts = entry.get("ts", "")
        if gate_id not in decided or entry_ts > decided[gate_id][0]:
            decided[gate_id] = (entry_ts, verdict)

    open_gate_id = None
    if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
        open_gate_id = gate.get("id")

    out = []
    for gate_id in GATE_ORDER:
        if gate_id == open_gate_id:
            wait = fl.age_seconds(gate.get("opened_at"), now)
            out.append((gate_id, "awaiting {}".format(_short_wait(wait))))
        elif gate_id in decided:
            entry_ts, verdict = decided[gate_id]
            out.append((gate_id, _gate_label(verdict, entry_ts, now)))
        else:
            out.append((gate_id, "-"))
    return tuple(out)


# --- blocked_since --------------------------------------------------------------

def blocked_since(flow: dict, log: list, action, now: str) -> Tuple[str, Optional[int]]:
    """(iso, seconds_or_None): how long the flow has been stuck on `action`.

    Per interpretation decision 4 of the plan: gate.opened_at when the action
    blocks on a human at an open gate, else the timestamp of the newest
    flow-log receipt, else flow.updated_at.
    """
    flow = flow or {}
    gate = flow.get("gate")
    if action.blocking == "human" and isinstance(gate, dict):
        since = gate.get("opened_at") or ""
    elif log:
        since = max(log, key=lambda e: e.get("ts", "")).get("ts", "")
    else:
        since = flow.get("updated_at", "") or ""
    return since, fl.age_seconds(since, now)


# --- build_status -----------------------------------------------------------

def build_status(flow: dict, log: list, sprint: Optional[dict], driver: dict,
                  now: str, flow_dir: str = "") -> FlowStatus:
    """Build the one FlowStatus record for this flow.

    Calls next_action(flow, sprint, driver) exactly once and flattens its six
    fields verbatim. `error` is always "" here: a build_status call that
    raises is the per-flow isolation Task 2's `collect` catches, not this
    function's concern.
    """
    flow = flow or {}
    log = log or []
    driver = driver or {}
    action = fp.next_action(flow, sprint, driver)
    sprint_dict = sprint or {}

    cs = flow.get("current_step") or {}
    step_index = cs.get("index")
    step_of = cs.get("of")
    steps = flow.get("steps") or []
    step_title = ""
    if isinstance(step_index, int) and 0 <= step_index < len(steps):
        step_title = steps[step_index].get("title", "") or ""

    gates = gate_history(log, flow.get("gate"), now)
    since, secs = blocked_since(flow, log, action, now)

    test_outcome = ""
    last_test = sprint_dict.get("last_test_result")
    if isinstance(last_test, dict):
        test_outcome = "pass" if last_test.get("exit_code") == 0 else "fail"

    verify_outcome = ""
    last_verify = sprint_dict.get("last_verify_result")
    if isinstance(last_verify, dict):
        verify_outcome = last_verify.get("outcome", "") or ""

    return FlowStatus(
        project=flow.get("project", ""),
        slug=flow.get("slug", ""),
        flow_dir=flow_dir,
        stage=flow.get("stage", ""),
        status=flow.get("status", ""),
        step_index=step_index,
        step_of=step_of,
        step_title=step_title,
        phase=cs.get("phase") or "",
        gates=gates,
        sprint_slug=sprint_dict.get("slug", ""),
        sprint_status=sprint_dict.get("status", ""),
        iteration=sprint_dict.get("iteration"),
        max_iterations=sprint_dict.get("max_iterations"),
        test_outcome=test_outcome,
        verify_outcome=verify_outcome,
        cost_usd=None,
        kind=action.kind,
        why=action.why,
        blocking=action.blocking,
        command=action.command,
        gate_id=action.gate_id,
        guidance_key=action.guidance_key,
        blocked_since=since,
        blocked_seconds=secs,
        chain_depth=driver.get("chain_depth"),
        chain_cap=driver.get("chain_cap"),
        guide=driver.get("guide", "coached"),
        rung=driver.get("rung"),
        updated_at=flow.get("updated_at", "") or "",
        error="",
    )


# --- ordering -----------------------------------------------------------------

def is_idle(status: FlowStatus) -> bool:
    """Idle when the flow's next action is the terminal flow_close, or when
    flow.json itself already reports the flow as closed.

    The second half of this covers the real repo today: no foreman.json
    driver record exists anywhere yet (slice b's job), so a closed flow's
    kind never actually reaches flow_close, it stalls on the first undone
    tail step (e.g. brief_write) instead. Without this, the board's Closed
    section, the queue's hidden-count and the hook's quiet-when-idle mode
    are all dead on arrival against real data.
    """
    return status.kind == "flow_close" or status.status == "closed"


def sort_key(status: FlowStatus) -> tuple:
    """Blocked-on-human first, then fence, clock, agent, non-blocking; within a
    rank, longest wait first. An unknown wait sorts as if it were zero, not as
    a crash."""
    rank = BLOCKING_RANK.get(status.blocking, BLOCKING_RANK[""])
    secs = status.blocked_seconds if status.blocked_seconds is not None else 0
    return (rank, -secs, status.project, status.slug)


def pick_focus(statuses: List[FlowStatus]) -> Optional[FlowStatus]:
    """The first non-idle status by sort_key, or None when everything is idle."""
    candidates = [s for s in (statuses or []) if not is_idle(s)]
    if not candidates:
        return None
    return sorted(candidates, key=sort_key)[0]


# --- banner rendering -----------------------------------------------------

def _or_dash(value) -> str:
    """None or '' renders as '-'; anything else is str(value). Never 'None'."""
    if value is None or value == "":
        return "-"
    return str(value)


def _compress_why(why: str) -> str:
    """`why` cut at the first sentence boundary or `_WHY_LIMIT` characters,
    whichever comes first. A hard cut gets a plain '...' suffix (never the
    U+2026 ellipsis character, a rendering hazard in some Windows consoles);
    a natural sentence boundary needs no suffix at all."""
    if not why:
        return "-"
    period = why.find(".")
    if period != -1 and period < _WHY_LIMIT:
        return why[:period + 1]
    if len(why) <= _WHY_LIMIT:
        return why
    return why[:_WHY_LIMIT] + "..."


def _line(label: str, content: str) -> str:
    """label left-padded to LABEL_WIDTH, one separating space, then content,
    hard-capped at _MAX_LINE_WIDTH."""
    return "{} {}".format(label.ljust(LABEL_WIDTH), content)[:_MAX_LINE_WIDTH]


def _gates_line_content(gates: tuple) -> str:
    by_id = dict(gates or ())
    return " | ".join(
        "{} {}".format(gate_id, by_id.get(gate_id, "-")) for gate_id in GATE_ORDER)


def _step_line_content(status: FlowStatus) -> str:
    if isinstance(status.step_index, int):
        display_index = status.step_index + 1
        if isinstance(status.step_of, int) and status.step_of:
            display_index = min(display_index, status.step_of)
        i = str(display_index)
    else:
        i = "-"
    n = str(status.step_of) if isinstance(status.step_of, int) else "-"
    title = '"{}"'.format(status.step_title) if status.step_title else "-"
    phase = status.phase if status.phase else "-"
    if status.chain_depth is None or status.chain_cap is None:
        chain = "-"
    else:
        chain = "{}/{}".format(status.chain_depth, status.chain_cap)
    return '{} of {}  {}        phase={}      chain {}'.format(i, n, title, phase, chain)


def _sprint_line_content(status: FlowStatus) -> str:
    cost = "${:.2f}".format(status.cost_usd) if status.cost_usd is not None else "-"
    return "{}  iter {}/{}  tests {}  verify {}  cost {}".format(
        _or_dash(status.sprint_slug), _or_dash(status.iteration),
        _or_dash(status.max_iterations), _or_dash(status.test_outcome),
        _or_dash(status.verify_outcome), cost)


def _blocked_line_content(status: FlowStatus) -> str:
    """'nothing' with no since clause when blocking is empty, else
    '<blocking>            since <wait>'."""
    if not status.blocking:
        return "nothing"
    return "{}            since {}".format(status.blocking, _short_wait(status.blocked_seconds))


def _banner_lines(status: FlowStatus, other_count: int, board_path: str, now: str) -> List[str]:
    """The seven spec lines for one focus flow. `other_count` is the number of
    non-idle flows other than `status`; `now` is accepted for symmetry with
    the rest of the module even though nothing here reads the clock directly
    (every time-derived field is already baked into `status`)."""
    del now  # unused: blocked_seconds/gate labels are already resolved on status
    proj_slug = "{}/{}".format(status.project, status.slug)
    foreman_content = "{}   guide={} rung={}".format(
        proj_slug, status.guide or "coached", _or_dash(status.rung))
    board_content = board_path
    if other_count > 0:
        suffix = "  (+{} more waiting)".format(other_count)
        budget = _MAX_LINE_WIDTH - (LABEL_WIDTH + 1) - len(suffix)
        if budget > 0 and len(board_path) > budget:
            board_path = board_path[:budget]
        board_content = board_path + suffix
    return [
        _line("FOREMAN", foreman_content),
        _line("STEP", _step_line_content(status)),
        _line("GATES", _gates_line_content(status.gates)),
        _line("SPRINT", _sprint_line_content(status)),
        _line("NEXT", _compress_why(status.why)),
        _line("BLOCKED", _blocked_line_content(status)),
        _line("BOARD", board_content),
    ]


def render_banner(statuses: List[FlowStatus], now: str, board_path: str,
                   quiet_when_idle: bool = False, flows_root: str = "") -> str:
    """The seven-line banner for the focus flow (pick_focus), plus a waiting
    counter on the BOARD line for every other non-idle flow.

    Two degenerate one-line cases: no flows at all, and no non-idle flows.
    With `quiet_when_idle`, both degenerate cases render as the empty string
    instead (what makes the UserPromptSubmit hook free when there is nothing
    to say); an active focus flow always renders, quiet_when_idle or not.

    `flows_root` names the root walked to find zero flows, for the "no flows
    at all" message. It is a separate argument from `board_path` (the
    board's own output path) on purpose: the two are different locations and
    conflating them names the wrong file in the degenerate message.
    """
    statuses = statuses or []
    if not statuses:
        if quiet_when_idle:
            return ""
        return _line("FOREMAN", "no flows under {}".format(flows_root))
    focus = pick_focus(statuses)
    if focus is None:
        if quiet_when_idle:
            return ""
        return _line("FOREMAN", "no active flows, {} closed".format(len(statuses)))
    other_count = sum(1 for s in statuses if s is not focus and not is_idle(s))
    return "\n".join(_banner_lines(focus, other_count, board_path, now))


def _format_receipt(entry: dict) -> str:
    """'HH:MM:SS  <outcome>  <actor>  <action>  <detail>', capped at
    _MAX_LINE_WIDTH the same way a banner line is."""
    entry_ts = entry.get("ts") or ""
    hhmmss = entry_ts[11:19] if len(entry_ts) >= 19 else _or_dash(entry_ts)
    line = "{}  {}  {}  {}  {}".format(
        hhmmss, _or_dash(entry.get("outcome")), _or_dash(entry.get("actor")),
        _or_dash(entry.get("action")), entry.get("detail") or "")
    return line[:_MAX_LINE_WIDTH]


def render_watch_frame(status: FlowStatus, log: list, now: str, board_path: str,
                        tail: int = 10) -> str:
    """The banner for `status` alone (no other-flows counter: this is a
    single-flow view), a blank line, then the last `tail` receipts of `log`
    formatted oldest-to-newest, each truncated to the line budget."""
    lines = _banner_lines(status, 0, board_path, now)
    lines.append("")
    tail_entries = (log or [])[-tail:] if tail > 0 else []
    for entry in tail_entries:
        lines.append(_format_receipt(entry))
    return "\n".join(lines)


# --- queue / next / why rendering (Task 4) -----------------------------------

_QUEUE_HEADERS = ("PROJECT/SLUG", "STAGE", "PHASE", "BLOCKED", "WAIT", "NEXT")


def _queue_row(status: FlowStatus) -> tuple:
    """One (project/slug, stage, phase, blocked, wait, next) tuple of plain
    strings: one row of render_queue's table."""
    ref = "{}/{}".format(status.project, status.slug)
    blocked = status.blocking if status.blocking else "-"
    return (ref, _or_dash(status.stage), _or_dash(status.phase), blocked,
            _short_wait(status.blocked_seconds), _compress_why(status.why))


def _error_count_line(error_count: int) -> Optional[str]:
    """'N flow(s) could not be read', or None when there is nothing to say."""
    if error_count > 0:
        return "{} flow(s) could not be read".format(error_count)
    return None


def render_queue(statuses: List[FlowStatus], now: str, show_all: bool = False,
                  error_count: int = 0) -> str:
    """The manager view: a header row, one row per non-idle flow (every flow
    when `show_all`), sorted human-blocked-first the same way `sort_key`
    orders the board everywhere else, then a trailing line counting the
    closed flows `show_all` would add and, when `error_count` is nonzero, a
    line counting flows whose flow.json could not be parsed at all (Task 2's
    `collect_with_errors`, threaded through here rather than recomputed).

    `now` is accepted for the same reason `render_banner` takes it even
    though nothing here reads the clock: every time-derived field
    (`blocked_seconds`, the gate labels) is already baked into each
    FlowStatus.
    """
    del now
    statuses = statuses or []
    error_line = _error_count_line(error_count)
    if not statuses:
        text = "the queue is empty: no flows found"
        return "{}\n{}".format(text, error_line) if error_line else text

    idle_count = sum(1 for s in statuses if is_idle(s))
    visible = list(statuses) if show_all else [s for s in statuses if not is_idle(s)]
    if not visible:
        text = "no active flows, {} closed".format(idle_count)
        return "{}\n{}".format(text, error_line) if error_line else text

    visible = sorted(visible, key=sort_key)
    rows = [_queue_row(s) for s in visible]
    widths = [max(len(_QUEUE_HEADERS[i]), max(len(row[i]) for row in rows))
              for i in range(len(_QUEUE_HEADERS))]
    lines = ["  ".join(_QUEUE_HEADERS[i].ljust(widths[i])
                        for i in range(len(widths))).rstrip()]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i])
                                for i in range(len(widths))).rstrip())

    hidden = idle_count if not show_all else 0
    if hidden > 0:
        lines.append("{} closed flow{} hidden (use --all to show)".format(
            hidden, "" if hidden == 1 else "s"))
    if error_line:
        lines.append(error_line)
    return "\n".join(lines)


def _gate_verdicts(gate_id: str) -> tuple:
    """Verdicts valid at `gate_id`, mirroring foreman_plan._allowed_verdicts
    over its two PUBLIC building blocks (SHIP_GATE_ID / SHIP_VERDICTS,
    flow_state.valid_verdicts) rather than reaching into that module's
    underscore-prefixed helper: the same house rule behind _log_for's local
    copy of terminal_sweep's private read logic."""
    if gate_id == fp.SHIP_GATE_ID:
        return fp.SHIP_VERDICTS
    return fs.valid_verdicts(gate_id)


def render_next(statuses: List[FlowStatus]) -> str:
    """The single highest-priority action waiting on a human or a fence, by
    `sort_key`: never a list. When nothing is, one sentence naming what
    everything else is doing instead."""
    statuses = statuses or []
    candidates = [s for s in statuses
                  if s.blocking in ("human", "fence") or s.kind in fp.NEEDS_HUMAN_KINDS]
    if not candidates:
        with_agent = sum(1 for s in statuses if s.blocking == "agent")
        closed = sum(1 for s in statuses if is_idle(s))
        return "nothing waiting on you: {} flows are with an agent, {} closed".format(
            with_agent, closed)

    focus = sorted(candidates, key=sort_key)[0]
    lines = [_line("FLOW", "{}/{}".format(focus.project, focus.slug)),
             _line("WHY", _compress_why(focus.why))]
    if focus.gate_id:
        lines.append(_line("ANSWER", ", ".join(_gate_verdicts(focus.gate_id))))
    if focus.command:
        lines.append(_line("DO", focus.command))
    return "\n".join(lines)


def render_why(status: FlowStatus, log: list, tail: int = 5) -> str:
    """The why chain: the last `tail` receipts oldest to newest, a blank-line
    separator (the same convention `render_watch_frame` uses between the
    banner and its receipt tail), then the current NextAction's kind,
    blocking, why, guidance_key and command."""
    lines = []
    tail_entries = (log or [])[-tail:] if tail > 0 else []
    for entry in tail_entries:
        lines.append(_format_receipt(entry))
    lines.append("")
    lines.append(_line("KIND", status.kind))
    lines.append(_line("BLOCKING", _or_dash(status.blocking)))
    lines.append(_line("WHY", status.why))
    lines.append(_line("GUIDANCE", status.guidance_key))
    lines.append(_line("COMMAND", _or_dash(status.command)))
    return "\n".join(lines)


def resolve_ref(statuses: List[FlowStatus], ref: str) -> FlowStatus:
    """`ref` as an exact 'project/slug', else a unique bare slug, else a
    unique substring of a slug. Ambiguity at any of the three levels raises
    LookupError naming the candidates instead of silently picking one; no
    match at all also raises LookupError."""
    statuses = statuses or []

    def _matches(pred):
        return [s for s in statuses if pred(s)]

    def _pick(candidates, label):
        if len(candidates) > 1:
            names = ", ".join("{}/{}".format(s.project, s.slug) for s in candidates)
            raise LookupError(
                "{} {!r} is ambiguous: matches {}".format(label, ref, names))
        return candidates[0] if candidates else None

    found = _pick(_matches(lambda s: "{}/{}".format(s.project, s.slug) == ref), "ref")
    if found is None:
        found = _pick(_matches(lambda s: s.slug == ref), "slug")
    if found is None:
        found = _pick(_matches(lambda s: ref in s.slug), "slug substring")
    if found is None:
        raise LookupError("no flow matches ref {!r}".format(ref))
    return found


def to_json(statuses: List[FlowStatus]) -> str:
    """The full board as one JSON array, one object per FlowStatus with the
    dataclass field names verbatim as keys: the single serializer every
    `--json` flag routes through."""
    return json.dumps([dataclasses.asdict(s) for s in (statuses or [])], indent=2)


# =============================================================================
# Thin host: load_driver() / collect() and, from Task 3, the CLI.
#
# This section is the ONLY part of the module that touches the filesystem or
# calls the clock, and it is still read-only: it opens flow.json, flow-log.ndjson,
# sprint.json and foreman.json for reading and writes nothing anywhere.
# =============================================================================

import argparse
import pathlib
import sys
import time

HALT_FILE = pathlib.Path(__file__).resolve().parent / "FOREMAN_HALT"
DEFAULT_FLOWS_ROOT = str(roots.flows_root())
DEFAULT_SPRINTS_ROOT = str(roots.sprints_root())
DEFAULT_BOARD_PATH = str(roots.foreman_board_path())


def load_driver(flow_dir, halt: Optional[bool] = None) -> dict:
    """Read <flow_dir>\\foreman.json, defaulting to {} on any failure to read it.

    Sets driver["halt"] from HALT_FILE.exists(), unless `halt` overrides that
    (so collect can stat the halt file once for the whole walk instead of
    once per flow).
    """
    path = pathlib.Path(flow_dir) / "foreman.json"
    driver: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            driver = loaded
    except (OSError, ValueError):
        driver = {}
    driver["halt"] = HALT_FILE.exists() if halt is None else halt
    return driver


def _collect_impl(flows_root, sprints_root, now: Optional[str]) -> Tuple[List[FlowStatus], int]:
    """Shared body of collect() and collect_with_errors(): one FlowStatus per
    discovered flow, sorted by sort_key, plus the count of discover_flows
    entries skipped as load_error (a flow.json that will not parse, so there
    is no flow dict to describe).

    Walks flows_root and sprints_root with terminal_sweep's discoverers, stats
    HALT_FILE once for the whole walk, and per flow calls load_driver,
    resolve_sprint and build_status inside a try/except: a flow whose
    build_status raises (the concrete trigger is next_action -> emit_sprint_line
    raising ValueError on a model_hint key that is not a /sprint phase) gets a
    FlowStatus with error set instead of taking out the rest of the board.
    """
    now = now or fs.iso_utc_now()
    halt = HALT_FILE.exists()
    flows = ts.discover_flows(flows_root)
    sprints = ts.discover_sprints(sprints_root)

    rows: List[FlowStatus] = []
    error_count = 0
    for ctx in flows:
        if ctx.get("load_error"):
            error_count += 1
            continue
        flow = ctx.get("flow") or {}
        log = ctx.get("log") or []
        flow_dir = ctx.get("flow_dir")
        try:
            driver = load_driver(flow_dir, halt=halt)
            sprint = resolve_sprint(flow, log, sprints)
            status = build_status(flow, log, sprint, driver, now,
                                  flow_dir=str(flow_dir))
        except Exception as exc:
            status = FlowStatus(
                project=flow.get("project", ""),
                slug=flow.get("slug", ""),
                flow_dir=str(flow_dir),
                stage=flow.get("stage", ""),
                status=flow.get("status", ""),
                step_index=None, step_of=None, step_title="", phase="",
                gates=(), sprint_slug="", sprint_status="",
                iteration=None, max_iterations=None,
                test_outcome="", verify_outcome="", cost_usd=None,
                kind="halt",
                why="{}/{} failed to build a status: {}".format(
                    flow.get("project", "?"), flow.get("slug", "?"), exc),
                blocking="fence", command="", gate_id=None,
                guidance_key="halt.status_error",
                blocked_since="", blocked_seconds=None,
                chain_depth=None, chain_cap=None, guide="coached", rung=None,
                updated_at=flow.get("updated_at", "") or "",
                error=str(exc),
            )
        rows.append(status)

    rows.sort(key=sort_key)
    return rows, error_count


def collect(flows_root, sprints_root, now: Optional[str] = None) -> List[FlowStatus]:
    """One FlowStatus per discovered flow, sorted by sort_key. See
    _collect_impl for the full walk. The load_error count _collect_impl also
    computes is dropped here so every existing caller (banner, watch, next,
    why) keeps destructuring a plain list unchanged; collect_with_errors is
    the variant that surfaces it.
    """
    rows, _error_count = _collect_impl(flows_root, sprints_root, now)
    return rows


def collect_with_errors(flows_root, sprints_root,
                         now: Optional[str] = None) -> Tuple[List[FlowStatus], int]:
    """Same as collect, plus the count of flows skipped because their
    flow.json would not parse: the count the board footer and the queue
    trailer surface (Task 5 / finding 3)."""
    return _collect_impl(flows_root, sprints_root, now)


def _log_for(flow_dir: str) -> list:
    """Read <flow_dir>\\flow-log.ndjson the same way terminal_sweep.discover_flows
    does, for the one flow watch() is drawing a receipt tail from. A local
    copy rather than reaching into terminal_sweep's private _read_ndjson: the
    parse rule (skip a line that will not parse, never raise) is a two-line
    rule not worth a cross-module private import.

    Degrades to an empty list on any read failure (a non-UTF-8 byte, a
    permission error, a file removed in the race between .exists() and the
    read), the same way load_driver degrades on a foreman.json it cannot
    read: a watch loop must not die because one receipt file is unreadable.
    """
    log_path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    entries: List[dict] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def watch(flows_root, sprints_root, interval: float = 10.0, ref: Optional[str] = None,
          max_iterations: Optional[int] = None, tail: int = 10,
          quiet_when_idle: bool = False, json_mode: bool = False) -> int:
    """Loop render_watch_frame (or the degenerate banner, when nothing is
    active) to stdout, clearing the screen first when stdout is a tty, using
    the same `\\x1b[2J\\x1b[H` sequence render_dashboard.watch_status_text uses.

    `ref` narrows the board to one "<project>/<slug>" before picking a focus,
    so a stale or misspelled ref degrades to the "no active flows" banner
    rather than raising. `max_iterations` stops the loop after that many
    frames instead of running forever, so a test can run it twice and exit;
    production calls (the CLI) leave it None. `tail`, `quiet_when_idle` and
    `json_mode` are additive keyword-only parameters (the same pattern as
    render_banner's quiet_when_idle) mirroring the CLI's `watch` subparser
    flags: `tail` sizes the receipt tail, `quiet_when_idle` suppresses a
    degenerate frame the same way it does on `banner`, and `json_mode`
    prints a raw JSON snapshot of the collected rows instead of a rendered
    frame each iteration (quiet_when_idle does not apply to `json_mode`:
    an empty list is still a valid, meaningful JSON snapshot for a machine
    reader, unlike an empty banner for a human one).
    """
    n = 0
    while True:
        n += 1
        rows = collect(flows_root, sprints_root)
        if ref:
            rows = [r for r in rows if "{}/{}".format(r.project, r.slug) == ref]
        now = fs.iso_utc_now()
        if json_mode:
            frame = json.dumps([dataclasses.asdict(r) for r in rows], indent=2)
        else:
            focus = pick_focus(rows)
            if focus is None:
                frame = render_banner(rows, now, DEFAULT_BOARD_PATH,
                                       quiet_when_idle=quiet_when_idle,
                                       flows_root=str(flows_root))
            else:
                frame = render_watch_frame(focus, _log_for(focus.flow_dir), now,
                                            DEFAULT_BOARD_PATH, tail=tail)
        if frame:
            if sys.stdout.isatty():
                sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(frame)
            sys.stdout.write("\n")
            sys.stdout.flush()
        if max_iterations is not None and n >= max_iterations:
            return 0
        time.sleep(interval)


# --- CLI ----------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foreman_status.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_common(sub_parser):
        sub_parser.add_argument("--flows-root", default=DEFAULT_FLOWS_ROOT)
        sub_parser.add_argument("--sprints-root", default=DEFAULT_SPRINTS_ROOT)
        sub_parser.add_argument("--flow", default=None,
            help="project/slug to force as the focus flow instead of picking one")
        sub_parser.add_argument("--quiet-when-idle", action="store_true")
        sub_parser.add_argument("--json", action="store_true")

    p_banner = sub.add_parser("banner", help="Print the seven-line status banner")
    _add_common(p_banner)

    p_watch = sub.add_parser("watch", help="Loop the banner and a log tail")
    _add_common(p_watch)
    p_watch.add_argument("--interval", type=float, default=10.0)
    p_watch.add_argument("--tail", type=int, default=10)

    def _add_roots(sub_parser):
        # queue/next/why work over the whole board and address a flow (why)
        # through resolve_ref's ref matching, not the exact-match --flow of
        # banner/watch, so they get a smaller shared flag set than _add_common.
        sub_parser.add_argument("--flows-root", default=DEFAULT_FLOWS_ROOT)
        sub_parser.add_argument("--sprints-root", default=DEFAULT_SPRINTS_ROOT)
        sub_parser.add_argument("--json", action="store_true")

    p_queue = sub.add_parser("queue", help="Print every flow, blocked-on and wait")
    _add_roots(p_queue)
    p_queue.add_argument("--all", action="store_true",
        help="include idle (closed) flows")

    p_next = sub.add_parser(
        "next", help="Print the single highest-priority action waiting on you")
    _add_roots(p_next)

    p_why = sub.add_parser(
        "why", help="Print the why chain (receipts plus the current action) for one flow")
    _add_roots(p_why)
    p_why.add_argument("ref",
        help="project/slug, a unique slug, or a unique slug substring")
    p_why.add_argument("--tail", type=int, default=5)

    return parser


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.cmd == "banner":
        rows = collect(args.flows_root, args.sprints_root)
        if args.flow:
            rows = [r for r in rows if "{}/{}".format(r.project, r.slug) == args.flow]
        if args.json:
            print(json.dumps([dataclasses.asdict(r) for r in rows], indent=2))
            return 0
        text = render_banner(rows, fs.iso_utc_now(), DEFAULT_BOARD_PATH,
                              quiet_when_idle=args.quiet_when_idle,
                              flows_root=str(args.flows_root))
        if text:
            print(text)
        return 0

    if args.cmd == "watch":
        return watch(args.flows_root, args.sprints_root, interval=args.interval,
                     ref=args.flow, tail=args.tail,
                     quiet_when_idle=args.quiet_when_idle, json_mode=args.json)

    if args.cmd == "queue":
        rows, error_count = collect_with_errors(args.flows_root, args.sprints_root)
        if args.json:
            print(to_json(rows))
            return 0
        print(render_queue(rows, fs.iso_utc_now(), show_all=args.all,
                            error_count=error_count))
        return 0

    if args.cmd == "next":
        rows = collect(args.flows_root, args.sprints_root)
        if args.json:
            print(to_json(rows))
            return 0
        print(render_next(rows))
        return 0

    if args.cmd == "why":
        rows = collect(args.flows_root, args.sprints_root)
        try:
            status = resolve_ref(rows, args.ref)
        except LookupError as exc:
            print(str(exc))
            return 1
        if args.json:
            print(to_json([status]))
            return 0
        print(render_why(status, _log_for(status.flow_dir), tail=args.tail))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
