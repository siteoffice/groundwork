"""Tests for foreman_run.py. Plain pytest, stdlib only, hermetic.

The pure section is tested with literal fixtures and injected facts; the host
section is tested over tmp_path trees built with flow_transition, the same way
test_foreman_status.py and test_render_flows.py build theirs.
"""
import json
import pathlib

import flow_transition as ft
import foreman_plan as fp
import foreman_run as fr

NOW = "2026-08-02T12:00:00Z"


# --- Task 1: the driver record ----------------------------------------------

def test_default_driver_carries_schema_version_one_and_the_spec_fields():
    d = fr.default_driver(session_id="s1", now=NOW)
    assert d["schema_version"] == 1
    for key in ("guide", "rung", "chain_depth", "chain_cap", "session_id",
                "last_tick", "paused_reason", "preflight", "budget_usd"):
        assert key in d, key
    assert d["chain_cap"] == fp.DEFAULT_CHAIN_CAP
    assert d["chain_depth"] == 0
    assert d["guide"] == "coached"
    assert d["session_id"] == "s1"


def test_load_driver_reconstructs_defaults_when_the_file_is_absent(tmp_path):
    d = fr.load_driver(tmp_path, halt=False)
    assert d["chain_cap"] == fp.DEFAULT_CHAIN_CAP
    assert d["paused_reason"] == ""
    assert d["halt"] is False


def test_load_driver_reconstructs_defaults_when_the_file_is_malformed(tmp_path):
    (tmp_path / "foreman.json").write_text("{not json", encoding="utf-8")
    assert fr.load_driver(tmp_path, halt=False)["chain_cap"] == fp.DEFAULT_CHAIN_CAP


def test_load_driver_does_not_overwrite_a_recorded_value_with_a_default(tmp_path):
    (tmp_path / "foreman.json").write_text(
        json.dumps({"chain_cap": 3, "guide": "terse"}), encoding="utf-8")
    d = fr.load_driver(tmp_path, halt=False)
    assert d["chain_cap"] == 3
    assert d["guide"] == "terse"
    assert d["chain_depth"] == 0          # still defaulted


def test_write_driver_round_trips(tmp_path):
    d = fr.default_driver(session_id="s1", now=NOW)
    d["chain_depth"] = 4
    fr.write_driver(tmp_path, d)
    assert fr.load_driver(tmp_path, halt=False)["chain_depth"] == 4


def test_write_driver_never_persists_the_derived_halt_flag(tmp_path):
    d = fr.default_driver(now=NOW)
    d["halt"] = True
    fr.write_driver(tmp_path, d)
    raw = json.loads((tmp_path / "foreman.json").read_text(encoding="utf-8"))
    assert "halt" not in raw


def test_write_driver_preserves_keys_written_by_other_slices(tmp_path):
    """foreman_plan already reads driver['shipped'] (slice c) and
    driver['tail'] (slice g). This sprint writes neither and must not eat them."""
    (tmp_path / "foreman.json").write_text(
        json.dumps({"shipped": True, "tail": {"brief_written": True}}),
        encoding="utf-8")
    d = fr.load_driver(tmp_path, halt=False)
    d["chain_depth"] = 1
    fr.write_driver(tmp_path, d)
    raw = json.loads((tmp_path / "foreman.json").read_text(encoding="utf-8"))
    assert raw["shipped"] is True
    assert raw["tail"] == {"brief_written": True}


def test_write_driver_leaves_no_temp_file_behind(tmp_path):
    fr.write_driver(tmp_path, fr.default_driver(now=NOW))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["foreman.json"]


def test_write_driver_is_collision_safe_under_concurrent_writers(tmp_path):
    """Reproduces the original bug: write_driver wrote to a FIXED-NAME sibling
    temp file (foreman.json.tmp), so concurrent writers to the same flow_dir
    raced for that one temp path and os.replace raised PermissionError on
    Windows. A pool of threads hammering write_driver on the same flow_dir
    must raise nothing and leave a valid, parseable record behind."""
    import concurrent.futures

    errors = []

    def _write(i):
        try:
            driver = fr.default_driver(session_id="writer-{}".format(i), now=NOW)
            driver["chain_depth"] = i
            fr.write_driver(tmp_path, driver)
        except Exception as exc:  # noqa: BLE001 - captured, not raised, for the assert
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write, range(320)))

    assert errors == []
    final = fr.load_driver(tmp_path, halt=False)
    assert isinstance(final["chain_depth"], int)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["foreman.json"]


