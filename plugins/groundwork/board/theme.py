"""Shared palette, status semantics, CSS and JS for the ZND OS console
family (Mission Control, Portfolio, Money, Foreman board) and the Siteoffice
board renderer. Canonical home: board\theme.py in this project
(C:\work\50_projects\siteoffice\board\theme.py).
C:\work\10_holding\04_engineering\command_center\znd_os_theme.py is now a
thin re-export of this module (sprint 1, 2026-09-10): edit values here,
never there.

Reads tokens.json (this package's version-pinned copy of
00_company/brand/tokens.json, with the DERIVED ramps below promoted into
it) at RENDER TIME and inlines the resulting CSS custom properties into
each caller's <style> block. This module is a source of values, never a
runtime dependency: every output HTML file stays a single self-contained
file with zero external references. Import this module from
render_mission_deck.py, render_costs_page.py and render_foreman.py, all
three via the hub re-export, and from board\render_board.py directly;
never <link> or <script src> to it from an output page.

Palette provenance (2026-08-18 redesign, see 04_engineering/command_center/
design/ZND_OS_IMPLEMENTATION_PROMPT.md): where tokens.json already defines
an equivalent (bg, surface/panel, surface2/panel-2, text, textMuted/muted,
accent/mint, accentStrong/mint-bright, accentSoft/mint-fill,
accentBorder/mint-line, line/hairline, lineStrong/hairline-2, navBg/chrome-bg,
for both the dark and light themes), the token value is used. The brand
tokens.json has no amber/red status colors, no header-gradient or
sticky-table-header shade, no modal backdrop and no alpha ramp for
amber/red/grey fills and borders; those DERIVED values (below) are promoted
into this package's tokens.json under a top-level "derived" object (sprint
1, plan scope item 2) and read from there, with the values that were
previously hardcoded kept as a fallback if the tokens copy is missing,
unreadable, or has no "derived" object yet.

check() diffs tokens.json against the brand tokens.json named in its own
"source" field and returns a one-line report; run as
python board\theme.py check.

Stdlib only. No network, ever.
"""
from __future__ import annotations

import json
import pathlib

__all__ = [
    "root_css_vars", "full_style", "render_script", "status_sem",
    "footer_stamp", "render_nav_row", "STATUS_SEM", "NAV_OWN", "NAV_LABELS",
    "SHARED_CSS", "SHARED_JS", "DERIVED_KEYS_FOR_REPORT", "check",
    "system_theme_media_css",
]

HERE = pathlib.Path(__file__).resolve().parent
TOKENS_PATH = HERE / "tokens.json"


def _load_tokens() -> dict:
    try:
        return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_TOKENS = _load_tokens()
_DARK = (_TOKENS.get("themes") or {}).get("dark", {})
_LIGHT = (_TOKENS.get("themes") or {}).get("light", {})

# Fallbacks match the previously-hardcoded values exactly, so a missing or
# unreadable tokens.json degrades to the same palette the console has always
# shipped with, never a broken render.
_DARK_FALLBACK = {
    "bg": "#0A1422", "surface": "#112038", "surface2": "#0E1B30",
    "text": "#EAF1F8", "textMuted": "#8A9CB5",
    "accent": "#3EDCB0", "accentStrong": "#5CEBC2",
    "accentSoft": "rgba(62, 220, 176, 0.12)", "accentBorder": "rgba(62, 220, 176, 0.40)",
    "line": "rgba(138, 156, 181, 0.15)", "lineStrong": "rgba(138, 156, 181, 0.28)",
    "navBg": "rgba(10, 20, 34, 0.94)",
}
_LIGHT_FALLBACK = {
    "bg": "#E9EEF4", "surface": "#FFFFFF", "surface2": "#F1F5F9",
    "text": "#16243A", "textMuted": "#566579",
    "accent": "#0F9E78", "accentStrong": "#0C8A69",
    "accentSoft": "rgba(15, 158, 120, 0.12)", "accentBorder": "rgba(15, 158, 120, 0.42)",
    "line": "rgba(20, 34, 53, 0.12)", "lineStrong": "rgba(20, 34, 53, 0.22)",
    "navBg": "rgba(233, 238, 244, 0.94)",
}


def _pick(theme: dict, fallback: dict, key: str) -> str:
    v = theme.get(key)
    return v if isinstance(v, str) and v else fallback[key]


# ===========================================================================
# DERIVED: promoted into this package's tokens.json under "derived" (sprint
# 1, plan scope item 2). Read from there; the dicts below are now only the
# fallback for a missing or pre-migration tokens.json, kept verbatim from
# the approved concept files (mission_deck_concept.html / portfolio_concept
# .html / money_concept.html) so a broken tokens copy still renders the
# known-good palette instead of crashing.
# ===========================================================================
_DERIVED_DARK_FALLBACK = {
    "amber": "#F5B950", "red": "#F16969",
    "amberFill": "rgba(245, 185, 80, 0.13)", "amberBorder": "rgba(245, 185, 80, 0.42)",
    "redFill": "rgba(240, 96, 96, 0.14)", "redBorder": "rgba(240, 96, 96, 0.45)",
    "greyFill": "rgba(138, 156, 181, 0.10)", "greyBorder": "rgba(138, 156, 181, 0.30)",
    "headerTop": "#0C182A", "theadBg": "#0F1D33", "heroCountFg": "#1B0F00",
    "modalBackdrop": "rgba(6, 12, 20, 0.74)",
}
_DERIVED_LIGHT_FALLBACK = {
    "amber": "#975E10", "red": "#BE3830",
    "amberFill": "rgba(169, 106, 18, 0.12)", "amberBorder": "rgba(169, 106, 18, 0.42)",
    "redFill": "rgba(198, 58, 50, 0.12)", "redBorder": "rgba(198, 58, 50, 0.40)",
    "greyFill": "rgba(20, 34, 53, 0.06)", "greyBorder": "rgba(20, 34, 53, 0.22)",
    "headerTop": "#DCE5EF", "theadBg": "#E6ECF3", "heroCountFg": "#FFFFFF",
    "modalBackdrop": "rgba(20, 34, 53, 0.40)",
}
_DERIVED_FROM_TOKENS = _TOKENS.get("derived") or {}
_DERIVED_DARK = {
    k: _pick(_DERIVED_FROM_TOKENS.get("dark") or {}, _DERIVED_DARK_FALLBACK, k)
    for k in _DERIVED_DARK_FALLBACK
}
_DERIVED_LIGHT = {
    k: _pick(_DERIVED_FROM_TOKENS.get("light") or {}, _DERIVED_LIGHT_FALLBACK, k)
    for k in _DERIVED_LIGHT_FALLBACK
}

# Values used in the report: every DERIVED key above, plus the spacing/type
# scale, which tokens.json does not cover at all (radius 8/5, pill radius 20,
# spacing 4/8/10/14/18/22/28px, type 19/15/14/13/12/11.5/11/10px).
DERIVED_KEYS_FOR_REPORT = sorted(set(_DERIVED_DARK) | set(_DERIVED_LIGHT))


def root_css_vars() -> str:
    """The :root block (dark values) plus the body[data-theme=light] override
    block. One shared string, inlined verbatim into every output page."""
    d = {k: _pick(_DARK, _DARK_FALLBACK, k) for k in _DARK_FALLBACK}
    l = {k: _pick(_LIGHT, _LIGHT_FALLBACK, k) for k in _LIGHT_FALLBACK}
    dd, dl = _DERIVED_DARK, _DERIVED_LIGHT
    return f"""  :root {{
    --bg: {d['bg']};
    --panel: {d['surface']};
    --panel-2: {d['surface2']};
    --mint: {d['accent']};
    --mint-bright: {d['accentStrong']};
    --text: {d['text']};
    --muted: {d['textMuted']};
    --hairline: {d['line']};
    --hairline-2: {d['lineStrong']};
    --amber: {dd['amber']};
    --red: {dd['red']};

    --mint-fill: {d['accentSoft']};
    --mint-line: {d['accentBorder']};
    --amber-fill: {dd['amberFill']};
    --amber-line: {dd['amberBorder']};
    --red-fill: {dd['redFill']};
    --red-line: {dd['redBorder']};
    --grey-fill: {dd['greyFill']};
    --grey-line: {dd['greyBorder']};

    --header-top: {dd['headerTop']};
    --chrome-bg: {d['navBg']};
    --thead-bg: {dd['theadBg']};
    --hero-count-fg: {dd['heroCountFg']};
    --modal-backdrop: {dd['modalBackdrop']};

    --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: "Consolas", ui-monospace, "SF Mono", "SFMono-Regular", Menlo, "Liberation Mono", monospace;

    --header-h: 87px;
    --chrome-h: 142px;
    --radius: 8px;
    --radius-sm: 5px;
  }}

  body[data-theme="light"] {{
    --bg: {l['bg']};
    --panel: {l['surface']};
    --panel-2: {l['surface2']};
    --mint: {l['accent']};
    --mint-bright: {l['accentStrong']};
    --text: {l['text']};
    --muted: {l['textMuted']};
    --hairline: {l['line']};
    --hairline-2: {l['lineStrong']};
    --amber: {dl['amber']};
    --red: {dl['red']};
    --mint-fill: {l['accentSoft']};
    --mint-line: {l['accentBorder']};
    --amber-fill: {dl['amberFill']};
    --amber-line: {dl['amberBorder']};
    --red-fill: {dl['redFill']};
    --red-line: {dl['redBorder']};
    --grey-fill: {dl['greyFill']};
    --grey-line: {dl['greyBorder']};
    --header-top: {dl['headerTop']};
    --chrome-bg: {l['navBg']};
    --thead-bg: {dl['theadBg']};
    --hero-count-fg: {dl['heroCountFg']};
    --modal-backdrop: {dl['modalBackdrop']};
  }}"""


