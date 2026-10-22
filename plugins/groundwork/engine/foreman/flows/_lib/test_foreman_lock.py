"""Tests for foreman_lock.py. Plain pytest, stdlib only, tmp_path only.

No real flow directory is touched. Every timestamp is injected, so nothing here
depends on the wall clock.
"""
import json

import pytest

import foreman_lock as fl

NOW = "2026-08-01T12:00:00Z"


def _log_lines(flow_dir):
    path = flow_dir / "flow-log.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _actions(flow_dir):
    return [entry["action"] for entry in _log_lines(flow_dir)]


# --- is_stale, pure ---------------------------------------------------------

def test_is_stale_is_false_below_the_ttl():
    holder = {"acquired_at": "2026-08-01T11:52:00Z"}   # 480s old
    assert fl.is_stale(holder, NOW, ttl_seconds=900) is False


def test_is_stale_is_true_at_exactly_the_ttl():
    holder = {"acquired_at": "2026-08-01T11:45:00Z"}   # 900s old
    assert fl.is_stale(holder, NOW, ttl_seconds=900) is True


def test_is_stale_is_true_above_the_ttl():
    holder = {"acquired_at": "2026-08-01T11:00:00Z"}   # 3600s old
    assert fl.is_stale(holder, NOW, ttl_seconds=900) is True


def test_a_holder_with_no_usable_timestamp_is_stale():
    assert fl.is_stale(None, NOW) is True
    assert fl.is_stale({}, NOW) is True
    assert fl.is_stale({"acquired_at": "yesterday"}, NOW) is True


def test_default_ttl_is_fifteen_minutes():
    assert fl.DEFAULT_TTL_SECONDS == 900


def test_age_seconds_is_pure_arithmetic():
    assert fl.age_seconds("2026-08-01T11:59:00Z", NOW) == 60
    assert fl.age_seconds("nonsense", NOW) is None


# --- acquire and release ----------------------------------------------------

def test_acquire_writes_the_holder_and_release_removes_it(tmp_path):
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=4242, host="laptop") as holder:
        path = fl.lock_path(tmp_path)
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == {"pid": 4242, "host": "laptop",
                           "session_id": "sess-1", "acquired_at": NOW}
        assert holder == on_disk
    assert not fl.lock_path(tmp_path).exists()


def test_acquire_defaults_pid_and_host_to_this_process(tmp_path):
    import os
    with fl.acquire(tmp_path, "sess-1", now=NOW) as holder:
        assert holder["pid"] == os.getpid()
        assert isinstance(holder["host"], str) and holder["host"]


def test_a_clean_acquire_writes_no_receipt(tmp_path):
    with fl.acquire(tmp_path, "sess-1", now=NOW):
        pass
    assert _actions(tmp_path) == []


# --- contention -------------------------------------------------------------

def test_contention_raises_lock_busy_and_leaves_the_holder_intact(tmp_path):
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1):
        with pytest.raises(fl.LockBusy) as excinfo:
            with fl.acquire(tmp_path, "sess-2", now=NOW, pid=2):
                raise AssertionError("the second acquire must not enter")
        assert excinfo.value.holder["session_id"] == "sess-1"
        on_disk = json.loads(fl.lock_path(tmp_path).read_text(encoding="utf-8"))
        assert on_disk["session_id"] == "sess-1"
    assert not fl.lock_path(tmp_path).exists()


def test_the_loser_writes_a_lock_busy_receipt(tmp_path):
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1):
        with pytest.raises(fl.LockBusy):
            with fl.acquire(tmp_path, "sess-2", now=NOW, pid=2):
                pass
    assert _actions(tmp_path) == ["lock_busy"]
    entry = _log_lines(tmp_path)[0]
    assert entry["outcome"] == "blocked"
    assert entry["actor"] == "foreman"
    assert "sess-1" in entry["detail"]


# --- stale breaking ---------------------------------------------------------

def _plant_stale_lock(tmp_path, acquired_at="2026-08-01T11:00:00Z"):
    fl.lock_path(tmp_path).write_text(json.dumps(
        {"pid": 999, "host": "other", "session_id": "ghost",
         "acquired_at": acquired_at}) + "\n", encoding="utf-8")


