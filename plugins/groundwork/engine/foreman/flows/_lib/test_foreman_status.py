"""Tests for foreman_status.py. Plain pytest, stdlib only, hermetic.

The pure section is tested with literal fixtures and an injected `now`; the
host section (Task 2 onward) is tested over tmp_path trees built with
flow_transition, the same way test_render_flows.py builds its trees.
"""
import foreman_plan as fp
import foreman_status as fst


# --- fixtures ---------------------------------------------------------------

NOW = "2026-08-01T12:00:00Z"
SPEC_PATH = r"C:\work\dsi\docs\superpowers\specs\2026-07-18-payoff-books-design.md"
EMITTED = "/sprint dsi " + SPEC_PATH + " --model-map spec=opus,plan=opus," \
          "dev=sonnet,test=haiku,verify=opus,iterate=sonnet,report=haiku"


def _flow(stage="execute", status="running", phase="sprint", index=0, of=3,
          gate=None, steps=None):
    return {
        "schema_version": 1,
        "flow_id": "dsi__payoff-books__20260718T091403Z",
        "project": "dsi", "slug": "payoff-books",
        "idea": "measure payoff of the books",
        "stage": stage, "status": status,
        "current_step": {"index": index, "of": of, "phase": phase},
        "steps": steps if steps is not None else [
            {"index": i, "title": "step {}".format(i), "status": "pending"}
            for i in range(of)],
        "model_hint": {}, "gate": gate, "spec_path": SPEC_PATH,
        "created_at": "2026-07-18T09:14:03Z", "updated_at": "2026-08-01T11:00:00Z",
    }


def _gate(gate_id="G3", step_index=0, decided=None, opened_at="2026-08-01T11:38:00Z"):
    gate = {"id": gate_id, "step_index": step_index, "status": "awaiting-owner",
            "decision": None, "notified": False, "opened_at": opened_at,
            "token": "FLOW-DSI-payoff-books-{}-X-7QF3".format(gate_id)}
    if decided is not None:
        gate["status"] = "decided"
        gate["decision"] = {"verdict": decided, "note": "", "surface": "session",
                            "decided_at": NOW, "message_ref": None}
    return gate


def _entry(ts, action, detail="", actor="orchestrator", outcome="info"):
    return {"ts": ts, "stage": "execute", "actor": actor, "action": action,
            "outcome": outcome, "detail": detail, "ref": None}


def _sprint_json(status="running", created="2026-07-18T10:00:00Z",
                 slug="payoff-books"):
    return {"schema_version": 2, "sprint_id": "dsi__{}__x".format(slug),
            "project": "dsi", "slug": slug, "spec_path": SPEC_PATH,
            "worktree_branch": "sprint/" + slug, "status": status,
            "phase": "dev", "iteration": 2, "max_iterations": 5,
            "created_at": created,
            "last_test_result": {"command": "pytest -q", "exit_code": 0,
                                 "summary": "47 passed", "at": created},
            "last_verify_result": None}


# --- resolve_sprint ---------------------------------------------------------

def test_resolve_sprint_matches_the_emitted_spec_path():
    log = [_entry("2026-07-18T09:59:00Z", "sprint_emitted", EMITTED)]
    sprint = _sprint_json()
    assert fst.resolve_sprint(_flow(), log, [sprint]) is sprint


def test_resolve_sprint_returns_none_without_an_emit_receipt():
    assert fst.resolve_sprint(_flow(), [], [_sprint_json()]) is None


def test_resolve_sprint_ignores_a_sprint_created_before_the_emit():
    log = [_entry("2026-07-18T11:00:00Z", "sprint_emitted", EMITTED)]
    stale = _sprint_json(created="2026-07-18T10:00:00Z")
    assert fst.resolve_sprint(_flow(), log, [stale]) is None


def test_resolve_sprint_matches_a_running_sprint_unlike_the_terminal_sweep():
    """The sweep matcher deliberately filters to terminal statuses; the status
    surface must see the sprint that is running right now."""
    log = [_entry("2026-07-18T09:59:00Z", "sprint_emitted", EMITTED)]
    running = _sprint_json(status="running")
    assert fst.resolve_sprint(_flow(), log, [running]) is running


def test_resolve_sprint_takes_the_newest_of_several_matches():
    log = [_entry("2026-07-18T09:59:00Z", "sprint_emitted", EMITTED)]
    old = _sprint_json(created="2026-07-18T10:00:00Z", slug="a")
    new = _sprint_json(created="2026-07-19T10:00:00Z", slug="b")
    assert fst.resolve_sprint(_flow(), log, [old, new]) is new


def test_resolve_sprint_requires_the_same_project():
    log = [_entry("2026-07-18T09:59:00Z", "sprint_emitted", EMITTED)]
    other = _sprint_json()
    other["project"] = "jarvis"
    assert fst.resolve_sprint(_flow(), log, [other]) is None


# --- gate_history -----------------------------------------------------------

def test_gate_history_covers_all_four_ids_in_order():
    labels = fst.gate_history([], None, NOW)
    assert tuple(g for g, _ in labels) == ("G1", "G2", "G3", "G4")
    assert all(label == "-" for _, label in labels)


def test_gate_history_shows_a_decided_gate_with_its_verdict():
    log = [_entry("2026-07-31T15:04:00Z", "gate_decided", "G1 verdict=approve",
                  actor="session", outcome="pass")]
    labels = dict(fst.gate_history(log, None, NOW))
    assert labels["G1"] == "approve 07-31"


def test_gate_history_adds_the_time_for_a_decision_made_today():
    log = [_entry("2026-08-01T10:04:00Z", "gate_decided", "G2 verdict=approve",
                  actor="session", outcome="pass")]
    labels = dict(fst.gate_history(log, None, NOW))
    assert labels["G2"] == "approve 08-01 10:04"