# ===========================================================================
# STATUS SEMANTICS
# One raw-to-semantic map shared by Mission Control, Portfolio and Money.
# run = mint (running), wait = amber (awaiting a decision), done = grey
# (closed/passing/paid, recedes), halt = red (failed/escalated/overdue/halted).
# ===========================================================================
STATUS_SEM = {
    "running": "run", "active": "run",
    "awaiting-owner": "wait", "awaiting-wilco": "wait", "due": "wait",
    "passing": "done", "done": "done", "closed": "done", "paid": "done",
    "escalated": "halt", "failed": "halt", "capped": "halt", "halted": "halt",
    "paused": "halt", "overdue": "halt", "cancel me": "halt",
}


def status_sem(status) -> str:
    return STATUS_SEM.get(str(status), "run")


# ===========================================================================
# REFERENCE NAV ROW
# Fixed list, fixed order, on every page: Mission Control, Portfolio, Money,
# separator, Foreman board, BurnRate, Operator guide, Cheat sheet. The current
# page renders as span.navlink.cur in its own slot instead of being dropped,
# so no link ever changes position between pages.
# ===========================================================================
NAV_OWN = ["mission", "portfolio", "money"]
NAV_LABELS = {
    "mission": "Mission Control", "portfolio": "Portfolio", "money": "Money",
    "foreman": "Foreman board", "burnrate": "BurnRate",
    "operator": "Operator guide", "cheatsheet": "Cheat sheet",
}


def render_nav_row(current: str, hrefs: dict) -> str:
    """hrefs: dict of nav-id -> href for every id in NAV_LABELS except
    `current`, which renders without a link. Missing hrefs are skipped."""
    import html as _html

    def esc(x):
        return _html.escape(str(x))

    parts = ['<nav class="navrow" aria-label="Other surfaces">',
             '<span class="navlbl">Reference</span>']
    for nav_id in NAV_OWN:
        label = NAV_LABELS[nav_id]
        if nav_id == current:
            parts.append(f'<span class="navlink own cur" aria-current="page">{esc(label)}</span>')
        elif nav_id in hrefs:
            parts.append(f'<a class="navlink own" href="{esc(hrefs[nav_id])}">{esc(label)}</a>')
    parts.append('<span class="navsep"></span>')
    for nav_id in ("foreman", "burnrate", "operator", "cheatsheet"):
        label = NAV_LABELS[nav_id]
        if nav_id == current:
            parts.append(f'<span class="navlink ext cur" aria-current="page">{esc(label)}</span>')
        elif nav_id in hrefs:
            parts.append(f'<a class="navlink ext" href="{esc(hrefs[nav_id])}">{esc(label)}</a>')
    parts.append("</nav>")
    return "".join(parts)


def footer_stamp(generator_name: str, state_path: str) -> str:
    return (f'<div class="stamp"><span class="dotm"></span>'
            f'<span>Generated by {generator_name}. Edit the generator, not this file.</span>'
            f'<span class="dotm"></span><span>state: {state_path}</span></div>')


