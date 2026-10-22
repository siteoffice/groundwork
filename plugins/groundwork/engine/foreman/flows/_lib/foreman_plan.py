"""foreman_plan.py - the pure "what next" seam for the Foreman driver (slice a).

Spec: (design spec, not shipped)

One public function, and it is the whole design:

    next_action(flow: dict, sprint: Optional[dict], driver: dict) -> NextAction

Pure: no I/O, no clock, no network, no model, same discipline as the core of
terminal_sweep.py and all of sprints\\_lib\\model_map.py. Every input arrives as
an argument and the only output is a value. This module NEVER writes flow.json,
NEVER advances a flow, NEVER decides a gate, and NEVER launches anything. It
names the next action; a host (slice b, not built yet) executes it.

It is the single "what next" seam: the CLI, the Cowork skill, the Slack prompt,
the status banner, the HTML board and any future Dispatch transport all call
next_action and render the same record differently, which is the property that
stops those surfaces from drifting.

`why` is a template fill derived from state, never model output.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import dataclasses
from typing import Optional

import flow_state as fs
import flow_transition as ft  # emit_sprint_line only; it is pure and does no import-time I/O

# --- contracts --------------------------------------------------------------

ACTION_KINDS = (
    "flow_advance",      # cross a gate-free boundary via cmd_advance
    "gate_open",         # a gate needs opening, then a prompt
    "gate_await",        # gate open and undecided, nothing to do but wait
    "gate_consume",      # gate decided, feed the verdict into cmd_advance
    "sprint_launch",     # run the emitted /sprint line
    "sprint_await",      # sprint running, not terminal
    "sprint_recover",    # sprint capped, failed or escalated
    "ship_preflight",    # run the deterministic ship checks
    "ship_execute",      # G4 shipped, run merge and push
    "brief_write",       # compose and write the hub_agent_update brief
    "hub_ingest",        # run hub-update propagation
    "hub_refresh",       # assemble.py --force plus render_mission_deck.py
    "flow_close",        # terminal
    "halt",              # a fence tripped, driver must stop
)

# Kinds whose NextAction.blocking is "" in the pure seam's own terms, but that
# foreman_run.decide()'s host-level judgment (or an equivalent read-only check)
# treats as needing a human. Currently just gate_open: a gate that has not
# opened yet judges an artifact the pure seam cannot confirm exists, so it is
# never executed automatically (see decide()'s needs_judgment branch). Any
# future kind added to NOT_YET_EXECUTABLE in foreman_run.py for the same
# "structurally needs a human, but blocking=='' " reason belongs here too.
NEEDS_HUMAN_KINDS = frozenset({"gate_open"})

# "clock" is reserved for a later transport (a Dispatch job awaiting a poll
# interval). This module is clock-free by contract and therefore never returns
# it; see the plan's interpretation decision 5.
BLOCKING_KINDS = ("", "human", "agent", "clock", "fence")

# The subset of slice f's role table that maps one-to-one onto an action kind.
# The stage-judgment roles (idea_shape, breakdown, grill, ship_review) attach to
# gate presentation, not to a mechanical action, and belong to slices e and f.
ROLES = ("none", "ship_preflight", "learn_summarise", "hub_ingest", "hub_refresh")

HALT_REASONS = ("halt_file", "paused", "flow_paused", "chain_cap",
                "unknown_stage", "unknown_phase", "malformed_gate")

# Slice c adds G4 to flow_state.GATE_IDS and VERDICTS_BY_GATE. That module is
# out of scope for this build-order step, so the ship gate is named locally and
# recognised defensively. test_foreman_plan asserts these agree with flow_state
# the moment flow_state grows them, so the duplication cannot drift silently.
SHIP_GATE_ID = "G4"
SHIP_VERDICTS = ("ship", "hold", "park")

# Identical to terminal_sweep.SUCCESS_STATUSES / NEEDS_GUIDANCE_STATUSES.
# Redeclared rather than imported so this pure module does not pull in
# terminal_sweep's host section (which imports outbox and flow_transition for
# I/O). test_foreman_plan asserts the two pairs are equal, so drift is caught.
SUCCESS_STATUSES = frozenset({"passing", "done"})
NEEDS_GUIDANCE_STATUSES = frozenset({"capped", "failed", "escalated"})

DEFAULT_CHAIN_CAP = 12

# --- slice e: the guidance rungs ---------------------------------------------

# The four levels of explanation, quietest first. `silent` prints nothing (the
# JSON path already carries every field guidance would summarize), `terse` is
# the NextAction's own `why`, `coached` is the static fragment for the key, and
# `teaching` is the fragment plus a one-time road-ahead block.
GUIDE_LEVELS = ("silent", "terse", "coached", "teaching")

# Completed flows after which the default rung drops from coached to terse.
# Named rather than inlined so the boundary is one edit, and so the test that
# walks 0/1/2/3 can assert against the same constant the function reads.
TERSE_AFTER_COMPLETIONS = 3


@dataclasses.dataclass(frozen=True)
class NextAction:
    """The single record every Foreman surface renders. Field order is the
    spec's field order."""
    kind: str
    why: str
    blocking: str
    command: str
    gate_id: Optional[str]
    step_index: Optional[int]
    role: str
    guidance_key: str


