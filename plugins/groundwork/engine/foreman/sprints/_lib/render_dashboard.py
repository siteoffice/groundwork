"""Thin caller for /sprint dashboards (Siteoffice fold, sprint 1 step 12).

The HTML itself now comes from board.render_board's sprints board kind, not
this file's own templates (see the import below): render_sprint(sprint_dir)
and render_index(sprints_root) delegate and return the new _board\\ path.
compute_progress and compute_stats stay here as pure functions, still used
by the text-mode status/tail/watch CLI below, which is unchanged.

Stdlib only. Python 3.8+.

CLI:
    python render_dashboard.py sprint  <sprint_dir>
    python render_dashboard.py index   <sprints_root>
    python render_dashboard.py status  <sprint_dir> [--watch SECONDS]
    python render_dashboard.py tail    <sprint_dir> [--follow]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

# Siteoffice fold (sprint 1 step 12, plan scope item 8, decision 6): the HTML
# dashboard now comes from the Siteoffice board renderer's sprints board kind,
# not from this file's own templates. Found by walking up from this file until
# a "projects/siteoffice" sibling turns up (same pattern znd_os_theme.py uses
# for board.theme), so nothing is hard-coded to one drive letter or to a fixed
# parent-directory depth: a fixed depth breaks the moment this file is
# checked out one level deeper than usual, which a /sprint git worktree under
# 10_holding\.worktrees\ does on every run.
# The repo root itself (this file three levels up, holding a board folder,
# as in the siteoffice-private checkout and its CI runner) is tried first;
# the walk-up search is the fallback.
_HERE = pathlib.Path(__file__).resolve().parent


def _find_siteoffice_root(start: pathlib.Path) -> pathlib.Path:
    direct = start.parents[2]  # _lib -> sprints -> foreman -> siteoffice
    if (direct / "board").is_dir():
        return direct
    for candidate in (start, *start.parents):
        probe = candidate / "50_projects" / "siteoffice"
        if probe.is_dir():
            return probe
    raise RuntimeError(
        "could not find a projects\\siteoffice sibling above " + str(start))


_SITEOFFICE_ROOT = _find_siteoffice_root(_HERE)
if str(_SITEOFFICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SITEOFFICE_ROOT))
from board import render_board as _board  # noqa: E402

# ---------------------------------------------------------------------------
# Constants (source of truth: sprints/_lib/schema.md)
# ---------------------------------------------------------------------------

PHASE_WEIGHTS = {
    "spec": 5, "plan": 10, "dev": 40, "test": 15, "iterate": 25, "report": 5,
}
PHASE_ORDER = ["spec", "plan", "dev", "test", "iterate", "report"]
# `done` is a terminal phase; not weighted, always renders as complete.
ALL_PHASES = PHASE_ORDER + ["done"]

TERMINAL_STATUSES = {"passing", "capped", "failed", "escalated"}

# Siteoffice fold (step 12): the HTML-only colour maps, phase icons and
# communication-action set moved to board\render_board.py with the page
# they used to serve (STATUS_COLORS, OUTCOME_COLORS, TASK_STATE_COLORS,
# PHASE_ICONS, COMMUNICATION_ACTIONS, LIVE_REFRESH_SECONDS). TASK_STATE_ORDER
# stays: the text-mode `status`/`tail` CLI still uses it (_text_tasks below).
TASK_STATE_ORDER = ["running", "review", "fixing", "blocked", "pending"]


# ---------------------------------------------------------------------------
# Progress math (unchanged public API)
# ---------------------------------------------------------------------------


def compute_progress(sprint: dict) -> int:
    """Return an integer 0..100 per the fixed phase-weight table.

    Capped at 99 while `status == "running"`, exactly 100 when `phase == "done"`.
    The `dev` phase's fractional term is `tasks_done / tasks_total` when
    `sprint["tasks"]` is a non-empty list (schema_version 2); every other
    phase, and a `dev` phase with no/empty `tasks`, uses the older
    `iteration / max_iterations` estimate (pre-v2 fallback).
    """
    if sprint.get("phase") == "done":
        return 100
    phase = sprint.get("phase", "spec")
    completed = 0
    for p in PHASE_ORDER:
        if p == phase:
            break
        completed += PHASE_WEIGHTS[p]
    current_weight = PHASE_WEIGHTS.get(phase, 0)
    tasks = sprint.get("tasks")
    if phase == "dev" and tasks:
        tasks_total = len(tasks)
        tasks_done = sum(1 for t in tasks if t.get("state") == "done")
        fractional = int(round((tasks_done / tasks_total) * current_weight))
    else:
        iteration = int(sprint.get("iteration") or 0)
        max_it = max(1, int(sprint.get("max_iterations") or 1))
        fractional = int(round((iteration / max_it) * current_weight))
    total = completed + fractional
    if sprint.get("status") == "running":
        return min(total, 99)
    return min(total, 100)


# ---------------------------------------------------------------------------
# Log ingestion
# ---------------------------------------------------------------------------


def _read_log(sprint_dir: pathlib.Path) -> List[dict]:
    """Parse action-log.ndjson. Malformed lines are skipped, not fatal."""
    log_path = sprint_dir / "action-log.ndjson"
    if not log_path.exists():
        return []
    entries: List[dict] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


# ---------------------------------------------------------------------------
# Derived stats (new)
# ---------------------------------------------------------------------------


def _parse_ts(ts: Optional[str]) -> Optional[_dt.datetime]:
    """Parse an ISO 8601 timestamp with trailing Z. Return None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    ts_clean = ts.strip()
    if ts_clean.endswith("Z"):
        ts_clean = ts_clean[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(ts_clean)
    except (ValueError, TypeError):
        return None


def _format_duration(seconds: float) -> str:
    """Render a duration like '2h 15m 03s' or '45s'. Negative -> '0s'."""
    if seconds is None or seconds < 0:
        return "0s"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def compute_stats(sprint: dict, log: List[dict]) -> dict:
    """Compute derived per-sprint stats used by the dashboard.

    Returns a dict with:
      - total_duration_s: float seconds from earliest to latest entry
      - phase_durations: {phase: seconds} (running total across iterations)
      - actor_counts: {actor: {"total": int, "pass": int, "fail": int, "info": int, "blocked": int}}
      - outcome_counts: {"pass": int, "fail": int, ...}
      - iterations: list of per-iteration summaries {iteration, test_outcome, verify_outcome, entries}
      - last_error: str | None (most recent fail detail)
      - is_terminal: bool
      - completed_phases: list[str] (phases with an observed phase_exit event or that appear before current)
      - phase_models: {phase: {model: count}}, built from the optional
        "model" field on log entries; entries without it are skipped
    """
    stats: Dict[str, Any] = {
        "total_duration_s": 0.0,
        "phase_durations": {p: 0.0 for p in ALL_PHASES},
        "actor_counts": {},
        "phase_models": {},
        "outcome_counts": {"pass": 0, "fail": 0, "info": 0, "blocked": 0},
        "iterations": [],
        "last_error": None,
        "is_terminal": sprint.get("status") in TERMINAL_STATUSES,
        "completed_phases": [],
    }

    # Total duration from first to last timestamp
    ts_first = _parse_ts(sprint.get("created_at"))
    ts_last = _parse_ts(sprint.get("updated_at")) or ts_first
    if ts_first and ts_last and ts_last >= ts_first:
        stats["total_duration_s"] = (ts_last - ts_first).total_seconds()

    # Per-phase duration approximation: sum gaps between consecutive entries
    # attributed to the phase of the earlier entry.
    prev_ts = None
    prev_phase = None
    for entry in log:
        ts = _parse_ts(entry.get("ts"))
        phase = entry.get("phase") or "spec"
        if prev_ts is not None and ts is not None and ts >= prev_ts:
            gap = (ts - prev_ts).total_seconds()
            if prev_phase in stats["phase_durations"]:
                stats["phase_durations"][prev_phase] += gap
        prev_ts, prev_phase = ts, phase

    # Actor + outcome counts
    for entry in log:
        actor = str(entry.get("actor") or "unknown")
        outcome = str(entry.get("outcome") or "info")
        bucket = stats["actor_counts"].setdefault(
            actor, {"total": 0, "pass": 0, "fail": 0, "info": 0, "blocked": 0}
        )
        bucket["total"] += 1
        if outcome in bucket:
            bucket[outcome] += 1
        if outcome in stats["outcome_counts"]:
            stats["outcome_counts"][outcome] += 1
        model = entry.get("model")
        if model:
            phase = str(entry.get("phase") or "spec")
            model_bucket = stats["phase_models"].setdefault(phase, {})
            model_bucket[str(model)] = model_bucket.get(str(model), 0) + 1

    # Per-iteration rollup
    per_iter: Dict[int, Dict[str, Any]] = {}
    for entry in log:
        it = int(entry.get("iteration") or 0)
        row = per_iter.setdefault(it, {
            "iteration": it,
            "test_outcome": None,
            "verify_outcome": None,
            "test_detail": None,
            "verify_detail": None,
            "entries": 0,
            "first_ts": None,
            "last_ts": None,
        })
        row["entries"] += 1
        if entry.get("action") == "test_run":
            row["test_outcome"] = entry.get("outcome")
            row["test_detail"] = entry.get("detail")
        if entry.get("action") == "verify_run":
            row["verify_outcome"] = entry.get("outcome")
            row["verify_detail"] = entry.get("detail")
        ts = _parse_ts(entry.get("ts"))
        if ts:
            if row["first_ts"] is None or ts < row["first_ts"]:
                row["first_ts"] = ts
            if row["last_ts"] is None or ts > row["last_ts"]:
                row["last_ts"] = ts
    for row in per_iter.values():
        if row["first_ts"] and row["last_ts"]:
            row["duration_s"] = (row["last_ts"] - row["first_ts"]).total_seconds()
        else:
            row["duration_s"] = 0
    stats["iterations"] = sorted(per_iter.values(), key=lambda r: r["iteration"])

    # Last error: most recent fail entry
    for entry in reversed(log):
        if entry.get("outcome") == "fail":
            stats["last_error"] = str(entry.get("detail") or "")
            break

    # Completed phases: everything strictly before the current one, plus current
    # phase if terminal/done.
    current = sprint.get("phase", "spec")
    completed: List[str] = []
    for p in PHASE_ORDER:
        if p == current:
            break
        completed.append(p)
    if current == "done":
        completed = list(PHASE_ORDER)  # everything done
    stats["completed_phases"] = completed

    return stats


def _task_stats(tasks: List[dict]) -> dict:
    """Total + per-state counts + tasks sorted by `n`. Empty dict if no tasks."""
    if not tasks:
        return {"total": 0, "by_state": {}, "sorted": []}
    by_state: Dict[str, int] = {}
    for t in tasks:
        state = str(t.get("state") or "pending")
        by_state[state] = by_state.get(state, 0) + 1
    return {
        "total": len(tasks),
        "by_state": by_state,
        "sorted": sorted(tasks, key=lambda t: t.get("n") or 0),
    }


def _task_duration_s(task: dict) -> Optional[float]:
    started = _parse_ts(task.get("started_at"))
    if not started:
        return None
    ended = _parse_ts(task.get("ended_at"))
    if ended:
        return (ended - started).total_seconds()
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now - started).total_seconds()