def test_gate_history_marks_the_open_gate_with_its_wait():
    gate = _gate("G3", opened_at="2026-08-01T11:38:00Z")
    labels = dict(fst.gate_history([], gate, NOW))
    assert labels["G3"] == "awaiting 22m"


def test_gate_history_uses_the_latest_decision_per_gate():
    log = [_entry("2026-07-30T09:00:00Z", "gate_decided", "G3 verdict=adapt"),
           _entry("2026-07-31T09:00:00Z", "gate_decided", "G3 verdict=proceed")]
    labels = dict(fst.gate_history(log, None, NOW))
    assert labels["G3"] == "proceed 07-31"


# --- blocked_since ----------------------------------------------------------

def test_blocked_since_uses_the_gate_open_time_for_a_human_block():
    gate = _gate("G3", opened_at="2026-08-01T11:38:00Z")
    flow = _flow(phase="learn", status="awaiting-owner", gate=gate)
    action = fp.next_action(flow, None, {})
    assert action.blocking == "human"
    since, secs = fst.blocked_since(flow, [], action, NOW)
    assert since == "2026-08-01T11:38:00Z"
    assert secs == 22 * 60


def test_blocked_since_uses_the_newest_receipt_when_no_gate_is_open():
    log = [_entry("2026-08-01T09:00:00Z", "advanced"),
           _entry("2026-08-01T11:20:00Z", "sprint_emitted", EMITTED)]
    flow = _flow()
    action = fp.next_action(flow, _sprint_json(), {})
    since, secs = fst.blocked_since(flow, log, action, NOW)
    assert since == "2026-08-01T11:20:00Z"
    assert secs == 40 * 60


def test_blocked_since_falls_back_to_updated_at_with_an_empty_log():
    flow = _flow()
    action = fp.next_action(flow, _sprint_json(), {})
    since, secs = fst.blocked_since(flow, [], action, NOW)
    assert since == flow["updated_at"]
    assert secs == 60 * 60


def test_blocked_since_survives_an_unparseable_timestamp():
    log = [_entry("not-a-timestamp", "advanced")]
    flow = _flow()
    action = fp.next_action(flow, _sprint_json(), {})
    since, secs = fst.blocked_since(flow, log, action, NOW)
    assert secs is None


# --- build_status -----------------------------------------------------------

def test_build_status_carries_the_next_action_fields_verbatim():
    flow = _flow()
    sprint = _sprint_json()
    action = fp.next_action(flow, sprint, {})
    status = fst.build_status(flow, [], sprint, {}, NOW)
    assert (status.kind, status.why, status.blocking, status.command,
            status.gate_id, status.guidance_key) == (
        action.kind, action.why, action.blocking, action.command,
        action.gate_id, action.guidance_key)


def test_build_status_reads_the_step_title_from_the_steps_array():
    flow = _flow(index=1, of=3)
    flow["steps"][1]["title"] = "board page"
    status = fst.build_status(flow, [], None, {}, NOW)
    assert status.step_title == "board page"
    assert (status.step_index, status.step_of) == (1, 3)


def test_build_status_summarises_the_sprint():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    assert status.sprint_slug == "payoff-books"
    assert status.sprint_status == "running"
    assert (status.iteration, status.max_iterations) == (2, 5)
    assert status.test_outcome == "pass"
    assert status.verify_outcome == ""
    assert status.cost_usd is None


def test_build_status_defaults_every_driver_field():
    status = fst.build_status(_flow(), [], None, {}, NOW)
    assert status.guide == "coached"
    assert status.rung is None
    assert status.chain_depth is None
    assert status.chain_cap is None


def test_build_status_reflects_a_halt_flag_from_the_driver():
    status = fst.build_status(_flow(), [], None, {"halt": True}, NOW)
    assert status.kind == "halt"
    assert status.blocking == "fence"


def test_build_status_is_frozen_and_json_serialisable():
    import dataclasses
    import json
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    try:
        status.kind = "halt"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("FlowStatus must be frozen")
    json.dumps(dataclasses.asdict(status))  # must not raise


# --- ordering ---------------------------------------------------------------

def _status_with(blocking_kind, secs, project="p", slug="s", why=""):
    """A FlowStatus built by hand for ordering tests only."""
    return fst.FlowStatus(
        project=project, slug=slug, flow_dir="", stage="execute",
        status="running", step_index=0, step_of=1, step_title="", phase="",
        gates=(), sprint_slug="", sprint_status="", iteration=None,
        max_iterations=None, test_outcome="", verify_outcome="", cost_usd=None,
        kind="gate_await" if blocking_kind == "human" else "sprint_await",
        why=why, blocking=blocking_kind, command="", gate_id=None,
        guidance_key="", blocked_since="", blocked_seconds=secs,
        chain_depth=None, chain_cap=None, guide="coached", rung=None,
        updated_at="", error="")


def test_sort_puts_human_first_then_fence_then_agent():
    rows = [_status_with("agent", 10), _status_with("fence", 10),
            _status_with("", 10), _status_with("human", 10)]
    ordered = sorted(rows, key=fst.sort_key)
    assert [r.blocking for r in ordered] == ["human", "fence", "agent", ""]


def test_sort_orders_by_wait_descending_within_a_rank():
    rows = [_status_with("human", 60, slug="fresh"),
            _status_with("human", 6000, slug="stale")]
    ordered = sorted(rows, key=fst.sort_key)
    assert [r.slug for r in ordered] == ["stale", "fresh"]


def test_sort_treats_an_unknown_wait_as_zero_not_as_a_crash():
    rows = [_status_with("human", None, slug="unknown"),
            _status_with("human", 30, slug="known")]
    ordered = sorted(rows, key=fst.sort_key)
    assert [r.slug for r in ordered] == ["known", "unknown"]