# ===========================================================================
# SHARED CSS
# One block, imported verbatim by all three generators. Covers chrome, filter
# bar, panels, hero, gates, deadlines, tables, badges, progress bars, project
# cards, week grid, empty/unavail states, toggle switch, modal, nav row, repo
# chips, KPI tiles, pipeline strip, twelve-month bar chart and the footer
# stamp. Anything a page needs beyond this (there should be little) stays in
# that generator's own small page-specific block.
# ===========================================================================
SHARED_CSS = r"""  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  a { color: var(--mint-bright); text-decoration: none; }
  a:hover { color: var(--text); text-decoration: underline; text-underline-offset: 3px; }
  ::selection { background: rgba(62, 220, 176, 0.30); color: #fff; }

  .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .num  { font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; }

  header.deck {
    position: sticky; top: 0; z-index: 40;
    background: linear-gradient(180deg, var(--header-top) 0%, var(--bg) 100%);
    border-bottom: 1px solid var(--hairline);
    padding: 14px 28px 12px;
    display: block;
  }
  /* Embed mode (2026-08-18): a page loaded inside another ZND OS shell's
     iframe with ?embed=1 (or ?embed=true) hides its own header, so the
     shell's own chrome is the only one shown and this page's own reference
     row and filter bar re-anchor to the very top of the frame instead of
     stacking two headers. measureChromeHeight() (below) recomputes
     --header-h/--chrome-h from real offsetHeights on load, so hiding
     header.deck here is the whole fix: the filter bar's own
     `top: var(--header-h)` collapses to 0 with no extra rule needed.
     Currently used by the Foreman board (framed by foreman_shell.html) and
     BurnRate's own dashboard (framed by burnrate_shell.html, a separate repo
     with its own equivalent implementation); Mission Control, Portfolio and
     Money are never themselves framed, so no page appends ?embed=1 to them,
     but the capability lives here so it is not forked per generator. */
  body.embed header.deck { display: none; }
  .deck-top { display: flex; align-items: baseline; justify-content: space-between; gap: 24px; }
  header.deck .brand { display: flex; align-items: baseline; gap: 14px; }
  header.deck h1 { margin: 0; font-size: 19px; font-weight: 700; letter-spacing: -0.01em; }
  header.deck h1 .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--mint); box-shadow: 0 0 10px rgba(62,220,176,0.8);
    margin-right: 10px; vertical-align: middle;
    animation: pulse 2.6s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  header.deck .sub { color: var(--muted); font-size: 12px; letter-spacing: 0.02em; }
  header.deck .gen { color: var(--muted); font-size: 12px; }
  header.deck .gen b { color: var(--text); font-weight: 500; }
  header.deck .chrome-right { display: flex; align-items: center; gap: 16px; }
  .theme-toggle {
    display: inline-flex; border: 1px solid var(--hairline-2); border-radius: 20px;
    overflow: hidden; cursor: pointer; background: transparent; padding: 0; font-family: var(--font);
  }
  .theme-toggle .ti {
    padding: 5px 11px; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--muted); background: transparent; transition: color 120ms ease, background 120ms ease;
  }
  body:not([data-theme="light"]) .theme-toggle .dark,
  body[data-theme="light"] .theme-toggle .light { background: var(--mint-fill); color: var(--mint-bright); }

  .navrow { display: flex; align-items: center; gap: 14px; margin-top: 8px; flex-wrap: wrap; min-height: 20px; }
  .navlbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.14em; opacity: 0.75; }
  .navlink { color: var(--muted); font-size: 12px; text-decoration: none; border-bottom: 1px solid transparent; padding-bottom: 1px; }
  .navlink:hover { color: var(--text); border-bottom-color: var(--hairline-2); text-decoration: none; }
  .navlink.own { color: var(--mint-bright); }
  .navlink.cur { color: var(--text); border-bottom-color: var(--mint-line); cursor: default; }
  .navlink.ext::after { content: " \2197"; font-size: 9px; opacity: 0.65; }
  .navsep { width: 1px; height: 12px; background: var(--hairline-2); }

  .filterbar {
    position: sticky; top: var(--header-h); z-index: 39;
    background: var(--chrome-bg);
    backdrop-filter: blur(6px);
    border-bottom: 1px solid var(--hairline);
    padding: 10px 28px;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .filterbar .grp { display: flex; align-items: center; gap: 6px; }
  .filterbar label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; }
  select, .search input {
    background: var(--panel); color: var(--text);
    border: 1px solid var(--hairline-2); border-radius: var(--radius-sm);
    padding: 6px 9px; font-family: var(--font); font-size: 13px; outline: none;
  }
  select:focus, .search input:focus { border-color: var(--mint-line); }
  .search { position: relative; display: flex; align-items: center; }
  .search input { width: 210px; padding-left: 26px; }
  .search .slash { position: absolute; left: 8px; color: var(--muted); font-family: var(--mono); font-size: 12px; pointer-events: none; }
  .search input:focus + .slash, .search.filled .slash { opacity: 0; }
  .filterbar .spacer { flex: 1; }
  .counter { color: var(--muted); font-size: 12px; font-family: var(--mono); }
  .counter b { color: var(--text); }
  .btn-reset {
    background: transparent; color: var(--muted); border: 1px solid var(--hairline-2);
    border-radius: var(--radius-sm); padding: 6px 10px; font-size: 12px; cursor: pointer;
    font-family: var(--font);
  }
  .btn-reset:hover { color: var(--text); border-color: var(--hairline-2); }

  main { padding: 22px 28px 80px; max-width: 1680px; margin: 0 auto; }
  .grid { display: grid; gap: 18px; }
  .cols-2 { grid-template-columns: 1fr 1fr; }
  .two-up { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }
  .two-up section.panel { margin-bottom: 0; }
  section.panel {
    background: var(--panel); border: 1px solid var(--hairline);
    border-radius: var(--radius); margin-bottom: 18px; overflow: clip;
  }
  /* overflow: clip, NOT hidden: hidden makes the panel a scroll container,
     which captures any position: sticky descendant (the viewport-pinned
     table headers) and shoves it --chrome-h px down INTO the panel even at
     scroll 0. clip gives the same rounded-corner clipping without creating
     a scroll container, so sticky children keep binding to the viewport. */
  .panel > .head { display: flex; align-items: center; gap: 12px; padding: 13px 18px; border-bottom: 1px solid var(--hairline); }
  .panel > .head h2 { margin: 0; font-size: 13px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text); }
  .panel > .head .meta { color: var(--muted); font-size: 12px; }
  .panel > .head .spacer { flex: 1; }
  .panel > .body { padding: 14px 18px; }
  .panel.flush > .body { padding: 0; }
  .kicker { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.14em; }

  section.hero {
    border: 1px solid var(--amber-line);
    background: radial-gradient(120% 140% at 0% 0%, rgba(245,185,80,0.06), transparent 55%), var(--panel);
    box-shadow: 0 0 0 1px rgba(245,185,80,0.08), 0 18px 40px -28px rgba(0,0,0,0.9);
  }
  section.hero.clear { border-color: var(--hairline); background: var(--panel); box-shadow: none; }
  section.hero > .head { border-bottom: 1px solid var(--amber-line); padding: 15px 20px; }
  section.hero.clear > .head { border-bottom-color: var(--hairline); }
  section.hero > .head h2 { font-size: 15px; letter-spacing: 0.06em; }
  .hero-count {
    font-family: var(--mono); font-size: 12px; font-weight: 700;
    color: var(--hero-count-fg); background: var(--amber); padding: 2px 9px; border-radius: 20px; letter-spacing: 0.02em;
  }
  section.hero.clear .hero-count { color: var(--hero-count-fg); background: var(--muted); }
  .hero-body { display: grid; grid-template-columns: 1.55fr 1fr; gap: 0; }
  .hero-col { padding: 16px 20px; display: flex; flex-direction: column; }
  .hero-col + .hero-col { border-left: 1px solid var(--hairline); }
  .hero-col h3 { margin: 0 0 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.14em; color: var(--muted); display: flex; align-items: center; gap: 8px; }
  .hero-col h3 .tally { color: var(--text); font-family: var(--mono); }

  .gate {
    border: 1px solid var(--hairline-2); border-left: 3px solid var(--amber);
    border-radius: var(--radius-sm); background: var(--panel-2);
    padding: 12px 14px; margin-bottom: 10px;
    display: grid; grid-template-columns: 1fr auto; gap: 8px 16px; align-items: center;
  }
  .gate:last-child { margin-bottom: 0; }
  .gate.long-wait { border-left-color: var(--red); }
  .gate .who { min-width: 0; }
  .gate .slug { font-family: var(--mono); font-size: 13px; color: var(--text); }
  .gate .gid { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .gate .gid .g { color: var(--mint-bright); }
  .gate .wait { font-family: var(--mono); font-size: 12px; text-align: right; white-space: nowrap; }
  .gate .wait .t { color: var(--amber); font-weight: 700; }
  .gate.long-wait .wait .t { color: var(--red); }
  .gate .wait .lbl { color: var(--muted); display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; }
  .verdicts { grid-column: 1 / -1; display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
  .verdict {
    font-family: var(--mono); font-size: 12px; padding: 5px 12px; border-radius: 20px;
    border: 1px solid var(--hairline-2); background: transparent; cursor: pointer;
    color: var(--text); text-decoration: none; transition: transform 120ms ease, background 120ms ease;
  }
  .verdict:hover { transform: translateY(-1px); text-decoration: none; }
  .verdict.copied, .vmail.copied { background: var(--mint-fill); border-color: var(--mint-line); }
  .verdict.copied::after, .vmail.copied::after { content: " (copied)"; font-size: 10px; opacity: 0.85; }
  .vmail { font-family: var(--mono); font-size: 11px; padding: 5px 7px; border-radius: 20px; border: 1px solid transparent; color: var(--muted); text-decoration: none; align-self: center; }
  .vmail:hover { border-color: var(--hairline-2); text-decoration: none; }
  .verdict.approve { color: var(--mint-bright); border-color: var(--mint-line); }
  .verdict.approve:hover { background: var(--mint-fill); }
  .verdict.reject { color: var(--red); border-color: var(--red-line); }
  .verdict.reject:hover { background: var(--red-fill); }
  .verdict.adapt { color: var(--amber); border-color: var(--amber-line); }
  .verdict.adapt:hover { background: var(--amber-fill); }
  .verdict.park { color: var(--muted); border-color: var(--grey-line); }
  .verdict.park:hover { background: var(--grey-fill); }

  .deadline { display: flex; align-items: baseline; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--hairline); }
  .deadline:last-child { border-bottom: 0; }
  .deadline .date { font-family: var(--mono); font-size: 12px; color: var(--red); min-width: 96px; }
  .deadline .txt { flex: 1; }
  .deadline .txt .p { color: var(--muted); font-size: 12px; }
  .deadline .over { font-family: var(--mono); font-size: 11px; color: var(--red); border: 1px solid var(--red-line); background: var(--red-fill); padding: 1px 7px; border-radius: 20px; white-space: nowrap; }
  .inbox-line { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--hairline); display: flex; align-items: center; gap: 10px; }
  .inbox-line .n { font-family: var(--mono); font-size: 22px; font-weight: 700; color: var(--amber); }
  .inbox-line .lbl { color: var(--muted); font-size: 12px; }

  /* Plain .tblwrap must NOT set overflow-x: auto: that made every wrapper a
     scroll container, so the sticky thead bound to the wrapper instead of
     the viewport and rendered --chrome-h px down inside its own table, with
     data rows visible above it (Money page, 2026-08-18). The concept file
     kept headers static for that reason; the real fix is to keep the
     wrapper overflow-free so headers genuinely pin beneath the chrome.
     .tblwrap.scroll stays a scroll container on purpose: its headers pin to
     the wrapper's own top edge via the top: 0 override below. */
  .tblwrap { width: 100%; }
  .tblwrap.scroll { overflow-y: scroll; }
  table.data.fx { table-layout: fixed; }
  table.data.fx td, table.data.fx th { overflow: hidden; text-overflow: ellipsis; }
  table.data[data-table="sprints"] thead th:nth-child(5),
  table.data[data-table="sprints"] thead th:nth-child(6),
  table.data[data-table="sprints"] tbody td:nth-child(5),
  table.data[data-table="sprints"] tbody td:nth-child(6) { text-align: left; }
  table.data[data-table="sprints"] tbody td:nth-child(6) .prog { justify-content: flex-start; }
  table.data[data-table="flows"] thead th:nth-child(7),
  table.data[data-table="flows"] tbody td:nth-child(7),
  table.data[data-table="sprints"] thead th:nth-child(8),
  table.data[data-table="sprints"] tbody td:nth-child(8),
  table.data[data-table="sessions"] thead th:nth-child(4),
  table.data[data-table="sessions"] tbody td:nth-child(4) { text-align: left; }
  .tblwrap.scroll table.data thead th { top: 0; }
  .tblwrap.rows-11 { max-height: 462px; }
  .tblwrap.rows-4 { max-height: 186px; }
  .tblwrap.scroll::-webkit-scrollbar { width: 10px; }
  .tblwrap.scroll::-webkit-scrollbar-thumb { background: rgba(138,156,181,0.28); border-radius: 6px; border: 2px solid var(--panel); }
  .tblwrap.scroll::-webkit-scrollbar-track { background: transparent; }

  .gates-scroll { flex: 1 1 auto; min-height: 312px; overflow-y: auto; }
  .deadlines-scroll { max-height: 150px; overflow-y: auto; }
  .gates-scroll::-webkit-scrollbar, .deadlines-scroll::-webkit-scrollbar { width: 10px; }
  .gates-scroll::-webkit-scrollbar-thumb, .deadlines-scroll::-webkit-scrollbar-thumb { background: rgba(138,156,181,0.28); border-radius: 6px; border: 2px solid var(--panel); }
  .gates-scroll::-webkit-scrollbar-track, .deadlines-scroll::-webkit-scrollbar-track { background: transparent; }

  /* Scroll, do not grow (board\DESIGN.md addendum, item 1.7): any panel body
     whose content length is unbounded (activity, roadmap, hours, a sprint's
     iteration cards) caps here instead of growing the panel's own grid
     footprint. */
  .panel-scroll { max-height: 320px; overflow-y: auto; }
  .panel-scroll::-webkit-scrollbar { width: 10px; }
  .panel-scroll::-webkit-scrollbar-thumb { background: rgba(138,156,181,0.28); border-radius: 6px; border: 2px solid var(--panel); }
  .panel-scroll::-webkit-scrollbar-track { background: transparent; }

  .hero-inbox { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--hairline); }
  .hero-inbox table.data { font-size: 12px; }
  .hero-inbox table.data thead th { padding: 7px 10px; }
  .hero-inbox table.data tbody td { padding: 7px 10px; }
  .ts.age-neutral { color: var(--muted); }
  .ts.age-amber { color: var(--amber); }
  .ts.age-red { color: var(--red); }

  .hero-sub, .hero-repos { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--hairline); }
  .hero-sub h3 { margin: 0 0 10px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.14em; color: var(--muted); display: flex; gap: 8px; align-items: center; }
  .hero-sub h3 .tally, .hero-repos .hr-label { color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; font-size: 11px; }
  .hero-repos { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; font-size: 12px; }
  .repos, .repo-chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip, .repo-chip {
    display: inline-flex; align-items: center; gap: 7px; font-family: var(--mono); font-size: 11.5px;
    padding: 3px 10px; border-radius: 20px; border: 1px solid var(--hairline-2); color: var(--muted);
  }
  .chip.dirty, .repo-chip { color: var(--amber); border-color: var(--amber-line); background: var(--amber-fill); }
  .chip.dirty b, .repo-chip b { font-weight: 700; }
  .chip.clean { border-color: var(--grey-line); background: var(--grey-fill); }
  .repos-clean, .repo-clean { color: var(--muted); font-size: 12.5px; font-family: var(--mono); }

  table.data { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.data thead th {
    position: sticky; top: var(--chrome-h); z-index: 5;
    background: var(--thead-bg); color: var(--muted);
    text-align: left; font-weight: 600; font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 9px 14px; white-space: nowrap;
    border-bottom: 1px solid var(--hairline-2); cursor: pointer; user-select: none;
  }
  table.data thead th.num { text-align: right; }
  table.data thead th.nosort { cursor: default; }
  table.data thead th .arw { display: inline-block; width: 10px; margin-left: 5px; color: var(--mint-bright); opacity: 0; font-size: 10px; }
  table.data thead th[aria-sort] .arw { opacity: 1; }
  table.data thead th:hover:not(.nosort) { color: var(--text); }
  table.data tbody td { padding: 9px 14px; border-bottom: 1px solid var(--hairline); vertical-align: middle; }
  table.data tbody tr:nth-child(even) td { background: rgba(255,255,255,0.012); }
  table.data tbody tr:hover td { background: rgba(62,220,176,0.05); }
  table.data tbody tr:last-child td { border-bottom: 0; }
  td .slug { font-family: var(--mono); font-size: 12.5px; }
  td.dim, .dim { color: var(--muted); }
  td .ts { color: var(--muted); font-family: var(--mono); font-size: 12px; }

  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-family: var(--mono); font-size: 11.5px; font-weight: 600; letter-spacing: 0.01em;
    padding: 2px 9px 2px 8px; border-radius: 20px; border: 1px solid; white-space: nowrap;
  }
  .badge::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .badge.run { color: var(--mint-bright); background: var(--mint-fill); border-color: var(--mint-line); }
  .badge.wait { color: var(--amber); background: var(--amber-fill); border-color: var(--amber-line); }
  .badge.done { color: var(--muted); background: var(--grey-fill); border-color: var(--grey-line); }
  .badge.halt { color: var(--red); background: var(--red-fill); border-color: var(--red-line); }
  tr[data-status-sem="done"] td { opacity: 0.72; }
  tr[data-status-sem="done"]:hover td { opacity: 1; }

  .prog { display: flex; align-items: center; gap: 9px; justify-content: flex-end; }
  .prog .track { width: 46px; height: 5px; border-radius: 3px; background: rgba(138,156,181,0.18); overflow: hidden; }
  .prog .fill { display: block; height: 100%; background: var(--mint); border-radius: 3px; }
  .prog.wait .fill { background: var(--amber); }
  .prog.halt .fill { background: var(--red); }
  .prog .pct { font-family: var(--mono); font-variant-numeric: tabular-nums; width: 34px; text-align: right; }

  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
  .pcard { position: relative; background: var(--panel-2); border: 1px solid var(--hairline); border-left: 3px solid var(--muted); border-radius: var(--radius-sm); padding: 14px 15px; }
  .pcard.h-green { border-left-color: var(--mint); }
  .pcard.h-yellow { border-left-color: var(--amber); }
  .pcard.h-red { border-left-color: var(--red); }
  .pcard .top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  .pcard .name { font-weight: 700; font-size: 14px; }
  .pcard .tier { font-family: var(--mono); font-size: 10px; color: var(--muted); border: 1px solid var(--hairline-2); border-radius: 4px; padding: 1px 5px; letter-spacing: 0.04em; }
  .pcard .stale { display: inline-block; margin-top: 8px; font-family: var(--mono); font-size: 10.5px; color: var(--amber); background: var(--amber-fill); border: 1px solid var(--amber-line); padding: 1px 7px; border-radius: 20px; letter-spacing: 0.04em; }
  .pcard .next { margin-top: 10px; font-size: 13px; color: var(--text); }
  .pcard .next .lbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em; display: block; margin-bottom: 2px; }
  .pcard .touched { margin-top: 10px; color: var(--muted); font-size: 11.5px; font-family: var(--mono); }

  /* min-width: 0 on the grid items (item 1.7/1.8 follow-up, 2026-09-13):
     grid items default to min-width: auto, so one day carrying an
     unbreakable long string (a file path, a URL) would otherwise widen
     its own 1fr track past its equal share and skew every other day's
     width. Forcing 0 makes all 7 columns genuinely equal, always. */
  .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; }
  /* Fixed height, not min-height (item 1.7/1.8 follow-up, 2026-09-13): a
     day with many items must scroll its own list, not grow the card, so
     the week grid's row height never depends on content length. */
  .day { background: var(--panel-2); border: 1px solid var(--hairline); border-radius: var(--radius-sm); padding: 10px; height: 240px; min-width: 0; display: flex; flex-direction: column; }
  .day.today { border-color: var(--mint-line); box-shadow: inset 0 0 0 1px rgba(62,220,176,0.15); }
  .day .dh { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; flex: none; }
  .day .dow { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); }
  .day.today .dow { color: var(--mint-bright); }
  .day .dnum { font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .day .dbody-scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
  .day .dbody-scroll::-webkit-scrollbar { width: 8px; }
  .day .dbody-scroll::-webkit-scrollbar-thumb { background: rgba(138,156,181,0.28); border-radius: 6px; border: 2px solid var(--panel-2); }
  .day .dbody-scroll::-webkit-scrollbar-track { background: transparent; }
  .day ul { list-style: none; margin: 0; padding: 0; }
  .day li { font-size: 12px; padding: 4px 0; border-bottom: 1px solid var(--hairline); color: var(--text); overflow-wrap: anywhere; }
  .day li:last-child { border-bottom: 0; }
  .day li .tag { font-family: var(--mono); font-size: 10px; color: var(--muted); }
  .day li .stx { display: inline-block; width: 14px; margin-right: 6px; text-align: center; }
  .day li.st-done { color: var(--muted); opacity: 0.72; }
  .day li.st-done .stx { color: var(--mint); }
  .day li.st-prog .stx { color: var(--amber); }
  .day li.st-blocked .stx { color: var(--red); }
  .day li.st-pending .stx { color: var(--muted); opacity: 0.6; }
  .day .rest { color: var(--muted); font-size: 11.5px; font-style: italic; opacity: 0.7; }

  .empty { text-align: center; padding: 30px 18px; color: var(--muted); }
  .empty .glyph { font-family: var(--mono); font-size: 20px; color: var(--mint); opacity: 0.6; margin-bottom: 8px; }
  .empty .msg { font-size: 13px; }
  .empty .sub { font-size: 12px; opacity: 0.7; margin-top: 3px; }
  .empty.warn .glyph { color: var(--amber); }

  .no-match { display: none; }
  table.data tbody tr.no-match-row td, table.data tbody tr.no-invoices-row td { text-align: center; color: var(--muted); padding: 22px; font-style: italic; }

  .unavail {
    display: flex; align-items: center; gap: 10px; padding: 14px 16px; margin: 14px 18px;
    border: 1px dashed var(--amber-line); background: var(--amber-fill); border-radius: var(--radius-sm);
    color: var(--amber); font-size: 12.5px;
  }
  .unavail code { font-family: var(--mono); }
  .unavail a { color: var(--amber); text-decoration: underline; text-underline-offset: 2px; }

  .toggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; color: var(--muted); font-size: 12px; }
  .toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
  .toggle .sw { width: 34px; height: 18px; border-radius: 20px; background: rgba(138,156,181,0.22); position: relative; transition: background 140ms ease; flex: none; }
  .toggle .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: var(--muted); transition: transform 140ms ease, background 140ms ease; }
  .toggle input:checked + .sw { background: var(--mint-fill); }
  .toggle input:checked + .sw::after { transform: translateX(16px); background: var(--mint); }
  .toggle input:focus-visible + .sw { outline: 2px solid var(--mint-line); outline-offset: 2px; }

  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 16px; }
  .kpi { background: var(--panel-2); border: 1px solid var(--hairline); border-radius: var(--radius-sm); padding: 13px 15px; }
  .kpi.alert { border-color: var(--red-line); background: var(--red-fill); }
  .kpi .klbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.13em; }
  .kpi .kval { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 24px; font-weight: 700; color: var(--text); margin-top: 5px; }
  .kpi.alert .kval { color: var(--red); }
  .kpi .ksub { font-family: var(--mono); font-size: 11.5px; margin-top: 3px; color: var(--muted); }
  .kpi .kdelta.up { color: var(--mint-bright); }
  .kpi .kdelta.down { color: var(--red); }
  td.money { font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; }
  table.data[data-table="revenue"] thead th:nth-child(6) { text-align: right; }
  table.data[data-table="revenue"] thead th:nth-child(7),
  table.data[data-table="revenue"] tbody td:nth-child(7) { text-align: center; }
  .pipe-wrap { margin-top: 16px; padding-top: 15px; border-top: 1px solid var(--hairline); display: grid; grid-template-columns: 1fr auto; gap: 18px; align-items: center; }
  .pipe-h { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.13em; margin-bottom: 8px; }
  .pipe { display: flex; height: 12px; border-radius: 3px; overflow: hidden; gap: 2px; }
  .pipe .seg { height: 100%; }
  .pipe .seg.committed { background: var(--mint); }
  .pipe .seg.probable { background: var(--amber); }
  .pipe .seg.early { background: rgba(138,156,181,0.5); }
  .pipe-legend, .legend { display: flex; gap: 16px; margin-top: 9px; font-size: 11.5px; color: var(--muted); flex-wrap: wrap; }
  .pipe-legend .k, .legend .k { display: inline-flex; align-items: center; gap: 6px; font-family: var(--mono); }
  .pipe-legend .sw, .legend .sw { width: 9px; height: 9px; border-radius: 2px; flex: none; }
  .pipe-legend .sw.committed { background: var(--mint); }
  .pipe-legend .sw.probable { background: var(--amber); }
  .pipe-legend .sw.early { background: rgba(138,156,181,0.5); }
  .cash30 { text-align: right; }
  .cash30 .lbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.13em; }
  .cash30 .v { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 22px; font-weight: 700; color: var(--mint-bright); margin-top: 3px; }

  /* Month window toggle (This year / Last 12 months). Both variants are
     server-rendered; JS only swaps visibility, so no client-side
     recomputation of formatted numbers ever happens. The server marks the
     "year" variant .on so a no-JS load still shows one honest window. */
  .win-toggle { display: inline-flex; margin-left: auto; border: 1px solid var(--hairline-2); border-radius: 20px; overflow: hidden; }
  .win-btn {
    background: transparent; border: 0; color: var(--muted); font-family: var(--font);
    font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
    padding: 5px 11px; cursor: pointer; transition: color 120ms ease, background 120ms ease;
  }
  .win-btn.cur { background: var(--mint-fill); color: var(--mint-bright); }
  .win-btn:hover:not(.cur) { color: var(--text); }
  [data-mwin] { display: none; }
  [data-mwin].on { display: block; }

  .mchart { display: flex; align-items: flex-end; gap: 12px; height: 220px; }
  .mcol { flex: 1; display: flex; flex-direction: column; height: 100%; }
  .mbars { flex: 1; display: flex; align-items: flex-end; gap: 4px; }
  .mbar { flex: 1; border-radius: 2px 2px 0 0; min-height: 2px; }
  .mbar.inc { background: var(--mint); }
  .mbar.cost { background: var(--amber); opacity: 0.85; }
  .mnet { font-family: var(--mono); font-size: 11px; text-align: center; margin-top: 7px; }
  .mnet.pos { color: var(--mint-bright); }
  .mnet.neg { color: var(--red); }
  .blbl { font-family: var(--mono); font-size: 10px; text-align: center; color: var(--muted); margin-top: 2px; }
  .legend .sw.inc { background: var(--mint); }
  .legend .sw.cost { background: var(--amber); opacity: 0.85; }
  .legend .sw.net { background: transparent; border: 1px solid var(--hairline-2); }
  .tsum { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--hairline); }
  .tcell { background: var(--panel-2); border: 1px solid var(--hairline); border-radius: var(--radius-sm); padding: 11px 13px; }
  .tcell.warn { border-color: var(--amber-line); background: var(--amber-fill); }
  .tcell .lbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.13em; }
  .tcell .v { font-family: var(--mono); font-size: 20px; font-weight: 700; margin-top: 4px; }
  .tcell.warn .v { color: var(--amber); }
  .tcell .s { color: var(--muted); font-size: 11.5px; margin-top: 3px; }

  .footnote { margin-top: 26px; border-top: 1px solid var(--hairline); padding-top: 16px; color: var(--muted); font-size: 12px; line-height: 1.7; }
  .footnote h3 { color: var(--text); font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; margin: 0 0 8px; }
  .footnote code { font-family: var(--mono); color: var(--mint-bright); font-size: 11.5px; }
  .footnote ul { margin: 0; padding-left: 18px; }
  .footnote li { margin-bottom: 5px; }

  .drill-link { cursor: pointer; }
  a.drill { color: var(--muted); font-size: 12px; white-space: nowrap; }
  a.drill:hover { color: var(--mint-bright); text-decoration: none; }
  table.data.fx td:has(a.drill), table.data.fx th.nosort { white-space: nowrap; }
  table.data.fx td:has(.badge) { white-space: nowrap; overflow: visible; padding-left: 10px; padding-right: 10px; }
  .modal-overlay {
    display: none; position: fixed; inset: 0; z-index: 500;
    background: var(--modal-backdrop); padding: 28px;
  }
  .modal-overlay.open { display: flex; flex-direction: column; }
  .modal-bar {
    display: flex; align-items: center; justify-content: space-between;
    background: var(--panel); border: 1px solid var(--hairline-2); border-bottom: none;
    border-radius: var(--radius) var(--radius) 0 0; padding: 8px 14px;
    color: var(--muted); font-size: 12px;
  }
  .modal-bar .modal-title { font-family: var(--mono); color: var(--text); font-size: 12.5px; }
  .modal-bar .modal-actions { display: flex; gap: 14px; align-items: center; }
  .modal-bar a { color: var(--mint); text-decoration: none; }
  .modal-bar a:hover { text-decoration: underline; }
  .modal-close {
    background: none; border: 1px solid var(--hairline-2); color: var(--text);
    border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; line-height: 1; padding: 4px 10px;
  }
  .modal-close:hover { border-color: var(--mint-line); color: var(--mint); }
  .modal-frame-wrap { flex: 1; background: var(--bg); border: 1px solid var(--hairline-2); border-radius: 0 0 var(--radius) var(--radius); overflow: hidden; }
  .modal-frame-wrap iframe { width: 100%; height: 100%; border: 0; background: var(--panel); }

  .stamp { margin-top: 20px; color: var(--muted); font-size: 11.5px; font-family: var(--mono); display: flex; align-items: center; gap: 9px; opacity: 0.85; }
  .stamp .dotm { width: 5px; height: 5px; border-radius: 50%; background: var(--hairline-2); flex: none; }

  @media (max-width: 1180px) {
    .hero-body { grid-template-columns: 1fr; }
    .hero-col + .hero-col { border-left: 0; border-top: 1px solid var(--hairline); }
    .cols-2, .two-up { grid-template-columns: 1fr; }
    .week { grid-template-columns: repeat(4, 1fr); }
    .tsum { grid-template-columns: repeat(2, 1fr); }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important; scroll-behavior: auto !important;
    }
  }"""


