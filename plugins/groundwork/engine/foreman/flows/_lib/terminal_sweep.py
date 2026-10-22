"""terminal_sweep.py - terminal-state sweep that opens G3 automatically.

This module is the pure-core of the terminal-state sweep (spec:
2026-07-21-sprint-terminal-sweep-g3-design.md). It is DISTINCT from
sweep_core.py (the reply-sweep / escalation-ladder feature): different
responsibility, different module, no shared state.

Responsibilities:
- Match flows parked at execute/phase=sprint/gate=null to terminal sprint.json
  files via the spec_path recorded in the latest sprint_emitted receipt.
- Classify terminal sprints (success vs needs-guidance).
- Build the status-aware sweep_context payload for the G3 notification body.

Invariants (spec §Invariants, never violated):
- Never launches a sprint.
- Never sends email.
- Never writes sprint.json or anything under sprints/.
- Never mutates flow.json directly; all flow state changes go through
  flow_transition.cmd_advance (done in the thin host main(), not here).
- Stdlib only, Python 3.8+.
- Pure-function core: no I/O, no clock calls. The host main() does all I/O.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

TERMINAL_STATUSES: frozenset = frozenset({"passing", "done", "capped", "failed", "escalated"})
SUCCESS_STATUSES: frozenset = frozenset({"passing", "done"})
NEEDS_GUIDANCE_STATUSES: frozenset = frozenset({"capped", "failed", "escalated"})

_MODEL_MAP_FLAG = " --model-map "


def parse_emitted_spec_path(detail: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (project, spec_path) from a '/sprint <project> <spec> [--model-map ...]' line.

    spec_path may contain spaces (Windows paths), so everything between the
    project token and a trailing ' --model-map ' is captured as-is.
    Returns (None, None) if the line does not start with '/sprint'.
    """
    if not detail or not detail.startswith("/sprint "):
        return None, None
    rest = detail[len("/sprint "):].strip()
    if _MODEL_MAP_FLAG in rest:
        rest = rest.split(_MODEL_MAP_FLAG, 1)[0].strip()
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return None, None
    project, spec_path = parts[0], parts[1].strip()
    return (project or None), (spec_path or None)


def latest_sprint_emitted(log: list) -> Optional[dict]:
    """Return the most recent log entry with action=='sprint_emitted', else None."""
    emits = [e for e in (log or []) if e.get("action") == "sprint_emitted"]
    if not emits:
        return None
    return max(emits, key=lambda e: e.get("ts", ""))


def is_sweep_eligible(flow: dict) -> bool:
    """True iff the flow is at execute/phase=sprint/gate=null (sweep predicate)."""
    cs = flow.get("current_step") or {}
    return (
        flow.get("stage") == "execute"
        and cs.get("phase") == "sprint"
        and flow.get("gate") is None
    )


def match_terminal_sprints(
    flows: List[dict],
    sprints: List[dict],
    swept_index: Dict[str, Set[str]],
) -> List[Tuple[dict, dict]]:
    """For each eligible flow, find the best matching terminal sprint.

    Args:
        flows: list of flow context dicts: {"flow": <flow.json dict>, "log": [...]}
        sprints: list of sprint dicts (from sprint.json files)
        swept_index: {flow_id: set(sprint_ids)} of already-swept sprint ids

    Returns:
        list of (flow_dict, sprint_dict) pairs, at most one per flow.
        Applies: eligibility predicate, spec_path match from latest
        sprint_emitted receipt (falling back to the flow's current spec_path
        if that finds nothing, see below), terminal-status filter,
        not-already-swept check, launched-after-emit honesty check,
        most-recent tie-break.

    Corrected-spec_path fallback (2026-09-13, found via
    _packs/2026-09-12-s2-lane2-2-11-first-operator-pack): the emitted
    receipt's own /sprint line is the ground truth for WHICH sprint was
    launched, but flow['spec_path'] can be corrected afterward
    (flow_transition.set_spec_path, spec 2026-08-01 scope A) without the
    historical receipt text changing, an append-only log, correctly so. A
    sprint that was actually run against the corrected path still covers
    this flow's real spec, it is not a different flow's unrelated sprint, so
    when the emitted path matches nothing, this function retries once
    against the flow's current spec_path before giving up.
    """
    matches = []
    for ctx in flows:
        flow = ctx.get("flow") or {}
        if not is_sweep_eligible(flow):
            continue
        emitted = latest_sprint_emitted(ctx.get("log"))
        if emitted is None:
            continue
        _proj, emitted_spec_path = parse_emitted_spec_path(emitted.get("detail", ""))
        if not emitted_spec_path:
            continue
        emitted_ts = emitted.get("ts", "")
        swept = swept_index.get(flow.get("flow_id"), set())

        def _candidates(spec_path):
            return [
                s for s in sprints
                if s.get("project") == flow.get("project")
                and s.get("spec_path") == spec_path
                and s.get("status") in TERMINAL_STATUSES
                and s.get("sprint_id") not in swept
                and s.get("created_at", "") > emitted_ts
            ]

        candidates = _candidates(emitted_spec_path)
        if not candidates:
            current_spec_path = flow.get("spec_path")
            if current_spec_path and current_spec_path != emitted_spec_path:
                candidates = _candidates(current_spec_path)
        if not candidates:
            continue
        chosen = max(candidates, key=lambda s: s.get("created_at", ""))
        matches.append((flow, chosen))
    return matches