def test_is_idle_is_true_for_flow_close_kind():
    closed = fst.build_status(
        _flow(stage="closed", status="closed"), [], None,
        {"tail": {"brief_written": True, "hub_ingested": True,
                  "hub_refreshed": True}}, NOW)
    assert closed.kind == "flow_close"
    assert fst.is_idle(closed) is True
    assert fst.is_idle(fst.build_status(_flow(), [], None, {}, NOW)) is False


def test_is_idle_treats_a_closed_flow_json_status_as_idle_without_a_driver_tail():
    """In the real repo no foreman.json exists yet (slice b's job), so a
    closed flow's kind never actually reaches flow_close: driver is {} and
    _tail returns the first undone tail step ('brief_write'). is_idle must
    still be True here, keyed off flow.json's own status field, or the board
    split, the queue filter and the (disabled) hook all stay dead on
    arrival."""
    closed = fst.build_status(_flow(stage="closed", status="closed"), [], None, {}, NOW)
    assert closed.kind != "flow_close"
    assert fst.is_idle(closed) is True


# --- Task 2: the host -------------------------------------------------------

import json
import pathlib

import flow_transition as ft


def _tree(tmp_path, project="dsi", slug="payoff-books", awaiting=False):
    """A real flow directory built through flow_transition, like test_render_flows."""
    flows_root = tmp_path / "flows"
    flows_root.mkdir(exist_ok=True)
    flow_dir, flow = ft.cmd_new(flows_root, project, "{} idea".format(slug),
                                slug=slug, now="2026-08-01T09:00:00Z")
    if awaiting:
        flow["stage"] = "execute"
        flow["current_step"] = {"index": 0, "of": 1, "phase": "learn"}
        flow["steps"] = [{"index": 0, "title": "only step", "status": "running"}]
        ft.write_flow(flow_dir, flow)
        flow = ft.load_flow(flow_dir)
        ft.persist_open_gate(flow_dir, flow, "G3", 0,
                             now="2026-08-01T11:38:00Z", nonce="7QF3")
    return flows_root, flow_dir


def test_load_driver_returns_empty_dict_when_foreman_json_is_absent(tmp_path):
    assert fst.load_driver(tmp_path, halt=False) == {"halt": False}


def test_load_driver_reads_the_advisory_record(tmp_path):
    (tmp_path / "foreman.json").write_text(
        json.dumps({"guide": "terse", "rung": 2, "chain_depth": 3,
                    "chain_cap": 12}), encoding="utf-8")
    driver = fst.load_driver(tmp_path, halt=False)
    assert driver["guide"] == "terse"
    assert driver["chain_depth"] == 3


def test_load_driver_survives_malformed_json(tmp_path):
    (tmp_path / "foreman.json").write_text("{not json", encoding="utf-8")
    assert fst.load_driver(tmp_path, halt=False) == {"halt": False}


def test_load_driver_sets_halt_from_the_kill_switch_argument(tmp_path):
    assert fst.load_driver(tmp_path, halt=True)["halt"] is True


def test_collect_returns_one_status_per_flow(tmp_path):
    flows_root, _ = _tree(tmp_path, slug="one")
    _tree(tmp_path, slug="two")
    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    assert sorted(r.slug for r in rows) == ["one", "two"]


def test_collect_sorts_the_human_blocked_flow_first(tmp_path):
    flows_root, _ = _tree(tmp_path, slug="running-one")
    _tree(tmp_path, slug="waiting-one", awaiting=True)
    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    assert rows[0].slug == "waiting-one"
    assert rows[0].blocking == "human"


def test_collect_isolates_a_flow_whose_next_action_raises(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="broken")
    _tree(tmp_path, slug="healthy")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["model_hint"] = {"not_a_phase": "opus"}   # emit_sprint_line raises
    ft.write_flow(flow_dir, flow)

    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    by_slug = {r.slug: r for r in rows}
    assert set(by_slug) == {"broken", "healthy"}
    assert by_slug["broken"].error
    assert by_slug["broken"].blocking == "fence"
    assert by_slug["healthy"].error == ""


def test_collect_tolerates_a_missing_flows_root(tmp_path):
    assert fst.collect(tmp_path / "nope", tmp_path / "sprints", now=NOW) == []


def test_collect_honours_the_halt_file(tmp_path, monkeypatch):
    flows_root, _ = _tree(tmp_path, slug="one")
    halt = tmp_path / "FOREMAN_HALT"
    halt.write_text("", encoding="utf-8")
    monkeypatch.setattr(fst, "HALT_FILE", halt)
    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    assert rows[0].kind == "halt"
    assert rows[0].guidance_key == "halt.halt_file"


def test_collect_with_errors_counts_flows_that_failed_to_parse(tmp_path):
    """discover_flows reports a malformed flow.json as load_error=True; plain
    collect() silently skips it (no flow dict to describe), but
    collect_with_errors must surface how many were skipped so the board
    footer and the queue trailer can show it."""
    flows_root, _ = _tree(tmp_path, slug="healthy")
    bad_dir = tmp_path / "flows" / "dsi" / "broken-json"
    bad_dir.mkdir(parents=True)
    (bad_dir / "flow.json").write_text("{not json", encoding="utf-8")

    rows, error_count = fst.collect_with_errors(flows_root, tmp_path / "sprints", now=NOW)
    assert {r.slug for r in rows} == {"healthy"}
    assert error_count == 1


def test_collect_still_returns_only_the_row_list(tmp_path):
    """collect()'s return shape must not change: every existing caller
    (banner, watch, next, why) destructures it as a plain list."""
    flows_root, _ = _tree(tmp_path, slug="healthy")
    bad_dir = tmp_path / "flows" / "dsi" / "broken-json"
    bad_dir.mkdir(parents=True)
    (bad_dir / "flow.json").write_text("{not json", encoding="utf-8")

    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    assert {r.slug for r in rows} == {"healthy"}


