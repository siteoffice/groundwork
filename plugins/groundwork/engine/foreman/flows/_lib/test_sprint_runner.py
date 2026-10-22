"""Tests for sprint_runner.py. Plain pytest, stdlib only, tmp_path only.

Matches the existing flows/_lib suites: module-level test functions, monkeypatch
for seams, tmp_path for every filesystem fixture. Every timestamp is injected,
so nothing here depends on the wall clock, and subprocess.run is mocked in
EVERY case: this suite never starts a real claude process.
"""
import json

import pytest

import sprint_runner as sr

NOW = "2026-08-24T16:32:20Z"

GOOD_SPEC = ("C:\\work\\10_holding\\05_docs\\superpowers\\specs\\"
             "2026-08-24-foreman-headless-sprint-runner-design.md")
GOOD_MAP = ("spec=opus,plan=opus,dev=sonnet,test=haiku,verify=opus,"
            "iterate=sonnet,report=haiku")
GOOD_LINE = "/sprint ZeroNonsense.dev {} --model-map {}".format(GOOD_SPEC, GOOD_MAP)


def test_build_argv_is_exactly_three_tokens_and_never_a_shell_string():
    assert sr.build_argv(GOOD_LINE) == [sr.CLAUDE_EXE, "-p", GOOD_LINE]


def test_build_argv_does_not_split_or_quote_the_line():
    """The whole /sprint line is ONE argv element. Splitting it on spaces would
    turn a Windows spec path into several arguments."""
    argv = sr.build_argv(GOOD_LINE)
    assert len(argv) == 3
    assert GOOD_SPEC in argv[2]


def test_make_session_id_is_visually_distinct_from_an_interactive_one():
    """An interactive launch records session_id 'foreman-...'; a headless run
    must be greppable apart from it in receipts."""
    sid = sr.make_session_id(NOW)
    assert sid.startswith("runner-")
    assert not sid.startswith("foreman-")
    assert "20260824T163220Z" in sid


def test_make_session_id_strips_colons_and_dashes_from_the_stamp():
    stamp = sr.make_session_id(NOW, suffix="abc123")
    assert stamp == "runner-20260824T163220Z-abc123"


def test_make_session_id_is_unique_across_calls_at_the_same_instant():
    """Two poll cycles in the same second must not collide on the log filename."""
    assert sr.make_session_id(NOW) != sr.make_session_id(NOW)


def test_module_constants():
    assert sr.RECEIPT_ACTION == "driver_headless_run"
    assert sr.DEFAULT_TTL_SECONDS == 7200
    assert sr.RUNNER_LOG_DIR.name == "_runner_logs"


# --- find_candidates ---------------------------------------------------------

def _row(**over):
    """One FlowStatus with sane sprint_await defaults. FlowStatus is a frozen
    dataclass with every field required, so an overrides helper keeps each test
    to the one or two fields it actually cares about."""
    base = dict(
        project="ZeroNonsense.dev", slug="alpha",
        flow_dir="C:\\work\\10_holding\\flows\\ZeroNonsense.dev\\alpha",
        stage="execute", status="running", step_index=1, step_of=3,
        step_title="build it", phase="sprint", gates=(), sprint_slug="",
        sprint_status="", iteration=None, max_iterations=None,
        test_outcome="", verify_outcome="", cost_usd=None,
        kind="sprint_await", why="", blocking="agent", command=GOOD_LINE,
        gate_id=None, guidance_key="sprint_await",
        blocked_since="2026-08-24T16:32:20Z", blocked_seconds=None,
        chain_depth=None, chain_cap=None, guide="coached", rung=None,
        updated_at="2026-08-24T16:00:00Z", error="")
    base.update(over)
    return sr.foreman_status.FlowStatus(**base)


def _patch_collect(monkeypatch, rows):
    monkeypatch.setattr(sr.foreman_status, "collect",
                        lambda flows_root, sprints_root, now=None: list(rows))


def test_find_candidates_on_an_empty_flows_root_returns_nothing(monkeypatch):
    _patch_collect(monkeypatch, [])
    assert sr.find_candidates("flows", "sprints", now=NOW) == []


