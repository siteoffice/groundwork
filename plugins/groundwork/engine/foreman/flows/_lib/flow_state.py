"""flow.json schema constants, validator, and shared primitives (flow-system slice c).

Source of truth for the flow.json schema described in
(design spec, not shipped).
Mirrors the sprint schema discipline in C:\\work\\10_holding\\sprints\\_lib\\schema.md:
one file overwritten each transition, ISO 8601 UTC timestamps with a trailing Z at
second precision.

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import datetime as _dt
import logging
import pathlib
import re
import sys
from typing import List

# flows/_lib and sprints/_lib are flat sibling directories with no __init__.py,
# so importing model_map needs the sibling _lib dir on sys.path. Deliberate
# workaround for the missing package structure (spec Problem 4), not a fix.
_SPRINT_LIB = pathlib.Path(__file__).resolve().parents[2] / "sprints" / "_lib"
if str(_SPRINT_LIB) not in sys.path:
    sys.path.insert(0, str(_SPRINT_LIB))

import model_map as mm  # noqa: E402  (must follow the sys.path bootstrap)

SCHEMA_VERSION = 1

# Enum domains (spec "flow.json schema", lines 41-50).
STAGES = ("idea", "design", "breakdown", "execute", "closed")
STATUSES = ("running", "awaiting-owner", "escalated", "closed", "paused")
PHASES = ("grill", "sprint", "learn")             # the execute step cycle
STEP_STATUSES = ("pending", "running", "done")    # per-step lifecycle (plan-defined)
# Gate vocabulary (gate rename OF30). Gates carry names, never numbers: the
# position ("gate 1 of 2") is computed at display from ACTIVE_GATES.
# `decide` is part of the order but the engine does not open it in this release.
GATE_ORDER = ("decide", "spec", "accept")
ACTIVE_GATES = ("spec", "accept")
LEARNINGS = "learnings"

# The only place old gate codes appear. Old records stay readable through this
# table; nothing here is ever written back by new code. The plan gate folds
# into spec, the old spec gate is spec, the ship gate and H3 are accept; the
# learnings gate and H4 are retired.
GATE_ALIASES = {"G1": "spec", "G2": "spec", "G4": "accept", "H3": "accept"}
RETIRED_GATES = ("G3", "H4")
NULL_STEP_ALIASES = ("G1",)  # legacy plan gate stored with step_index null
GATE_STATUSES = ("awaiting-owner", "decided")
MODEL_CLASSES = ("opus", "sonnet", "haiku", "fable")

# Verdicts allowed per gate, keyed by name; `park` is valid at any gate. `decide`
# has no entry until it is defined (valid_verdicts falls back to park only).
# foreman_plan.SHIP_VERDICTS is the same tuple as "accept"; test_foreman_plan
# asserts the two agree.
VERDICTS_BY_GATE = {
    "spec": ("approve", "reject", "park"),
    "accept": ("ship", "hold", "park"),
}

# Display text for each verdict (display only, never parsed).
VERDICT_MEANINGS = {
    "approve": "Approve",
    "reject": "Reject (do not build this step)",
    "park": "Park (pause, answer later)",
    "ship": "Ship (accept and merge)",
    "hold": "Hold (send back)",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")

OPT_IN_IGNORED_LINE = "opt_in_gates ignored: G1 and G3 retired"
_LOG = logging.getLogger("flow_state")


def read_opt_in_gates(source) -> tuple:
    """Read and ignore `opt_in_gates` (the plan and learnings gates are retired).

    Accepts a dict with or without the key. A non-empty value logs
    OPT_IN_IGNORED_LINE once for this call. Always returns (); never raises
    and never aliases the values.
    """
    if isinstance(source, dict) and source.get("opt_in_gates"):
        _LOG.warning(OPT_IN_IGNORED_LINE)
    return ()


def iso_utc_now() -> str:
    """Current UTC time as ISO 8601 with trailing Z, second precision."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_stamp(now: str) -> str:
    """Turn '2026-07-18T09:14:03Z' into '20260718T091403Z' for ids and tokens."""
    return re.sub(r"[:\-]", "", now)


