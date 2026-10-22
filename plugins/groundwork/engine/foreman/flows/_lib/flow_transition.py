"""Flow state transitions for the /flow command (flow-system slice c).

Pure state functions plus thin file I/O, mirroring the sprint state discipline
(C:\\work\\10_holding\\sprints\\_lib\\schema.md). flow.json holds the current
snapshot, overwritten each transition; flow-log.ndjson is an append-only receipt
log, one JSON object per line.

This module NEVER launches a sprint and NEVER sends email. The /flow command
markdown and the Cowork wrapper (both out of scope) call these functions and,
after a transition, call render_flows to regenerate the dashboards.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import sys
from typing import Optional, Tuple

import flow_state as fs

# flows/_lib and sprints/_lib are flat sibling directories with no __init__.py,
# so importing model_map needs the sibling _lib dir on sys.path. Deliberate
# workaround for the missing package structure (spec Problem 4), not a fix.
_SPRINT_LIB = pathlib.Path(__file__).resolve().parents[2] / "sprints" / "_lib"
if str(_SPRINT_LIB) not in sys.path:
    sys.path.insert(0, str(_SPRINT_LIB))

import model_map as mm  # noqa: E402  (must follow the sys.path bootstrap)


# --- file I/O helpers -------------------------------------------------------

def load_flow(flow_dir) -> dict:
    """Read flow.json from a flow directory."""
    return json.loads(
        (pathlib.Path(flow_dir) / "flow.json").read_text(encoding="utf-8")
    )


def write_flow(flow_dir, flow: dict) -> pathlib.Path:
    """Overwrite flow.json with the current snapshot."""
    out = pathlib.Path(flow_dir) / "flow.json"
    out.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
    return out


def _log_entry(now: str, stage: str, actor: str, action: str,
               outcome: str = "info", detail: str = "", ref=None) -> dict:
    """Build one flow-log.ndjson record (mirrors action-log.ndjson fields)."""
    return {"ts": now, "stage": stage, "actor": actor, "action": action,
            "outcome": outcome, "detail": (detail or "")[:240], "ref": ref}


def append_log(flow_dir, entry: dict) -> pathlib.Path:
    """Append one receipt line to flow-log.ndjson."""
    out = pathlib.Path(flow_dir) / "flow-log.ndjson"
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return out


def _read_flow_log(flow_dir) -> list:
    """Read flow-log.ndjson tolerantly: skip a line that fails to parse,
    never raise. Mirrors terminal_sweep.discover_flows's per-flow log read
    (terminal_sweep._read_ndjson), so cmd_advance_guarded's sprint-not-terminal
    guard sees the log the same way terminal_sweep would if it discovered this
    flow from disk during the automated sweep.
    """
    path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# --- new --------------------------------------------------------------------

def set_spec_path(flow: dict, spec_path: str, now: str) -> dict:
    """The ONE write path for flow['spec_path'] (spec scope A).

    Both entry points that can learn a spec route through here: cmd_new at
    creation time, and the advance() G2 consume when the grill has produced the
    spec (the spec IS the G2 artifact). Mirrors the single-decision-write-path
    discipline cmd_gate already has for gate verdicts, so the two write points
    can never disagree about the field's shape or about touching updated_at.

    The null-or-.md rule lives in flow_state.validate and nowhere else, so this
    setter stores what it is given: one rule, one place. Mutates and returns
    `flow`.
    """
    flow["spec_path"] = spec_path
    flow["updated_at"] = now
    return flow


def clear_spec_path(flow: dict) -> dict:
    """Drop flow['spec_path'] as the flow re-enters a grill phase.

    The counterpart to set_spec_path, and the reason a multi-step flow cannot
    launch step N against step N-1's spec. spec_path is a flow-level singleton
    while the grilled artifact is per-step, so a value left standing across a
    step boundary emits a line naming the PREVIOUS step's spec, and
    SpecMissingError cannot catch that because that spec does exist on disk.
    Predicted in writing at line 32 of
    C:\\work\\10_holding\\04_assets\\hub_agent_update_2026-08-03_ZeroNonsense.dev_foreman-routing-reconciliation-and-shim-grill.md

    Deliberately NOT cleared at the sprint to learn boundary: the terminal
    sweep matches a finished sprint back to its flow through
    has_terminal_sprint(project, spec_path, sprints), so the value has to
    survive learn AND the G3 decision or G3 auto-open breaks. Re-entry to
    grill is the latest safe point. The key is REMOVED rather than set to
    null, matching new_flow's absent-key convention.
    """
    flow.pop("spec_path", None)
    return flow


def new_flow(project: str, slug: str, idea: str, now: str,
             model_hint: Optional[dict] = None,
             spec_path: Optional[str] = None) -> dict:
    """Build an initial flow dict at stage 'idea' (gate null, status running).

    The top-level `idea` field captures the `new <project> "<idea>"` argument
    (see plan "Documented schema decisions"); the validator is lenient about it.

    `spec_path` defaults to None, and None leaves the KEY ABSENT rather than
    writing a null: that is what keeps flow.json byte-identical to every flow
    created before this field existed.
    """
    flow = {
        "schema_version": fs.SCHEMA_VERSION,
        "flow_id": fs.make_flow_id(project, slug, now),
        "project": project,
        "slug": slug,
        "idea": idea,
        "stage": "idea",
        "status": "running",
        "current_step": {"index": 0, "of": 0, "phase": None},
        "steps": [],
        "model_hint": dict(model_hint or {}),
        "gate": None,
        "created_at": now,
        "updated_at": now,
    }
    if spec_path is not None:
        set_spec_path(flow, spec_path, now)
    return flow


def cmd_new(flows_root, project: str, idea: str,
            now: Optional[str] = None,
            slug: Optional[str] = None,
            spec_path: Optional[str] = None,
            model_hint: Optional[dict] = None) -> Tuple[pathlib.Path, dict]:
    """Create flows_root/<project>/<slug>/, write flow.json, log flow_created.

    `spec_path` and `model_hint` are both optional and both default to None, so
    every existing call site is unchanged. `model_hint` closes the gap where
    new_flow accepted it but cmd_new dropped it, which left every flow starting
    at model_hint = {} and every emitted /sprint line carrying the bare default
    table.
    """
    now = now or fs.iso_utc_now()
    slug = slug or fs.slugify(idea)
    flow_dir = pathlib.Path(flows_root) / project / slug
    if (flow_dir / "flow.json").exists():
        existing = load_flow(flow_dir)
        raise FileExistsError(
            f"flow '{project}/{slug}' already exists "
            f"(stage={existing.get('stage')}, status={existing.get('status')}); "
            "resume it via /flow, or pick a new slug"
        )
    flow_dir.mkdir(parents=True, exist_ok=True)
    flow = new_flow(project, slug, idea, now, model_hint=model_hint,
                    spec_path=spec_path)
    write_flow(flow_dir, flow)
    append_log(flow_dir, _log_entry(now, "idea", "orchestrator", "flow_created",
                                    outcome="info", detail=f"idea: {idea}"))
    return flow_dir, flow


# --- gate: open (persist-then-prompt) --------------------------------------

def open_gate(flow: dict, gate_id: str, step_index, now: str,
              nonce: Optional[str] = None) -> dict:
    """Pure: attach a fresh awaiting-owner gate and set flow status.

    Generates the self-describing token once (spec lines 56-63). step_index is
    None for G1, an int for G2/G3. Mutates and returns `flow`.
    """
    nonce = nonce or secrets.token_hex(2).upper()  # 4 hex chars, e.g. '7QF3'
    token = fs.gate_token(flow["project"], flow["slug"], gate_id, step_index,
                          now, nonce)
    flow["gate"] = {
        "id": gate_id,
        "step_index": step_index,
        "status": "awaiting-owner",
        "decision": None,
        "notified": False,
        "token": token,
        "opened_at": now,
    }
    flow["status"] = "awaiting-owner"
    flow["updated_at"] = now
    return flow


def persist_open_gate(flow_dir, flow: dict, gate_id: str, step_index, now: str,
                      nonce: Optional[str] = None) -> dict:
    """Persist-then-prompt (spec lines 85-89): apply open_gate, then write
    flow.json + a gate_opened log line FIRST. The durable write is
    authoritative; the caller shows the non-authoritative prompt afterwards.
    """
    flow = open_gate(flow, gate_id, step_index, now, nonce)
    write_flow(flow_dir, flow)
    append_log(flow_dir, _log_entry(
        now, flow["stage"], "orchestrator", "gate_opened", outcome="info",
        detail=f"{gate_id} token {flow['gate']['token']}",
        ref=flow["gate"]["token"]))
    return flow


# --- gate: decide (first-decision-wins) ------------------------------------

def apply_gate_decision(flow: dict, verdict: str, note: str, surface: str,
                        now: str, message_ref=None) -> Tuple[dict, bool]:
    """Pure first-decision-wins: return (flow, applied).

    If there is no open gate, or it is already decided, applied is False and the
    flow is returned unchanged.
    """
    gate = flow.get("gate")
    if not isinstance(gate, dict) or gate.get("status") == "decided":
        return flow, False
    gate["decision"] = {
        "verdict": verdict,
        "note": note,
        "surface": surface,
        "decided_at": now,
        "message_ref": message_ref,
    }
    gate["status"] = "decided"
    flow["updated_at"] = now
    return flow, True


def cmd_gate(flow_dir, verdict: str, note: str = "", surface: str = "session",
             now: Optional[str] = None, message_ref=None) -> dict:
    """The single decision-write path (spec lines 75-79).

    Called identically by an in-session answer and by the slice-e reply sweep.
    Idempotent: if the gate is already decided, the call is ignored and logged
    'reply_ignored (already decided)', so first-decision-wins holds across both
    surfaces. Raises ValueError if the verdict is not valid for the open gate.
    """
    now = now or fs.iso_utc_now()
    flow = load_flow(flow_dir)
    gate = flow.get("gate")
    if not isinstance(gate, dict):
        append_log(flow_dir, _log_entry(now, flow.get("stage", ""), surface,
                   "reply_ignored", outcome="blocked", detail="no open gate"))
        return flow
    if gate.get("status") == "decided":
        append_log(flow_dir, _log_entry(
            now, flow.get("stage", ""), surface, "reply_ignored", outcome="info",
            detail=f"reply_ignored (already decided) verdict={verdict}",
            ref=gate.get("token")))
        return flow
    allowed = fs.valid_verdicts(gate.get("id"))
    if verdict not in allowed:
        raise ValueError(
            f"verdict {verdict!r} not valid for {gate.get('id')}; allowed {allowed}"
        )
    flow, _applied = apply_gate_decision(flow, verdict, note, surface, now,
                                         message_ref)
    write_flow(flow_dir, flow)
    append_log(flow_dir, _log_entry(
        now, flow.get("stage", ""), surface, "gate_decided", outcome="pass",
        detail=f"{gate.get('id')} verdict={verdict}", ref=gate.get("token")))
    return flow


# --- resume (park's counterpart) --------------------------------------------

def resume(flow: dict, now: str) -> dict:
    """Pure: clear a paused flow back to running.

    Raises ValueError if the flow is not paused (park/resume, spec lines
    130-136). Does not touch stage/phase/step/gate; the next advance() call
    re-evaluates whatever boundary the flow was parked at.
    """
    if flow.get("status") != "paused":
        raise ValueError("cannot resume: flow is not paused")
    flow["status"] = "running"
    flow["updated_at"] = now
    return flow


def cmd_resume(flow_dir, now: Optional[str] = None) -> dict:
    """Load, resume, persist, and log a 'resumed' receipt."""
    now = now or fs.iso_utc_now()
    flow = load_flow(flow_dir)
    flow = resume(flow, now)
    write_flow(flow_dir, flow)
    append_log(flow_dir, _log_entry(now, flow.get("stage", ""), "orchestrator",
               "resumed", outcome="info", detail="flow resumed"))
    return flow


# --- advance (emit a /sprint line, never launch) ---------------------------

class RelaunchGuardError(RuntimeError):
    """emit_sprint_line was handed a terminal sprint that already covers this
    flow's spec, so emitting a launch line would relaunch finished work.

    Refuse-and-explain only (spec Invariants): raising this neither launches,
    blocks nor recovers anything. A human reads the named sprint_id and status
    and decides what should happen instead. Deliberately a RuntimeError, not a
    ValueError, so a caller can catch it separately from the model_hint
    validation backstop below.
    """


class SpecMissingError(RuntimeError):
    """emit_sprint_line's host reported that the spec file the line would name
    does not exist on disk, so emitting it would launch a /sprint against a
    file nobody wrote.

    Refuse-and-explain only, exactly like RelaunchGuardError above: raising
    this neither launches, blocks nor recovers anything. A human reads the
    named project, slug and resolved path and writes the spec (or records the
    real spec_path on the flow) before advancing again. Deliberately a
    RuntimeError and not a ValueError, so a caller can catch it separately from
    the model_hint validation backstop.

    The existence CHECK belongs in the host, never here: emit_sprint_line stays
    pure, deterministic and byte-stable, and no existing test that passes a
    fabricated path starts raising. This is the same seam the
    existing_terminal / RelaunchGuardError pair already uses.
    """


class NoGenuineTerminalSprintError(RuntimeError):
    """cmd_advance_guarded found the flow parked at phase=="sprint" (sweep-eligible)
    with no genuine new terminal sprint backing it (terminal_sweep.is_sweep_eligible
    plus terminal_sweep.match_terminal_sprints, the same predicate the automated
    sweep uses), so it refuses to call cmd_advance at all.

    Unlike RelaunchGuardError and SpecMissingError, this check runs BEFORE
    cmd_advance: nothing is mutated, nothing is logged, the flow is returned exactly
    as it sat on disk. There is nothing to explain away afterward, because nothing
    happened.
    """


class SprintNotTerminalError(RuntimeError):
    """cmd_advance_guarded detected the flow crossing phase 'sprint' -> 'learn'
    (an inline, disk-free transition inside advance()) with no terminal sprint
    on disk whose created_at postdates the flow's current sprint_emitted log
    receipt.

    This is the backstop for the one case NoGenuineTerminalSprintError's
    pre-call guard cannot see: terminal_sweep.is_sweep_eligible also requires
    gate is None, but advance()'s phase=="sprint" branch does not check gate
    at all. A flow at phase=="sprint" with a non-null gate (should not happen
    on the normal G2-approve path, since _consume_g2 always clears gate in the
    same write that sets phase to sprint; only reachable via a hand-edited
    flow.json or a bug elsewhere) is not sweep-eligible, so the pre-call guard
    never evaluates it, and cmd_advance runs the crossing anyway. This guard
    catches that crossing after the fact. Decision record:
    docs/superpowers/2026-08-23_proposal_terminal-sprint-guard-decision.md.

    Refuse-and-explain only, exactly like RelaunchGuardError and
    SpecMissingError above: raising this neither launches, blocks nor rolls
    back anything. cmd_advance has already flipped phase to 'learn' and
    persisted it (and logged 'advanced') before this guard runs, so the flow
    is left at phase == 'learn' with no G3 opened, the same shape of stuck
    state the underlying bug already produces at phase == 'sprint', one phase
    further along. A human reads the named project, spec_path and
    sprint_emitted timestamp and decides explicitly what should happen next.
    """


def _resolve_spec_path(flow: dict) -> str:
    """The one place the spec-path fallback rule lives: a flow's recorded
    spec_path if it has one, else the conventional per-project spec path.
    Pure and disk-free; used by emit_sprint_line and by any host (such as
    cmd_advance_guarded) that needs to derive the same path to check it
    against disk. Do not duplicate this expression elsewhere.
    """
    project = flow.get("project", "")
    slug = flow.get("slug", "")
    return flow.get("spec_path") or (
        f"C:\\work\\{project}\\docs\\superpowers\\specs\\{slug}.md"
    )


# The Windows work tree every emitted /sprint line is written against. The line
# is copy-pasted into a Windows session by hand, so it MUST keep naming a
# `C:\work\...` path even when the process that produced it runs on Linux. That
# is the whole reason local_spec_path exists.
WINDOWS_DEV_ROOT = "C:\\work"

# What `C:\src` is mounted as when this code runs off Windows. WSL puts it at
# /mnt/c/work; the Cowork sandbox mounts it somewhere else entirely, so the
# environment gets the last word. Same convention as ZND_ARMORY_PATH.
POSIX_DEV_ROOT_ENV = "ZND_DEV_ROOT"
DEFAULT_POSIX_DEV_ROOT = "/mnt/c/work"

# Read once, here, so a test can flip the POSIX branch on by patching THIS
# attribute. Never patch os.name to do that: os.name is what pathlib itself
# dispatches on, so setting it to "posix" on a Windows host makes every
# pathlib.Path() raise NotImplementedError and takes pytest's own reporting
# down with it (INTERNALERROR, whole run lost, learned 2026-08-13).
IS_WINDOWS = os.name == "nt"

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:[\\/]")


def local_spec_path(spec_path: str, is_windows: Optional[bool] = None,
                    dev_root: Optional[str] = None) -> Optional[str]:
    r"""Translate a spec path into one THIS host can stat, or None if it cannot.

    _resolve_spec_path is deliberately Windows-shaped: its fallback is the
    literal `C:\work\<project>\docs\superpowers\specs\<slug>.md`, because the
    emitted /sprint line is pasted into a Windows session. Handing that string
    to pathlib on POSIX is not merely wrong, it INVERTS the guard: `C:\work\...`
    is a single filename component there, `.exists()` is always False, and every
    flow whose spec path is the Windows fallback would raise SpecMissingError.
    The suite would stay green, because its specs live under tmp_path.

    Three outcomes, and the third is the point:

    * a str  -> stat it, the answer means something
    * spec_path unchanged -> Windows host, or a path already native to this one
    * None   -> UNDECIDABLE. Not "the spec is missing" and not "the spec is
      there": this host cannot tell. Callers must not raise on None. A guard
      that cannot see the disk has to abstain, not guess, or it becomes the
      inverted guard described above wearing a different hat.

    `is_windows` and `dev_root` are injectable so the POSIX branch is testable
    from a Windows host and vice versa; both default to the live environment
    (module-level IS_WINDOWS, and $ZND_DEV_ROOT). A test that needs the guard
    itself, which calls this with no arguments, patches IS_WINDOWS.
    """
    if is_windows is None:
        is_windows = IS_WINDOWS
    if is_windows:
        return spec_path
    if not _DRIVE_LETTER.match(spec_path or ""):
        # Already native to this host (a POSIX absolute path, or a tmp_path
        # fixture). Nothing to translate, and the stat is meaningful as is.
        return spec_path
    if dev_root is None:
        dev_root = os.environ.get(POSIX_DEV_ROOT_ENV) or DEFAULT_POSIX_DEV_ROOT
    if not pathlib.Path(dev_root).is_dir():
        # No mount to check against, so a missing spec and a missing mount are
        # indistinguishable. Abstain.
        return None
    head = spec_path[:len(WINDOWS_DEV_ROOT)]
    if head.lower() != WINDOWS_DEV_ROOT.lower():
        # A Windows path outside C:\work (another drive, another root). Mapping
        # it would be a guess about this host's mount layout. Abstain.
        return None
    tail = spec_path[len(WINDOWS_DEV_ROOT):].replace("\\", "/").lstrip("/")
    return str(pathlib.PurePosixPath(dev_root) / tail) if tail else dev_root


def emit_sprint_line(flow: dict, existing_terminal: Optional[dict] = None,
                     spec_missing: bool = False) -> str:
    """Build the '/sprint <project> <spec-path> --model-map ...' line to EMIT.

    NEVER executed (spec decision #1, lines 82-83). The spec-path falls back to
    a conventional per-project spec path when the flow has not recorded an
    explicit design spec.

    The line ALWAYS carries a complete model map: model_hint is merged over
    model_map.DEFAULT_TABLE (hint wins per phase) and all seven pairs are
    serialized in canonical DEFAULT_TABLE order, so an empty hint yields the
    full default table and a partial hint yields a complete merged map. That
    makes the emitted line a self-contained receipt: replaying an old line
    reproduces the routing that was current when it was emitted, even if
    DEFAULT_TABLE later changes. Output is deterministic and byte-stable for a
    given flow; the harness relies on that.

    Raises ValueError if model_hint carries a key that is not a /sprint phase.
    That is a backstop: flow_state.validate rejects such a key upstream, so no
    operator intent is ever silently discarded by the merge.

    `existing_terminal` is the relaunch guard: pass
    terminal_sweep.has_terminal_sprint(project, spec_path, sprints) from a host
    that already loaded sprints from disk and this call raises
    RelaunchGuardError instead of returning a line. It defaults to None, so
    every existing call site and every golden case is byte-for-byte unchanged.
    The check belongs in that host, never inside advance(), whose
    pure-transition contract this spec does not touch.

    `spec_missing` is the second host-computed guard: a host that has stat'd
    the resolved spec path passes True and this call raises SpecMissingError
    naming project, slug and that path instead of returning a line. It defaults
    to False, so every existing call site and every golden case is
    byte-for-byte unchanged.
    """
    if existing_terminal:
        raise RelaunchGuardError(
            "refusing to emit a /sprint line for {}/{}: sprint {} already "
            "reached terminal status {} for this spec. Relaunching would redo "
            "finished work; decide explicitly what should happen next.".format(
                flow.get("project", ""), flow.get("slug", ""),
                existing_terminal.get("sprint_id"),
                existing_terminal.get("status")))
    project = flow.get("project", "")
    slug = flow.get("slug", "")
    spec_path = _resolve_spec_path(flow)
    if spec_missing:
        raise SpecMissingError(
            "refusing to emit a /sprint line for {}/{}: the spec it would "
            "name, {}, does not exist on disk. Write that spec, or record the "
            "real spec_path on the flow, then advance again.".format(
                project, slug, spec_path))
    mh = flow.get("model_hint") or {}
    unknown = sorted(k for k in mh if k not in mm.DEFAULT_TABLE)
    if unknown:
        raise ValueError(
            f"model_hint key {unknown[0]!r} is not a /sprint phase "
            f"{tuple(mm.DEFAULT_TABLE)}"
        )
    merged = {**mm.DEFAULT_TABLE, **mh}
    model_map = ",".join(f"{phase}={merged[phase]}" for phase in mm.DEFAULT_TABLE)
    return f"/sprint {project} {spec_path} --model-map {model_map}"


def advance(flow: dict, now: str, steps: Optional[list] = None,
            spec_path: Optional[str] = None,
            driver: Optional[dict] = None) -> Tuple[dict, Optional[str]]:
    """Move the flow across the next boundary: open the next required gate,
    consume an already-decided gate's verdict, or cross a gate-free boundary
    directly (spec "Mechanism principle" - exactly one of these per call).

    `steps` is a list of step titles; required only to consume a G1
    'approve' verdict at the breakdown -> execute boundary (ignored
    otherwise, per "steps arrive only via the explicit steps argument").

    `spec_path` is the second write point for flow['spec_path'] (spec scope A):
    the spec is the G2 artifact, so a call that consumes a G2 'approve' records
    it through set_spec_path before emit_sprint_line builds the line. It is
    ignored at every other boundary and defaults to None, so an existing caller
    that passes nothing behaves exactly as before.

    `driver` is the advisory foreman.json record, MUTATED IN PLACE and never
    read from or written to disk here: this module does no foreman.json I/O and
    must not import foreman_run (that would be a cycle). Only one branch uses
    it, the G4 `hold` verdict, which has to leave a mark somewhere for
    `foreman_run.py ship` to find and the flow schema has no field for "held".
    Every other boundary ignores it, so a caller that passes nothing behaves
    exactly as before.

    Returns (flow, sprint_line); sprint_line is a '/sprint ...' string to
    EMIT (never executed) when this call moves a step from grill to sprint
    phase, else None.

    Raises ValueError if flow["status"] == "paused", or if a gate is
    `awaiting-owner`.
    """
    if flow.get("status") == "paused":
        raise ValueError("cannot advance: flow is paused")
    gate = flow.get("gate")
    if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
        raise ValueError("cannot advance: gate awaiting decision")

    sprint_line: Optional[str] = None
    stage = flow.get("stage")

    if stage == "idea":
        flow["stage"] = "design"
        flow["status"] = "running"
    elif stage == "design":
        flow["stage"] = "breakdown"
        flow["status"] = "running"
    elif stage == "breakdown":
        if isinstance(gate, dict) and gate.get("status") == "decided":
            flow = _consume_g1(flow, gate, steps)
        else:
            flow = open_gate(flow, "G1", None, now)
    elif stage == "execute":
        cs = dict(flow.get("current_step") or {})
        phase = cs.get("phase")
        if phase == "grill":
            if isinstance(gate, dict) and gate.get("status") == "decided":
                flow, sprint_line = _consume_g2(flow, gate, cs, now, spec_path)
            else:
                flow = open_gate(flow, "G2", cs.get("index"), now)
        elif phase == "sprint":
            if isinstance(gate, dict) and gate.get("status") == "decided":
                flow, driver = _consume_g4(flow, gate, cs, driver)
            else:
                flow = open_gate(flow, "G4", cs.get("index"), now)
        elif phase == "learn":
            if isinstance(gate, dict) and gate.get("status") == "decided":
                flow = _consume_g3(flow, gate, cs)
            else:
                flow = open_gate(flow, "G3", cs.get("index"), now)
    # stage == "closed": no-op, nothing left to advance.

    flow["updated_at"] = now
    return flow, sprint_line


def _consume_g1(flow: dict, gate: dict, steps: Optional[list]) -> dict:
    """Apply a decided G1 verdict at the breakdown boundary, clearing the gate."""
    verdict = gate["decision"]["verdict"]
    if verdict == "approve":
        if not steps:
            raise ValueError(
                "advance: 'steps' (non-empty list of titles) required to "
                "consume a G1 approve at breakdown -> execute"
            )
        step_list = [{"index": i, "title": t, "status": "pending"}
                     for i, t in enumerate(steps)]
        step_list[0]["status"] = "running"
        flow["steps"] = step_list
        flow["stage"] = "execute"
        flow["current_step"] = {"index": 0, "of": len(step_list), "phase": "grill"}
        flow["status"] = "running"
    elif verdict == "reject":
        flow["stage"] = "design"
        flow["status"] = "running"
    elif verdict == "park":
        flow["status"] = "paused"
    flow["gate"] = None
    return flow


def _consume_g2(flow: dict, gate: dict, cs: dict, now: str,
                spec_path: Optional[str] = None) -> Tuple[dict, Optional[str]]:
    """Apply a decided G2 verdict at the grill boundary, clearing the gate.

    On 'approve' with a spec_path supplied, records it through the shared
    set_spec_path setter FIRST, so the /sprint line this function emits names
    the spec that was just approved rather than the slug fallback.
    """
    verdict = gate["decision"]["verdict"]
    sprint_line: Optional[str] = None
    if verdict == "approve":
        if spec_path is not None:
            set_spec_path(flow, spec_path, now)
        cs["phase"] = "sprint"
        flow["status"] = "running"
        sprint_line = emit_sprint_line(flow)
    elif verdict == "reject":
        flow["status"] = "running"  # phase stays "grill"; next call opens a fresh G2
    elif verdict == "park":
        flow["status"] = "paused"
    flow["current_step"] = cs
    flow["gate"] = None
    return flow, sprint_line


def _consume_g3(flow: dict, gate: dict, cs: dict) -> dict:
    """Apply a decided G3 verdict at the learn boundary, clearing the gate."""
    verdict = gate["decision"]["verdict"]
    steps = flow.get("steps") or []
    i = cs.get("index", 0)
    if verdict == "proceed":
        if 0 <= i < len(steps):
            steps[i]["status"] = "done"
        new_index = i + 1
        cs["index"] = new_index
        if new_index == cs.get("of"):
            flow["stage"] = "closed"
            flow["status"] = "closed"
        else:
            if 0 <= new_index < len(steps):
                steps[new_index]["status"] = "running"
            cs["phase"] = "grill"
            flow["status"] = "running"
        flow["steps"] = steps
    elif verdict == "adapt":
        cs["phase"] = "grill"
        flow["status"] = "running"
    elif verdict == "close":
        flow["stage"] = "closed"
        flow["status"] = "closed"
    elif verdict == "park":
        flow["status"] = "paused"
    # A new grill produces a new spec, so the old one must not survive into it.
    # Covers 'proceed' to a further step and 'adapt' re-grilling this one; a
    # closed or parked flow keeps its last spec_path as a record.
    if cs.get("phase") == "grill":
        clear_spec_path(flow)
    flow["current_step"] = cs
    flow["gate"] = None
    return flow


def _consume_g4(flow: dict, gate: dict, cs: dict,
                driver: Optional[dict]) -> Tuple[dict, dict]:
    """Apply a decided G4 verdict at the sprint boundary, clearing the gate.

    Returns (flow, driver). The only _consume_* that touches the driver, and
    the reason its signature differs from _consume_g1 / _consume_g2 /
    _consume_g3: `hold` has to record that a reopen is pending, and flow.json
    has no field for it.

    `ship` and `hold` produce the SAME state transition, deliberately: the step
    is finished either way and G3 reviews the learnings next. The only
    difference is whether the branch was merged, and merging is foreman_run's
    ship_execute, not this function's. The branch a `hold` leaves behind is
    simply never merged.

    The hold timestamp is the decision's own `decided_at` rather than a fresh
    clock read, because this function is pure and the honest answer to "when
    was this held" is "when it was decided".
    """
    verdict = gate["decision"]["verdict"]
    driver = driver if isinstance(driver, dict) else {}
    if verdict in ("ship", "hold"):
        driver.pop("shipped", None)
        driver.pop("preflight", None)
        driver.pop("ship", None)
        cs["phase"] = "learn"
        flow["status"] = "running"
        if verdict == "hold":
            decision = gate["decision"]
            driver["held"] = {
                "reason": decision.get("note") or "",
                "ts": decision.get("decided_at") or flow.get("updated_at", ""),
            }
    elif verdict == "park":
        flow["status"] = "paused"
    flow["current_step"] = cs
    flow["gate"] = None
    return flow, driver


def cmd_advance(flow_dir, now: Optional[str] = None,
                steps: Optional[list] = None,
                spec_path: Optional[str] = None,
                driver: Optional[dict] = None) -> Tuple[dict, Optional[str]]:
    """Load, advance, persist, and log. Returns (flow, sprint_line).

    `steps` is a list of step titles, threaded through to `advance()`;
    required only when this call consumes a G1 'approve' verdict. `spec_path`
    is threaded through to advance(); it is recorded only when this call
    consumes a G2 'approve'. `driver` is passed straight through to advance();
    it is mutated in place, only by a G4 hold, and never written to disk here.
    A gate opened by this call is persisted
    (flow.json write) and logged as 'gate_opened' before returning
    (persist-then-prompt); a sprint step is EMITTED as a /sprint line (logged
    'sprint_emitted'), never auto-run.
    """
    now = now or fs.iso_utc_now()
    flow = load_flow(flow_dir)
    flow, sprint_line = advance(flow, now, steps=steps, spec_path=spec_path,
                                driver=driver)
    write_flow(flow_dir, flow)

    gate = flow.get("gate")
    if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
        append_log(flow_dir, _log_entry(
            now, flow.get("stage", ""), "orchestrator", "gate_opened",
            outcome="info", detail=f"{gate['id']} token {gate['token']}",
            ref=gate["token"]))
    elif sprint_line:
        append_log(flow_dir, _log_entry(now, flow.get("stage", ""), "orchestrator",
                   "sprint_emitted", outcome="info", detail=sprint_line))
    else:
        append_log(flow_dir, _log_entry(now, flow.get("stage", ""), "orchestrator",
                   "advanced", outcome="info", detail=f"stage={flow.get('stage')}"))
    return flow, sprint_line


def cmd_advance_guarded(flow_dir, sprints_root: Optional[str] = None,
                        now: Optional[str] = None,
                        spec_path: Optional[str] = None,
                        driver: Optional[dict] = None) -> Tuple[dict, Optional[str]]:
    """Like cmd_advance, plus three read-only guards applied after the fact
    (spec 2026-08-01 Resolved contract 2 for the first two; spec
    2026-08-23-sprint-not-terminal-guard-design.md for the third). `driver`
    is threaded straight through to cmd_advance (spec scope B), unrelated to
    the three guards.

    Calls cmd_advance(flow_dir, now, spec_path=spec_path): this function adds
    read-only checks afterward, and also passes `spec_path` straight through
    to cmd_advance so the production /flow advance entry point can record a G2
    spec the same way the inner function can (spec scope A, criterion 2).
    `spec_path` is the last keyword argument and defaults to None, so every
    existing positional or keyword call is unaffected.

    Before calling cmd_advance, this function loads the flow once more to read
    its current_step.phase (`phase_before`) and its gate (`gate_before`),
    purely to detect the phase == "sprint" -> "learn" crossing after
    cmd_advance runs (that transition is inline and disk-free inside
    advance(), and emits no sprint line, so it is otherwise invisible to a
    caller here). This crossing happens on two different shapes, and only one
    of them gets re-checked:

    - `gate_before` is NOT a decided gate with id "G4" (either gate is None,
      the real auto-sweep-eligible shape the pre-call guard above already
      requires a genuine terminal sprint for, or it is some other decided
      gate a hand edit or bug left sitting at phase=="sprint"): this function
      loads sprints from disk (same sprints_root resolution the relaunch
      guard below uses) and checks terminal_sweep.match_terminal_sprints for
      this single flow (using the PRE-crossing flow snapshot, since
      match_terminal_sprints's own is_sweep_eligible predicate requires
      phase == "sprint" and gate is None, which is only true before
      cmd_advance's write, so this check is always exercised here whether or
      not gate_before happens to be None), with swept_index={}. If no
      candidate is returned (no terminal sprint whose created_at postdates
      the flow's current sprint_emitted log receipt), raises
      SprintNotTerminalError naming project, spec_path and that receipt's
      timestamp, instead of returning silently as if the crossing were
      routine.
    - `gate_before` IS a decided gate with id "G4" (a genuine ship/hold
      consumption via `_consume_g4`): the re-check is skipped entirely. A
      gate with id "G4" only ever gets created by open_gate(flow, "G4", ...),
      which this module's phase=="sprint" branch of advance() only reaches
      through the pre-call guard above or terminal_sweep.apply_match, both of
      which already ran the genuine terminal-sprint match before opening it.
      match_terminal_sprints would always return [] here regardless of
      whether the sprint is terminal (is_sweep_eligible requires gate is
      None), so re-running it against the pre-crossing snapshot (which by
      definition still carries the just-consumed G4 gate) is not a second
      opinion, it is a guaranteed false positive.

    This is the second half of the relaunch guard's job: the first half
    (below) catches a hand-typed re-emit of an already-finished sprint's
    /sprint line; this half catches a hand-typed advance PAST a sprint step
    with no genuinely finished (or no) terminal sprint behind it.

    If cmd_advance did not cross sprint -> learn AND did not emit a sprint
    line (inline transition, or a gate just opened), returns its result
    unchanged: none of the three guards has anything to check.

    If a sprint line WAS emitted, this function is the host emit_sprint_line's
    docstrings describe: it has already loaded sprints from disk for the
    relaunch guard below, so it also stats the resolved spec path (via the
    shared _resolve_spec_path helper, the same derivation emit_sprint_line
    uses internally) and applies two guards in this order:

    1. If a terminal sprint already covers this flow's spec
       (terminal_sweep.has_terminal_sprint), calls
       emit_sprint_line(flow, existing_terminal=match) so it raises
       RelaunchGuardError with the exact tested message (sprint_id and status
       named), instead of returning the now-stale relaunchable line.
    2. Else if the resolved spec path does not exist on disk, calls
       emit_sprint_line(flow, spec_missing=True) so it raises SpecMissingError
       naming project, slug and the resolved path, instead of returning a line
       that names a spec nobody wrote.

    The flow's state is already persisted by cmd_advance before any of the
    three checks runs (and, for the sprint -> learn crossing, already logged
    as 'advanced'; for a sprint line emission, already logged as
    'sprint_emitted'), so raising here leaves nothing inconsistent on disk:
    this is refuse-and-explain only, never a rollback, never a block, never a
    second write. That is the same documented caveat RelaunchGuardError
    already carries (flow.md); mirroring its shape here rather than
    pre-checking before cmd_advance keeps all three guards behaviourally
    identical instead of any one being cleverer than the others.

    Before any of the above runs, this function also applies a pre-call guard:
    if the on-disk flow is currently sweep-eligible
    (terminal_sweep.is_sweep_eligible: stage=="execute", current_step.phase
    =="sprint", gate is None), it requires that a genuine new terminal sprint
    exists for it, using the exact predicate terminal_sweep.match_terminal_sprints
    already applies for the automated sweep. If none exists, it raises
    NoGenuineTerminalSprintError instead of calling cmd_advance at all: unlike
    the two guards above, nothing is mutated or logged when this one fires.

    This is intentionally additive: it exists because the spec's own
    'host-side caller that already loads sprints from disk for the /flow
    advance CLI path' did not exist anywhere in this repo (the /flow advance
    command calls cmd_advance directly with no Python host script in between,
    and the only other production caller of cmd_advance,
    terminal_sweep.apply_match, never calls emit_sprint_line, and never
    reaches the sprint -> learn crossing either since foreman_run.py's driver
    stops at phase == "sprint" before ever calling cmd_advance_guarded). Once
    the Foreman driver (slice b) lands with its own gate_consume/sprint_launch
    action plumbing, this function's guards duplicate that seam and should be
    retired, exactly as the spec's 'Explicitly out of scope' section warns for
    gate_watch.py.
    """
    # Deferred import: terminal_sweep imports this module (flow_transition as
    # ft), so a top-level `import terminal_sweep` here would be circular.
    # flows/_lib puts both modules in the same sys.path directory, so this
    # local import resolves the same module a top-level import would, without
    # creating a static cycle at module-load time.
    import terminal_sweep as ts

    before_flow = load_flow(flow_dir)
    phase_before = (before_flow.get("current_step") or {}).get("phase")

    if ts.is_sweep_eligible(before_flow):
        log = ts._read_ndjson(pathlib.Path(flow_dir) / "flow-log.ndjson")
        ctx = {"flow": before_flow, "log": log}
        sprints = ts.discover_sprints(sprints_root or ts.DEFAULT_SPRINTS_ROOT)
        swept_index = ts.build_swept_index([ctx])
        if not ts.match_terminal_sprints([ctx], sprints, swept_index):
            reasons = ts.diagnose_unmatched([ctx], sprints)
            if reasons:
                detail = reasons[0]["detail"]
            else:
                # diagnose_unmatched always diagnoses with an empty
                # swept_index (its own docstring: an already-swept flow is
                # normally no longer sweep-eligible, so it cannot hide a
                # real unmatched flow). Here the flow IS still
                # sweep-eligible despite a sprint_swept receipt for its only
                # remaining candidate, so diagnose_unmatched sees a "match"
                # and produces no row. Name that directly instead of
                # indexing into an empty list.
                detail = ("the only matching terminal sprint is already "
                          "recorded as swept for this flow")
            raise NoGenuineTerminalSprintError(
                "refusing to advance {}/{} from phase=sprint to learn: {}".format(
                    before_flow.get("project", ""), before_flow.get("slug", ""), detail))

    # The pre-call guard above only fires when is_sweep_eligible(before_flow)
    # is True (stage=="execute", phase=="sprint", gate is None). advance()'s
    # own phase=="sprint" branch does not check gate, so a flow sitting at
    # phase=="sprint" with a non-null gate skips the pre-call guard entirely
    # and cmd_advance still performs the crossing below. The post-call check
    # that follows is the backstop for exactly that case; see
    # SprintNotTerminalError's docstring.
    flow, sprint_line = cmd_advance(flow_dir, now, spec_path=spec_path,
                                    driver=driver)

    phase_after = (flow.get("current_step") or {}).get("phase")
    if phase_before == "sprint" and phase_after == "learn":
        gate_before = before_flow.get("gate")
        # A decided gate whose id is "G4" only ever got there through
        # open_gate(flow, "G4", ...), which this module's own phase=="sprint"
        # branch of advance() only reaches via the pre-call guard above (a
        # validated match) or terminal_sweep.apply_match (same validation).
        # Its ship/hold consumption has nothing left to re-verify, so it
        # skips the re-check entirely. Any OTHER decided-gate shape at
        # phase=="sprint" (a hand-edited or otherwise stale gate that never
        # went through that validated open) still gets the full re-check
        # below: is_sweep_eligible would exclude it too (gate is not None),
        # so it is exactly the case the re-check exists to catch.
        if not (isinstance(gate_before, dict) and gate_before.get("id") == "G4"):
            log = _read_flow_log(flow_dir)
            sprints = ts.discover_sprints(sprints_root or ts.DEFAULT_SPRINTS_ROOT)
            matches = ts.match_terminal_sprints(
                [{"flow": before_flow, "log": log}], sprints, {})
            if not matches:
                emitted = ts.latest_sprint_emitted(log)
                waited_ts = emitted.get("ts", "") if emitted else ""
                raise SprintNotTerminalError(
                    "refusing the sprint -> learn crossing for {}/{}: no terminal "
                    "sprint on disk postdates the sprint_emitted receipt at {} "
                    "for spec {}. cmd_advance has already flipped phase to "
                    "'learn' and persisted it; decide explicitly what should "
                    "happen next.".format(
                        before_flow.get("project", ""), before_flow.get("slug", ""),
                        waited_ts, _resolve_spec_path(before_flow)))
        return flow, sprint_line

    if not sprint_line:
        return flow, sprint_line
    project = flow.get("project", "")
    resolved_spec_path = _resolve_spec_path(flow)
    sprints = ts.discover_sprints(sprints_root or ts.DEFAULT_SPRINTS_ROOT)
    match = ts.has_terminal_sprint(project, resolved_spec_path, sprints)
    if match:
        emit_sprint_line(flow, existing_terminal=match)  # raises RelaunchGuardError
    # local_spec_path returns None when this host cannot check the path at all
    # (a C:\work path with no mount behind it). Abstaining is deliberate: see
    # its docstring for why raising there inverts the guard instead of arming
    # it. On Windows the translation is the identity, so nothing changes.
    local_path = local_spec_path(resolved_spec_path)
    if local_path is not None and not pathlib.Path(local_path).exists():
        emit_sprint_line(flow, spec_missing=True)  # raises SpecMissingError
    return flow, sprint_line