def full_style() -> str:
    return root_css_vars() + "\n" + SHARED_CSS


def system_theme_media_css(selector: str = "body:not([data-theme])") -> str:
    """Three-state model, board only: no stored choice follows the OS via
    prefers-color-scheme. Same names and values as root_css_vars()'s light
    block; other consoles do not call this, they keep dark-default."""
    l = {k: _pick(_LIGHT, _LIGHT_FALLBACK, k) for k in _LIGHT_FALLBACK}
    dl = _DERIVED_LIGHT
    return f"""  @media (prefers-color-scheme: light) {{
    {selector} {{
      --bg: {l['bg']}; --panel: {l['surface']}; --panel-2: {l['surface2']};
      --mint: {l['accent']}; --mint-bright: {l['accentStrong']};
      --text: {l['text']}; --muted: {l['textMuted']};
      --hairline: {l['line']}; --hairline-2: {l['lineStrong']};
      --amber: {dl['amber']}; --red: {dl['red']};
      --mint-fill: {l['accentSoft']}; --mint-line: {l['accentBorder']};
      --amber-fill: {dl['amberFill']}; --amber-line: {dl['amberBorder']};
      --red-fill: {dl['redFill']}; --red-line: {dl['redBorder']};
      --grey-fill: {dl['greyFill']}; --grey-line: {dl['greyBorder']};
      --header-top: {dl['headerTop']}; --chrome-bg: {l['navBg']};
      --thead-bg: {dl['theadBg']}; --hero-count-fg: {dl['heroCountFg']};
      --modal-backdrop: {dl['modalBackdrop']};
    }}
  }}"""


