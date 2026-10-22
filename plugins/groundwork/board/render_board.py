"""Siteoffice recursive board renderer.

CLI: node <dir> | tree <root> | check <root> | export-hours <root> | as-of
<dir> <date>. Stdlib only, Python 3.8+, no pip installs, no network calls
(as-of's git clone/checkout is local-only, never a remote fetch). Reads a
node's siteoffice.json (schema v1 or v2) and the manifests of its declared
children, and writes exactly `<dir>\\_board\\board.html` (one self-contained
file, no <link>, no <script src>, no CDN, no fetch) and `<dir>\\_board\\
board.json` (the same data, for machines and for a parent board). Writes
nowhere else, except as-of's own `board.asof-<date>.html`, alongside, never
over today's board.html.

Steps 4 to 10 of sprint 1 (02_roadmap\\2026-09-11_sprint1_steps_4-6_guide.md,
02_roadmap\\2026-09-11_sprint1_steps_7-10_guide.md): `node`, `tree`, `check`
and `export-hours` all do work. Seven panels (waiting_on_you, next_weeks,
roadmap, children, activity, funnel, hours) collect and render real data;
status and sync_status stay empty/loading shells until the flow and sprint
board kinds row (steps 9 to 10) fills them in further.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import theme  # noqa: E402  (needs the sys.path insert above)
from board_common import esc, json_safe, load_module_from_path  # noqa: E402
# board.kinds.flow / board.kinds.sprint (decision 13, 2026-09-12 resume
# guide, step 10b): the flow and sprint board kinds, moved out to keep this
# file under its own 60,000-byte guard. render_board.py imports the kinds;
# never the other way round (no import cycle).
import kinds.flow as kind_flow  # noqa: E402
import kinds.sprint as kind_sprint  # noqa: E402

# Loaded by explicit path, not `import hours`: board\templates\hours.py is
# also a top-level module named "hours" (imported below as tpl_hours); a
# plain import here would race with that on sys.modules['hours'].
hours = load_module_from_path("_siteoffice_hours_reader_cli", HERE / "hours.py")

TEMPLATES_DIR = HERE / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))
import waiting_on_you as tpl_waiting_on_you  # noqa: E402
import next_weeks as tpl_next_weeks  # noqa: E402
import roadmap as tpl_roadmap  # noqa: E402
import children as tpl_children  # noqa: E402
import activity as tpl_activity  # noqa: E402
import funnel as tpl_funnel  # noqa: E402
import receipts as tpl_receipts  # noqa: E402
import status as tpl_status  # noqa: E402
import table as tpl_table  # noqa: E402
tpl_hours = load_module_from_path("_siteoffice_hours_panel", TEMPLATES_DIR / "hours.py")

LABELS_DIR = HERE / "labels"
LANGUAGE_ALIASES = {"no": "nb"}

NODE_TYPES = ("holding", "hub", "company", "department", "project")

# Grid panels (architecture note 5.2/5.3): STATUS (department/project layer
# 1), waiting on you, the horizon panel, the funnel kanban, the roadmap with
# its backlog, the hours panel, the sync-status live value (the only panel
# with a loading state). Children and activity render as strips below the
# grid, not as grid panels (see render_children_strip, render_activity_strip).
PANEL_KEYS = (
    "status", "waiting_on_you", "next_weeks", "funnel",
    "roadmap", "hours", "sync_status", "receipts",
)

# DOM order per node type (DESIGN.md, architecture note 5.2: "layer order is
# the DOM order, do not reorder by CSS"). Holding and hub lead with waiting
# on you; company, department and project lead with STATUS plus the horizon
# panel. Only holding and project types exist in the real tree today; company
# and department are approximated on the same order as project, the closest
# read of DESIGN.md's "its projects with status colour and next gate" /
# "STATUS and Next N weeks" until a real node of those types needs it exactly.
_HOLDING_HUB_PANEL_ORDER = (
    "waiting_on_you", "next_weeks", "funnel", "roadmap", "hours", "sync_status", "status",
    "receipts",
)
_DEFAULT_PANEL_ORDER = (
    "status", "next_weeks", "waiting_on_you", "roadmap", "funnel", "hours", "sync_status",
    "receipts",
)


def _default_layout_for(node_type: str) -> dict:
    """Generated backward-compatible default (spec point 2): each panel from
    today's fixed tuple gets one full-width row, in tuple order. Columns is
    fixed at 4, the widest breakpoint; every panel's width equals columns,
    so the row is full-width regardless of viewport.
    """
    order = _HOLDING_HUB_PANEL_ORDER if node_type in ("holding", "hub") else _DEFAULT_PANEL_ORDER
    return {
        "columns": 4,
        "panels": [
            {"id": key, "col": 1, "row": i + 1, "w": 4, "h": 1}
            for i, key in enumerate(order)
        ],
    }


def _validate_layout(raw_layout, node_type: str):
    """(layout, note). note is None when raw_layout was already valid;
    otherwise layout is the generated default and note is the amber string
    for settings['_layout_note'] (same mechanism _horizon_note uses, lines
    196-200). Never raises: every check below runs before the value it
    guards is indexed.
    """
    note = "settings: layout is malformed, using the generated default"

    if not isinstance(raw_layout, dict):
        return _default_layout_for(node_type), note
    columns = raw_layout.get("columns")
    if not isinstance(columns, int) or isinstance(columns, bool) or columns not in (1, 2, 3, 4):
        return _default_layout_for(node_type), note
    panels = raw_layout.get("panels")
    if not isinstance(panels, list):
        return _default_layout_for(node_type), note

    checked = []
    for entry in panels:
        if not isinstance(entry, dict):
            return _default_layout_for(node_type), note
        if not all(k in entry for k in ("id", "col", "row", "w", "h")):
            return _default_layout_for(node_type), note
        if entry["id"] not in PANEL_KEYS:
            return _default_layout_for(node_type), note
        if not isinstance(entry["col"], int) or isinstance(entry["col"], bool) or entry["col"] not in (1, 2, 3, 4):
            return _default_layout_for(node_type), note
        if not isinstance(entry["w"], int) or isinstance(entry["w"], bool) or entry["w"] not in (1, 2, 3, 4):
            return _default_layout_for(node_type), note
        if not isinstance(entry["row"], int) or isinstance(entry["row"], bool) or entry["row"] < 1:
            return _default_layout_for(node_type), note
        if not isinstance(entry["h"], int) or isinstance(entry["h"], bool) or entry["h"] < 1:
            return _default_layout_for(node_type), note
        if entry["col"] + entry["w"] - 1 > columns:
            return _default_layout_for(node_type), note
        checked.append(entry)

    return {"columns": raw_layout["columns"], "panels": checked}, None


def _resolve_layout(layout: dict) -> list:
    """Ordered, placed panel list, replacing the removed fixed-tuple lookup. Declared
    panels first, sorted row then col (DOM order follows the resolved
    layout's row-then-col order, architecture note 5.6); any PANEL_KEYS id
    with real content that layout['panels'] omits is appended afterward,
    collapsed one per row at the bottom, in PANEL_KEYS declaration order
    (spec point 4). layout must already be validated (Task 1's
    _validate_layout / _default_layout_for guarantee this shape).
    """
    declared = list(layout.get("panels") or [])
    declared_ids = {p["id"] for p in declared}
    columns = layout.get("columns", 4)

    max_row = max((p["row"] for p in declared), default=0)
    omitted = [key for key in PANEL_KEYS if key not in declared_ids]
    appended = [
        {"id": key, "col": 1, "row": max_row + 1 + i, "w": columns, "h": 1}
        for i, key in enumerate(omitted)
    ]

    return sorted(declared, key=lambda p: (p["row"], p["col"])) + appended


def _compact_rows(entries: list, disabled_keys: set) -> list:
    """Renumber rows so a row vacated entirely by disabled panels closes,
    without disturbing a row occupied by a surviving panel's own h-span
    (spec: h >= 1 rows are reserved, not compactable). disabled_keys is
    the set of panel ids that will not render at all this pass.
    """
    def _occupied(items):
        rows = set()
        for e in items:
            rows.update(range(e["row"], e["row"] + e["h"]))
        return rows

    before = _occupied(entries)
    surviving = [e for e in entries if e["id"] not in disabled_keys]
    after = _occupied(surviving)
    closed = sorted(before - after)

    def _shift(row):
        return row - sum(1 for c in closed if c < row)

    return [{**e, "row": _shift(e["row"])} for e in surviving]


def _panels_grid_css(columns: int) -> str:
    """Per-node responsive CSS for the .panels grid, since a shared static
    string cannot encode a node's own declared columns count. Widest tier
    (1200px+) uses the node's real column count so its authored placement
    renders exactly as declared; every narrower width collapses every panel
    to one full-width column in DOM order, since authored col/row values
    assume the widest tier's coordinate space and cannot be remapped onto a
    narrower track count without a second authoring system this spec does
    not define (Wilco's decision, 2026-09-12: N columns wide, 1 column
    anything narrower, all the way to phone).
    """
    return (
        "  .panels { display: grid; grid-template-columns: 1fr; gap: 18px; }\n"
        f"  @media (min-width: 1200px) {{ .panels {{ grid-template-columns: "
        f"repeat({columns}, 1fr); }} }}\n"
        "  @media (max-width: 1199px) { .panels .panel { grid-column: 1 / -1 !important; "
        "grid-row: auto !important; } }"
    )

# Panels with a real collect()/render() module this row, keyed the same as
# their data-panel attribute. "children" and "activity" are strips, not
# PANEL_KEYS grid members, but share this dispatch table.
_REAL_PANEL_MODULES = {
    "waiting_on_you": tpl_waiting_on_you,
    "next_weeks": tpl_next_weeks,
    "roadmap": tpl_roadmap,
    "children": tpl_children,
    "activity": tpl_activity,
    "funnel": tpl_funnel,
    "hours": tpl_hours,
    "receipts": tpl_receipts,
    "status": tpl_status,
}

# Directory names never worth descending into while hunting for
# siteoffice.json files (tree command): version control internals,
# dependency trees and caches can be large and slow on this mount.
_TREE_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "_board",
    ".venv", "venv", ".pytest_cache", "dist", "build",
}


# ===========================================================================
# Schema v1/v2 manifest reader
# ===========================================================================
_MAX_ANCESTOR_WALK = 8


def _read_raw(node_dir: pathlib.Path) -> dict:
    path = node_dir / "siteoffice.json"
    if not path.exists():
        raise FileNotFoundError(f"no siteoffice.json in {node_dir}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    for key in ("name", "type"):
        if key not in raw:
            raise ValueError(f"{path}: missing required key '{key}'")
    if raw["type"] not in NODE_TYPES:
        raise ValueError(f"{path}: key 'type' is '{raw['type']}', must be one of {NODE_TYPES}")

    raw = dict(raw)
    raw["_path"] = path
    raw["_dir"] = node_dir
    return raw


def _find_root_raw(node_dir: pathlib.Path, self_raw: dict) -> dict:
    """Walk up the directory tree to the manifest that carries this node's
    inherited settings and labels (architecture note 4.4 option B). A node
    with no 'parent' key, or an empty one, is already a root: no walk. Each
    step looks for a siteoffice.json in an ancestor directory (children live
    inside their parent per the unit convention); the walk stops as soon as
    the found manifest itself has no 'parent', or when no further ancestor
    carries a manifest, whichever comes first. A node whose logical parent
    is not a physical ancestor (pre-Siteoffice-convention layout) simply
    gets no inheritance: it stands as its own root.
    """
    current_dir, current_raw = node_dir, self_raw
    steps = 0
    while (current_raw.get("parent") or "") and steps < _MAX_ANCESTOR_WALK:
        found_dir = None
        probe = current_dir.parent
        for _ in range(_MAX_ANCESTOR_WALK):
            if probe == probe.parent:  # filesystem root, stop
                break
            if (probe / "siteoffice.json").exists():
                found_dir = probe
                break
            probe = probe.parent
        if found_dir is None:
            break
        current_dir = found_dir
        current_raw = _read_raw(found_dir)
        steps += 1
    return current_raw


def load_manifest(node_dir: pathlib.Path) -> dict:
    raw = _read_raw(node_dir)
    root_raw = _find_root_raw(node_dir, raw)
    inherits = root_raw is not raw

    manifest = dict(raw)  # v2 files are read as-is; unknown keys are kept

    language = manifest.get("language", "en")

    settings = {}
    if inherits:
        settings.update(root_raw.get("settings") or {})
    settings.update(manifest.get("settings") or {})
    settings.setdefault("planningHorizonWeeks", 2)
    settings.setdefault("languages", [language])
    settings.setdefault("timeRegistration", False)
    settings.setdefault("refreshDays", 7)
    valid_languages = (
        isinstance(settings["languages"], list)
        and bool(settings["languages"])
        and all(isinstance(x, str) for x in settings["languages"])
    )
    if not valid_languages:
        settings["languages"] = [language]
    if settings["planningHorizonWeeks"] not in (1, 2, 3, 4):
        settings["_horizon_note"] = (
            "settings: planningHorizonWeeks must be 1 to 4, using 2"
        )
        settings["planningHorizonWeeks"] = 2
    if "layout" not in settings:
        settings["layout"] = _default_layout_for(manifest["type"])
    else:
        layout, note = _validate_layout(settings["layout"], manifest["type"])
        settings["layout"] = layout
        if note:
            settings["_layout_note"] = note
    manifest["settings"] = settings

    labels = {}
    if inherits:
        for lang, kv in (root_raw.get("labels") or {}).items():
            labels[lang] = dict(kv or {})
    for lang, kv in (manifest.get("labels") or {}).items():
        labels.setdefault(lang, {})
        labels[lang].update(kv or {})
    manifest["labels"] = labels

    # owners: flat path->owner-id map, sibling to settings/labels/profiles
    # (sync note Part 1 section 3; sync\owner_check.py re-derives this same
    # inheritance independently for its own refuse-rule resolution, since
    # sync\ never imports board\). No per-language nesting like labels, so
    # this is a plain update() same as settings' inheritance above.
    #
    # Deliberate, ruled behavior, not a bug: every key in the merged map is
    # relative to the ROOT manifest's own directory, never to whichever
    # child node it ends up copied into. inherits copies root_raw["owners"]
    # into every descendant unchanged, exactly like labels above, so a root
    # key such as "docs" also matches a same-named "docs" subfolder that
    # happens to live under a child node's own directory, not only the
    # root's own "docs" folder. A per-manifest-relative reading (each
    # node's owners keys scoped to that node's own directory) was
    # considered and explicitly rejected: keep the current, simpler,
    # labels-like model. See sync\owner_check.py's merged_owners() for the
    # matching comment on the sync side, and sync\tests\test_owner_check.py
    # for the pinned cross-directory case.
    owners = {}
    if inherits:
        owners.update(root_raw.get("owners") or {})
    owners.update(manifest.get("owners") or {})
    manifest["owners"] = owners

    manifest.setdefault("display_name", manifest["name"])
    manifest.setdefault("profiles", {})
    manifest.setdefault("children", [])
    return manifest


# ===========================================================================
# Labels: per-language default files, node overrides, fallback chain
# ===========================================================================
_LABEL_CACHE: dict = {}


def load_label_file(tag: str) -> dict:
    tag = LANGUAGE_ALIASES.get(tag, tag)
    if tag not in _LABEL_CACHE:
        path = LABELS_DIR / f"{tag}.json"
        try:
            _LABEL_CACHE[tag] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LABEL_CACHE[tag] = {}
    return _LABEL_CACHE[tag]


def resolve_label(key: str, lang: str, manifest_labels: dict) -> str:
    """Fallback chain, per key: node labels, requested language, en, key."""
    node_map = (manifest_labels or {}).get(lang) or {}
    if key in node_map:
        return node_map[key]
    v = (load_label_file(lang).get("labels") or {}).get(key)
    if v is not None:
        return v
    if lang != "en":
        v = (load_label_file("en").get("labels") or {}).get(key)
        if v is not None:
            return v
    return key


def resolve_plural(key: str, n: int, lang: str, manifest_labels: dict) -> str:
    form = f"{key}_one" if n == 1 else f"{key}_other"
    text = resolve_label(form, lang, manifest_labels)
    return text.replace("{n}", str(n))


def all_labels_for(lang: str, manifest_labels: dict) -> dict:
    """Every key this language's default file plus node overrides know
    about, flattened, for the client-side language switch (architecture
    note 4.3: the switch swaps labels without a re-render)."""
    merged = dict(load_label_file(lang).get("labels") or {})
    merged.update((manifest_labels or {}).get(lang) or {})
    return merged


# ===========================================================================
# HTML building
# ===========================================================================
BOARD_CSS = r"""  .board-head { padding: 18px 28px 14px; border-bottom: 1px solid var(--hairline); }
  .board-top { display: flex; align-items: baseline; justify-content: space-between; gap: 18px; flex-wrap: wrap; }
  .board-type { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; display: block; }
  .board-head h1 { margin: 2px 0 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
  .board-controls { display: none; align-items: center; gap: 10px; }
  body.js .board-controls { display: flex; }
  [hidden] { display: none !important; }
  .layout-save-bar { display: inline-flex; align-items: center; gap: 8px; margin-right: 6px; }
  .layout-save-btn {
    display: inline-flex; border: 1px solid var(--mint-line); border-radius: 20px;
    background: var(--mint-fill); color: var(--mint-bright); padding: 5px 12px; font-size: 11px;
    font-weight: 600; cursor: pointer; font-family: var(--font); transition: opacity 120ms ease;
  }
  .layout-save-btn:hover { opacity: 0.85; }
  .layout-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .layout-save-msg { font-size: 11px; color: var(--muted); }
  .layout-save-msg.err { color: var(--red-bright); }
  .skip-link {
    position: absolute; left: -9999px; top: 0; z-index: 100;
    background: var(--panel); color: var(--text); padding: 8px 14px;
    border: 1px solid var(--mint-line); border-radius: var(--radius-sm);
  }
  .skip-link:focus { left: 12px; top: 12px; }
  .breadcrumb { margin-top: 10px; color: var(--muted); font-size: 12px; }
  .breadcrumb a { color: var(--muted); }
  .freshness { margin-top: 6px; color: var(--muted); font-size: 12px; }
  main.board-main { padding: 22px 28px 60px; max-width: 1400px; margin: 0 auto; }
  .panels .panel { margin-bottom: 0; }
  .panels .panel .head { cursor: grab; user-select: none; -webkit-user-select: none; }
  .panels .panel .head:active { cursor: grabbing; }
  .panels .panel.dragging { opacity: 0.5; }
  .panels .panel.drag-over { outline: 2px dashed var(--mint-line); outline-offset: -2px; }
  .panel .empty .glyph { display: none; }
  .strip { margin-top: 18px; }
  .strip > .head h2 { margin: 0; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
  .children-cards { display: flex; flex-wrap: wrap; gap: 10px; }
  .funnel-board { display: flex; gap: 12px; overflow-x: auto; }
  .funnel-col { flex: 0 0 200px; min-width: 200px; }
  .funnel-col-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 6px; }
  .funnel-col-head h3 { margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  .funnel-col-cards { display: flex; flex-direction: column; gap: 8px; }
  .funnel-card { border: 1px solid var(--hairline); border-radius: var(--radius-sm); padding: 8px 10px; }
  .funnel-card .ct { margin: 0 0 4px; font-size: 12.5px; }
  .funnel-card .cm { margin: 0; font-size: 11px; color: var(--muted); }
  .board-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12.5px; }
  .board-table th, .board-table td { padding: 6px 10px; border-bottom: 1px solid var(--hairline); text-align: left; vertical-align: top; }
  .board-table th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.06em; }
  .queue-block { border: 1px solid var(--amber-line); background: var(--amber-fill); border-radius: var(--radius-sm); padding: 12px 16px; margin-bottom: 18px; }
  @media (max-width: 699px) { .board-table { display: block; overflow-x: auto; } }

  /* Roadmap date-range timeline (plan item 1.11, pattern 1): three
     server-rendered zoom variants share one track, JS only swaps which one
     is visible, no client-side date math. */
  .tl-zoom-toggle, .slice-rail { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 10px; }
  .tl-zoom-btn, .slice-btn {
    font-family: var(--mono); font-size: 11px; padding: 3px 10px; border-radius: 20px;
    border: 1px solid var(--hairline-2); background: transparent; color: var(--muted); cursor: pointer;
  }
  .tl-zoom-btn.tl-zoom-active, .slice-btn.slice-active {
    border-color: var(--mint-line); color: var(--text); background: var(--mint-fill);
  }
  .tl-variant { display: none; }
  .tl-variant.tl-active { display: block; }
  .tl-track { position: relative; height: 28px; border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); margin-bottom: 4px; }
  .tl-mark, .tl-gate { position: absolute; top: 0; bottom: 0; }
  .tl-mark { width: 8px; height: 8px; margin-top: 10px; border-radius: 50%; background: var(--mint); transform: translateX(-4px); }
  .tl-gate { border-left: 2px dashed var(--red-line); }
  .tl-gate-label { position: absolute; top: -2px; left: 4px; font-family: var(--mono); font-size: 10px; color: var(--red); white-space: nowrap; }
  .tl-range { display: flex; justify-content: space-between; font-family: var(--mono); font-size: 10.5px; color: var(--muted); margin-bottom: 12px; }

  /* Group headers with sums (plan item 1.11, pattern 2): the roadmap
     backlog and the hours panel both group their rows under a header that
     carries the group's own count/sum, instead of a flat list. */
  .groups, .backlog-groups { display: flex; flex-direction: column; gap: 10px; }
  .group-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .group-head h3 { margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  .group ul { margin: 4px 0 0; }"""


def _print_style() -> str:
    """The one @media print block (guide step 14, architecture note 5.5).
    Uses only the print block and the light theme from tokens.json (read via
    theme._TOKENS, the same parsed dict theme.py itself builds from; no new
    hex is typed here, board\\ stays the only place theme values are typed):
    every custom property this overrides is one the rest of the page's own
    CSS already reads by that same name, so the whole page repaints in the
    print palette without a second copy of any selector. Status pills lose
    their colour and dot, leaving the plain status word as note 5.5 asks;
    the header's theme/language switches are hidden; the freshness line and
    table heads are untouched (kept, and thead already repeats per page by
    default)."""
    p = theme._TOKENS["print"]
    light = theme._TOKENS["themes"]["light"]
    return f"""  @media print {{
    :root {{
      --bg: {p['bg']}; --panel: {light['surface']}; --panel-2: {light['surface2']};
      --text: {p['text']}; --muted: {p['textMuted']};
      --hairline: {p['line']}; --hairline-2: {p['line']};
      --mint: {p['accent']}; --mint-bright: {p['accent']};
    }}
    .board-controls {{ display: none; }}
    .badge::before {{ display: none; }}
    .badge {{ background: transparent; border: 1px solid var(--hairline); }}
    .board-table thead {{ display: table-header-group; }}
  }}"""

BOARD_JS = r"""(function () {
  "use strict";
  var THEME_KEY = "znd-theme", LANG_KEY = "znd-lang";
  var THEME_ORDER = ["system", "light", "dark"];
  var LANGS = window.__ZND_BOARD_LANGS__ || [];
  var LABELS = window.__ZND_BOARD_LABELS__ || {};

  function readStored(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function writeStored(key, val) {
    try { localStorage.setItem(key, val); } catch (e) { /* file:// may block storage */ }
  }
  function systemPrefersLight() {
    try { return window.matchMedia("(prefers-color-scheme: light)").matches; } catch (e) { return false; }
  }

  function applyTheme(choice) {
    if (choice === "light" || choice === "dark") {
      document.body.setAttribute("data-theme", choice);
    } else {
      document.body.removeAttribute("data-theme");
    }
    var lbl = document.getElementById("theme-toggle-label");
    if (lbl) lbl.textContent = choice.charAt(0).toUpperCase() + choice.slice(1);
  }

  function applyLang(lang) {
    document.documentElement.setAttribute("lang", lang);
    document.documentElement.setAttribute("dir", "ltr");
    var set = LABELS[lang] || {};
    var i = 0;
    var nodes = document.querySelectorAll("[data-i18n]");
    for (i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute("data-i18n");
      if (set.hasOwnProperty(key)) nodes[i].textContent = set[key];
    }
    var tpl = document.querySelectorAll("[data-i18n-template]");
    for (i = 0; i < tpl.length; i++) {
      var tkey = tpl[i].getAttribute("data-i18n-template");
      var text = set.hasOwnProperty(tkey) ? set[tkey] : tkey;
      text = text.replace("{time}", tpl[i].getAttribute("data-time") || "");
      text = text.replace("{count}", tpl[i].getAttribute("data-count") || "");
      text = text.replace("{n}", tpl[i].getAttribute("data-n") || "");
      tpl[i].textContent = text;
    }
    var langLbl = document.getElementById("lang-toggle-label");
    if (langLbl) langLbl.textContent = lang.toUpperCase();
  }

  var storedTheme = readStored(THEME_KEY);
  var effectiveTheme = storedTheme || (systemPrefersLight() ? "light" : "system-dark");
  applyTheme(storedTheme || "system");

  var storedLang = readStored(LANG_KEY);
  var initialLang = (storedLang && LANGS.indexOf(storedLang) !== -1) ? storedLang : (LANGS[0] || "en");
  if (storedLang) applyLang(initialLang);

  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var cur = readStored(THEME_KEY) || "system";
      var next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
      writeStored(THEME_KEY, next);
      applyTheme(next);
    });
  }
  var langBtn = document.getElementById("lang-toggle");
  if (langBtn && LANGS.length > 1) {
    langBtn.addEventListener("click", function () {
      var cur = readStored(LANG_KEY) || LANGS[0];
      var next = LANGS[(LANGS.indexOf(cur) + 1) % LANGS.length];
      writeStored(LANG_KEY, next);
      applyLang(next);
    });
  }

  // sync_status.js (SYNC_STATUS_CONTRACT.md) has already had its chance to
  // run by now (script tags execute in document order); a 404 there is a
  // silent no-op in a browser, so window.ZND_SYNC_STATUS is simply absent.
  var syncEl = document.getElementById("sync-status-body");
  var syncBtn = document.getElementById("sync-btn");
  var approveBtn = document.getElementById("approve-btn");

  function renderSyncFallback() {
    if (!syncEl) return;
    var syncSet = LABELS[initialLang] || {};
    var s = window.ZND_SYNC_STATUS;
    if (syncBtn) syncBtn.hidden = true;
    if (approveBtn) approveBtn.hidden = true;
    if (s && typeof s === "object") {
      var bits = [];
      if (s.node) bits.push(s.node);
      if (s.state) bits.push(s.state);
      if (s.lastPull) bits.push("pull " + s.lastPull);
      if (s.lastPush) bits.push("push " + s.lastPush);
      if (typeof s.behind === "number" && s.behind) bits.push(s.behind + " behind");
      if (typeof s.ahead === "number" && s.ahead) bits.push(s.ahead + " ahead");
      if (s.conflicts && s.conflicts.length) bits.push(s.conflicts.length + " conflict(s)");
      if (s.message) bits.push(s.message);
      syncEl.textContent = bits.join(" · ");
      syncEl.className = "empty" + (s.state === "ok" ? "" : " warn");
    } else {
      syncEl.textContent = syncSet.no_sync_status || "No sync status.";
      syncEl.className = "empty";
    }
  }

  var lastSyncBody = null;

  function renderSyncPanel(body) {
    lastSyncBody = body;
    if (!syncEl) return;
    var hideBoth = body.state === "conflict" || body.state === "refused" || body.state === "error"
      || body.state === "timeout";
    if (hideBoth) {
      if (syncBtn) syncBtn.hidden = true;
      if (approveBtn) approveBtn.hidden = true;
    } else {
      if (syncBtn) { syncBtn.hidden = false; syncBtn.disabled = false; }
      if (approveBtn) { approveBtn.hidden = !body.hasLocalChanges; approveBtn.disabled = false; }
    }
    var bits = [];
    if (body.node) bits.push(body.node);
    if (body.state) bits.push(body.state);
    if (body.lastPull) bits.push("pull " + body.lastPull);
    if (body.lastPush) bits.push("push " + body.lastPush);
    if (typeof body.behind === "number" && body.behind) bits.push(body.behind + " behind");
    if (typeof body.ahead === "number" && body.ahead) bits.push(body.ahead + " ahead");
    if (body.conflicts && body.conflicts.length) bits.push(body.conflicts.length + " conflict(s)");
    if (body.message) bits.push(body.message);
    syncEl.textContent = bits.join(" · ");
    syncEl.className = "empty" + (body.state === "ok" ? "" : " warn");
  }

  function _postJson(url, payload, cb) {
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (resp) { return resp.json(); })
      .then(function (body) { cb(body); })
      .catch(function () { cb(null); });
  }

  function onSyncClick() {
    if (!syncBtn) return;
    syncBtn.disabled = true;
    var op = "pull";
    if (lastSyncBody) {
      if (lastSyncBody.state === "behind") op = "pull";
      else if (lastSyncBody.state === "ok" && lastSyncBody.ahead > 0) op = "push";
    }
    _postJson("http://127.0.0.1:8765/sync", { op: op }, function (body) {
      syncBtn.disabled = false;
      if (body) renderSyncPanel(body);
    });
  }

  function onApproveClick() {
    if (!approveBtn) return;
    approveBtn.disabled = true;
    var message = "Approved via board, " + new Date().toISOString();
    _postJson("http://127.0.0.1:8765/commit", { message: message }, function (body) {
      approveBtn.disabled = false;
      if (body) renderSyncPanel(body);
    });
  }

  function wireSyncButtons() {
    if (syncBtn && !syncBtn._wired) {
      syncBtn._wired = true;
      syncBtn.addEventListener("click", function () { onSyncClick(); });
    }
    if (approveBtn && !approveBtn._wired) {
      approveBtn._wired = true;
      approveBtn.addEventListener("click", function () { onApproveClick(); });
    }
  }

  function probeSyncHelper() {
    if (!syncEl) return;
    var node = encodeURIComponent(window.__ZND_SYNC_NODE__ || "");
    var url = "http://127.0.0.1:8765/status?node=" + node + "&clone=";
    var opts = {};
    try { opts.signal = AbortSignal.timeout(1500); } catch (e) { /* older engine, no timeout */ }
    fetch(url, opts)
      .then(function (resp) {
        if (!resp.ok) throw new Error("bad status " + resp.status);
        return resp.json();
      })
      .then(function (body) { renderSyncPanel(body); wireSyncButtons(); })
      .catch(function () { renderSyncFallback(); });
  }

  // Drag-and-drop (item 1.10): swaps two panels' inline grid placement, in the DOM and via /layout.
  function _parseGridPlacement(el) {
    function part(value) {
      var m = /^(\d+)\s*\/\s*span\s*(\d+)$/.exec(value || "");
      return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : [1, 1];
    }
    var col = part(el.style.gridColumn);
    var row = part(el.style.gridRow);
    return { col: col[0], w: col[1], row: row[0], h: row[1] };
  }

  function _applyGridPlacement(el, pos) {
    el.style.gridColumn = pos.col + " / span " + pos.w;
    el.style.gridRow = pos.row + " / span " + pos.h;
  }

  function getClientLabel(key) {
    var lang = document.documentElement.getAttribute("lang") || "en";
    var set = LABELS[lang] || LABELS["en"] || {};
    return set[key] || key;
  }

  function saveLayout() {
    var container = document.querySelector(".panels");
    if (!container) return;
    var columns = parseInt(container.getAttribute("data-layout-columns"), 10) || 4;
    var panels = container.querySelectorAll(".panel");
    var entries = [];
    for (var i = 0; i < panels.length; i++) {
      var pos = _parseGridPlacement(panels[i]);
      entries.push({
        id: panels[i].getAttribute("data-panel"),
        col: pos.col, row: pos.row, w: pos.w, h: pos.h,
      });
    }
    var layoutData = { columns: columns, panels: entries };
    var layoutKey = "znd-layout:" + (window.__ZND_SYNC_NODE__ || "default");
    writeStored(layoutKey, JSON.stringify(layoutData));

    // Best-effort post to local sync helper if available
    _postJson("http://127.0.0.1:8765/layout", { layout: layoutData }, function () {});
  }

  function restoreSavedLayout() {
    var layoutKey = "znd-layout:" + (window.__ZND_SYNC_NODE__ || "default");
    var raw = readStored(layoutKey);
    if (!raw) return;
    try {
      var saved = JSON.parse(raw);
      if (!saved || !saved.panels) return;
      var panels = saved.panels;
      var container = document.querySelector(".panels");
      if (!container) return;
      for (var i = 0; i < panels.length; i++) {
        var entry = panels[i];
        var el = container.querySelector('.panel[data-panel="' + entry.id + '"]');
        if (el) {
          _applyGridPlacement(el, entry);
          container.appendChild(el);
        }
      }
    } catch (e) {}
  }

  var saveBar = document.getElementById("layout-save-bar");
  var saveBtn = document.getElementById("layout-save-btn");
  var saveMsg = document.getElementById("layout-save-msg");

  function showSaveLayoutUI() {
    if (!saveBar || !saveBtn) return;
    saveBar.hidden = false;
    saveBtn.hidden = false;
    saveBtn.disabled = false;
    if (saveMsg) {
      saveMsg.hidden = true;
      saveMsg.className = "layout-save-msg";
    }
  }

  if (saveBtn && !saveBtn._wired) {
    saveBtn._wired = true;
    saveBtn.addEventListener("click", function () {
      saveLayout();
      saveBtn.hidden = true;
      if (saveMsg) {
        saveMsg.textContent = getClientLabel("layout_saved");
        saveMsg.className = "layout-save-msg";
        saveMsg.hidden = false;
      }
      setTimeout(function () {
        if (saveBtn.hidden) {
          if (saveBar) saveBar.hidden = true;
          if (saveMsg) saveMsg.hidden = true;
        }
      }, 3000);
    });
  }

  var _draggedPanelEl = null;

  function onPanelDragStart(ev) {
    var head = ev.target.closest ? ev.target.closest(".head") : null;
    if (!head) { ev.preventDefault(); return; }
    var panelEl = (ev.currentTarget && ev.currentTarget.closest) ? ev.currentTarget.closest(".panel") : (ev.target.closest ? ev.target.closest(".panel") : null);
    if (!panelEl) return;
    _draggedPanelEl = panelEl;
    try {
      ev.dataTransfer.setData("text/plain", panelEl.getAttribute("data-panel") || "");
      ev.dataTransfer.effectAllowed = "move";
    } catch (e) {}
    panelEl.classList.add("dragging");
  }

  function onPanelDragEnd(ev) {
    var panelEl = (ev.currentTarget && ev.currentTarget.closest) ? ev.currentTarget.closest(".panel") : (ev.target.closest ? ev.target.closest(".panel") : null);
    if (panelEl) {
      panelEl.classList.remove("dragging");
    }
    _draggedPanelEl = null;
  }

  function onPanelDragOver(ev) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
    ev.currentTarget.classList.add("drag-over");
  }

  function onPanelDragLeave(ev) {
    ev.currentTarget.classList.remove("drag-over");
  }

  function onPanelDrop(ev) {
    ev.preventDefault();
    var targetEl = ev.currentTarget;
    targetEl.classList.remove("drag-over");
    var draggedEl = _draggedPanelEl;
    if (!draggedEl) {
      var draggedId = "";
      try { draggedId = ev.dataTransfer.getData("text/plain"); } catch (e) {}
      if (draggedId) {
        draggedEl = document.querySelector('.panels .panel[data-panel="' + draggedId + '"]');
      }
    }
    if (!draggedEl || draggedEl === targetEl) return;

    var draggedPos = _parseGridPlacement(draggedEl);
    var targetPos = _parseGridPlacement(targetEl);
    _applyGridPlacement(draggedEl, targetPos);
    _applyGridPlacement(targetEl, draggedPos);

    var parent = targetEl.parentNode;
    if (parent && draggedEl.parentNode === parent) {
      var draggedNext = draggedEl.nextSibling === targetEl ? draggedEl : draggedEl.nextSibling;
      parent.insertBefore(draggedEl, targetEl);
      if (draggedNext) {
        parent.insertBefore(targetEl, draggedNext);
      } else {
        parent.appendChild(targetEl);
      }
    }

    showSaveLayoutUI();
  }

  function wireDragAndDrop() {
    var panels = document.querySelectorAll(".panels .panel");
    for (var i = 0; i < panels.length; i++) {
      var el = panels[i];
      if (el._dragWired) continue;
      el._dragWired = true;
      el.setAttribute("draggable", "true");
      var head = el.querySelector(".head");
      if (head) {
        head.setAttribute("draggable", "true");
        head.addEventListener("dragstart", onPanelDragStart);
        head.addEventListener("dragend", onPanelDragEnd);
      }
      el.addEventListener("dragstart", onPanelDragStart);
      el.addEventListener("dragend", onPanelDragEnd);
      el.addEventListener("dragover", onPanelDragOver);
      el.addEventListener("dragleave", onPanelDragLeave);
      el.addEventListener("drop", onPanelDrop);
    }
  }

  // Roadmap timeline zoom (plan item 1.11, pattern 1): three server-
  // rendered variants share one panel; a click only swaps which one carries
  // .tl-active, never any client-side date math.
  function wireZoomToggles() {
    var toggles = document.querySelectorAll(".tl-zoom-toggle");
    for (var i = 0; i < toggles.length; i++) {
      var toggle = toggles[i];
      if (toggle._zoomWired) continue;
      toggle._zoomWired = true;
      toggle.addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("[data-zoom-btn]") : null;
        if (!btn) return;
        var panel = btn.closest(".panel");
        if (!panel) return;
        var zoom = btn.getAttribute("data-zoom-btn");
        var variants = panel.querySelectorAll(".tl-variant");
        for (var j = 0; j < variants.length; j++) {
          variants[j].classList.toggle("tl-active", variants[j].getAttribute("data-zoom") === zoom);
        }
        var btns = btn.closest(".tl-zoom-toggle").querySelectorAll("[data-zoom-btn]");
        for (var k = 0; k < btns.length; k++) {
          btns[k].classList.toggle("tl-zoom-active", btns[k] === btn);
        }
      });
    }
  }

  // Slice-by rail (plan item 1.11, pattern 3): a button sets data-slice on
  // the grouped container; rows carry their own data-gate, hidden/shown by
  // a plain attribute match, "all" clears the filter. No framework.
  function wireSliceRails() {
    var rails = document.querySelectorAll(".slice-rail");
    for (var i = 0; i < rails.length; i++) {
      var rail = rails[i];
      if (rail._sliceWired) continue;
      rail._sliceWired = true;
      rail.addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("[data-slice-btn]") : null;
        if (!btn) return;
        var railEl = btn.closest(".slice-rail");
        var target = railEl.parentNode.querySelector("[data-slice]");
        if (!target) return;
        var value = btn.getAttribute("data-slice-btn");
        target.setAttribute("data-slice", value);
        var rows = target.querySelectorAll("[data-gate]");
        for (var j = 0; j < rows.length; j++) {
          rows[j].hidden = !(value === "all" || rows[j].getAttribute("data-gate") === value);
        }
        var groups = target.querySelectorAll(".group");
        for (var g = 0; g < groups.length; g++) {
          groups[g].hidden = groups[g].querySelectorAll("[data-gate]:not([hidden])").length === 0;
        }
        var btns = railEl.querySelectorAll("[data-slice-btn]");
        for (var k = 0; k < btns.length; k++) {
          btns[k].classList.toggle("slice-active", btns[k] === btn);
        }
      });
    }
  }

  wireZoomToggles();
  wireSliceRails();
  restoreSavedLayout();
  wireDragAndDrop();
  probeSyncHelper();
})();"""


def render_panel_shell(key: str, lang: str, manifest_labels: dict, state: str, note: str = "") -> str:
    title = resolve_label(key, lang, manifest_labels)
    body_text = note or resolve_label("state_empty", lang, manifest_labels)
    body_key = "state_unavailable" if state == "unavailable" else "state_empty"
    css_class = "empty warn" if state == "unavailable" else "empty"
    return (
        f'<section class="panel" data-panel="{esc(key)}">'
        f'<div class="head"><h2 data-i18n="{esc(key)}">{esc(title)}</h2></div>'
        f'<div class="body"><p class="{css_class}">'
        f'<span class="msg" data-i18n="{esc(body_key)}">{esc(body_text)}</span></p></div>'
        f"</section>"
    )


def render_sync_status_panel(lang: str, manifest_labels: dict) -> str:
    """The one panel that ever renders in the loading state server-side
    (architecture note 5.3): sync freshness is a live value, not a file the
    renderer can read, so the actual state is resolved client-side by the
    inline script reading window.ZND_SYNC_STATUS after the sibling
    sync_status.js has had its chance to load (SYNC_STATUS_CONTRACT.md)."""
    title = resolve_label("sync_status", lang, manifest_labels)
    loading_text = resolve_label("state_loading", lang, manifest_labels)
    sync_label = resolve_label("sync_action", lang, manifest_labels)
    approve_label = resolve_label("approve_action", lang, manifest_labels)
    return (
        f'<section class="panel" data-panel="sync_status">'
        f'<div class="head"><h2 data-i18n="sync_status">{esc(title)}</h2></div>'
        f'<div class="body"><p class="empty" id="sync-status-body">'
        f'<span class="msg" data-i18n="state_loading">{esc(loading_text)}</span></p>'
        f'<button id="sync-btn" type="button" hidden data-i18n="sync_action">{esc(sync_label)}</button>'
        f'<button id="approve-btn" type="button" hidden data-i18n="approve_action">{esc(approve_label)}</button>'
        f"</div></section>"
    )


def _place_panel_html(html: str, key: str, entry: dict) -> str:
    """Inject the resolved layout's grid-column/grid-row as an inline style
    on the panel's own <section> tag (spec point 6). Every panel-rendering
    path (render_panel_shell, render_sync_status_panel, every board\\
    templates\\*.py render()) emits that tag as the exact literal
    `<section class="panel" data-panel="{key}">`, so one literal substring
    replace is enough; no template file needs touching.
    """
    needle = f'<section class="panel" data-panel="{esc(key)}">'
    style = f'style="grid-column: {entry["col"]} / span {entry["w"]}; grid-row: {entry["row"]} / span {entry["h"]};"'
    replacement = f'<section class="panel" data-panel="{esc(key)}" {style}>'
    return html.replace(needle, replacement, 1)


def _render_real_panel(key: str, panel_results: dict, label_bundle: dict) -> str:
    module = _REAL_PANEL_MODULES[key]
    state, data = panel_results[key]
    return module.render(data, label_bundle, state)


def render_children_strip(panel_results: dict, label_bundle: dict) -> str:
    return _render_real_panel("children", panel_results, label_bundle)


def render_activity_strip(panel_results: dict, label_bundle: dict) -> str:
    return _render_real_panel("activity", panel_results, label_bundle)


def collect_all_panels(manifest: dict) -> dict:
    """(state, data) per panel key, computed once so build_board_html and
    build_board_json share the same result (no re-reading files, no second
    GitHub call). A collector that raises degrades that one panel to
    unavailable rather than failing the whole node (states Must, REVIEW.md
    category 3)."""
    results = {}
    for key, module in _REAL_PANEL_MODULES.items():
        try:
            results[key] = module.collect(manifest)
        except Exception as exc:  # noqa: BLE001 - one panel must not sink the render
            results[key] = ("unavailable", {"error": str(exc)})
    for key in ("sync_status",):
        results[key] = ("empty", {})
    return results


def _render_page_shell(
    display_name: str,
    type_i18n_key: str,
    type_label: str,
    lang: str,
    languages: list,
    manifest_labels: dict,
    body_html: str,
    breadcrumb_html: str = "",
    footer_state_path: str = "",
    extra_head: str = "",
    extra_style: str = "",
    node_name: str = "",
    render_time: Optional[datetime] = None,
) -> str:
    """Header, theme, freshness line, labels, footer: the chrome every
    rendered page shares, node board and node-less board kind alike (guide
    step 9: the flow and sprint board kinds reuse this exact skeleton,
    a caller-supplied body instead of the panel grid). render_time is the
    one fixed moment this whole page is stamped with (T19: cmd_as_of
    passes the as-of date itself here, never live now, so a historical
    page's own freshness line does not silently drift with real time);
    every existing caller that omits it keeps today's live-now behaviour
    unchanged."""
    iso_time = (render_time or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    sources_read = 1
    freshness_tpl_key = "up_to_date_as_of"

    footer = theme.footer_stamp("Siteoffice board renderer v1", footer_state_path or display_name)

    labels_json = json.dumps(
        {t: all_labels_for(t, manifest_labels) for t in languages},
        ensure_ascii=False,
    )
    langs_json = json.dumps(languages)
    node_json = json.dumps(node_name)

    style = (
        theme.full_style()
        + "\n"
        + theme.system_theme_media_css()
        + "\n"
        + BOARD_CSS
        + ("\n" + extra_style if extra_style else "")
        + "\n"
        + _print_style()
    )

    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(display_name)} - {esc(type_label)}</title>
{extra_head}
<style>
  html {{ color-scheme: light dark; }}
{style}
</style>
</head>
<body>
<script>document.body.classList.add("js");document.body.setAttribute("data-theme-init", "1");try{{var t=localStorage.getItem("znd-theme");if(t==="light"||t==="dark")document.body.setAttribute("data-theme",t);}}catch(e){{}}</script>
<a class="skip-link" href="#board-main" data-i18n="skip_to_content">{esc(resolve_label('skip_to_content', lang, manifest_labels))}</a>
<header class="board-head">
  <div class="board-top">
    <div class="board-brand">
      <span class="board-type" data-i18n="{esc(type_i18n_key)}">{esc(type_label)}</span>
      <h1>{esc(display_name)}</h1>
    </div>
    <div class="board-controls">
      <div id="layout-save-bar" class="layout-save-bar" hidden>
        <button id="layout-save-btn" class="layout-save-btn" type="button" data-i18n="save_layout">{esc(resolve_label('save_layout', lang, manifest_labels))}</button>
        <span id="layout-save-msg" class="layout-save-msg" hidden></span>
      </div>
      <button id="theme-toggle" class="theme-toggle" type="button" aria-label="Theme">
        <span class="ti" id="theme-toggle-label" data-i18n="theme_system">{esc(resolve_label('theme_system', lang, manifest_labels))}</span>
      </button>
      <button id="lang-toggle" class="theme-toggle" type="button" aria-label="{esc(resolve_label('language_switch', lang, manifest_labels))}">
        <span class="ti" id="lang-toggle-label">{esc(lang.upper())}</span>
      </button>
    </div>
  </div>
  {breadcrumb_html}
  <p class="freshness mono" data-i18n-template="{freshness_tpl_key}" data-time="{esc(iso_time)}" data-count="{sources_read}">{esc(resolve_label(freshness_tpl_key, lang, manifest_labels).replace('{time}', iso_time).replace('{count}', str(sources_read)))}</p>
</header>
<main class="board-main" id="board-main">
  {body_html}
  {footer}
</main>
<script src="sync_status.js"></script>
<script>
window.__ZND_BOARD_LANGS__ = {langs_json};
window.__ZND_BOARD_LABELS__ = {labels_json};
window.__ZND_SYNC_NODE__ = {node_json};
{BOARD_JS}
</script>
</body>
</html>
"""


def build_board_html(manifest: dict, render_time: datetime, panel_results: dict) -> str:
    settings = manifest["settings"]
    languages = [LANGUAGE_ALIASES.get(t, t) for t in settings["languages"]]
    lang = languages[0]
    manifest_labels = manifest["labels"]
    label_bundle = all_labels_for(lang, manifest_labels)

    node_type = manifest["type"]
    type_label = resolve_label(node_type, lang, manifest_labels)
    display_name = manifest["display_name"]
    parent_name = manifest.get("parent") or ""

    resolved_layout = _resolve_layout(settings["layout"])
    disabled_keys = {
        entry["id"]
        for entry in resolved_layout
        # A panel a node has turned off (e.g. hours with
        # settings.timeRegistration false) gets no shell at all, not an
        # empty state (guide step 8), and its row is compacted away below
        # so it never leaves a visually empty grid row.
        if entry["id"] in _REAL_PANEL_MODULES
        and panel_results[entry["id"]][0] == "disabled"
    }
    panel_sections = []
    for entry in _compact_rows(resolved_layout, disabled_keys):
        key = entry["id"]
        if key in _REAL_PANEL_MODULES:
            panel_html = _render_real_panel(key, panel_results, label_bundle)
        elif key == "sync_status":
            panel_html = render_sync_status_panel(lang, manifest_labels)
        else:
            panel_html = render_panel_shell(key, lang, manifest_labels, "empty")
        panel_sections.append(_place_panel_html(panel_html, key, entry))
    panels_html = "".join(panel_sections)
    children_html = render_children_strip(panel_results, label_bundle)
    activity_html = render_activity_strip(panel_results, label_bundle)
    body_html = (
        f'<div class="panels" data-layout-columns="{settings["layout"]["columns"]}">\n'
        f"    {panels_html}\n  </div>\n  {children_html}\n  {activity_html}"
    )

    breadcrumb = ""
    if parent_name:
        breadcrumb = (
            f'<nav class="breadcrumb" aria-label="Breadcrumb">'
            f'<span data-i18n="breadcrumb_root">'
            f"{esc(resolve_label('breadcrumb_root', lang, manifest_labels).replace('{name}', parent_name))}"
            f"</span></nav>"
        )

    return _render_page_shell(
        display_name, node_type, type_label, lang, languages, manifest_labels,
        body_html, breadcrumb, footer_state_path=str(manifest["_path"]),
        extra_style=_panels_grid_css(settings["layout"]["columns"]),
        node_name=manifest.get("name", ""), render_time=render_time,
    )


def build_board_json(manifest: dict, render_time: datetime, panel_results: dict) -> dict:
    panels_out = {
        key: {"state": state, "data": json_safe(data)}
        for key, (state, data) in panel_results.items()
    }
    return {
        "generatedBy": "Siteoffice board renderer v1",
        "renderedAt": render_time.isoformat(),
        "name": manifest["name"],
        "type": manifest["type"],
        "displayName": manifest["display_name"],
        "parent": manifest.get("parent") or "",
        "children": manifest.get("children") or [],
        "settings": manifest["settings"],
        "sourcePath": str(manifest["_path"]),
        "panels": panels_out,
    }


# ===========================================================================
# CLI
# ===========================================================================
def _render_node(node_dir: pathlib.Path, manifest: dict, render_time: Optional[datetime] = None) -> int:
    """Collect, render and write one node. Returns the bytes written to
    board.html. render_time defaults to live now (cmd_node/cmd_tree,
    unchanged); cmd_as_of passes a FIXED time derived from its own as-of
    date instead, so a historical page's own freshness/renderedAt stamp
    reflects that date, never the moment the as-of command happened to
    run (T19's own "fixed input time")."""
    render_time = render_time or datetime.now(timezone.utc)
    panel_results = collect_all_panels(manifest)
    out_dir = node_dir / "_board"
    out_dir.mkdir(exist_ok=True)
    html_out = build_board_html(manifest, render_time, panel_results)
    json_out = build_board_json(manifest, render_time, panel_results)
    (out_dir / "board.html").write_text(html_out, encoding="utf-8")
    (out_dir / "board.json").write_text(
        json.dumps(json_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return (out_dir / "board.html").stat().st_size, panel_results


def cmd_node(args) -> int:
    node_dir = pathlib.Path(args.dir).resolve()
    manifest = load_manifest(node_dir)
    bytes_written, _ = _render_node(node_dir, manifest)
    out_dir = node_dir / "_board"
    print(f"wrote {out_dir / 'board.html'} ({bytes_written} bytes)")
    print(f"wrote {out_dir / 'board.json'}")
    return 0


def cmd_check(args) -> int:
    print(theme.check())
    return 0


def _git_toplevel(path: pathlib.Path) -> pathlib.Path:
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return pathlib.Path(out.stdout.strip())


def _git_current_branch(repo_root: pathlib.Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _git_commit_as_of(repo_root: pathlib.Path, branch: str, date: str) -> str:
    """Last commit on branch at or before the end of date (local time, git's
    own --before semantics), or raises ValueError if the branch did not
    exist yet on that date."""
    out = subprocess.run(
        ["git", "-C", str(repo_root), "log", branch, f"--before={date} 23:59:59",
         "-1", "--format=%H"],
        capture_output=True, text=True, check=True,
    )
    sha = out.stdout.strip()
    if not sha:
        raise ValueError(f"no commit on {branch!r} at or before {date}")
    return sha


def cmd_as_of(args) -> int:
    """Render one node as it existed at the end of a past date, in a
    disposable local clone: never a git worktree, never a commit/push/init/
    merge on the real repo (the lane's own hard rule, item 1.9's guide entry
    names the clone fallback explicitly). The clone and its checkout are
    local-only (no network) and deleted before this returns; the real
    repo's branches, HEAD and working tree are never touched."""
    node_dir = pathlib.Path(args.dir).resolve()
    date = args.date
    repo_root = _git_toplevel(node_dir)
    rel = node_dir.relative_to(repo_root)
    branch = _git_current_branch(repo_root)
    sha = _git_commit_as_of(repo_root, branch, date)
    # The one fixed input time this whole historical page is stamped
    # with: the end of the as-of date itself (the same boundary
    # _git_commit_as_of already used to pick `sha`), never live now, so
    # two as-of calls for the SAME date produce the SAME freshness/
    # renderedAt stamp regardless of when either call actually runs.
    render_time = datetime.strptime(f"{date} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory(prefix="siteoffice-asof-") as tmp:
        clone_dir = pathlib.Path(tmp) / "clone"
        subprocess.run(
            ["git", "clone", "--no-hardlinks", "--quiet", str(repo_root), str(clone_dir)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "--quiet", sha],
            check=True,
        )
        clone_node_dir = clone_dir / rel
        manifest = load_manifest(clone_node_dir)
        _render_node(clone_node_dir, manifest, render_time=render_time)
        rendered_html = (clone_node_dir / "_board" / "board.html").read_text(encoding="utf-8")

    out_path = (
        pathlib.Path(args.out) if args.out
        else node_dir / "_board" / f"board.asof-{date}.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered_html, encoding="utf-8")
    print(f"wrote {out_path} (as of {date}, commit {sha[:10]}, branch {branch})")
    return 0


def _find_manifests(root: pathlib.Path):
    """Every siteoffice.json under root, pruning directories not worth
    descending into (.git, node_modules, _board, ...): an unbounded walk
    over this estate is slow enough to matter (git internals alone hold
    thousands of files)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _TREE_SKIP_DIRS and not d.startswith(".")
        ]
        if "siteoffice.json" in filenames:
            found.append(pathlib.Path(dirpath) / "siteoffice.json")
    return found


def cmd_tree(args) -> int:
    root = pathlib.Path(args.root).resolve()
    found = sorted(
        _find_manifests(root),
        key=lambda p: len(p.parent.parts),
        reverse=True,  # deepest first: children render before parents
    )
    found_dirs = {p.parent.resolve() for p in found}

    rendered = 0
    failed = []
    no_manifest = []

    for path in found:
        node_dir = path.parent
        try:
            manifest = load_manifest(node_dir)
        except (FileNotFoundError, ValueError) as exc:
            failed.append((node_dir, str(exc)))
            continue

        for child in manifest.get("children") or []:
            # A child entry that is not a plain path string (found live in a
            # znd-skills scratch fixture, a dict) is a malformed manifest,
            # not a missing one: report it the same way a render failure is
            # reported, do not let it crash the whole walk.
            if not isinstance(child, (str, pathlib.PurePath)):
                failed.append((node_dir, f"children entry is not a path: {child!r}"))
                continue
            child_path = pathlib.Path(child)
            child_dir = child_path if child_path.is_absolute() else node_dir / child
            if child_dir.resolve() not in found_dirs:
                no_manifest.append(child_dir)

        try:
            bytes_written, panel_results = _render_node(node_dir, manifest)
            state_summary = ",".join(f"{k}:{v[0]}" for k, v in panel_results.items())
            print(f"{node_dir}  {state_summary}  {bytes_written} bytes")
            rendered += 1
        except Exception as exc:  # noqa: BLE001 - one node's failure must not stop the walk
            failed.append((node_dir, str(exc)))

    for child_dir in no_manifest:
        print(f"no manifest: {child_dir}")
    for node_dir, reason in failed:
        print(f"FAILED {node_dir}: {reason}")

    print(f"tree: {rendered} rendered, {len(failed)} failed")
    return 0 if not failed else 1


def cmd_export_hours(args) -> int:
    """Write a WBSO-grade CSV of every ndjson hours entry found under root
    for one year (guide step 8): the only file the renderer writes outside
    board.html/board.json, and it still lands inside _board\\."""
    root = pathlib.Path(args.root).resolve()
    year = int(args.year) if args.year else datetime.now(timezone.utc).year
    node_dirs = sorted({p.parent for p in _find_manifests(root)})

    rows = []
    for node_dir in node_dirs:
        entries, _malformed = hours.load_entries(node_dir, year)
        for e in entries:
            if args.person and e.get("person") != args.person:
                continue
            rows.append(e)

    if not rows:
        suffix = f" for person '{args.person}'" if args.person else ""
        print(f"export-hours: no entries found for {year}{suffix}")
        return 1

    rows.sort(key=lambda e: (e["date"], e.get("node", "")))

    if args.out:
        out_path = pathlib.Path(args.out)
    else:
        suffix = f"_{args.person}" if args.person else ""
        out_path = root / "_board" / f"hours_{year}{suffix}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "person", "node", "hours", "so", "tag", "note"])
        for e in rows:
            writer.writerow([
                e["date"].isoformat(), e.get("person", ""), e.get("node", ""),
                e["hours"], bool(e.get("so")), e.get("tag", ""), e.get("note", ""),
            ])

    print(f"export-hours: wrote {out_path} ({len(rows)} entries)")
    return 0


# ===========================================================================
# Board kinds: node-less flows and sprints (guide step 9, plan scope item 8)
#
# Neither reads a siteoffice.json of its own: labels and theme come from
# --root (the holding root passed explicitly) or the nearest ancestor
# manifest above the given path. Neither ever imports flows\_lib or
# sprints\_lib (hard rule): the small amount of logic this needs
# (is_awaiting, compute_progress) is reproduced here, not called.
# ===========================================================================
def _kind_manifest(explicit_root, start_dir: pathlib.Path) -> dict:
    root_dir = pathlib.Path(explicit_root).resolve() if explicit_root else None
    if root_dir is None:
        probe = start_dir
        for _ in range(_MAX_ANCESTOR_WALK):
            if (probe / "siteoffice.json").exists():
                root_dir = probe
                break
            if probe == probe.parent:
                break
            probe = probe.parent
    if root_dir is not None:
        try:
            return load_manifest(root_dir)
        except (FileNotFoundError, ValueError):
            pass
    return {"settings": {"languages": ["en"], "refreshDays": 7}, "labels": {}}


def cmd_flows(args) -> int:
    """Thin delegation to board.kinds.flow (decision 13, step 10b): this
    function only resolves the manifest/labels, then hands off."""
    flows_root = pathlib.Path(args.flows_root).resolve()
    manifest = _kind_manifest(args.root, flows_root)
    settings = manifest["settings"]
    languages = [LANGUAGE_ALIASES.get(t, t) for t in settings["languages"]]
    lang = languages[0]
    manifest_labels = manifest["labels"]
    label_bundle = all_labels_for(lang, manifest_labels)
    kind_flow.render_board(flows_root, _render_page_shell, lang, languages, manifest_labels, label_bundle)
    return 0


def cmd_sprints(args) -> int:
    """Thin delegation to board.kinds.sprint (decision 13, step 10b): this
    function only resolves the manifest/labels, then hands off."""
    sprints_root = pathlib.Path(args.sprints_root).resolve()
    manifest = _kind_manifest(args.root, sprints_root)
    settings = manifest["settings"]
    languages = [LANGUAGE_ALIASES.get(t, t) for t in settings["languages"]]
    lang = languages[0]
    manifest_labels = manifest["labels"]
    label_bundle = all_labels_for(lang, manifest_labels)
    kind_sprint.render_board(sprints_root, _render_page_shell, lang, languages, manifest_labels, label_bundle)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="render_board.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_node = sub.add_parser("node", help="render one node")
    p_node.add_argument("dir")
    p_node.set_defaults(func=cmd_node)

    p_tree = sub.add_parser("tree", help="render a node and every descendant")
    p_tree.add_argument("root")
    p_tree.set_defaults(func=cmd_tree)

    p_check = sub.add_parser("check", help="diff board tokens against the brand tokens file")
    p_check.add_argument("root")
    p_check.set_defaults(func=cmd_check)

    p_export = sub.add_parser("export-hours", help="write a WBSO-grade CSV of logged hours")
    p_export.add_argument("root")
    p_export.add_argument("--year")
    p_export.add_argument("--person")
    p_export.add_argument("--out")
    p_export.set_defaults(func=cmd_export_hours)

    p_flows = sub.add_parser("flows", help="render the flow board kind (node-less)")
    p_flows.add_argument("flows_root")
    p_flows.add_argument("--root", help="the holding root for labels/theme; default: nearest ancestor manifest")
    p_flows.set_defaults(func=cmd_flows)

    p_sprints = sub.add_parser("sprints", help="render the sprint board kind, index only (node-less)")
    p_sprints.add_argument("sprints_root")
    p_sprints.add_argument("--root", help="the holding root for labels/theme; default: nearest ancestor manifest")
    p_sprints.set_defaults(func=cmd_sprints)

    p_asof = sub.add_parser("as-of", help="render one node as it existed on a past date (disposable local clone)")
    p_asof.add_argument("dir")
    p_asof.add_argument("date", help="YYYY-MM-DD")
    p_asof.add_argument("--out", help="output path; default <dir>\\_board\\board.asof-<date>.html")
    p_asof.set_defaults(func=cmd_as_of)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