def test_find_candidates_filters_by_kind_and_command(monkeypatch):
    """Table-driven: one row per exclusion rule in the spec."""
    cases = [
        ("wrong kind is excluded", _row(kind="sprint_launch"), False),
        ("wrong kind with a command is still excluded",
         _row(kind="gate_await", command=GOOD_LINE), False),
        ("sprint_await with an empty command is excluded",
         _row(command=""), False),
        ("sprint_await with a command is included", _row(), True),
    ]
    for label, row, expected in cases:
        _patch_collect(monkeypatch, [row])
        got = bool(sr.find_candidates("flows", "sprints", now=NOW))
        assert got is expected, label


def test_find_candidates_sorts_oldest_first_by_blocked_since(monkeypatch):
    newest = _row(slug="newest", blocked_since="2026-08-24T18:00:00Z")
    oldest = _row(slug="oldest", blocked_since="2026-08-24T09:00:00Z")
    middle = _row(slug="middle", blocked_since="2026-08-24T12:00:00Z")
    _patch_collect(monkeypatch, [newest, oldest, middle])
    got = [r.slug for r in sr.find_candidates("flows", "sprints", now=NOW)]
    assert got == ["oldest", "middle", "newest"]


def test_find_candidates_falls_back_to_updated_at_when_blocked_since_is_empty(monkeypatch):
    """blocked_since is '' when unknown. Sorting on '' would float that row to
    the front and starve every genuinely older one, so updated_at stands in."""
    no_since = _row(slug="nosince", blocked_since="",
                    updated_at="2026-08-24T20:00:00Z")
    older = _row(slug="older", blocked_since="2026-08-24T10:00:00Z")
    _patch_collect(monkeypatch, [no_since, older])
    got = [r.slug for r in sr.find_candidates("flows", "sprints", now=NOW)]
    assert got == ["older", "nosince"]


# --- run_once, the lock ------------------------------------------------------

def _log_entries(flow_dir):
    path = flow_dir / "flow-log.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _no_subprocess(monkeypatch):
    """Fail loudly if anything reaches subprocess.run. Every test in this file
    installs this or an explicit recorder: no real claude process, ever."""
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called: {!r}".format(args))
    monkeypatch.setattr(sr.subprocess, "run", _boom)


def test_run_once_returns_skipped_busy_when_the_lock_is_held(monkeypatch, tmp_path):
    _no_subprocess(monkeypatch)
    calls = []

    def _busy(*args, **kwargs):
        calls.append(kwargs)
        raise sr.foreman_lock.LockBusy({"session_id": "foreman-other", "pid": 4242})

    monkeypatch.setattr(sr.foreman_lock, "acquire", _busy)
    got = sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                      GOOD_SPEC, tmp_path, now=NOW)
    assert got == "skipped_busy"
    assert len(calls) == 1, "a busy lock is picked up next cycle, never retried here"


def test_run_once_passes_the_two_hour_ttl_and_the_stage_to_the_lock(monkeypatch, tmp_path):
    _no_subprocess(monkeypatch)
    seen = {}

    def _busy(flow_dir, session_id, **kwargs):
        seen["session_id"] = session_id
        seen.update(kwargs)
        raise sr.foreman_lock.LockBusy({})

    monkeypatch.setattr(sr.foreman_lock, "acquire", _busy)
    sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE, GOOD_SPEC,
                tmp_path, now=NOW)
    assert seen["ttl_seconds"] == 7200
    assert seen["stage"] == "sprint_await"
    assert seen["now"] == NOW
    assert seen["session_id"].startswith("runner-")


def test_a_real_busy_lockfile_produces_skipped_busy_without_running(monkeypatch, tmp_path):
    """End to end through the real foreman_lock, not a mocked acquire: a fresh
    holder file on disk must be enough to stop this poll cycle dead."""
    _no_subprocess(monkeypatch)
    (tmp_path / "foreman.lock").write_text(json.dumps(
        {"pid": 4242, "host": "elsewhere", "session_id": "foreman-live",
         "acquired_at": NOW}) + "\n", encoding="utf-8")
    got = sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                      GOOD_SPEC, tmp_path, now=NOW)
    assert got == "skipped_busy"
    assert (tmp_path / "foreman.lock").exists(), "the other holder's lock survives"


