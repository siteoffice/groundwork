"""foreman_lock.py - the advisory per-flow lockfile (spec slice b, "The lock").

Spec: (design spec, not shipped)

`foreman.lock` sits next to flow.json and holds pid, host, session_id and an
ISO 8601 UTC timestamp. It is taken for the duration of any state-changing
action and released in a finally block. A lock older than a configurable TTL is
broken with a logged `lock_broken` receipt; a live lock makes the caller lose
with a logged `lock_busy` receipt and a LockBusy exception.

It is ADVISORY. It coordinates the cooperating writers in this repo (the driver,
the Slack sweep runner, the terminal sweep, the interactive /flow command); it
is not an OS-level mandatory lock and it does not defend against a hostile
writer. That is the right trade for one laptop and one human.

Wiring the four callers is slice b and is NOT done here: this module lands with
its tests and no caller, so the chain that needs it can take it as a
one-line `with` at each call site.

Atomicity comes from os.open with O_CREAT|O_EXCL, which is atomic on NTFS and on
POSIX. Everything else in this module is ordinary file I/O.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import pathlib
import socket
from typing import Optional

import flow_state as fs
import flow_transition as ft

LOCK_FILENAME = "foreman.lock"
DEFAULT_TTL_SECONDS = 900          # 15 minutes, one Slack sweep interval plus slack
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


class LockBusy(RuntimeError):
    """The flow lock is held by a holder that is not stale."""

    def __init__(self, holder: dict):
        self.holder = holder or {}
        super().__init__(
            "foreman.lock held by pid={} host={} session={} since {}".format(
                self.holder.get("pid"), self.holder.get("host"),
                self.holder.get("session_id"), self.holder.get("acquired_at"))
        )


def lock_path(flow_dir) -> pathlib.Path:
    """The lockfile path for a flow directory (next to flow.json)."""
    return pathlib.Path(flow_dir) / LOCK_FILENAME


# --- pure helpers -----------------------------------------------------------

def _parse_ts(ts):
    """ISO 8601 'Z' string to an aware datetime, or None. Pure.

    strptime rather than fromisoformat: fromisoformat does not accept the
    trailing 'Z' before Python 3.11 and the floor here is 3.8.
    """
    try:
        return _dt.datetime.strptime(ts, _TS_FMT).replace(
            tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def age_seconds(acquired_at, now) -> Optional[int]:
    """Whole seconds between two ISO 'Z' stamps, or None if either is unusable."""
    then, current = _parse_ts(acquired_at), _parse_ts(now)
    if then is None or current is None:
        return None
    return int((current - then).total_seconds())


def is_stale(holder: Optional[dict], now: str,
             ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """True if `holder` may be broken. Pure, no clock.

    A holder with a missing, corrupt or unparseable timestamp is stale by
    definition: the alternative is a malformed file wedging a flow forever.
    """
    age = age_seconds((holder or {}).get("acquired_at"), now)
    if age is None:
        return True
    return age >= ttl_seconds


# --- file I/O ---------------------------------------------------------------

def read_lock(flow_dir) -> Optional[dict]:
    """The current holder dict, or None if absent, unreadable or not JSON."""
    try:
        raw = lock_path(flow_dir).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        holder = json.loads(raw)
    except ValueError:
        return None
    return holder if isinstance(holder, dict) else None


def _create_exclusive(path: pathlib.Path, holder: dict) -> bool:
    """Atomically create `path` holding `holder`. False if it already exists."""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(holder) + "\n")
    return True


def _receipt(flow_dir, now: str, stage: str, action: str, outcome: str,
             detail: str) -> None:
    """Append one lock receipt to flow-log.ndjson. Never raises: a lock that
    cannot log is still a working lock, and losing the receipt must not turn
    into losing the flow."""
    try:
        ft.append_log(flow_dir, ft._log_entry(
            now, stage, "foreman", action, outcome=outcome, detail=detail))
    except OSError:
        pass


def release(flow_dir, holder: dict) -> bool:
    """Remove the lockfile if it still records `holder`. True if removed.

    The ownership check matters: if our lock went stale and another writer broke
    it and took over, deleting the file would hand them a silent double-write.
    """
    current = read_lock(flow_dir)
    if current is None:
        # Absent, or unreadable/unparseable, including the just-created but
        # not-yet-written window between _create_exclusive's os.open and its
        # os.fdopen write completing. Never treat that as ours to delete: it
        # may be another writer's lock mid-write.
        return False
    if (current.get("session_id") != (holder or {}).get("session_id")
            or current.get("pid") != (holder or {}).get("pid")):
        return False
    try:
        lock_path(flow_dir).unlink()
    except OSError:
        return False
    return True


@contextlib.contextmanager
def acquire(flow_dir, session_id: str, now: Optional[str] = None,
            ttl_seconds: int = DEFAULT_TTL_SECONDS, pid: Optional[int] = None,
            host: Optional[str] = None, stage: str = "", log: bool = True):
    """Hold the flow lock for the body, and release it in a finally block.

    Raises LockBusy, without entering the body, when a non-stale holder exists.
    Breaks a stale holder first, recording a `lock_broken` receipt.
    """
    now = now or fs.iso_utc_now()
    path = lock_path(flow_dir)
    holder = {
        "pid": os.getpid() if pid is None else pid,
        "host": host or socket.gethostname(),
        "session_id": session_id,
        "acquired_at": now,
    }

    if not _create_exclusive(path, holder):
        existing = read_lock(flow_dir)
        if not is_stale(existing, now, ttl_seconds):
            if log:
                _receipt(flow_dir, now, stage, "lock_busy", "blocked",
                         "lock held by session={} pid={} since {}".format(
                             (existing or {}).get("session_id"),
                             (existing or {}).get("pid"),
                             (existing or {}).get("acquired_at")))
            raise LockBusy(existing or {})
        age = age_seconds((existing or {}).get("acquired_at"), now)
        if log:
            _receipt(flow_dir, now, stage, "lock_broken", "info",
                     "broke stale lock session={} pid={} age={}s ttl={}s".format(
                         (existing or {}).get("session_id"),
                         (existing or {}).get("pid"),
                         "unknown" if age is None else age, ttl_seconds))
        try:
            path.unlink()
        except OSError:
            pass
        if not _create_exclusive(path, holder):
            # Lost a genuine race to break the same stale lock. The other writer
            # owns it now and it is fresh, so this caller loses cleanly.
            existing = read_lock(flow_dir)
            if log:
                _receipt(flow_dir, now, stage, "lock_busy", "blocked",
                         "lost the race to break a stale lock, now held by "
                         "session={}".format((existing or {}).get("session_id")))
            raise LockBusy(existing or {})

    try:
        yield holder
    finally:
        release(flow_dir, holder)
