"""Thin caller for /flow (flow-system slice c). Siteoffice fold, sprint 1
step 12, plan scope item 8, decision 6: the HTML now comes from the
Siteoffice board renderer's flows board kind (board\\render_board.py,
`cmd_flows`), not from this file's own templates. `render_flow` and
`render_index` delegate and return the new `_board\\flows\\...` path;
`render_status_text` (plain text, no HTML) and `is_awaiting` are unchanged.

`LIVE_REFRESH_SECONDS` and `THEME_SCRIPT` stay: foreman\\_lib\\render_foreman.py
imports both from this module (`rf.LIVE_REFRESH_SECONDS`, `rf.THEME_SCRIPT`)
for its own, still-separate HTML (step 13's concern, not this one).

CLI:
    python render_flows.py flow   <flow_dir>
    python render_flows.py index  <flows_root>
    python render_flows.py status <flow_dir>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Foreman cloud package (T04): this file now lives inside the siteoffice
# repo itself, at foreman\flows\_lib\, so the siteoffice root is simply three
# levels up (no more walking up looking for a "projects\siteoffice" sibling,
# a hub-tree-specific naming convention a customer checkout never has).
# Falls back to the old walk-up search only if that direct path is somehow
# not a real siteoffice checkout (a worktree nested one level deeper, or a
# stray copy of just this file), so this never hard-codes one drive letter
# or one fixed depth either.
_HERE = pathlib.Path(__file__).resolve().parent


def _find_siteoffice_root(start: pathlib.Path) -> pathlib.Path:
    direct = start.parents[2]  # _lib -> flows -> foreman -> siteoffice
    if (direct / "board").is_dir():
        return direct
    for candidate in (start, *start.parents):
        probe = candidate / "50_projects" / "siteoffice"
        if probe.is_dir():
            return probe
    raise RuntimeError(
        "could not find the siteoffice checkout above " + str(start))


_SITEOFFICE_ROOT = _find_siteoffice_root(_HERE)
if str(_SITEOFFICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SITEOFFICE_ROOT))
from board import render_board as _board  # noqa: E402

LIVE_REFRESH_SECONDS = 30

# Passive theme follower, matching Mission Control's ?theme= convention: this
# board has no switch of its own (Mission Control is the only page with one,
# by design), it just reads the query param Mission Control's links (and its
# own drill-in modal iframe src) stamp on the way here, applies it, and keeps
# it stamped on any further link a reader might follow onward from this page.
# Public: render_foreman.py reuses THEME_SCRIPT via `rf.`.
THEME_SCRIPT = r"""<script>
(function () {
  "use strict";
  // Passive theme follower. Priority: an explicit ?theme= on the link wins,
  // then the family-wide shared key written by the ZND OS toggles
  // (Mission Control, Portfolio, Money), then this page family's legacy key,
  // then dark. Also listens for the shared key changing in another tab or
  // page, so a toggle flip on the deck restyles an already-open board live.
  var SHARED = "znd-theme";
  var LEGACY = "znd-board-theme-v1";
  function themeify(theme) {
    document.body.setAttribute("data-theme", theme);
    document.querySelectorAll("a[href]").forEach(function (a) {
      var href = a.getAttribute("href");
      if (!href || href.indexOf(".html") === -1 || href.charAt(0) === "#") return;
      var hashIdx = href.indexOf("#");
      var hash = hashIdx !== -1 ? href.slice(hashIdx) : "";
      var base = (hashIdx !== -1 ? href.slice(0, hashIdx) : href).split("?")[0];
      a.setAttribute("href", base + "?theme=" + theme + hash);
    });
  }
  var theme = null;
  try {
    var q = new URLSearchParams(window.location.search).get("theme");
    if (q === "dark" || q === "light") theme = q;
  } catch (e) {}
  if (!theme) { try { theme = localStorage.getItem(SHARED); } catch (e) {} }
  if (theme !== "dark" && theme !== "light") {
    try { theme = localStorage.getItem(LEGACY); } catch (e) {}
  }
  if (theme !== "dark" && theme !== "light") theme = "dark";
  try { localStorage.setItem(LEGACY, theme); } catch (e) {}
  themeify(theme);
  window.addEventListener("storage", function (ev) {
    if (ev.key !== SHARED) return;
    var next = ev.newValue;
    if (next !== "dark" && next !== "light") return;
    try { localStorage.setItem(LEGACY, next); } catch (e) {}
    themeify(next);
  });
})();
</script>"""


def is_awaiting(flow: dict) -> bool:
    """True if the flow is on the waiting-on-you queue.

    The GATE is authoritative when one exists (fixed 2026-08-01). The
    flow-level status is deliberately left at "awaiting-owner" by
    flow_transition.cmd_gate after a decision: only `advance` clears it back
    to "running" when it consumes the verdict. So a decided-but-not-yet-
    advanced flow still carries status "awaiting-owner", and checking that
    field first kept such flows on the "waiting on you" queue forever even
    though the owner had already answered. Once a gate is decided the flow is
    waiting on the orchestrator, not on the owner.

    Only when there is no gate at all does the flow-level status decide.
    """
    gate = flow.get("gate")
    if isinstance(gate, dict):
        return gate.get("status") == "awaiting-owner"
    return flow.get("status") == "awaiting-owner"


def render_index(flows_root) -> pathlib.Path:
    """Thin caller (Siteoffice fold, sprint 1 step 12): delegates to
    board.render_board's flows board kind (index + every per-flow page) and
    returns the new _board\flows\index.html path."""
    flows_root = pathlib.Path(flows_root)
    _board.main(["flows", str(flows_root)])
    return flows_root.parent / "_board" / "flows" / "index.html"


def render_flow(flow_dir) -> pathlib.Path:
    """Thin caller (Siteoffice fold, sprint 1 step 12): delegates to
    board.render_board's flows board kind and returns this flow's new
    _board\flows\<project>\<slug>\index.html path (not the old
    `dashboard.html` next to flow.json). Like render_dashboard.render_sprint,
    this re-renders the whole flows index and every per-flow page under the
    same root each call, not only this one flow; the board kind has no
    single-flow entry point. flow.json (present from flow creation) is what
    the board kind discovers on, so an awaiting or still-running flow always
    gets its page, matching the old per-transition behaviour."""
    flow_dir = pathlib.Path(flow_dir)
    flows_root = flow_dir.parent.parent
    _board.main(["flows", str(flows_root)])
    rel = flow_dir.relative_to(flows_root)
    return flows_root.parent / "_board" / "flows" / rel / "index.html"


def render_status_text(flow_dir) -> str:
    """Plain-text status block for one flow (mirrors render_dashboard status)."""
    flow_dir = pathlib.Path(flow_dir)
    fp = flow_dir / "flow.json"
    if not fp.exists():
        return f"error: no flow.json at {fp}"
    try:
        flow = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"error: flow.json malformed: {exc}"
    gate = flow.get("gate") if isinstance(flow.get("gate"), dict) else None
    lines = [
        "-" * 60,
        f"/flow {flow.get('project')} / {flow.get('slug')}",
        "-" * 60,
        f"  Stage:   {flow.get('stage')}",
        f"  Status:  {flow.get('status')}",
        f"  Updated: {flow.get('updated_at')}",
    ]
    if gate:
        lines.append(f"  Gate:    {gate.get('id')} [{gate.get('status')}] "
                     f"token={gate.get('token')}")
    lines.append("-" * 60)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="render_flows.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_flow = sub.add_parser("flow", help="Render one flow's dashboard.html")
    p_flow.add_argument("flow_dir", type=pathlib.Path)

    p_index = sub.add_parser("index", help="Rebuild index.html + index.json")
    p_index.add_argument("flows_root", type=pathlib.Path)

    p_status = sub.add_parser("status", help="Print a text status block for a flow")
    p_status.add_argument("flow_dir", type=pathlib.Path)

    args = parser.parse_args(argv)
    if args.cmd == "flow":
        print(str(render_flow(args.flow_dir)))
        return 0
    if args.cmd == "index":
        print(str(render_index(args.flows_root)))
        return 0
    if args.cmd == "status":
        print(render_status_text(args.flow_dir))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
