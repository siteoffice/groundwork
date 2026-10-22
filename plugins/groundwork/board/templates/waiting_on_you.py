"""Waiting-on-you panel: open flow gates (only when the manifest or its
root declares a flows root), an open PR count per repo (GITHUB_TOKEN or the
gh CLI's stored token only, note 4.7), and open funnel cards from
00_funnel\\ stage folders. Collector reads; render only formats what
collect() found.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc, flow_gate_is_open  # noqa: E402
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import funnel  # noqa: E402  (sibling template module, shared 00_funnel read)

STATE_UNAVAILABLE = "unavailable"
STATE_EMPTY = "empty"
STATE_NORMAL = "normal"

_PR_FETCH_TIMEOUT = 4


def _flows_root(manifest: dict):
    declared = manifest.get("flows")
    if not declared:
        return None
    p = pathlib.Path(declared)
    if not p.is_absolute():
        p = (manifest["_dir"] / declared).resolve()
    return p


def _open_gates(manifest: dict):
    """(items, unavailable). unavailable is True only when a flows root IS
    declared but cannot be read; no declaration simply means no gates,
    silently, per the data-source table. Uses the one shared pending-gate
    predicate (board_common.flow_gate_is_open, T09, R05) so a decided gate
    excludes itself here exactly the same way it does in the flows board
    kind and nowhere silently disagrees with the other."""
    root = _flows_root(manifest)
    if root is None:
        return [], False
    if not root.is_dir():
        return [], True
    items = []
    for flow_path in sorted(root.glob("*/*/flow.json")):
        try:
            flow = json.loads(flow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if flow_gate_is_open(flow):
            project = flow.get("project", "")
            slug = flow.get("slug", "")
            items.append({"text": f"{project}/{slug}: gate open", "date": None})
    return items, False


def _funnel_cards(manifest: dict):
    """Funnel cards whose next move is the reader's (data-source table,
    step 7 guide): settings.reader (a person key) narrows to that person's
    own cards; absent, every card still waiting on a gate is listed with
    its owner named, so nothing is silently hidden. A card in the funnel's
    last stage (no gate left) is done with the funnel, not waiting on
    anyone, and is excluded here (funnel.py still shows it in its column)."""
    funnel_dir = funnel.find_funnel_dir(manifest["_dir"])
    if funnel_dir is None:
        return []
    reader = manifest["settings"].get("reader")
    cards = []
    for stage_name, _label_key, stage_cards in funnel.collect_columns(funnel_dir):
        for card in stage_cards:
            if not card.get("gate"):
                continue
            if reader and card.get("owner") != reader:
                continue
            owner_suffix = "" if reader else f" ({card.get('owner', '')})"
            cards.append({
                "text": f"{stage_name}: {card['title']}{owner_suffix}",
                "date": None,
            })
    return cards


def _repo_slug(node_dir: pathlib.Path):
    config_path = node_dir / ".git" / "config"
    if not config_path.is_file():
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"url\s*=\s*(\S+)", text)
    if not m:
        return None
    m2 = re.search(r"github\.com[:/]+([^/]+)/([^/.\s]+)", m.group(1))
    if not m2:
        return None
    return f"{m2.group(1)}/{m2.group(2)}"


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _fetch_open_pr_count(slug: str, token: str):
    """One call, one repo. None on any failure (network, rate limit,
    anything else): a missing PR count is not a broken board (note 4.7)."""
    url = f"https://api.github.com/search/issues?q=repo:{slug}+type:pr+state:open"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "siteoffice-board-renderer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_PR_FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("total_count")
    except Exception:
        return None


def collect_open_pr_count(node_dir: pathlib.Path, fetch=_fetch_open_pr_count):
    """None means "nothing to show", not "unavailable": no repo and no
    token are both ordinary, silent cases (note 4.7)."""
    slug = _repo_slug(node_dir)
    if slug is None:
        return None
    token = _github_token()
    if not token:
        return None
    return fetch(slug, token)


def collect(manifest: dict):
    gates, gates_unavailable = _open_gates(manifest)
    if gates_unavailable:
        return STATE_UNAVAILABLE, {"items": [], "prCount": None}

    cards = _funnel_cards(manifest)
    pr_count = collect_open_pr_count(manifest["_dir"])

    items = list(gates) + list(cards)
    if pr_count:
        items.append({"text": f"{pr_count} open pull request(s)", "date": None})

    if not items:
        return STATE_EMPTY, {"items": [], "prCount": pr_count}
    return STATE_NORMAL, {"items": items, "prCount": pr_count}


def render(data: dict, labels: dict, state: str) -> str:
    title = labels.get("waiting_on_you", "waiting_on_you")

    if state == STATE_UNAVAILABLE:
        body = (
            f'<p class="empty warn"><span class="msg" data-i18n="state_unavailable">'
            f'{esc(labels.get("state_unavailable", "state_unavailable"))}</span></p>'
        )
    elif state == STATE_EMPTY:
        body = (
            f'<p class="empty"><span class="msg" data-i18n="state_empty">'
            f'{esc(labels.get("state_empty", "state_empty"))}</span></p>'
        )
    else:
        items = "".join(f"<li>{esc(i['text'])}</li>" for i in data.get("items", []))
        body = f'<ul class="rows">{items}</ul>'

    return (
        f'<section class="panel" data-panel="waiting_on_you">'
        f'<div class="head"><h2 data-i18n="waiting_on_you">{esc(title)}</h2></div>'
        f'<div class="body">{body}</div>'
        f"</section>"
    )