def _tasks_summary_line(stats: dict) -> str:
    total = stats["total"]
    by_state = stats["by_state"]
    done = by_state.get("done", 0)
    parts = [f"{done} of {total} done"]
    for state in TASK_STATE_ORDER:
        count = by_state.get(state, 0)
        if count:
            parts.append(f"{count} {state}")
    return "Tasks: " + ", ".join(parts)


def _resume_snippet(sprint: dict) -> str:
    """Still used by the text-mode `status` CLI (render_status_text below);
    the HTML dashboard's own resume/regen snippets moved to
    board\\render_board.py with the rest of the page."""
    project = sprint.get("project", "<project>")
    slug = sprint.get("slug", "<slug>")
    spec_path = sprint.get("spec_path", "<spec-path>")
    max_it = sprint.get("max_iterations", 5)
    return (
        f"/sprint {project} {spec_path} --max-iterations {max_it}\n"
        f"# resumes existing sprint at C:\\work\\10_holding\\sprints\\{project}\\{slug}\\"
    )


# ---------------------------------------------------------------------------
# Sprint dashboard (thin caller; HTML now built by board.render_board)
# ---------------------------------------------------------------------------


def render_sprint(sprint_dir: pathlib.Path) -> pathlib.Path:
    """Thin caller (Siteoffice fold, sprint 1 step 12): delegates to
    board.render_board's sprints board kind and returns this sprint's new
    _board\\ page path (`<sprints_root>\\..\\_board\\sprints\\<project>\\
    <slug>\\index.html`, not the old `dashboard.html` next to sprint.json).

    Real behaviour change, named rather than hidden: the board kind has no
    single-sprint entry point, so this call re-renders the whole sprints
    index and every per-sprint page under the same root, not only this one.
    `sprint_step.py` calls this once per phase transition; on a root with
    many sprints (53 under the real hub today) that is real render cost per
    call where the old function only ever touched one file. Left as-is per
    the guide's own scope (thin callers keep names and delegate; a
    lighter-weight single-sprint board-kind entry point is a sprint 2
    candidate, not this row's to build)."""
    sprint_dir = pathlib.Path(sprint_dir)
    sprints_root = sprint_dir.parent.parent
    _board.main(["sprints", str(sprints_root)])
    rel = sprint_dir.relative_to(sprints_root)
    return sprints_root.parent / "_board" / "sprints" / rel / "index.html"