# ===========================================================================
# SHARED JS
# One template, imported by every generator. __LS_KEY__ is substituted per
# page (versioned: znd-mission-deck-v3, znd-money-v1). Theme lives in the
# single shared key znd-theme, read and written by every page, so a theme
# choice propagates across the family. Closed-row toggles are discovered from
# any input[id^="show-closed-"], seeded AFTER reading localStorage so stored
# state never drops a newly-added table's key. Sticky chrome heights
# (--header-h for the filter bar, --chrome-h for viewport-pinned table
# headers) are measured from the header's and filter bar's layout heights
# (offsetHeight, scroll-independent) on load and resize, not hardcoded,
# because the filter bar wraps at narrow widths.
# ===========================================================================
SHARED_JS = r"""(function () {
  "use strict";

  var LS_KEY = "__LS_KEY__";
  var THEME_KEY = "znd-theme";
  var state = {
    project: "", lane: "", status: "", search: "",
    showScheduled: false,
    showClosed: {},
    sorts: {},
    win: "year"
  };

  function load() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      if (raw) { var o = JSON.parse(raw); for (var k in o) state[k] = o[k]; }
    } catch (e) { /* file:// with storage blocked, degrade to defaults */ }
  }
  function save() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch (e) {}
  }

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* -------------------------------------------------- populate filter facets */
  function uniqueValues(attr, skipSynthetic) {
    var set = {};
    $$(".filterable[" + attr + "]").forEach(function (el) {
      if (skipSynthetic && el.getAttribute("data-status-synthetic") === "1") return;
      var v = el.getAttribute(attr);
      if (v) set[v] = true;
    });
    return Object.keys(set).sort();
  }
  function fillSelect(sel, values) {
    if (!sel) return;
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v; sel.appendChild(o);
    });
  }
  var selProject = $("#f-project"), selLane = $("#f-lane"), selStatus = $("#f-status"), inpSearch = $("#f-search");
  if (selProject) fillSelect(selProject, uniqueValues("data-project"));
  if (selLane) fillSelect(selLane, uniqueValues("data-lane"));
  if (selStatus) fillSelect(selStatus, uniqueValues("data-status", true));

  /* ------------------------------------------------------------ closed toggle
     Discovered from any input[id^="show-closed-"], so a new table needs no JS
     change. Seeded AFTER load() so stored state can never drop a new key. */
  var CLOSED_TABLES = $$('input[id^="show-closed-"]').map(function (b) {
    return b.id.replace("show-closed-", "");
  });
  function seedClosed() {
    if (!state.showClosed) state.showClosed = {};
    CLOSED_TABLES.forEach(function (t) {
      if (!state.showClosed.hasOwnProperty(t)) state.showClosed[t] = false;
    });
  }
  function closedHidden(tname) {
    return $$('table[data-table="' + tname + '"] tr[data-status-sem="done"]').length;
  }
  function syncClosedLabels() {
    CLOSED_TABLES.forEach(function (t) {
      var lbl = $("#closed-label-" + t);
      if (!lbl) return;
      lbl.textContent = state.showClosed[t]
        ? "Hide closed (" + closedHidden(t) + ")"
        : "Show closed (" + closedHidden(t) + " hidden)";
    });
  }
  CLOSED_TABLES.forEach(function (t) {
    var box = $("#show-closed-" + t);
    if (!box) return;
    box.addEventListener("change", function () {
      state.showClosed[t] = this.checked;
      syncClosedLabels(); applyFilters(); save();
    });
  });

  /* --------------------------------------------------------------- filtering */
  function rowMatches(el) {
    if (state.project && el.getAttribute("data-project") !== state.project) return false;
    if (state.lane && el.getAttribute("data-lane") !== state.lane) return false;
    if (state.status && el.getAttribute("data-status") !== state.status) return false;
    if (!state.showScheduled && el.getAttribute("data-scheduled") === "1") return false;
    if (el.getAttribute("data-status-sem") === "done") {
      var tbl = el.closest ? el.closest("table.data") : null;
      var tname = tbl ? tbl.getAttribute("data-table") : null;
      if (tname && state.showClosed && state.showClosed.hasOwnProperty(tname) && !state.showClosed[tname]) return false;
    }
    if (state.search) {
      var hay = (el.getAttribute("data-search") || "") + " " + el.textContent.toLowerCase();
      if (hay.toLowerCase().indexOf(state.search.toLowerCase()) === -1) return false;
    }
    return true;
  }

  function applyFilters() {
    var shown = 0, total = 0;
    $$(".filterable").forEach(function (el) {
      var isSched = el.getAttribute("data-scheduled") === "1";
      var isDone = el.getAttribute("data-status-sem") === "done";
      var tbl = el.closest ? el.closest("table.data") : null;
      var tname = tbl ? tbl.getAttribute("data-table") : null;
      var doneShown = !isDone || !tname || !state.showClosed || !state.showClosed.hasOwnProperty(tname) || state.showClosed[tname];
      var counts = (!isSched || state.showScheduled) && doneShown;
      var ok = rowMatches(el);
      el.classList.toggle("no-match", !ok);
      if (counts) { total++; if (ok) shown++; }
    });
    var cs = $("#c-shown"), ct = $("#c-total");
    if (cs) cs.textContent = shown;
    if (ct) ct.textContent = total;
    $$("table.data").forEach(function (tbl) { refreshTableEmpty(tbl); });
    refreshHeroEmpty();
    syncClosedLabels();
    if (typeof syncSchedLabel === "function") syncSchedLabel();
  }

  function refreshTableEmpty(tbl) {
    var body = tbl.tBodies[0];
    if (!body) return;
    var visible = $$("tr.filterable", body).filter(function (r) { return !r.classList.contains("no-match"); });
    var existing = $(".no-match-row", body);
    if (visible.length === 0) {
      if (!existing) {
        var cols = tbl.tHead.rows[0].cells.length;
        var tr = document.createElement("tr");
        tr.className = "no-match-row";
        var td = document.createElement("td");
        td.colSpan = cols;
        td.textContent = "No rows match the current filter. Loosen it or reset.";
        tr.appendChild(td); body.appendChild(tr);
      }
    } else if (existing) {
      existing.parentNode.removeChild(existing);
    }
  }

  function refreshHeroEmpty() {
    var hero = $("#hero");
    if (!hero) return;
    var gatesVisible = $$("#hero .gate.filterable").filter(function (g) { return !g.classList.contains("no-match"); }).length;
    var deadVisible = $$("#hero .deadline.filterable").filter(function (d) { return !d.classList.contains("no-match"); }).length;
    var count = $("#hero-count");
    if (!count) return;
    if (gatesVisible === 0 && deadVisible === 0) {
      count.textContent = "clear";
      hero.classList.add("clear");
    } else {
      count.textContent = gatesVisible + (gatesVisible === 1 ? " gate, " : " gates, ") + deadVisible + " overdue";
      hero.classList.remove("clear");
    }
  }

  /* --------------------------------------------------------------- sorting */
  function cellSortKey(row, colIndex, numeric) {
    var cell = row.cells[colIndex];
    if (!cell) return numeric ? 0 : "";
    var sv = cell.getAttribute("data-sort-value");
    if (sv === null) {
      var child = cell.querySelector("[data-sort-value]");
      if (child) sv = child.getAttribute("data-sort-value");
    }
    if (numeric) {
      var n = parseFloat(sv !== null ? sv : cell.textContent.replace(/[^0-9.\-]/g, ""));
      return isNaN(n) ? 0 : n;
    }
    return (sv !== null ? sv : cell.textContent).trim().toLowerCase();
  }

  function sortTable(tbl, colIndex, dir, numeric) {
    var body = tbl.tBodies[0];
    var rows = $$("tr.filterable", body);
    rows.sort(function (a, b) {
      var ka = cellSortKey(a, colIndex, numeric), kb = cellSortKey(b, colIndex, numeric);
      if (ka < kb) return dir === "asc" ? -1 : 1;
      if (ka > kb) return dir === "asc" ? 1 : -1;
      return 0;
    });
    rows.forEach(function (r) { body.appendChild(r); });
    var em = $(".no-match-row", body);
    if (em) body.appendChild(em);
  }

  var DEFAULT_SORT = { flows: "updated", sprints: "updated", sessions: "last", inbox: "since",
                        revenue: "status", spend: "last",
                        active: "updated", unjoined: "updated" };

  function wireTable(tbl) {
    var name = tbl.getAttribute("data-table");
    var headers = $$("thead th", tbl);
    headers.forEach(function (th, idx) {
      if (th.classList.contains("nosort")) return;
      var numeric = th.getAttribute("data-type") === "num";
      th.addEventListener("click", function () {
        var cur = state.sorts[name] || {};
        var dir = (cur.col === idx && cur.dir === "asc") ? "desc" : "asc";
        state.sorts[name] = { col: idx, dir: dir, numeric: numeric };
        applySort(tbl);
        save();
      });
    });
    if (!state.sorts[name] && DEFAULT_SORT[name]) {
      headers.forEach(function (th, idx) {
        if (th.getAttribute("data-col") === DEFAULT_SORT[name]) {
          state.sorts[name] = { col: idx, dir: "desc", numeric: th.getAttribute("data-type") === "num" };
        }
      });
    }
    applySort(tbl);
  }

  function applySort(tbl) {
    var name = tbl.getAttribute("data-table");
    var s = state.sorts[name];
    var headers = $$("thead th", tbl);
    headers.forEach(function (th) { th.removeAttribute("aria-sort"); var a = $(".arw", th); if (a) a.textContent = ""; });
    if (!s) return;
    var th = headers[s.col];
    if (!th) return;
    var numeric = th.getAttribute("data-type") === "num";
    sortTable(tbl, s.col, s.dir, numeric);
    th.setAttribute("aria-sort", s.dir === "asc" ? "ascending" : "descending");
    var arw = $(".arw", th); if (arw) arw.textContent = s.dir === "asc" ? "\u25B2" : "\u25BC";
  }

  /* --------------------------------------------------------- scheduled toggle */
  var chkSched = $("#show-scheduled");
  var schedLabel = $("#sched-label");
  function schedCount() { return $$(".sched-row").length; }
  function syncSchedLabel() {
    if (!schedLabel) return;
    var n = schedCount();
    schedLabel.textContent = state.showScheduled
      ? "Hide scheduled tasks (" + n + ")"
      : "Show scheduled tasks (" + n + " hidden)";
  }
  if (chkSched) chkSched.addEventListener("change", function () {
    state.showScheduled = this.checked;
    syncSchedLabel(); applyFilters(); save();
  });

  /* --------------------------------------------------------------- wiring */
  function pushControls() {
    if (selProject) selProject.value = state.project;
    if (selLane) selLane.value = state.lane;
    if (selStatus) selStatus.value = state.status;
    if (inpSearch) inpSearch.value = state.search;
    if (chkSched) chkSched.checked = state.showScheduled;
    CLOSED_TABLES.forEach(function (t) {
      var box = $("#show-closed-" + t);
      if (box) box.checked = !!state.showClosed[t];
    });
    var sg = $("#searchgrp");
    if (sg) sg.classList.toggle("filled", !!state.search);
    syncClosedLabels();
    syncSchedLabel();
  }

  if (selProject) selProject.addEventListener("change", function () { state.project = this.value; applyFilters(); save(); });
  if (selLane) selLane.addEventListener("change", function () { state.lane = this.value; applyFilters(); save(); });
  if (selStatus) selStatus.addEventListener("change", function () { state.status = this.value; applyFilters(); save(); });
  if (inpSearch) inpSearch.addEventListener("input", function () {
    state.search = this.value;
    var sg = $("#searchgrp");
    if (sg) sg.classList.toggle("filled", !!this.value);
    applyFilters(); save();
  });
  var btnReset = $("#btn-reset");
  if (btnReset) btnReset.addEventListener("click", function () {
    state.project = state.lane = state.status = state.search = "";
    pushControls(); applyFilters(); save();
  });

  document.addEventListener("keydown", function (e) {
    if (!inpSearch) return;
    if (e.key === "/" && document.activeElement !== inpSearch &&
        ["INPUT", "SELECT", "TEXTAREA"].indexOf((document.activeElement.tagName || "")) === -1) {
      e.preventDefault(); inpSearch.focus(); inpSearch.select();
    }
    if (e.key === "Escape" && document.activeElement === inpSearch) { inpSearch.blur(); }
  });

  /* ------------------------------------------------------------ theme
     One shared key, znd-theme, read and written by every page in the family
     so a theme choice on any page propagates to the others on next load. */
  function readTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function writeTheme(t) {
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
  }
  var currentTheme = readTheme() === "light" ? "light" : "dark";
  function applyTheme() {
    document.body.setAttribute("data-theme", currentTheme === "light" ? "light" : "dark");
  }
  /* 2026-08-18: on a shell page (guide.html, cheatsheet.html, foreman_shell.html,
     burnrate_shell.html), the framed page's own storage listener SHOULD pick this
     up cross-frame for free, no shell-side code needed. Found not reliable for
     BurnRate specifically: its framed target (dashboard/dashboard.html) lives in a
     different top-level directory (a sibling repo, C:\work\50_projects\burnrate) than every
     other framed target (all under C:\work\10_holding), and at least one
     Chromium file:// build partitions storage EVENT delivery (not the stored
     value itself, which is why a reload always shows the correct theme) by
     top-level directory, so a same-machine, same-browser toggle click on this
     shell can silently fail to notify a cross-directory iframe even though the
     identical mechanism works for every in-repo shell. postMessage has no such
     directory-based restriction, so push the new theme directly into any framed
     iframe as a belt-and-braces companion to the storage key, not a replacement
     for it (the storage key still has to be the source of truth other tabs read
     on their own next load or their own working storage listener). */
  function notifyFrame(t) {
    var frame = $("#doc-frame");
    if (frame && frame.contentWindow) {
      try { frame.contentWindow.postMessage({znd_theme: t}, "*"); } catch (e) {}
    }
  }
  var themeBtn = $("#theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      currentTheme = currentTheme === "light" ? "dark" : "light";
      writeTheme(currentTheme);
      applyTheme();
      notifyFrame(currentTheme);
    });
  }

  /* --------------------------------------------------------- sticky chrome
     Two sticky layers stack at the top of the viewport: header.deck pins at
     0, the filter bar pins directly beneath it at --header-h, and
     viewport-sticky table headers pin beneath both at --chrome-h. Both
     custom properties are measured from the elements' layout heights
     (offsetHeight) on load and resize; the filter bar wraps at narrow
     widths so hardcoded values drift. Never use getBoundingClientRect()
     .bottom here: that is a viewport position, so a restored scroll offset
     poisons the measurement and the table headers pin into empty space. */
  function measureChromeHeight() {
    var head = $("header.deck");
    var bar = $("#filterbar");
    var headH = head ? head.offsetHeight : 0;
    var chromeH = headH + (bar ? bar.offsetHeight : 0);
    document.documentElement.style.setProperty("--header-h", headH + "px");
    document.documentElement.style.setProperty("--chrome-h", chromeH + "px");
  }
  window.addEventListener("resize", measureChromeHeight);

  /* ------------------------------------------------------- drill modal
     "flow" / "sprint" rows link to that flow's or sprint's own dashboard.html.
     Loading it in an iframe overlay keeps the shell in place instead of
     navigating away; a modifier-click or a click with JS disabled falls
     through to the plain href. */
  var modal = $("#drill-modal"), modalFrame = $("#drill-modal-frame"),
      modalTitle = $("#drill-modal-title"), modalOpenLink = $("#drill-modal-open");
  function openDrill(href, label) {
    if (!modal || !modalFrame) { window.location.href = href; return; }
    modalFrame.src = href;
    if (modalTitle) modalTitle.textContent = label || href;
    if (modalOpenLink) modalOpenLink.href = href;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }
  function closeDrill() {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    modalFrame.src = "about:blank";
  }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a.drill-link") : null;
    if (!a) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    var row = a.closest ? a.closest("tr") : null;
    var label = (row && row.getAttribute("data-search")) || a.textContent || a.getAttribute("href");
    openDrill(a.getAttribute("href"), label);
  });
  var modalCloseBtn = $("#drill-modal-close");
  if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeDrill);
  if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeDrill(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal && modal.classList.contains("open")) closeDrill();
  });

  /* -------------------------------------------- gate verdict copy + Slack open
     Copies the verdict text (data-copy) to the clipboard before the href
     navigates to Slack (or mailto as the envelope fallback). No
     preventDefault: the href does its own work, this only copies. */
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[data-copy]") : null;
    if (!a) return;
    var text = a.getAttribute("data-copy") || "";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function () {});
    }
    a.classList.add("copied");
    setTimeout(function () { a.classList.remove("copied"); }, 1500);
  });

  /* ------------------------------------------------- month window toggle
     Discovered from the DOM like the closed-row toggles: any .win-btn
     button switches every [data-mwin] section on the page between the
     "year" (This year) and "l12" (Last 12 months) prebuilt variants, so
     panels that each carry their own toggle stay in sync from one handler.
     The choice persists inside the page's own LS_KEY state (key: win,
     default "year", seeded at declaration so an older stored shape without
     the key degrades to the default). No-op on pages without .win-btn. */
  function applyWin() {
    var mode = state.win === "l12" ? "l12" : "year";
    $$("[data-mwin]").forEach(function (el) {
      el.classList.toggle("on", el.getAttribute("data-mwin") === mode);
    });
    $$(".win-btn").forEach(function (b) {
      var cur = b.getAttribute("data-win") === mode;
      b.classList.toggle("cur", cur);
      b.setAttribute("aria-pressed", cur ? "true" : "false");
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest ? e.target.closest(".win-btn") : null;
    if (!b) return;
    state.win = b.getAttribute("data-win") === "l12" ? "l12" : "year";
    applyWin(); save();
  });

  /* ------------------------------------------------------ auto-refresh */
  var refreshMinutes = window.ZND_REFRESH_MINUTES || 0;
  if (refreshMinutes > 0) {
    setInterval(function () { window.location.reload(); }, refreshMinutes * 60 * 1000);
  }

  /* --------------------------------------------------- local-time display
     Timestamps are generated and stored server-side in UTC. Reformat for
     display only, client-side, in the viewer's own locale/timezone; the raw
     UTC value stays in data-utc / data-sort-value / title for sorting. */
  var LOCAL_TS_OPTS = { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  function localizeGenTs() {
    var el = $("#gen-ts");
    if (!el) return;
    var utc = el.getAttribute("data-utc") || el.textContent;
    var d = new Date(utc);
    if (isNaN(d.getTime())) return;
    el.textContent = d.toLocaleString(undefined, LOCAL_TS_OPTS) + " local";
    el.title = utc + " (UTC, source)";
  }
  function localizeRowTimestamps() {
    $$(".ts[data-sort-value]").forEach(function (el) {
      var d = new Date(el.getAttribute("data-sort-value"));
      if (isNaN(d.getTime())) return;
      el.title = d.toLocaleString(undefined, LOCAL_TS_OPTS) + " local";
    });
  }

  /* ------------------------------------------------------------ embed mode
     Reads ?embed=1 or ?embed=true from this page's own URL (set by the
     framing shell when it composes the iframe src, see _SHELL_FRAME_JS in
     render_mission_deck.py) and, if present, adds the "embed" class to
     <body> before the chrome height is first measured, so header.deck is
     already display:none when measureChromeHeight() reads its offsetHeight. */
  function applyEmbed() {
    var v = null;
    try { v = new URLSearchParams(window.location.search).get("embed"); } catch (e) {}
    if (v === "1" || v === "true") { document.body.classList.add("embed"); }
  }

  /* --------------------------------------------------------------- init */
  load();
  seedClosed();
  applyTheme();
  applyEmbed();
  applyWin();
  pushControls();
  measureChromeHeight();
  $$("table.data").forEach(wireTable);
  applyFilters();
  localizeGenTs();
  localizeRowTimestamps();
})();"""