def test_write_driver_cleans_up_its_temp_file_when_retries_are_exhausted(
        tmp_path, monkeypatch):
    """Unlike the old fixed temp name (a stale `.tmp` was harmlessly
    overwritten by the next writer), a unique-per-call temp name left behind
    on a raise litters flow_dir forever: nothing globs `*.tmp` today. On
    retry exhaustion write_driver must unlink its own temp file before
    re-raising."""
    def _always_denied(*_args, **_kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(fr.os, "replace", _always_denied)
    monkeypatch.setattr(fr, "_REPLACE_RETRIES", 3)
    monkeypatch.setattr(fr, "_REPLACE_RETRY_SLEEP_SECONDS", 0)

    with pytest.raises(PermissionError):
        fr.write_driver(tmp_path, fr.default_driver(now=NOW))

    assert list(tmp_path.iterdir()) == []


def test_set_pause_records_the_reason_and_the_seam_then_halts(tmp_path):
    fr.set_pause(tmp_path, "waiting on the partner call", now=NOW)
    driver = fr.load_driver(tmp_path, halt=False)
    assert driver["paused_reason"] == "waiting on the partner call"
    action = fp.next_action({"stage": "idea", "status": "running"}, None, driver)
    assert action.kind == "halt"
    assert action.guidance_key == "halt.paused"


def test_clear_pause_empties_the_reason_and_the_seam_stops_halting(tmp_path):
    fr.set_pause(tmp_path, "hold", now=NOW)
    fr.clear_pause(tmp_path, now=NOW)
    driver = fr.load_driver(tmp_path, halt=False)
    assert driver["paused_reason"] == ""
    action = fp.next_action({"stage": "idea", "status": "running"}, None, driver)
    assert action.kind == "flow_advance"


def test_new_session_id_is_unique_per_call():
    assert fr.new_session_id(NOW) != fr.new_session_id(NOW)


# --- Task 2: the decision seam ----------------------------------------------

def _action(kind, blocking="", command="", gate_id=None, why="because"):
    return fp.NextAction(kind=kind, why=why, blocking=blocking, command=command,
                         gate_id=gate_id, step_index=0, role="none",
                         guidance_key=kind)


def test_every_action_kind_is_classified_exactly_once():
    """No kind may fall through unclassified: a new one added later must be
    put in a bucket deliberately, not silently executed."""
    buckets = (set(fr.EXECUTABLE_KINDS) | set(fr.NOT_YET_EXECUTABLE)
               | {"gate_open", "gate_await", "sprint_await", "halt", "flow_close"})
    assert buckets == set(fp.ACTION_KINDS)
    assert set(fr.EXECUTABLE_KINDS).isdisjoint(fr.NOT_YET_EXECUTABLE)


def test_flow_advance_executes():
    d = fr.decide(_action("flow_advance", command="/flow advance p/s"), fr.HostFacts())
    assert d.execute is True
    assert d.reason == ""


def test_gate_consume_executes():
    d = fr.decide(_action("gate_consume", command="/flow advance p/s", gate_id="G3"),
                  fr.HostFacts(), verdict="proceed")
    assert d.execute is True


def test_gate_consume_of_g1_approve_stops_because_it_needs_a_step_list():
    """flow_transition._consume_g1 raises ValueError without `steps`, and the
    step list is the breakdown judgment, not something the driver can derive."""
    d = fr.decide(_action("gate_consume", gate_id="G1"), fr.HostFacts(),
                  verdict="approve")
    assert d.execute is False
    assert d.reason == "needs_steps"


def test_gate_consume_of_g1_reject_still_executes():
    d = fr.decide(_action("gate_consume", gate_id="G1"), fr.HostFacts(),
                  verdict="reject")
    assert d.execute is True


def test_gate_open_stops_on_needs_judgment_at_every_gate():
    for gate_id in ("G1", "G2", "G3", "G4"):
        d = fr.decide(_action("gate_open", gate_id=gate_id), fr.HostFacts())
        assert d.execute is False, gate_id
        assert d.reason == "needs_judgment", gate_id


def test_flow_close_stops_as_complete_and_is_not_a_blocking_stop():
    d = fr.decide(_action("flow_close"), fr.HostFacts())
    assert (d.execute, d.reason) == (False, "complete")


def test_every_blocking_kind_stops():
    for kind, blocking in (("gate_await", "human"), ("sprint_await", "agent"),
                           ("halt", "fence")):
        d = fr.decide(_action(kind, blocking=blocking), fr.HostFacts())
        assert d.execute is False, kind
        assert d.reason == "blocking", kind


def test_every_not_yet_executable_kind_stops_with_that_reason():
    for kind in sorted(fr.NOT_YET_EXECUTABLE):
        d = fr.decide(_action(kind), fr.HostFacts())
        assert d.execute is False, kind
        assert d.reason == "not_executable", kind


def test_a_stopped_decision_keeps_the_command_line_for_copy_paste():
    line = "/sprint dsi C:\\work\\dsi\\docs\\spec.md --model-map dev=sonnet"
    d = fr.decide(_action("sprint_launch", command=line), fr.HostFacts())
    assert d.action.command == line


# --- the Cowork fence -------------------------------------------------------

def test_cowork_fence_converts_sprint_launch_and_ship_execute_to_emit_only():
    facts = fr.HostFacts(cowork=True)
    for kind in ("sprint_launch", "ship_execute"):
        d = fr.decide(_action(kind, command="X"), facts)
        assert d.execute is False, kind
        assert d.reason == "cowork", kind
        assert d.action.command == "X", kind


def test_cowork_fence_does_not_touch_a_flow_advance():
    d = fr.decide(_action("flow_advance"), fr.HostFacts(cowork=True))
    assert d.execute is True


def test_is_cowork_mount_says_yes_off_windows():
    assert fr.is_cowork_mount("/mnt/user-data/dev/ZeroNonsense.dev", "posix", {}) is True


def test_is_cowork_mount_says_no_on_the_windows_laptop():
    assert fr.is_cowork_mount(r"C:\work\10_holding", "nt", {}) is False


def test_is_cowork_mount_says_yes_for_a_non_drive_letter_root_on_windows():
    assert fr.is_cowork_mount("/srv/checkout", "nt", {}) is True


def test_is_cowork_mount_env_override_wins_in_both_directions():
    assert fr.is_cowork_mount(r"C:\work\x", "nt", {"FOREMAN_COWORK": "1"}) is True
    assert fr.is_cowork_mount("/mnt/x", "posix", {"FOREMAN_COWORK": "0"}) is False


# --- the WIP fence ----------------------------------------------------------

def _sprint(project="dsi", status="running", slug="s"):
    return {"project": project, "slug": slug, "status": status,
            "sprint_id": project + "__" + slug}


def test_wip_count_counts_only_non_terminal_sprints():
    sprints = [_sprint(status="running"), _sprint(status="passing", slug="b"),
               _sprint(status="failed", slug="c")]
    assert fr.wip_count("dsi", sprints) == 1


def test_wip_count_matches_the_project_case_insensitively():
    """The live tree carries both 'zerononsense-dev' and 'ZeroNonsense.dev'
    for the same repo; a case-sensitive count would let two sprints through."""
    assert fr.wip_count("dsi", [_sprint(project="DSI")]) == 1
    assert fr.wip_count("DSI", [_sprint(project="dsi")]) == 1
    assert fr.wip_count("dsi", [_sprint(project="jarvis")]) == 0


def test_wip_limit_blocks_a_second_sprint_in_the_same_repo():
    d = fr.decide(_action("sprint_launch"), fr.HostFacts(wip_count=1, wip_limit=1))
    assert (d.execute, d.reason) == (False, "wip_limit")


def test_wip_limit_does_not_block_when_the_repo_is_idle():
    """sprint_launch became executable in the 2026-08-16 step 5 spec (scope A),
    so an idle repo no longer stops here at all."""
    d = fr.decide(_action("sprint_launch"), fr.HostFacts(wip_count=0, wip_limit=1))
    assert (d.execute, d.reason) == (True, "")


# --- the budget fence -------------------------------------------------------

def test_budget_fence_trips_when_a_known_cost_reaches_the_ceiling():
    d = fr.decide(_action("flow_advance"),
                  fr.HostFacts(cost_usd=25.0, budget_usd=25.0))
    assert (d.execute, d.reason) == (False, "budget")


def test_budget_fence_does_not_trip_below_the_ceiling():
    d = fr.decide(_action("flow_advance"),
                  fr.HostFacts(cost_usd=24.99, budget_usd=25.0))
    assert d.execute is True


def test_an_unknown_cost_never_trips_the_budget_fence():
    d = fr.decide(_action("flow_advance"),
                  fr.HostFacts(cost_usd=None, budget_usd=1.0))
    assert d.execute is True


def test_no_ceiling_means_no_budget_fence():
    d = fr.decide(_action("flow_advance"),
                  fr.HostFacts(cost_usd=9999.0, budget_usd=None))
    assert d.execute is True


# --- precedence -------------------------------------------------------------

def test_fence_precedence_is_budget_then_cowork_then_wip():
    facts = fr.HostFacts(cowork=True, wip_count=5, wip_limit=1,
                         cost_usd=100.0, budget_usd=1.0)
    assert fr.decide(_action("sprint_launch"), facts).reason == "budget"
    facts = fr.HostFacts(cowork=True, wip_count=5, wip_limit=1)
    assert fr.decide(_action("sprint_launch"), facts).reason == "cowork"
    facts = fr.HostFacts(wip_count=5, wip_limit=1)
    assert fr.decide(_action("sprint_launch"), facts).reason == "wip_limit"


def test_a_blocking_action_outranks_every_host_fence():
    facts = fr.HostFacts(cowork=True, cost_usd=100.0, budget_usd=1.0)
    d = fr.decide(_action("halt", blocking="fence"), facts)
    assert d.reason == "blocking"


def test_guidance_key_is_the_seam_key_when_executing_and_a_stop_key_otherwise():
    assert fr.decide(_action("flow_advance"), fr.HostFacts()).guidance_key == "flow_advance"
    assert fr.decide(_action("flow_close"), fr.HostFacts()).guidance_key == "stop.complete"


def test_every_stop_reason_a_decision_can_produce_is_in_stop_reasons():
    for reason in ("complete", "blocking", "budget", "cowork", "wip_limit",
                   "needs_steps", "not_executable", "needs_judgment"):
        assert reason in fr.STOP_REASONS


def test_an_unrecognised_kind_never_executes_and_never_raises():
    """A kind foreman_plan does not know about (e.g. added by a future slice
    before this module's ladder is updated for it) must fail safe."""
    d = fr.decide(_action("some_future_kind_nobody_has_classified_yet"), fr.HostFacts())
    assert d.execute is False
    assert d.reason == "not_executable"


# --- Task 3: the executor, the lock and the receipt -------------------------

import foreman_lock as fl


def _tree(tmp_path, project="dsi", slug="payoff-books"):
    """A real flow directory built through flow_transition."""
    flows_root = tmp_path / "flows"
    flows_root.mkdir(exist_ok=True)
    flow_dir, _flow = ft.cmd_new(flows_root, project, slug + " idea",
                                 slug=slug, now="2026-08-02T09:00:00Z")
    return flows_root, flow_dir


def _log(flow_dir):
    path = pathlib.Path(flow_dir) / "flow-log.ndjson"
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_execute_crosses_the_boundary_through_the_single_write_path(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    action = fp.next_action(ft.load_flow(flow_dir), None, {})
    assert action.kind == "flow_advance"
    fr.execute(flow_dir, action, now=NOW)
    assert ft.load_flow(flow_dir)["stage"] == "design"


def test_record_receipt_writes_the_spec_fields(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    action = fp.next_action(ft.load_flow(flow_dir), None, {})
    fr.record_receipt(flow_dir, action, "idea", NOW)
    entry = _log(flow_dir)[-1]
    assert entry["actor"] == "foreman"
    assert entry["action"] == "driver_flow_advance"
    assert entry["detail"] == action.why[:240]
    assert entry["stage"] == "idea"


def test_execute_once_writes_both_the_transition_and_the_reasoning_receipt(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    action = fp.next_action(ft.load_flow(flow_dir), None, {})
    decision = fr.execute_once(flow_dir, action, "sess-1", now=NOW, stage="idea")
    assert decision.execute is True
    actions = [e["action"] for e in _log(flow_dir)]
    assert "advanced" in actions            # cmd_advance's own receipt
    assert "driver_flow_advance" in actions  # the driver's reasoning receipt


def test_execute_once_releases_the_lock(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    action = fp.next_action(ft.load_flow(flow_dir), None, {})
    fr.execute_once(flow_dir, action, "sess-1", now=NOW)
    assert not fl.lock_path(flow_dir).exists()


def test_execute_once_yields_to_a_live_lock_and_changes_nothing(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    action = fp.next_action(ft.load_flow(flow_dir), None, {})
    with fl.acquire(flow_dir, "other-session", now=NOW, pid=999):
        decision = fr.execute_once(flow_dir, action, "sess-1", now=NOW)
    assert decision.execute is False
    assert decision.reason == "lock_busy"
    assert ft.load_flow(flow_dir)["stage"] == "idea"     # untouched
    assert "driver_flow_advance" not in [e["action"] for e in _log(flow_dir)]


def test_execute_once_releases_the_lock_when_the_write_path_raises(tmp_path):
    """A ValueError out of advance() must not leave the flow wedged."""
    _flows_root, flow_dir = _tree(tmp_path)
    flow = ft.load_flow(flow_dir)
    flow["status"] = "paused"          # advance() raises 'cannot advance: paused'
    ft.write_flow(flow_dir, flow)
    action = _action("flow_advance", command="/flow advance dsi/payoff-books")
    decision = fr.execute_once(flow_dir, action, "sess-1", now=NOW)
    assert (decision.execute, decision.reason) == (False, "execute_error")
    assert not fl.lock_path(flow_dir).exists()


def test_record_fence_writes_one_receipt_and_dedupes_an_immediate_repeat(tmp_path):
    _flows_root, flow_dir = _tree(tmp_path)
    decision = fr.decide(_action("sprint_launch"), fr.HostFacts(cowork=True))
    assert fr.record_fence(flow_dir, decision, "execute", NOW) is True
    assert fr.record_fence(flow_dir, decision, "execute", NOW) is False
    fences = [e for e in _log(flow_dir) if e["action"] == "driver_fence"]
    assert len(fences) == 1
    assert fences[0]["outcome"] == "blocked"
    assert fences[0]["detail"].startswith("cowork:")


def test_record_fence_does_not_dedupe_a_genuinely_different_fence(tmp_path):
    """The dedupe rule is 'same reason, same detail', not 'any fence'; a
    second, different stop reason must still write its own receipt."""
    _flows_root, flow_dir = _tree(tmp_path)
    cowork_decision = fr.decide(_action("sprint_launch"), fr.HostFacts(cowork=True))
    wip_decision = fr.decide(_action("sprint_launch"),
                             fr.HostFacts(wip_count=1, wip_limit=1))
    assert fr.record_fence(flow_dir, cowork_decision, "execute", NOW) is True
    assert fr.record_fence(flow_dir, wip_decision, "execute", NOW) is True
    fences = [e for e in _log(flow_dir) if e["action"] == "driver_fence"]
    assert len(fences) == 2
    assert fences[0]["detail"].startswith("cowork:")
    assert fences[1]["detail"].startswith("wip_limit:")


def test_record_fence_dedupes_an_immediate_repeat_of_the_same_blocking_fence(
        tmp_path):
    """The reason == "blocking" branch (added to fold in guidance_key so the
    four halt-family fences stop colliding) must still dedupe an immediate
    repeat of the exact same fence, not just distinguish different ones; the
    two tests above only ever exercise the old cowork/wip_limit branch."""
    _flows_root, flow_dir = _tree(tmp_path)
    decision = fr.decide(_action("halt", blocking="fence"), fr.HostFacts())
    assert fr.record_fence(flow_dir, decision, "execute", NOW) is True
    assert fr.record_fence(flow_dir, decision, "execute", NOW) is False
    fences = [e for e in _log(flow_dir) if e["action"] == "driver_fence"]
    assert len(fences) == 1
    assert fences[0]["detail"].startswith("blocking: halt:")


def test_execute_once_maps_relaunch_guard_to_a_stop_decision_and_releases_the_lock(
        tmp_path):
    """cmd_advance_guarded persists flow.json and its own 'sprint_emitted' log
    line BEFORE it raises RelaunchGuardError (refuse-and-explain, never a
    rollback, per flow_transition.cmd_advance_guarded's own docstring):
    execute_once must still surface that as a stop Decision rather than let
    the exception escape, and must still release the lock afterward."""
    _flows_root, flow_dir = _tree(tmp_path)  # project="dsi", slug="payoff-books"
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 0, "of": 1, "phase": "grill"}
    flow["steps"] = [{"index": 0, "title": "build it", "status": "running"}]
    flow["gate"] = {"id": "G2", "step_index": 0, "status": "decided",
                    "decision": {"verdict": "approve", "note": "",
                                 "surface": "session", "decided_at": NOW,
                                 "message_ref": None},
                    "notified": True}
    ft.write_flow(flow_dir, flow)

    # The spec_path fallback flow_transition derives when none is recorded
    # (f"C:\\work\\{project}\\docs\\superpowers\\specs\\{slug}.md") is exactly
    # what the terminal sprint below must match on for has_terminal_sprint to
    # find it.
    spec_path = r"C:\work\dsi\docs\superpowers\specs\payoff-books.md"
    sprints_root = tmp_path / "sprints"
    sprint_dir = sprints_root / "dsi" / "payoff-books"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "sprint.json").write_text(json.dumps({
        "schema_version": 1, "sprint_id": "dsi__payoff-books__20260801T090000Z",
        "project": "dsi", "slug": "payoff-books", "spec_path": spec_path,
        "status": "passing", "created_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-01T09:00:00Z"}), encoding="utf-8")

    action = _action("gate_consume", gate_id="G2", command="/flow advance dsi/payoff-books")
    decision = fr.execute_once(flow_dir, action, "sess-1",
                              sprints_root=str(sprints_root), now=NOW)

    assert (decision.execute, decision.reason) == (False, "relaunch_guard")
    assert "dsi__payoff-books__20260801T090000Z" in decision.detail
    assert "passing" in decision.detail
    # cmd_advance's own write already happened before the raise: refuse and
    # explain, never a rollback.
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"
    assert "sprint_emitted" in [e["action"] for e in _log(flow_dir)]
    assert not fl.lock_path(flow_dir).exists()


# --- Task 4: the chain ------------------------------------------------------

import foreman_status as fst
import pytest


@pytest.fixture(autouse=True)
def _isolate_halt_file(tmp_path_factory, monkeypatch):
    """No test may read the real C:\\work\\10_holding\\flows\\_lib\\FOREMAN_HALT."""
    monkeypatch.setattr(fst, "HALT_FILE", tmp_path_factory.mktemp("halt") / "FOREMAN_HALT")


@pytest.fixture(autouse=True)
def _isolate_render_board(monkeypatch):
    """No test may let run_chain's render_board reach the real, hardcoded
    C:\\work\\10_holding\\foreman board: render_board's out_root defaults
    to _REPO_ROOT / "foreman" (derived from __file__, not tmp_path), so any
    render=True test that executes an action would otherwise overwrite the
    real foreman/index.html and index.json with tmp_path fixture data."""
    monkeypatch.setattr(fr, "render_board", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolate_brief_write_dev_root(tmp_path, monkeypatch):
    """No test may let execute_brief_write's real dev_root default write
    outside tmp_path. execute() never passes dev_root (Task 3), so any
    run_chain test whose flow reaches stage closed now genuinely executes
    brief_write, and roots.dev_root() with no override resolves to a real
    directory next to this repo. Confirmed by observation: without this
    fixture, test_the_chain_consumes_a_decided_gate_and_then_stops_at_the_next_gate
    wrote a real file to C:\\work\\10_holding\\.worktrees\\dsi\\04_assets."""
    monkeypatch.setattr(fr.roots, "dev_root", lambda *a, **k: tmp_path)


def _decided_gate_tree(tmp_path, gate_id="G3", verdict="proceed"):
    """A flow parked at `gate_id`, decided `verdict`, gate not yet consumed.
    This is the exact live defect the driver exists to close."""
    flows_root, flow_dir = _tree(tmp_path, slug="decided-gate")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "only step", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1,
                            "phase": "learn" if gate_id == "G3" else "grill"}
    ft.write_flow(flow_dir, flow)
    ft.persist_open_gate(flow_dir, ft.load_flow(flow_dir), gate_id, 0,
                         now="2026-08-02T10:00:00Z", nonce="7QF3")
    ft.cmd_gate(flow_dir, verdict, now="2026-08-02T10:05:00Z")
    return flows_root, flow_dir


def test_the_chain_consumes_a_decided_gate_and_then_stops_at_the_next_gate(
        tmp_path, monkeypatch):
    flows_root, flow_dir = _decided_gate_tree(tmp_path)
    monkeypatch.setattr(fr, "run_refresh_script", lambda s, a, c: (0, "ok"))
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False,
                          operator_path=tmp_path / "operator.json")
    # Task 3/4/5: brief_write, hub_ingest and hub_refresh are all executable
    # now, so a one-step flow that closes inside this same chain run runs its
    # whole tail to completion instead of stopping partway through it.
    # run_refresh_script is monkeypatched so the chain never shells into the
    # real command_center scripts.
    assert result.executed == ("gate_consume", "brief_write", "hub_ingest",
                               "hub_refresh")
    assert ft.load_flow(flow_dir)["stage"] == "closed"   # one-step flow, proceed closes it
    assert result.stopped.execute is False
    assert result.stopped.reason == "complete"


def test_the_chain_stops_on_a_gate_awaiting_a_human(tmp_path):
    """The first of the three blocking kinds. sprint_await (agent) is the next
    test and halt (fence) is covered by the halt-file and chain-cap tests."""
    for gate_id, phase in (("G3", "learn"), ("G2", "grill")):
        flows_root, flow_dir = _tree(tmp_path, slug="await-" + gate_id)
        flow = ft.load_flow(flow_dir)
        flow["stage"] = "execute"
        flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
        flow["current_step"] = {"index": 0, "of": 1, "phase": phase}
        ft.write_flow(flow_dir, flow)
        ft.persist_open_gate(flow_dir, ft.load_flow(flow_dir), gate_id, 0,
                             now=NOW, nonce="7QF3")
        result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                              session_id="s1", now=NOW, render=False)
        assert result.executed == (), gate_id
        assert result.stopped.reason == "blocking", gate_id
        assert result.stopped.action.kind == "gate_await", gate_id


def test_the_chain_stops_when_a_sprint_is_still_running(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="running-sprint")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    flow["spec_path"] = r"C:\work\dsi\docs\superpowers\specs\x.md"
    ft.write_flow(flow_dir, flow)
    ft.append_log(flow_dir, ft._log_entry(
        "2026-08-02T09:30:00Z", "execute", "orchestrator", "sprint_emitted",
        detail="/sprint dsi " + flow["spec_path"] + " --model-map dev=sonnet"))
    sprints_root = tmp_path / "sprints" / "dsi" / "s"
    sprints_root.mkdir(parents=True)
    (sprints_root / "sprint.json").write_text(json.dumps({
        "project": "dsi", "slug": "s", "spec_path": flow["spec_path"],
        "status": "running", "created_at": "2026-08-02T09:31:00Z",
        "worktree_path": r"C:\work\dsi\.worktrees\sprint-s"}), encoding="utf-8")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ()
    assert result.stopped.action.kind == "sprint_await"
    assert result.stopped.reason == "blocking"


def test_the_chain_cap_trips_at_exactly_the_cap(tmp_path):
    """A fresh flow can cross idea -> design -> breakdown. With a cap of 1,
    exactly one action executes and the halt names chain_cap."""
    flows_root, flow_dir = _tree(tmp_path, slug="capped")
    driver = fr.default_driver(now=NOW)
    driver["chain_cap"] = 1
    fr.write_driver(flow_dir, driver)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert len(result.executed) == 1
    assert result.stopped.action.guidance_key == "halt.chain_cap"
    assert fr.load_driver(flow_dir, halt=False)["chain_depth"] == 1


def test_the_chain_runs_the_gate_free_advances_back_to_back(tmp_path):
    """With the default cap, idea -> design -> breakdown both cross, then the
    chain stops at gate_open G1 (needs_judgment), not at the cap."""
    flows_root, flow_dir = _tree(tmp_path, slug="two-advances")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ("flow_advance", "flow_advance")
    assert ft.load_flow(flow_dir)["stage"] == "breakdown"
    assert result.stopped.reason == "needs_judgment"
    assert result.stopped.action.gate_id == "G1"


def test_foreman_halt_stops_before_any_execute(tmp_path, monkeypatch):
    flows_root, flow_dir = _tree(tmp_path, slug="halted")
    halt = tmp_path / "FOREMAN_HALT"
    halt.write_text("", encoding="utf-8")
    monkeypatch.setattr(fst, "HALT_FILE", halt)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ()
    assert result.stopped.action.guidance_key == "halt.halt_file"
    assert ft.load_flow(flow_dir)["stage"] == "idea"


def test_foreman_halt_dropped_mid_chain_stops_the_next_action(tmp_path, monkeypatch):
    """The halt file is re-stat'd every pass, not once per invocation."""
    flows_root, flow_dir = _tree(tmp_path, slug="mid-halt")
    halt = tmp_path / "FOREMAN_HALT"
    monkeypatch.setattr(fst, "HALT_FILE", halt)
    real_execute_once = fr.execute_once

    def _halting_execute_once(*args, **kwargs):
        out = real_execute_once(*args, **kwargs)
        halt.write_text("", encoding="utf-8")
        return out

    monkeypatch.setattr(fr, "execute_once", _halting_execute_once)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ("flow_advance",)
    assert result.stopped.action.guidance_key == "halt.halt_file"


def test_a_per_flow_pause_is_honoured_before_any_execute(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="paused")
    fr.set_pause(flow_dir, "waiting on the partner call", now=NOW)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ()
    assert result.stopped.action.guidance_key == "halt.paused"
    assert ft.load_flow(flow_dir)["stage"] == "idea"


def test_park_at_a_gate_pauses_the_flow_and_the_chain_honours_it(tmp_path):
    flows_root, flow_dir = _decided_gate_tree(tmp_path, "G3", "park")
    fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints", session_id="s1",
                 now=NOW, render=False)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ()
    assert result.stopped.action.guidance_key == "halt.flow_paused"


def test_record_fence_does_not_collapse_the_distinct_halt_family_fences(
        tmp_path, monkeypatch):
    """chain_cap, a per-flow pause and the FOREMAN_HALT kill switch all reach
    decide() as reason == "blocking" with the SAME generic detail text ("the
    next action blocks on fence..."). Tripping all three in sequence, across
    three separate run_chain invocations, must still produce three distinct
    driver_fence receipts rather than the first swallowing the other two."""
    flows_root, flow_dir = _tree(tmp_path, slug="fence-dedupe")

    driver = fr.default_driver(now=NOW)
    driver["chain_cap"] = 0
    fr.write_driver(flow_dir, driver)
    result_cap = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                              session_id="s1", now=NOW, render=False)
    assert result_cap.stopped.action.guidance_key == "halt.chain_cap"

    fr.set_pause(flow_dir, "waiting on the partner call", now=NOW)
    result_pause = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                                session_id="s1", now=NOW, render=False)
    assert result_pause.stopped.action.guidance_key == "halt.paused"

    halt_file = tmp_path / "FOREMAN_HALT"
    monkeypatch.setattr(fst, "HALT_FILE", halt_file)
    halt_file.write_text("", encoding="utf-8")
    result_halt = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                               session_id="s1", now=NOW, render=False)
    assert result_halt.stopped.action.guidance_key == "halt.halt_file"

    fences = [e for e in _log(flow_dir) if e["action"] == "driver_fence"]
    assert len(fences) == 3
    assert fences[0]["detail"].startswith("blocking: halt.chain_cap:")
    assert fences[1]["detail"].startswith("blocking: halt.paused:")
    assert fences[2]["detail"].startswith("blocking: halt.halt_file:")