# ---------------------------------------------------------------------------
# Aggregate index
# ---------------------------------------------------------------------------


def render_index(sprints_root: pathlib.Path) -> pathlib.Path:
    """Thin caller (Siteoffice fold, sprint 1 step 12): delegates to
    board.render_board's sprints board kind (index + every per-sprint page)
    and returns the new _board\\sprints\\index.html path."""
    sprints_root = pathlib.Path(sprints_root)
    _board.main(["sprints", str(sprints_root)])
    return sprints_root.parent / "_board" / "sprints" / "index.html"


# ---------------------------------------------------------------------------
# Text-mode dashboard (CLI-visible during a running /sprint)
# ---------------------------------------------------------------------------


# ANSI colour toggles. Auto-disabled when stdout isn't a tty or NO_COLOR is set.
_ANSI = {
    "reset":  "\x1b[0m",
    "bold":   "\x1b[1m",
    "dim":    "\x1b[2m",
    "red":    "\x1b[31m",
    "green":  "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue":   "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan":   "\x1b[36m",
    "grey":   "\x1b[90m",
}

_STATUS_ANSI = {
    "running":  "cyan",
    "passing":  "green",
    "failing":  "red",
    "capped":   "yellow",
    "blocked":  "red",
    "done":     "green",
    "planning": "blue",
    "starting": "blue",
    "reporting": "magenta",
}