def has_terminal_sprint(project: str, spec_path: str,
                        sprints: List[dict]) -> Optional[dict]:
    """Return the most recent terminal sprint for (project, spec_path), else None.

    The relaunch-guard lookup: flow_transition.emit_sprint_line builds a launch
    line from flow.spec_path alone and has no visibility into
    sprints\\<project>\\*\\sprint.json, so a stale advance or a copy-paste of an
    old line can re-emit a launch for a spec whose sprint already finished. A
    caller that already loaded `sprints` from disk passes the result of this
    lookup into emit_sprint_line, which then refuses instead of emitting.

    Pure: reuses TERMINAL_STATUSES and the caller's already-loaded `sprints`
    list, so it does no I/O of its own. Unlike match_terminal_sprints this
    applies NO emit-receipt, swept-index or launched-after-emit filtering: the
    question here is only "did a sprint for this spec already finish", which is
    the question a relaunch is wrong about.
    """
    if not project or not spec_path:
        return None
    candidates = [s for s in sprints or []
                  if s.get("project") == project
                  and s.get("spec_path") == spec_path
                  and s.get("status") in TERMINAL_STATUSES]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.get("created_at", ""))


def classify_terminal(sprint: dict) -> str:
    """Return 'success' for passing/done, 'needs-guidance' for capped/failed/escalated."""
    return "success" if sprint.get("status") in SUCCESS_STATUSES else "needs-guidance"


def _summary(block) -> str:
    """Extract .summary string from a last_verify_result / last_test_result block."""
    return (block or {}).get("summary", "") if isinstance(block, dict) else ""


def build_sweep_context(sprint: dict) -> dict:
    """Build the status-aware sweep_context payload for the G3 notification body.

    Returns a dict with: status, class, verify_summary, test_summary,
    suggested_verbs. For needs-guidance sprints the verbs steer toward
    adapt/close; for success sprints toward proceed.
    """
    klass = classify_terminal(sprint)
    if klass == "needs-guidance":
        suggested = ["adapt: <note>", "close",
                     "(proceed available but not suggested)"]
    else:
        suggested = ["proceed"]
    return {
        "status": sprint.get("status"),
        "class": klass,
        "verify_summary": _summary(sprint.get("last_verify_result")),
        "test_summary": _summary(sprint.get("last_test_result")),
        "suggested_verbs": suggested,
    }


# --- diagnostics: why did a sweep-eligible flow not match? ------------------

# Reason codes in the spec's priority order. The first one that applies wins.
DIAGNOSTIC_REASONS = ("no_emit_receipt", "no_matching_sprint", "not_terminal",
                      "predates_emit")


def _diag_row(flow: dict, reason: str, detail: str) -> dict:
    return {"project": flow.get("project", ""), "slug": flow.get("slug", ""),
            "flow_id": flow.get("flow_id"), "reason": reason, "detail": detail}