def test_a_receipt_is_written_for_every_executed_action(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="receipted")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    driver_receipts = [e for e in _log(flow_dir)
                       if e["actor"] == "foreman" and e["action"].startswith("driver_")]
    assert len(driver_receipts) == len(result.executed) == result.receipts
    assert all(e["detail"] for e in driver_receipts)


def test_re_running_a_completed_chain_is_a_no_op(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="done")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "closed"
    flow["status"] = "closed"
    ft.write_flow(flow_dir, flow)
    driver = fr.default_driver(now=NOW)
    driver["tail"] = {"brief_written": True, "hub_ingested": True,
                      "hub_refreshed": True}
    fr.write_driver(flow_dir, driver)

    before = _log(flow_dir)
    op_path = tmp_path / "operator.json"
    first = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                         session_id="s1", now=NOW, render=False,
                         operator_path=op_path)
    second = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False,
                          operator_path=op_path)
    assert first.executed == second.executed == ()
    assert first.stopped.reason == second.stopped.reason == "complete"
    assert _log(flow_dir) == before          # not one byte written


def test_dry_run_computes_the_decision_and_executes_nothing(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="dry")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False, dry_run=True)
    assert result.executed == ()
    assert result.stopped.action.kind == "flow_advance"
    assert ft.load_flow(flow_dir)["stage"] == "idea"


def test_the_chain_re_renders_the_flow_dashboard_after_an_execute(tmp_path):
    # Siteoffice fold (sprint 1 step 12): render_flows now delegates to the
    # board renderer, writing under _board\flows\ instead of next to
    # flow.json / flows_root\index.html.
    flows_root, flow_dir = _tree(tmp_path, slug="rendered")
    fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints", session_id="s1",
                 now=NOW, render=True)
    flows_root = pathlib.Path(flows_root)
    flow_dir = pathlib.Path(flow_dir)
    rel = flow_dir.relative_to(flows_root)
    assert (flows_root.parent / "_board" / "flows" / rel / "index.html").exists()
    assert (flows_root.parent / "_board" / "flows" / "index.html").exists()


def test_a_render_failure_never_loses_a_chain(tmp_path, monkeypatch):
    flows_root, flow_dir = _tree(tmp_path, slug="render-boom")

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(fr, "render_after", _boom)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=True)
    assert result.executed == ("flow_advance", "flow_advance")


# --- Task 5: the budget fence wired -----------------------------------------

def test_the_chain_never_reads_burnrate_when_no_ceiling_is_set(tmp_path, monkeypatch):
    flows_root, flow_dir = _tree(tmp_path, slug="no-ceiling")
    calls = []
    monkeypatch.setattr(fr.fcost, "sprint_cost_usd",
                        lambda *a, **k: calls.append(1))
    fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints", session_id="s1",
                 now=NOW, render=False)
    assert calls == []


def test_the_chain_halts_when_the_ceiling_is_reached(tmp_path, monkeypatch):
    flows_root, flow_dir = _tree(tmp_path, slug="over-budget")
    driver = fr.default_driver(now=NOW)
    driver["budget_usd"] = 5.0
    fr.write_driver(flow_dir, driver)
    monkeypatch.setattr(fr.fcost, "sprint_cost_usd", lambda *a, **k: 7.5)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert result.executed == ()
    assert result.stopped.reason == "budget"
    assert "5.00" in result.stopped.detail and "7.50" in result.stopped.detail


def test_an_unreadable_burnrate_store_never_wedges_the_chain(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="no-burnrate")
    driver = fr.default_driver(now=NOW)
    driver["budget_usd"] = 5.0
    fr.write_driver(flow_dir, driver)
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False,
                          burnrate_db=tmp_path / "no-such-db")
    assert result.executed == ("flow_advance", "flow_advance")


# --- Task 6: presentation and CLI -------------------------------------------

def test_render_stop_names_the_flow_the_reason_and_the_next_kind(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="presented")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    text = fr.render_stop(result, "dsi/presented")
    labels = [line.split()[0] for line in text.splitlines()]
    assert labels[:5] == ["FOREMAN", "CHAIN", "STOPPED", "WHY", "NEXT"]
    assert "needs_judgment" in text


def test_render_stop_prints_the_command_line_for_an_unexecutable_action():
    action = _action("sprint_launch", command="/sprint dsi C:\\work\\x.md")
    decision = fr.decide(action, fr.HostFacts())
    result = fr.ChainResult(executed=(), stopped=decision, chain_depth=0,
                            receipts=0)
    assert "/sprint dsi C:\\work\\x.md" in fr.render_stop(result, "dsi/x")


def test_render_stop_lists_the_allowed_verdicts_at_an_open_gate():
    action = _action("gate_await", blocking="human", gate_id="G3",
                     command="/flow gate dsi/x <verdict>")
    decision = fr.decide(action, fr.HostFacts())
    result = fr.ChainResult(executed=(), stopped=decision, chain_depth=0,
                            receipts=0)
    text = fr.render_stop(result, "dsi/x")
    assert "proceed" in text and "adapt" in text and "park" in text


def test_no_string_the_driver_emits_contains_an_em_dash():
    """Wilco hard rule 2026-07-08, enforced the same way slice a and slice d
    enforce it."""
    for path in ("foreman_run.py", "foreman_cost.py"):
        source = (pathlib.Path(__file__).resolve().parent / path).read_text(
            encoding="utf-8")
        assert "\u2014" not in source, path


def test_no_rendered_line_ever_shows_a_literal_none(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="no-none")
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False)
    assert "None" not in fr.render_stop(result, "dsi/no-none")


def test_cli_run_exits_zero_and_prints_the_stop(tmp_path, capsys):
    flows_root, flow_dir = _tree(tmp_path, slug="cli-run")
    code = fr.main(["run", "dsi/cli-run", "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints"), "--no-render"])
    assert code == 0
    assert "FOREMAN" in capsys.readouterr().out


def test_cli_run_dry_run_changes_nothing(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="cli-dry")
    fr.main(["run", "cli-dry", "--flows-root", str(flows_root),
             "--sprints-root", str(tmp_path / "sprints"), "--dry-run",
             "--no-render"])
    assert ft.load_flow(flow_dir)["stage"] == "idea"


def test_cli_run_exits_one_on_an_unresolvable_ref(tmp_path, capsys):
    flows_root, _flow_dir = _tree(tmp_path, slug="cli-missing")
    code = fr.main(["run", "not-a-flow", "--flows-root", str(flows_root),
                    "--sprints-root", str(tmp_path / "sprints")])
    assert code == 1


def test_cli_pause_and_resume_round_trip(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="cli-pause")
    common = ["--flows-root", str(flows_root),
              "--sprints-root", str(tmp_path / "sprints")]
    fr.main(["pause", "cli-pause", "--reason", "partner call"] + common)
    assert fr.load_driver(flow_dir, halt=False)["paused_reason"] == "partner call"
    fr.main(["resume", "cli-pause"] + common)
    assert fr.load_driver(flow_dir, halt=False)["paused_reason"] == ""


def test_cli_run_json_emits_parsable_json(tmp_path, capsys):
    flows_root, _flow_dir = _tree(tmp_path, slug="cli-json")
    fr.main(["run", "cli-json", "--flows-root", str(flows_root),
             "--sprints-root", str(tmp_path / "sprints"), "--json",
             "--no-render"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped"]["reason"] == "needs_judgment"
    assert payload["executed"] == ["flow_advance", "flow_advance"]


# --- Task 7: every writer takes the lock ------------------------------------

import outbox
import terminal_sweep as ts


def _sweepable_tree(tmp_path, slug="sweepable"):
    flows_root, flow_dir = _tree(tmp_path, slug=slug)
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    flow["gate"] = None
    ft.write_flow(flow_dir, flow)
    return flows_root, flow_dir


def test_apply_match_skips_when_another_writer_holds_the_flow_lock(tmp_path):
    """The concurrency defect the spec names: two writers crossing two
    boundaries when the operator meant one."""
    _flows_root, flow_dir = _sweepable_tree(tmp_path)
    sprint = {"sprint_id": "dsi__s__x", "status": "passing",
              "last_test_result": None, "last_verify_result": None}
    with fl.acquire(flow_dir, "the-driver", now=NOW, pid=999):
        res = ts.apply_match(flow_dir, sprint, now=NOW)
    assert res["skipped"] is True
    assert res.get("reason") == "lock_busy"
    assert ft.load_flow(flow_dir)["current_step"]["phase"] == "sprint"


def test_apply_match_still_sweeps_normally_when_the_lock_is_free(tmp_path):
    _flows_root, flow_dir = _sweepable_tree(tmp_path, slug="free-lock")
    sprint = {"sprint_id": "dsi__s__x", "status": "passing",
              "last_test_result": None, "last_verify_result": None}
    res = ts.apply_match(flow_dir, sprint, now=NOW)
    assert res["skipped"] is False
    flow = ft.load_flow(flow_dir)
    # Spec item 9: apply_match now stops at the G4 ship gate it opens rather
    # than walking past it to G3 (flow_transition's phase=="sprint" boundary
    # is gated by spec item 6).
    assert flow["current_step"]["phase"] == "sprint"
    assert flow["gate"]["id"] == "G4"
    # sweep_context is attached to the in-memory gate only to feed
    # outbox.compose(); by design (spec 2026-07-21-...-g3-design.md lines
    # 176-181, and test_outbox.py's own guidance-block tests) it is never
    # persisted back onto flow.json, so the queued notification body is
    # where the sweep's status-aware guidance actually shows up.
    entry = outbox.read_outbox(flow_dir)[0]
    assert "Sprint status:" in entry["body"]


def test_apply_match_releases_the_lock(tmp_path):
    _flows_root, flow_dir = _sweepable_tree(tmp_path, slug="released")
    ts.apply_match(flow_dir, {"sprint_id": "x", "status": "passing"}, now=NOW)
    assert not fl.lock_path(flow_dir).exists()


def test_the_driver_and_the_sweep_cannot_both_cross_the_same_boundary(tmp_path):
    """The spec's lock drill, in miniature: while the driver holds the lock,
    the sweep gets nothing, not a silent double-write.

    Fixed 2026-08-02 per reviewer finding: the original fixture here was
    _decided_gate_tree, which builds a flow at phase=learn with a non-null
    gate. is_sweep_eligible is False for that flow, so apply_match's own
    pre-write eligibility re-assert returns {"skipped": True} before ever
    reaching foreman_lock.acquire; the test passed against the OLD, unlocked
    apply_match too and proved nothing about the lock. A sweep-eligible tree
    (phase=sprint, gate=None, matching _sweepable_tree) is required to reach
    the lock-acquisition code at all.
    """
    _flows_root, flow_dir = _sweepable_tree(tmp_path, slug="driver-vs-sweep")
    with fl.acquire(flow_dir, "the-driver", now=NOW, pid=999):
        res = ts.apply_match(flow_dir, {"sprint_id": "x", "status": "passing"},
                             now=NOW)
    assert res["skipped"] is True
    assert res.get("reason") == "lock_busy"


# --- scope E: needs_spec ------------------------------------------------------

def _g2_consume_action(gate_id="G2"):
    """A gate_consume NextAction for `gate_id`, the shape foreman_plan._gate_consume
    produces. One owner for this seed data: every needs_spec case below uses it."""
    return fp.NextAction(
        kind="gate_consume",
        why="{} is decided with verdict approve at step 2 of 3".format(gate_id),
        blocking="",
        command="/flow advance ZeroNonsense.dev/demo",
        gate_id=gate_id,
        step_index=1,
        role="none",
        guidance_key="gate_consume.{}.approve".format(gate_id.lower()),
    )


def test_needs_spec_is_a_stop_reason():
    assert "needs_spec" in fr.STOP_REASONS


def test_needs_spec_fires_for_a_g2_approve_on_a_multi_step_flow_with_no_spec_path():
    decision = fr.decide(_g2_consume_action(), fr.HostFacts(), "approve",
                         step_of=3, flow_spec_path="")
    assert decision.execute is False
    assert decision.reason == "needs_spec"
    assert "spec_path" in decision.detail
    assert "cmd_advance" in decision.detail


def test_needs_spec_does_not_fire_when_the_conditions_are_not_all_met():
    """Table-driven: each row flips exactly one condition off, and each row must
    execute. of == 1, a recorded spec_path, another gate, another verdict."""
    cases = [
        ("single-step flow", _g2_consume_action(), "approve", 1, ""),
        ("spec_path recorded", _g2_consume_action(), "approve", 3,
         "C:\\work\\10_holding\\05_docs\\superpowers\\specs\\x.md"),
        ("G3 not G2", _g2_consume_action("G3"), "approve", 3, ""),
        ("verdict revise", _g2_consume_action(), "revise", 3, ""),
    ]
    for label, action, verdict, of, spec in cases:
        decision = fr.decide(action, fr.HostFacts(), verdict,
                             step_of=of, flow_spec_path=spec)
        assert decision.execute is True, label
        assert decision.reason == "", label


def test_needs_spec_does_not_fire_when_step_of_is_absent():
    """The driver must not refuse on a flow whose current_step.of it cannot read:
    an unknown `of` is not evidence of a multi-step flow."""
    decision = fr.decide(_g2_consume_action(), fr.HostFacts(), "approve")
    assert decision.execute is True


def test_needs_steps_still_wins_for_g1_approve():
    """The new rule sits next to needs_steps and must not displace it."""
    action = _g2_consume_action("G1")
    decision = fr.decide(action, fr.HostFacts(), "approve", step_of=3,
                         flow_spec_path="")
    assert decision.reason == "needs_steps"


def test_step_of_and_flow_spec_path_read_the_flow_the_way_decide_expects():
    flow = {"current_step": {"index": 1, "of": 3, "phase": "grill"},
            "spec_path": "C:\\work\\10_holding\\05_docs\\x.md"}
    assert fr.step_of(flow) == 3
    assert fr.flow_spec_path(flow) == "C:\\work\\10_holding\\05_docs\\x.md"
    assert fr.step_of({}) is None
    assert fr.flow_spec_path({}) == ""
    assert fr.step_of(None) is None
    assert fr.flow_spec_path(None) == ""


# --- scope C: launch-line shape validation ------------------------------------

GOOD_SPEC = ("C:\\work\\10_holding\\05_docs\\superpowers\\specs\\"
             "2026-08-16-foreman-sprint-launch-await-design.md")
GOOD_MAP = ("spec=opus,plan=opus,dev=sonnet,test=haiku,verify=opus,"
            "iterate=sonnet,report=haiku")
GOOD_LINE = "/sprint ZeroNonsense.dev {} --model-map {}".format(GOOD_SPEC, GOOD_MAP)


def test_malformed_launch_is_a_stop_reason():
    assert "malformed_launch" in fr.STOP_REASONS


def test_validate_launch_line_accepts_a_well_formed_line():
    assert fr.validate_launch_line(GOOD_LINE, GOOD_SPEC) == ""


def test_validate_launch_line_rejects_one_case_per_check():
    """Table-driven, one row per mechanical check in spec scope C, plus the
    model_map parse failure. Each row asserts the returned sentence quotes the
    offending value, because the spec requires it printed verbatim."""
    cases = [
        ("CR",
         GOOD_LINE.replace(" --model-map", "\r --model-map"), GOOD_SPEC, "newline"),
        ("LF",
         GOOD_LINE + "\nrm -rf /", GOOD_SPEC, "newline"),
        ("no /sprint prefix",
         "sprint ZeroNonsense.dev " + GOOD_SPEC + " --model-map " + GOOD_MAP,
         GOOD_SPEC, "/sprint "),
        ("spec is not .md",
         GOOD_LINE, "C:\\work\\10_holding\\05_docs\\notes.txt", "notes.txt"),
        ("spec outside C:\\work",
         GOOD_LINE, "D:\\elsewhere\\specs\\thing.md", "D:\\elsewhere"),
        ("no --model-map segment",
         "/sprint ZeroNonsense.dev " + GOOD_SPEC, GOOD_SPEC, "--model-map"),
        ("--model-map does not parse",
         "/sprint ZeroNonsense.dev {} --model-map dev=nonsuch".format(GOOD_SPEC),
         GOOD_SPEC, "dev=nonsuch"),
    ]
    for label, line, spec, needle in cases:
        problem = fr.validate_launch_line(line, spec)
        assert problem != "", label
        assert needle in problem, "{}: {!r}".format(label, problem)


def test_validate_launch_line_accepts_a_lowercase_md_and_a_lowercase_drive():
    """Case-insensitivity is explicit in the spec for the .md check, and the
    C:\\work prefix is compared the same way flow_transition.local_spec_path
    compares it."""
    spec = "C:\\work\\10_holding\\05_docs\\superpowers\\specs\\thing.MD"
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)
    assert fr.validate_launch_line(line, spec) == ""


def test_validate_launch_line_rejects_a_dot_dot_escape_from_c_dev():
    """SEC-1 fix: a raw string-prefix compare on the C:\\work root is bypassable
    with a '..' traversal segment that still starts with the literal prefix
    'C:\\work' but resolves outside it. Reported by the blind verifier against
    the pre-fix code, which returned "" (accepted) for this line."""
    spec = "C:\\work\\..\\Windows\\System32\\evil.md"
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)
    problem = fr.validate_launch_line(line, spec)
    assert problem != "", "traversal escape out of C:\\work must be rejected"


