"""Tests for terminal_sweep.py. Plain pytest, stdlib only, fixtures only.

No sprint is launched and no email is sent anywhere in these tests or in the
module under test (spec Invariants). Every timestamp is injected so the tests
are deterministic.
"""
import json

import flow_state as fs
import terminal_sweep as ts


# --- fixtures --------------------------------------------------------------

SPEC = r"C:\work\10_holding\05_docs\superpowers\specs\2026-07-21-x-design.md"
EMIT_TS = "2026-07-21T09:00:00Z"
LATER_TS = "2026-07-21T10:00:00Z"


def _emit_detail(project="ZeroNonsense.dev", spec=SPEC, model_map=None):
    line = f"/sprint {project} {spec}"
    return line + (f" --model-map {model_map}" if model_map else "")


def _flow(project="ZeroNonsense.dev", slug="x", phase="sprint", gate=None,
          stage="execute", flow_id=None):
    return {
        "schema_version": fs.SCHEMA_VERSION,
        "flow_id": flow_id or fs.make_flow_id(project, slug, EMIT_TS),
        "project": project, "slug": slug, "idea": "x",
        "stage": stage, "status": "running",
        "current_step": {"index": 0, "of": 1, "phase": phase},
        "steps": [{"index": 0, "title": "build", "status": "running"}],
        "model_hint": {}, "gate": gate,
        "created_at": EMIT_TS, "updated_at": EMIT_TS,
    }


def _log(detail=None, ts=EMIT_TS, swept_ids=()):
    entries = [{"ts": ts, "stage": "execute", "actor": "orchestrator",
                "action": "sprint_emitted", "outcome": "info",
                "detail": detail if detail is not None else _emit_detail(),
                "ref": None}]
    for sid in swept_ids:
        entries.append({"ts": LATER_TS, "stage": "execute",
                        "actor": "orchestrator", "action": "sprint_swept",
                        "outcome": "info", "detail": f"{sid} status=passing",
                        "ref": sid})
    return entries


def _ctx(flow=None, log=None):
    flow = flow if flow is not None else _flow()
    return {"flow": flow, "log": log if log is not None else _log()}


def _sprint(project="ZeroNonsense.dev", slug="x", spec=SPEC, status="passing",
            sprint_id="ZeroNonsense.dev__x__20260721T093000Z",
            created_at=LATER_TS, verify_summary=None, test_summary=None):
    s = {"schema_version": 1, "sprint_id": sprint_id, "project": project,
         "slug": slug, "spec_path": spec, "status": status,
         "created_at": created_at, "updated_at": created_at,
         "last_verify_result": None, "last_test_result": None}
    if verify_summary is not None:
        s["last_verify_result"] = {"summary": verify_summary, "outcome": "fail",
                                   "at": created_at}
    if test_summary is not None:
        s["last_test_result"] = {"summary": test_summary, "exit_code": 1,
                                 "at": created_at}
    return s


# --- parse_emitted_spec_path ----------------------------------------------

def test_parse_emitted_spec_path_plain():
    proj, spec = ts.parse_emitted_spec_path(_emit_detail())
    assert proj == "ZeroNonsense.dev"
    assert spec == SPEC


def test_parse_emitted_spec_path_strips_model_map():
    proj, spec = ts.parse_emitted_spec_path(
        _emit_detail(model_map="dev=sonnet,plan=opus"))
    assert spec == SPEC  # spec path preserved, --model-map removed


def test_parse_emitted_spec_path_non_sprint_line():
    assert ts.parse_emitted_spec_path("advanced stage=execute") == (None, None)


FULL_MAP = ("spec=opus,plan=opus,dev=sonnet,test=haiku,"
            "verify=opus,iterate=sonnet,report=haiku")


def test_parse_emitted_spec_path_strips_full_seven_pair_model_map():
    """Every flow-emitted line now carries a complete map (v1.1 always-emit)."""
    proj, spec = ts.parse_emitted_spec_path(_emit_detail(model_map=FULL_MAP))
    assert proj == "ZeroNonsense.dev"
    assert spec == SPEC