# --- run_once, the re-checks inside the lock ---------------------------------

def test_run_once_skips_a_spec_whose_sprint_already_finished(monkeypatch, tmp_path):
    """Someone ran it interactively between authorisation and this poll."""
    _no_subprocess(monkeypatch)
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])
    monkeypatch.setattr(sr.ts, "has_terminal_sprint",
                        lambda project, spec_path, sprints: {
                            "sprint_id": "sp-20260824-abc", "status": "passing"})
    got = sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                      GOOD_SPEC, tmp_path, now=NOW)
    assert got == "skipped_terminal"
    entries = _log_entries(tmp_path)
    assert [e["action"] for e in entries] == ["driver_headless_run"]
    assert "sp-20260824-abc" in entries[0]["detail"], "the matched sprint id is logged"
    assert entries[0]["actor"] == "sprint_runner"
    assert entries[0]["ref"].startswith("runner-")


def test_run_once_refuses_a_malformed_line_before_it_can_run(monkeypatch, tmp_path):
    """Table-driven, one row per way foreman.json could have been mutated
    between authorisation and this poll. validate_launch_line is called for
    real here: it is pure, needs no disk, and is already tested in
    test_foreman_run.py, so this asserts the WIRING, not the rules."""
    _no_subprocess(monkeypatch)
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])
    monkeypatch.setattr(sr.ts, "has_terminal_sprint",
                        lambda project, spec_path, sprints: None)
    cases = [
        ("no --model-map",
         "/sprint ZeroNonsense.dev " + GOOD_SPEC, GOOD_SPEC, "--model-map"),
        ("wrong prefix",
         "sprint ZeroNonsense.dev " + GOOD_SPEC + " --model-map " + GOOD_MAP,
         GOOD_SPEC, "/sprint "),
        ("spec outside C:\\work", GOOD_LINE, "D:\\elsewhere\\specs\\thing.md",
         "D:\\elsewhere"),
    ]
    for label, line, spec, needle in cases:
        flow_dir = tmp_path / label.replace(" ", "_").replace("\\", "_").replace(":", "")
        flow_dir.mkdir()
        got = sr.run_once(flow_dir, "ZeroNonsense.dev", "alpha", line, spec,
                          tmp_path, now=NOW)
        assert got == "skipped_malformed", label
        entries = _log_entries(flow_dir)
        assert len(entries) == 1, label
        assert needle in entries[0]["detail"], "{}: {!r}".format(
            label, entries[0]["detail"])


def test_the_terminal_check_runs_before_the_shape_check(monkeypatch, tmp_path):
    """Order matters for the receipt: 'someone already ran it' is the more
    useful sentence than 'the line looks wrong' when both are true."""
    _no_subprocess(monkeypatch)
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])
    monkeypatch.setattr(sr.ts, "has_terminal_sprint",
                        lambda project, spec_path, sprints: {
                            "sprint_id": "sp-1", "status": "done"})
    got = sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha",
                      "/sprint no-model-map", GOOD_SPEC, tmp_path, now=NOW)
    assert got == "skipped_terminal"


def test_the_re_checks_happen_inside_the_lock(monkeypatch, tmp_path):
    """Not merely before subprocess.run: a check outside the lock could race a
    second writer between the check and the launch."""
    order = []
    real_acquire = sr.foreman_lock.acquire

    def _traced(*args, **kwargs):
        order.append("lock")
        return real_acquire(*args, **kwargs)

    monkeypatch.setattr(sr.foreman_lock, "acquire", _traced)
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])

    def _checked(project, spec_path, sprints):
        order.append("terminal_check")
        return {"sprint_id": "sp-1", "status": "done"}

    monkeypatch.setattr(sr.ts, "has_terminal_sprint", _checked)
    _no_subprocess(monkeypatch)
    sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE, GOOD_SPEC,
                tmp_path, now=NOW)
    assert order == ["lock", "terminal_check"]
    assert not (tmp_path / "foreman.lock").exists(), "the lock is released"


