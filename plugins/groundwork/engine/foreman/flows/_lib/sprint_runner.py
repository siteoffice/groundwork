"""sprint_runner.py - the local headless sprint runner (Dispatch-free fallback).

Spec: (design spec, not shipped)

foreman_run.execute_launch AUTHORISES a /sprint line and writes it to
foreman.json under driver.launch. Its own docstring is explicit that it never
runs the line. Until this module existed there was exactly one way an
authorised line actually ran: a live session, with a human or Dispatch reading
it and pressing enter. Take that session away and the line sits in foreman.json
forever, correctly recorded, never run.

This module is a new, INDEPENDENT CONSUMER of that same record, the same
relationship the /foreman shim already has to it. It reads, it runs the line,
it does not change what "authorised" means. Nothing in foreman_run.py,
foreman_plan.py, flow_state.py or foreman_lock.py is written by this work.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import uuid
from typing import List, Optional

import flow_state as fs
import flow_transition as ft
import foreman_lock
import foreman_run
import foreman_status
import roots
import terminal_sweep as ts

DEFAULT_FLOWS_ROOT = str(roots.flows_root())
DEFAULT_SPRINTS_ROOT = str(roots.sprints_root())

# The executable name. It is NOT verified that a Scheduled Task's
# Interactive-logon session resolves `claude` the same way an interactive
# terminal does; if it does not, this constant takes the full path and nothing
# else in this module changes.
CLAUDE_EXE = "claude"

RUNNER_LOG_DIR = pathlib.Path(__file__).resolve().parent / "_runner_logs"
RECEIPT_ACTION = "driver_headless_run"

# Two hours, not foreman_lock's own 900s default: a sprint is a long process,
# and a lock broken out from under a running claude -p would let the next poll
# cycle launch the same line a second time.
DEFAULT_TTL_SECONDS = 7200


def build_argv(command: str) -> List[str]:
    """The exact argv for one headless run. No shell=True, ever.

    The whole /sprint line is ONE argv element: a Windows spec path carries
    spaces, and splitting here would hand claude several arguments instead of
    one command.
    """
    return [CLAUDE_EXE, "-p", command or ""]


def make_session_id(now: str, suffix: Optional[str] = None) -> str:
    """'runner-20260824T163220Z-9d13ec'.

    Deliberately NOT the interactive 'foreman-...' shape: a receipt must say at
    a glance whether a human's session or this poller pressed enter. The random
    tail keeps two cycles in the same second from colliding on a log filename.
    """
    stamp = (now or "").replace(":", "").replace("-", "")
    tail = suffix if suffix is not None else uuid.uuid4().hex[:6]
    return "runner-{}-{}".format(stamp, tail)


def find_candidates(flows_root, sprints_root,
                    now: Optional[str] = None) -> List[foreman_status.FlowStatus]:
    """Every authorised-but-unrun launch, oldest first.

    Goes through foreman_status.collect, the SAME read model the Mission Deck,
    the board and /foreman why already use, so this introduces no second source
    of truth about what a flow is waiting on.

    The predicate is kind == 'sprint_await' AND a non-empty command. Both
    sprint_await branches in foreman_plan matter here: the one that fires when a
    launch was authorised carries the authorised line as its command
    (foreman_plan.py:411), and the one that fires while a sprint is genuinely
    running carries '' (foreman_plan.py:469). The second is an ordinary wait,
    not something to run, so the empty command excludes it. No new FlowStatus
    field is added for this.

    Sort key is blocked_since, falling back to updated_at when it is '':
    sorting on the empty string would float an unknown-age row to the front of
    the queue and starve every genuinely older one.
    """
    rows = foreman_status.collect(flows_root, sprints_root, now)
    eligible = [r for r in rows if r.kind == "sprint_await" and r.command]
    eligible.sort(key=lambda r: r.blocked_since or r.updated_at or "")
    return eligible


def _receipt(flow_dir, now: str, session_id: str, outcome_word: str,
             detail: str) -> None:
    """One flow-log.ndjson receipt for this poll cycle's outcome.

    actor 'sprint_runner' and action 'driver_headless_run' are the two strings
    that make a headless run greppable apart from foreman_run's own
    'driver_sprint_launch'. _log_entry truncates detail at 240 characters, so
    every caller keeps its detail short and puts the identifying value first.

    No receipt is written for skipped_busy: foreman_lock.acquire already wrote
    its own lock_busy receipt before raising, and a second line would double
    count the same event.
    """
    ft.append_log(flow_dir, ft._log_entry(
        now, "execute", "sprint_runner", RECEIPT_ACTION, outcome="info",
        detail="{} {}".format(outcome_word, detail), ref=session_id))


def _run_claude(command: str, session_id: str, project: str = "",
                slug: str = "") -> pathlib.Path:
    """One headless run, its combined output written to a per-session log.

    timeout=None on purpose: a sprint is a long process, and a timeout here
    would kill a healthy run partway and leave a half-finished branch behind.
    The Scheduled Task's own ExecutionTimeLimit is the outer bound.

    RUNNER_LOG_DIR is read through the module global rather than captured at
    import time, so a test can point it at tmp_path.
    """
    log_dir = pathlib.Path(RUNNER_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "{}.log".format(session_id)
    proc = subprocess.run(build_argv(command), capture_output=True, text=True,
                          timeout=None)
    log_path.write_text(
        "session={}\nflow={}/{}\ncommand={}\nreturncode={}\n"
        "--- stdout ---\n{}\n--- stderr ---\n{}\n".format(
            session_id, project, slug, command, proc.returncode,
            proc.stdout or "", proc.stderr or ""),
        encoding="utf-8")
    return log_path


def run_once(flow_dir, project: str, slug: str, command: str, spec_path: str,
             sprints_root, now: Optional[str] = None,
             ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Run one authorised launch, or say why it was skipped.

    Returns 'ran', 'skipped_busy', 'skipped_terminal' or 'skipped_malformed'.

    A busy lock returns immediately with NO retry loop: the poller runs again in
    fifteen minutes, and retrying inside one invocation would hold a Scheduled
    Task open waiting on a session that may be an hour from finishing.

    Inside the lock, the exact two checks execute_launch itself runs are re-run
    against disk AS IT IS NOW, because up to one poll interval has passed since
    authorisation. That closes the window where foreman.json was edited, by hand
    or by a bug, in between. It is the same trust boundary foreman_lock's own
    docstring states ("one laptop and one human"), defended the cheap way: by
    re-checking two pure functions that already exist and are already tested,
    not by adding a new mandatory lock.
    """
    now = now or fs.iso_utc_now()
    session_id = make_session_id(now)
    try:
        with foreman_lock.acquire(flow_dir, session_id, now=now,
                                  ttl_seconds=ttl_seconds,
                                  stage="sprint_await"):
            # Terminal first: "someone already ran it" is the more useful
            # sentence in the receipt than "the line looks wrong" when both are
            # true, and it is the cheaper mistake to make.
            match = ts.has_terminal_sprint(project, spec_path,
                                           ts.discover_sprints(sprints_root))
            if match:
                _receipt(flow_dir, now, session_id, "skipped_terminal",
                         "sprint {} status={} already terminal for this spec".format(
                             match.get("sprint_id"), match.get("status")))
                return "skipped_terminal"

            problem = foreman_run.validate_launch_line(command, spec_path)
            if problem:
                # Verbatim: the offending value is the whole point of the
                # sentence validate_launch_line returns.
                _receipt(flow_dir, now, session_id, "skipped_malformed", problem)
                return "skipped_malformed"

            log_path = _run_claude(command, session_id, project, slug)
            # Detail is truncated at 240 characters by _log_entry, so the log
            # FILENAME goes in rather than its full path: the directory is a
            # module constant and the name is the session id, which is also the
            # receipt's ref.
            _receipt(flow_dir, now, session_id, "ran",
                     "headless, log={}".format(log_path.name))
            return "ran"
    except foreman_lock.LockBusy:
        return "skipped_busy"


