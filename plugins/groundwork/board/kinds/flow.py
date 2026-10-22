"""Flow board kind: the flows index and per-flow pages. Moved out of
render_board.py (decision 13, 2026-09-12 resume guide, step 10b) to keep
render_board.py itself under the 60,000-byte guard, capped at 20,000 bytes
of its own. Imports board_common, theme and templates.* directly; never
imports render_board (no cycle: render_board imports this module, not the
reverse). `render_page_shell` is passed in by the caller instead, since that
one function still lives in render_board.py itself.

Never imports flows\\_lib (hard rule): the pending-gate predicate this needs
is board_common.flow_gate_is_open, the one shared implementation also used
by board\\templates\\waiting_on_you.py (T09, R05), not a call into flows\\_lib.
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
from board_common import esc, flow_gate_is_open, json_safe, read_ndjson  # noqa: E402
import table as tpl_table  # noqa: E402
import activity as tpl_activity  # noqa: E402


def _flow_summary(flow_path: pathlib.Path):
    try:
        flow = json.loads(flow_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    gate = flow.get("gate") if isinstance(flow.get("gate"), dict) else {}
    return {
        "project": flow.get("project", ""),
        "slug": flow.get("slug", ""),
        "stage": flow.get("stage", ""),
        "status": flow.get("status", ""),
        "awaiting": flow_gate_is_open(flow),
        "gate": gate,
        "updated_at": flow.get("updated_at", ""),
        "steps": flow.get("steps") or [],
        "idea": flow.get("idea", ""),
        "log": read_ndjson(flow_path.parent / "flow-log.ndjson"),
        "dir": flow_path.parent,
    }


def _flow_next_command(summary: dict) -> str:
    project, slug, gate = summary["project"], summary["slug"], summary["gate"]
    if summary["awaiting"] and gate:
        return f"/flow gate {project} {slug} approve|reject  (token {gate.get('token', '')})"
    if summary["status"] == "closed":
        return ""
    return f"/flow status {project} {slug}"


def _flow_index_row(s: dict):
    last_log = s["log"][-1] if s["log"] else None
    last_log_text = last_log.get("action", "") if last_log else ""
    link = f'{s["project"]}/{s["slug"]}/index.html'
    gate_text = f'{s["gate"].get("id", "")} {s["gate"].get("status", "")}'.strip()
    return [
        esc(s["project"]),
        f'<a href="{esc(link)}">{esc(s["slug"])}</a>',
        esc(s["stage"]),
        tpl_table.render_badge(s["status"], theme.status_sem(s["status"])),
        esc(gate_text),
        esc(last_log_text),
        esc(s["updated_at"]),
    ]


def _render_flows_index(summaries, lang, languages, manifest_labels, label_bundle, render_page_shell) -> str:
    headers = ["project", "flow", "stage", "status", "gate", "last activity", "updated"]
    awaiting_rows = [_flow_index_row(s) for s in summaries if s["awaiting"]]
    all_rows = [_flow_index_row(s) for s in summaries]
    empty_text = label_bundle.get("state_empty", "state_empty")

    queue_html = ""
    if awaiting_rows:
        queue_html = (
            f'<div class="queue-block"><strong data-i18n="waiting_on_you">'
            f'{esc(label_bundle.get("waiting_on_you", "waiting_on_you"))}</strong>'
            f"{tpl_table.render_table(headers, awaiting_rows)}</div>"
        )

    table_html = tpl_table.render_table(headers, all_rows, empty_text=empty_text)
    title = label_bundle.get("flows", "Flows")
    body = f'<h2 data-i18n="flows">{esc(title)}</h2>{queue_html}{table_html}'
    return render_page_shell(title, "flows", title, lang, languages, manifest_labels, body)


def _render_flow_page(s: dict, lang, languages, manifest_labels, label_bundle, render_page_shell) -> str:
    empty_text = label_bundle.get("state_empty", "state_empty")

    step_rows = [
        [esc(step.get("index", "")), esc(step.get("title", "")), esc(step.get("status", ""))]
        for step in s["steps"]
    ]
    ladder_html = tpl_table.render_table(["#", "title", "status"], step_rows, empty_text=empty_text)

    gate = s["gate"]
    if gate:
        decision = gate.get("decision") or {}
        gate_sem = "wait" if gate.get("status") == "awaiting-owner" else "done"
        gate_html = (
            f'<p>{tpl_table.render_badge(gate.get("status", ""), gate_sem)} '
            f'gate {esc(gate.get("id", ""))} &middot; token {esc(gate.get("token", ""))}</p>'
            f'<p class="note">verdict: {esc(decision.get("verdict") or "-")} '
            f'&middot; note: {esc(decision.get("note") or "-")}</p>'
        )
    else:
        gate_html = f'<p class="empty"><span class="msg">{esc(empty_text)}</span></p>'

    log_entries = [
        {"name": " · ".join(str(e.get(k, "")) for k in
                                 ("ts", "stage", "actor", "action", "outcome", "detail"))}
        for e in s["log"]
    ]
    log_state = tpl_activity.STATE_NORMAL if log_entries else tpl_activity.STATE_EMPTY
    log_html = tpl_activity.render({"entries": log_entries}, label_bundle, log_state)

    next_command = _flow_next_command(s)
    if next_command:
        next_html = f'<p class="mono">{esc(next_command)}</p>'
    else:
        next_html = f'<p class="empty"><span class="msg">{esc(empty_text)}</span></p>'

    title = f'{s["project"]} / {s["slug"]}'
    body = (
        f"<h2>{esc(title)}</h2>"
        f'<p>{tpl_table.render_badge(s["status"], theme.status_sem(s["status"]))} '
        f'<span class="chip">{esc(s["stage"])}</span></p>'
        f'<p class="note">{esc(s["idea"])}</p>'
        f"<h3>Gate</h3>{gate_html}"
        f"<h3>Steps</h3>{ladder_html}"
        f"<h3>Next</h3>{next_html}"
        f'<h3 data-i18n="log">{esc(label_bundle.get("log", "log"))}</h3>{log_html}'
    )
    return render_page_shell(
        title, "flows", label_bundle.get("flows", "Flows"), lang, languages,
        manifest_labels, body, footer_state_path=str(s["dir"]),
    )


def render_board(flows_root: pathlib.Path, render_page_shell, lang, languages, manifest_labels, label_bundle):
    """The former cmd_flows body: writes the flows index plus every per-flow
    page under flows_root.parent / "_board" / "flows". Returns the number of
    flows found (for the caller's own print/report)."""
    flows_root = pathlib.Path(flows_root)
    summaries = []
    for fj in sorted(flows_root.glob("*/*/flow.json")):
        s = _flow_summary(fj)
        if s is not None:
            summaries.append(s)
    summaries.sort(key=lambda s: (0 if s["awaiting"] else 1, s["project"], s["slug"]))

    out_root = flows_root.parent / "_board" / "flows"
    out_root.mkdir(parents=True, exist_ok=True)

    for s in summaries:
        per_flow_dir = out_root / s["project"] / s["slug"]
        per_flow_dir.mkdir(parents=True, exist_ok=True)
        html_out = _render_flow_page(s, lang, languages, manifest_labels, label_bundle, render_page_shell)
        (per_flow_dir / "index.html").write_text(html_out, encoding="utf-8")
        (per_flow_dir / "index.json").write_text(
            json.dumps(json_safe({
                "project": s["project"], "slug": s["slug"], "stage": s["stage"],
                "status": s["status"], "awaiting": s["awaiting"], "gate": s["gate"],
                "updatedAt": s["updated_at"],
            }), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    index_html = _render_flows_index(summaries, lang, languages, manifest_labels, label_bundle, render_page_shell)
    (out_root / "index.html").write_text(index_html, encoding="utf-8")
    (out_root / "index.json").write_text(
        json.dumps(json_safe({
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "flows": [
                {"project": s["project"], "slug": s["slug"], "stage": s["stage"],
                 "status": s["status"], "awaiting": s["awaiting"], "updatedAt": s["updated_at"]}
                for s in summaries
            ],
        }), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"wrote {out_root / 'index.html'} ({len(summaries)} flow(s))")
    return out_root