def _diagnose_flow(flow: dict, log: list, sprints: List[dict]) -> dict:
    """Classify one unmatched sweep-eligible flow, in the spec's priority order."""
    emitted = latest_sprint_emitted(log)
    if emitted is None:
        return _diag_row(flow, "no_emit_receipt",
                         "flow-log.ndjson carries no sprint_emitted entry, so "
                         "there is no spec_path to match on")
    _proj, spec_path = parse_emitted_spec_path(emitted.get("detail", ""))
    if not spec_path:
        return _diag_row(flow, "no_emit_receipt",
                         "the sprint_emitted entry at {} carries no parsable "
                         "/sprint line".format(emitted.get("ts", "")))

    by_spec = [s for s in sprints
               if s.get("project") == flow.get("project")
               and s.get("spec_path") == spec_path]
    if not by_spec:
        # Same corrected-spec_path fallback as match_terminal_sprints: a
        # spec_path correction after the emit receipt was logged leaves the
        # receipt's text stale, so retry against the flow's current field
        # before reporting no match at all.
        current_spec_path = flow.get("spec_path")
        if current_spec_path and current_spec_path != spec_path:
            retry = [s for s in sprints
                     if s.get("project") == flow.get("project")
                     and s.get("spec_path") == current_spec_path]
            if retry:
                by_spec = retry
                spec_path = current_spec_path
    if not by_spec:
        return _diag_row(flow, "no_matching_sprint",
                         "no sprint.json under project {} carries spec_path "
                         "{}".format(flow.get("project", ""), spec_path))

    terminal = [s for s in by_spec if s.get("status") in TERMINAL_STATUSES]
    if not terminal:
        newest = max(by_spec, key=lambda s: s.get("created_at", ""))
        return _diag_row(flow, "not_terminal",
                         "sprint {} for that spec has status {}, which is not "
                         "terminal".format(newest.get("sprint_id"),
                                           newest.get("status")))

    newest = max(terminal, key=lambda s: s.get("created_at", ""))
    return _diag_row(flow, "predates_emit",
                     "terminal sprint {} was created at {}, which is not after "
                     "the sprint_emitted receipt at {}, so it is a stale run of "
                     "an earlier launch".format(newest.get("sprint_id"),
                                               newest.get("created_at", ""),
                                               emitted.get("ts", "")))


def diagnose_unmatched(flows: List[dict], sprints: List[dict]) -> List[dict]:
    """Explain, per sweep-eligible flow, why it matched no terminal sprint.

    sweep_once reports only aggregate counts, so a wrong-or-missing
    precondition reads identical to "nothing to do". This names which
    precondition is missing for each flow that produced no match.

    Returns [{"project", "slug", "flow_id", "reason", "detail"}], one per
    unmatched eligible flow, reason drawn from DIAGNOSTIC_REASONS. A flow with
    a genuine match produces no entry.

    Pure and read-only: no I/O, no clock, nothing written to a receipt or to
    flow-log.ndjson. An empty swept_index is passed to match_terminal_sprints
    because an already-swept flow is no longer sweep-eligible (its phase is
    learn and G3 is open), so it cannot hide a real unmatched flow.
    """
    sprints = sprints or []
    matched_ids = {flow.get("flow_id")
                   for flow, _sprint in match_terminal_sprints(flows or [],
                                                               sprints, {})}
    rows = []
    for ctx in flows or []:
        flow = (ctx or {}).get("flow") or {}
        if not is_sweep_eligible(flow):
            continue
        if flow.get("flow_id") in matched_ids:
            continue
        rows.append(_diagnose_flow(flow, (ctx or {}).get("log") or [], sprints))
    return rows


# =============================================================================
# Thin host: main() / sweep_once() / apply_match() / discover_*() / build_swept_index()
#
# This section is the ONLY part of the module that does I/O or calls the clock.
# It drives the gate/step engine ONLY through flow_transition.cmd_advance /
# outbox.append_gate_open; it never mutates flow.json directly and never
# reimplements a transition. It never launches a sprint, never sends email,
# and never writes anything under sprints\ (it only reads sprint.json).
# =============================================================================

import argparse
import json
import pathlib
import sys

import flow_state as fs
import flow_transition as ft
import foreman_lock
import outbox
import roots

DEFAULT_FLOWS_ROOT = str(roots.flows_root())
DEFAULT_SPRINTS_ROOT = str(roots.sprints_root())


