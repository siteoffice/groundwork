"""slack_web_api.py - stdlib Slack Web API transport for the local sweep runner.

Replaces the Cowork Slack connector for the znd-slack-sweep task (token-burn
fix, 2026-07-27). Provides exactly the two callables slack_sweep_host.main()
expects, with the same contracts its docstring assumes:

  send_message(channel_id, text, thread_ts=None) -> {"ts": ..., "channel_id": ...}
  read_thread(channel_id, message_ts)            -> list of reply dicts

Token: env var SLACK_BOT_TOKEN, else SLACK_BOT_TOKEN= line in <hub_root>\\.env
(gitignored, covered by the ungit backup; hub root resolved via roots.py).
Required Slack app scopes: chat:write, channels:history (groups:history if
#znd-gates is ever made private). The bot must be a member of #znd-gates.

STDLIB ONLY. One retry on HTTP 429 honoring Retry-After. Every Slack API
"ok": false response raises RuntimeError with the Slack error code so the
runner logs it instead of half-succeeding. urlopen is injectable for tests.

Python 3.8+.
"""
from __future__ import annotations

import json
import pathlib
import os
import time
import urllib.parse
import urllib.request
from typing import Callable, List, Optional

import roots

API_BASE = "https://slack.com/api"
ENV_FILE = roots.hub_root() / ".env"


def load_token(env_file: Optional[pathlib.Path] = None) -> str:
    """SLACK_BOT_TOKEN from the environment, else from the .env file."""
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if tok:
        return tok
    path = env_file or ENV_FILE
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SLACK_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            # Tolerate a bare token line (real-world .env from 2026-07-27 had
            # exactly this: the xoxb- value without the KEY= prefix).
            if line.startswith("xoxb-"):
                return line.strip('"').strip("'")
    raise RuntimeError(
        "SLACK_BOT_TOKEN not set and not found in "
        + str(path)
        + " ; create a bot token (scopes: chat:write, channels:history), "
          "invite the bot to #znd-gates, and store the token there."
    )


def _call(method: str, payload: dict, token: str,
          urlopen: Callable = urllib.request.urlopen,
          _retried: bool = False) -> dict:
    """POST one Slack Web API method, return the parsed JSON, raise on ok=false."""
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        if exc.code == 429 and not _retried:
            wait = int(exc.headers.get("Retry-After", "5"))
            time.sleep(min(wait, 60))
            return _call(method, payload, token, urlopen, _retried=True)
        raise RuntimeError(f"slack {method} HTTP {exc.code}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"slack {method} failed: {data.get('error', 'unknown')}")
    return data


def make_transport(token: Optional[str] = None,
                   urlopen: Callable = urllib.request.urlopen):
    """Build (send_message, read_thread) bound to one token.

    Shapes match what slack_sweep_host.process_open_gate and
    process_escalations consume: send_message returns a dict with "ts";
    read_thread returns the raw messages list (parent included; the host's
    slack_channel.normalize_thread excludes it by ts).
    """
    tok = token or load_token()

    def send_message(channel_id: str, text: str,
                     thread_ts: Optional[str] = None) -> dict:
        payload = {"channel": channel_id, "text": text, "unfurl_links": "false"}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = _call("chat.postMessage", payload, tok, urlopen)
        return {"ts": data["ts"], "channel_id": data.get("channel", channel_id)}

    def read_thread(channel_id: str, message_ts: str) -> List[dict]:
        data = _call("conversations.replies",
                     {"channel": channel_id, "ts": message_ts, "limit": "200"},
                     tok, urlopen)
        return data.get("messages", [])

    return send_message, read_thread