def test_collect_pairs_a_flow_with_its_sprint(tmp_path):
    flows_root, flow_dir = _tree(tmp_path, slug="payoff-books")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    flow["steps"] = [{"index": 0, "title": "s", "status": "running"}]
    flow["spec_path"] = SPEC_PATH
    ft.write_flow(flow_dir, flow)
    ft.append_log(flow_dir, ft._log_entry(
        "2026-08-01T09:59:00Z", "execute", "orchestrator", "sprint_emitted",
        detail=EMITTED))

    sprints_root = tmp_path / "sprints" / "dsi" / "payoff-books"
    sprints_root.mkdir(parents=True)
    (sprints_root / "sprint.json").write_text(
        json.dumps(_sprint_json(created="2026-08-01T10:00:00Z")),
        encoding="utf-8")

    rows = fst.collect(flows_root, tmp_path / "sprints", now=NOW)
    assert rows[0].sprint_status == "running"
    assert rows[0].kind == "sprint_await"


# --- Task 3: the banner and the watch view -----------------------------------

BOARD = fst.DEFAULT_BOARD_PATH


def _closed_status():
    """A FlowStatus for a fully closed flow: kind == 'flow_close', the only
    thing is_idle checks."""
    flow = _flow(stage="closed", status="closed")
    return fst.build_status(
        flow, [], None,
        {"tail": {"brief_written": True, "hub_ingested": True, "hub_refreshed": True}}, NOW)


def test_banner_is_seven_lines_or_fewer():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    assert len(banner.splitlines()) <= fst.BANNER_MAX_LINES


def test_banner_labels_are_the_seven_spec_labels_in_order():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    labels = [line.split()[0] for line in banner.splitlines()]
    assert labels == ["FOREMAN", "STEP", "GATES", "SPRINT", "NEXT", "BLOCKED", "BOARD"]


def test_banner_names_the_focus_flow_project_and_slug():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    assert "dsi/payoff-books" in banner