def poll_once(flows_root, sprints_root, now: Optional[str] = None) -> str:
    """One poll cycle: run the single oldest authorised launch, or nothing.

    With zero candidates this writes NOTHING and prints NOTHING. That is the
    common case fifteen minutes out of fifteen, and the existing --await poll's
    own philosophy (receipts at start and terminal only) applies here too: a
    scheduled job that logs every idle tick buries the one line that matters.

    Never more than one candidate per invocation, even when several flows are
    eligible at once. The rest are picked up on the following cycles.
    """
    now = now or fs.iso_utc_now()
    candidates = find_candidates(flows_root, sprints_root, now)
    if not candidates:
        return "no_candidates"

    row = candidates[0]
    # FlowStatus carries no spec_path, so it comes from the driver record that
    # execute_launch itself wrote, which is the same value it validated at
    # authorisation time. A deleted foreman.json leaves it "", and
    # validate_launch_line then refuses inside run_once: a missing record must
    # never become a launch on a guessed path.
    launch = (foreman_run.load_driver(row.flow_dir) or {}).get("launch") or {}
    outcome = run_once(row.flow_dir, row.project, row.slug, row.command,
                       launch.get("spec_path") or "", sprints_root, now=now)
    print("sprint_runner: {}/{} {}".format(row.project, row.slug, outcome))
    return outcome


def main(argv=None) -> int:
    """Exit 0 for every outcome. LastTaskResult is how the Scheduled Task's
    health gets checked, and "no candidate" and "candidate was busy" are both
    healthy. A non-zero exit is reserved for a crash in the runner itself, which
    is why nothing here catches a broad exception."""
    parser = argparse.ArgumentParser(
        description="Run the oldest authorised-but-unrun /sprint launch "
                    "headlessly. Never authorises a launch of its own, never "
                    "merges, never pushes.")
    sub = parser.add_subparsers(dest="cmd")
    poll = sub.add_parser("poll-once",
                          help="run at most one authorised launch, then exit")
    poll.add_argument("--flows-root", default=DEFAULT_FLOWS_ROOT)
    poll.add_argument("--sprints-root", default=DEFAULT_SPRINTS_ROOT)
    args = parser.parse_args(argv)
    if args.cmd != "poll-once":
        parser.print_help()
        return 0
    poll_once(args.flows_root, args.sprints_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
