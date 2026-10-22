"""foreman_run.py - the Foreman driver host and the chain (slice b).

Spec: (design spec, not shipped)

The host does the I/O the pure core refuses to do, and runs the chain:

    action = next_action(load_flow(), load_sprint(), load_driver())
    if it blocks or a fence trips: present and stop
    else: execute, record the receipt, re-render, chain_depth += 1

Split, matching terminal_sweep.py and foreman_status.py: a pure section (no
I/O, no clock, every input an argument) and a thin host section below the
banner comment that does all the filesystem work and all the printing.

This module NEVER writes flow.json, NEVER mutates a gate and NEVER
reimplements a transition: every state change goes through
flow_transition.cmd_advance_guarded, so the existing single write paths stay
single. It never launches a sprint, never touches git, never writes a hub
brief and never calls a model.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
from typing import Optional, Tuple

import brief_composer as bc
import flow_state as fs
import flow_transition as ft
import foreman_cost as fcost
import foreman_lock as fl
import foreman_plan as fp
import foreman_status as fst
import render_flows
import render_guidance as rg
import roots
import ship_preflight as sp
import terminal_sweep as ts

# --- contracts ----------------------------------------------------------------

STOP_REASONS = (
    "complete",         # flow_close: nothing left to do
    "blocking",         # the seam itself blocks (halt / gate_await / sprint_await)
    "budget",           # per-flow token-cost ceiling reached
    "cowork",           # running under the Cowork mount, this kind is refused there
    "wip_limit",        # one sprint in dev per repo, already at the limit
    "needs_steps",      # G1 approve cannot be consumed without a step list
    "needs_spec",       # G2 approve on a multi-step flow with no per-step spec
    "malformed_launch", # the launch line failed a shape check    (host only)
    "not_executable",   # no executor built for this kind yet (see decision 1)
    "needs_judgment",   # gate_open: the artifact this gate judges may not exist yet
    "lock_busy",        # another writer holds foreman.lock            (host only)
    "relaunch_guard",   # emitting would relaunch a finished sprint    (host only)
    "spec_missing",     # the spec the launch line names is not on disk (host only)
    "execute_error",    # the write path raised                        (host only)
    "await_timeout",    # --await ran out of time waiting        (host only)
)


@dataclasses.dataclass(frozen=True)
class HostFacts:
    """Facts the pure seam is forbidden to have. Every one is injected."""
    cowork: bool = False
    wip_count: int = 0
    wip_limit: int = 1
    cost_usd: Optional[float] = None      # None means unknown, which never trips
    budget_usd: Optional[float] = None    # None means no ceiling set


@dataclasses.dataclass(frozen=True)
class Decision:
    action: fp.NextAction   # the seam's record, unmodified, command intact
    execute: bool
    reason: str             # "" when execute is True, else one of STOP_REASONS
    detail: str             # "" when execute is True, else one sentence

    @property
    def guidance_key(self) -> str:
        """The seam's key when executing, else 'stop.<reason>' for slice e."""
        return self.action.guidance_key if self.execute else "stop." + self.reason


@dataclasses.dataclass(frozen=True)
class ChainResult:
    executed: tuple         # action kinds executed this invocation, in order
    stopped: Decision       # the decision that ended the chain
    chain_depth: int
    receipts: int


EXECUTABLE_KINDS = ("flow_advance", "gate_consume", "sprint_launch",
                    "ship_preflight", "ship_execute", "sprint_recover",
                    "brief_write", "hub_ingest", "hub_refresh")

# Every action kind now has an executor (build-order step 7, slice g, closed
# 2026-08-23). The constant stays: decide()'s membership check is the ladder's
# documented parking place for any kind added to ACTION_KINDS without a
# bucket, and the defensive fallthrough below it still refuses one.
NOT_YET_EXECUTABLE = frozenset()

COWORK_FORBIDDEN = frozenset({"sprint_launch", "ship_execute"})
DEFAULT_WIP_LIMIT = 1

_STOP_DETAIL = {
    "complete": "the flow is closed and every recorded tail step is done, so "
                "there is nothing left to do",
    "blocking": "the next action blocks on {blocking}, so the chain stops here "
                "and asks",
    "budget": "this flow has reached its ${budget:.2f} ceiling at an estimated "
              "${cost:.2f} of API-equivalent spend, so the driver stops and "
              "asks; note the BurnRate store is refreshed daily, so the figure "
              "can be up to 24 hours behind",
    "cowork": "the driver is running under the Cowork mount, which cannot hold "
              "a .git lock, so {kind} is emitted for the operator to run on the "
              "laptop instead of being executed here",
    "wip_limit": "{count} sprint(s) are already non-terminal in this repo and "
                 "the limit is {limit}, so a second sprint does not launch",
    "needs_steps": "G1 is decided approve, but consuming it needs the step list "
                   "the breakdown produces, which the driver cannot derive from "
                   "flow.json",
    "needs_spec": "G2 is decided approve on a multi-step flow that records no "
                  "spec_path, so consuming it here would leave this step and "
                  "every later one falling back to the whole-flow spec; consume "
                  "it through the /flow skill instead, which calls "
                  "ft.cmd_advance(<flow_dir>, now, spec_path='<path>') and "
                  "records the per-step spec",
    "malformed_launch": "the launch line failed a shape check and was not "
                        "authorised: {detail}",
    "not_executable": "{kind} has no executor in this build-order step, so the "
                      "driver prints the command line and stops",
    "needs_judgment": "{gate} judges an artifact the driver cannot confirm "
                      "exists yet, so the gate is opened by the operator, not "
                      "by the driver",
    "lock_busy": "another writer holds foreman.lock, so this invocation yields "
                 "rather than crossing a boundary twice",
    "relaunch_guard": "emitting a launch line here would relaunch a sprint that "
                      "already finished for this spec: {detail}",
    "spec_missing": "the spec the launch line would name does not exist on "
                    "disk, so nothing was authorised: {detail}",
    "execute_error": "the write path raised and nothing further was attempted: "
                     "{detail}",
    "await_timeout": "the await ran out after {elapsed:.0f}s with the sprint at "
                     "phase {phase}, iteration {iteration}; nothing was lost and "
                     "re-running with --await resumes waiting",
}


def is_cowork_mount(root_path: str, os_name: str, environ: dict) -> bool:
    """Interpretation decision 15: an explicit FOREMAN_COWORK env var wins in
    both directions; else non-Windows is Cowork; else a root path that does
    not look like a Windows drive letter is Cowork; else not."""
    override = environ.get("FOREMAN_COWORK")
    if override in ("1", "true", "yes"):
        return True
    if override in ("0", "false", "no"):
        return False
    if os_name != "nt":
        return True
    return re.match(r"^[A-Za-z]:[\\/]", root_path) is None


def wip_count(project: str, sprints: list) -> int:
    """Case-insensitive project match (decision 16), counting sprints whose
    status is not in terminal_sweep.TERMINAL_STATUSES."""
    project = project.lower()
    return sum(
        1 for s in sprints
        if str(s.get("project", "")).lower() == project
        and s.get("status") not in ts.TERMINAL_STATUSES
    )


def decide(action: fp.NextAction, facts: HostFacts, verdict: str = "",
          step_of: Optional[int] = None, flow_spec_path: str = "") -> Decision:
    """The ten-rule precedence ladder (interpretation decision 8, plus the
    needs_spec rule of the 2026-08-16 step 5 spec), first match wins. Never
    raises: an unrecognized kind falls through to execute=False,
    reason="not_executable" rather than to execution, which is the safe
    direction."""
    kind = action.kind

    if kind == "flow_close":
        return Decision(action, False, "complete", _STOP_DETAIL["complete"])

    if action.blocking:
        return Decision(action, False, "blocking",
                         _STOP_DETAIL["blocking"].format(blocking=action.blocking))

    if (facts.budget_usd is not None and facts.cost_usd is not None
            and facts.cost_usd >= facts.budget_usd):
        return Decision(action, False, "budget", _STOP_DETAIL["budget"].format(
            budget=facts.budget_usd, cost=facts.cost_usd))

    if facts.cowork and kind in COWORK_FORBIDDEN:
        return Decision(action, False, "cowork",
                         _STOP_DETAIL["cowork"].format(kind=kind))

    if kind == "sprint_launch" and facts.wip_count >= facts.wip_limit:
        return Decision(action, False, "wip_limit", _STOP_DETAIL["wip_limit"].format(
            count=facts.wip_count, limit=facts.wip_limit))

    if kind == "gate_consume" and action.gate_id == "G1" and verdict == "approve":
        return Decision(action, False, "needs_steps", _STOP_DETAIL["needs_steps"])

    if (kind == "gate_consume" and action.gate_id == "G2" and verdict == "approve"
            and isinstance(step_of, int) and step_of > 1 and not flow_spec_path):
        return Decision(action, False, "needs_spec", _STOP_DETAIL["needs_spec"])

    if kind in NOT_YET_EXECUTABLE:
        return Decision(action, False, "not_executable",
                         _STOP_DETAIL["not_executable"].format(kind=kind))

    if kind == "gate_open":
        return Decision(action, False, "needs_judgment",
                         _STOP_DETAIL["needs_judgment"].format(gate=action.gate_id))

    if kind in EXECUTABLE_KINDS:
        return Decision(action, True, "", "")

    # Defensive: a kind this ladder does not recognize (e.g. added to
    # ACTION_KINDS without being sorted into a bucket here) must never execute.
    return Decision(action, False, "not_executable",
                     _STOP_DETAIL["not_executable"].format(kind=kind))


# --- presentation ---------------------------------------------------------

# The banner's width, matching foreman_status.LABEL_WIDTH so the two surfaces
# read as one product.
LABEL_WIDTH = 8


def _line(label: str, content: str) -> str:
    """label left-padded to LABEL_WIDTH, one separating space, then content.

    A local copy of foreman_status._line's format rather than an import of
    that module's private helper, the same house rule record_fence and
    read_log already follow for their own local copies of private read logic.
    """
    return "{} {}".format(label.ljust(LABEL_WIDTH), content)


