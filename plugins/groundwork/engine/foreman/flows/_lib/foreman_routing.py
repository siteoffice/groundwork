"""foreman_routing.py - model routing across the whole Foreman surface (slice f).

Spec: (design spec, not shipped),
section "Slice f: model routing across the whole surface".

Resolves, for every Foreman role (the 7 /sprint phases plus the driver-side
roles), which model class it runs under and why: a table default, a per-flow
hint (flow.json's model_hint), or a per-invocation CLI flag. Six roles never
call a model at all and resolve to the literal class "none", which is not in
model_map.VALID_CLASSES and never passes through model_map.resolve_model.

This file's pure section (ROLE_CLASS, NONE_CLASS, ROLES, Routed, parse_hint,
resolve_table) is Task 1 of the routing sprint: no I/O, no clock, every input
an argument. The host/CLI section (render_table, _load_flow_for_ref,
_build_arg_parser, main) is Task 2, read-only: it prints a table and writes
nothing (no flow.json, no foreman.json, no flow-log entry).

Reads sprints/_lib/model_map.CLASS_TO_MODEL and model_map.DEFAULT_TABLE live
(via **mm.DEFAULT_TABLE at import time and mm.resolve_model at call time);
never redefines or copies either by value. One table, three consumers.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys
from typing import Dict, Optional

# flows/_lib and sprints/_lib are flat sibling directories with no __init__.py,
# so importing model_map needs the sibling _lib dir on sys.path. Deliberate
# workaround for the missing package structure, not a fix. Matches
# flow_state.py's own bootstrap.
_SPRINT_LIB = pathlib.Path(__file__).resolve().parents[2] / "sprints" / "_lib"
if str(_SPRINT_LIB) not in sys.path:
    sys.path.insert(0, str(_SPRINT_LIB))

import model_map as mm  # noqa: E402  (must follow the sys.path bootstrap)

# --- contracts ----------------------------------------------------------------

NONE_CLASS = "none"

ROLE_CLASS: Dict[str, str] = {
    # the 7 /sprint phases: identical to model_map.DEFAULT_TABLE, not copied by
    # value at import time in case DEFAULT_TABLE ever changes; read live.
    **mm.DEFAULT_TABLE,
    "plan_next_action": "none",
    "render_status": "none",
    "render_guidance": "none",
    "ship_preflight": "none",
    "hub_refresh": "none",
    "idea_shape": "fable",
    "breakdown": "opus",
    "grill": "opus",
    "spec_lint_fix": "sonnet",
    "ship_review": "opus",
    "learn_summarise": "sonnet",
    "hub_ingest": "sonnet",
    "slack_compose": "none",
}

ROLES = tuple(sorted(ROLE_CLASS))

_VALID_HINT_VALUES = mm.VALID_CLASSES | {NONE_CLASS}


@dataclasses.dataclass(frozen=True)
class Routed:
    role: str
    cls: str  # a model_map.VALID_CLASSES member, or NONE_CLASS
    source: str  # "default" | "hint" | "flag"
    model: Optional[str]  # model_map.resolve_model(cls) result, or None when cls is NONE_CLASS


def _check_hint(hint: Dict[str, str], label: str) -> None:
    for role, cls in hint.items():
        if role not in ROLES:
            raise ValueError(f"unknown role in {label}: {role!r}")
        if cls not in _VALID_HINT_VALUES:
            raise ValueError(f"unknown model class in {label}: {cls!r}")


def resolve_table(model_hint: dict, cli_hint: Optional[dict] = None) -> Dict[str, Routed]:
    """Resolve every role in ROLES to a Routed record.

    Pure: no I/O, no flow object, just the two dicts already loaded by the
    caller. Precedence per role, flag beats hint beats default:

    1. cli_hint names the role: source="flag".
    2. else model_hint names the role: source="hint".
    3. else ROLE_CLASS[role]: source="default".

    Raises ValueError on any key in model_hint or cli_hint that is not in
    ROLES, and on any value that is not in model_map.VALID_CLASSES |
    {NONE_CLASS}. resolve_model is never called for a role whose resolved
    class is NONE_CLASS; Routed.model is None in that case, never a guess.
    """
    model_hint = model_hint or {}
    cli_hint = cli_hint or {}
    _check_hint(model_hint, "model_hint")
    _check_hint(cli_hint, "cli_hint")

    result: Dict[str, Routed] = {}
    for role in ROLES:
        if role in cli_hint:
            cls, source = cli_hint[role], "flag"
        elif role in model_hint:
            cls, source = model_hint[role], "hint"
        else:
            cls, source = ROLE_CLASS[role], "default"
        model = None if cls == NONE_CLASS else mm.resolve_model(cls)
        result[role] = Routed(role=role, cls=cls, source=source, model=model)
    return result


def parse_hint(spec: str) -> Dict[str, str]:
    """Parse "role=class,role=class" into a role->class dict.

    Same grammar as model_map.parse, checked against ROLES and
    model_map.VALID_CLASSES | {NONE_CLASS} instead of the 7-phase table, so a
    typo'd role name on the command line fails exactly as loudly as a typo'd
    one in model_hint.
    """
    result: Dict[str, str] = {}
    if not spec:
        return result
    for raw_pair in spec.split(","):
        pair = raw_pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"invalid hint entry (expected role=class): {pair!r}")
        role, cls = (part.strip() for part in pair.split("=", 1))
        if role not in ROLES:
            raise ValueError(f"unknown role in hint: {role!r}")
        if cls not in _VALID_HINT_VALUES:
            raise ValueError(f"unknown model class in hint: {cls!r}")
        result[role] = cls
    return result


# =============================================================================
# Thin host: render_table(), _load_flow_for_ref() and the CLI.
#
# This section is the ONLY part of the module that touches the filesystem. It
# is read-only, matching foreman_status.py's own read-only host section: it
# opens flow.json for reading only, never writes flow.json, foreman.json or a
# flow-log entry, and never takes the flow lock (there is nothing to protect).
# =============================================================================

import argparse

import foreman_status as fst
import flow_transition as ftr

_TABLE_HEADERS = ("ROLE", "CLASS", "MODEL", "SOURCE")


def render_table(routed: Dict[str, "Routed"]) -> str:
    """Plain-text table, one row per ROLES entry: ROLE, CLASS, MODEL, SOURCE.

    The CLASS column always prints the literal class string, so a "none"
    role prints the word "none", never a dash or a blank (named question 2).
    The SOURCE column always prints one of "default", "hint" or "flag", so a
    role's provenance is visibly derivable straight off the printed table,
    not something a reader has to go check the source code for: a hinted
    role's row reads "hint" where a defaulted role's reads "default".
    """
    rows = []
    for role in ROLES:
        r = routed[role]
        model = r.model if r.model is not None else "None"
        rows.append((role, r.cls, model, r.source))
    widths = [max(len(_TABLE_HEADERS[i]), max(len(row[i]) for row in rows))
              for i in range(len(_TABLE_HEADERS))]
    lines = ["  ".join(_TABLE_HEADERS[i].ljust(widths[i])
                        for i in range(len(widths))).rstrip()]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i])
                                for i in range(len(widths))).rstrip())
    return "\n".join(lines)


def _load_flow_for_ref(ref: str, flows_root, sprints_root) -> dict:
    """Resolve `ref` the same way `foreman why <ref>` does (Interpretation
    decision 6): collect the board via foreman_status.collect, match `ref`
    through foreman_status.resolve_ref, then read flow.json from the matched
    flow_dir. Raises LookupError exactly as resolve_ref does on an ambiguous
    or missing ref, naming the candidates or the absence of a match; that is
    the caller's job to catch, not this function's.

    Read-only: opens flow.json only, no flow-log, no foreman.json, no lock.
    """
    statuses = fst.collect(flows_root, sprints_root)
    status = fst.resolve_ref(statuses, ref)
    return ftr.load_flow(status.flow_dir)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foreman_routing.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_routing = sub.add_parser(
        "routing", help="Print the model routing table for one flow")
    p_routing.add_argument(
        "ref", help="project/slug, a unique slug, or a unique slug substring")
    p_routing.add_argument(
        "--hint", default="",
        help="role=class,role=class... per-invocation override; never "
             "written to flow.json or foreman.json")
    p_routing.add_argument("--flows-root", default=fst.DEFAULT_FLOWS_ROOT)
    p_routing.add_argument("--sprints-root", default=fst.DEFAULT_SPRINTS_ROOT)

    return parser


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.cmd == "routing":
        try:
            flow = _load_flow_for_ref(args.ref, args.flows_root, args.sprints_root)
        except LookupError as exc:
            print(str(exc))
            return 1
        try:
            cli_hint = parse_hint(args.hint)
            table = resolve_table(flow.get("model_hint") or {}, cli_hint)
        except ValueError as exc:
            print(str(exc))
            return 1
        print(render_table(table))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