def test_a_stale_lock_is_broken_and_reacquired(tmp_path):
    _plant_stale_lock(tmp_path)
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1, ttl_seconds=900):
        on_disk = json.loads(fl.lock_path(tmp_path).read_text(encoding="utf-8"))
        assert on_disk["session_id"] == "sess-1"
    assert not fl.lock_path(tmp_path).exists()


def test_breaking_a_stale_lock_writes_a_lock_broken_receipt(tmp_path):
    _plant_stale_lock(tmp_path)
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1, ttl_seconds=900):
        pass
    assert _actions(tmp_path) == ["lock_broken"]
    entry = _log_lines(tmp_path)[0]
    assert entry["actor"] == "foreman"
    assert "ghost" in entry["detail"]
    assert "3600" in entry["detail"]


def test_a_fresh_lock_is_not_broken(tmp_path):
    _plant_stale_lock(tmp_path, acquired_at="2026-08-01T11:59:00Z")
    with pytest.raises(fl.LockBusy):
        with fl.acquire(tmp_path, "sess-1", now=NOW, ttl_seconds=900):
            pass


def test_a_corrupt_lockfile_is_treated_as_stale(tmp_path):
    fl.lock_path(tmp_path).write_text("{not json", encoding="utf-8")
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1):
        on_disk = json.loads(fl.lock_path(tmp_path).read_text(encoding="utf-8"))
        assert on_disk["session_id"] == "sess-1"
    assert _actions(tmp_path) == ["lock_broken"]


def test_ttl_is_configurable(tmp_path):
    _plant_stale_lock(tmp_path, acquired_at="2026-08-01T11:59:00Z")  # 60s old
    with fl.acquire(tmp_path, "sess-1", now=NOW, ttl_seconds=30):
        pass  # 60 >= 30, so it breaks


# --- release discipline -----------------------------------------------------

def test_the_lock_is_released_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with fl.acquire(tmp_path, "sess-1", now=NOW):
            raise ValueError("boom")
    assert not fl.lock_path(tmp_path).exists()


def test_release_does_not_delete_a_lock_someone_else_now_holds(tmp_path):
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1):
        # Simulate another process breaking ours and taking the lock.
        fl.lock_path(tmp_path).write_text(json.dumps(
            {"pid": 2, "host": "other", "session_id": "sess-2",
             "acquired_at": NOW}) + "\n", encoding="utf-8")
    on_disk = json.loads(fl.lock_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk["session_id"] == "sess-2"


def test_release_on_an_absent_lockfile_is_false_not_an_error(tmp_path):
    assert fl.release(tmp_path, {"pid": 1, "session_id": "sess-1"}) is False


def test_release_does_not_delete_a_zero_length_lockfile_it_does_not_own(tmp_path):
    # Simulates the just-created-but-not-yet-written window: another writer's
    # os.open has landed but its os.fdopen write has not completed yet, so
    # read_lock sees an empty (unparseable) file and returns None. That must
    # never be treated as this caller's own lock to reclaim and delete.
    path = fl.lock_path(tmp_path)
    path.write_text("", encoding="utf-8")
    assert fl.read_lock(tmp_path) is None
    assert fl.release(tmp_path, {"pid": 1, "session_id": "sess-1"}) is False
    assert path.exists()


def test_release_does_not_delete_a_corrupt_lockfile_it_does_not_own(tmp_path):
    path = fl.lock_path(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert fl.release(tmp_path, {"pid": 1, "session_id": "sess-1"}) is False
    assert path.exists()


def test_a_non_utf8_lockfile_is_treated_as_corrupt(tmp_path):
    # read_text(encoding="utf-8") raises UnicodeDecodeError on non-UTF-8 bytes,
    # which used to escape both the OSError and the ValueError handler and
    # propagate out of read_lock, wedging the flow exactly as the module's
    # docstring says a corrupt lockfile must not.
    path = fl.lock_path(tmp_path)
    path.write_bytes(b"\xff\xfe\x00\x01")
    assert fl.read_lock(tmp_path) is None
    assert fl.is_stale(fl.read_lock(tmp_path), NOW) is True
    assert fl.release(tmp_path, {"pid": 1, "session_id": "sess-1"}) is False
    assert path.exists()


def test_logging_can_be_switched_off(tmp_path):
    _plant_stale_lock(tmp_path)
    with fl.acquire(tmp_path, "sess-1", now=NOW, pid=1, log=False):
        pass
    assert _actions(tmp_path) == []