def _chain_line_content(executed: tuple) -> str:
    count = len(executed)
    if not executed:
        return "{} action(s) executed".format(count)
    return "{} action(s) executed: {}".format(count, ", ".join(executed))


def _gate_verdicts(gate_id: str) -> tuple:
    """Verdicts valid at `gate_id`, composed from the same two public
    building blocks foreman_status._gate_verdicts uses
    (foreman_plan.SHIP_GATE_ID / SHIP_VERDICTS, flow_state.valid_verdicts)
    rather than importing that module's private helper.
    """
    if gate_id == fp.SHIP_GATE_ID:
        return fp.SHIP_VERDICTS
    return fs.valid_verdicts(gate_id)


def authorised_state(result):
    """True, False, or None meaning "omit the AUTHORISED line entirely".

    Required, not cosmetic. sprint_launch is in COWORK_FORBIDDEN and decide()
    stops it with reason "cowork", so under in-session relay the Cowork path and
    the authorised path BOTH print a /sprint line in DO. Two identical-looking
    outputs where one means "run this" and one means "do not run this here" is a
    legibility defect, and legibility outranks autonomy in this spec's parent.

    Three cases, in order:
      * this invocation executed a sprint_launch      -> authorised
      * the stop is ON a sprint_launch (cowork, wip_limit, relaunch_guard,
        spec_missing, malformed_launch, dry-run)      -> NOT authorised
      * the stop is a sprint_await carrying a line    -> authorised, because a
        sprint_await only ever carries a command when a launch record put it
        there, and that record is only written by execute_launch
    """
    if "sprint_launch" in result.executed:
        return True
    action = result.stopped.action
    if action.kind == "sprint_launch":
        return False
    if action.kind == "sprint_await" and action.command:
        return True
    return None


def render_stop(result: ChainResult, ref: str) -> str:
    """The presentation for one chain invocation, deliberately the same
    shape as foreman_status.render_next so the driver and the status surface
    read as one product. No field is ever the literal string "None": an
    absent value is substituted with something sensible, or the whole line
    is omitted where ANSWER/DO are conditional.
    """
    decision = result.stopped
    action = decision.action
    lines = [
        _line("FOREMAN", ref),
        _line("CHAIN", _chain_line_content(result.executed)),
        _line("STOPPED", decision.reason or "-"),
        _line("WHY", decision.detail or action.why or "-"),
        _line("NEXT", action.kind or "-"),
    ]
    state = authorised_state(result)
    if state is not None:
        lines.append(_line("AUTHORISED", "yes" if state else "no"))
    if action.gate_id:
        lines.append(_line("ANSWER", ", ".join(_gate_verdicts(action.gate_id))))
    if action.command:
        lines.append(_line("DO", action.command))
    return "\n".join(lines)


def guidance_block(result: ChainResult, flow: Optional[dict], operator: dict,
                   explicit: Optional[str]) -> str:
    """The guidance text to print under the banner, or "" for nothing.

    Two things are deliberate here. The key rendered is
    `result.stopped.action.guidance_key`, NOT `Decision.guidance_key`: the
    latter is "stop.<reason>" whenever the chain stopped, and no fragment is
    filed against a stop reason. And the operator's own stored `guide` value
    is folded in as the explicit argument when no flag was typed, which keeps
    resolve_guide_level's precedence exactly as the spec locks it while still
    letting operator.json hold a standing override.
    """
    action = result.stopped.action
    level = fp.resolve_guide_level(operator, explicit or operator.get("guide"))
    return rg.render(action.guidance_key, level, action.why, flow=flow)


# =============================================================================
# Thin host: default_driver() / load_driver() / write_driver() / set_pause() /
# clear_pause() / new_session_id(), plus execute() / run_chain() / main() in
# later build-order steps.
#
# This is the only section that does I/O or calls the clock. It never writes
# flow.json, never mutates a gate and never reimplements a transition: every
# state change goes through flow_transition.cmd_advance_guarded.
# =============================================================================

# flows/_lib/foreman_run.py -> flows/_lib -> flows -> the hub repo (or worktree)
# root: the same directory board_lib and render_board's out_root are built from.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

DRIVER_SCHEMA_VERSION = 1
DRIVER_FILENAME = "foreman.json"

# Derived at read time from the kill-switch file, never persisted: a stale
# `halt: false` on disk must not be able to override a real FOREMAN_HALT.
DERIVED_KEYS = ("halt",)

_DEFAULTS = (
    ("schema_version", DRIVER_SCHEMA_VERSION),
    ("guide", "coached"),
    ("rung", None),
    ("chain_depth", 0),
    ("chain_cap", fp.DEFAULT_CHAIN_CAP),
    ("session_id", ""),
    ("last_tick", ""),
    ("paused_reason", ""),
    ("preflight", None),
    ("budget_usd", None),
    ("launch", None),
    # Slice e: set once, the first time this flow's chain reaches the
    # "complete" stop, so a later tick over the same closed flow cannot count
    # the same completion a second time.
    ("completion_counted", False),
)


def default_driver(session_id: str = "", now: str = "") -> dict:
    driver = dict(_DEFAULTS)
    driver["session_id"] = session_id
    driver["last_tick"] = now
    return driver


def load_driver(flow_dir, halt: Optional[bool] = None) -> dict:
    """foreman_status.load_driver plus the schema-1 defaults.

    Reuses the read contract slice d already shipped rather than forking a
    second reader, then fills any missing key without overwriting a recorded
    one. A key another slice wrote (shipped, tail) survives untouched.
    """
    driver = fst.load_driver(flow_dir, halt=halt)
    for key, value in _DEFAULTS:
        driver.setdefault(key, value)
    return driver


# os.replace onto an existing destination is not fully collision-free between
# two concurrent writers on Windows even once the temp source names differ:
# two threads replacing the SAME destination at the same instant can still
# raise PermissionError (WinError 5), a transient sharing condition rather
# than a real failure. A short bounded retry absorbs it. At 8 threads / 320
# writes to one flow_dir, measurement showed roughly half of all writes
# needing at least one retry, with a worst observed case of 16 of 30 attempts
# before success: 30 is a measured margin over the observed worst case, not a
# generous one, and a heavier load could still exhaust it.
_REPLACE_RETRIES = 30
_REPLACE_RETRY_SLEEP_SECONDS = 0.001


def write_driver(flow_dir, driver: dict) -> pathlib.Path:
    """Write foreman.json atomically, stripping DERIVED_KEYS.

    os.replace on a sibling temp file, so a crash mid-write leaves the previous
    record intact rather than a truncated one. The record is advisory, but an
    advisory record that reads as `{` is worse than one that is absent.

    The temp file's name carries the pid and a random suffix (the same
    collision-avoidance foreman_lock.py's _create_exclusive gets for free from
    O_CREAT|O_EXCL on a fixed name): two concurrent writers to the same
    flow_dir must never contend for the same temp path, which is what raised
    under concurrency before this fix. The final os.replace onto the shared
    destination name is retried a bounded number of times on PermissionError,
    see _REPLACE_RETRIES above, to absorb the residual Windows sharing race
    between two replaces landing on the same destination in the same instant.
    If every retry is exhausted, the unique temp file is unlinked before the
    exception is re-raised: unlike the old fixed temp name (where a stale
    `.tmp` was harmlessly overwritten by the next writer), a unique-per-call
    name left behind on a raise would otherwise litter flow_dir forever,
    since nothing globs `*.tmp` today.
    """
    flow_dir = pathlib.Path(flow_dir)
    path = flow_dir / DRIVER_FILENAME
    to_write = {k: v for k, v in driver.items() if k not in DERIVED_KEYS}
    tmp_path = flow_dir / "{}.{}.{}.tmp".format(
        DRIVER_FILENAME, os.getpid(), secrets.token_hex(4))
    tmp_path.write_text(
        json.dumps(to_write, indent=2, sort_keys=True), encoding="utf-8")
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp_path, path)
            return path
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                tmp_path.unlink(missing_ok=True)
                raise
            time.sleep(_REPLACE_RETRY_SLEEP_SECONDS)
    return path  # pragma: no cover - unreachable, the loop always returns or raises


def set_pause(flow_dir, reason: str, now: Optional[str] = None) -> dict:
    """Load, record `reason` and the tick, write, and return the driver."""
    now = now or fs.iso_utc_now()
    driver = load_driver(flow_dir)
    driver["paused_reason"] = reason
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return driver


def clear_pause(flow_dir, now: Optional[str] = None) -> dict:
    """Load, empty the paused reason and record the tick, write, and return
    the driver."""
    now = now or fs.iso_utc_now()
    driver = load_driver(flow_dir)
    driver["paused_reason"] = ""
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return driver


def new_session_id(now: str) -> str:
    return "foreman-" + fs.compact_stamp(now) + "-" + secrets.token_hex(3)


# --- the operator record (slice e) -------------------------------------------

OPERATOR_FILENAME = "operator.json"

# One record for the operator, not one per flow: the rung is a property of the
# person, so it lives next to the modules rather than in any flow directory.
OPERATOR_PATH = pathlib.Path(__file__).resolve().parent / OPERATOR_FILENAME

OPERATOR_DEFAULTS = (
    ("completions", 0),   # flows this operator has driven to a complete stop
    ("guide", None),      # a standing --guide override, or null for the rung
)


def load_operator(path=None) -> dict:
    """The operator record, with defaults filled in. Never raises.

    Same read contract as load_driver: an absent or malformed file reads as
    the defaults rather than an error, because a lost completion count is a
    quieter failure than a driver that will not start.
    """
    path = pathlib.Path(path) if path else OPERATOR_PATH
    operator = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            operator = loaded
    except (OSError, ValueError):
        operator = {}
    for key, value in OPERATOR_DEFAULTS:
        operator.setdefault(key, value)
    return operator