_OUTCOME_ANSI = {
    "pass":   "green",
    "fail":   "red",
    "info":   "grey",
    "warn":   "yellow",
    "error":  "red",
}

_TASK_STATE_ANSI = {
    "done":    "green",
    "running": "cyan",
    "review":  "magenta",
    "fixing":  "yellow",
    "blocked": "red",
    "pending": "grey",
}


def _use_color(stream=None) -> bool:
    import os
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("SPRINT_FORCE_COLOR"):
        return True
    s = stream if stream is not None else sys.stdout
    return hasattr(s, "isatty") and s.isatty()


def _c(text: str, colour: str, enable: bool) -> str:
    if not enable or not colour or colour not in _ANSI:
        return text
    return f"{_ANSI[colour]}{text}{_ANSI['reset']}"


def _text_timeline(sprint: dict, stats: dict, colour: bool) -> str:
    """One-line phase timeline: spec > plan > [dev] > test > ...

    Current phase is bracketed and coloured cyan; completed phases green;
    halted phase red; upcoming phases dim.
    """
    parts = []
    phase = sprint.get("phase", "")
    status = sprint.get("status", "")
    completed = set(stats.get("completed_phases", []))
    halted = status in ("failing", "capped", "blocked")
    for p in PHASE_ORDER + ["done"]:
        if p in completed and p != phase:
            parts.append(_c(p, "green", colour))
        elif p == phase:
            marker = f"[{p}]"
            if halted:
                parts.append(_c(marker, "red", colour))
            else:
                parts.append(_c(marker, "cyan", colour))
        else:
            parts.append(_c(p, "grey", colour))
    return " > ".join(parts)


