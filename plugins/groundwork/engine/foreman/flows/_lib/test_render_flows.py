"""Tests for render_flows.py. Plain pytest, stdlib only, hermetic (tmp_path)."""
import flow_transition as ft
import render_flows as rf


def _make_flow(tmp_path, project, slug, awaiting):
    """Create a flow; if awaiting, open a gate so it lands on the queue."""
    flow_dir, flow = ft.cmd_new(tmp_path, project, f"{slug} idea", slug=slug,
                                now="2026-07-18T09:00:00Z")
    if awaiting:
        flow["stage"] = "execute"
        flow["current_step"] = {"index": 1, "of": 2, "phase": "sprint"}
        ft.write_flow(flow_dir, flow)
        flow = ft.load_flow(flow_dir)
        ft.persist_open_gate(flow_dir, flow, "G2", 1,
                             now="2026-07-18T09:14:03Z", nonce="7QF3")
    return flow_dir


# Siteoffice fold (sprint 1 step 12): render_index and render_flow no longer
# build HTML themselves, they delegate to board.render_board and return its
# new _board\ path. The content assertions the four removed tests used to
# make (empty root, awaiting-flow-on-top ordering, gate id shown, HTML
# escaping) were ported to board\tests\test_board_kinds.py::TestFlowsKind
# back in sprint 1 step 9, when the flows board kind was first built; they
# are not re-ported here a second time. These two tests only prove the
# delegation happened and the new output path exists, per the guide's own
# instruction.
def test_render_index_delegates_and_returns_new_board_path(tmp_path):
    _make_flow(tmp_path, "jarvis", "one", awaiting=False)
    out = rf.render_index(tmp_path)
    assert out == tmp_path.parent / "_board" / "flows" / "index.html"
    assert out.is_file()


def test_render_flow_delegates_and_returns_new_board_path(tmp_path):
    flow_dir = _make_flow(tmp_path, "dsi", "payoff", awaiting=True)
    out = rf.render_flow(flow_dir)
    assert out == tmp_path.parent / "_board" / "flows" / "dsi" / "payoff" / "index.html"
    assert out.is_file()


def test_decided_gate_no_longer_counts_as_awaiting(tmp_path):
    # Regression (2026-08-01): cmd_gate leaves flow["status"] at
    # "awaiting-owner" (only advance clears it), so keying is_awaiting off the
    # flow-level status kept answered gates pinned to "WAITING ON YOU" forever.
    flow_dir = _make_flow(tmp_path, "dsi", "answered-one", awaiting=True)
    assert rf.is_awaiting(ft.load_flow(flow_dir)) is True
    ft.cmd_gate(flow_dir, "approve", surface="slack",
                now="2026-07-18T09:31:00Z")
    flow = ft.load_flow(flow_dir)
    assert flow["gate"]["status"] == "decided"
    assert flow["status"] == "awaiting-owner"     # stale by design, until advance
    assert rf.is_awaiting(flow) is False          # but no longer waiting on Wilco


def test_gateless_flow_still_uses_flow_status(tmp_path):
    flow_dir = _make_flow(tmp_path, "dsi", "no-gate", awaiting=False)
    flow = ft.load_flow(flow_dir)
    assert flow.get("gate") is None
    flow["status"] = "awaiting-owner"
    assert rf.is_awaiting(flow) is True


def test_render_status_text_reports_project_and_stage(tmp_path):
    flow_dir = _make_flow(tmp_path, "dsi", "payoff", awaiting=False)
    text = rf.render_status_text(flow_dir)
    assert "dsi" in text and "payoff" in text
    assert "idea" in text  # stage


def test_status_text_errors_when_no_flow_json(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    text = rf.render_status_text(empty)
    assert "error" in text.lower()