def write_operator(operator: dict, path=None) -> pathlib.Path:
    """Write the operator record atomically, same os.replace discipline as
    write_driver: a crash mid-write leaves the previous count, not a truncated
    file."""
    path = pathlib.Path(path) if path else OPERATOR_PATH
    tmp_path = path.with_name("{}.{}.{}.tmp".format(
        path.name, os.getpid(), secrets.token_hex(4)))
    tmp_path.write_text(
        json.dumps(operator, indent=2, sort_keys=True), encoding="utf-8")
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp_path, path)
            return path
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                tmp_path.unlink(missing_ok=True)
                raise
            time.sleep(_REPLACE_RETRY_SLEEP_SECONDS)
    return path  # pragma: no cover - the loop always returns or raises


# --- the executor, the lock and the receipt ----------------------------------

RECEIPT_ACTOR = "foreman"
FENCE_ACTION = "driver_fence"

LAUNCH_PREFIX = "/sprint "
MODEL_MAP_FLAG = " --model-map "

# The one Windows work tree an authorised line may ever name. Taken from
# flow_transition rather than re-declared, so the two cannot drift: that module
# already owns this constant because emit_sprint_line has to keep naming a
# C:\work path even when the process that built the line runs on Linux.
_DEV_ROOT_PREFIX = ft.WINDOWS_DEV_ROOT + "\\"


class MalformedLaunchError(RuntimeError):
    """The line the driver was about to authorise failed one of the five
    mechanical shape checks in validate_launch_line, so nothing was authorised
    and nothing was written.

    Refuse-and-explain only, exactly like flow_transition.RelaunchGuardError and
    SpecMissingError: raising this neither launches, blocks nor recovers
    anything. Deliberately a RuntimeError and not a ValueError, so a caller can
    catch it separately from emit_sprint_line's model_hint ValueError backstop.
    """


class ShipExecuteError(RuntimeError):
    """The merge or the push exited non-zero, or the state needed to run them
    is not there.

    Caught by execute_once the same way RelaunchGuardError and SpecMissingError
    are, and surfaced as the existing stop reason "execute_error": a failed
    merge is a write path that raised, which is exactly what that reason
    already means. No new stop reason.
    """


def validate_launch_line(line, spec_path) -> str:
    """Five mechanical checks on the line about to be handed to a session.
    Returns "" when it is safe to authorise, else one sentence naming the
    offending value verbatim. No model, no network, no disk.

    Defence in depth rather than a live hole: flow.json is written only by the
    harness on one laptop and flow_state.validate already runs on every write.
    It is here because under in-session relay the authorised line is a string a
    session executes, and emit_sprint_line interpolates flow.json fields with no
    sanitisation at all.

    `spec_path` is the path the executor already resolved through
    flow_transition._resolve_spec_path, not a path re-parsed out of `line`:
    re-parsing would be a second derivation of the same fact and could disagree
    with the one the guards were run against.
    """
    line = line or ""
    spec_path = spec_path or ""
    if "\r" in line or "\n" in line:
        return ("the launch line carries a newline, so it is two commands and "
                "not one: {!r}".format(line))
    if not line.startswith(LAUNCH_PREFIX):
        return ("the launch line does not start with {!r}: {!r}".format(
            LAUNCH_PREFIX, line))
    if not spec_path.lower().endswith(".md"):
        return ("the resolved spec path is not a .md file: {!r}".format(spec_path))
    normalised_spec_path = os.path.normpath(spec_path)
    if not normalised_spec_path.lower().startswith(_DEV_ROOT_PREFIX.lower()):
        # Plain, not !r: repr() doubles each backslash in a Windows path,
        # which would hide the offending "D:\..." prefix from a substring
        # check on the verbatim value.
        #
        # Compares the NORMALISED path, not the raw string: a raw prefix
        # compare on spec_path is bypassable with a ".." segment that keeps
        # the literal "C:\work" prefix while resolving outside it, e.g.
        # "C:\work\..\Windows\System32\evil.md" (SEC-1, verifier finding).
        return ("the resolved spec path is not under {}: {}".format(
            ft.WINDOWS_DEV_ROOT, spec_path))
    if MODEL_MAP_FLAG not in line:
        return ("the launch line carries no {} segment: {!r}".format(
            MODEL_MAP_FLAG.strip(), line))
    segment = line.split(MODEL_MAP_FLAG, 1)[1].strip()
    try:
        # flow_transition already bootstrapped sprints\_lib onto sys.path and
        # bound model_map as `mm`. Going through it rather than importing
        # model_map here keeps test_lib_imports.py's contract intact (every
        # flows/_lib module must import with only flows/_lib on sys.path) without
        # duplicating that bootstrap.
        ft.mm.parse(segment)
    except ValueError as exc:
        return ("the --model-map segment does not parse: {!r} ({})".format(
            segment, exc))
    return ""


LAUNCH_RECEIPT_ACTION = "driver_sprint_launch"
SHIP_RECEIPT_ACTION = "driver_ship_execute"
SHIP_DETAIL_TEMPLATE = ("merge_sha={merge_sha} revert_point={revert_point} "
                        "branch={branch} target={target} pushed={pushed}")


def execute_launch(flow_dir, action, sprints_root=None, now=None, session_id=""):
    """Authorise one /sprint line. Returns (flow, authorised_line).

    NEVER runs the line, never spawns a process, never shells out. The /foreman
    shim runs it in-session; this function only says that running it is now the
    right thing to do, and records that it said so.

    In order:

    1. Re-run both emit-time guards against disk AS IT IS NOW, not as it was at
       G2-consume time. cmd_advance persists state and writes the
       `sprint_emitted` log entry BEFORE cmd_advance_guarded runs its guards, so
       a refused advance still leaves a runnable-looking line in
       flow-log.ndjson, and nothing else would stop a driver from running it.
       - a terminal sprint for this spec raises RelaunchGuardError;
       - a resolved spec path that does not exist raises SpecMissingError;
       - a None from local_spec_path means this host CANNOT SEE the disk, which
         is undecidable rather than missing, so the guard abstains rather than
         raising, per that function's own docstring.
       Both raises go through emit_sprint_line so the messages stay the exact
       already-tested ones, the same seam cmd_advance_guarded uses.
    2. Validate the shape of the line before handing it to a session.
    3. Write the advisory launch record to foreman.json. execute_once appends
       the receipt immediately afterwards, under the same lock, so the record
       is always written before the receipt.
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    project = flow.get("project", "")
    # The single derivation of this path, shared with emit_sprint_line. Its
    # docstring sanctions exactly this host use and forbids duplicating it.
    spec_path = ft._resolve_spec_path(flow)

    if sprints_root is None:
        sprints_root = ts.DEFAULT_SPRINTS_ROOT
    match = ts.has_terminal_sprint(project, spec_path, ts.discover_sprints(sprints_root))
    if match:
        ft.emit_sprint_line(flow, existing_terminal=match)   # raises RelaunchGuardError

    local = ft.local_spec_path(spec_path)
    if local is not None and not pathlib.Path(local).exists():
        ft.emit_sprint_line(flow, spec_missing=True)         # raises SpecMissingError

    problem = validate_launch_line(action.command, spec_path)
    if problem:
        raise MalformedLaunchError(problem)

    driver = load_driver(flow_dir)
    driver["launch"] = {
        "line": action.command,
        "spec_path": spec_path,
        "ts": now,
        "session_id": session_id,
    }
    write_driver(flow_dir, driver)
    return flow, action.command


PREFLIGHT_RECEIPT_ACTION = "driver_ship_preflight"


def resolve_sprint_for(flow_dir, flow, sprints_root=None):
    """The sprint.json this flow's current step launched, or None.

    The same three calls run_chain already makes (read_log, discover_sprints,
    resolve_sprint), gathered here because the two ship executors need the
    sprint's worktree and its recorded test/verify results, and execute() is
    handed only the flow_dir and the action.
    """
    sprints = ts.discover_sprints(sprints_root or ts.DEFAULT_SPRINTS_ROOT)
    return fst.resolve_sprint(flow, read_log(flow_dir), sprints)


def execute_preflight(flow_dir, action, sprints_root=None, now=None) -> dict:
    """Run the nine checks and record them on the driver. Returns the dict.

    Never judges: a check that failed is written down, the gate still opens,
    and the operator decides. The only failure mode that raises is not knowing
    WHICH repository to check, because every check would then be an answer
    about the wrong tree.
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    sprint = resolve_sprint_for(flow_dir, flow, sprints_root)
    repo_root = sp.repo_root_for(flow, sprint, dev_root=str(roots.dev_root()))
    if not repo_root:
        raise ValueError(
            "cannot run the ship checks: no repo root resolves for {}/{}; the "
            "sprint records no worktree_path and no project directory was "
            "found under the dev root".format(
                flow.get("project", "?"), flow.get("slug", "?")))
    driver = load_driver(flow_dir)
    budget = driver.get("diff_budget_lines")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        budget = sp.DEFAULT_DIFF_BUDGET_LINES
    results = sp.run_preflight(flow, sprint, repo_root,
                               diff_budget_lines=budget)
    driver["preflight"] = results
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return results


def _preflight_ship_action(flow):
    """The NextAction shape execute_preflight's receipt needs when
    execute_ship finds no preflight recorded.

    That shape arises whenever the terminal sweep opened this G4: apply_match
    opens the gate through flow_transition.cmd_advance directly and never
    runs the checks, so a `ship` decision on that gate would otherwise
    dead-end at ShipExecuteError forever (the reopen path, cmd_ship, needs
    driver["held"], which a sweep-opened gate never sets either). Fix for a
    2026-08-17 review finding.
    """
    return fp.NextAction(
        kind="ship_preflight",
        why="G4 was answered ship but no pre-flight check dict was recorded "
            "yet, so ship_execute runs the checks itself before merging on "
            "{}/{}".format(flow.get("project", "?"), flow.get("slug", "?")),
        blocking="", command="", gate_id=None,
        step_index=(flow.get("current_step") or {}).get("index", 0),
        role="ship_preflight", guidance_key="ship_preflight")