def render_script(ls_key: str) -> str:
    return SHARED_JS.replace("__LS_KEY__", ls_key)


# ===========================================================================
# TOKENS DRIFT CHECK (sprint 1, plan scope item 2, risk R2)
# ===========================================================================
def check() -> str:
    """Diff tokens.json (this package's pinned copy) against the brand
    tokens.json named in its own "source" field. One-line report: "tokens in
    sync" or the list of differing keys (version, updated, every themes.*
    value). Never crashes on a missing source: a customer repo has no hub
    tokens.json to compare against, so that degrades to one explanatory
    line instead of a traceback."""
    try:
        own = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"tokens check: cannot read {TOKENS_PATH}: {exc}"

    source = own.get("source")
    if not source:
        return "tokens check: no source recorded in tokens.json, skipping drift check"
    source_path = pathlib.Path(source)
    try:
        brand = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"tokens check: source unavailable ({source_path}), skipping drift check"

    diffs = []
    if own.get("version") != brand.get("version"):
        diffs.append(f"version: own={own.get('version')!r} brand={brand.get('version')!r}")
    if own.get("updated") != brand.get("updated"):
        diffs.append(f"updated: own={own.get('updated')!r} brand={brand.get('updated')!r}")

    own_themes = own.get("themes") or {}
    brand_themes = brand.get("themes") or {}
    for theme_name in sorted(set(own_themes) | set(brand_themes)):
        own_theme = own_themes.get(theme_name) or {}
        brand_theme = brand_themes.get(theme_name) or {}
        for key in sorted(set(own_theme) | set(brand_theme)):
            if own_theme.get(key) != brand_theme.get(key):
                diffs.append(
                    "themes." + theme_name + "." + key +
                    ": own=" + repr(own_theme.get(key)) +
                    " brand=" + repr(brand_theme.get(key))
                )

    if not diffs:
        return "tokens in sync"
    return "tokens differ: " + "; ".join(diffs)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        print(check())
    else:
        print("usage: python board\\theme.py check", file=sys.stderr)
        raise SystemExit(2)
