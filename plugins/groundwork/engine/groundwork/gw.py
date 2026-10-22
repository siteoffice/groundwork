#!/usr/bin/env python3
"""gw.py - the Groundwork command line for one loop, idea to close.

A thin layer over the vendored Foreman engine (foreman\\flows\\_lib). It never edits state by
hand: every change goes through the engine's own functions. It adds one thing the engine does
not do: a local, unsigned receipt when a change is accepted at the G4 ship gate.

  gw.py --root <folder> new <project> "<idea>"
  gw.py --root <folder> status [<project>/<slug>]
  gw.py --root <folder> advance <project>/<slug> [--steps "a;b;c"] [--spec <path>]
  gw.py --root <folder> gate <project>/<slug> <verdict> [note]
  gw.py --root <folder> close <project>/<slug> "<reason>"

Records go to <folder>\\flows. Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_LIB = _HERE.parent / "foreman" / "flows" / "_lib"
for _p in (str(_LIB), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_state as fs  # noqa: E402
import flow_transition as ft  # noqa: E402
import receipt  # noqa: E402


def _flow_dir(root, ref):
    if "/" not in ref:
        raise ValueError("flow reference must look like <project>/<slug>")
    project, slug = ref.split("/", 1)
    return pathlib.Path(root) / "flows" / project / slug


def _summary(flow):
    gate = flow.get("gate")
    line = "stage=%s status=%s" % (flow.get("stage"), flow.get("status"))
    cs = flow.get("current_step") or {}
    if cs.get("phase"):
        line += " step=%s/%s phase=%s" % (cs.get("index", 0) + 1, cs.get("of"), cs.get("phase"))
    if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
        line += " | GATE %s open, token %s, verdicts: %s" % (
            gate["id"], gate["token"], ", ".join(fs.valid_verdicts(gate["id"])))
    elif isinstance(gate, dict) and gate.get("status") == "decided":
        line = line.replace("status=awaiting-owner", "status=decided")
        line += " | GATE %s decided: %s. Run advance to apply it" % (
            gate["id"], (gate.get("decision") or {}).get("verdict"))
    return line


def _parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="folder that holds your flows records")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("project")
    p.add_argument("idea")
    p = sub.add_parser("status")
    p.add_argument("flow", nargs="?")
    p = sub.add_parser("advance")
    p.add_argument("flow")
    p.add_argument("--steps", help="semicolon-separated step titles, needed when G1 is approved")
    p.add_argument("--spec", help="path to the approved spec, recorded when G2 is approved")
    p = sub.add_parser("gate")
    p.add_argument("flow")
    p.add_argument("verdict")
    p.add_argument("note", nargs="?", default="")
    p = sub.add_parser("close")
    p.add_argument("flow")
    p.add_argument("reason")
    return ap


def _status(root, ref):
    if ref:
        print(_summary(ft.load_flow(_flow_dir(root, ref))))
        return
    found = sorted((root / "flows").glob("*/*/flow.json")) if (root / "flows").exists() else []
    for f in found:
        flow = ft.load_flow(f.parent)
        print("%s/%s: %s" % (flow["project"], flow["slug"], _summary(flow)))
    if not found:
        print("no flows yet")


def _gate(root, a):
    fdir = _flow_dir(root, a.flow)
    before = ft.load_flow(fdir).get("gate")
    flow = ft.cmd_gate(fdir, a.verdict, note=a.note, surface="session")
    print(_summary(flow))
    after = flow.get("gate")
    fresh = (isinstance(before, dict) and before.get("status") == "awaiting-owner"
             and isinstance(after, dict) and after.get("status") == "decided")
    if fresh and after.get("id") == "G4" and a.verdict == "ship":
        j, h = receipt.write_receipt(flow, after, fdir)
        print("receipt (%s): %s" % (receipt.LABEL, h))
        print("receipt data: %s" % j)


def _close(root, a):
    """Close a flow by hand: it will never reach G3. Not a gate verdict, so no receipt."""
    reason = (a.reason or "").strip()
    if not reason:
        raise ValueError("a reason is required to close a flow")
    fdir = _flow_dir(root, a.flow)
    flow = ft.load_flow(fdir)
    now = fs.iso_utc_now()
    if flow.get("stage") == "closed":
        ft.append_log(fdir, ft._log_entry(now, "closed", "session", "reply_ignored",
                                          outcome="info", detail="reply_ignored (already closed)"))
    else:
        flow["stage"] = "closed"
        flow["status"] = "closed"
        flow["gate"] = None
        flow["updated_at"] = now
        ft.write_flow(fdir, flow)
        ft.append_log(fdir, ft._log_entry(now, "closed", "session", "closed_manual",
                                          outcome="info", detail=reason))
    print(_summary(flow))


def main(argv=None):
    a = _parser().parse_args(argv)
    root = pathlib.Path(a.root)
    try:
        if a.cmd == "new":
            flow_dir, flow = ft.cmd_new(root / "flows", a.project, a.idea)
            print("created %s" % flow_dir)
            print(_summary(flow))
        elif a.cmd == "status":
            _status(root, a.flow)
        elif a.cmd == "advance":
            steps = [s.strip() for s in a.steps.split(";") if s.strip()] if a.steps else None
            flow, line = ft.cmd_advance(_flow_dir(root, a.flow), steps=steps, spec_path=a.spec)
            print(_summary(flow))
            if line:
                # The engine also builds a /sprint line for a sprint runner Groundwork does not
                # ship. The work step is done in the session, so say that instead.
                print("work step ready: do it now, then run advance to open the ship gate")
        elif a.cmd == "gate":
            _gate(root, a)
        elif a.cmd == "close":
            _close(root, a)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print("refused: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