def execute_ship(flow_dir, action, sprints_root=None, now=None, push=True):
    """Merge the sprint branch with --no-ff and push. Returns the sha record.

    Runs in the flow's repo root, which is the only place a merge into the
    target branch means anything. Both git calls happen inside the flow lock
    execute_once already holds. `push` is True in production and forced False
    only by the ship drill, which has no remote to push to.

    Runs the pre-flight checks itself, through execute_preflight, when the
    driver records none yet (see _preflight_ship_action above), rather than
    refusing outright: the spec's success criterion 4 is that no branch is
    ever merged without a recorded verdict, a populated check dict AND a
    pre-merge sha, and computing that dict here (instead of only reusing one
    computed earlier) still satisfies it.

    Any non-zero exit raises ShipExecuteError. A merge that fails leaves
    driver["shipped"] unset, so a re-run of the driver comes back to the same
    ship_execute action rather than skipping past it.

    Refuses outright, before touching git or the driver, when
    ZND_EXTERNAL_DEMO=1 is set: the demo fence
    (02_roadmap/2026-08-24_foreman_guided_lane_program.md section 3). The
    raise is caught by execute_once's generic handler and lands as an
    "execute_error" stop, so the refusal is a driver_fence receipt, not a
    stack trace.
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    if os.environ.get("ZND_EXTERNAL_DEMO") == "1":
        raise ShipExecuteError(
            "refusing to ship {}/{}: ZND_EXTERNAL_DEMO fence is set".format(
                flow.get("project", "?"), flow.get("slug", "?")))
    driver = load_driver(flow_dir)
    preflight = driver.get("preflight")
    if not isinstance(preflight, dict) or not preflight:
        try:
            preflight = execute_preflight(flow_dir, _preflight_ship_action(flow),
                                          sprints_root=sprints_root, now=now)
        except ValueError as exc:
            raise ShipExecuteError(str(exc)) from exc
        driver = load_driver(flow_dir)

    branch = preflight.get("worktree_branch") or sp.expected_branch(flow)
    sprint = resolve_sprint_for(flow_dir, flow, sprints_root)
    repo_root = sp.repo_root_for(flow, sprint, dev_root=str(roots.dev_root()))
    if not repo_root:
        raise ShipExecuteError(
            "refusing to merge {}: no repo root resolves for {}/{}".format(
                branch, flow.get("project", "?"), flow.get("slug", "?")))

    code, target = sp.git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    target = target.strip()
    if code != 0 or not target:
        raise ShipExecuteError(
            "cannot read the checked-out branch of {}, so there is nothing "
            "safe to merge into".format(repo_root))

    revert_point = str(preflight.get("revert_point") or "")
    if not revert_point:
        raise ShipExecuteError(
            "refusing to merge {}/{}: the driver's pre-flight check dict "
            "has no revert_point recorded, so there is no pre-merge sha to "
            "revert to".format(flow.get("project", "?"), flow.get("slug", "?")))

    # 2026-08-17 review finding: the preflight-time revert_point above can be
    # stale by the time this merge actually runs, since the G4 decision may
    # arrive long after the checks did and the target branch can move in
    # between. Re-read the target's tip right before merging and record THAT
    # sha instead: the guard above only needs to know preflight found some
    # revert point at all, not which value survives to the receipt. Falling
    # back to the preflight-time value keeps this merge from refusing on a
    # read that happens to fail here.
    code, fresh_revert_point = sp.git(["rev-parse", target], repo_root)
    fresh_revert_point = fresh_revert_point.strip()
    if code == 0 and fresh_revert_point:
        revert_point = fresh_revert_point

    code, out = sp.git(["merge", "--no-ff", branch], repo_root)
    if code != 0:
        raise ShipExecuteError(
            "git merge --no-ff {} in {} exited {}: {}".format(
                branch, repo_root, code, out.strip()[:200]))

    code, merge_sha = sp.git(["rev-parse", "HEAD"], repo_root)
    merge_sha = merge_sha.strip()
    if code != 0 or not merge_sha:
        raise ShipExecuteError(
            "the merge reported success but its sha could not be read in "
            "{}, so nothing revertible was recorded".format(repo_root))

    if push:
        code, out = sp.git(["push"], repo_root)
        if code != 0:
            raise ShipExecuteError(
                "git push in {} exited {} AFTER a successful local merge {}: "
                "{}".format(repo_root, code, merge_sha, out.strip()[:200]))

    info = {
        "merge_sha": merge_sha,
        "revert_point": revert_point,
        "branch": branch,
        "target": target,
        "pushed": bool(push),
    }
    driver["shipped"] = True
    driver["ship"] = dict(info, ts=now, repo_root=repo_root)
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return info


def execute_recover(flow_dir, action, sprints_root=None, now=None) -> dict:
    """Cross the needs-guidance sprint boundary, through the sweep's own path.

    A thin host, not a second implementation: terminal_sweep.apply_match
    already crosses this exact boundary, attaches the sweep_context the gate
    notification body reads, and queues the outbox entry. A bare cmd_advance
    here would strip both. `lock=False` because execute_once already holds
    this flow's lock and foreman_lock.acquire is not re-entrant.

    Raises ShipExecuteError (surfaced by execute_once as the existing
    "execute_error" stop reason) in the two cases where crossing would be
    wrong rather than merely unlucky:

      * no sprint resolves, so there is no status to build a sweep_context
        from and the gate would open blind;
      * apply_match reports a skip, meaning the flow is no longer
        sweep-eligible (something advanced it in between). Reporting that as a
        crossing would leave next_action returning sprint_recover forever and
        the chain spinning to its cap.
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    ref = "{}/{}".format(flow.get("project", "?"), flow.get("slug", "?"))
    sprint = resolve_sprint_for(flow_dir, flow, sprints_root)
    if not sprint:
        raise ShipExecuteError(
            "refusing to recover {}: no sprint.json resolves for this step, so "
            "there is no terminal status to attach as sweep context".format(ref))
    result = ts.apply_match(flow_dir, sprint, now=now, lock=False)
    if result.get("skipped"):
        raise ShipExecuteError(
            "refusing to recover {}: the flow is no longer sweep-eligible "
            "(reason {}), so nothing was crossed".format(
                ref, result.get("reason") or "not_eligible"))
    return result


BRIEF_OWNER = "Wilco de Tree"
STEP_BOUNDARY_ACTIONS = ("gate_decided", "sprint_swept")


def step_brief_paths(flow: dict, log: list, assets_dir) -> list:
    """The `related` list for a closed-flow brief: one entry per step this
    flow got through, citing that step's own learn-phase brief where one is on
    disk and naming the step by index where none is.

    ft._log_entry records ts/stage/actor/action/outcome/detail/ref and NO step
    index, so the index is derived by ordinal: the Nth `sprint_swept` receipt
    belongs to step N. A flow that closed with no sprint_swept receipt at all
    still gets step 1 examined, so the list is never empty. The date in a step
    brief's filename is the date THAT step closed, which this function does
    not know, hence the glob rather than a constructed name.
    """
    project = (flow or {}).get("project", "")
    slug = (flow or {}).get("slug", "")
    log = log or []
    boundaries = [e for e in log
                  if e.get("action") in STEP_BOUNDARY_ACTIONS]
    swept = sum(1 for e in log if e.get("action") == "sprint_swept")
    assets_dir = pathlib.Path(assets_dir)
    related = []
    for index in range(1, max(1, swept) + 1):
        pattern = "hub_agent_update_*_{}_{}_step{}.md".format(
            project, slug, index)
        found = sorted(str(p) for p in assets_dir.glob(pattern))
        related.extend(found or [
            "step {} of {}/{}: no step brief on disk".format(
                index, project, slug)])
    related.append(
        "{} step-boundary receipt(s) in this flow's flow-log.ndjson".format(
            len(boundaries)))
    return related