def test_validate_launch_line_accepts_a_legitimately_nested_path():
    """Companion to the traversal-escape test above: a normal nested path with
    no '..' segments must still pass, confirming the fix did not overtighten
    the check."""
    spec = ("C:\\work\\10_holding\\05_docs\\superpowers\\specs\\"
            "foo.md")
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)
    assert fr.validate_launch_line(line, spec) == ""


def test_validate_launch_line_handles_empty_inputs_without_raising():
    assert fr.validate_launch_line("", "") != ""
    assert fr.validate_launch_line(None, None) != ""


def test_malformed_launch_error_is_a_runtime_error_not_a_value_error():
    """Mirrors flow_transition.RelaunchGuardError's own reason for being a
    RuntimeError: a caller must be able to catch it separately from the
    model_hint ValueError backstop."""
    assert issubclass(fr.MalformedLaunchError, RuntimeError)


# --- scope B: the driver record carries a launch key ---------------------------

def test_default_driver_carries_the_launch_key():
    assert "launch" in fr.default_driver(session_id="s1", now=NOW)
    assert fr.default_driver(session_id="s1", now=NOW)["launch"] is None


def test_load_driver_defaults_launch_without_overwriting_a_recorded_one(tmp_path):
    assert fr.load_driver(tmp_path, halt=False)["launch"] is None
    (tmp_path / "foreman.json").write_text(
        json.dumps({"launch": {"line": "/sprint x", "spec_path": "p",
                               "ts": NOW, "session_id": "s"}}),
        encoding="utf-8")
    assert fr.load_driver(tmp_path, halt=False)["launch"]["line"] == "/sprint x"
    assert not issubclass(fr.MalformedLaunchError, ValueError)


# --- scope A: sprint_launch is executable, as an authorisation -----------------

def _sprint_phase_flow(tmp_path, spec_path, project="ZeroNonsense.dev",
                       slug="demo-step-5"):
    """A flow directory parked at stage execute / phase sprint, with `spec_path`
    recorded. One owner for this seed data: every scope A case below uses it.
    Deterministic: fixed ids, fixed timestamps, no generator.
    """
    flow_dir = tmp_path / "flows" / project / slug
    flow_dir.mkdir(parents=True)
    flow = {
        "schema_version": 1,
        "flow_id": "{}__{}__20260817T091403Z".format(project, slug),
        "project": project,
        "slug": slug,
        "idea": "step 5",
        "stage": "execute",
        "status": "running",
        "current_step": {"index": 0, "of": 1, "phase": "sprint"},
        "steps": [{"index": 0, "title": "step 0", "status": "running"}],
        "model_hint": {},
        "gate": None,
        "spec_path": str(spec_path),
        "created_at": NOW,
        "updated_at": NOW,
    }
    ft.write_flow(flow_dir, flow)
    (flow_dir / "flow-log.ndjson").write_text("", encoding="utf-8")
    # A flow only ever reaches phase=sprint by advancing through a G2 consume,
    # which cmd_advance logs as 'sprint_emitted' the moment it happens
    # (flow_transition.py:660-662). foreman_status.resolve_sprint matches a
    # sprint.json against that receipt, so a fixture that skips straight to
    # phase=sprint via write_flow must still carry the receipt a real flow
    # would already have, or no sprint.json can ever resolve for it.
    ft.append_log(flow_dir, ft._log_entry(
        NOW, "execute", "orchestrator", "sprint_emitted", outcome="info",
        detail="/sprint {} {}".format(project, spec_path)))
    return flow_dir


def _real_spec(tmp_path, name="thing.md"):
    """A spec file that actually exists, under a fake C:\\src-shaped tree."""
    spec_dir = tmp_path / "dev" / "ZeroNonsense.dev" / "docs" / "superpowers" / "specs"
    spec_dir.mkdir(parents=True)
    spec = spec_dir / name
    spec.write_text("# spec\n", encoding="utf-8")
    return spec


def _launch_action(line):
    return fp.NextAction(
        kind="sprint_launch",
        why="step 1 of 1 is at phase sprint and no sprint has been launched",
        blocking="",
        command=line,
        gate_id=None,
        step_index=0,
        role="none",
        guidance_key="sprint_launch",
    )


def test_sprint_launch_is_no_longer_not_yet_executable():
    assert "sprint_launch" not in fr.NOT_YET_EXECUTABLE
    assert "sprint_launch" in fr.EXECUTABLE_KINDS
    assert "ship_preflight" in fr.EXECUTABLE_KINDS
    assert "ship_execute" in fr.EXECUTABLE_KINDS
    assert len(fr.EXECUTABLE_KINDS) == 9


def test_sprint_recover_is_now_executable(tmp_path):
    """Slice g moves this kind out of NOT_YET_EXECUTABLE (spec item 1)."""
    assert "sprint_recover" in fr.EXECUTABLE_KINDS
    assert "sprint_recover" not in fr.NOT_YET_EXECUTABLE
    action = _recover_action()
    assert fr.decide(action, fr.HostFacts()).execute is True


def _recover_flow(tmp_path, project="dsi", slug="payoff-books"):
    """A flow at execute/phase=sprint/gate=null: what is_sweep_eligible wants
    and what foreman_plan returns sprint_recover for on a needs-guidance
    terminal sprint."""
    flow_dir = tmp_path / project / slug
    flow_dir.mkdir(parents=True)
    flow = ft.new_flow(project, slug, "idea", NOW,
                       spec_path=r"C:\work\50_projects\dsi\05_docs\s.md")
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 1, "of": 1, "phase": "sprint",
                            "title": "step one"}
    flow["gate"] = None
    (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
    return flow_dir


def _recover_action():
    return fp.NextAction(kind="sprint_recover", why="w", blocking="",
                         command="/flow advance dsi/payoff-books", gate_id=None,
                         step_index=1, role="none",
                         guidance_key="sprint_recover.failed")


def test_execute_recover_opens_g3_family_gate_with_sweep_context(tmp_path,
                                                                  monkeypatch):
    """Success criterion 2, and the mutation proof: break the apply_match
    dispatch in execute_recover and this goes red, because nothing else in the
    driver attaches sweep_context.

    sweep_context lives on the in-memory gate only (terminal_sweep.py's
    Task-1-era test_apply_match_with_lock_false_crosses_while_the_caller_holds
    _the_lock documents the same thing), so it is asserted through the queued
    outbox record's composed body, not flow.json.
    """
    import outbox
    flow_dir = _recover_flow(tmp_path)
    sprint = {"sprint_id": "dsi__x__1", "status": "failed",
              "last_verify_result": {"summary": "3 failed"},
              "last_test_result": {"summary": "1288 passed"}}
    monkeypatch.setattr(fr, "resolve_sprint_for",
                        lambda *a, **k: sprint)
    result = fr.execute_recover(flow_dir, _recover_action(), now=NOW)
    assert result["skipped"] is False
    gate = ft.load_flow(flow_dir)["gate"]
    assert gate["status"] == "awaiting-owner"
    records = outbox.read_outbox(flow_dir)
    assert len(records) == 1
    body = records[0]["body"]
    assert "Sprint status: failed (needs-guidance)" in body
    assert "Verify: 3 failed" in body


def test_execute_recover_works_inside_the_lock_execute_once_holds(tmp_path,
                                                                  monkeypatch):
    """The whole reason Task 1 exists: the real call path is inside the lock."""
    flow_dir = _recover_flow(tmp_path)
    monkeypatch.setattr(fr, "resolve_sprint_for",
                        lambda *a, **k: {"sprint_id": "s1", "status": "failed"})
    decision = fr.execute_once(flow_dir, _recover_action(), "sess", now=NOW,
                               stage="execute")
    assert decision.execute is True, decision.detail
    assert isinstance(ft.load_flow(flow_dir).get("gate"), dict)


def test_execute_recover_raises_when_no_sprint_resolves(tmp_path, monkeypatch):
    """Failure path. A recover with no sprint has no status to build a
    sweep_context from, so crossing would strip the very thing this executor
    exists to attach."""
    flow_dir = _recover_flow(tmp_path)
    monkeypatch.setattr(fr, "resolve_sprint_for", lambda *a, **k: None)
    decision = fr.execute_once(flow_dir, _recover_action(), "sess", now=NOW,
                               stage="execute")
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert ft.load_flow(flow_dir).get("gate") is None


def test_execute_recover_raises_when_the_flow_is_no_longer_eligible(tmp_path,
                                                                    monkeypatch):
    """A skip must not be reported as a crossing: next_action would return
    sprint_recover again and the chain would spin to its cap."""
    flow_dir = _recover_flow(tmp_path)
    flow = ft.load_flow(flow_dir)
    flow["current_step"]["phase"] = "learn"      # no longer sweep-eligible
    (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.setattr(fr, "resolve_sprint_for",
                        lambda *a, **k: {"sprint_id": "s1", "status": "failed"})
    decision = fr.execute_once(flow_dir, _recover_action(), "sess", now=NOW,
                               stage="execute")
    assert decision.execute is False
    assert decision.reason == "execute_error"


def test_spec_missing_is_a_stop_reason_and_stop_reasons_grew():
    assert "spec_missing" in fr.STOP_REASONS
    assert "relaunch_guard" in fr.STOP_REASONS


def test_execute_launch_writes_the_record_then_the_receipt_carries_the_line(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)

    decision = fr.execute_once(flow_dir, _launch_action(line), "sess-1",
                               sprints_root=str(tmp_path / "sprints"),
                               now=NOW, stage="execute")

    assert decision.execute is True
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["launch"]["line"] == line
    assert driver["launch"]["spec_path"] == str(spec)
    assert driver["launch"]["ts"] == NOW
    assert driver["launch"]["session_id"] == "sess-1"

    entries = fr.read_log(flow_dir)
    receipts = [e for e in entries if e["action"] == fr.LAUNCH_RECEIPT_ACTION]
    assert len(receipts) == 1
    assert receipts[0]["actor"] == "foreman"
    assert receipts[0]["detail"] == line[:240]


def test_execute_launch_refuses_a_terminal_sprint_for_the_same_spec(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints" / "ZeroNonsense.dev" / "done"
    sprints_root.mkdir(parents=True)
    (sprints_root / "sprint.json").write_text(json.dumps({
        "sprint_id": "ZeroNonsense.dev__done__20260803T115034Z",
        "project": "ZeroNonsense.dev",
        "spec_path": str(spec),
        "status": "passing"}), encoding="utf-8")
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)

    decision = fr.execute_once(flow_dir, _launch_action(line), "sess-1",
                               sprints_root=str(tmp_path / "sprints"),
                               now=NOW, stage="execute")

    assert decision.execute is False
    assert decision.reason == "relaunch_guard"
    assert "ZeroNonsense.dev__done__20260803T115034Z" in decision.detail
    assert "passing" in decision.detail
    assert fr.load_driver(flow_dir, halt=False)["launch"] is None


def test_execute_launch_refuses_a_spec_that_is_not_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    spec = tmp_path / "dev" / "ZeroNonsense.dev" / "docs" / "nowhere.md"
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)

    decision = fr.execute_once(flow_dir, _launch_action(line), "sess-1",
                               sprints_root=str(tmp_path / "sprints"),
                               now=NOW, stage="execute")

    assert decision.execute is False
    assert decision.reason == "spec_missing"
    assert "nowhere.md" in decision.detail
    assert fr.load_driver(flow_dir, halt=False)["launch"] is None


def test_execute_launch_abstains_when_local_spec_path_cannot_see_the_disk(
        tmp_path, monkeypatch):
    """local_spec_path returning None means UNDECIDABLE, not "missing". Its own
    docstring forbids raising on it, so the launch proceeds.

    NOTE: this test flips the POSIX branch on by patching the module constant
    flow_transition.IS_WINDOWS. NEVER patch os.name to do this: pathlib
    dispatches on os.name, so setting it to "posix" on this Windows laptop makes
    every subsequent pathlib.Path() raise NotImplementedError and takes pytest's
    own reporting down with it.
    """
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(ft, "IS_WINDOWS", False)
    monkeypatch.setenv("ZND_DEV_ROOT", str(tmp_path / "no-such-mount"))
    spec = tmp_path / "dev" / "ZeroNonsense.dev" / "docs" / "nowhere.md"
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    line = "/sprint ZeroNonsense.dev {} --model-map {}".format(spec, GOOD_MAP)

    decision = fr.execute_once(flow_dir, _launch_action(line), "sess-1",
                               sprints_root=str(tmp_path / "sprints"),
                               now=NOW, stage="execute")

    assert decision.execute is True
    assert fr.load_driver(flow_dir, halt=False)["launch"]["line"] == line


def test_execute_launch_refuses_a_malformed_line(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    line = "/sprint ZeroNonsense.dev {} --model-map {}\nrm -rf /".format(spec, GOOD_MAP)

    decision = fr.execute_once(flow_dir, _launch_action(line), "sess-1",
                               sprints_root=str(tmp_path / "sprints"),
                               now=NOW, stage="execute")

    assert decision.execute is False
    assert decision.reason == "malformed_launch"
    assert "newline" in decision.detail
    assert fr.load_driver(flow_dir, halt=False)["launch"] is None


def test_two_runs_in_the_window_produce_exactly_one_launch_receipt(
        tmp_path, monkeypatch):
    """Success criterion 6. No sprint.json ever appears between the two runs."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)   # keep is_cowork_mount off
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = str(tmp_path / "sprints")

    first = fr.run_chain(flow_dir, sprints_root=sprints_root, session_id="s1",
                         now=NOW, render=False)
    second = fr.run_chain(flow_dir, sprints_root=sprints_root, session_id="s2",
                          now=NOW, render=False)

    assert first.executed == ("sprint_launch",)
    assert second.executed == ()
    assert second.stopped.action.kind == "sprint_await"
    entries = fr.read_log(flow_dir)
    assert sum(1 for e in entries
               if e["action"] == fr.LAUNCH_RECEIPT_ACTION) == 1


def test_deleting_foreman_json_offers_the_launch_again(tmp_path, monkeypatch):
    """The record is advisory, and losing it is the safe direction: the guards
    in execute_launch re-run against a disk that by now may hold the sprint."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = str(tmp_path / "sprints")

    fr.run_chain(flow_dir, sprints_root=sprints_root, session_id="s1", now=NOW,
                 render=False)
    (pathlib.Path(flow_dir) / "foreman.json").unlink()
    again = fr.run_chain(flow_dir, sprints_root=sprints_root, session_id="s2",
                         now=NOW, render=False)

    assert again.executed == ("sprint_launch",)


def test_the_launch_record_survives_the_chain_depth_write(tmp_path, monkeypatch):
    """run_chain writes chain_depth after every executed action. It must re-read
    the driver first, or it writes back the pre-execute snapshot and erases the
    record execute_launch just wrote."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)

    fr.run_chain(flow_dir, sprints_root=str(tmp_path / "sprints"),
                 session_id="s1", now=NOW, render=False)

    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["launch"] is not None
    assert driver["chain_depth"] == 1


def test_the_launch_record_is_cleared_once_the_sprint_resolves(
        tmp_path, monkeypatch):
    """Scope B: cleared once the step's sprint.json resolves, so a later step of
    the same flow gets a clean slate."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s1",
                 now=NOW, render=False)

    sprint_dir = sprints_root / "ZeroNonsense.dev" / "demo-step-5"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "sprint.json").write_text(json.dumps({
        "sprint_id": "ZeroNonsense.dev__demo-step-5__20260817T091403Z",
        "project": "ZeroNonsense.dev", "spec_path": str(spec),
        "phase": "dev", "iteration": 1, "status": "running"}), encoding="utf-8")

    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s2",
                 now=NOW, render=False)
    assert fr.load_driver(flow_dir, halt=False)["launch"] is None