def test_parse_emitted_spec_path_spec_path_with_spaces_and_full_model_map():
    spaced = r"C:\work\10_holding\05_docs\superpowers\specs\a spec with spaces.md"
    proj, spec = ts.parse_emitted_spec_path(
        _emit_detail(spec=spaced, model_map=FULL_MAP))
    assert proj == "ZeroNonsense.dev"
    assert spec == spaced


# --- match_terminal_sprints (eligibility + spec_path + terminal set) -------

def test_match_eligible_flow_to_terminal_sprint():
    out = ts.match_terminal_sprints([_ctx()], [_sprint()], {})
    assert len(out) == 1
    flow, sprint = out[0]
    assert sprint["status"] == "passing"


def test_match_skips_open_gate_flow():
    gate = {"id": "G3", "step_index": 0, "status": "awaiting-owner",
            "decision": None, "notified": False, "token": "T", "opened_at": EMIT_TS}
    out = ts.match_terminal_sprints([_ctx(flow=_flow(gate=gate))], [_sprint()], {})
    assert out == []


def test_match_skips_non_sprint_phase():
    out = ts.match_terminal_sprints([_ctx(flow=_flow(phase="learn"))],
                                    [_sprint()], {})
    assert out == []


def test_match_terminal_status_set():
    for status in ("passing", "done", "capped", "failed", "escalated"):
        out = ts.match_terminal_sprints([_ctx()], [_sprint(status=status)], {})
        assert len(out) == 1, status
    for status in ("running", "paused"):
        out = ts.match_terminal_sprints([_ctx()], [_sprint(status=status)], {})
        assert out == [], status


def test_match_skips_already_swept_sprint_id():
    flow = _flow()
    sprint = _sprint()
    swept = {flow["flow_id"]: {sprint["sprint_id"]}}
    out = ts.match_terminal_sprints([_ctx(flow=flow)], [sprint], swept)
    assert out == []


def test_match_launched_after_emit_honesty_check():
    # sprint created_at predates the emit -> stale, must not match.
    stale = _sprint(created_at="2026-07-21T08:00:00Z")  # before EMIT_TS 09:00
    out = ts.match_terminal_sprints([_ctx()], [stale], {})
    assert out == []


def test_match_most_recent_tie_break_one_per_cycle():
    older = _sprint(sprint_id="ZeroNonsense.dev__x__20260721T093000Z",
                    created_at="2026-07-21T09:30:00Z")
    newer = _sprint(sprint_id="ZeroNonsense.dev__x__20260721T100000Z",
                    created_at=LATER_TS)
    out = ts.match_terminal_sprints([_ctx()], [older, newer], {})
    assert len(out) == 1
    assert out[0][1]["sprint_id"] == newer["sprint_id"]


CORRECTED_SPEC = r"C:\work\10_holding\05_docs\superpowers\specs\2026-07-21-x-corrected.md"


def test_match_falls_back_to_flow_spec_path_when_emitted_path_finds_nothing():
    # Found via _packs/2026-09-12-s2-lane2-2-11-first-operator-pack: the
    # emitted /sprint line named a stale (pre-correction) spec_path, the
    # flow's own spec_path field was corrected afterward, and the sprint
    # that actually ran used the corrected path. The old behaviour matched
    # nothing and refused to advance a genuinely passing sprint.
    flow = _flow()
    flow["spec_path"] = CORRECTED_SPEC
    sprint = _sprint(spec=CORRECTED_SPEC)
    out = ts.match_terminal_sprints([_ctx(flow=flow)], [sprint], {})
    assert len(out) == 1
    assert out[0][1]["sprint_id"] == sprint["sprint_id"]