def execute_brief_write(flow_dir, action, now=None, dev_root=None) -> str:
    """Compose and write the flow's closing retrospective brief. Returns the
    path written.

    Summarises the CLOSURE OF THE FLOW, not any one sprint, which is why
    compose_brief is called with empty sprint_report and action_log strings
    and empty learnings: there is no single sprint report to fold in at this
    point, and inventing one would put unsourced prose in a durable file.

    `dev_root` is injectable (the same house pattern as
    flow_transition.local_spec_path) so a test never has to write into the
    real C:\\work tree. execute() never passes it.
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    project = flow.get("project", "")
    slug = flow.get("slug", "")
    date = now[:10]
    if dev_root is None:
        dev_root = str(roots.dev_root())
    assets_dir = pathlib.Path(dev_root) / project / "04_assets"
    path = assets_dir / "hub_agent_update_{}_{}_closed.md".format(date, slug)

    meta = {
        "date": date,
        "owner": BRIEF_OWNER,
        "project": project,
        "purpose": "record that flow {}/{} closed, for the hub to "
                   "absorb".format(project, slug),
        "read_order": ["this file"],
        "supersedes": "nothing",
        "headline": "Flow {}/{} closed at {}. Every step reached a decision "
                    "and the flow is terminal; this brief is the driver's own "
                    "record of the closure, composed from the flow's "
                    "receipts.".format(project, slug, now),
        "related": step_brief_paths(flow, read_log(flow_dir), assets_dir),
        # Only the legacy ZND hub layout has a portfolio.md/STATUS.md to
        # point at; a packaged install without that hub gets an empty list
        # here rather than an invented customer-side path (roots.py T04).
        "next_updates": roots.status_doc_paths(pathlib.Path(dev_root)),
    }
    brief = bc.compose_brief("", "", {}, meta)
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(brief, encoding="utf-8")
    except OSError as exc:
        raise ShipExecuteError(
            "could not write the closing brief for {}/{} to {}: {}".format(
                project, slug, path, exc)) from exc

    driver = load_driver(flow_dir)
    tail = dict(driver.get("tail") or {})
    tail["brief_written"] = True
    tail["brief_path"] = str(path)
    driver["tail"] = tail
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return str(path)


HUB_UPDATE_LINE = "/hub-update"


def execute_hub_ingest(flow_dir, action, now=None) -> str:
    """Build the exact /hub-update line and record that it was authorised.
    Returns the line.

    NEVER runs it. No subprocess, no spawned Claude Code session, no subagent,
    no network, no model call: the same "authorise, never execute a model
    call" contract flow_transition.emit_sprint_line already holds for a
    /sprint line. The skill is bare-argument (C:\\work\\50_projects\\znd-skills\\skills\\
    hub-update\\SKILL.md): it scans every project's 04_assets folder itself,
    so the line carries no path. The brief this ingest is FOR is named in the
    receipt instead, which is where the audit trail belongs.

    Raises ShipExecuteError when the driver records no brief path: ingesting
    before a brief exists would propagate nothing and then mark this tail step
    done, closing the flow with its own retrospective lost.
    """
    now = now or fs.iso_utc_now()
    driver = load_driver(flow_dir)
    tail = dict(driver.get("tail") or {})
    if not tail.get("brief_written") or not tail.get("brief_path"):
        flow = ft.load_flow(flow_dir)
        raise ShipExecuteError(
            "refusing to authorise {} for {}/{}: the driver records no written "
            "brief, so there is nothing for the hub to ingest".format(
                HUB_UPDATE_LINE, flow.get("project", "?"),
                flow.get("slug", "?")))
    tail["hub_ingested"] = True
    driver["tail"] = tail
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return HUB_UPDATE_LINE


# Deliberately not sp.GIT_TIMEOUT_SECONDS: assemble.py and render_mission_deck.py
# do more work than a single git call, and assemble.py can make live HTTP calls
# to FRIDAY agents when configured, so this ceiling is generous but bounded.
REFRESH_TIMEOUT_SECONDS = 300
COMMAND_CENTER_PARTS = ("04_engineering", "command_center")
# assemble.py rebuilds the read model, render_mission_deck.py renders the deck
# from it, in that order. notify.py and refresh_and_notify.cmd are DELIBERATELY
# absent: the sweep already posts to Slack and running them here would double
# post (parent design spec line 366).
REFRESH_STEPS = (("assemble.py", ["--force"]),
                 ("render_mission_deck.py", []))


def run_refresh_script(script: str, args: list, cwd):
    """Run one command-center script and return (exit_code, output). Never
    raises. The single subprocess runner for the refresh, shaped exactly like
    ship_preflight.git so a test can monkeypatch one name and cover both
    scripts. utf-8 with errors="replace" is the existing Windows-locale fix.
    """
    try:
        proc = subprocess.run([sys.executable, script] + list(args),
                              cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=REFRESH_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    if proc.returncode == 0:
        return proc.returncode, proc.stdout or ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def execute_hub_refresh(flow_dir, action, now=None, hub_root=None) -> dict:
    """Rebuild the read model and re-render the deck. Returns {script: code}.

    Both scripts run with cwd C:\\work\\10_holding\\04_engineering\\
    command_center, which is the only directory their own relative paths
    resolve against. Deferred to a subprocess rather than imported: those
    modules are not on flows/_lib's sys.path and importing them would break
    test_lib_imports.py's contract.

    A non-zero exit from EITHER script raises ShipExecuteError and leaves
    driver["tail"] exactly as it was, so the same hub_refresh action comes
    back on the next chain run. A first-script failure short-circuits: there
    is no point rendering a deck from a read model that did not rebuild.

    `hub_root` is injectable so a test never shells into the real checkout.
    """
    now = now or fs.iso_utc_now()
    driver = load_driver(flow_dir)
    tail = dict(driver.get("tail") or {})
    if not tail.get("hub_ingested"):
        flow = ft.load_flow(flow_dir)
        ref = "{}/{}".format(flow.get("project", "?"), flow.get("slug", "?"))
        raise ShipExecuteError(
            "refusing to refresh {}: the driver records no hub_ingest, so "
            "refreshing would run ahead of it".format(ref))
    if hub_root is None:
        hub_root = str(roots.hub_root())
    cwd = pathlib.Path(hub_root).joinpath(*COMMAND_CENTER_PARTS)
    codes = {}
    for script, args in REFRESH_STEPS:
        code, out = run_refresh_script(script, args, cwd)
        codes[script] = code
        if code != 0:
            raise ShipExecuteError(
                "{} {} in {} exited {}: {}".format(
                    script, " ".join(args), cwd, code, out.strip()[:200]))

    tail["hub_refreshed"] = True
    driver["tail"] = tail
    driver["last_tick"] = now
    write_driver(flow_dir, driver)
    return codes


class ShipReopenError(RuntimeError):
    """`foreman_run.py ship` cannot reopen G4, with a named reason."""


UNSHIP_NOTHING = ("no ship_execute receipt in {ref}'s flow-log.ndjson, so this "
                  "flow's branch was never merged by the driver and there is "
                  "nothing to run")
UNSHIP_TEMPLATE = """\
{ref} was shipped: branch {branch} merged into {target} as {merge_sha}.
Pre-merge sha (the revert point) was {revert_point}.

To undo it, PowerShell on the laptop, from {repo_root}:

    git revert -m 1 {merge_sha}

That is printed, never run. Pushing the revert is a separate deliberate step:

    git push
