"""Retro report model: silent-failure hunt (built from main digests only)."""
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

HOOK_MIN_SESSIONS = 3
MCP_MIN_SESSIONS = 3


def find_silent_failures(digests: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    final_errors = []  # type: List[Dict[str, Any]]
    no_success = []  # type: List[Dict[str, Any]]
    hooks = {}  # type: Dict[str, int]
    mcp = {}  # type: Dict[str, int]
    for d in digests:
        sid = d.get("session_id")
        task = d.get("scheduled_task")
        if d.get("headless") and d.get("final_error"):
            final_errors.append({"session": sid, "date": (d.get("start") or "")[:10],
                                 "scheduled_task": task, "snippet": d["final_error"]})
        if task and not d.get("success_line"):
            no_success.append({"session": sid, "scheduled_task": task,
                               "date": (d.get("start") or "")[:10]})
        for text in (d.get("hook_errors") or {}):
            hooks[text] = hooks.get(text, 0) + 1
        for server in set(d.get("mcp_failures") or []):
            mcp[server] = mcp.get(server, 0) + 1
    return {
        "final_errors": final_errors,
        "hooks": [{"text": t, "sessions": n}
                  for t, n in sorted(hooks.items(), key=lambda kv: (-kv[1], kv[0]))
                  if n >= HOOK_MIN_SESSIONS],
        "mcp": [{"server": s, "sessions": n}
                for s, n in sorted(mcp.items(), key=lambda kv: (-kv[1], kv[0]))
                if n >= MCP_MIN_SESSIONS],
        "no_success_line": no_success,
    }