# --- run_once, the happy path ------------------------------------------------

class _Completed:
    def __init__(self, returncode=0, stdout="sprint finished", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _clear_guards(monkeypatch, log_dir):
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])
    monkeypatch.setattr(sr.ts, "has_terminal_sprint",
                        lambda project, spec_path, sprints: None)
    monkeypatch.setattr(sr, "RUNNER_LOG_DIR", log_dir)


def test_run_once_shells_out_with_exactly_the_authorised_line(monkeypatch, tmp_path):
    _clear_guards(monkeypatch, tmp_path / "_runner_logs")
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(sr.subprocess, "run", _fake_run)
    got = sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                      GOOD_SPEC, tmp_path, now=NOW)
    assert got == "ran"
    assert seen["argv"] == [sr.CLAUDE_EXE, "-p", GOOD_LINE]
    assert "shell" not in seen["kwargs"], "shell=True is never passed"
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["text"] is True


def test_run_once_writes_a_log_file_named_for_the_session(monkeypatch, tmp_path):
    log_dir = tmp_path / "_runner_logs"
    _clear_guards(monkeypatch, log_dir)
    monkeypatch.setattr(sr.subprocess, "run",
                        lambda argv, **kw: _Completed(stdout="OUT", stderr="ERR"))
    sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE, GOOD_SPEC,
                tmp_path, now=NOW)
    written = list(log_dir.glob("runner-*.log"))
    assert len(written) == 1, "the directory is created and one log lands in it"
    body = written[0].read_text(encoding="utf-8")
    assert "OUT" in body and "ERR" in body, "stdout and stderr are both kept"
    assert "ZeroNonsense.dev/alpha" in body, "the flow is identified in the log"


def test_the_receipt_detail_points_at_the_log_that_was_written(monkeypatch, tmp_path):
    log_dir = tmp_path / "_runner_logs"
    _clear_guards(monkeypatch, log_dir)
    monkeypatch.setattr(sr.subprocess, "run", lambda argv, **kw: _Completed())
    sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE, GOOD_SPEC,
                tmp_path, now=NOW)
    entries = _log_entries(tmp_path)
    assert [e["action"] for e in entries] == ["driver_headless_run"]
    entry = entries[0]
    assert entry["actor"] == "sprint_runner"
    assert entry["detail"].startswith("ran ")
    written = list(log_dir.glob("runner-*.log"))[0]
    assert written.name in entry["detail"], "the receipt names the log file"
    assert entry["ref"] == written.stem, "ref is the session id"


def test_the_lock_is_released_even_when_the_subprocess_raises(monkeypatch, tmp_path):
    """The one failure mode that would wedge a flow for two hours."""
    _clear_guards(monkeypatch, tmp_path / "_runner_logs")

    def _explode(argv, **kwargs):
        raise OSError("claude is not on PATH")

    monkeypatch.setattr(sr.subprocess, "run", _explode)
    with pytest.raises(OSError):
        sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                    GOOD_SPEC, tmp_path, now=NOW)
    assert sr.foreman_lock.read_lock(tmp_path) is None


def test_a_nonzero_return_code_is_still_a_run(monkeypatch, tmp_path):
    """This module reports that it pressed enter, not whether the sprint
    passed. The sprint's own terminal status is the sweep's business."""
    log_dir = tmp_path / "_runner_logs"
    _clear_guards(monkeypatch, log_dir)
    monkeypatch.setattr(sr.subprocess, "run",
                        lambda argv, **kw: _Completed(returncode=3, stderr="boom"))
    assert sr.run_once(tmp_path, "ZeroNonsense.dev", "alpha", GOOD_LINE,
                       GOOD_SPEC, tmp_path, now=NOW) == "ran"
    body = list(log_dir.glob("runner-*.log"))[0].read_text(encoding="utf-8")
    assert "returncode=3" in body


# --- the poll-once CLI -------------------------------------------------------