def test_the_cowork_fence_still_converts_sprint_launch_to_emit_only():
    action = _launch_action("/sprint x C:\\work\\a.md --model-map dev=sonnet")
    decision = fr.decide(action, fr.HostFacts(cowork=True))
    assert decision.execute is False
    assert decision.reason == "cowork"
    assert decision.action.command.startswith("/sprint ")


def test_the_wip_limit_still_fences_sprint_launch_before_it_executes():
    action = _launch_action("/sprint x C:\\work\\a.md --model-map dev=sonnet")
    decision = fr.decide(action, fr.HostFacts(wip_count=1, wip_limit=1))
    assert decision.reason == "wip_limit"


# --- scope F: the AUTHORISED line ---------------------------------------------

def _result(executed, decision):
    return fr.ChainResult(tuple(executed), decision, len(executed), len(executed))


def test_render_stop_prints_authorised_yes_on_the_executed_path():
    line = "/sprint x C:\\work\\a.md --model-map dev=sonnet"
    await_action = fp.NextAction("sprint_await", "waiting", "agent", line,
                                 None, 0, "none", "sprint_await")
    decision = fr.Decision(await_action, False, "blocking", "blocks on agent")
    out = fr.render_stop(_result(["sprint_launch"], decision), "p/s")
    assert "AUTHORISED yes" in out
    assert "AUTHORISED no" not in out


def test_render_stop_prints_authorised_no_on_the_cowork_path():
    line = "/sprint x C:\\work\\a.md --model-map dev=sonnet"
    action = _launch_action(line)
    decision = fr.decide(action, fr.HostFacts(cowork=True))
    out = fr.render_stop(_result([], decision), "p/s")
    assert "AUTHORISED no" in out
    assert "AUTHORISED yes" not in out


def test_the_two_blocks_are_distinguishable_without_reading_the_do_line():
    """Success criterion 9: same DO line, different block."""
    line = "/sprint x C:\\work\\a.md --model-map dev=sonnet"
    cowork = fr.render_stop(
        _result([], fr.decide(_launch_action(line), fr.HostFacts(cowork=True))),
        "p/s")
    await_action = fp.NextAction("sprint_await", "waiting", "agent", line,
                                 None, 0, "none", "sprint_await")
    authorised = fr.render_stop(
        _result(["sprint_launch"],
                fr.Decision(await_action, False, "blocking", "blocks on agent")),
        "p/s")
    do_line = "DO       " + line
    assert do_line in cowork and do_line in authorised
    assert [l for l in cowork.splitlines() if "AUTHORISED" in l] != \
           [l for l in authorised.splitlines() if "AUTHORISED" in l]


def test_render_stop_prints_authorised_no_for_every_refused_launch():
    """A refused launch is an unauthorised launch, whatever refused it."""
    line = "/sprint x C:\\work\\a.md --model-map dev=sonnet"
    action = _launch_action(line)
    for reason, decision in (
            ("wip_limit", fr.decide(action, fr.HostFacts(wip_count=1, wip_limit=1))),
            ("relaunch_guard",
             fr.Decision(action, False, "relaunch_guard", "already terminal")),
            ("spec_missing",
             fr.Decision(action, False, "spec_missing", "not on disk")),
            ("malformed_launch",
             fr.Decision(action, False, "malformed_launch", "newline")),
    ):
        out = fr.render_stop(_result([], decision), "p/s")
        assert "AUTHORISED no" in out, reason


def test_render_stop_omits_the_authorised_line_for_unrelated_stops():
    action = fp.NextAction("gate_await", "waiting on G1", "human",
                           "/flow gate p/s <verdict>", "G1", 0, "none",
                           "gate_await.g1")
    decision = fr.Decision(action, False, "blocking", "blocks on human")
    assert "AUTHORISED" not in fr.render_stop(_result([], decision), "p/s")


def test_render_stop_still_omits_the_do_line_when_there_is_no_command():
    action = fp.NextAction("flow_close", "nothing left", "", "", None, 0,
                           "none", "flow_close")
    decision = fr.Decision(action, False, "complete", "nothing left to do")
    out = fr.render_stop(_result([], decision), "p/s")
    assert "DO" not in out
    assert "AUTHORISED" not in out


# --- scope D: the await poll --------------------------------------------------

class _FakeClock:
    """Monotonic seconds from a fixed list, then the last value forever. The
    suite gains no wall-clock time: `sleep` is a no-op recorder."""

    def __init__(self, ticks):
        self.ticks = list(ticks)
        self.slept = []

    def __call__(self):
        return self.ticks.pop(0) if len(self.ticks) > 1 else self.ticks[0]

    def sleep(self, seconds):
        self.slept.append(seconds)


def _write_sprint(sprints_root, spec, status, phase="dev", iteration=1):
    # created_at must sort after the fixture's 'sprint_emitted' receipt (NOW,
    # "2026-08-02T12:00:00Z"): foreman_status.resolve_sprint only accepts a
    # sprint.json whose created_at is strictly later than the emit it is
    # matched against.
    sprint_dir = pathlib.Path(sprints_root) / "ZeroNonsense.dev" / "demo-step-5"
    sprint_dir.mkdir(parents=True, exist_ok=True)
    (sprint_dir / "sprint.json").write_text(json.dumps({
        "sprint_id": "ZeroNonsense.dev__demo-step-5__20260817T091403Z",
        "project": "ZeroNonsense.dev", "spec_path": str(spec),
        "phase": phase, "iteration": iteration, "status": status,
        "created_at": "2026-08-17T09:14:03Z"}),
        encoding="utf-8")


def test_stop_reasons_is_exactly_fifteen_with_the_four_new_ones():
    assert len(fr.STOP_REASONS) == 15
    for reason in ("spec_missing", "needs_spec", "malformed_launch",
                   "await_timeout"):
        assert reason in fr.STOP_REASONS, reason
    assert len(set(fr.STOP_REASONS)) == 15


def test_await_defaults_match_the_spec():
    assert fr.AWAIT_DEFAULT_SECONDS == 3600
    assert fr.POLL_DEFAULT_SECONDS == 15


def test_await_sprint_returns_terminal_and_writes_exactly_two_receipts(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s1",
                 now=NOW, render=False)
    _write_sprint(sprints_root, spec, "running")
    before = len(fr.read_log(flow_dir))

    clock = _FakeClock([0, 0, 10, 20])

    def _sleep(seconds):
        clock.sleep(seconds)
        if len(clock.slept) == 2:
            _write_sprint(sprints_root, spec, "passing")

    out = fr.await_sprint(flow_dir, sprints_root=str(sprints_root),
                          timeout_seconds=600, poll_seconds=15, now=NOW,
                          clock=clock, sleep=_sleep)

    assert out.outcome == "terminal"
    assert out.sprint["status"] == "passing"
    assert out.ticks == 3
    assert clock.slept == [15, 15]
    entries = fr.read_log(flow_dir)[before:]
    assert [e["action"] for e in entries] == [fr.AWAIT_START_ACTION,
                                              fr.AWAIT_END_ACTION]


def test_await_sprint_times_out_with_await_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s1",
                 now=NOW, render=False)
    _write_sprint(sprints_root, spec, "running", phase="test", iteration=2)
    before = len(fr.read_log(flow_dir))

    clock = _FakeClock([0, 0, 15, 30])
    out = fr.await_sprint(flow_dir, sprints_root=str(sprints_root),
                          timeout_seconds=25, poll_seconds=15, now=NOW,
                          clock=clock, sleep=clock.sleep)

    assert out.outcome == "timeout"
    assert out.stopped.reason == "await_timeout"
    assert "test" in out.stopped.detail
    assert "2" in out.stopped.detail
    assert "resumes waiting" in out.stopped.detail
    entries = fr.read_log(flow_dir)[before:]
    assert len(entries) == 2


def test_await_sprint_honours_a_per_flow_pause_mid_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s1",
                 now=NOW, render=False)
    _write_sprint(sprints_root, spec, "running")
    before = len(fr.read_log(flow_dir))

    clock = _FakeClock([0, 0, 15, 30])

    def _sleep(seconds):
        clock.sleep(seconds)
        fr.set_pause(flow_dir, "operator stepped in", now=NOW)

    out = fr.await_sprint(flow_dir, sprints_root=str(sprints_root),
                          timeout_seconds=600, poll_seconds=15, now=NOW,
                          clock=clock, sleep=_sleep)

    assert out.outcome == "fence"
    assert out.stopped.action.guidance_key == "halt.paused"
    assert len(fr.read_log(flow_dir)[before:]) == 2