def _read_ndjson(path: pathlib.Path) -> list:
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def discover_flows(flows_root) -> list:
    """Walk flows_root/<project>/<slug>/, loading flow.json + flow-log.ndjson.

    Malformed entries are skipped, not fatal: a flow whose flow.json fails to
    parse is still returned (flow=None, load_error=True) so a caller can log a
    per-flow error, but it can never match (is_sweep_eligible({}) is False).
    """
    root = pathlib.Path(flows_root)
    flows = []
    if not root.exists():
        return flows
    for fj in root.glob("*/*/flow.json"):
        flow_dir = fj.parent
        try:
            flow = json.loads(fj.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            flows.append({"flow": None, "log": [], "flow_dir": flow_dir,
                         "load_error": True})
            continue
        flows.append({"flow": flow,
                     "log": _read_ndjson(flow_dir / "flow-log.ndjson"),
                     "flow_dir": flow_dir})
    return flows


def discover_sprints(sprints_root) -> list:
    """Walk sprints_root/<project>/<slug>/sprint.json. Malformed entries skipped.

    Read-only: this function (and this module) never writes under sprints\\.
    """
    root = pathlib.Path(sprints_root)
    sprints = []
    if not root.exists():
        return sprints
    for sj in root.glob("*/*/sprint.json"):
        try:
            sprints.append(json.loads(sj.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return sprints


def build_swept_index(flows) -> dict:
    """{flow_id: set(sprint_ids)} built from each flow's sprint_swept receipts."""
    index: dict = {}
    for ctx in flows:
        flow = ctx.get("flow") or {}
        fid = flow.get("flow_id")
        if not fid:
            continue
        swept = {e.get("ref") for e in ctx.get("log", [])
                 if e.get("action") == "sprint_swept" and e.get("ref")}
        if swept:
            index[fid] = swept
    return index


def _apply_match_body(flow_dir, sprint: dict, now: str) -> dict:
    """The crossing itself, with no locking of its own. One implementation,
    two callers: apply_match below takes the lock for the scheduled sweep,
    and foreman_run.execute_recover calls this through apply_match(lock=False)
    because execute_once already holds that same lock and foreman_lock.acquire
    is not re-entrant.
    """
    # Slice c (spec 2026-08-17 items 6 and 9) gated this boundary: one
    # cmd_advance at phase sprint now OPENS G4 rather than moving to learn, so
    # a second call would raise "cannot advance: gate awaiting decision". The
    # conditional second call keeps this correct for any boundary that is
    # still gate-free.
    flow, _sprint_line = ft.cmd_advance(flow_dir, now)
    if not isinstance(flow.get("gate"), dict):
        flow, _sprint_line = ft.cmd_advance(flow_dir, now)
    if isinstance(flow.get("gate"), dict):
        flow["gate"]["sweep_context"] = build_sweep_context(sprint)
    outbox.append_gate_open(flow_dir, flow, now)  # queue only, never send

    ft.append_log(flow_dir, ft._log_entry(
        now, flow.get("stage", ""), "orchestrator", "sprint_swept",
        outcome="info",
        detail=f"{sprint.get('sprint_id')} status={sprint.get('status')}",
        ref=sprint.get("sprint_id")))
    return {"skipped": False, "sprint_id": sprint.get("sprint_id")}


def apply_match(flow_dir, sprint: dict, now=None, lock: bool = True) -> dict:
    """Pre-write re-assert + the spec section-3 sequence for one match.

    Returns {"skipped": bool, "sprint_id": ...}. Never launches a sprint,
    never sends email, never writes sprint.json. Drives the gate/step engine
    ONLY through flow_transition.cmd_advance / outbox.append_gate_open.

    `lock=False` skips the acquire because the caller already holds this
    flow's lock (foreman_run.execute_once does, around every executor).
    foreman_lock.acquire is not re-entrant and LockBusy is a skip here, so a
    nested default call would silently do nothing and look like a success.
    """
    now = now or fs.iso_utc_now()
    current = ft.load_flow(flow_dir)
    if not is_sweep_eligible(current):
        # Pre-write eligibility re-assert (spec safety guard): an in-session
        # advance opened G3 (or moved the step) in the window between
        # discovery and write. No-op this flow; no double advance.
        return {"skipped": True, "sprint_id": sprint.get("sprint_id")}

    if not lock:
        return _apply_match_body(flow_dir, sprint, now)

    # The advisory flow lock (Foreman slice b). Two cmd_advance calls in a row
    # is exactly the sequence the driver must not interleave with. LockBusy is
    # a skip, not an error: the sweep runs every 10 minutes and matches again.
    try:
        with foreman_lock.acquire(flow_dir, "terminal-sweep", now=now,
                                  stage=current.get("stage", "")):
            return _apply_match_body(flow_dir, sprint, now)
    except foreman_lock.LockBusy:
        return {"skipped": True, "sprint_id": sprint.get("sprint_id"),
                "reason": "lock_busy"}


def sweep_once(flows_root, sprints_root, now=None, dry_run=False) -> dict:
    """Build inputs from disk, match, and apply (or, if dry_run, print only).

    Returns {"matched": n, "swept": [sprint_id, ...], "errors": [...], "dry_run": bool}.
    """
    now = now or fs.iso_utc_now()
    flows = discover_flows(flows_root)
    sprints = discover_sprints(sprints_root)
    swept_index = build_swept_index(flows)
    matches = match_terminal_sprints(flows, sprints, swept_index)
    dir_of = {(c["flow"] or {}).get("flow_id"): c["flow_dir"]
             for c in flows if c.get("flow")}

    swept, errors = [], []
    for flow, sprint in matches:
        flow_dir = dir_of.get(flow.get("flow_id"))
        if dry_run:
            klass = classify_terminal(sprint)
            print(f"[dry-run] would sweep {flow.get('project')}/"
                  f"{flow.get('slug')} <- {sprint.get('sprint_id')} "
                  f"status={sprint.get('status')} class={klass}")
            swept.append(sprint.get("sprint_id"))
            continue
        try:
            res = apply_match(flow_dir, sprint, now)
            if not res["skipped"]:
                swept.append(res["sprint_id"])
        except Exception as exc:  # per-flow isolation (spec safety guard)
            errors.append({"flow_id": flow.get("flow_id"), "error": str(exc)})
            try:
                ft.append_log(flow_dir, ft._log_entry(
                    now, flow.get("stage", ""), "orchestrator", "sweep_error",
                    outcome="blocked", detail=str(exc),
                    ref=sprint.get("sprint_id")))
            except Exception:
                pass

    return {"matched": len(matches), "swept": swept, "errors": errors,
            "dry_run": dry_run}


def main(argv=None) -> int:
    """Entrypoint for refresh_and_notify.cmd. Exit 0 as long as the tree walk
    completed (even if some per-flow matches errored); non-zero only on a
    total walk failure."""
    parser = argparse.ArgumentParser(
        description="Terminal-sprint sweep: open G3 for flows whose step's "
                    "sprint reached a terminal state. Never launches a sprint, "
                    "never sends email, never writes sprint.json.")
    parser.add_argument("--flows-root", default=DEFAULT_FLOWS_ROOT)
    parser.add_argument("--sprints-root", default=DEFAULT_SPRINTS_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diagnose", action="store_true",
                        help="read-only: name the missing precondition for "
                             "every sweep-eligible flow that matched no "
                             "terminal sprint, then exit. No sweep runs and "
                             "nothing is written.")
    args = parser.parse_args(argv)
    if args.diagnose:
        try:
            rows = diagnose_unmatched(discover_flows(args.flows_root),
                                      discover_sprints(args.sprints_root))
        except Exception as exc:  # total walk failure only
            print("terminal_sweep: diagnose failed: {}".format(exc),
                  file=sys.stderr)
            return 1
        for row in rows:
            print("terminal_sweep: unmatched {}/{} reason={} {}".format(
                row["project"], row["slug"], row["reason"], row["detail"]))
        print("terminal_sweep: unmatched={}".format(len(rows)))
        return 0
    try:
        summary = sweep_once(args.flows_root, args.sprints_root,
                             dry_run=args.dry_run)
    except Exception as exc:  # total walk failure only
        print(f"terminal_sweep: total failure: {exc}", file=sys.stderr)
        return 1
    print(f"terminal_sweep: matched={summary['matched']} "
          f"swept={len(summary['swept'])} errors={len(summary['errors'])} "
          f"dry_run={summary['dry_run']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