"""

_MERGE_SHA_RE = re.compile(r"merge_sha=([0-9a-fA-F]{7,40})")
_SHIP_FIELD_RE = re.compile(r"(\w+)=(\S+)")


def _preflight_reopen_action(flow):
    """The NextAction shape execute_preflight's receipt needs, built here
    because a reopen is operator-initiated and never came off the ladder."""
    return fp.NextAction(
        kind="ship_preflight",
        why="G4 is being reopened after a hold on {}/{}, so the checks re-run "
            "against disk as it is now".format(
                flow.get("project", "?"), flow.get("slug", "?")),
        blocking="", command="", gate_id=None,
        step_index=(flow.get("current_step") or {}).get("index", 0),
        role="ship_preflight", guidance_key="ship_preflight")


def cmd_ship(flow_dir, sprints_root=None, now=None) -> dict:
    """Reopen G4 on a held flow: re-check, then open a fresh gate.

    Valid only when driver["held"] is set, because that flag is the only record
    that a branch was deliberately left unmerged. The checks are re-run rather
    than reused: state may have drifted since the hold, which is the whole
    reason a hold is reversible instead of final.

    Fails loudly, with a named reason, when the branch is gone, when no repo
    root resolves, or when another gate is already awaiting a decision
    (reopening would clobber that gate and its token).
    """
    now = now or fs.iso_utc_now()
    flow = ft.load_flow(flow_dir)
    ref = "{}/{}".format(flow.get("project", "?"), flow.get("slug", "?"))
    driver = load_driver(flow_dir)
    if not driver.get("held"):
        raise ShipReopenError(
            "{}: no hold is pending (the driver records no 'held'), so there "
            "is no G4 to reopen; run the driver instead".format(ref))

    gate = flow.get("gate")
    if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
        raise ShipReopenError(
            "{}: gate {} is already awaiting a decision; answer or park it "
            "before reopening G4".format(ref, gate.get("id")))

    sprint = resolve_sprint_for(flow_dir, flow, sprints_root)
    repo_root = sp.repo_root_for(flow, sprint, dev_root=str(roots.dev_root()))
    branch = ((driver.get("preflight") or {}).get("worktree_branch")
              or sp.expected_branch(flow))
    if not repo_root:
        raise ShipReopenError(
            "{}: no repo root resolves, so branch {} cannot be "
            "re-checked".format(ref, branch))
    code, _out = sp.git(["rev-parse", "--verify", branch], repo_root)
    if code != 0:
        raise ShipReopenError(
            "{}: branch {} no longer exists in {}, so there is nothing to "
            "ship".format(ref, branch, repo_root))

    execute_preflight(flow_dir, _preflight_reopen_action(flow),
                      sprints_root=sprints_root, now=now)
    driver = load_driver(flow_dir)
    driver["held"] = None
    driver["last_tick"] = now
    write_driver(flow_dir, driver)

    flow = ft.load_flow(flow_dir)
    step_index = (flow.get("current_step") or {}).get("index", 0)
    flow = ft.persist_open_gate(flow_dir, flow, "G4", step_index, now)
    ft.append_log(flow_dir, ft._log_entry(
        now, flow.get("stage", ""), RECEIPT_ACTOR, "driver_ship_reopen",
        outcome="pass", detail="G4 reopened on {} after a hold".format(branch),
        ref=flow["gate"]["token"]))
    return flow


def cmd_unship(flow_dir) -> str:
    """Return the text that undoes the last recorded merge. Read-only.

    Never executes anything, never shells out, never writes. Finds the most
    recent driver_ship_execute receipt, reads the merge sha back out of its
    detail, and formats one `git revert -m 1 <sha>` line with the branch, the
    target and the directory it runs in spelled out (the repo's rule that every
    command snippet states where it runs). Prints nothing to run when no such
    receipt exists.
    """
    flow = ft.load_flow(flow_dir)
    ref = "{}/{}".format(flow.get("project", "?"), flow.get("slug", "?"))
    receipts = [e for e in read_log(flow_dir)
                if e.get("action") == SHIP_RECEIPT_ACTION]
    for entry in reversed(receipts):
        detail = entry.get("detail") or ""
        match = _MERGE_SHA_RE.search(detail)
        if not match:
            continue
        fields = dict(_SHIP_FIELD_RE.findall(detail))
        driver = load_driver(flow_dir)
        return UNSHIP_TEMPLATE.format(
            ref=ref,
            merge_sha=match.group(1),
            revert_point=fields.get("revert_point", "unrecorded"),
            branch=fields.get("branch", sp.expected_branch(flow)),
            target=fields.get("target", "the target branch"),
            repo_root=(driver.get("ship") or {}).get("repo_root")
                      or "the flow's repo root")
    return UNSHIP_NOTHING.format(ref=ref)


def execute(flow_dir, action, sprints_root=None, now=None, session_id="",
            push=True):
    """Cross the boundary `action` names, through the single write path.

    Eight explicit action kinds now, plus a defensive fallthrough.
    flow_advance and gate_consume are "cross the next boundary", which is
    cmd_advance_guarded's job, and the G4 hold verdict is why that branch now
    loads and writes the driver: flow_transition mutates the dict it is
    handed and deliberately does no foreman.json I/O of its own.
    sprint_launch authorises a line. ship_preflight annotates. ship_execute is
    the only branch that runs git. sprint_recover crosses through
    terminal_sweep.apply_match instead of cmd_advance_guarded. brief_write,
    hub_ingest and hub_refresh close the hub tail: composing the closing
    brief, authorising the hub-update line, and refreshing the boards.
    """
    if action.kind == "sprint_launch":
        return execute_launch(flow_dir, action, sprints_root=sprints_root,
                              now=now, session_id=session_id)
    if action.kind == "ship_preflight":
        return execute_preflight(flow_dir, action, sprints_root=sprints_root,
                                 now=now)
    if action.kind == "ship_execute":
        return execute_ship(flow_dir, action, sprints_root=sprints_root,
                            now=now, push=push)
    if action.kind == "sprint_recover":
        return execute_recover(flow_dir, action, sprints_root=sprints_root,
                               now=now)
    if action.kind == "brief_write":
        return execute_brief_write(flow_dir, action, now=now)
    if action.kind == "hub_ingest":
        return execute_hub_ingest(flow_dir, action, now=now)
    if action.kind == "hub_refresh":
        return execute_hub_refresh(flow_dir, action, now=now)
    if action.kind not in EXECUTABLE_KINDS:
        raise ValueError("foreman_run.execute refuses kind {!r}".format(action.kind))
    driver = load_driver(flow_dir)
    before = json.dumps(driver, sort_keys=True, default=str)
    result = ft.cmd_advance_guarded(flow_dir, sprints_root=sprints_root,
                                    now=now, driver=driver)
    if json.dumps(driver, sort_keys=True, default=str) != before:
        write_driver(flow_dir, driver)
    return result


def receipt_detail(action, result) -> Optional[str]:
    """The detail an executed ship action's receipt carries, or None to keep
    record_receipt's own default. Pure.

    The two ship kinds are the only ones whose receipt has to be auditable
    against something other than the action's own `why`: which shas were
    involved, and what the checks found. `secrets_found` is excluded on
    purpose: the receipt is durable and a redacted secret is still a shape
    worth not writing down twice.
    """
    if action.kind == "ship_execute" and isinstance(result, dict):
        return SHIP_DETAIL_TEMPLATE.format(**result)
    if action.kind == "ship_preflight" and isinstance(result, dict):
        return "preflight " + " ".join(
            "{}={}".format(k, result[k]) for k in sorted(result)
            if k != "secrets_found")
    if action.kind in ("brief_write", "hub_ingest") and isinstance(result, str):
        return result
    if action.kind == "hub_refresh" and isinstance(result, dict):
        return "refreshed " + " ".join(
            "{}={}".format(k, result[k]) for k in sorted(result))
    return None


def record_receipt(flow_dir, action, stage, now, detail=None) -> None:
    """Append the driver's reasoning receipt. `stage` is the PRE-execute stage
    (the state `action.why` describes); cmd_advance already logged the
    post-state one line earlier.

    Two exceptions to "detail is the why": a sprint_launch receipt carries the
    exact authorised line, because that receipt is the record of WHAT was
    authorised and a paraphrase of why is not auditable; and an explicit
    `detail` (the ship kinds, via receipt_detail) overrides both. `detail` of
    None is today's behaviour exactly, so no existing call site changes.
    """
    if detail is None:
        detail = action.command if action.kind == "sprint_launch" else action.why
    ft.append_log(flow_dir, ft._log_entry(
        now, stage, RECEIPT_ACTOR, "driver_" + action.kind,
        outcome="pass", detail=detail, ref=action.gate_id))


def _last_log_entry(flow_dir) -> Optional[dict]:
    """The last parseable line of flow-log.ndjson, or None.

    A local copy of foreman_status._log_for's tolerant read logic (skip a
    line that will not parse, never raise), rather than reaching into that
    module's underscore-prefixed helper: the same house rule
    foreman_status._gate_verdicts already documents for terminal_sweep's
    private read logic. record_fence only ever needs the last line.
    """
    log_path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    last = None
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            last = json.loads(raw)
        except json.JSONDecodeError:
            continue
    return last


def record_fence(flow_dir, decision, stage, now) -> bool:
    """Append a `driver_fence` receipt for a stopped `decision`, unless the
    immediately preceding log line is already that exact fence.

    Dedupes so that re-running the driver against a fence that has not
    changed (the same reason, the same detail) does not spam the log one
    line per invocation.

    The four halt-family fences (chain_cap, the FOREMAN_HALT kill switch, a
    per-flow pause, flow_paused) all arrive here as reason == "blocking" with
    the SAME generic detail text, so the dedupe key folds in
    decision.action.guidance_key (already distinct per fence: halt.chain_cap,
    halt.halt_file, halt.paused, halt.flow_paused) for that one reason. The
    other stop reasons (cowork, wip_limit, ...) already carry distinct detail
    text and keep their original two-part key.
    """
    if decision.reason == "blocking":
        detail = "{}: {}: {}".format(
            decision.reason, decision.action.guidance_key, decision.detail)
    else:
        detail = "{}: {}".format(decision.reason, decision.detail)
    last = _last_log_entry(flow_dir)
    if (isinstance(last, dict) and last.get("action") == FENCE_ACTION
            and last.get("detail") == detail[:240]):
        return False
    ft.append_log(flow_dir, ft._log_entry(
        now, stage, RECEIPT_ACTOR, FENCE_ACTION, outcome="blocked", detail=detail))
    return True


def execute_once(flow_dir, action, session_id, sprints_root=None, now=None,
                 stage="", push=True) -> Decision:
    """The locked execute-plus-receipt unit: take the flow lock, cross the
    boundary through `execute`, append the reasoning receipt, release the
    lock in a finally (via foreman_lock.acquire's context manager), whatever
    happens inside.

    `push` is threaded to execute() for ship_execute only; every other kind
    ignores it.

    Returns a stop `Decision` (never raises) when the lock is held elsewhere,
    when the relaunch guard refuses, or when the write path raises for any
    other reason; else `Decision(action, execute=True, reason="", detail="")`.
    """
    now = now or fs.iso_utc_now()
    try:
        with fl.acquire(flow_dir, session_id, now=now, stage=stage):
            result = execute(flow_dir, action, sprints_root=sprints_root,
                             now=now, session_id=session_id, push=push)
            record_receipt(flow_dir, action, stage, now,
                           detail=receipt_detail(action, result))
    except fl.LockBusy:
        return Decision(action, False, "lock_busy", _STOP_DETAIL["lock_busy"])
    except ft.RelaunchGuardError as exc:
        return Decision(action, False, "relaunch_guard",
                        _STOP_DETAIL["relaunch_guard"].format(detail=str(exc)))
    except ft.SpecMissingError as exc:
        return Decision(action, False, "spec_missing",
                        _STOP_DETAIL["spec_missing"].format(detail=str(exc)))
    except MalformedLaunchError as exc:
        return Decision(action, False, "malformed_launch",
                        _STOP_DETAIL["malformed_launch"].format(detail=str(exc)))
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        return Decision(action, False, "execute_error",
                        _STOP_DETAIL["execute_error"].format(detail=str(exc)))
    return Decision(action, True, "", "")


# --- the chain ----------------------------------------------------------------

def read_log(flow_dir) -> list:
    """Read flow-log.ndjson tolerantly: skip a line that will not parse, never
    raise. A local copy of foreman_status._log_for's read logic (the same
    house rule _last_log_entry above already follows for its own tail read,
    rather than reaching into that module's private helper)."""
    log_path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    entries = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def gate_verdict(flow) -> str:
    """The verdict recorded on flow's gate, or "" when there is no decided
    gate to feed into `decide` (an open, undecided gate carries no verdict
    either, and decide never looks at this argument for that case)."""
    gate = (flow or {}).get("gate")
    if not isinstance(gate, dict):
        return ""
    return ((gate.get("decision") or {}).get("verdict")) or ""


def step_of(flow) -> Optional[int]:
    """`current_step.of` when the flow records an integer one, else None.

    None means "this flow does not say how many steps it has", which is not
    evidence of a multi-step flow and therefore never trips the needs_spec
    stop. Same shape as gate_verdict above: a flow-derived scalar the pure
    ladder is handed rather than digging out itself.
    """
    cs = (flow or {}).get("current_step") or {}
    of = cs.get("of")
    return of if isinstance(of, int) else None


def flow_spec_path(flow) -> str:
    """The flow's recorded spec_path, or "" when it carries none.

    Deliberately NOT flow_transition._resolve_spec_path: the question this
    answers is "did anyone record a per-step spec", and that helper's whole job
    is to substitute the conventional fallback when nobody did, which would
    make the needs_spec check always pass.
    """
    return (flow or {}).get("spec_path") or ""


def host_facts(flow, sprints, driver, burnrate_db=None, sprint=None) -> HostFacts:
    """Assemble the facts `decide` is forbidden to compute itself, all
    injected: `cowork` from the repo root path, `wip_count` from the sprints
    the caller already discovered this pass, and `cost_usd` read from
    BurnRate through foreman_cost.sprint_cost_usd, but only when a budget
    ceiling is actually set (interpretation decision 9): without a ceiling
    there is nothing the number could do, so the store is never read. A
    None cost never trips the budget fence regardless of budget_usd.
    """
    driver = driver or {}
    project = (flow or {}).get("project", "")
    budget = driver.get("budget_usd")
    cost = None
    if budget is not None:
        # Only now is the BurnRate store worth a read: see interpretation
        # decision 9. Without a ceiling there is nothing the number could do.
        cost = fcost.sprint_cost_usd(sprint, burnrate_db)
    return HostFacts(
        cowork=is_cowork_mount(str(_REPO_ROOT), os.name, os.environ),
        wip_count=wip_count(project, sprints or []),
        wip_limit=DEFAULT_WIP_LIMIT,
        cost_usd=cost,
        budget_usd=budget,
    )


def render_after(flow_dir, flows_root) -> None:
    """Re-render this flow's dashboard and the flows index.

    run_chain wraps every call to this in try/except Exception: pass; a stale
    dashboard is never worth losing a chain's already-written receipts over.
    """
    render_flows.render_flow(flow_dir)
    render_flows.render_index(flows_root)


def render_board(flows_root, sprints_root, out_root=None) -> None:
    """Rebuild C:\\work\\10_holding\\foreman\\index.html once per chain.

    `out_root` defaults to `_REPO_ROOT / "foreman"` (the real board) when not
    given, matching the pattern render_foreman.render_board itself already
    uses; a caller (a test, or a future sandboxed invocation) can pass a
    tmp_path instead so this never has to touch the real checkout.

    Deferred import: foreman/_lib is a third flat sibling directory, and a
    top-level import here would break test_lib_imports.py's contract that
    every flows/_lib module imports with only flows/_lib on sys.path. Swallows
    every exception: the receipts are already durable, and a stale board is
    never worth losing a chain over.
    """
    if out_root is None:
        out_root = str(_REPO_ROOT / "foreman")
    try:
        board_lib = _REPO_ROOT / "foreman" / "_lib"
        if str(board_lib) not in sys.path:
            sys.path.insert(0, str(board_lib))
        import render_foreman
        render_foreman.render_board(flows_root, sprints_root, out_root)
    except Exception:
        pass


# The stop reasons that get a driver_fence receipt at the pre-execute stop
# point in run_chain: budget/cowork/wip_limit/needs_steps are genuine host
# facts refusing an otherwise-executable action, and a kill-switch, a pause or
# the chain cap (decision.action.blocking == "fence") is the same kind of
# fence. "complete" (nothing left to do), "needs_judgment" and
# "not_executable" (the next action is simply outside this build-order step's
# authority yet, or awaits a human opening a gate) are structural rather than
# fences: recomputing next_action already tells that story on every call, so
# there is nothing to dedupe and nothing worth a receipt for. A stop out of
# execute_once itself (lock_busy / relaunch_guard / execute_error) is always
# fenced, unconditionally, at its own call site in run_chain below.
_HOST_FENCE_REASONS = frozenset({"budget", "cowork", "wip_limit", "needs_steps",
                                 "needs_spec"})


def run_chain(flow_dir, sprints_root=None, session_id=None, now=None,
             render=True, dry_run=False, burnrate_db=None,
             operator_path=None) -> ChainResult:
    """Run the driver against one flow, one action per iteration, until the
    decision seam stops it or `dry_run` refuses to execute anything.

    One iteration:

        driver = load_driver(flow_dir)            # re-read every pass, so a
                                                    # mid-chain pause/halt is
                                                    # honoured on the next tick
        flow = ft.load_flow(flow_dir)
        action = fp.next_action(flow, sprint, driver)
        decision = decide(action, host_facts(...), gate_verdict(flow))
        if not decision.execute or dry_run: fence (maybe) and stop
        decision = execute_once(...)
        if not decision.execute: fence and stop   # lock_busy/relaunch_guard/error
        chain_depth += 1; write_driver; render_after (best-effort)

    `chain_depth` is reset to 0 and written once, before the loop starts
    (interpretation decision 7), then persisted after every executed action.
    The cap itself is never re-checked here (interpretation decision 6): the
    next `next_action` call returns the halt for it, arriving as
    `reason == "blocking"` with `action.guidance_key == "halt.chain_cap"`.

    `flows_root` is derived, never a parameter: a flow directory is always
    `<flows_root>\\<project>\\<slug>` by construction, and passing the root
    separately invites the two to disagree.
    """
    flow_dir = pathlib.Path(flow_dir).resolve()
    flows_root = flow_dir.parents[1]
    if sprints_root is None:
        sprints_root = ts.DEFAULT_SPRINTS_ROOT
    session_id = session_id or new_session_id(now or fs.iso_utc_now())

    driver = load_driver(flow_dir)
    driver["chain_depth"] = 0
    write_driver(flow_dir, driver)

    # Slice e: the operator record is read once here and written at most once
    # at the end, matching the chain_depth persistence pattern above rather
    # than touching the file on every tick.
    operator = load_operator(operator_path)
    completions_before = operator.get("completions", 0)

    executed = []
    receipts = 0
    stopped = None

    while True:
        tick = now or fs.iso_utc_now()
        driver = load_driver(flow_dir)
        flow = ft.load_flow(flow_dir)
        log = read_log(flow_dir)
        sprints = ts.discover_sprints(sprints_root)
        sprint = fst.resolve_sprint(flow, log, sprints)
        launch = driver.get("launch")
        if launch and not sprint:
            # fst.resolve_sprint needs a 'sprint_emitted' log entry to match
            # against (it disambiguates which of several launches over time a
            # sprint.json belongs to). execute_launch logs a driver_sprint_launch
            # receipt instead, so a launch it authorised is invisible to that
            # matcher until this flow later emits through cmd_advance_guarded.
            # The launch record's own spec_path is a simpler, sufficient
            # question here: has ANY sprint appeared for the spec this launch
            # named. A separate local match, not a reassignment of `sprint`
            # itself, so next_action below keeps resolve_sprint's stricter
            # answer unchanged.
            if any(s.get("project") == flow.get("project")
                   and s.get("spec_path") == launch.get("spec_path")
                   for s in sprints):
                sprint = None  # still None: next_action reasons from `launch`
                driver["launch"] = None
                write_driver(flow_dir, driver)
        elif sprint and launch:
            # Scope B: the record is cleared once the step's sprint.json
            # resolves, so a later step of the same flow gets a clean slate and
            # cannot inherit a stale "a launch is outstanding" answer.
            driver["launch"] = None
            write_driver(flow_dir, driver)
        action = fp.next_action(flow, sprint, driver)
        facts = host_facts(flow, sprints, driver, burnrate_db, sprint=sprint)
        decision = decide(action, facts, gate_verdict(flow),
                          step_of=step_of(flow),
                          flow_spec_path=flow_spec_path(flow))
        stage = flow.get("stage", "")

        if not decision.execute or dry_run:
            if (decision.reason in _HOST_FENCE_REASONS
                    or (decision.reason == "blocking"
                        and decision.action.blocking == "fence")):
                record_fence(flow_dir, decision, stage, tick)
            if (decision.reason == "complete" and not dry_run
                    and not driver.get("completion_counted")):
                # Once per flow, not once per invocation: every later tick
                # over the same closed flow reaches this same stop.
                operator["completions"] = completions_before + 1
                driver["completion_counted"] = True
                driver["last_tick"] = tick
                write_driver(flow_dir, driver)
            stopped = decision
            break

        exec_decision = execute_once(flow_dir, action, session_id,
                                     sprints_root=sprints_root, now=tick,
                                     stage=stage)
        if not exec_decision.execute:
            record_fence(flow_dir, exec_decision, stage, tick)
            stopped = exec_decision
            break

        executed.append(action.kind)
        receipts += 1
        depth = driver.get("chain_depth", 0) + 1
        # Re-read: execute_launch may have written a launch record under the
        # lock, and writing back the pre-execute snapshot would erase it.
        driver = load_driver(flow_dir)
        driver["chain_depth"] = depth
        driver["last_tick"] = tick
        write_driver(flow_dir, driver)

        if render:
            try:
                render_after(flow_dir, flows_root)
            except Exception:
                pass

    if operator.get("completions", 0) != completions_before:
        write_operator(operator, operator_path)

    if render and executed:
        try:
            render_board(flows_root, sprints_root)
        except Exception:
            pass

    return ChainResult(tuple(executed), stopped, driver.get("chain_depth", 0),
                       receipts)


# --- the await poll -----------------------------------------------------------

AWAIT_DEFAULT_SECONDS = 3600
POLL_DEFAULT_SECONDS = 15
AWAIT_START_ACTION = "driver_await_start"
AWAIT_END_ACTION = "driver_await_end"


@dataclasses.dataclass(frozen=True)
class AwaitResult:
    outcome: str                    # "terminal" | "timeout" | "fence"
    sprint: Optional[dict]
    stopped: Optional[Decision]     # None only when outcome == "terminal"
    ticks: int
    elapsed_seconds: float


def _end_await(flow_dir, outcome, sprint, decision, ticks, elapsed, stamp,
               flow) -> AwaitResult:
    """Append the single end-of-await receipt and build the result."""
    ft.append_log(flow_dir, ft._log_entry(
        stamp, flow.get("stage", ""), RECEIPT_ACTOR, AWAIT_END_ACTION,
        outcome="pass" if outcome == "terminal" else "blocked",
        detail="{} after {} tick(s), {:.0f}s elapsed; sprint status {}".format(
            outcome, ticks, elapsed, (sprint or {}).get("status") or "none")))
    return AwaitResult(outcome, sprint, decision, ticks, elapsed)


def await_sprint(flow_dir, sprints_root=None,
                 timeout_seconds=AWAIT_DEFAULT_SECONDS,
                 poll_seconds=POLL_DEFAULT_SECONDS, now=None,
                 clock=None, sleep=None) -> AwaitResult:
    """Poll until the flow's sprint reaches a terminal status, the timeout
    expires, or a fence trips. Never launches, never advances, never writes
    flow.json and never writes foreman.json.

    Both `clock` (monotonic seconds) and `sleep` are injected, exactly as `now`
    already is throughout this module, so the whole poll is testable with zero
    real sleeping.

    Every tick re-reads foreman.json AND the FOREMAN_HALT file (load_driver
    stats it on every call), so /foreman pause and the global kill switch stop
    an await mid-flight just as they stop a chain. The fence signal is the
    public one the chain already uses: next_action returning blocking == "fence".

    The budget fence is deliberately checked on chain re-entry and NOT per tick.
    The BurnRate store refreshes daily, so it cannot move inside one sprint, and
    re-reading it every 15 seconds would be noise.

    Exactly two receipts, one at poll start and one at terminal, timeout or
    fence. A heartbeat per tick would put 240 lines in flow-log.ndjson for a
    one-hour wait.
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    if sprints_root is None:
        sprints_root = ts.DEFAULT_SPRINTS_ROOT
    flow_dir = pathlib.Path(flow_dir).resolve()
    stamp = now or fs.iso_utc_now()
    start = clock()

    flow = ft.load_flow(flow_dir)
    ft.append_log(flow_dir, ft._log_entry(
        stamp, flow.get("stage", ""), RECEIPT_ACTOR, AWAIT_START_ACTION,
        outcome="info",
        detail="awaiting a terminal sprint status, polling every {}s for up to "
               "{}s".format(poll_seconds, timeout_seconds)))

    ticks = 0
    while True:
        ticks += 1
        driver = load_driver(flow_dir)
        flow = ft.load_flow(flow_dir)
        sprints = ts.discover_sprints(sprints_root)
        sprint = fst.resolve_sprint(flow, read_log(flow_dir), sprints)
        action = fp.next_action(flow, sprint, driver)
        elapsed = clock() - start

        if action.blocking == "fence":
            decision = Decision(action, False, "blocking",
                                _STOP_DETAIL["blocking"].format(blocking="fence"))
            return _end_await(flow_dir, "fence", sprint, decision, ticks,
                              elapsed, stamp, flow)

        if sprint and sprint.get("status") in ts.TERMINAL_STATUSES:
            return _end_await(flow_dir, "terminal", sprint, None, ticks,
                              elapsed, stamp, flow)

        if elapsed >= timeout_seconds:
            decision = Decision(action, False, "await_timeout",
                                _STOP_DETAIL["await_timeout"].format(
                                    elapsed=elapsed,
                                    phase=(sprint or {}).get("phase") or "unknown",
                                    iteration=(sprint or {}).get("iteration",
                                                                 "unknown")))
            return _end_await(flow_dir, "timeout", sprint, decision, ticks,
                              elapsed, stamp, flow)

        sleep(poll_seconds)


def run_await(flow_dir, result, sprints_root=None, render=True,
              timeout_seconds=AWAIT_DEFAULT_SECONDS,
              poll_seconds=POLL_DEFAULT_SECONDS, now=None,
              clock=None, sleep=None) -> ChainResult:
    """Wait out a sprint_await stop, then re-enter the chain exactly once.

    On terminal, expect the re-entry to continue: as of build-order step 7
    (slice g) both ship_preflight and sprint_recover are executable, so the
    chain crosses the boundary and stops at the gate it opened.

    On timeout or a fence, returns the original chain's executed list with the
    await's own stop decision, so everything stays resumable: re-running with
    --await simply resumes waiting.
    """
    awaited = await_sprint(flow_dir, sprints_root=sprints_root,
                           timeout_seconds=timeout_seconds,
                           poll_seconds=poll_seconds, now=now,
                           clock=clock, sleep=sleep)
    if awaited.outcome == "terminal":
        again = run_chain(flow_dir, sprints_root=sprints_root, now=now,
                          render=render)
        return ChainResult(result.executed + again.executed, again.stopped,
                           again.chain_depth, result.receipts + again.receipts)
    return ChainResult(result.executed, awaited.stopped, result.chain_depth,
                       result.receipts)


# --- the CLI ------------------------------------------------------------------

import argparse

_AWAIT_VALUE_RE = re.compile(r"^-?\d+$")


def _normalize_await_argv(argv):
    """Splice the bare-flag default in ahead of argparse, working around a
    real argparse ambiguity (bpo-9338): an optional with `nargs="?"` greedily
    consumes whatever non-flag token follows it, even when that token is
    actually meant for the required `ref` positional. `parser.parse_args(["run",
    "--await", "p/s"])` would otherwise fail trying to `int("p/s")` instead of
    treating "p/s" as `ref` and `--await` as bare. Only a token that is not a
    plain integer gets the default spliced in front of it; a real value
    (`--await 120`) is left untouched and consumed normally.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, tok in enumerate(argv):
        if tok == "--await":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or not _AWAIT_VALUE_RE.match(nxt):
                argv.insert(i + 1, str(AWAIT_DEFAULT_SECONDS))
            break
    return argv


def _build_arg_parser() -> argparse.ArgumentParser:
    """Mirrors foreman_status._build_arg_parser's shape: a shared
    --flows-root / --sprints-root pair on every subcommand, `ref` matched the
    same way `foreman_status why` matches it.
    """
    parser = argparse.ArgumentParser(prog="foreman_run.py")
    real_parse_args = parser.parse_args
    parser.parse_args = lambda args=None, namespace=None: real_parse_args(
        _normalize_await_argv(args), namespace)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_common(sub_parser):
        sub_parser.add_argument("--flows-root", default=fst.DEFAULT_FLOWS_ROOT)
        sub_parser.add_argument("--sprints-root", default=fst.DEFAULT_SPRINTS_ROOT)
        sub_parser.add_argument("ref",
            help="project/slug, a unique slug, or a unique slug substring")

    p_run = sub.add_parser("run", help="Run the chain against one flow")
    _add_common(p_run)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--no-render", action="store_true")
    p_run.add_argument("--guide", choices=fp.GUIDE_LEVELS, default=None,
                       help="explanation level for this run; the default "
                            "comes from flows\\_lib\\operator.json")
    # `await` is a Python keyword, so the destination has to be named
    # explicitly; argparse would otherwise produce args.await, which is a
    # SyntaxError to read.
    p_run.add_argument("--await", dest="await_seconds", nargs="?", type=int,
                       const=AWAIT_DEFAULT_SECONDS, default=None,
                       help="poll until the sprint is terminal (bare: {}s)".format(
                           AWAIT_DEFAULT_SECONDS))
    p_run.add_argument("--poll", dest="poll_seconds", type=int,
                       default=POLL_DEFAULT_SECONDS,
                       help="seconds between --await polls (default {})".format(
                           POLL_DEFAULT_SECONDS))

    p_pause = sub.add_parser("pause", help="Record a per-flow pause reason")
    _add_common(p_pause)
    p_pause.add_argument("--reason", required=True)

    p_resume = sub.add_parser("resume", help="Clear a per-flow pause")
    _add_common(p_resume)

    p_ship = sub.add_parser(
        "ship", help="Reopen G4 on a flow whose ship gate was held")
    _add_common(p_ship)

    p_unship = sub.add_parser(
        "unship", help="Print (never run) the revert line for a shipped flow")
    _add_common(p_unship)

    return parser


def _resolve_flow_dir(flows_root, sprints_root, ref: str) -> Tuple[str, str]:
    """(flow_dir, "<project>/<slug>") for `ref`, through
    foreman_status.resolve_ref over foreman_status.collect: the same ref
    matching every other Foreman surface uses. Raises LookupError, same as
    resolve_ref, when the ref cannot be resolved.
    """
    rows = fst.collect(flows_root, sprints_root)
    status = fst.resolve_ref(rows, ref)
    return status.flow_dir, "{}/{}".format(status.project, status.slug)


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        flow_dir, ref = _resolve_flow_dir(args.flows_root, args.sprints_root, args.ref)
    except LookupError as exc:
        print(str(exc))
        return 1

    if args.cmd == "run":
        result = run_chain(flow_dir, sprints_root=args.sprints_root,
                           render=not args.no_render, dry_run=args.dry_run)
        if (args.await_seconds is not None and not args.dry_run
                and result.stopped.action.kind == "sprint_await"):
            result = run_await(flow_dir, result, sprints_root=args.sprints_root,
                               render=not args.no_render,
                               timeout_seconds=args.await_seconds,
                               poll_seconds=args.poll_seconds)
        if args.json:
            decision = result.stopped
            action = decision.action
            payload = {
                "ref": ref,
                "executed": list(result.executed),
                "chain_depth": result.chain_depth,
                "receipts": result.receipts,
                "stopped": {
                    "kind": action.kind,
                    "reason": decision.reason,
                    "detail": decision.detail,
                    "why": action.why,
                    "command": action.command,
                    "gate_id": action.gate_id,
                    "guidance_key": decision.guidance_key,
                },
            }
            print(json.dumps(payload))
            return 0
        print(render_stop(result, ref))
        # Text path only: --json already carries every field guidance would
        # summarize, and the spec leaves that path untouched.
        try:
            flow = ft.load_flow(flow_dir)
        except Exception:
            flow = None
        block = guidance_block(result, flow, load_operator(), args.guide)
        if block:
            print()
            print(block)
        return 0

    if args.cmd == "pause":
        set_pause(flow_dir, args.reason)
        return 0

    if args.cmd == "resume":
        clear_pause(flow_dir)
        return 0

    if args.cmd == "ship":
        try:
            flow = cmd_ship(flow_dir, sprints_root=args.sprints_root)
        except ShipReopenError as exc:
            print(str(exc))
            return 1
        print(_line("FOREMAN", ref))
        print(_line("REOPENED", "G4 token {}".format(flow["gate"]["token"])))
        print(_line("ANSWER", ", ".join(_gate_verdicts("G4"))))
        return 0

    if args.cmd == "unship":
        print(cmd_unship(flow_dir))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