def test_poll_once_with_no_candidates_writes_nothing_and_exits_zero(monkeypatch, tmp_path, capsys):
    _no_subprocess(monkeypatch)
    _patch_collect(monkeypatch, [])
    assert sr.main(["poll-once", "--flows-root", str(tmp_path),
                    "--sprints-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "", "no noise for the common case"
    assert _log_entries(tmp_path) == []


def test_poll_once_runs_only_the_single_oldest_candidate(monkeypatch, tmp_path):
    """Several eligible flows at once is explicitly one run per invocation."""
    calls = []
    oldest_dir = tmp_path / "oldest"
    newest_dir = tmp_path / "newest"
    oldest_dir.mkdir()
    newest_dir.mkdir()
    _patch_collect(monkeypatch, [
        _row(slug="newest", flow_dir=str(newest_dir),
             blocked_since="2026-08-24T18:00:00Z"),
        _row(slug="oldest", flow_dir=str(oldest_dir),
             blocked_since="2026-08-24T09:00:00Z"),
    ])
    monkeypatch.setattr(sr.foreman_run, "load_driver",
                        lambda flow_dir, halt=None: {
                            "launch": {"line": GOOD_LINE, "spec_path": GOOD_SPEC}})
    _clear_guards(monkeypatch, tmp_path / "_runner_logs")

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        return _Completed()

    monkeypatch.setattr(sr.subprocess, "run", _fake_run)
    assert sr.poll_once(str(tmp_path), str(tmp_path), now=NOW) == "ran"
    assert len(calls) == 1, "exactly one candidate runs per invocation"
    assert _log_entries(oldest_dir), "the oldest flow got the receipt"
    assert _log_entries(newest_dir) == [], "the newer one is left for next cycle"


def test_poll_once_reads_the_spec_path_from_the_driver_record(monkeypatch, tmp_path):
    """FlowStatus carries no spec_path; foreman.json's driver.launch does, and
    it is the same value execute_launch validated at authorisation time."""
    seen = {}
    _patch_collect(monkeypatch, [_row(flow_dir=str(tmp_path))])
    monkeypatch.setattr(sr.foreman_run, "load_driver",
                        lambda flow_dir, halt=None: {
                            "launch": {"line": GOOD_LINE, "spec_path": GOOD_SPEC}})
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])

    def _check(project, spec_path, sprints):
        seen["spec_path"] = spec_path
        return None

    monkeypatch.setattr(sr.ts, "has_terminal_sprint", _check)
    monkeypatch.setattr(sr, "RUNNER_LOG_DIR", tmp_path / "_runner_logs")
    monkeypatch.setattr(sr.subprocess, "run", lambda argv, **kw: _Completed())
    sr.poll_once(str(tmp_path), str(tmp_path), now=NOW)
    assert seen["spec_path"] == GOOD_SPEC


def test_a_missing_driver_record_is_malformed_not_a_crash(monkeypatch, tmp_path):
    """foreman.json is advisory and deletable. A candidate with no recorded
    spec_path must refuse, not launch on a guessed one."""
    _no_subprocess(monkeypatch)
    _patch_collect(monkeypatch, [_row(flow_dir=str(tmp_path))])
    monkeypatch.setattr(sr.foreman_run, "load_driver",
                        lambda flow_dir, halt=None: {})
    monkeypatch.setattr(sr.ts, "discover_sprints", lambda root: [])
    monkeypatch.setattr(sr.ts, "has_terminal_sprint",
                        lambda project, spec_path, sprints: None)
    assert sr.poll_once(str(tmp_path), str(tmp_path), now=NOW) == "skipped_malformed"


def test_main_exits_zero_for_every_skip_outcome(monkeypatch, tmp_path):
    """LastTaskResult is how the Scheduled Task's health is read, and 'nothing
    to do' or 'busy' are healthy outcomes, not failures."""
    for outcome in ("ran", "skipped_busy", "skipped_terminal",
                    "skipped_malformed", "no_candidates"):
        monkeypatch.setattr(sr, "poll_once",
                            lambda flows_root, sprints_root, now=None,
                            _o=outcome: _o)
        assert sr.main(["poll-once", "--flows-root", str(tmp_path),
                        "--sprints-root", str(tmp_path)]) == 0, outcome