def make_flow_id(project: str, slug: str, now: str) -> str:
    """`<project>__<slug>__<UTC compact>` (mirrors sprint_id)."""
    return f"{project}__{slug}__{compact_stamp(now)}"


def slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to '-', trim, first 5 tokens."""
    cleaned = _SLUG_RE.sub("-", text.lower()).strip("-")
    tokens = [t for t in cleaned.split("-") if t]
    return "-".join(tokens[:5]) or "flow"


def canonical_gate(gate_id):
    """Gate name for any stored id. A name returns itself, an alias its name,
    a retired id `learnings`; anything else (None included) comes back as given.
    Codes match case-insensitively. Pure."""
    if not isinstance(gate_id, str):
        return gate_id
    if gate_id in GATE_ORDER:
        return gate_id
    code = gate_id.strip().upper()
    if code in GATE_ALIASES:
        return GATE_ALIASES[code]
    if code in RETIRED_GATES:
        return LEARNINGS
    return gate_id


def is_retired_gate(gate_id) -> bool:
    return canonical_gate(gate_id) == LEARNINGS


def normalize_gate(gate):
    """In place: an alias id becomes its name; a stored null-step legacy gate
    gets step_index 0. Retired and unknown ids, token and other keys untouched."""
    if not isinstance(gate, dict):
        return gate
    gid = gate.get("id")
    if isinstance(gid, str) and gid.strip().upper() in GATE_ALIASES:
        if gid.strip().upper() in NULL_STEP_ALIASES and gate.get("step_index") is None:
            gate["step_index"] = 0
        gate["id"] = canonical_gate(gid)
    return gate


def normalize_flow(flow):
    """Apply normalize_gate to flow["gate"] in place and return the flow."""
    if isinstance(flow, dict):
        normalize_gate(flow.get("gate"))
    return flow


def gate_position(gate_id):
    """1-based position in ACTIVE_GATES, or None (decide, retired, unknown)."""
    name = canonical_gate(gate_id)
    if name in ACTIVE_GATES:
        return ACTIVE_GATES.index(name) + 1
    return None


def gate_label(gate_id) -> str:
    """`gate 1 of 2`, computed at display, never stored; "" when no position."""
    pos = gate_position(gate_id)
    return f"gate {pos} of {len(ACTIVE_GATES)}" if pos else ""


def valid_verdicts(gate_id: str) -> tuple:
    """Verdicts allowed for a gate id (old codes read through the alias table);
    unknown and retired ids allow only 'park'."""
    return VERDICTS_BY_GATE.get(canonical_gate(gate_id), ("park",))


def gate_token(project: str, slug: str, gate_id: str, step_index,
               gate_opened_at: str, nonce: str) -> str:
    """Self-describing gate token, generated once at gate-open (spec lines 56-63).

    FLOW-<PROJECT>-<SLUG>-<GATE>[-S<step>]-<opened_at compact>-<nonce4>.
    <GATE> is the canonical gate name (spec, accept), never an old code.
    step_index is required: every gate belongs to a step.
    """
    if step_index is None:
        raise ValueError("gate_token: step_index is required")
    parts = ["FLOW", project.upper(), slug, canonical_gate(gate_id)]
    parts.append(f"S{step_index}")
    parts.append(compact_stamp(gate_opened_at))
    parts.append(nonce)
    return "-".join(parts)


def _validate_gate(gate) -> List[str]:
    if gate is None:
        return []
    if not isinstance(gate, dict):
        return ["gate must be an object or null"]
    errors: List[str] = []
    gid = gate.get("id")
    canon = canonical_gate(gid)
    if canon == LEARNINGS:
        errors.append(f"gate.id {gid!r} is retired (learnings run without a gate)")
    elif canon not in ACTIVE_GATES:
        errors.append(f"gate.id {gid!r} not in {ACTIVE_GATES}")
    if gate.get("status") not in GATE_STATUSES:
        errors.append(f"gate.status {gate.get('status')!r} not in {GATE_STATUSES}")
    if not isinstance(gate.get("notified"), bool):
        errors.append("gate.notified must be a bool")
    si = gate.get("step_index")
    if not (isinstance(si, int) and not isinstance(si, bool)) and not (
            si is None and gid in NULL_STEP_ALIASES):
        errors.append("gate.step_index must be an int")
    decision = gate.get("decision")
    if decision is not None:
        if not isinstance(decision, dict):
            errors.append("gate.decision must be an object or null")
        else:
            if decision.get("verdict") not in valid_verdicts(gid):
                errors.append(
                    f"gate.decision.verdict {decision.get('verdict')!r} not valid for {gid}"
                )
            for key in ("note", "surface", "decided_at", "message_ref"):
                if key not in decision:
                    errors.append(f"gate.decision missing key: {key}")
    return errors


def validate(flow) -> List[str]:
    """Return a list of schema violations for `flow`; empty means valid.

    Pure, returns human-readable strings (mirrors spec_lint.lint's contract).
    Lenient about unknown extra keys.
    """
    if not isinstance(flow, dict):
        return ["flow is not an object"]
    errors: List[str] = []

    if flow.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    for field in ("flow_id", "project", "slug", "created_at", "updated_at"):
        val = flow.get(field)
        if not isinstance(val, str) or not val:
            errors.append(f"missing or empty required field: {field}")

    if flow.get("stage") not in STAGES:
        errors.append(f"stage {flow.get('stage')!r} not in {STAGES}")
    if flow.get("status") not in STATUSES:
        errors.append(f"status {flow.get('status')!r} not in {STATUSES}")

    cs = flow.get("current_step")
    if not isinstance(cs, dict):
        errors.append("current_step must be an object")
    else:
        for key in ("index", "of"):
            if not isinstance(cs.get(key), int):
                errors.append(f"current_step.{key} must be an int")
        phase = cs.get("phase")
        if phase is not None and phase not in PHASES:
            errors.append(f"current_step.phase {phase!r} not in {PHASES}")

    steps = flow.get("steps")
    if not isinstance(steps, list):
        errors.append("steps must be an array")
    else:
        for i, st in enumerate(steps):
            if not isinstance(st, dict):
                errors.append(f"steps[{i}] must be an object")
                continue
            if not isinstance(st.get("index"), int):
                errors.append(f"steps[{i}].index must be an int")
            if not isinstance(st.get("title"), str):
                errors.append(f"steps[{i}].title must be a string")
            if st.get("status") not in STEP_STATUSES:
                errors.append(
                    f"steps[{i}].status {st.get('status')!r} not in {STEP_STATUSES}"
                )

    mh = flow.get("model_hint")
    if not isinstance(mh, dict):
        errors.append("model_hint must be an object")
    else:
        phases = tuple(mm.DEFAULT_TABLE)
        for key, val in mh.items():
            if key not in mm.DEFAULT_TABLE:
                errors.append(
                    f"model_hint[{key!r}] is not a /sprint phase {phases}"
                )
            if val not in MODEL_CLASSES:
                errors.append(
                    f"model_hint[{key!r}] value {val!r} not a model class {MODEL_CLASSES}"
                )

    # spec_path: absent or null is fine (the emit_sprint_line slug fallback
    # covers it); anything else must be a string naming a .md file. Pure: the
    # path is never stat'd here, so a spec that does not exist yet is a
    # host-side concern (flow_transition.SpecMissingError), not a schema error.
    rc = flow.get("regrill_cleared")
    if rc is not None and not (isinstance(rc, list)
                               and all(isinstance(x, str) for x in rc)):
        errors.append("regrill_cleared must be a list of sprint id strings")

    spec_path = flow.get("spec_path")
    if spec_path is not None:
        if not isinstance(spec_path, str):
            errors.append(
                f"spec_path must be null or a string ending in '.md', got {spec_path!r}"
            )
        elif not spec_path.endswith(".md"):
            errors.append(f"spec_path {spec_path!r} must end in '.md'")

    errors += _validate_gate(flow.get("gate"))
    return errors