def test_match_still_prefers_emitted_path_when_it_has_a_real_match():
    # The fallback must never mask a genuine emitted-path match: if a sprint
    # exists for the emitted path, it wins, the flow's current spec_path is
    # not even consulted.
    flow = _flow()
    flow["spec_path"] = CORRECTED_SPEC  # differs from SPEC, but SPEC matches
    out = ts.match_terminal_sprints([_ctx(flow=flow)], [_sprint(spec=SPEC)], {})
    assert len(out) == 1
    assert out[0][1]["spec_path"] == SPEC


def test_match_no_fallback_when_flow_spec_path_matches_emitted_path():
    # flow['spec_path'] equal to the emitted path is the ordinary case
    # (no correction ever happened); must not behave differently.
    flow = _flow()
    flow["spec_path"] = SPEC
    out = ts.match_terminal_sprints([_ctx(flow=flow)], [_sprint()], {})
    assert len(out) == 1


def test_match_no_match_at_all_when_neither_path_has_a_sprint():
    flow = _flow()
    flow["spec_path"] = CORRECTED_SPEC
    unrelated = _sprint(spec=r"C:\work\10_holding\05_docs\superpowers\specs\unrelated.md")
    out = ts.match_terminal_sprints([_ctx(flow=flow)], [unrelated], {})
    assert out == []


# --- classify_terminal -----------------------------------------------------

def test_classify_success_vs_needs_guidance():
    for s in ("passing", "done"):
        assert ts.classify_terminal(_sprint(status=s)) == "success"
    for s in ("capped", "failed", "escalated"):
        assert ts.classify_terminal(_sprint(status=s)) == "needs-guidance"


# --- build_sweep_context ---------------------------------------------------

def test_build_sweep_context_failed_includes_summaries_and_verbs():
    sprint = _sprint(status="failed",
                     verify_summary="latency 780ms vs target 500ms",
                     test_summary="3 failed, 47 passed")
    ctx = ts.build_sweep_context(sprint)
    assert ctx["status"] == "failed"
    assert ctx["class"] == "needs-guidance"
    assert "780ms" in ctx["verify_summary"]
    assert "3 failed" in ctx["test_summary"]
    joined = " ".join(ctx["suggested_verbs"]).lower()
    assert "adapt" in joined and "close" in joined


# =============================================================================
# Task 2: host tests (sweep_once / apply_match / discover_* / build_swept_index)
# =============================================================================

import pathlib

import flow_transition as ft
import outbox


def _write_ndjson(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries),
                    encoding="utf-8")


def _build_live_flow(tmp_path, project="ZeroNonsense.dev", slug="x"):
    """A flow at execute/phase=sprint/gate=null with a sprint_emitted receipt,
    plus a terminal sprint.json under sprints_root matching its spec_path."""
    flows_root = tmp_path / "flows"
    sprints_root = tmp_path / "sprints"
    flow_dir = flows_root / project / slug
    flow_dir.mkdir(parents=True)
    flow = _flow(project=project, slug=slug)  # from Task-1 fixtures
    ft.write_flow(flow_dir, flow)
    _write_ndjson(flow_dir / "flow-log.ndjson", _log())  # sprint_emitted @ EMIT_TS
    sprint_dir = sprints_root / project / slug
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "sprint.json").write_text(
        json.dumps(_sprint(project=project, slug=slug)), encoding="utf-8")
    return flows_root, sprints_root, flow_dir