# --- helpers ----------------------------------------------------------------

def _ref(flow: dict) -> str:
    """'<project>/<slug>', the argument shape every /flow subcommand takes."""
    return "{}/{}".format(flow.get("project", ""), flow.get("slug", ""))


def _advance_cmd(flow: dict) -> str:
    return "/flow advance {}".format(_ref(flow))


def _step_index(flow: dict):
    cs = flow.get("current_step") or {}
    idx = cs.get("index")
    return idx if isinstance(idx, int) else None


def _step_label(flow: dict) -> str:
    """'step 2 of 5' for the why template; 'the flow' when there are no steps."""
    cs = flow.get("current_step") or {}
    idx, of = cs.get("index"), cs.get("of")
    if not isinstance(idx, int) or not isinstance(of, int) or of <= 0:
        return "the flow"
    return "step {} of {}".format(idx + 1, of)


def _verdict_of(gate: dict) -> str:
    return ((gate or {}).get("decision") or {}).get("verdict") or ""


def _allowed_verdicts(gate_id: str) -> tuple:
    """Verdicts valid at `gate_id`, including the ship gate flow_state does not
    know about yet."""
    if gate_id == SHIP_GATE_ID:
        return SHIP_VERDICTS
    return fs.valid_verdicts(gate_id)


# --- halt precedence --------------------------------------------------------

_HALT_WHY = {
    "halt_file": "the global FOREMAN_HALT kill switch is set, so no action runs "
                 "on any flow until it is removed",
    "paused": "this flow is paused: {detail}",
    "flow_paused": "flow status is paused (parked at a gate), so nothing "
                   "advances until it is resumed",
    "chain_cap": "the chain cap of {detail} non-blocking actions is reached in "
                 "this invocation, which is the fence against a plan bug that "
                 "oscillates between two states",
    "unknown_stage": "stage {detail} is not a known flow stage, so the next "
                     "action is not computable and the driver stops",
    "unknown_phase": "step phase {detail} is not a known execute phase, so the "
                     "next action is not computable and the driver stops",
    "malformed_gate": "the flow's gate is malformed: {detail}, so the next "
                      "action is not computable and the driver stops",
}


def _halt(flow: dict, reason: str, detail: str = "") -> NextAction:
    return NextAction(
        kind="halt",
        why=_HALT_WHY[reason].format(detail=detail),
        blocking="fence",
        command="",
        gate_id=None,
        step_index=_step_index(flow),
        role="none",
        guidance_key="halt.{}".format(reason),
    )


def _halt_reason(flow: dict, driver: dict):
    """(reason, detail) for the first tripped fence, else (None, '')."""
    if driver.get("halt"):
        return "halt_file", ""
    paused_reason = driver.get("paused_reason")
    if paused_reason:
        return "paused", str(paused_reason)
    if flow.get("status") == "paused":
        return "flow_paused", ""
    depth = driver.get("chain_depth", 0)
    cap = driver.get("chain_cap", DEFAULT_CHAIN_CAP)
    if isinstance(depth, int) and isinstance(cap, int) and depth >= cap:
        return "chain_cap", str(cap)
    return None, ""


# --- gate validity ------------------------------------------------------------

