"""gate_watch.py - read-only scan for decided-but-unconsumed gates.

Spec: (design spec, not shipped)

flow_transition.advance() consumes a decided gate the moment it runs, but
nothing calls it automatically, so a settled decision can age indefinitely with
zero visible signal. This module makes that condition visible and does nothing
else: it NEVER advances a flow, NEVER decides or consumes a gate, NEVER writes
flow.json or flow-log.ndjson, and NEVER launches a sprint.

Auto-consuming a decided gate is foreman_plan's gate_consume action, executed by
the Foreman driver (slice b, not built yet). Once that driver runs, this module
duplicates its detection and should be retired rather than kept running in
parallel (spec "Explicitly out of scope").

Pure-function core, thin host: find_unconsumed_gates does no I/O and never calls
the clock, so it returns the raw decided_at stamp and main() computes the age.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

from typing import List

# The gate ids this scan routes on. Identical to
# foreman_plan._RECOGNISED_GATE_IDS, which carries G4 ahead of flow_state.
# Redeclared rather than imported so this module stays out of foreman_plan's
# import chain; test_gate_watch asserts the two agree, so drift is caught.
RECOGNISED_GATE_IDS = ("G1", "G2", "G3", "G4")


def _verdict_of(gate: dict) -> str:
    return ((gate or {}).get("decision") or {}).get("verdict") or ""


def find_unconsumed_gates(flows: List[dict]) -> List[dict]:
    """Return one entry per flow whose gate is decided and not yet consumed.

    Args:
        flows: the list terminal_sweep.discover_flows() returns, i.e.
            [{"flow": <flow.json dict or None>, "log": [...], "flow_dir": ...}].

    Returns:
        [{"project", "slug", "gate_id", "verdict", "decided_at"}], one per
        unconsumed gate, in discovery order. decided_at is the raw stamp: this
        function never calls the clock, so the host owns age.

    A malformed gate (no decision block, an empty verdict, or an unrecognised
    id) is skipped rather than raised on, and the skip rule matches
    foreman_plan._malformed_gate_reason for decided gates.
    """
    out = []
    for ctx in flows or []:
        flow = (ctx or {}).get("flow") or {}
        gate = flow.get("gate")
        if not isinstance(gate, dict):
            continue
        if gate.get("status") != "decided":
            continue
        if gate.get("id") not in RECOGNISED_GATE_IDS:
            continue
        verdict = _verdict_of(gate)
        if not verdict:
            continue
        out.append({
            "project": flow.get("project", ""),
            "slug": flow.get("slug", ""),
            "gate_id": gate.get("id"),
            "verdict": verdict,
            "decided_at": (gate.get("decision") or {}).get("decided_at", ""),
        })
    return out


# =============================================================================
# Thin host: format_age() / main()
#
# The ONLY part of this module that does I/O or calls the clock. It reuses
# terminal_sweep.discover_flows rather than re-walking the tree. It prints and
# nothing else: no writes, no transitions, no email, no sprint.
# =============================================================================

import argparse
import datetime as _dt
import sys

import flow_state as fs
import terminal_sweep as ts

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(ts_str):
    try:
        return _dt.datetime.strptime(ts_str, _TS_FMT)
    except (TypeError, ValueError):
        return None


def format_age(decided_at: str, now: str) -> str:
    """'2h05m' between two ISO-Z stamps, or 'age-unknown' if that is not
    computable (unparsable stamp, or a decision stamped in the future)."""
    then, current = _parse_ts(decided_at), _parse_ts(now)
    if then is None or current is None:
        return "age-unknown"
    seconds = int((current - then).total_seconds())
    if seconds < 0:
        return "age-unknown"
    return "{}h{:02d}m".format(seconds // 3600, (seconds % 3600) // 60)


def main(argv=None) -> int:
    """Entrypoint for refresh_and_notify.cmd.

    ALWAYS exits 0: visibility tooling must never fail a batch job on a
    transient read error, mirroring terminal_sweep's per-flow isolation.
    """
    parser = argparse.ArgumentParser(
        description="List flows whose gate is decided but not yet consumed by "
                    "/flow advance. Read-only: never advances a flow, never "
                    "consumes a gate, never writes flow.json.")
    parser.add_argument("--flows-root", default=ts.DEFAULT_FLOWS_ROOT)
    args = parser.parse_args(argv)
    now = fs.iso_utc_now()
    try:
        pending = find_unconsumed_gates(ts.discover_flows(args.flows_root))
    except Exception as exc:  # never fail the batch job
        print("gate_watch: scan failed: {}".format(exc), file=sys.stderr)
        return 0
    for entry in pending:
        print("gate_watch: {}/{} {} decided verdict={} unconsumed for {} "
              "(run: /flow advance {}/{})".format(
                  entry["project"], entry["slug"], entry["gate_id"],
                  entry["verdict"], format_age(entry["decided_at"], now),
                  entry["project"], entry["slug"]))
    print("gate_watch: unconsumed={}".format(len(pending)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
