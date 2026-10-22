"""slack_sweep_runner.py - local, LLM-free replacement for the znd-slack-sweep
Cowork task (token-burn fix, 2026-07-27).

The Cowork task burned ~4.50 USD-equivalent per 30-minute run booting a full
Opus session, usually to conclude "nothing to do". This runner does the same
sweep cycle for zero tokens: Windows Task Scheduler invokes it every 10
minutes (cadence tightened from 30 on 2026-07-27; a no-op tick is pure local
file reads, so frequency is free), it short-circuits locally when no gate is
awaiting-owner, and only touches the Slack Web API (via
slack_web_api.make_transport) when there is actual work. All decision logic stays in the already-tested pure modules
(slack_sweep_host, slack_channel, sweep_core, flow_transition, outbox);
nothing is re-implemented here.

Cycle (same as the retired Cowork task, spec
(design spec, not shipped)):
  1. short-circuit: no flow.json with gate.status == "awaiting-owner" -> log a
     no-op line and exit 0 without loading a token or making any network call.
  2. otherwise run slack_sweep_host.sweep_once() with the real transport:
     post new gate prompts, poll open threads, apply decisions via cmd_gate,
     post due escalation reminders.
  3. after any state change (posted/decided > 0), re-render the affected flow
     dashboards and the index via render_flows.
  4. append one ndjson record to slack-sweep-log.ndjson
     (host: "slack-sweep-runner").

Guardrails inherited unchanged: never edits or deletes Slack messages, never
posts to #znd-alerts, never runs git, never writes under sprints\\, safe to
re-run (idempotency lives in outbox + cmd_gate first-decision-wins).

Exit codes: 0 = clean (including no-op), 1 = sweep ran with per-flow errors,
2 = fatal (token missing, transport failure before any work).

Windows Task Scheduler registration: see RUNNER_SETUP.md in this folder.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import flow_state as fs            # noqa: E402
import slack_sweep_host as host    # noqa: E402

FLOWS_ROOT = pathlib.Path(host.DEFAULT_FLOWS_ROOT)
LOG_PATH = _HERE / "slack-sweep-log.ndjson"
# Default is the ZND deployment's #znd-gates channel id; ZND_GATE_CHANNEL_ID
# still wins when set. The empty default here (not in slack_sweep_host, whose
# constant this runner overrides in main()) is what made every gate prompt
# fail with channel_not_found whenever the scheduled task's environment lost
# the env var: the step 6 G3 opened 2026-08-17T21:29Z and never reached Slack,
# while the sweep logged the same error every 10 minutes for 11 hours.
GATE_CHANNEL_ID = os.environ.get("ZND_GATE_CHANNEL_ID", "C0BK70BTFQ9")


def _log(record: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def awaiting_gates(flows_root: pathlib.Path) -> list:
    """Flow dirs whose gate.status == 'awaiting-owner' (the short-circuit test)."""
    out = []
    for ctx in host.discover_flows(flows_root):
        gate = ctx["flow"].get("gate")
        if isinstance(gate, dict) and gate.get("status") == "awaiting-owner":
            out.append(ctx["flow_dir"])
    return out

def _render(flow_dirs, flows_root: pathlib.Path) -> list:
    """Re-render each touched flow dashboard plus the index. Best effort."""
    notes = []
    py = sys.executable or "python"
    render = str(_HERE / "render_flows.py")
    for d in list(flow_dirs) + ["__index__"]:
        args = ([py, render, "index", str(flows_root)] if d == "__index__"
                else [py, render, "flow", str(d)])
        try:
            subprocess.run(args, check=True, capture_output=True, timeout=120)
        except Exception as exc:
            notes.append(f"render failed for {d}: {exc}")
    return notes


def main() -> int:
    now = fs.iso_utc_now()
    open_dirs = awaiting_gates(FLOWS_ROOT)
    if not open_dirs:
        _log({"ts": now, "host": "slack-sweep-runner", "posted": 0, "polled": 0,
              "decided": 0, "reminders": 0, "open_gates": 0,
              "notes": ["no gate awaiting-owner; no-op, no network call"],
              "errors": []})
        return 0

    try:
        import slack_web_api
        send_message, read_thread = slack_web_api.make_transport()
    except Exception as exc:
        _log({"ts": now, "host": "slack-sweep-runner", "posted": 0, "polled": 0,
              "decided": 0, "reminders": 0, "open_gates": len(open_dirs),
              "notes": [], "errors": [f"transport unavailable: {exc}"]})
        print(f"[slack-sweep-runner] fatal: {exc}", file=sys.stderr)
        return 2

    host.GATE_CHANNEL_ID = GATE_CHANNEL_ID
    result = host.sweep_once(FLOWS_ROOT, send_message, read_thread, now)

    notes = [f"open gates: {len(open_dirs)}"]
    if result["posted"] or result["decided"]:
        notes += _render(open_dirs, FLOWS_ROOT)
        notes.append("dashboards re-rendered")

    _log({"ts": now, "host": "slack-sweep-runner",
          "posted": result["posted"], "polled": result["polled"],
          "decided": result["decided"], "reminders": result["reminders"],
          "open_gates": len(open_dirs), "notes": notes,
          "errors": result["errors"]})
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:
        # Headless rule: the scheduled task runs via pyw/pythonw (background,
        # no console window, house preference 2026-07-27), so there is no
        # stderr anyone sees. An unhandled crash must land in the log.
        _log({"ts": fs.iso_utc_now(), "host": "slack-sweep-runner",
              "posted": 0, "polled": 0, "decided": 0, "reminders": 0,
              "open_gates": -1, "notes": [],
              "errors": [f"unhandled: {exc!r}"]})
        rc = 2
    sys.exit(rc)