# The gate ids this seam ever routes on. SHIP_GATE_ID is included even though
# flow_state does not carry G4 yet (see the module-level comment on
# SHIP_GATE_ID above).
_RECOGNISED_GATE_IDS = ("G1", "G2", "G3", SHIP_GATE_ID)
_RECOGNISED_GATE_STATUSES = ("awaiting-owner", "decided")


def _malformed_gate_reason(gate: dict):
    """None if `gate` is well-formed enough to route on, else a detail string
    for the malformed_gate halt.

    flow_state._validate_gate does not require `decision` to be non-null when
    `status == "decided"`, so a gate can be a valid flow.json and still be
    unroutable here. Guessing through it would produce a broken guidance_key
    (a trailing dot from an empty verdict, or '?' from a missing id), and
    slice e composes guidance_key straight into a filesystem path, so this
    halts instead of guessing.
    """
    gate_id = gate.get("id")
    if gate_id not in _RECOGNISED_GATE_IDS:
        return "gate id {} is not recognised".format(repr(gate_id))
    status = gate.get("status")
    if status not in _RECOGNISED_GATE_STATUSES:
        return "gate {} has unrecognised status {}".format(gate_id, repr(status))
    if status == "decided" and not _verdict_of(gate):
        return "gate {} is decided but carries no verdict".format(gate_id)
    return None


# --- gate branches ----------------------------------------------------------

def _gate_await(flow: dict, gate: dict) -> NextAction:
    gate_id = gate.get("id") or "?"
    verdicts = ", ".join(_allowed_verdicts(gate_id))
    return NextAction(
        kind="gate_await",
        why="{} is open at {} and undecided, so nothing moves until a verdict "
            "arrives; allowed verdicts: {}".format(gate_id, _step_label(flow),
                                                   verdicts),
        blocking="human",
        # For a human-blocking kind, `command` is the template the operator
        # completes; the verdict itself is the judgment being asked for.
        command="/flow gate {} <verdict>".format(_ref(flow)),
        gate_id=gate_id,
        step_index=gate.get("step_index"),
        role="none",
        guidance_key="gate_await.{}".format(gate_id.lower()),
    )


def _gate_consume(flow: dict, gate: dict) -> NextAction:
    gate_id = gate.get("id") or "?"
    verdict = _verdict_of(gate)
    return NextAction(
        kind="gate_consume",
        why="{} is decided with verdict {} at {}, so the verdict feeds into the "
            "next advance".format(gate_id, verdict, _step_label(flow)),
        blocking="",
        command=_advance_cmd(flow),
        gate_id=gate_id,
        step_index=gate.get("step_index"),
        role="none",
        guidance_key="gate_consume.{}.{}".format(gate_id.lower(), verdict),
    )


def _gate_open(flow: dict, gate_id: str, step_index) -> NextAction:
    return NextAction(
        kind="gate_open",
        why="{} has no open gate and {} needs a {} decision, so the gate opens "
            "next".format(_step_label(flow), flow.get("stage", ""), gate_id),
        blocking="",
        command=_advance_cmd(flow),
        gate_id=gate_id,
        step_index=step_index,
        role="none",
        guidance_key="gate_open.{}".format(gate_id.lower()),
    )


# --- the ladder -------------------------------------------------------------

def next_action(flow: dict, sprint: Optional[dict], driver: dict) -> NextAction:
    """Return the single next action for this state. Pure, total, idempotent.

    Precedence, highest first:
      1. fences from `driver` and a parked flow      -> halt
      2. stage closed                                -> the tail, then flow_close
      3. an open undecided gate                      -> gate_await
      4. a decided gate                               -> ship_execute or gate_consume
      5. stage idea / design                         -> flow_advance
      6. stage breakdown                             -> gate_open G1
      7. stage execute                               -> per current_step.phase
    Anything unrecognised halts rather than guessing.
    """
    flow = flow or {}
    driver = driver or {}

    reason, detail = _halt_reason(flow, driver)
    if reason:
        return _halt(flow, reason, detail)

    stage = flow.get("stage")

    if stage == "closed":
        return _tail(flow, driver)

    gate = flow.get("gate")
    if isinstance(gate, dict):
        malformed = _malformed_gate_reason(gate)
        if malformed:
            return _halt(flow, "malformed_gate", malformed)
        if gate.get("status") == "awaiting-owner":
            return _gate_await(flow, gate)
        return _decided(flow, gate, driver)

    if stage in ("idea", "design"):
        return NextAction(
            kind="flow_advance",
            why="stage is {} with no open gate, so the next boundary is "
                "gate-free and crosses directly".format(stage),
            blocking="",
            command=_advance_cmd(flow),
            gate_id=None,
            step_index=_step_index(flow),
            role="none",
            guidance_key="flow_advance.{}".format(stage),
        )

    if stage == "breakdown":
        return _gate_open(flow, "G1", None)

    if stage == "execute":
        return _execute(flow, sprint, driver)

    return _halt(flow, "unknown_stage", repr(stage))