def test_banner_shows_the_step_title_in_quotes():
    flow = _flow(index=1, of=3)
    flow["steps"][1]["title"] = "board page"
    status = fst.build_status(flow, [], _sprint_json(), {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    assert '"board page"' in banner


def test_banner_step_line_clamps_the_index_so_it_never_exceeds_step_of():
    """A closed flow's current_step.index can outlive its steps array (of=1,
    index=1): the STEP line must show '1 of 1', never the nonsensical
    '2 of 1' step_index + 1 produces unclamped."""
    flow = _flow(index=1, of=1)
    flow["steps"] = [{"index": 0, "title": "only step", "status": "done"}]
    status = fst.build_status(flow, [], None, {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    step_line = [l for l in banner.splitlines() if l.startswith("STEP")][0]
    assert "1 of 1" in step_line
    assert "2 of 1" not in step_line


def test_banner_renders_every_unknown_as_a_dash_never_as_none():
    status = fst.build_status(_flow(), [], None, {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    assert "None" not in banner


def test_banner_gates_line_lists_all_four_gates_pipe_separated():
    gate = _gate("G3", opened_at="2026-08-01T11:38:00Z")
    flow = _flow(phase="learn", status="awaiting-owner", gate=gate)
    status = fst.build_status(flow, [], None, {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    gates_line = [l for l in banner.splitlines() if l.startswith("GATES")][0]
    assert gates_line.count("|") == 3
    for gate_id in ("G1", "G2", "G3", "G4"):
        assert gate_id in gates_line


def test_banner_blocked_line_reports_the_wait():
    gate = _gate("G3", opened_at="2026-08-01T11:38:00Z")
    flow = _flow(phase="learn", status="awaiting-owner", gate=gate)
    status = fst.build_status(flow, [], None, {}, NOW)
    assert status.blocking == "human"
    banner = fst.render_banner([status], NOW, BOARD)
    assert "BLOCKED  human" in banner
    assert "since 22m" in banner


def test_banner_blocked_line_says_nothing_when_not_blocked():
    status = fst.build_status(_flow(), [], None, {}, NOW)
    assert status.blocking == ""
    banner = fst.render_banner([status], NOW, BOARD)
    assert "BLOCKED  nothing" in banner
    assert "since" not in banner


def test_banner_counts_the_other_waiting_flows_on_the_board_line():
    a = _status_with("human", 100, slug="a")
    b = _status_with("agent", 50, slug="b")
    banner = fst.render_banner([a, b], NOW, BOARD)
    assert "(+1 more waiting)" in banner


def test_banner_omits_the_counter_when_only_one_flow_is_active():
    a = _status_with("human", 100, slug="a")
    banner = fst.render_banner([a], NOW, BOARD)
    assert "more waiting" not in banner


def test_banner_with_no_flows_is_one_line():
    banner = fst.render_banner([], NOW, BOARD)
    assert len(banner.splitlines()) == 1
    assert banner.startswith("FOREMAN")


def test_banner_with_no_flows_names_the_flows_root_not_the_board_path():
    """Regression for the verifier's finding: the degenerate 'no flows at all'
    message must name the flows root it was given, not substitute the
    board's own output path (board_path) for it."""
    root = r"C:\work\10_holding\flows"
    banner = fst.render_banner([], NOW, BOARD, flows_root=root)
    assert "no flows under {}".format(root) in banner
    assert BOARD not in banner


def test_banner_with_only_closed_flows_says_so():
    closed = _closed_status()
    banner = fst.render_banner([closed], NOW, BOARD)
    assert len(banner.splitlines()) == 1
    assert "no active flows, 1 closed" in banner


def test_banner_quiet_when_idle_returns_the_empty_string():
    assert fst.render_banner([], NOW, BOARD, quiet_when_idle=True) == ""
    closed = _closed_status()
    assert fst.render_banner([closed], NOW, BOARD, quiet_when_idle=True) == ""


def test_banner_never_exceeds_the_line_budget_with_a_very_long_title():
    flow = _flow(index=0, of=1)
    flow["steps"][0]["title"] = "x" * 300
    status = fst.build_status(flow, [], _sprint_json(), {}, NOW)
    banner = fst.render_banner([status], NOW, BOARD)
    lines = banner.splitlines()
    assert len(lines) == 7
    assert all(len(l) <= 120 for l in lines)


def test_pick_focus_returns_the_first_non_idle_by_sort_key():
    a = _status_with("agent", 10, slug="a")
    b = _status_with("human", 10, slug="b")
    focus = fst.pick_focus([a, b])
    assert focus.slug == "b"


def test_pick_focus_returns_none_when_everything_is_idle():
    closed = _closed_status()
    assert fst.pick_focus([closed]) is None


def test_watch_frame_is_the_banner_plus_the_last_ten_receipts():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    log = [_entry("2026-08-01T09:{:02d}:00Z".format(i), "advanced",
                  detail="step {}".format(i)) for i in range(25)]
    frame = fst.render_watch_frame(status, log, NOW, BOARD, tail=10)
    lines = frame.split("\n")
    assert lines[0].startswith("FOREMAN")
    assert lines[7] == ""
    receipts = lines[8:]
    assert len(receipts) == 10
    assert "step 24" in receipts[-1]
    assert "step 14" not in "\n".join(receipts)


def test_watch_frame_with_an_empty_log_still_renders():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    frame = fst.render_watch_frame(status, [], NOW, BOARD, tail=10)
    lines = frame.split("\n")
    assert len(lines) == 8
    assert lines[-1] == ""


def test_watch_exits_after_max_iterations(tmp_path, capsys):
    flows_root, _ = _tree(tmp_path, slug="one")
    sprints_root = tmp_path / "sprints"
    rc = fst.watch(flows_root, sprints_root, interval=0, max_iterations=2)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("FOREMAN") == 2


# --- reviewer fix-round: _log_for's read guard, watch's CLI wiring ----------

def test_log_for_survives_a_non_utf8_log_file(tmp_path):
    """A non-UTF-8 byte in flow-log.ndjson must not crash watch(); _log_for
    degrades to [] the same way load_driver degrades on unreadable JSON."""
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    (flow_dir / "flow-log.ndjson").write_bytes(b"\xff\xfe not valid utf-8 \x00")
    assert fst._log_for(flow_dir) == []


def test_log_for_survives_a_missing_directory(tmp_path):
    assert fst._log_for(tmp_path / "does-not-exist") == []


def test_watch_cli_wires_tail_quiet_when_idle_and_json_through(monkeypatch):
    """The defect: --tail, --quiet-when-idle and --json parsed on the `watch`
    subparser but never reached watch(). Proven at the main()/watch() seam
    with watch() replaced, so this cannot pass by watch() happening to use
    its own defaults."""
    captured = {}

    def fake_watch(flows_root, sprints_root, **kwargs):
        captured["flows_root"] = flows_root
        captured["sprints_root"] = sprints_root
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(fst, "watch", fake_watch)
    rc = fst.main(["watch", "--flows-root", "F", "--sprints-root", "S",
                  "--interval", "5", "--tail", "3", "--quiet-when-idle",
                  "--json"])
    assert rc == 0
    assert captured["interval"] == 5.0
    assert captured["tail"] == 3
    assert captured["quiet_when_idle"] is True
    assert captured["json_mode"] is True


def test_watch_cli_defaults_tail_and_quiet_when_idle_when_omitted(monkeypatch):
    captured = {}

    def fake_watch(flows_root, sprints_root, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(fst, "watch", fake_watch)
    fst.main(["watch"])
    assert captured["tail"] == 10
    assert captured["quiet_when_idle"] is False
    assert captured["json_mode"] is False


def test_watch_honours_a_custom_tail_end_to_end(tmp_path, capsys):
    """Beyond the wiring: watch()'s own `tail` parameter, reachable from the
    CLI as of this fix, actually limits the printed receipt count."""
    flows_root, flow_dir = _tree(tmp_path, slug="tailed")
    for i in range(5):
        ft.append_log(flow_dir, ft._log_entry(
            "2026-08-01T09:{:02d}:00Z".format(i), "execute", "orchestrator",
            "advanced", detail="marker-{}".format(i)))
    sprints_root = tmp_path / "sprints"
    rc = fst.watch(flows_root, sprints_root, interval=0, max_iterations=1, tail=2)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("marker-") == 2
    assert "marker-4" in out
    assert "marker-3" in out
    assert "marker-2" not in out


def test_watch_quiet_when_idle_suppresses_output_when_everything_is_closed(
        tmp_path, capsys):
    flows_root, flow_dir = _tree(tmp_path, slug="closed-one")
    flow = ft.load_flow(flow_dir)
    flow["stage"] = "closed"
    flow["status"] = "closed"
    ft.write_flow(flow_dir, flow)
    (flow_dir / "foreman.json").write_text(json.dumps(
        {"tail": {"brief_written": True, "hub_ingested": True,
                  "hub_refreshed": True}}), encoding="utf-8")
    sprints_root = tmp_path / "sprints"
    rc = fst.watch(flows_root, sprints_root, interval=0, max_iterations=1,
                  quiet_when_idle=True)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_banner_renders_under_the_time_budget():
    import time
    statuses = [_status_with("agent", i, slug="flow-{}".format(i)) for i in range(40)]
    start = time.time()
    for _ in range(100):
        fst.render_banner(statuses, NOW, BOARD)
    elapsed = time.time() - start
    assert elapsed < 0.5


# --- Task 4: queue, next, why and the house-rule lints -----------------------

def test_queue_lists_every_active_flow_one_line_each():
    a = _status_with("agent", 30, slug="a")
    b = _status_with("human", 10, slug="b")
    text = fst.render_queue([a, b], NOW)
    body = text.splitlines()[1:]
    assert len(body) == 2
    assert any("p/a" in line for line in body)
    assert any("p/b" in line for line in body)


def test_queue_hides_closed_flows_by_default_and_counts_them():
    active = _status_with("agent", 30, slug="active")
    closed = _closed_status()
    text = fst.render_queue([active, closed], NOW)
    assert "active" in text
    assert closed.slug not in text
    assert "1 closed" in text


def test_queue_all_includes_closed_flows():
    active = _status_with("agent", 30, slug="active")
    closed = _closed_status()
    text = fst.render_queue([active, closed], NOW, show_all=True)
    assert closed.slug in text


def test_queue_orders_human_blocked_first_then_by_wait():
    a = _status_with("agent", 5, slug="a")
    b = _status_with("human", 50, slug="b")
    c = _status_with("human", 500, slug="c")
    text = fst.render_queue([a, b, c], NOW)
    body = text.splitlines()[1:]
    assert body[0].startswith("p/c")
    assert body[1].startswith("p/b")
    assert body[2].startswith("p/a")


def test_queue_reports_the_wait_for_each_row():
    a = _status_with("human", 130, slug="a")
    text = fst.render_queue([a], NOW)
    assert "2m" in text


def test_queue_with_no_flows_says_so():
    text = fst.render_queue([], NOW)
    assert "queue is empty" in text


def test_queue_reports_a_nonzero_load_error_count_in_the_trailer():
    active = _status_with("agent", 30, slug="active")
    text = fst.render_queue([active], NOW, error_count=1)
    assert "1 flow(s) could not be read" in text


def test_queue_omits_the_error_line_when_the_count_is_zero():
    active = _status_with("agent", 30, slug="active")
    text = fst.render_queue([active], NOW)
    assert "could not be read" not in text


def test_next_returns_exactly_one_action():
    rows = _build_corpus(NOW)
    text = fst.render_next(rows)
    refs = ["{}/{}".format(r.project, r.slug) for r in rows]
    matches = [ref for ref in refs if ref in text]
    assert len(matches) == 1


def test_next_prefers_the_longest_waiting_human_block():
    a = _status_with("human", 60, slug="fresh")
    b = _status_with("human", 6000, slug="stale")
    text = fst.render_next([a, b])
    assert "p/stale" in text
    assert "p/fresh" not in text


def test_next_falls_back_to_a_fence_when_no_gate_is_open():
    a = _status_with("agent", 10, slug="a")
    b = _status_with("fence", 10, slug="b")
    text = fst.render_next([a, b])
    assert "p/b" in text
    assert "p/a" not in text


def test_next_surfaces_a_gate_open_flow_whose_blocking_is_empty():
    """Regression for the live 2026-08-03 transcript on this repo's own
    2026-08-01-foreman-orchestration-design flow: foreman_plan._gate_open
    returns blocking == "" (a gate_open is not blocking in the pure seam's own
    terms), so the old ("human", "fence") candidate filter reported "nothing
    waiting on you" for a flow that was in fact waiting on a human to open G2.
    Reproduced as a fixture so it survives that flow closing."""
    import dataclasses
    status = dataclasses.replace(
        _status_with("", 3600, slug="gate-open-flow"),
        kind="gate_open", gate_id="G2",
        command="/flow advance p/gate-open-flow",
        why="step 5 of 10 has no open gate and execute needs a G2 decision, "
            "so the gate opens next",
        guidance_key="gate_open.g2")
    text = fst.render_next([status])
    assert "p/gate-open-flow" in text
    assert "nothing waiting on you" not in text
    assert "step 5 of 10 has no open gate and execute needs a G2 decision, so the gate opens next" in text
    assert "/flow advance p/gate-open-flow" in text
    assert "gate_open" in fp.NEEDS_HUMAN_KINDS


def test_next_truncates_a_long_why_with_a_marker():
    """Regression for the verifier's finding: render_next must route WHY
    through _compress_why like every other renderer, not the raw hard cap,
    so a cut sentence ends in '...' instead of stopping mid-word."""
    long_why = "allowed verdicts: " + ", ".join(
        "verdict-{}".format(i) for i in range(20))
    status = _status_with("human", 10, why=long_why)
    text = fst.render_next([status])
    assert text.endswith("...")
    assert long_why not in text


def test_next_says_nothing_is_waiting_and_counts_only_the_agent_blocked():
    """Regression for the verifier's finding: with_agent must count only
    flows actually blocking on an agent (blocking == "agent"), not every
    non-idle flow. `b` here is non-idle but not agent-blocked and must not
    be counted."""
    a = _status_with("agent", 10, slug="a")
    b = _status_with("", 10, slug="b")
    closed = _closed_status()
    text = fst.render_next([a, b, closed])
    assert "nothing waiting on you" in text
    assert "1 flows are with an agent" in text
    assert "1 closed" in text


def test_next_names_the_allowed_verdicts_at_an_open_gate():
    gate = _gate("G3", opened_at="2026-08-01T11:38:00Z")
    flow = _flow(phase="learn", status="awaiting-owner", gate=gate)
    status = fst.build_status(flow, [], None, {}, NOW)
    text = fst.render_next([status])
    assert "proceed, adapt, close, park" in text


def test_why_shows_the_last_five_receipts_then_the_current_action():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    log = [_entry("2026-08-01T09:{:02d}:00Z".format(i), "advanced",
                  detail="step {}".format(i)) for i in range(10)]
    text = fst.render_why(status, log)
    lines = text.splitlines()
    assert "step 5" in lines[0]
    assert "step 9" in lines[4]
    assert "step 4" not in text
    assert "KIND" in text
    assert status.kind in text
    assert status.why in text
    assert status.guidance_key in text


def test_why_with_fewer_than_five_receipts_shows_what_there_is():
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    log = [_entry("2026-08-01T09:00:00Z", "advanced", detail="only-one")]
    text = fst.render_why(status, log)
    assert text.count("only-one") == 1


def test_resolve_ref_matches_project_slash_slug():
    a = _status_with("agent", 10, project="dsi", slug="payoff-books")
    b = _status_with("agent", 10, project="jarvis", slug="payoff-books")
    found = fst.resolve_ref([a, b], "dsi/payoff-books")
    assert found is a


def test_resolve_ref_matches_a_unique_bare_slug():
    a = _status_with("agent", 10, slug="unique-slug")
    b = _status_with("agent", 10, slug="other")
    found = fst.resolve_ref([a, b], "unique-slug")
    assert found is a


def test_resolve_ref_raises_on_an_ambiguous_substring():
    a = _status_with("agent", 10, slug="payoff-books-alpha")
    b = _status_with("agent", 10, slug="payoff-books-beta")
    try:
        fst.resolve_ref([a, b], "payoff-books")
    except LookupError:
        pass
    else:
        raise AssertionError("expected an ambiguous LookupError")


def test_resolve_ref_raises_on_no_match():
    a = _status_with("agent", 10, slug="one")
    try:
        fst.resolve_ref([a], "nope")
    except LookupError:
        pass
    else:
        raise AssertionError("expected a no-match LookupError")


def test_to_json_round_trips_and_uses_flowstatus_field_names():
    import dataclasses
    status = fst.build_status(_flow(), [], _sprint_json(), {}, NOW)
    text = fst.to_json([status])
    parsed = json.loads(text)
    assert isinstance(parsed, list) and len(parsed) == 1
    field_names = {f.name for f in dataclasses.fields(fst.FlowStatus)}
    assert set(parsed[0].keys()) == field_names


def _corpus_flow(project, slug, stage, current_step=None, gate=None):
    """A hand-built flow dict for one ACTION_KINDS corpus entry: distinct
    project/slug so each corpus row is individually addressable, otherwise
    the same shape as _flow()."""
    return {
        "schema_version": 1,
        "flow_id": "{}__{}__20260801T090000Z".format(project, slug),
        "project": project, "slug": slug,
        "idea": "idea for {}".format(slug),
        "stage": stage, "status": "running",
        "current_step": current_step or {"index": 0, "of": 1, "phase": None},
        "steps": [{"index": 0, "title": "the only step", "status": "running"}],
        "model_hint": {}, "gate": gate, "spec_path": "",
        "created_at": "2026-08-01T09:00:00Z", "updated_at": "2026-08-01T11:00:00Z",
    }


def _build_corpus(now):
    """One FlowStatus per fp.ACTION_KINDS value, built through build_status so
    every `why` string is the real template text next_action produces rather
    than a hand-written stand-in. Backs the em-dash/None lints and
    test_next_returns_exactly_one_action."""
    rows = []

    rows.append(fst.build_status(
        _corpus_flow("corp", "flow-advance", "idea"), [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "gate-open", "breakdown"), [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "gate-await", "execute",
                     current_step={"index": 0, "of": 1, "phase": "learn"},
                     gate=_gate("G3", opened_at="2026-08-01T11:00:00Z")),
        [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "gate-consume", "execute",
                     current_step={"index": 0, "of": 1, "phase": "learn"},
                     gate=_gate("G2", decided="approve")),
        [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "sprint-launch", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"}),
        [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "sprint-await", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"}),
        [], _sprint_json(status="running"), {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "sprint-recover", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"}),
        [], _sprint_json(status="capped"), {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "ship-preflight", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"}),
        [], _sprint_json(status="passing"), {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "ship-execute", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"},
                     gate=_gate("G4", decided="ship")),
        [], None, {}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "brief-write", "closed"), [], None,
        {"tail": {}}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "hub-ingest", "closed"), [], None,
        {"tail": {"brief_written": True}}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "hub-refresh", "closed"), [], None,
        {"tail": {"brief_written": True, "hub_ingested": True}}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "flow-close", "closed"), [], None,
        {"tail": {"brief_written": True, "hub_ingested": True,
                  "hub_refreshed": True}}, now))

    rows.append(fst.build_status(
        _corpus_flow("corp", "halted", "execute",
                     current_step={"index": 0, "of": 1, "phase": "sprint"}),
        [], None, {"halt": True}, now))

    return rows


def test_no_rendered_string_contains_an_em_dash():
    rows = _build_corpus(NOW)
    assert {r.kind for r in rows} == set(fp.ACTION_KINDS)
    outputs = [
        fst.render_queue(rows, NOW, show_all=True),
        fst.render_queue(rows, NOW, show_all=False),
        fst.render_next(rows),
        fst.render_why(rows[2], [], tail=5),
        fst.to_json(rows),
    ]
    for out in outputs:
        assert "\u2014" not in out


def test_no_rendered_string_contains_the_literal_none():
    rows = _build_corpus(NOW)
    outputs = [
        fst.render_queue(rows, NOW, show_all=True),
        fst.render_queue(rows, NOW, show_all=False),
        fst.render_next(rows),
        fst.render_why(rows[2], [], tail=5),
    ]
    for out in outputs:
        assert "None" not in out


def _cli_args(cmd, flows_root, sprints_root, *extra):
    return [cmd, "--flows-root", str(flows_root),
            "--sprints-root", str(sprints_root)] + list(extra)


def test_cli_exits_zero_for_every_read_only_subcommand(tmp_path, capsys):
    flows_root, _ = _tree(tmp_path, slug="cli-flow")
    sprints_root = tmp_path / "sprints"
    for args in (
        _cli_args("banner", flows_root, sprints_root),
        _cli_args("queue", flows_root, sprints_root),
        _cli_args("next", flows_root, sprints_root),
        ["why", "cli-flow", "--flows-root", str(flows_root),
         "--sprints-root", str(sprints_root)],
    ):
        rc = fst.main(args)
        assert rc == 0
    assert capsys.readouterr().out


def test_why_json_resolves_the_ref_first_and_returns_only_that_flow(tmp_path, capsys):
    """The fix-round defect: --json must not bypass resolve_ref. A valid ref
    with --json returns exactly that one flow's JSON, not the whole board."""
    flows_root, _ = _tree(tmp_path, slug="one")
    _tree(tmp_path, slug="two")
    sprints_root = tmp_path / "sprints"
    rc = fst.main(["why", "one", "--json",
                  "--flows-root", str(flows_root),
                  "--sprints-root", str(sprints_root)])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, list) and len(parsed) == 1
    assert parsed[0]["slug"] == "one"


def test_why_json_reports_a_bad_ref_the_same_way_the_non_json_path_does(tmp_path):
    flows_root, _ = _tree(tmp_path, slug="only-one")
    sprints_root = tmp_path / "sprints"
    rc = fst.main(["why", "no-such-flow", "--json",
                  "--flows-root", str(flows_root),
                  "--sprints-root", str(sprints_root)])
    assert rc == 1


def test_cli_never_writes_anything(tmp_path):
    """The single most important test in this task: every read-only
    subcommand runs over a real tree and the tree comes out byte-identical,
    file list and every mtime, on the other side."""
    flows_root, _ = _tree(tmp_path, slug="untouched")
    sprints_root = tmp_path / "sprints"

    def _snapshot():
        files = sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())
        return files, {f: pathlib.Path(f).stat().st_mtime for f in files}

    before_files, before_mtimes = _snapshot()

    for args in (
        _cli_args("banner", flows_root, sprints_root),
        _cli_args("queue", flows_root, sprints_root),
        _cli_args("next", flows_root, sprints_root),
        ["why", "untouched", "--flows-root", str(flows_root),
         "--sprints-root", str(sprints_root)],
    ):
        fst.main(args)

    after_files, after_mtimes = _snapshot()
    assert after_files == before_files
    assert after_mtimes == before_mtimes


def test_watch_never_writes_anything(tmp_path):
    """test_cli_never_writes_anything's sibling for `watch`, the CLI surface
    added after that test was written and never covered by it. max_iterations
    and interval=0 keep the loop from hanging."""
    flows_root, _ = _tree(tmp_path, slug="untouched-watch")
    sprints_root = tmp_path / "sprints"

    def _snapshot():
        files = sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())
        return files, {f: pathlib.Path(f).stat().st_mtime for f in files}

    before_files, before_mtimes = _snapshot()

    rc = fst.watch(flows_root, sprints_root, interval=0, max_iterations=1)
    assert rc == 0

    after_files, after_mtimes = _snapshot()
    assert after_files == before_files
    assert after_mtimes == before_mtimes


# --- Task 6: the hook contract -----------------------------------------------
# These test the UserPromptSubmit/SessionStart hook contract (a crash must
# never propagate to a non-zero exit, which a `|| true` on the hook command
# would turn into a silent no-banner rather than an eaten prompt), not
# anything Claude Code specific. They exercise `main` the same way the hook
# command line does.

import pytest


def test_banner_exits_zero_on_a_missing_flows_root(tmp_path):
    rc = fst.main(["banner", "--flows-root", str(tmp_path / "nope")])
    assert rc == 0


def test_banner_exits_zero_on_a_malformed_flow_json(tmp_path, capsys):
    flows_root = tmp_path / "flows"
    flow_dir = flows_root / "proj" / "slug"
    flow_dir.mkdir(parents=True)
    (flow_dir / "flow.json").write_text("{not json", encoding="utf-8")
    rc = fst.main(["banner", "--flows-root", str(flows_root),
                  "--sprints-root", str(tmp_path / "sprints")])
    assert rc == 0
    assert "Traceback" not in capsys.readouterr().out


def test_banner_quiet_when_idle_prints_nothing_for_an_idle_tree(tmp_path, capsys):
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    rc = fst.main(["banner", "--quiet-when-idle",
                  "--flows-root", str(flows_root),
                  "--sprints-root", str(tmp_path / "sprints")])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_banner_prints_something_without_quiet_when_idle_for_an_idle_tree(
        tmp_path, capsys):
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    rc = fst.main(["banner", "--flows-root", str(flows_root),
                  "--sprints-root", str(tmp_path / "sprints")])
    assert rc == 0
    assert capsys.readouterr().out.strip() != ""


def test_settings_json_parses_and_declares_both_hooks():
    """Skipped, not failed, when Step 3's hook has not been applied: Wilco
    can decline or defer the settings.json change, and the suite must stay
    green either way (see the sprint report for the Step 3 decision).
    Also skipped, not failed, in a packaged copy of this engine (Foreman
    cloud package) that was never checked out with its own hub-style
    `.claude\\settings.json` banner hook."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    settings_path = repo_root / ".claude" / "settings.json"
    if not settings_path.is_file():
        pytest.skip("no .claude/settings.json at this checkout: not a hub "
                    "banner-hook checkout")
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    if "SessionStart" not in hooks or "UserPromptSubmit" not in hooks:
        pytest.skip("Step 3 hook not applied: Wilco declined/deferred the "
                    "settings.json session-banner hook")

    def _commands(entries):
        return [h.get("command", "") for e in entries for h in e.get("hooks", [])]

    session_start_cmds = _commands(hooks["SessionStart"])
    prompt_submit_cmds = _commands(hooks["UserPromptSubmit"])
    assert any("foreman_status.py" in c and "banner" in c
               for c in session_start_cmds)
    assert any("foreman_status.py" in c and "banner" in c
               and "--quiet-when-idle" in c for c in prompt_submit_cmds)
