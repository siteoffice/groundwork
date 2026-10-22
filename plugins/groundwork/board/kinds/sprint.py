"""Sprint board kind: per-sprint pages and the sprints index. Moved out of
render_board.py (decision 13, 2026-09-12 resume guide) to keep it under its
60,000-byte guard; this module is capped at 20,000. Imports board_common,
theme and templates.* directly, never render_board (no import cycle);
render_page_shell is passed in by the caller. Never imports sprints\\_lib
(hard rule): progress math and stats are reproduced here, not called.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
TEMPLATES_DIR = BOARD_DIR / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))

import theme  # noqa: E402
from board_common import esc, json_safe, read_ndjson  # noqa: E402
import table as tpl_table  # noqa: E402
import activity as tpl_activity  # noqa: E402

_SPRINT_PHASE_WEIGHTS = {"spec": 5, "plan": 10, "dev": 40, "test": 15, "iterate": 25, "report": 5}
_SPRINT_PHASE_ORDER = ["spec", "plan", "dev", "test", "iterate", "report"]
_SPRINT_PHASE_ICONS = {
    "spec": "\U0001F4CB", "plan": "\U0001F5FA", "dev": "\U0001F527",
    "test": "\U0001F9EA", "iterate": "\U0001F501", "report": "\U0001F4DD",
    "done": "✅",
}
_TASK_STATE_SEM = {
    "done": "done", "running": "run", "review": "wait",
    "fixing": "wait", "blocked": "halt", "pending": "done",
}
_TASK_STATE_ORDER = ["running", "review", "fixing", "blocked", "pending"]
_COMM_ACTIONS = {"dispatch", "decision", "escalate", "cap_hit", "sprint_start", "sprint_done"}
_SPRINT_NEXT_HINTS = {
    "spec": "Locate or brainstorm a spec, then move to plan.",
    "plan": "Write or load an implementation plan for the spec.",
    "dev": "Implementer subagents are working through plan tasks in the worktree.",
    "test": "Run the project's test command and the verify skill.",
    "iterate": "Feed the last test/verify failure back into the next dev pass.",
    "report": "Writing the final human summary in report.md.",
}


def _compute_sprint_progress(sprint: dict) -> int:
    """Reproduced from render_dashboard.compute_progress, not imported (hard rule)."""
    if sprint.get("phase") == "done":
        return 100
    phase = sprint.get("phase", "spec")
    completed = 0
    for p in _SPRINT_PHASE_ORDER:
        if p == phase:
            break
        completed += _SPRINT_PHASE_WEIGHTS.get(p, 0)
    current_weight = _SPRINT_PHASE_WEIGHTS.get(phase, 0)
    tasks = sprint.get("tasks")
    if phase == "dev" and tasks:
        tasks_total = len(tasks)
        tasks_done = sum(1 for t in tasks if t.get("state") == "done")
        fractional = int(round((tasks_done / tasks_total) * current_weight)) if tasks_total else 0
    else:
        iteration = int(sprint.get("iteration") or 0)
        max_it = max(1, int(sprint.get("max_iterations") or 1))
        fractional = int(round((iteration / max_it) * current_weight))
    total = completed + fractional
    if sprint.get("status") == "running":
        return min(total, 99)
    return min(total, 100)


def _parse_sprint_ts(ts):
    if not ts or not isinstance(ts, str):
        return None
    t = ts.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(t)
    except (ValueError, TypeError):
        return None


def _format_sprint_duration(seconds) -> str:
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


def _sprint_stats(sprint: dict, log: list) -> dict:
    """Reproduced from render_dashboard.compute_stats, not imported (hard rule)."""
    stats: dict = {
        "total_duration_s": 0.0,
        "actor_counts": {}, "phase_models": {},
        "outcome_counts": {"pass": 0, "fail": 0, "info": 0, "blocked": 0},
        "iterations": [], "last_error": None, "completed_phases": [],
    }
    t0 = _parse_sprint_ts(sprint.get("created_at"))
    t1 = _parse_sprint_ts(sprint.get("updated_at")) or t0
    if t0 and t1 and t1 >= t0:
        stats["total_duration_s"] = (t1 - t0).total_seconds()
    for entry in log:
        actor = str(entry.get("actor") or "unknown")
        outcome = str(entry.get("outcome") or "info")
        bucket = stats["actor_counts"].setdefault(
            actor, {"total": 0, "pass": 0, "fail": 0, "info": 0, "blocked": 0})
        bucket["total"] += 1
        if outcome in bucket:
            bucket[outcome] += 1
        if outcome in stats["outcome_counts"]:
            stats["outcome_counts"][outcome] += 1
        model = entry.get("model")
        if model:
            phase = str(entry.get("phase") or "spec")
            mb = stats["phase_models"].setdefault(phase, {})
            mb[str(model)] = mb.get(str(model), 0) + 1
    per_iter: dict = {}
    for entry in log:
        it = int(entry.get("iteration") or 0)
        row = per_iter.setdefault(it, {"iteration": it, "test_outcome": None,
                                        "verify_outcome": None, "entries": 0})
        row["entries"] += 1
        if entry.get("action") == "test_run":
            row["test_outcome"] = entry.get("outcome")
        if entry.get("action") == "verify_run":
            row["verify_outcome"] = entry.get("outcome")
    stats["iterations"] = sorted(per_iter.values(), key=lambda r: r["iteration"])
    for entry in reversed(log):
        if entry.get("outcome") == "fail":
            stats["last_error"] = str(entry.get("detail") or "")
            break
    current = sprint.get("phase", "spec")
    completed = []
    for p in _SPRINT_PHASE_ORDER:
        if p == current:
            break
        completed.append(p)
    if current == "done":
        completed = list(_SPRINT_PHASE_ORDER)
    stats["completed_phases"] = completed
    return stats


def _sprint_task_stats(tasks: list) -> dict:
    if not tasks:
        return {"total": 0, "by_state": {}, "sorted": []}
    by_state: dict = {}
    for t in tasks:
        st = str(t.get("state") or "pending")
        by_state[st] = by_state.get(st, 0) + 1
    return {"total": len(tasks), "by_state": by_state,
            "sorted": sorted(tasks, key=lambda t: t.get("n") or 0)}


def _sprint_tasks_summary(task_stats: dict) -> str:
    total = task_stats["total"]
    by_state = task_stats["by_state"]
    parts = [f"{by_state.get('done', 0)} of {total} done"]
    for state in _TASK_STATE_ORDER:
        n = by_state.get(state, 0)
        if n:
            parts.append(f"{n} {state}")
    return "Tasks: " + ", ".join(parts)


def _sprint_phase_timeline_html(sprint: dict, stats: dict) -> str:
    current = sprint.get("phase", "spec")
    halted = sprint.get("status", "") in {"failed", "escalated", "capped"}
    chips = []
    for p in _SPRINT_PHASE_ORDER + ["done"]:
        icon = _SPRINT_PHASE_ICONS.get(p, "")
        if p in stats["completed_phases"] or (p == "done" and current == "done"):
            sem = "done"
        elif p == current:
            sem = "halt" if halted else "run"
        else:
            sem = "done"  # upcoming, recedes
        chips.append(tpl_table.render_badge(f"{icon} {p}", sem))
    return '<p class="mono">' + " &rarr; ".join(chips) + "</p>"


def _sprint_progress_bar_html(progress: int) -> str:
    return (
        '<div style="height:8px;border-radius:var(--radius-sm);'
        'background:var(--grey-fill);overflow:hidden">'
        f'<div style="width:{progress}%;height:100%;background:var(--mint)"></div></div>'
        f'<p class="mono">{progress}%</p>'
    )


def _sprint_kpis_html(sprint: dict, stats: dict, log: list) -> str:
    ltr = sprint.get("last_test_result") or {}
    lvr = sprint.get("last_verify_result") or {}

    def _kpi(label, value, alert=False, sub=""):
        cls = "kpi alert" if alert else "kpi"
        sub_html = f'<div class="ksub">{esc(sub)}</div>' if sub else ""
        return (f'<div class="{cls}"><div class="klbl">{esc(label)}</div>'
                f'<div class="kval">{esc(value)}</div>{sub_html}</div>')

    tests_val, tests_alert = "-", False
    if isinstance(ltr, dict) and ltr.get("exit_code") is not None:
        if ltr.get("exit_code") == 0:
            tests_val = "pass"
        else:
            tests_val, tests_alert = f"exit {ltr.get('exit_code')}", True
    verify_val, verify_alert = "-", False
    if isinstance(lvr, dict) and lvr.get("outcome"):
        verify_val = str(lvr.get("outcome"))
        verify_alert = lvr.get("outcome") == "fail"
    oc = stats["outcome_counts"]
    tiles = [
        _kpi("Iteration", f'{int(sprint.get("iteration") or 0)} of {int(sprint.get("max_iterations") or 0)}'),
        _kpi("Phase", sprint.get("phase", "") or "-"),
        _kpi("Elapsed", _format_sprint_duration(stats["total_duration_s"])),
        _kpi("Last tests", tests_val, tests_alert,
             str(ltr.get("summary", "")) if isinstance(ltr, dict) else ""),
        _kpi("Last verify", verify_val, verify_alert,
             str(lvr.get("summary", "")) if isinstance(lvr, dict) else ""),
        _kpi("Log entries", str(len(log)), False,
             f"pass {oc['pass']}, fail {oc['fail']}, blocked {oc['blocked']}"),
    ]
    return '<div class="kpis">' + "".join(tiles) + "</div>"


def _sprint_tasks_html(task_stats: dict) -> str:
    if not task_stats["total"]:
        return ""
    headers = ["#", "state", "duration", "model", "commit", "title"]
    rows = []
    for t in task_stats["sorted"]:
        state = str(t.get("state") or "pending")
        dur_s = None
        started = _parse_sprint_ts(t.get("started_at"))
        if started:
            ended = _parse_sprint_ts(t.get("ended_at")) or datetime.now(timezone.utc)
            dur_s = (ended - started).total_seconds()
        dur = _format_sprint_duration(dur_s) if dur_s is not None else "-"
        rows.append([
            f"#{esc(t.get('n', ''))}",
            tpl_table.render_badge(state, _TASK_STATE_SEM.get(state, "done")),
            dur, esc(t.get("model") or "-"),
            f'<span class="mono">{esc(str(t.get("commit") or "-")[:7])}</span>',
            esc(t.get("title") or ""),
        ])
    return f"<p>{esc(_sprint_tasks_summary(task_stats))}</p>" + tpl_table.render_table(headers, rows)


def _empty_html(text: str) -> str:
    return f'<p class="empty"><span class="msg">{esc(text)}</span></p>'


def _stat_table_or_empty(rows, headers, empty_text: str) -> str:
    """Shared by the actor and phase-model tables (trim duplication, not a third module)."""
    return _empty_html(empty_text) if not rows else tpl_table.render_table(headers, rows)


def _sprint_iteration_cards_html(stats: dict, max_it: int, empty_text: str) -> str:
    if not stats["iterations"]:
        return _empty_html(empty_text)
    cards = []
    for row in stats["iterations"]:
        test_o, verify_o = row["test_outcome"], row["verify_outcome"]
        if test_o == "pass" and verify_o == "pass":
            health = "h-green"
        elif test_o == "fail" or verify_o == "fail":
            health = "h-red"
        elif test_o == "blocked" or verify_o == "blocked":
            health = "h-yellow"
        else:
            health = ""
        it_label = f'Iteration {row["iteration"]}' + (f" of {max_it}" if max_it else "")
        cards.append(
            f'<div class="pcard {health}"><div class="top"><span class="name">{esc(it_label)}</span></div>'
            f'<p class="next"><span class="lbl">test</span>{esc(test_o or "-")}'
            f' &middot; <span class="lbl">verify</span>{esc(verify_o or "-")}</p>'
            f'<p class="touched">entries: {row["entries"]}</p></div>'
        )
    # Fixed height, scrolling body: iteration count is unbounded on a
    # long-running sprint, the grid's footprint must not grow with it.
    return ('<div class="panel-scroll">'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">'
            + "".join(cards) + "</div></div>")


def _sprint_actor_table_html(stats: dict, empty_text: str) -> str:
    rows = [
        [esc(actor), str(c["total"]), str(c["pass"]), str(c["fail"]), str(c["blocked"]), str(c["info"])]
        for actor, c in sorted(stats["actor_counts"].items(), key=lambda kv: -kv[1]["total"])
    ]
    return _stat_table_or_empty(rows, ["actor", "total", "pass", "fail", "blocked", "info"], empty_text)


def _sprint_phase_model_table_html(stats: dict, empty_text: str) -> str:
    known = _SPRINT_PHASE_ORDER + ["done"]
    extra = sorted(k for k in stats["phase_models"] if k not in known)
    rows = []
    for phase in known + extra:
        models = stats["phase_models"].get(phase)
        if not models:
            continue
        model_str = ", ".join(f"{m} ({c})" for m, c in sorted(models.items(), key=lambda kv: -kv[1]))
        rows.append([esc(phase), esc(model_str)])
    return _stat_table_or_empty(rows, ["phase", "model(s)"], empty_text)


def _sprint_communication_html(log: list, label_bundle: dict) -> str:
    """Reuses the activity panel's own render() (same reshape the flow log uses)."""
    convo = [e for e in log if str(e.get("action")) in _COMM_ACTIONS]
    entries = [
        {"name": " · ".join(str(e.get(k, "")) for k in
                                  ("ts", "iteration", "phase", "actor", "action", "outcome", "detail"))}
        for e in convo
    ]
    state = tpl_activity.STATE_NORMAL if entries else tpl_activity.STATE_EMPTY
    return tpl_activity.render({"entries": entries}, label_bundle, state)