def _decided(flow: dict, gate: dict, driver: dict) -> NextAction:
    """A decided gate. The one special case is a G4 `ship` that has not run yet:
    the merge and push happen before the verdict is consumed, so the receipt
    order reads ship-then-advance."""
    if (gate.get("id") == SHIP_GATE_ID
            and _verdict_of(gate) == "ship"
            and not driver.get("shipped")):
        return _ship_execute(flow, gate, driver)
    return _gate_consume(flow, gate)


def _ship_execute(flow: dict, gate: dict, driver: dict) -> NextAction:
    preflight = driver.get("preflight") or {}
    branch = preflight.get("worktree_branch") or "sprint/{}".format(
        flow.get("slug", ""))
    sha = preflight.get("revert_point") or "unrecorded"
    return NextAction(
        kind="ship_execute",
        why="G4 is decided with verdict ship at {}, so branch {} merges with "
            "--no-ff and pushes; pre-merge sha {}".format(
                _step_label(flow), branch, sha),
        blocking="",
        # Internal: slice c composes the exact git invocation together with the
        # directory it runs in. A bare git line here would break the repo rule
        # that every command snippet states where it runs.
        command="",
        gate_id=SHIP_GATE_ID,
        step_index=gate.get("step_index"),
        role="none",
        guidance_key="ship_execute",
    )


def _execute(flow: dict, sprint: Optional[dict], driver: dict) -> NextAction:
    cs = flow.get("current_step") or {}
    phase = cs.get("phase")
    index = cs.get("index")
    if phase == "grill":
        return _gate_open(flow, "G2", index)
    if phase == "learn":
        return _gate_open(flow, "G3", index)
    if phase == "sprint":
        return _sprint(flow, sprint, driver, index)
    return _halt(flow, "unknown_phase", repr(phase))