def test_await_sprint_honours_the_global_halt_file_mid_poll(
        tmp_path, monkeypatch):
    """FOREMAN_HALT is stat'd by load_driver on every tick, so it stops an await
    mid-flight exactly as it stops a chain."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fst, "HALT_FILE", tmp_path / "FOREMAN_HALT")
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    fr.run_chain(flow_dir, sprints_root=str(sprints_root), session_id="s1",
                 now=NOW, render=False)
    _write_sprint(sprints_root, spec, "running")

    clock = _FakeClock([0, 0, 15, 30])

    def _sleep(seconds):
        clock.sleep(seconds)
        (tmp_path / "FOREMAN_HALT").write_text("stop", encoding="utf-8")

    out = fr.await_sprint(flow_dir, sprints_root=str(sprints_root),
                          timeout_seconds=600, poll_seconds=15, now=NOW,
                          clock=clock, sleep=_sleep)

    assert out.outcome == "fence"
    assert out.stopped.action.guidance_key == "halt.halt_file"


def test_run_await_re_enters_the_chain_once_on_terminal(tmp_path, monkeypatch):
    """The spec is explicit: --await buys "told the instant it finished, with the
    next action already computed", not a longer chain. ship_preflight is now
    executable (slice c), so the re-entry either stops there for some other
    reason or runs it and reaches the G4 gate_open right behind it; both are
    "got past the await and onto the ship path", which is the honest claim."""
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    first = fr.run_chain(flow_dir, sprints_root=str(sprints_root),
                         session_id="s1", now=NOW, render=False)
    _write_sprint(sprints_root, spec, "passing")

    clock = _FakeClock([0, 0])
    out = fr.run_await(flow_dir, first, sprints_root=str(sprints_root),
                       render=False, timeout_seconds=600, poll_seconds=15,
                       now=NOW, clock=clock, sleep=clock.sleep)

    assert out.stopped.action.kind in ("gate_open", "ship_preflight")
    assert out.stopped.reason in ("needs_judgment", "")
    assert clock.slept == []


def test_run_await_returns_the_timeout_stop_without_re_entering(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_DEV_ROOT_PREFIX", str(tmp_path / "dev") + "\\")
    monkeypatch.setattr(fr, "_REPO_ROOT", tmp_path)
    spec = _real_spec(tmp_path)
    flow_dir = _sprint_phase_flow(tmp_path, spec)
    sprints_root = tmp_path / "sprints"
    first = fr.run_chain(flow_dir, sprints_root=str(sprints_root),
                         session_id="s1", now=NOW, render=False)
    _write_sprint(sprints_root, spec, "running")

    clock = _FakeClock([0, 0, 30])
    out = fr.run_await(flow_dir, first, sprints_root=str(sprints_root),
                       render=False, timeout_seconds=25, poll_seconds=15,
                       now=NOW, clock=clock, sleep=clock.sleep)

    assert out.stopped.reason == "await_timeout"
    assert out.executed == first.executed


def test_the_cli_parses_await_and_poll():
    parser = fr._build_arg_parser()
    plain = parser.parse_args(["run", "p/s"])
    assert plain.await_seconds is None
    assert plain.poll_seconds == 15
    bare = parser.parse_args(["run", "--await", "p/s"])
    assert bare.await_seconds == 3600
    valued = parser.parse_args(["run", "--await", "120", "--poll", "5", "p/s"])
    assert valued.await_seconds == 120
    assert valued.poll_seconds == 5


# --- slice c: ship_preflight execution --------------------------------------

import ship_preflight as sp_mod


def _preflight_action():
    return fp.NextAction(
        kind="ship_preflight",
        why="the sprint reached success status passing, so the checks run",
        blocking="", command="", gate_id=None, step_index=0,
        role="ship_preflight", guidance_key="ship_preflight")


def _sprint_phase_flow_dir(tmp_path, slug="shipme"):
    """A flow at execute/phase=sprint with no gate, on disk."""
    flow_dir, flow = ft.cmd_new(tmp_path, "znd", "ship it", now=NOW, slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_ship_preflight_is_executable_now():
    assert "ship_preflight" in fr.EXECUTABLE_KINDS
    assert "ship_preflight" not in fr.NOT_YET_EXECUTABLE


def test_execute_preflight_writes_the_results_dict_to_the_driver(tmp_path,
                                                                 monkeypatch):
    flow_dir = _sprint_phase_flow_dir(tmp_path)
    captured = {}

    def fake_run_preflight(flow, sprint, repo_root, diff_budget_lines=800,
                           spec_text=None):
        captured["repo_root"] = repo_root
        captured["budget"] = diff_budget_lines
        return {"clean_tree": True, "right_branch": True,
                "worktree_branch": "sprint/shipme", "revert_point": "abc1234",
                "diff_lines": 12}

    monkeypatch.setattr(fr.sp, "run_preflight", fake_run_preflight)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    out = fr.execute_preflight(flow_dir, _preflight_action(), now=NOW)
    assert out["worktree_branch"] == "sprint/shipme"
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["preflight"]["revert_point"] == "abc1234"
    assert captured["repo_root"] == "C:\\work\\znd"
    assert captured["budget"] == sp_mod.DEFAULT_DIFF_BUDGET_LINES


def test_execute_preflight_passes_the_flows_own_diff_budget_override(tmp_path,
                                                                    monkeypatch):
    flow_dir = _sprint_phase_flow_dir(tmp_path, slug="budget")
    driver = fr.load_driver(flow_dir, halt=False)
    driver["diff_budget_lines"] = 40
    fr.write_driver(flow_dir, driver)
    seen = {}

    def fake_run_preflight(flow, sprint, repo_root, diff_budget_lines=800,
                           spec_text=None):
        seen["budget"] = diff_budget_lines
        return {"clean_tree": True}

    monkeypatch.setattr(fr.sp, "run_preflight", fake_run_preflight)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    fr.execute_preflight(flow_dir, _preflight_action(), now=NOW)
    assert seen["budget"] == 40


def test_execute_once_on_ship_preflight_records_a_receipt_naming_the_checks(
        tmp_path, monkeypatch):
    flow_dir = _sprint_phase_flow_dir(tmp_path, slug="receipt")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(
        fr.sp, "run_preflight",
        lambda flow, sprint, repo_root, diff_budget_lines=800, spec_text=None: {
            "clean_tree": True, "right_branch": True, "tests_pass": False,
            "worktree_branch": "sprint/receipt", "revert_point": "abc1234",
            "diff_lines": 12, "scope_respected": None,
        })

    decision = fr.execute_once(flow_dir, _preflight_action(), "sess",
                               now=NOW, stage="execute")
    assert decision.execute is True
    log = fr.read_log(flow_dir)
    receipt = [e for e in log if e["action"] == fr.PREFLIGHT_RECEIPT_ACTION][-1]
    assert "tests_pass" in receipt["detail"]
    assert receipt["outcome"] == "pass"


def test_a_failing_check_never_stops_the_chain(tmp_path, monkeypatch):
    """The parent spec's 'annotates, does not block' rule, at the host level.
    Every one of the nine checks is red and the action still executes."""
    flow_dir = _sprint_phase_flow_dir(tmp_path, slug="annotate")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(
        fr.sp, "run_preflight",
        lambda flow, sprint, repo_root, diff_budget_lines=800, spec_text=None: {
            "clean_tree": False, "right_branch": False, "tests_pass": False,
            "verify_pass": False, "no_secrets": False,
            "secrets_found": ["[REDACTED]"], "scope_respected": False,
            "out_of_scope_paths": ["x.py"], "diff_budget": False,
            "diff_lines": 9000, "no_em_dash": False,
            "worktree_branch": "sprint/annotate", "revert_point": "abc1234",
        })
    decision = fr.execute_once(flow_dir, _preflight_action(), "sess",
                               now=NOW, stage="execute")
    assert decision.execute is True
    assert decision.reason == ""


def test_the_preflight_receipt_never_leaks_a_secret(tmp_path, monkeypatch):
    """secrets_found is deliberately excluded from the receipt detail: the
    receipt is durable and a redacted secret is still a secret's shape."""
    flow_dir = _sprint_phase_flow_dir(tmp_path, slug="noleak")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(
        fr.sp, "run_preflight",
        lambda flow, sprint, repo_root, diff_budget_lines=800, spec_text=None: {
            "no_secrets": False, "secrets_found": ["ghp_" + "z" * 36],
            "clean_tree": True,
        })
    fr.execute_once(flow_dir, _preflight_action(), "sess", now=NOW,
                    stage="execute")
    receipt = [e for e in fr.read_log(flow_dir)
               if e["action"] == fr.PREFLIGHT_RECEIPT_ACTION][-1]
    assert "ghp_" not in receipt["detail"]
    assert "no_secrets=False" in receipt["detail"]


def test_execute_preflight_reports_rather_than_guessing_a_repo_root(tmp_path,
                                                                   monkeypatch):
    flow_dir = _sprint_phase_flow_dir(tmp_path, slug="noroot")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "")
    decision = fr.execute_once(flow_dir, _preflight_action(), "sess",
                               now=NOW, stage="execute")
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert "repo root" in decision.detail


def test_record_receipt_without_a_detail_override_is_unchanged(tmp_path):
    flow_dir, _flow = ft.cmd_new(tmp_path, "znd", "receipts", now=NOW,
                                 slug="detailnone")
    action = _preflight_action()
    fr.record_receipt(flow_dir, action, "execute", NOW)
    last = fr.read_log(flow_dir)[-1]
    assert last["detail"] == action.why


def test_receipt_detail_is_none_for_kinds_that_carry_no_result():
    action = fp.NextAction(kind="flow_advance", why="w", blocking="", command="",
                           gate_id=None, step_index=0, role="none",
                           guidance_key="flow_advance.idea")
    assert fr.receipt_detail(action, None) is None


# --- slice c: ship_execute --------------------------------------------------

def _ship_action():
    return fp.NextAction(
        kind="ship_execute",
        why="G4 is decided with verdict ship, so branch sprint/shipme merges",
        blocking="", command="", gate_id="G4", step_index=0, role="none",
        guidance_key="ship_execute")


def _shippable(tmp_path, slug="shipme"):
    """A flow with a decided G4 ship gate and a preflight record on disk."""
    flow_dir, flow = ft.cmd_new(tmp_path, "znd", "ship it", now=NOW, slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.persist_open_gate(flow_dir, flow, "G4", 0, NOW)
    ft.cmd_gate(flow_dir, "ship", now=NOW)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["preflight"] = {"worktree_branch": "sprint/" + slug,
                           "revert_point": "0" * 40, "clean_tree": True}
    fr.write_driver(flow_dir, driver)
    return flow_dir


def _git_recorder(results=None):
    """(fake_git, calls). `results` maps a joined arg prefix to (code, stdout)."""
    calls = []
    results = results or {}

    def fake_git(args, cwd):
        joined = " ".join(args)
        calls.append((joined, str(cwd)))
        for prefix, outcome in results.items():
            if joined.startswith(prefix):
                return outcome
        if joined.startswith("rev-parse --abbrev-ref"):
            return 0, "main\n"
        if joined.startswith("rev-parse"):
            return 0, "f" * 40 + "\n"
        return 0, ""
    return fake_git, calls


def test_ship_execute_runs_merge_no_ff_then_push_in_that_order(tmp_path,
                                                               monkeypatch):
    flow_dir = _shippable(tmp_path)
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    info = fr.execute_ship(flow_dir, _ship_action(), now=NOW)
    joined = [c[0] for c in calls]
    merge_at = joined.index("merge --no-ff sprint/shipme")
    push_at = joined.index("push")
    assert merge_at < push_at
    assert all(c[1] == "C:\\work\\znd" for c in calls)
    assert info["merge_sha"] == "f" * 40
    assert info["branch"] == "sprint/shipme"
    assert info["target"] == "main"
    assert info["pushed"] is True


def test_ship_execute_with_push_false_never_calls_push(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="nopush")
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    info = fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)
    assert "push" not in [c[0] for c in calls]
    assert info["pushed"] is False


def test_ship_execute_records_the_shas_and_the_shipped_flag(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="shas")
    # A distinct value per rev-parse target, so the freshly-read revert_point
    # (Finding 3, 2026-08-17 review) is provably not the stale preflight-time
    # one ("0" * 40, baked in by _shippable) and not the merge sha either.
    fake_git, _calls = _git_recorder({
        "rev-parse --abbrev-ref": (0, "main\n"),
        "rev-parse main": (0, "e" * 40 + "\n"),
        "rev-parse HEAD": (0, "f" * 40 + "\n"),
    })
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["shipped"] is True
    assert driver["ship"]["merge_sha"] == "f" * 40
    assert driver["ship"]["revert_point"] == "e" * 40


def test_a_non_zero_merge_raises_ship_execute_error(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="conflict")
    fake_git, _calls = _git_recorder(
        {"merge --no-ff": (1, "CONFLICT (content): Merge conflict in x.py")})
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    with pytest.raises(fr.ShipExecuteError) as exc:
        fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)
    assert "merge" in str(exc.value)
    assert fr.load_driver(flow_dir, halt=False).get("shipped") is not True


def test_external_demo_fence_refuses_ship_before_any_git_action(tmp_path,
                                                                 monkeypatch):
    """02_roadmap/2026-08-24_foreman_guided_lane_program.md section 3: with
    ZND_EXTERNAL_DEMO=1, ship_execute refuses before touching git."""
    flow_dir = _shippable(tmp_path, slug="fenced")
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setenv("ZND_EXTERNAL_DEMO", "1")

    with pytest.raises(fr.ShipExecuteError) as exc:
        fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)
    assert "ZND_EXTERNAL_DEMO" in str(exc.value)
    assert "fenced" in str(exc.value)
    assert calls == []
    assert fr.load_driver(flow_dir, halt=False).get("shipped") is not True


def test_external_demo_fence_surfaces_as_stop_reason_execute_error(tmp_path,
                                                                    monkeypatch):
    """The generic execute_once handler already turns any raised exception
    into an "execute_error" Decision rather than a stack trace, and run_chain
    already fences every execute_error stop with a driver_fence receipt
    (see the comment above _HOST_FENCE_REASONS). The fence's raise only needs
    to arrive as that same exception, so it inherits that behavior for free."""
    flow_dir = _shippable(tmp_path, slug="fencereceipt")
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setenv("ZND_EXTERNAL_DEMO", "1")

    decision = fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW,
                               stage="execute", push=False)
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert "ZND_EXTERNAL_DEMO" in decision.detail
    assert calls == []


def test_hold_is_unaffected_by_the_external_demo_fence(tmp_path, monkeypatch):
    flow_dir, flow = ft.cmd_new(tmp_path, "znd", "hold under fence", now=NOW,
                               slug="holdfenced")
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "one", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    ft.cmd_advance(flow_dir, now=NOW)  # opens G4
    monkeypatch.setenv("ZND_EXTERNAL_DEMO", "1")

    ft.cmd_gate(flow_dir, "hold", note="not yet", now=NOW)

    driver = {}
    updated, _line = ft.cmd_advance(flow_dir, now=NOW, driver=driver)
    assert updated["current_step"]["phase"] == "learn"
    assert driver["held"]["reason"] == "not yet"


def test_a_failed_merge_surfaces_as_stop_reason_execute_error(tmp_path,
                                                              monkeypatch):
    flow_dir = _shippable(tmp_path, slug="stopreason")
    fake_git, _calls = _git_recorder({"merge --no-ff": (1, "CONFLICT")})
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    decision = fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW,
                               stage="execute", push=False)
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert "execute_error" in fr.STOP_REASONS
    assert len(fr.STOP_REASONS) == 15


def test_a_failed_push_after_a_good_merge_still_raises(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="pushfail")
    fake_git, _calls = _git_recorder({"push": (128, "fatal: no upstream")})
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    with pytest.raises(fr.ShipExecuteError) as exc:
        fr.execute_ship(flow_dir, _ship_action(), now=NOW)
    assert "push" in str(exc.value)


def test_a_failed_push_surfaces_stderr_text_in_the_error(tmp_path, monkeypatch):
    """Finding 2 (2026-08-17 review): ship_preflight.git() used to discard
    stderr entirely, and a real `git push` failure puts nearly all of its
    diagnostic text there. Exercises the real (unmocked) sp.git through a
    faked subprocess.run, so this proves the fix in git() itself, not just a
    test double's behaviour: execute_ship's push-failure message must carry
    the stderr text, not an empty tail."""
    flow_dir = _shippable(tmp_path, slug="pushstderr")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    class _Proc:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd[1:])  # drop the leading "git"
        if joined == "push":
            return _Proc(1, "", "remote: permission denied\nfatal: unable to push")
        if joined.startswith("rev-parse --abbrev-ref"):
            return _Proc(0, "main\n")
        if joined.startswith("rev-parse"):
            return _Proc(0, "f" * 40 + "\n")
        return _Proc(0, "")

    monkeypatch.setattr(fr.sp.subprocess, "run", fake_run)
    with pytest.raises(fr.ShipExecuteError) as exc:
        fr.execute_ship(flow_dir, _ship_action(), now=NOW)
    assert "permission denied" in str(exc.value)