def test_host_sequence_success_flow(tmp_path):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    summary = ts.sweep_once(flows_root, sprints_root, now="2026-07-21T11:00:00Z")
    assert summary["matched"] == 1
    final = ft.load_flow(flow_dir)
    # Spec item 9: one advance happens, and it opens the G4 ship gate. The
    # sweep no longer walks past it to G3: every terminal sprint now gets an
    # operator ship / hold decision, and `hold` is the right answer for a
    # failed one.
    assert final["gate"]["id"] == "G4"
    assert final["gate"]["status"] == "awaiting-owner"
    log = [json.loads(x) for x in
           (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8").splitlines() if x.strip()]
    actions = [e["action"] for e in log]
    # Only one cmd_advance now runs (it opens G4 directly), so 'advanced'
    # is never logged here; 'gate_opened' is the only transition receipt.
    assert "gate_opened" in actions
    assert actions.count("sprint_swept") == 1
    # exactly one outbox entry queued
    assert len(outbox.read_outbox(flow_dir)) == 1


def test_host_sequence_idempotent_on_rerun(tmp_path):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    ts.sweep_once(flows_root, sprints_root, now="2026-07-21T11:00:00Z")
    outbox_before = list(outbox.read_outbox(flow_dir))
    log_before = (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8")
    summary2 = ts.sweep_once(flows_root, sprints_root, now="2026-07-21T11:30:00Z")
    assert summary2["matched"] == 0  # flow no longer eligible (phase=learn, gate open)
    assert list(outbox.read_outbox(flow_dir)) == outbox_before
    assert (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8") == log_before


def test_host_dry_run_writes_nothing(tmp_path):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    before_flow = (flow_dir / "flow.json").read_text(encoding="utf-8")
    before_log = (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8")
    summary = ts.sweep_once(flows_root, sprints_root,
                            now="2026-07-21T11:00:00Z", dry_run=True)
    assert summary["dry_run"] is True and summary["matched"] == 1
    assert (flow_dir / "flow.json").read_text(encoding="utf-8") == before_flow
    assert (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8") == before_log
    assert outbox.read_outbox(flow_dir) == []  # nothing queued


def test_host_pre_write_reassert_skips_gate_opened_between_read_and_write(tmp_path):
    """If G3 was opened between discovery and write, the re-assert no-ops the flow
    (no double advance). Simulated by opening a gate on disk after discovery."""
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    flows = ts.discover_flows(flows_root)
    sprints = ts.discover_sprints(sprints_root)
    swept = ts.build_swept_index(flows)
    matches = ts.match_terminal_sprints(flows, sprints, swept)
    assert len(matches) == 1
    # Mutate on-disk state so the flow is no longer eligible before the write.
    stale = ft.load_flow(flow_dir)
    stale["current_step"]["phase"] = "learn"
    stale["gate"] = {"id": "G3", "step_index": 0, "status": "awaiting-owner",
                     "decision": None, "notified": False, "token": "T",
                     "opened_at": "2026-07-21T10:59:00Z"}
    ft.write_flow(flow_dir, stale)
    # apply_match must re-assert eligibility and no-op.
    result = ts.apply_match(flow_dir, matches[0][1], now="2026-07-21T11:00:00Z")
    assert result["skipped"] is True
    final = ft.load_flow(flow_dir)
    assert final["gate"]["token"] == "T"  # untouched; no second advance


def test_host_per_flow_error_is_isolated(tmp_path):
    """A malformed flow yields a sweep_error receipt and does not abort the batch;
    sweep_once still returns and exits 0-equivalent."""
    flows_root, sprints_root, good_dir = _build_live_flow(tmp_path)
    bad_dir = flows_root / "ZeroNonsense.dev" / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "flow.json").write_text("{ not json", encoding="utf-8")
    summary = ts.sweep_once(flows_root, sprints_root, now="2026-07-21T11:00:00Z")
    assert summary["matched"] == 1  # good flow still swept
    assert ft.load_flow(good_dir)["gate"]["id"] == "G4"


# =============================================================================
# Relaunch guard: has_terminal_sprint (pure, no I/O, reuses TERMINAL_STATUSES)
# =============================================================================

def test_has_terminal_sprint_matches_project_spec_path_and_terminal_status():
    sprint = _sprint()
    found = ts.has_terminal_sprint("ZeroNonsense.dev", SPEC, [sprint])
    assert found is not None
    assert found["sprint_id"] == sprint["sprint_id"]


def test_has_terminal_sprint_covers_the_whole_terminal_set():
    for status in ("passing", "done", "capped", "failed", "escalated"):
        found = ts.has_terminal_sprint("ZeroNonsense.dev", SPEC,
                                       [_sprint(status=status)])
        assert found is not None, status


def test_has_terminal_sprint_running_sprint_same_spec_path_returns_none():
    assert ts.has_terminal_sprint(
        "ZeroNonsense.dev", SPEC, [_sprint(status="running")]) is None


def test_has_terminal_sprint_other_project_returns_none():
    assert ts.has_terminal_sprint("other-project", SPEC, [_sprint()]) is None


def test_has_terminal_sprint_other_spec_path_returns_none():
    assert ts.has_terminal_sprint(
        "ZeroNonsense.dev", r"C:\work\10_holding\05_docs\other.md",
        [_sprint()]) is None


def test_has_terminal_sprint_empty_inputs_return_none():
    assert ts.has_terminal_sprint("ZeroNonsense.dev", SPEC, []) is None
    assert ts.has_terminal_sprint("", SPEC, [_sprint()]) is None
    assert ts.has_terminal_sprint("ZeroNonsense.dev", "", [_sprint()]) is None


def test_has_terminal_sprint_returns_the_most_recent_match():
    older = _sprint(sprint_id="ZeroNonsense.dev__x__20260721T093000Z",
                    created_at="2026-07-21T09:30:00Z")
    newer = _sprint(sprint_id="ZeroNonsense.dev__x__20260721T100000Z",
                    created_at=LATER_TS)
    found = ts.has_terminal_sprint("ZeroNonsense.dev", SPEC, [older, newer])
    assert found["sprint_id"] == newer["sprint_id"]


def test_has_terminal_sprint_does_no_io_and_does_not_mutate_sprints():
    sprints = [_sprint()]
    before = json.dumps(sprints, sort_keys=True)
    ts.has_terminal_sprint("ZeroNonsense.dev", SPEC, sprints)
    assert json.dumps(sprints, sort_keys=True) == before


# =============================================================================
# Sweep diagnostics: diagnose_unmatched (pure) and the --diagnose flag
# =============================================================================

def _reason_of(rows):
    assert len(rows) == 1, rows
    return rows[0]["reason"]


def test_diagnose_no_emit_receipt_when_the_log_has_no_sprint_emitted():
    rows = ts.diagnose_unmatched([_ctx(log=[])], [_sprint()])
    assert _reason_of(rows) == "no_emit_receipt"


def test_diagnose_no_emit_receipt_when_the_receipt_detail_is_unparsable():
    rows = ts.diagnose_unmatched(
        [_ctx(log=_log(detail="advanced stage=execute"))], [_sprint()])
    assert _reason_of(rows) == "no_emit_receipt"


def test_diagnose_no_matching_sprint_when_no_sprint_json_carries_the_spec():
    other = _sprint(spec=r"C:\work\10_holding\05_docs\other.md")
    rows = ts.diagnose_unmatched([_ctx()], [other])
    assert _reason_of(rows) == "no_matching_sprint"


def test_diagnose_no_matching_sprint_when_the_project_differs():
    rows = ts.diagnose_unmatched([_ctx()], [_sprint(project="other-project")])
    assert _reason_of(rows) == "no_matching_sprint"


def test_diagnose_finds_a_match_via_the_corrected_spec_path_fallback():
    # Same corrected-spec_path scenario as match_terminal_sprints's own
    # fallback test: must not report no_matching_sprint when a sprint exists
    # for the flow's current (corrected) spec_path.
    flow = _flow()
    flow["spec_path"] = CORRECTED_SPEC
    rows = ts.diagnose_unmatched([_ctx(flow=flow)], [_sprint(spec=CORRECTED_SPEC)])
    assert rows == []


def test_diagnose_not_terminal_when_the_matching_sprint_is_still_running():
    rows = ts.diagnose_unmatched([_ctx()], [_sprint(status="running")])
    assert _reason_of(rows) == "not_terminal"


def test_diagnose_predates_emit_when_created_at_is_not_after_the_receipt():
    stale = _sprint(created_at="2026-07-21T08:00:00Z")  # before EMIT_TS 09:00
    rows = ts.diagnose_unmatched([_ctx()], [stale])
    assert _reason_of(rows) == "predates_emit"


def test_diagnose_returns_nothing_for_a_genuine_match():
    assert ts.diagnose_unmatched([_ctx()], [_sprint()]) == []


def test_diagnose_ignores_flows_that_are_not_sweep_eligible():
    assert ts.diagnose_unmatched([_ctx(flow=_flow(phase="learn"))], []) == []
    gate = {"id": "G3", "step_index": 0, "status": "awaiting-owner",
            "decision": None, "notified": False, "token": "T",
            "opened_at": EMIT_TS}
    assert ts.diagnose_unmatched([_ctx(flow=_flow(gate=gate))], []) == []
    assert ts.diagnose_unmatched(
        [{"flow": None, "log": [], "load_error": True}], []) == []


def test_diagnose_row_carries_project_slug_flow_id_reason_and_detail():
    row = ts.diagnose_unmatched([_ctx(log=[])], [])[0]
    assert set(row) == {"project", "slug", "flow_id", "reason", "detail"}
    assert row["project"] == "ZeroNonsense.dev" and row["slug"] == "x"
    assert row["detail"]  # never a bare reason code


def test_diagnose_every_reason_is_a_declared_code():
    cases = [
        ([_ctx(log=[])], [_sprint()]),
        ([_ctx()], [_sprint(spec=r"C:\work\10_holding\05_docs\other.md")]),
        ([_ctx()], [_sprint(status="running")]),
        ([_ctx()], [_sprint(created_at="2026-07-21T08:00:00Z")]),
    ]
    seen = set()
    for flows, sprints in cases:
        for row in ts.diagnose_unmatched(flows, sprints):
            assert row["reason"] in ts.DIAGNOSTIC_REASONS
            seen.add(row["reason"])
    assert seen == set(ts.DIAGNOSTIC_REASONS)


def test_diagnose_priority_not_terminal_wins_over_predates_emit():
    """A running sprint that also predates the emit reports the earlier reason
    in the spec's priority order."""
    rows = ts.diagnose_unmatched(
        [_ctx()], [_sprint(status="running", created_at="2026-07-21T08:00:00Z")])
    assert _reason_of(rows) == "not_terminal"


def test_diagnose_is_pure_and_writes_nothing(tmp_path):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    before_flow = (flow_dir / "flow.json").read_text(encoding="utf-8")
    before_log = (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8")
    ts.diagnose_unmatched(ts.discover_flows(flows_root),
                          ts.discover_sprints(sprints_root))
    assert (flow_dir / "flow.json").read_text(encoding="utf-8") == before_flow
    assert (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8") == before_log
    assert outbox.read_outbox(flow_dir) == []


# --- main() --diagnose -----------------------------------------------------

def test_main_diagnose_prints_a_reason_and_sweeps_nothing(tmp_path, capsys):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    # Break the match so there is something to explain.
    (sprints_root / "ZeroNonsense.dev" / "x" / "sprint.json").write_text(
        json.dumps(_sprint(status="running")), encoding="utf-8")
    before_flow = (flow_dir / "flow.json").read_text(encoding="utf-8")
    before_log = (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8")
    rc = ts.main(["--flows-root", str(flows_root),
                  "--sprints-root", str(sprints_root), "--diagnose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not_terminal" in out
    assert "ZeroNonsense.dev/x" in out
    assert "unmatched=1" in out
    # Diagnostic mode never sweeps and never writes.
    assert (flow_dir / "flow.json").read_text(encoding="utf-8") == before_flow
    assert (flow_dir / "flow-log.ndjson").read_text(encoding="utf-8") == before_log


def test_main_without_diagnose_still_sweeps_exactly_as_before(tmp_path, capsys):
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    rc = ts.main(["--flows-root", str(flows_root),
                  "--sprints-root", str(sprints_root)])
    assert rc == 0
    assert "matched=1" in capsys.readouterr().out
    assert ft.load_flow(flow_dir)["gate"]["id"] == "G4"


def test_apply_match_stops_at_g4_and_its_context_reaches_the_notification(tmp_path):
    """Spec item 9. Two things have to be true at once: the sweep leaves the
    flow on the ship gate, and the sweep context it gathered still reaches the
    operator. The context lives on the in-memory gate only, so it is asserted
    through the queued outbox record rather than through flow.json.
    """
    import outbox

    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    sprint = _sprint()
    result = ts.apply_match(flow_dir, sprint, now="2026-07-21T11:00:00Z")
    assert result["skipped"] is False

    gate = ft.load_flow(flow_dir)["gate"]
    assert gate["id"] == "G4"
    assert gate["status"] == "awaiting-owner"

    records = outbox.read_outbox(flow_dir)
    assert len(records) == 1
    assert records[0]["gate_id"] == "G4"
    assert "ship approval" in records[0]["subject"]
    assert "ship | hold | park" in records[0]["body"]
    # the sweep_context block outbox.compose appends when it is present
    assert "Sprint status:" in records[0]["body"]


def test_a_swept_flow_is_no_longer_sweep_eligible(tmp_path):
    """The G4 gate the sweep opens is itself the guard against a second sweep:
    is_sweep_eligible requires gate is None."""
    flows_root, sprints_root, flow_dir = _build_live_flow(tmp_path)
    ts.apply_match(flow_dir, _sprint(), now="2026-07-21T11:00:00Z")
    assert ts.is_sweep_eligible(ft.load_flow(flow_dir)) is False


# =============================================================================
# Task 1: apply_match(lock=False) for a caller already holding the flow lock
# =============================================================================

import foreman_lock as fl

NOW = "2026-07-21T11:00:00Z"


def _sweep_eligible_flow(tmp_path):
    """A flow at execute/phase=sprint/gate=null, which is_sweep_eligible accepts."""
    flow_dir = tmp_path / "dsi" / "payoff-books"
    flow_dir.mkdir(parents=True)
    flow = ft.new_flow("dsi", "payoff-books", "idea", NOW,
                       spec_path=r"C:\work\50_projects\dsi\05_docs\s.md")
    flow["stage"] = "execute"
    flow["current_step"] = {"index": 1, "of": 1, "phase": "sprint",
                            "title": "step one"}
    flow["gate"] = None
    (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
    return flow_dir


def test_apply_match_with_lock_false_crosses_while_the_caller_holds_the_lock(tmp_path):
    """The whole point: execute_once already holds this lock, and the default
    path would come back a silent skip rather than crossing.

    sweep_context lives on the in-memory gate only (see
    test_apply_match_stops_at_g4_and_its_context_reaches_the_notification),
    so it is asserted through the queued outbox record, not flow.json.
    """
    import outbox

    flow_dir = _sweep_eligible_flow(tmp_path)
    sprint = {"sprint_id": "dsi__x__1", "status": "failed"}
    with fl.acquire(flow_dir, "caller-session", now=NOW):
        result = ts.apply_match(flow_dir, sprint, now=NOW, lock=False)
    assert result == {"skipped": False, "sprint_id": "dsi__x__1"}
    flow = ft.load_flow(flow_dir)
    assert isinstance(flow.get("gate"), dict)
    records = outbox.read_outbox(flow_dir)
    assert len(records) == 1
    assert "Sprint status:" in records[0]["body"]


def test_apply_match_still_takes_the_lock_by_default(tmp_path):
    """Regression fence: the scheduled sweep's behaviour must not change."""
    flow_dir = _sweep_eligible_flow(tmp_path)
    sprint = {"sprint_id": "dsi__x__1", "status": "failed"}
    with fl.acquire(flow_dir, "someone-else", now=NOW):
        result = ts.apply_match(flow_dir, sprint, now=NOW)
    assert result["skipped"] is True
    assert result["reason"] == "lock_busy"