def _sprint(flow: dict, sprint: Optional[dict], driver: dict,
            index) -> NextAction:
    """The five outcomes at phase sprint with no gate open."""
    if not sprint:
        launch = (driver or {}).get("launch") or {}
        if launch:
            # The double-launch window (spec 2026-08-16, Problem fact 3):
            # between "the driver authorised a launch" and "sprint.json exists
            # on disk", resolve_sprint returns None, the relaunch guard matches
            # nothing (it only matches TERMINAL sprints) and the WIP limit
            # counts nothing (there is no sprint.json to count). The advisory
            # launch record is the only thing that knows, so the pure seam reads
            # it here and stays the single answer to "what next": the banner,
            # the board, /foreman why and any future Slack prompt cannot then
            # disagree about whether a launch is outstanding.
            #
            # `command` is the authorised line itself, not an empty string: for
            # an agent-blocking kind the command is what the session is meant to
            # be running, the same way _gate_await's command is the template the
            # human completes. It also means a re-run of /foreman re-presents
            # the exact line, still marked AUTHORISED.
            return NextAction(
                kind="sprint_await",
                why="{} is at phase sprint and a launch was authorised at {}, "
                    "but no sprint.json has appeared for it yet, so the driver "
                    "waits rather than authorising a second launch".format(
                        _step_label(flow), launch.get("ts") or "an unrecorded time"),
                blocking="agent",
                command=launch.get("line") or "",
                gate_id=None,
                step_index=index,
                role="none",
                guidance_key="sprint_await",
            )
        return NextAction(
            kind="sprint_launch",
            why="{} is at phase sprint and no sprint has been launched for it, "
                "so the emitted /sprint line runs next".format(
                    _step_label(flow)),
            blocking="",
            command=ft.emit_sprint_line(flow),
            gate_id=None,
            step_index=index,
            role="none",
            guidance_key="sprint_launch",
        )

    status = sprint.get("status")

    if status in NEEDS_GUIDANCE_STATUSES:
        return NextAction(
            kind="sprint_recover",
            why="the sprint for {} reached terminal status {}, so the step "
                "moves to learn and G3 opens carrying the recovery "
                "context".format(_step_label(flow), status),
            blocking="",
            command=_advance_cmd(flow),
            gate_id=None,
            step_index=index,
            role="none",
            guidance_key="sprint_recover.{}".format(status),
        )

    if status in SUCCESS_STATUSES:
        if not driver.get("preflight"):
            return NextAction(
                kind="ship_preflight",
                why="the sprint for {} reached success status {}, so the "
                    "deterministic ship checks run before the ship gate "
                    "opens".format(_step_label(flow), status),
                blocking="",
                command="",
                gate_id=None,
                step_index=index,
                role="ship_preflight",
                guidance_key="ship_preflight",
            )
        return _gate_open(flow, SHIP_GATE_ID, index)

    return NextAction(
        kind="sprint_await",
        why="{} is at phase sprint and sprint status is {}, which is not "
            "terminal, so the driver waits".format(
                _step_label(flow), status if status else "unknown"),
        blocking="agent",
        command="",
        gate_id=None,
        step_index=index,
        role="none",
        guidance_key="sprint_await",
    )


_TAIL_STEPS = (
    ("brief_written", "brief_write", "learn_summarise",
     "the flow is closed and no hub_agent_update brief has been written yet, "
     "so the brief is composed from the receipts next"),
    ("hub_ingested", "hub_ingest", "hub_ingest",
     "the brief is written but not ingested, so hub-update propagation runs "
     "next"),
    ("hub_refreshed", "hub_refresh", "hub_refresh",
     "the brief is ingested but the boards are stale, so the deck and the "
     "dashboards are regenerated next"),
)


def _tail(flow: dict, driver: dict) -> NextAction:
    """Stage closed: walk the three tail actions in order, then terminate.

    Tail progress is not derivable from flow.json, and this function may not
    read the flow log, so it arrives on the advisory driver record. A missing
    or partial `tail` dict simply means "not done yet", which makes a deleted
    foreman.json replay the tail rather than skip it. That is the safe
    direction: every tail action is idempotent by design in slice g.
    """
    tail = driver.get("tail") or {}
    for key, kind, role, why in _TAIL_STEPS:
        if not tail.get(key):
            return NextAction(
                kind=kind,
                why=why,
                blocking="",
                command="",
                gate_id=None,
                step_index=_step_index(flow),
                role=role,
                guidance_key=kind,
            )
    return NextAction(
        kind="flow_close",
        why="the flow is closed and the brief, the ingest and the refresh are "
            "all recorded, so there is nothing left to do",
        blocking="",
        command="",
        gate_id=None,
        step_index=_step_index(flow),
        role="none",
        guidance_key="flow_close",
    )


def resolve_guide_level(operator: dict, explicit: Optional[str]) -> str:
    """The operator's guidance rung for this invocation. Pure, total.

    Precedence, first match wins:
      1. `explicit`, when it is one of GUIDE_LEVELS. The CLI passes the
         --guide flag here, falling back to operator.json's own `guide`
         override, so a stored preference and a typed one resolve identically.
      2. "terse", once `operator["completions"]` has reached
         TERSE_AFTER_COMPLETIONS. A fluent operator does not need the fragment
         re-read to them every time.
      3. "coached", the default for everyone else.

    Never raises and never mutates `operator`: a missing, null or non-integer
    completion count reads as zero, which is the safe direction (more
    explanation, not less).
    """
    if explicit in GUIDE_LEVELS:
        return explicit
    completions = (operator or {}).get("completions", 0)
    if not isinstance(completions, int) or isinstance(completions, bool):
        completions = 0
    if completions >= TERSE_AFTER_COMPLETIONS:
        return "terse"
    return "coached"