def test_ship_execute_runs_preflight_itself_when_none_was_recorded(tmp_path,
                                                                    monkeypatch):
    """Finding 1 (2026-08-17 review): a driver with no preflight recorded at
    all is exactly the shape a sweep-opened G4 leaves behind (apply_match
    opens the gate via cmd_advance and never runs the checks), so this used
    to dead-end execute_ship at ShipExecuteError permanently: cmd_ship's
    reopen path needs driver["held"], which a sweep-opened gate never sets.
    execute_ship must run the checks itself first instead of refusing, and
    still complete the merge using the freshly-computed dict."""
    flow_dir = _shippable(tmp_path, slug="nopreflight")
    driver = fr.load_driver(flow_dir, halt=False)
    driver["preflight"] = None
    fr.write_driver(flow_dir, driver)
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    info = fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)

    assert calls  # the checks actually ran rather than being skipped
    driver = fr.load_driver(flow_dir, halt=False)
    assert isinstance(driver.get("preflight"), dict) and driver["preflight"]
    assert driver["shipped"] is True
    assert info["merge_sha"] == "f" * 40


def test_ship_execute_refuses_when_revert_point_is_missing(tmp_path,
                                                            monkeypatch):
    """Success criterion 4: no merge without a recorded pre-merge sha. A
    preflight dict that IS present but whose revert_point degraded to empty
    (ship_preflight's git calls never raise) must still refuse before the
    merge, not coerce the empty value forward."""
    flow_dir = _shippable(tmp_path, slug="norevertpoint")
    driver = fr.load_driver(flow_dir, halt=False)
    driver["preflight"]["revert_point"] = ""
    fr.write_driver(flow_dir, driver)
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    with pytest.raises(fr.ShipExecuteError):
        fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)
    assert not any(c[0].startswith("merge") or c[0] == "push" for c in calls)
    assert fr.load_driver(flow_dir, halt=False).get("shipped") is not True


def test_ship_execute_records_a_freshly_read_revert_point_not_the_stale_one(
        tmp_path, monkeypatch):
    """Finding 3 (2026-08-17 review): the preflight dict's revert_point is
    read at gate-open time and can be stale by the time G4 is actually
    decided ship, since the target branch may have moved in between. The
    revert instruction (`git revert -m 1 <merge_sha>`) does not depend on it,
    but the RECORDED revert_point must be the target's actual pre-merge sha
    for this specific merge, not whatever preflight saw earlier."""
    flow_dir = _shippable(tmp_path, slug="stalerevert")
    stale = fr.load_driver(flow_dir, halt=False)
    assert stale["preflight"]["revert_point"] == "0" * 40  # the stale value
    fake_git, _calls = _git_recorder({
        "rev-parse --abbrev-ref": (0, "main\n"),
        "rev-parse main": (0, "e" * 40 + "\n"),   # freshly-read target tip
        "rev-parse HEAD": (0, "f" * 40 + "\n"),   # post-merge sha
    })
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    info = fr.execute_ship(flow_dir, _ship_action(), now=NOW, push=False)

    assert info["revert_point"] == "e" * 40
    assert info["revert_point"] != "0" * 40
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["ship"]["revert_point"] == "e" * 40


def test_the_receipt_detail_carries_the_merge_sha_for_unship_to_find(tmp_path,
                                                                    monkeypatch):
    flow_dir = _shippable(tmp_path, slug="detail")
    # Distinct target-tip vs. merge-sha values, so the receipt is proven to
    # carry the freshly-read revert_point (Finding 3), not the stale one
    # baked into _shippable's preflight fixture ("0" * 40).
    fake_git, _calls = _git_recorder({
        "rev-parse --abbrev-ref": (0, "main\n"),
        "rev-parse main": (0, "e" * 40 + "\n"),
        "rev-parse HEAD": (0, "f" * 40 + "\n"),
    })
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")

    fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW, stage="execute",
                    push=False)
    receipt = [e for e in fr.read_log(flow_dir)
               if e["action"] == fr.SHIP_RECEIPT_ACTION][-1]
    assert "merge_sha=" + "f" * 40 in receipt["detail"]
    assert "revert_point=" + "e" * 40 in receipt["detail"]
    # _log_entry truncates detail at 240 chars, so both shas must survive it
    assert len(receipt["detail"]) <= 240


def test_ship_execute_holds_the_flow_lock_for_both_git_calls(tmp_path,
                                                             monkeypatch):
    """The lock is acquired by execute_once and released in its finally; both
    git calls happen inside it, which is what this asserts."""
    import foreman_lock as fl_mod
    flow_dir = _shippable(tmp_path, slug="locked")
    seen = []

    def fake_git(args, cwd):
        seen.append(fl_mod.read_lock(flow_dir) is not None)
        if " ".join(args).startswith("rev-parse --abbrev-ref"):
            return 0, "main\n"
        if " ".join(args).startswith("rev-parse"):
            return 0, "f" * 40 + "\n"
        return 0, ""

    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW, stage="execute")
    assert seen and all(seen)


def test_cowork_still_refuses_ship_execute(tmp_path):
    """The entire fence this slice is allowed: no platform check, no session
    shape check, just the existing COWORK_FORBIDDEN membership."""
    facts = fr.HostFacts(cowork=True)
    decision = fr.decide(_ship_action(), facts)
    assert decision.execute is False
    assert decision.reason == "cowork"
    assert fr.COWORK_FORBIDDEN == frozenset({"sprint_launch", "ship_execute"})


# --- slice c: the ship / unship subcommands ---------------------------------

def _held(tmp_path, slug="heldflow"):
    """A flow whose G4 was answered `hold`: phase learn, driver['held'] set."""
    flow_dir = _shippable(tmp_path, slug=slug)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["held"] = {"reason": "not yet", "ts": NOW}
    fr.write_driver(flow_dir, driver)
    flow = ft.load_flow(flow_dir)
    flow["gate"] = None
    flow["current_step"] = {"index": 0, "of": 1, "phase": "learn"}
    flow["status"] = "running"
    ft.write_flow(flow_dir, flow)
    return flow_dir


def test_ship_reopens_a_fresh_g4_and_clears_the_held_flag(tmp_path, monkeypatch):
    flow_dir = _held(tmp_path)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(
        fr.sp, "run_preflight",
        lambda flow, sprint, repo_root, diff_budget_lines=800, spec_text=None: {
            "worktree_branch": "sprint/heldflow", "revert_point": "1" * 40,
            "clean_tree": True})
    monkeypatch.setattr(fr.sp, "git", lambda args, cwd: (0, "sprint/heldflow\n"))

    flow = fr.cmd_ship(flow_dir, now=NOW)
    assert flow["gate"]["id"] == "G4"
    assert flow["gate"]["status"] == "awaiting-owner"
    driver = fr.load_driver(flow_dir, halt=False)
    assert not driver.get("held")
    assert driver["preflight"]["revert_point"] == "1" * 40


def test_ship_refuses_when_no_hold_is_pending(tmp_path):
    flow_dir = _shippable(tmp_path, slug="nohold")
    with pytest.raises(fr.ShipReopenError) as exc:
        fr.cmd_ship(flow_dir, now=NOW)
    assert "hold" in str(exc.value)


def test_ship_refuses_when_the_branch_no_longer_exists(tmp_path, monkeypatch):
    flow_dir = _held(tmp_path, slug="gonebranch")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(fr.sp, "git", lambda args, cwd: (1, ""))
    with pytest.raises(fr.ShipReopenError) as exc:
        fr.cmd_ship(flow_dir, now=NOW)
    assert "sprint/gonebranch" in str(exc.value)


def test_ship_refuses_while_another_gate_is_open(tmp_path, monkeypatch):
    """Interpretation decision 7: after a hold the phase is learn, so a G3 gate
    may be awaiting a decision. Reopening G4 would clobber it and its token."""
    flow_dir = _held(tmp_path, slug="g3open")
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G3", 0, NOW)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    monkeypatch.setattr(fr.sp, "git", lambda args, cwd: (0, "sprint/g3open\n"))
    with pytest.raises(fr.ShipReopenError) as exc:
        fr.cmd_ship(flow_dir, now=NOW)
    assert "G3" in str(exc.value)


def test_unship_prints_the_exact_revert_line_for_the_recorded_merge(tmp_path,
                                                                    monkeypatch):
    flow_dir = _shippable(tmp_path, slug="unshipme")
    fake_git, _calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW, stage="execute",
                    push=False)

    text = fr.cmd_unship(flow_dir)
    assert "git revert -m 1 " + "f" * 40 in text
    assert "sprint/unshipme" in text
    assert "C:\\work\\znd" in text


def test_unship_prints_nothing_to_run_without_a_ship_receipt(tmp_path):
    flow_dir = _shippable(tmp_path, slug="nevershipped")
    text = fr.cmd_unship(flow_dir)
    assert "git revert" not in text
    assert "znd/nevershipped" in text


def test_unship_never_runs_git(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="readonly")
    fake_git, calls = _git_recorder()
    monkeypatch.setattr(fr.sp, "git", fake_git)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW, stage="execute",
                    push=False)
    calls.clear()
    fr.cmd_unship(flow_dir)
    assert calls == []


def test_unship_uses_the_most_recent_ship_receipt(tmp_path, monkeypatch):
    flow_dir = _shippable(tmp_path, slug="twoships")
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: "C:\\work\\znd")
    for sha in ("a" * 40, "b" * 40):
        fake_git, _calls = _git_recorder({"rev-parse --abbrev-ref": (0, "main\n"),
                                          "rev-parse": (0, sha + "\n")})
        monkeypatch.setattr(fr.sp, "git", fake_git)
        driver = fr.load_driver(flow_dir, halt=False)
        driver["shipped"] = False
        fr.write_driver(flow_dir, driver)
        fr.execute_once(flow_dir, _ship_action(), "sess", now=NOW,
                        stage="execute", push=False)
    assert "b" * 40 in fr.cmd_unship(flow_dir)


def test_the_parser_knows_ship_and_unship():
    parser = fr._build_arg_parser()
    for cmd in ("ship", "unship"):
        args = parser.parse_args([cmd, "znd/x"])
        assert args.cmd == cmd
        assert args.ref == "znd/x"


# --- Task 3: execute_brief_write ---------------------------------------------

def _closed_flow(tmp_path, project="dsi", slug="payoff-books"):
    """A flow at stage closed, which foreman_plan._tail walks."""
    flow_dir = tmp_path / "flows" / project / slug
    flow_dir.mkdir(parents=True)
    flow = ft.new_flow(project, slug, "idea", NOW)
    flow["stage"] = "closed"
    flow["current_step"] = {"index": 2, "of": 2, "phase": "learn",
                            "title": "step two"}
    flow["gate"] = None
    (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
    for entry in (
        ft._log_entry(NOW, "execute", "human", "gate_decided", detail="G2 approve"),
        ft._log_entry(NOW, "execute", "orchestrator", "sprint_swept",
                      detail="dsi__x__1 status=passing", ref="dsi__x__1"),
        ft._log_entry(NOW, "execute", "orchestrator", "sprint_swept",
                      detail="dsi__x__2 status=passing", ref="dsi__x__2"),
    ):
        ft.append_log(flow_dir, entry)
    return flow_dir


def _tail_action(kind):
    return fp.NextAction(kind=kind, why="w", blocking="", command="",
                         gate_id=None, step_index=2, role="learn_summarise",
                         guidance_key=kind)


_orig_brief = fr.execute_brief_write     # captured before any monkeypatch


def test_execute_brief_write_writes_the_closed_named_brief(tmp_path):
    """Success criterion 3: the naming pattern and a related section built
    from the flow's own log."""
    flow_dir = _closed_flow(tmp_path)
    assets = tmp_path / "dev" / "dsi" / "04_assets"
    assets.mkdir(parents=True)
    (assets / "hub_agent_update_2026-08-20_dsi_payoff-books_step1.md").write_text(
        "step one brief", encoding="utf-8")
    path = fr.execute_brief_write(flow_dir, _tail_action("brief_write"),
                                  now=NOW, dev_root=str(tmp_path / "dev"))
    assert path.endswith("hub_agent_update_2026-08-02_payoff-books_closed.md")
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert "## 8. Related files" in text
    assert "hub_agent_update_2026-08-20_dsi_payoff-books_step1.md" in text
    assert "step 2 of dsi/payoff-books: no step brief on disk" in text
    assert "closed" in text.lower()
    assert "\u2014" not in text                      # no em dash, house rule
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["tail"]["brief_written"] is True
    assert driver["tail"]["brief_path"] == path


def test_execute_brief_write_leaves_the_tail_untouched_when_the_write_fails(
        tmp_path, monkeypatch):
    """Failure path, spec item 6: the same action must retry next chain run."""
    flow_dir = _closed_flow(tmp_path)
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    decision = fr.execute_once(flow_dir, _tail_action("brief_write"), "sess",
                               now=NOW, stage="closed")
    monkeypatch.undo()
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert not fr.load_driver(flow_dir, halt=False).get("tail")


def test_brief_write_is_now_executable():
    assert "brief_write" in fr.EXECUTABLE_KINDS
    assert "brief_write" not in fr.NOT_YET_EXECUTABLE
    assert fr.decide(_tail_action("brief_write"), fr.HostFacts()).execute is True


# --- Task 4: execute_hub_ingest ----------------------------------------------

def test_execute_hub_ingest_returns_the_hub_update_line_and_marks_the_tail(
        tmp_path):
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True,
                      "brief_path": r"C:\work\dsi\04_assets\b.md"}
    fr.write_driver(flow_dir, driver)
    line = fr.execute_hub_ingest(flow_dir, _tail_action("hub_ingest"), now=NOW)
    assert line == "/hub-update"
    tail = fr.load_driver(flow_dir, halt=False)["tail"]
    assert tail["hub_ingested"] is True
    assert tail["brief_written"] is True         # earlier step not disturbed


def test_execute_hub_ingest_raises_when_no_brief_was_recorded(tmp_path):
    """Failure path: ingesting before a brief exists would propagate nothing
    and then mark the tail step done, so the flow would close having lost its
    own retrospective."""
    flow_dir = _closed_flow(tmp_path)
    decision = fr.execute_once(flow_dir, _tail_action("hub_ingest"), "sess",
                               now=NOW, stage="closed")
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert not fr.load_driver(flow_dir, halt=False).get("tail")


def test_execute_hub_ingest_records_the_brief_path_in_its_receipt(tmp_path):
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True,
                      "brief_path": r"C:\work\dsi\04_assets\b.md"}
    fr.write_driver(flow_dir, driver)
    fr.execute_once(flow_dir, _tail_action("hub_ingest"), "sess", now=NOW,
                    stage="closed")
    receipts = [e for e in fr.read_log(flow_dir)
                if e.get("action") == "driver_hub_ingest"]
    assert receipts and receipts[-1]["detail"] == "/hub-update"