def _sprint_failure_banner_html(sprint: dict, stats: dict) -> str:
    ltr = sprint.get("last_test_result") or {}
    lvr = sprint.get("last_verify_result") or {}
    status = sprint.get("status", "")
    triggers = []
    if isinstance(ltr, dict) and ltr.get("exit_code") not in (None, 0):
        triggers.append(f"tests exit {ltr.get('exit_code')}: {ltr.get('summary', '')}")
    if isinstance(lvr, dict) and lvr.get("outcome") == "fail":
        triggers.append(f"verify failed: {lvr.get('summary', '')}")
    if status in {"failed", "escalated", "capped"}:
        triggers.append(f"sprint status: {status}")
    if not triggers:
        return ""
    lines = "".join(f"<li>{esc(t)}</li>" for t in triggers)
    err = (f'<p class="mono">last error: {esc(stats["last_error"])}</p>'
           if stats.get("last_error") else "")
    return (f'<div style="border-left:4px solid var(--red-line);background:var(--red-fill);'
            f'padding:10px 14px;border-radius:var(--radius-sm)">'
            f'<strong style="color:var(--red)">Attention required</strong><ul>{lines}</ul>{err}</div>')


def _sprint_next_action_hint(sprint: dict) -> str:
    phase, status = sprint.get("phase", "spec"), sprint.get("status", "")
    if phase == "done":
        return "Sprint complete. Review the branch and merge when ready."
    if status == "escalated":
        return "Escalated. Human review required, see the failure banner and action log."
    if status == "capped":
        return "Iteration cap reached. Read report.md and decide whether to extend or abandon."
    return _SPRINT_NEXT_HINTS.get(phase, "-")


