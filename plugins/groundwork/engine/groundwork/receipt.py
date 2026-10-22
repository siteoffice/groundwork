"""receipt.py - the local receipt written when a change is accepted at the G4 ship gate.

One accepted change gives two files in <flow_dir>\\receipts: a JSON file for machines and a
readable HTML page. Both say plainly that the receipt is self-issued and not signed: it is a
record you keep for yourself, not proof to a third party.

Only a G4 verdict of "ship" writes a receipt. "hold", "park" and a rejected gate write nothing.
Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import hashlib
import html
import json
import pathlib

LABEL = "self-issued, not signed"
SCHEMA = 1


def _log_digest(flow_dir):
    log = pathlib.Path(flow_dir) / "flow-log.ndjson"
    if not log.exists():
        return None, 0
    data = log.read_bytes()
    return hashlib.sha256(data).hexdigest(), data.count(b"\n")


def build_receipt(flow, gate, flow_dir):
    decision = gate.get("decision") or {}
    index = gate.get("step_index")
    steps = flow.get("steps") or []
    title = ""
    if isinstance(index, int) and 0 <= index < len(steps):
        title = steps[index].get("title", "")
    digest, lines = _log_digest(flow_dir)
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "flow_id": flow.get("flow_id"),
        "project": flow.get("project"),
        "slug": flow.get("slug"),
        "idea": flow.get("idea"),
        "step_index": index,
        "step_title": title,
        "spec_path": flow.get("spec_path"),
        "gate": gate.get("id"),
        "gate_token": gate.get("token"),
        "verdict": decision.get("verdict"),
        "note": decision.get("note", ""),
        "surface": decision.get("surface"),
        "decided_at": decision.get("decided_at"),
        "flow_log_lines": lines,
        "flow_log_sha256": digest,
    }


_CSS = (
    ":root{--bg:#fff;--fg:#1a1a1a;--mute:#555;--line:#ddd}"
    "@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mute:#aaa;--line:#333}}"
    "body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;"
    "max-width:42rem;margin:2rem auto;padding:0 1rem}"
    "h1{font-size:1.4rem}.label{display:inline-block;border:1px solid var(--line);"
    "padding:.1rem .6rem;border-radius:1rem;color:var(--mute)}"
    "table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;"
    "padding:.4rem .6rem;border-top:1px solid var(--line);overflow-wrap:anywhere}"
    "th{width:9rem;color:var(--mute);font-weight:600}"
)


def render_page(r):
    def esc(v):
        return html.escape("" if v is None else str(v))

    rows = [
        ("Project", r["project"]),
        ("Change", r["step_title"] or r["idea"]),
        ("Idea", r["idea"]),
        ("Decision", "%s at %s" % (r["verdict"], r["gate"])),
        ("Decided at (UTC)", r["decided_at"]),
        ("Decided in", r["surface"]),
        ("Note", r["note"]),
        ("Spec", r["spec_path"]),
        ("Gate token", r["gate_token"]),
        ("Flow log", "%s lines, sha256 %s" % (r["flow_log_lines"], r["flow_log_sha256"])),
    ]
    body = "\n".join("<tr><th>%s</th><td>%s</td></tr>" % (esc(k), esc(v)) for k, v in rows)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Receipt: " + esc(r["step_title"] or r["slug"]) + "</title>\n"
        "<style>" + _CSS + "</style></head><body>\n"
        "<h1>Receipt for an accepted change</h1>\n"
        '<p class="label">' + esc(LABEL) + "</p>\n"
        "<p>This page records a decision you made. Nobody has signed it and nobody else can "
        "check it. Keep it with the project.</p>\n"
        "<table>\n" + body + "\n</table>\n</body></html>\n"
    )


def write_receipt(flow, gate, flow_dir):
    """Write the JSON and HTML receipt for a decided G4 'ship' gate. Returns (json, html) paths."""
    flow_dir = pathlib.Path(flow_dir)
    rec = build_receipt(flow, gate, flow_dir)
    n = (rec["step_index"] if isinstance(rec["step_index"], int) else 0) + 1
    out = flow_dir / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    stem = "step-%02d" % n
    jpath, hpath = out / (stem + ".json"), out / (stem + ".html")
    jpath.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    hpath.write_text(render_page(rec), encoding="utf-8")
    return jpath, hpath