def test_hub_ingest_makes_no_subprocess_call(tmp_path, monkeypatch):
    """Success criterion 4, enforced rather than reviewed."""
    import subprocess
    def _forbidden(*a, **k):
        raise AssertionError("hub_ingest must never run a subprocess")
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True, "brief_path": "x.md"}
    fr.write_driver(flow_dir, driver)
    assert fr.execute_hub_ingest(flow_dir, _tail_action("hub_ingest"),
                                 now=NOW) == "/hub-update"


def test_hub_ingest_is_now_executable():
    assert "hub_ingest" in fr.EXECUTABLE_KINDS
    assert "hub_ingest" not in fr.NOT_YET_EXECUTABLE
    assert fr.decide(_tail_action("hub_ingest"), fr.HostFacts()).execute is True


# --- Task 5: execute_hub_refresh ---------------------------------------------

def test_execute_hub_refresh_runs_both_scripts_in_the_command_center(tmp_path,
                                                                     monkeypatch):
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True, "brief_path": "b.md",
                      "hub_ingested": True}
    fr.write_driver(flow_dir, driver)
    calls = []
    def _fake(script, args, cwd):
        calls.append((script, list(args), str(cwd)))
        return 0, "ok"
    monkeypatch.setattr(fr, "run_refresh_script", _fake)
    result = fr.execute_hub_refresh(flow_dir, _tail_action("hub_refresh"),
                                    now=NOW, hub_root=str(tmp_path / "hub"))
    assert result == {"assemble.py": 0, "render_mission_deck.py": 0}
    assert [c[0] for c in calls] == ["assemble.py", "render_mission_deck.py"]
    assert calls[0][1] == ["--force"]
    assert calls[1][1] == []
    for _script, _args, cwd in calls:
        assert cwd.endswith(str(pathlib.Path("04_engineering") / "command_center"))
    tail = fr.load_driver(flow_dir, halt=False)["tail"]
    assert tail["hub_refreshed"] is True
    assert tail["hub_ingested"] is True          # earlier step not disturbed


def test_execute_hub_refresh_never_runs_notify(tmp_path, monkeypatch):
    """Out-of-scope list: notify.py and refresh_and_notify.cmd stay unrun."""
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True, "brief_path": "b.md",
                      "hub_ingested": True}
    fr.write_driver(flow_dir, driver)
    seen = []
    monkeypatch.setattr(fr, "run_refresh_script",
                        lambda s, a, c: (seen.append(s), (0, ""))[1])
    fr.execute_hub_refresh(flow_dir, _tail_action("hub_refresh"), now=NOW,
                           hub_root=str(tmp_path / "hub"))
    assert "notify.py" not in seen
    assert "refresh_and_notify.cmd" not in seen


def test_execute_hub_refresh_leaves_the_tail_unset_when_a_script_fails(
        tmp_path, monkeypatch):
    """Success criterion 5. Also proves the SECOND script's failure does not
    get papered over by the first one's success."""
    flow_dir = _closed_flow(tmp_path)
    driver = fr.load_driver(flow_dir, halt=False)
    driver["tail"] = {"brief_written": True, "brief_path": "b.md",
                      "hub_ingested": True}
    fr.write_driver(flow_dir, driver)
    def _second_fails(script, args, cwd):
        return (0, "ok") if script == "assemble.py" else (2, "traceback")
    monkeypatch.setattr(fr, "run_refresh_script", _second_fails)
    decision = fr.execute_once(flow_dir, _tail_action("hub_refresh"), "sess",
                               now=NOW, stage="closed")
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert "render_mission_deck.py" in decision.detail
    tail = fr.load_driver(flow_dir, halt=False)["tail"]
    assert "hub_refreshed" not in tail
    assert tail["hub_ingested"] is True          # nothing rolled back either


def test_execute_hub_refresh_raises_when_hub_was_not_ingested(tmp_path):
    """Mirrors execute_hub_ingest's guard: refreshing before ingest would run
    the driver ahead of the tail's recorded order."""
    flow_dir = _closed_flow(tmp_path)
    decision = fr.execute_once(flow_dir, _tail_action("hub_refresh"), "sess",
                               now=NOW, stage="closed")
    assert decision.execute is False
    assert decision.reason == "execute_error"
    assert not fr.load_driver(flow_dir, halt=False).get("tail")
    assert decision.detail == (
        "the write path raised and nothing further was attempted: "
        "refusing to refresh dsi/payoff-books: the driver records no "
        "hub_ingest, so refreshing would run ahead of it")


def test_run_refresh_script_is_bounded_and_utf8(monkeypatch, tmp_path):
    """The existing Windows-locale subprocess pattern, asserted not assumed."""
    captured = {}
    import subprocess
    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""
    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _Proc()
    monkeypatch.setattr(subprocess, "run", _fake_run)
    code, out = fr.run_refresh_script("assemble.py", ["--force"], tmp_path)
    assert (code, out) == (0, "ok")
    assert captured["cmd"][1:] == ["assemble.py", "--force"]
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["timeout"] == fr.REFRESH_TIMEOUT_SECONDS


def test_hub_refresh_is_now_executable_and_nothing_is_parked():
    """Spec success criterion 1, final state."""
    assert fr.NOT_YET_EXECUTABLE == frozenset()
    assert fr.EXECUTABLE_KINDS == (
        "flow_advance", "gate_consume", "sprint_launch", "ship_preflight",
        "ship_execute", "sprint_recover", "brief_write", "hub_ingest",
        "hub_refresh")
    for kind in ("sprint_recover", "brief_write", "hub_ingest", "hub_refresh"):
        assert kind not in fr.NOT_YET_EXECUTABLE


def test_the_whole_tail_runs_through_the_real_executors_and_closes(tmp_path,
                                                                   monkeypatch):
    """Success criterion 6: test_complete_tail_closes_the_flow's plan-layer
    assertion, now reached through the real executor path."""
    flow_dir = _closed_flow(tmp_path)
    (tmp_path / "dev" / "dsi" / "04_assets").mkdir(parents=True)
    monkeypatch.setattr(fr, "run_refresh_script", lambda s, a, c: (0, "ok"))
    monkeypatch.setattr(fr, "execute_brief_write",
                        lambda fd, act, now=None: _orig_brief(
                            fd, act, now=now, dev_root=str(tmp_path / "dev")))
    result = fr.run_chain(flow_dir, sprints_root=str(tmp_path / "sprints"),
                          now=NOW, render=False,
                          operator_path=tmp_path / "operator.json")
    assert result.executed == ("brief_write", "hub_ingest", "hub_refresh")
    assert result.stopped.action.kind == "flow_close"
    assert result.stopped.reason == "complete"
    tail = fr.load_driver(flow_dir, halt=False)["tail"]
    assert tail["brief_written"] and tail["hub_ingested"] and tail["hub_refreshed"]


def test_a_tail_failure_marks_neither_a_later_nor_an_earlier_step_done(
        tmp_path, monkeypatch):
    """The invariant Opus verifies blind (spec item 6, last sentence)."""
    flow_dir = _closed_flow(tmp_path)
    (tmp_path / "dev" / "dsi" / "04_assets").mkdir(parents=True)
    monkeypatch.setattr(fr, "execute_brief_write",
                        lambda fd, act, now=None: _orig_brief(
                            fd, act, now=now, dev_root=str(tmp_path / "dev")))
    monkeypatch.setattr(fr, "run_refresh_script", lambda s, a, c: (1, "boom"))
    result = fr.run_chain(flow_dir, sprints_root=str(tmp_path / "sprints"),
                          now=NOW, render=False)
    assert result.executed == ("brief_write", "hub_ingest")
    assert result.stopped.reason == "execute_error"
    tail = fr.load_driver(flow_dir, halt=False)["tail"]
    assert tail["brief_written"] is True        # earlier step stays done
    assert tail["hub_ingested"] is True         # earlier step stays done
    assert "hub_refreshed" not in tail          # the failed step is NOT done
    # And re-running comes back to the same action, not past it.
    again = fr.run_chain(flow_dir, sprints_root=str(tmp_path / "sprints"),
                         now=NOW, render=False)
    assert again.executed == ()
    assert again.stopped.action.kind == "hub_refresh"
    assert again.stopped.reason == "execute_error"


# --- slice e: operator.json and the completion counter -----------------------

def test_load_operator_returns_the_defaults_when_the_file_is_absent(tmp_path):
    op = fr.load_operator(tmp_path / "nope.json")
    assert op == {"completions": 0, "guide": None}


def test_load_operator_returns_the_defaults_when_the_file_is_malformed(tmp_path):
    path = tmp_path / "operator.json"
    path.write_text("{not json", encoding="utf-8")
    assert fr.load_operator(path) == {"completions": 0, "guide": None}


def test_load_operator_does_not_overwrite_a_recorded_value(tmp_path):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps({"completions": 7}), encoding="utf-8")
    op = fr.load_operator(path)
    assert op["completions"] == 7
    assert op["guide"] is None


def test_write_operator_round_trips(tmp_path):
    path = tmp_path / "operator.json"
    fr.write_operator({"completions": 4, "guide": "terse"}, path)
    assert fr.load_operator(path) == {"completions": 4, "guide": "terse"}


def test_the_shipped_operator_json_parses_and_carries_both_fields():
    op = fr.load_operator()
    assert isinstance(op["completions"], int)
    assert op["guide"] is None or op["guide"] in fp.GUIDE_LEVELS


def test_default_driver_carries_the_completion_counted_marker():
    assert fr.default_driver(now=NOW)["completion_counted"] is False


def _closed_flow_tree(tmp_path, slug="counted"):
    """A flow already at stage closed with every tail step recorded, so the
    very first next_action is flow_close and the chain stops at complete
    without executing anything."""
    flows_root, flow_dir = _tree(tmp_path, slug=slug)
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "closed"
    flow["status"] = "done"
    ft.write_flow(flow_dir, flow)
    driver = fr.default_driver(now=NOW)
    driver["tail"] = {"brief_written": True, "hub_ingested": True,
                      "hub_refreshed": True}
    fr.write_driver(flow_dir, driver)
    return flows_root, flow_dir


def test_a_completed_flow_increments_completions_by_exactly_one(tmp_path):
    flows_root, flow_dir = _closed_flow_tree(tmp_path)
    op_path = tmp_path / "operator.json"
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False,
                          operator_path=op_path)
    assert result.stopped.reason == "complete"
    assert fr.load_operator(op_path)["completions"] == 1


def test_a_second_tick_over_an_already_closed_flow_does_not_increment_again(
        tmp_path):
    """The whole point of the completion_counted marker: the chain reaches the
    complete stop on every subsequent run, and only the first one counts."""
    flows_root, flow_dir = _closed_flow_tree(tmp_path)
    op_path = tmp_path / "operator.json"
    for _ in range(3):
        fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                     session_id="s1", now=NOW, render=False,
                     operator_path=op_path)
    assert fr.load_operator(op_path)["completions"] == 1
    assert fr.load_driver(flow_dir, halt=False)["completion_counted"] is True


def test_two_different_flows_each_count_once(tmp_path):
    op_path = tmp_path / "operator.json"
    for slug in ("counted-a", "counted-b"):
        flows_root, flow_dir = _closed_flow_tree(tmp_path, slug=slug)
        fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                     session_id="s1", now=NOW, render=False,
                     operator_path=op_path)
    assert fr.load_operator(op_path)["completions"] == 2


def test_a_chain_that_stops_short_of_complete_does_not_increment(tmp_path):
    """A gate_await stop is not a completion, and the operator file is not even
    written when nothing changed."""
    flows_root, flow_dir = _tree(tmp_path, slug="not-done")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "learn"}
    ft.write_flow(flow_dir, flow)
    ft.persist_open_gate(flow_dir, ft.load_flow(flow_dir), "G3", 0,
                         now=NOW, nonce="7QF3")
    op_path = tmp_path / "operator.json"
    result = fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints",
                          session_id="s1", now=NOW, render=False,
                          operator_path=op_path)
    assert result.stopped.action.kind == "gate_await"
    assert not op_path.exists()


def test_a_dry_run_over_a_closed_flow_never_increments(tmp_path):
    flows_root, flow_dir = _closed_flow_tree(tmp_path, slug="dry")
    op_path = tmp_path / "operator.json"
    fr.run_chain(flow_dir, sprints_root=tmp_path / "sprints", session_id="s1",
                 now=NOW, render=False, dry_run=True, operator_path=op_path)
    assert not op_path.exists()
    assert fr.load_driver(flow_dir, halt=False)["completion_counted"] is False


# --- slice e: the --guide flag ----------------------------------------------

def test_the_run_parser_accepts_the_four_guide_levels():
    parser = fr._build_arg_parser()
    for level in fp.GUIDE_LEVELS:
        args = parser.parse_args(["run", "dsi/x", "--guide", level])
        assert args.guide == level


def test_guide_defaults_to_none_so_the_stored_rung_decides():
    parser = fr._build_arg_parser()
    assert parser.parse_args(["run", "dsi/x"]).guide is None


def test_an_unknown_guide_level_is_rejected_by_the_parser():
    parser = fr._build_arg_parser()
    try:
        parser.parse_args(["run", "dsi/x", "--guide", "loud"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--guide loud must not parse")


def _stopped_result(kind="gate_await", guidance_key="gate_await.g1",
                    why="G1 is open and undecided"):
    action = fp.NextAction(kind=kind, why=why, blocking="human", command="",
                           gate_id="G1", step_index=0, role="none",
                           guidance_key=guidance_key)
    decision = fr.Decision(action, False, "blocking", "it blocks on human")
    return fr.ChainResult((), decision, 0, 0)


def test_guidance_block_renders_against_the_actions_key_not_the_stop_reason():
    """Decision.guidance_key would be 'stop.blocking' here, which no fragment
    is ever filed against. The action's key is the one that gets a fragment."""
    result = _stopped_result()
    block = fr.guidance_block(result, None, {"completions": 0}, "coached")
    assert "waiting on you" in block   # from the real gate_await.g1 fragment


def test_guidance_block_is_empty_at_silent():
    result = _stopped_result()
    assert fr.guidance_block(result, None, {"completions": 0}, "silent") == ""


def test_the_stored_guide_override_is_honoured_when_no_flag_is_typed():
    result = _stopped_result()
    operator = {"completions": 0, "guide": "terse"}
    assert fr.guidance_block(result, None, operator, None) == result.stopped.action.why


def test_an_explicit_flag_beats_the_stored_override_in_both_directions():
    result = _stopped_result()
    quiet = {"completions": 0, "guide": "silent"}
    assert fr.guidance_block(result, None, quiet, "terse") == result.stopped.action.why
    loud = {"completions": 9, "guide": "teaching"}
    assert fr.guidance_block(result, None, loud, "silent") == ""


def test_a_fluent_operator_gets_terse_and_a_new_one_gets_coached():
    result = _stopped_result()
    fluent = fr.guidance_block(result, None, {"completions": 3}, None)
    fresh = fr.guidance_block(result, None, {"completions": 0}, None)
    assert fluent == result.stopped.action.why
    assert fresh != fluent
    assert "waiting on you" in fresh