def _sprint_resume_snippet(sprint: dict) -> str:
    return (f'/sprint {sprint.get("project", "<project>")} {sprint.get("spec_path", "<spec-path>")} '
            f'--max-iterations {sprint.get("max_iterations", 5)}')


def _sprint_regen_snippet(sprint_dir: pathlib.Path) -> str:
    return (f'python board\\render_board.py sprints "{sprint_dir.parent.parent}" --root <holding-root>')


def _render_sprint_detail(sprint_dir: pathlib.Path, lang, languages, manifest_labels, label_bundle, render_page_shell):
    """Never a blank page: falls back to the empty-state paragraph per section."""
    sprint_dir = pathlib.Path(sprint_dir)
    sprint = {}
    sp_path = sprint_dir / "sprint.json"
    if sp_path.is_file():
        try:
            sprint = json.loads(sp_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sprint = {}
    log = read_ndjson(sprint_dir / "action-log.ndjson")
    stats = _sprint_stats(sprint, log)
    progress = _compute_sprint_progress(sprint)
    task_stats = _sprint_task_stats(sprint.get("tasks") or [])
    empty_text = label_bundle.get("state_empty", "state_empty")

    project, slug = sprint.get("project", ""), sprint.get("slug", "")
    status, phase = str(sprint.get("status", "")), sprint.get("phase", "")
    iteration, max_it = int(sprint.get("iteration") or 0), int(sprint.get("max_iterations") or 0)

    banner = _sprint_failure_banner_html(sprint, stats)
    tasks_html = _sprint_tasks_html(task_stats)
    tasks_section = f"<h3>Tasks</h3>{tasks_html}" if tasks_html else ""
    extra_head = '<meta http-equiv="refresh" content="30">' if status == "running" else ""

    body = (
        f"<h2>{esc(project)} / {esc(slug)}</h2>"
        f'<p>{tpl_table.render_badge(status, theme.status_sem(status))} '
        f'<span class="chip">{esc(phase)}</span> '
        f'<span class="mono">Iteration {iteration} of {max_it}</span></p>'
        f'<p class="note">Next: {esc(_sprint_next_action_hint(sprint))}</p>'
        f"{banner}"
        f"<h3>Phase timeline</h3>{_sprint_phase_timeline_html(sprint, stats)}"
        f"{_sprint_progress_bar_html(progress)}"
        f"{tasks_section}"
        f"<h3>At a glance</h3>{_sprint_kpis_html(sprint, stats, log)}"
        f"<h3>Iterations</h3>{_sprint_iteration_cards_html(stats, max_it, empty_text)}"
        f"<h3>Communication</h3>{_sprint_communication_html(log, label_bundle)}"
        f"<h3>Actors</h3>{_sprint_actor_table_html(stats, empty_text)}"
        f"<h3>Model per phase</h3>{_sprint_phase_model_table_html(stats, empty_text)}"
        f'<h3>Resume / re-run</h3><pre class="mono">{esc(_sprint_resume_snippet(sprint))}</pre>'
        f'<h3>Regenerate</h3><pre class="mono">{esc(_sprint_regen_snippet(sprint_dir))}</pre>'
    )
    title = f"{project} / {slug}" if project else (slug or "sprint")
    html_out = render_page_shell(
        title, "sprints", label_bundle.get("sprints", "Sprints"), lang, languages,
        manifest_labels, body, footer_state_path=str(sprint_dir), extra_head=extra_head,
    )
    json_out = {
        "project": project, "slug": slug, "status": status, "phase": phase,
        "progress": progress, "iteration": iteration, "maxIterations": max_it,
        "updatedAt": sprint.get("updated_at", ""),
    }
    return html_out, json_out


def _sprint_row(sprint_json_path: pathlib.Path, sprints_root: pathlib.Path):
    """T09/R07: discovered the same way as the detail page, from sprint.json,
    present from creation; report.md only exists once a sprint reaches its
    report phase, long after it started running. report_available records
    which is true rather than silently omitting a still-running sprint."""
    sprint_dir = sprint_json_path.parent
    data = {}
    try:
        data = json.loads(sprint_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    last_activity = ""
    log_entries = read_ndjson(sprint_dir / "action-log.ndjson")
    if log_entries:
        last_activity = log_entries[-1].get("ts", "") or ""
    rel_parts = sprint_dir.relative_to(sprints_root).parts
    return {
        "project": rel_parts[0] if len(rel_parts) > 0 else "",
        "slug": rel_parts[1] if len(rel_parts) > 1 else "",
        "status": data.get("status", ""),
        "phase": data.get("phase", ""),
        "progress": _compute_sprint_progress(data),
        "updated_at": data.get("updated_at", ""),
        "last_activity": last_activity,
        "report_available": (sprint_dir / "report.md").is_file(),
    }


def render_board(sprints_root: pathlib.Path, render_page_shell, lang, languages, manifest_labels, label_bundle):
    """Writes the sprints index plus every per-sprint page under _board\\sprints\\."""
    sprints_root = pathlib.Path(sprints_root)

    # T09/R07: index rows and per-sprint pages both gate on sprint.json now,
    # present from creation, so a still-running sprint (called every phase
    # transition, long before report.md exists) gets an index row rather
    # than silently losing it until it happens to finish.
    sprint_json_paths = sorted(sprints_root.glob("*/*/sprint.json"))
    rows_data = [_sprint_row(p, sprints_root) for p in sprint_json_paths]

    out_root = sprints_root.parent / "_board" / "sprints"
    out_root.mkdir(parents=True, exist_ok=True)

    for sprint_json_path in sprint_json_paths:
        sprint_dir = sprint_json_path.parent
        rel_parts = sprint_dir.relative_to(sprints_root).parts
        html_out, json_out = _render_sprint_detail(
            sprint_dir, lang, languages, manifest_labels, label_bundle, render_page_shell)
        per_dir = out_root.joinpath(*rel_parts)
        per_dir.mkdir(parents=True, exist_ok=True)
        (per_dir / "index.html").write_text(html_out, encoding="utf-8")
        (per_dir / "index.json").write_text(
            json.dumps(json_safe(json_out), indent=2, ensure_ascii=False), encoding="utf-8",
        )

    headers = ["project", "sprint", "status", "phase", "progress", "last activity", "report"]
    rows = [
        [
            esc(r["project"]),
            f'<a href="{esc(r["project"])}/{esc(r["slug"])}/index.html">{esc(r["slug"])}</a>',
            tpl_table.render_badge(r["status"], theme.status_sem(r["status"])),
            esc(r["phase"]), f'{r["progress"]}%', esc(r["last_activity"]),
            esc("available" if r["report_available"] else "not yet available"),
        ]
        for r in rows_data
    ]
    empty_text = label_bundle.get("state_empty", "state_empty")
    table_html = tpl_table.render_table(headers, rows, empty_text=empty_text)

    title = label_bundle.get("sprints", "Sprints")
    body = f'<h2 data-i18n="sprints">{esc(title)}</h2>{table_html}'
    html_out = render_page_shell(title, "sprints", title, lang, languages, manifest_labels, body)
    (out_root / "index.html").write_text(html_out, encoding="utf-8")
    (out_root / "index.json").write_text(
        json.dumps(json_safe({
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sprints": rows_data,
        }), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"wrote {out_root / 'index.html'} ({len(rows_data)} sprint(s), {len(sprint_json_paths)} per-sprint page(s))")
    return out_root
