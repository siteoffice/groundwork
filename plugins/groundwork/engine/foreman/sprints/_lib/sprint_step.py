"""Single write path for /sprint boundary bookkeeping.

Before this script, every phase or task boundary required the orchestrator
to do four separate things by hand: append an action-log.ndjson line,
overwrite sprint.json, regenerate both dashboards, and print a status block
to the CLI. Four chances to skip one of them; skipping the print is exactly
what makes /sprint go silent for hours during a long dev phase (see
docs/superpowers/2026-07-31-sprint-visibility-proposal.md). This script does
all four as one atomic call, so the print cannot be separated from the write.

Reads/writes the schema in ./schema.md. Stdlib only. Python 3.8+.

CLI:
    python sprint_step.py <sprint_dir> --phase <phase> --action <action> \
      --actor <actor> --detail "<text>" [--outcome pass|fail|info|blocked] \
      [--task N] [--model <class>] [--commit <sha>] [--ref <ref>] \
      [--iteration N] [--status <status>] [--tail N]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Any, Dict, Optional

import render_dashboard

VALID_ACTIONS = {
    "sprint_start", "phase_enter", "phase_exit", "dispatch", "decision",
    "test_run", "verify_run", "iterate", "cap_hit", "escalate", "sprint_done",
    "task_start", "task_review", "task_fix", "task_done",
}
TASK_ACTIONS = {"task_start", "task_review", "task_fix", "task_done"}
VALID_OUTCOMES = {"pass", "fail", "info", "blocked"}
MAX_DETAIL_LEN = 240


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_detail(detail: str) -> None:
    if "\n" in detail or "\r" in detail:
        raise ValueError(f"detail must not contain newlines: {detail!r}")
    if len(detail) > MAX_DETAIL_LEN:
        raise ValueError(
            f"detail exceeds {MAX_DETAIL_LEN} chars ({len(detail)}): {detail!r}"
        )


def _find_task(sprint: Dict[str, Any], task_n: int) -> Dict[str, Any]:
    tasks = sprint.get("tasks")
    if not tasks:
        raise ValueError("action requires --task but sprint.json has no tasks[]")
    for t in tasks:
        if t.get("n") == task_n:
            return t
    raise ValueError(f"no task with n={task_n} in sprint.json tasks[]")


def apply_step(
    sprint_dir: pathlib.Path,
    *,
    phase: str,
    action: str,
    actor: str,
    detail: str,
    outcome: str = "info",
    task: Optional[int] = None,
    model: Optional[str] = None,
    commit: Optional[str] = None,
    ref: Optional[str] = None,
    iteration: Optional[int] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Append the log entry, update sprint.json, regenerate both dashboards.

    Returns the updated sprint dict. Raises ValueError on any schema
    violation (unknown action/outcome, oversized/newline detail, a task
    action missing --task, or --task naming a task that doesn't exist).
    """
    sprint_dir = pathlib.Path(sprint_dir)
    sprint_path = sprint_dir / "sprint.json"
    sprint: Dict[str, Any] = json.loads(sprint_path.read_text(encoding="utf-8"))

    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action: {action!r} (known: {sorted(VALID_ACTIONS)})")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome!r} (known: {sorted(VALID_OUTCOMES)})")
    _validate_detail(detail)
    if action in TASK_ACTIONS and task is None:
        raise ValueError(f"action {action!r} requires --task")

    task_entry = _find_task(sprint, task) if task is not None else None

    ts = _now_iso()
    entry_iteration = iteration if iteration is not None else int(sprint.get("iteration") or 0)

    log_entry: Dict[str, Any] = {
        "ts": ts,
        "iteration": entry_iteration,
        "phase": phase,
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "detail": detail,
        "ref": ref,
    }
    if model:
        log_entry["model"] = model
    if task is not None:
        log_entry["task"] = task

    with (sprint_dir / "action-log.ndjson").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    sprint["phase"] = phase
    sprint["updated_at"] = ts
    if status is not None:
        sprint["status"] = status
    if iteration is not None:
        sprint["iteration"] = iteration

    if task_entry is not None:
        if action == "task_start":
            task_entry["state"] = "running"
            task_entry["attempts"] = int(task_entry.get("attempts") or 0) + 1
            task_entry["started_at"] = ts
            if model:
                task_entry["model"] = model
        elif action == "task_review":
            task_entry["state"] = "review"
        elif action == "task_fix":
            task_entry["state"] = "fixing"
        elif action == "task_done":
            task_entry["state"] = "done" if outcome == "pass" else "blocked"
            task_entry["ended_at"] = ts
            if commit:
                task_entry["commit"] = commit

    sprint_path.write_text(json.dumps(sprint, indent=2) + "\n", encoding="utf-8")

    render_dashboard.render_sprint(sprint_dir)
    render_dashboard.render_index(sprint_dir.parent.parent)

    return sprint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sprint_step.py")
    parser.add_argument("sprint_dir", type=pathlib.Path)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--outcome", default="info")
    parser.add_argument("--task", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--ref", default=None)
    parser.add_argument("--iteration", type=int, default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--tail", type=int, default=6)
    args = parser.parse_args(argv)

    apply_step(
        args.sprint_dir,
        phase=args.phase,
        action=args.action,
        actor=args.actor,
        detail=args.detail,
        outcome=args.outcome,
        task=args.task,
        model=args.model,
        commit=args.commit,
        ref=args.ref,
        iteration=args.iteration,
        status=args.status,
    )

    text = render_dashboard.render_status_text(args.sprint_dir, colour=False, tail=args.tail)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