def _text_tasks(task_stats: dict, colour: bool) -> List[str]:
    """Lines for the 'Tasks:' section: summary + one row per task, sorted by n."""
    if not task_stats["total"]:
        return []
    lines = [_c(_tasks_summary_line(task_stats), "grey", colour)]
    name_w = max(6, min(40, max(len(str(t.get("title") or "")) for t in task_stats["sorted"])))
    for t in task_stats["sorted"]:
        n = t.get("n", "?")
        state = str(t.get("state") or "pending")
        state_col = _TASK_STATE_ANSI.get(state, "grey")
        dur_s = _task_duration_s(t)
        dur = _format_duration(dur_s) if dur_s is not None else "-"
        model = str(t.get("model") or "-")
        commit = str(t.get("commit") or "-")[:7]
        title = str(t.get("title") or "")
        lines.append(
            f"  #{n} {_c(state.ljust(8), state_col, colour)} {dur:>7}  "
            f"{model:<7} {commit:<8} {title.ljust(name_w)[:name_w]}"
        )
    return lines


def _text_bar(progress: int, width: int = 30, colour: bool = False) -> str:
    filled = int(round(progress * width / 100))
    bar = "#" * filled + "-" * (width - filled)
    coloured_bar = _c(bar, "cyan", colour) if colour else bar
    return f"[{coloured_bar}] {progress:3d}%"


def _text_kv(label: str, value: str, colour: bool, label_colour: str = "grey") -> str:
    return f"  {_c(label + ':', label_colour, colour)} {value}"


