"""foreman_init.py - scaffold a fresh Foreman hub directory.

`init(hub_root)` creates the directory contract a stranger's checkout needs
before any Foreman command runs against it: the flow/sprint/foreman boards,
the log files hub-update reads and writes, DEADLINES.md with a `## NOW`
block, and a minimal valid `znd_os_state.json` (assemble.py's only hard
`SystemExit` today is a missing one, per the extraction design doc section 3).

Refuses to run against a directory that does not already contain this
codebase (no `flows/_lib`, no `sprints/_lib`): that guards against scaffolding
into an unrelated folder because the operator pointed --hub-root at the wrong
place. Idempotent: an already-scaffolded hub is left untouched unless
`--force` overwrites the state file and DEADLINES.md.

CLI:
    python foreman_init.py [--hub-root PATH] [--force]

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import List, Optional

import roots

CODE_MARKERS = ("flows/_lib", "sprints/_lib")

SCAFFOLD_DIRS = (
    "flows",
    "sprints",
    "foreman",
    "03_logs",
    "04_engineering/command_center/state",
    "docs/superpowers/specs",
)

_LEDGER_HEADER = "timestamp\tsource\tsummary\n"
_HUB_INBOX_TEMPLATE = "# Hub inbox\n\n(no entries yet)\n"
_DEADLINES_TEMPLATE = "# Deadlines\n\n## NOW\n\n(no entries yet)\n"


class NotAFormanHubError(RuntimeError):
    """Raised when `hub_root` does not look like a Foreman checkout."""


def _iso_utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_recognised_hub(hub_root: pathlib.Path) -> bool:
    """True if `hub_root` already contains this codebase's marker directories."""
    return all((hub_root / marker).is_dir() for marker in CODE_MARKERS)


def minimal_state(now: Optional[str] = None) -> dict:
    """A minimal but valid znd_os_state.json: every field assemble.py and its
    consumers read with `.get()` is present, all empty until real sources
    accumulate history."""
    now = now or _iso_utc_now()
    return {
        "generated_at": now,
        "generated_by": "foreman_init.py",
        "staleness_threshold_hours": 24,
        "schema": "command_center/README.md",
        "sources": [],
        "flags": [],
        "deadlines": [],
        "this_week": {"start_date": "", "end_date": "", "source": "", "days": []},
        "projects": [],
        "revenue": {},
    }


def init(hub_root: pathlib.Path, force: bool = False) -> List[str]:
    """Scaffold `hub_root`. Returns a list of human-readable actions taken.

    Raises NotAFormanHubError if `hub_root` does not contain this codebase.
    Directories are always created (mkdir is naturally idempotent); files are
    written only if missing, unless `force` is set.
    """
    hub_root = pathlib.Path(hub_root)
    if not is_recognised_hub(hub_root):
        raise NotAFormanHubError(
            "{} does not look like a Foreman checkout (missing {}); "
            "refusing to scaffold an unrelated directory".format(
                hub_root, " and ".join(CODE_MARKERS))
        )

    actions: List[str] = []

    for rel in SCAFFOLD_DIRS:
        d = hub_root / rel
        existed = d.is_dir()
        d.mkdir(parents=True, exist_ok=True)
        if not existed:
            actions.append("created directory {}".format(d))

    state_path = hub_root / "04_engineering" / "command_center" / "state" / "znd_os_state.json"
    if force or not state_path.exists():
        state_path.write_text(
            json.dumps(minimal_state(), indent=2) + "\n", encoding="utf-8")
        actions.append("wrote {}".format(state_path))

    deadlines_path = hub_root / "DEADLINES.md"
    if force or not deadlines_path.exists():
        deadlines_path.write_text(_DEADLINES_TEMPLATE, encoding="utf-8")
        actions.append("wrote {}".format(deadlines_path))

    ledger_path = hub_root / "03_logs" / "hub_update_ledger.tsv"
    if not ledger_path.exists():
        ledger_path.write_text(_LEDGER_HEADER, encoding="utf-8")
        actions.append("wrote {}".format(ledger_path))

    inbox_path = hub_root / "03_logs" / "hub_inbox.md"
    if not inbox_path.exists():
        inbox_path.write_text(_HUB_INBOX_TEMPLATE, encoding="utf-8")
        actions.append("wrote {}".format(inbox_path))

    return actions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="foreman_init.py")
    parser.add_argument("--hub-root", default=None,
        help="hub root to scaffold; defaults to roots.hub_root()")
    parser.add_argument("--force", action="store_true",
        help="overwrite an existing state file and DEADLINES.md")
    args = parser.parse_args(argv)

    hub_root = roots.hub_root(args.hub_root)
    try:
        actions = init(hub_root, force=args.force)
    except NotAFormanHubError as exc:
        print("[foreman_init] {}".format(exc), file=sys.stderr)
        return 1

    if not actions:
        print("[foreman_init] {} already scaffolded, nothing to do".format(hub_root))
        return 0
    print("[foreman_init] scaffolded {}:".format(hub_root))
    for a in actions:
        print("  - {}".format(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