def render_status_text(sprint_dir: pathlib.Path,
                       colour: Optional[bool] = None,
                       tail: int = 12,
                       width: int = 80) -> str:
    """Render a plain-text dashboard for a single sprint.

    Same underlying data as render_sprint() (uses compute_stats), but
    formatted for a terminal. Safe to call in a loop for --watch mode.
    """
    if colour is None:
        colour = _use_color()
    sp_path = sprint_dir / "sprint.json"
    if not sp_path.exists():
        return _c(f"error: no sprint.json at {sp_path}", "red", colour)
    try:
        sprint = json.loads(sp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _c(f"error: sprint.json malformed: {e}", "red", colour)

    log = _read_log(sprint_dir)
    stats = compute_stats(sprint, log)
    progress = compute_progress(sprint)
    task_stats = _task_stats(sprint.get("tasks") or [])

    project = sprint.get("project", "?")
    slug = sprint.get("slug", "?")
    status = sprint.get("status", "?")
    phase = sprint.get("phase", "?")
    it = sprint.get("iteration", 0)
    max_it = sprint.get("max_iterations", 0)
    updated = sprint.get("updated_at", "?")

    status_colour = _STATUS_ANSI.get(status, "yellow")

    lines: List[str] = []
    hr = "-" * min(width, 100)
    lines.append(hr)
    header = f"/sprint {project} / {slug}"
    lines.append(_c(header, "bold", colour))
    lines.append(hr)
    lines.append(_text_kv("Status", _c(status.upper(), status_colour, colour), colour))
    lines.append(_text_kv("Phase", phase, colour))
    lines.append(_text_kv("Iteration", f"{it} of {max_it}", colour))
    lines.append(_text_kv("Progress", _text_bar(progress, colour=colour), colour))
    lines.append(_text_kv("Elapsed", _format_duration(stats.get("elapsed_seconds") or 0), colour))
    lines.append(_text_kv("Updated", updated, colour))

    lines.append("")
    lines.append(_c("Timeline:", "grey", colour))
    lines.append("  " + _text_timeline(sprint, stats, colour))

    task_lines = _text_tasks(task_stats, colour)
    if task_lines:
        lines.append("")
        lines.extend(task_lines)

    banner = _text_failure_banner(sprint, stats, colour)
    if banner:
        lines.append("")
        lines.append(banner)

    # Iteration rollup
    iters = stats.get("iterations", [])
    if iters:
        lines.append("")
        lines.append(_c("Iterations:", "grey", colour))
        for it_stat in iters:
            n = it_stat.get("iteration", "?")
            t_out = it_stat.get("test_outcome") or "-"
            v_out = it_stat.get("verify_outcome") or "-"
            entries = it_stat.get("entries", 0)
            t_col = _OUTCOME_ANSI.get(t_out, "grey")
            v_col = _OUTCOME_ANSI.get(v_out, "grey")
            lines.append(
                f"  #{n}  test={_c(t_out, t_col, colour)}  "
                f"verify={_c(v_out, v_col, colour)}  entries={entries}"
            )

    # Actor breakdown
    actors = stats.get("actor_counts", {})
    if actors:
        lines.append("")
        lines.append(_c("Actors:", "grey", colour))
        rows = sorted(actors.items(), key=lambda kv: -kv[1]["total"])
        name_w = max(6, min(28, max(len(a) for a, _ in rows)))
        lines.append(
            f"  {'actor'.ljust(name_w)}  {'total':>5}  "
            f"{'pass':>5}  {'fail':>5}  {'info':>5}"
        )
        for a, c in rows:
            passc = _c(str(c.get('pass', 0)).rjust(5), 'green', colour) if c.get('pass', 0) else str(c.get('pass', 0)).rjust(5)
            failc = _c(str(c.get('fail', 0)).rjust(5), 'red', colour) if c.get('fail', 0) else str(c.get('fail', 0)).rjust(5)
            lines.append(
                f"  {a.ljust(name_w)}  {c.get('total', 0):>5}  "
                f"{passc}  {failc}  {c.get('info', 0):>5}"
            )

    # Recent action log tail
    if log:
        lines.append("")
        lines.append(_c(f"Last {min(tail, len(log))} log entries (most recent last):", "grey", colour))
        for entry in log[-tail:]:
            lines.append("  " + _format_log_line(entry, colour, width))

    # Copy-paste helpers
    lines.append("")
    lines.append(_c("Resume:", "grey", colour))
    for ln in _resume_snippet(sprint).splitlines():
        lines.append("  " + ln)
    lines.append("")
    lines.append(_c("Open HTML dashboard:", "grey", colour))
    lines.append(f"  {sprint_dir / 'dashboard.html'}")
    lines.append(hr)

    return "\n".join(lines)


def _text_failure_banner(sprint: dict, stats: dict, colour: bool) -> str:
    reasons = []
    t = sprint.get("last_test_result") or {}
    if isinstance(t, dict) and t.get("exit_code") not in (0, None):
        reasons.append(f"tests exit {t.get('exit_code')} ({t.get('summary','?')})")
    v = sprint.get("last_verify_result") or {}
    if isinstance(v, dict) and v.get("outcome") == "fail":
        reasons.append(f"verify=fail ({v.get('summary','?')})")
    status = sprint.get("status", "")
    if status in ("failing", "capped", "blocked"):
        reasons.append(f"status={status}")
    if not reasons:
        return ""
    body = "  ".join(reasons)
    tag = _c("[!] FAILURE", "red", colour)
    return f"{tag} {body}"


def _format_log_line(entry: dict, colour: bool, width: int) -> str:
    ts = entry.get("ts", "")
    if len(ts) > 19:
        ts = ts[11:19]  # HH:MM:SS
    phase = entry.get("phase", "?")
    actor = entry.get("actor", "?")
    action = entry.get("action", "?")
    outcome = entry.get("outcome", "info")
    detail = entry.get("detail", "") or ""
    outcome_col = _OUTCOME_ANSI.get(outcome, "grey")
    out_tag = _c(outcome.ljust(4), outcome_col, colour)
    prefix = f"{_c(ts, 'grey', colour)} {out_tag} {_c(phase, 'blue', colour)}/{actor} {_c(action, 'bold', colour)}"
    # Truncate detail to fit width
    max_detail = max(20, width - len(f"{ts} {outcome.ljust(4)} {phase}/{actor} {action} ") - 2)
    if len(detail) > max_detail:
        detail = detail[: max_detail - 1] + "\u2026"
    return f"{prefix}  {detail}"


def watch_status_text(sprint_dir: pathlib.Path,
                      interval: float = 3.0,
                      colour: Optional[bool] = None,
                      tail: int = 12,
                      width: int = 80,
                      max_iterations: Optional[int] = None) -> int:
    """Loop render_status_text() until the sprint is terminal or interrupted.

    Clears the screen between redraws when stdout is a tty. Returns 0 on
    clean exit (terminal status or KeyboardInterrupt), non-zero on error.
    """
    import time
    if colour is None:
        colour = _use_color()
    terminal = {"passing", "failing", "capped", "blocked"}
    n = 0
    try:
        while True:
            n += 1
            if _use_color() and sys.stdout.isatty():
                # Clear + home cursor
                sys.stdout.write("\x1b[2J\x1b[H")
            text = render_status_text(sprint_dir, colour=colour, tail=tail, width=width)
            sys.stdout.write(text)
            sys.stdout.write("\n")
            sys.stdout.flush()
            # Check for terminal state
            sp_path = sprint_dir / "sprint.json"
            if sp_path.exists():
                try:
                    sp = json.loads(sp_path.read_text(encoding="utf-8"))
                    if sp.get("status") in terminal:
                        sys.stdout.write(f"\n{_c('sprint reached terminal status; exiting watch.', 'grey', colour)}\n")
                        return 0
                except json.JSONDecodeError:
                    pass
            if max_iterations is not None and n >= max_iterations:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


def tail_log(sprint_dir: pathlib.Path,
             follow: bool = False,
             interval: float = 1.0,
             colour: Optional[bool] = None,
             width: int = 100) -> int:
    """Print action-log.ndjson entries, optionally following like `tail -f`.

    Does not lock the file; safe for concurrent writers as long as they
    only append. Uses byte offsets to resume between polls.
    """
    import time
    if colour is None:
        colour = _use_color()
    log_path = sprint_dir / "action-log.ndjson"
    if not log_path.exists():
        sys.stderr.write(f"no action-log.ndjson at {log_path}\n")
        return 1
    # Print existing content first
    offset = 0
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sys.stdout.write(_format_log_line(entry, colour, width) + "\n")
            offset = f.tell()
        sys.stdout.flush()
        if not follow:
            return 0
        while True:
            time.sleep(interval)
            try:
                with log_path.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    new = f.read()
                    offset = f.tell()
            except FileNotFoundError:
                continue
            if not new:
                continue
            for line in new.splitlines():
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sys.stdout.write(_format_log_line(entry, colour, width) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="render_dashboard.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sprint = sub.add_parser("sprint", help="Render one sprint's dashboard.html")
    p_sprint.add_argument("sprint_dir", type=pathlib.Path)

    p_index = sub.add_parser("index", help="Rebuild the aggregate index.html + index.json")
    p_index.add_argument("sprints_root", type=pathlib.Path)

    p_status = sub.add_parser("status",
        help="Print a text-mode dashboard for a running sprint (CLI-visible)")
    p_status.add_argument("sprint_dir", type=pathlib.Path)
    p_status.add_argument("--watch", type=float, metavar="SECONDS", default=None,
        help="Refresh every N seconds until sprint reaches terminal status")
    p_status.add_argument("--tail", type=int, default=12,
        help="Number of recent log entries to show (default: 12)")
    p_status.add_argument("--width", type=int, default=100,
        help="Terminal width for wrapping (default: 100)")
    p_status.add_argument("--no-color", action="store_true", help="Disable ANSI colours")
    p_status.add_argument("--color", action="store_true", help="Force ANSI colours")

    p_tail = sub.add_parser("tail",
        help="Stream action-log.ndjson entries (optionally --follow)")
    p_tail.add_argument("sprint_dir", type=pathlib.Path)
    p_tail.add_argument("-f", "--follow", action="store_true",
        help="Keep polling for new entries (like tail -f)")
    p_tail.add_argument("--interval", type=float, default=1.0,
        help="Poll interval in seconds when following (default: 1.0)")
    p_tail.add_argument("--width", type=int, default=120)
    p_tail.add_argument("--no-color", action="store_true")
    p_tail.add_argument("--color", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "sprint":
        out = render_sprint(args.sprint_dir)
        print(str(out))
        return 0
    if args.cmd == "index":
        out = render_index(args.sprints_root)
        print(str(out))
        return 0
    if args.cmd == "status":
        colour = True if args.color else (False if args.no_color else None)
        if args.watch:
            return watch_status_text(args.sprint_dir,
                interval=args.watch, colour=colour,
                tail=args.tail, width=args.width)
        text = render_status_text(args.sprint_dir,
            colour=colour, tail=args.tail, width=args.width)
        print(text)
        return 0
    if args.cmd == "tail":
        colour = True if args.color else (False if args.no_color else None)
        return tail_log(args.sprint_dir, follow=args.follow,
            interval=args.interval, colour=colour, width=args.width)
    return 2


if __name__ == "__main__":
    sys.exit(main())
