"""Tests for the node-less flow and sprint board kinds (guide step 9):
synthetic flows/sprints roots, no real data. Rewritten assertions from
flows\\_lib\\test_render_flows.py against the new output (not copied: the
old tests import flow_transition/flow_state to build fixtures, which this
board never imports, so fixtures here are built as plain flow.json dicts).
Stdlib compatible: run with `python -m pytest board\\tests -q` or plain
unittest.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import render_board  # noqa: E402
import kinds.sprint as kind_sprint  # noqa: E402


def _write_flow(flows_root: pathlib.Path, project: str, slug: str, **overrides):
    flow_dir = flows_root / project / slug
    flow_dir.mkdir(parents=True, exist_ok=True)
    flow = {
        "project": project, "slug": slug, "stage": "execute", "status": "running",
        "gate": None, "updated_at": "2026-09-11T09:00:00Z",
        "steps": [{"index": 0, "title": "First step", "status": "done"}],
        "idea": "a plain idea",
    }
    flow.update(overrides)
    (flow_dir / "flow.json").write_text(json.dumps(flow), encoding="utf-8")
    return flow_dir


def _write_sprint(sprints_root: pathlib.Path, project: str, slug: str, sprint_data: dict,
                   log_entries=None):
    sprint_dir = sprints_root / project / slug
    sprint_dir.mkdir(parents=True, exist_ok=True)
    (sprint_dir / "report.md").write_text("# report\n\ndone.\n", encoding="utf-8")
    (sprint_dir / "sprint.json").write_text(json.dumps(sprint_data), encoding="utf-8")
    if log_entries is not None:
        (sprint_dir / "action-log.ndjson").write_text(
            "\n".join(json.dumps(e) for e in log_entries) + ("\n" if log_entries else ""),
            encoding="utf-8",
        )
    return sprint_dir


def _write_holding_root(root_dir: pathlib.Path, languages=None):
    root_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / "siteoffice.json").write_text(json.dumps({
        "schemaVersion": 2, "type": "holding", "name": "test-holding",
        "parent": "", "children": [], "language": "en",
        "settings": {"languages": languages or ["en", "nl"]},
    }), encoding="utf-8")


class TestFlowsKind(unittest.TestCase):
    def test_empty_root_writes_zero_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            flows_root.mkdir()
            rc = render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            index_json = json.loads(
                (root_dir / "_board" / "flows" / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index_json["flows"], [])

    def test_awaiting_flow_sorts_first_and_joins_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            _write_flow(flows_root, "jarvis", "running-one", status="running")
            _write_flow(flows_root, "dsi", "waiting-one", status="awaiting-owner",
                        gate={"id": "G2", "status": "awaiting-owner", "token": "abcd"})
            rc = render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            html = (root_dir / "_board" / "flows" / "index.html").read_text(encoding="utf-8")
            self.assertLess(html.index("waiting-one"), html.index("running-one"))
            self.assertIn("queue-block", html)
            index_json = json.loads(
                (root_dir / "_board" / "flows" / "index.json").read_text(encoding="utf-8")
            )
            self.assertTrue(index_json["flows"][0]["awaiting"])
            self.assertEqual(index_json["flows"][0]["slug"], "waiting-one")

    def test_decided_gate_drops_off_the_waiting_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            _write_flow(flows_root, "dsi", "answered-one", status="awaiting-owner",
                        gate={"id": "G2", "status": "decided", "token": "abcd",
                              "decision": {"verdict": "approve"}})
            render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            index_json = json.loads(
                (root_dir / "_board" / "flows" / "index.json").read_text(encoding="utf-8")
            )
            self.assertFalse(index_json["flows"][0]["awaiting"])

    def test_gateless_flow_still_uses_flow_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            _write_flow(flows_root, "dsi", "no-gate", status="awaiting-owner", gate=None)
            render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            index_json = json.loads(
                (root_dir / "_board" / "flows" / "index.json").read_text(encoding="utf-8")
            )
            self.assertTrue(index_json["flows"][0]["awaiting"])

    def test_per_flow_page_shows_gate_id_and_escapes_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            _write_flow(
                flows_root, "dsi", "payoff", status="awaiting-owner",
                gate={"id": "G2", "status": "awaiting-owner", "token": "abcd"},
                idea="<script>x</script>",
            )
            render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            page = (root_dir / "_board" / "flows" / "dsi" / "payoff" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("dsi", page)
            self.assertIn("payoff", page)
            self.assertIn("G2", page)
            self.assertNotIn("<script>x</script>", page)
            self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
            self.assertTrue(
                (root_dir / "_board" / "flows" / "dsi" / "payoff" / "index.json").is_file()
            )

    def test_labels_for_every_listed_language_are_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir, languages=["en", "nl"])
            flows_root = root_dir / "flows"
            _write_flow(flows_root, "jarvis", "one")
            render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            html = (root_dir / "_board" / "flows" / "index.html").read_text(encoding="utf-8")
            self.assertIn('__ZND_BOARD_LANGS__ = ["en", "nl"]', html)
            self.assertIn('"waiting_on_you": "Wacht op jou"', html)  # nl label bundle embedded
            self.assertEqual(html.count("<style>"), 1)
            self.assertIn(":root", html)
            self.assertIn('body[data-theme="light"]', html)

    def test_no_bare_label_keys_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            flows_root = root_dir / "flows"
            render_board.main(["flows", str(flows_root), "--root", str(root_dir)])
            html = (root_dir / "_board" / "flows" / "index.html").read_text(encoding="utf-8")
            style_end = html.index("</style>") + len("</style>")
            body = html[style_end:]
            self.assertNotIn(">flows<", body)
            self.assertNotIn(">state_empty<", body)


class TestSprintsKind(unittest.TestCase):
    def test_empty_root_writes_zero_sprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            sprints_root = root_dir / "sprints"
            sprints_root.mkdir()
            rc = render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            index_json = json.loads(
                (root_dir / "_board" / "sprints" / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index_json["sprints"], [])
            html = (root_dir / "_board" / "sprints" / "index.html").read_text(encoding="utf-8")
            self.assertIn("empty", html)

    def test_two_sprints_progress_and_status_badge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            sprints_root = root_dir / "sprints"
            _write_sprint(sprints_root, "shoppilot", "done-one", {
                "project": "shoppilot", "slug": "done-one", "status": "passing",
                "phase": "done", "iteration": 3, "max_iterations": 5,
                "updated_at": "2026-09-10T10:00:00Z",
            })
            _write_sprint(sprints_root, "shoppilot", "running-one", {
                "project": "shoppilot", "slug": "running-one", "status": "running",
                "phase": "dev", "iteration": 2, "max_iterations": 4,
                "updated_at": "2026-09-11T09:00:00Z",
            })
            rc = render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            index_json = json.loads(
                (root_dir / "_board" / "sprints" / "index.json").read_text(encoding="utf-8")
            )
            by_slug = {s["slug"]: s for s in index_json["sprints"]}
            self.assertEqual(by_slug["done-one"]["progress"], 100)
            # spec(5) + plan(10) completed, dev(40) at iteration 2 of 4: 15 + round(40*2/4) = 35
            self.assertEqual(by_slug["running-one"]["progress"], 35)
            html = (root_dir / "_board" / "sprints" / "index.html").read_text(encoding="utf-8")
            self.assertIn("badge", html)
            self.assertIn("board-table", html)


class TestSprintDetailPage(unittest.TestCase):
    """Ported from sprints\\_lib\\test_render_dashboard.py's content assertions
    (guide step 11), rewritten against the new per-sprint page, not copied
    (the old tests import render_dashboard directly; this board never
    imports it). Dropped, not ported, and why: the filterable raw
    action-log table (`id="log-q"` etc.) and the plain "Links" section are
    not in the guide's step 11 scope (the "communication log" item asks only
    for the curated dispatch/decision/escalate subset, reused through the
    activity panel, not a second full log-table renderer); the CLI-only
    `status`/`tail`/`watch` text-mode features have no HTML output to port
    at all. Old assertion count concerning render_sprint's HTML: ~20 across
    RenderSprintTests, ComputeStatsTests, ModelRoutingRenderTests and
    DashboardEnhancementsTests; ported count: 16 below; dropped: 4 (the
    filter-control id assertions and the raw per-row data-phase/data-actor
    assertions, superseded by the curated communication log)."""

    def _sprint(self, **overrides):
        base = {
            "project": "demo", "slug": "hello", "phase": "dev", "status": "running",
            "iteration": 1, "max_iterations": 5,
            "created_at": "2026-07-07T00:00:00Z", "updated_at": "2026-07-07T00:10:00Z",
        }
        base.update(overrides)
        return base

    def _render(self, tmp, project, slug, sprint_data, log_entries=None, root_languages=None):
        root_dir = pathlib.Path(tmp)
        _write_holding_root(root_dir, languages=root_languages)
        sprints_root = root_dir / "sprints"
        _write_sprint(sprints_root, project, slug, sprint_data, log_entries)
        rc = render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
        self.assertEqual(rc, 0)
        page = (root_dir / "_board" / "sprints" / project / slug / "index.html").read_text(
            encoding="utf-8"
        )
        return page

    def test_writes_per_sprint_page_with_project_slug_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint(iteration=1, max_iterations=5),
                                 log_entries=[{"ts": "2026-07-07T00:00:00Z", "iteration": 0,
                                               "phase": "spec", "actor": "orchestrator",
                                               "action": "sprint_start", "outcome": "info",
                                               "detail": "started"}])
            self.assertIn("demo", page)
            self.assertIn("hello", page)
            self.assertIn("Iteration 1 of 5", page)
            self.assertIn("sprint_start", page)

    def test_html_escapes_log_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint(),
                                 log_entries=[{"ts": "2026-07-07T00:00:00Z", "iteration": 0,
                                               "phase": "spec", "actor": "orchestrator",
                                               "action": "sprint_start", "outcome": "info",
                                               "detail": "<script>x</script>"}])
            self.assertNotIn("<script>x</script>", page)
            self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)

    def test_capped_status_renders_badge(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello",
                                 self._sprint(phase="report", iteration=5, status="capped"))
            self.assertIn("capped", page.lower())
            self.assertIn('class="badge halt"', page)

    def test_tasks_table_shown_when_present(self):
        tasks = [
            {"n": 1, "title": "scaffold package", "state": "done", "commit": "3f9a1c2",
             "started_at": "2026-07-07T00:00:00Z", "ended_at": "2026-07-07T00:01:44Z",
             "model": "haiku"},
            {"n": 2, "title": "tool dispatcher", "state": "running", "model": "sonnet"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint(tasks=tasks))
            self.assertIn("Tasks: 1 of 2 done, 1 running", page)
            self.assertIn("scaffold package", page)
            self.assertIn("3f9a1c2", page)

    def test_tasks_section_omitted_when_no_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint())
            self.assertNotIn("<h3>Tasks</h3>", page)

    def test_phase_timeline_renders_all_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint(phase="dev"))
            for phase in kind_sprint._SPRINT_PHASE_ORDER + ["done"]:
                icon = kind_sprint._SPRINT_PHASE_ICONS.get(phase, "")
                self.assertIn(f">{icon} {phase}<", page)

    def test_failure_banner_appears_for_failing_test_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint = self._sprint(last_test_result={"exit_code": 1, "summary": "3 failed"})
            page = self._render(tmp, "demo", "hello", sprint)
            self.assertIn("Attention required", page)
            self.assertIn("tests exit 1", page)

    def test_no_failure_banner_when_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint(status="running"))
            self.assertNotIn("Attention required", page)

    def test_iteration_card_shows_pass_health_when_all_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(
                tmp, "demo", "hello", self._sprint(phase="done", status="passing"),
                log_entries=[
                    {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "test",
                     "actor": "test", "action": "test_run", "outcome": "pass", "detail": "50 passed"},
                    {"ts": "2026-07-07T00:01:00Z", "iteration": 1, "phase": "test",
                     "actor": "verify", "action": "verify_run", "outcome": "pass",
                     "detail": "criteria met"},
                ],
            )
            self.assertIn("h-green", page)

    def test_iteration_cards_scroll_not_grow_regardless_of_count(self):
        """item 1.7: the iteration cards grid must wrap in the fixed-height,
        internally-scrolling panel-scroll class whether there is one
        iteration or many, so the sprint page's own footprint never grows."""
        with tempfile.TemporaryDirectory() as tmp:
            few = self._render(
                tmp, "demo", "few", self._sprint(phase="done", status="passing", iteration=1),
                log_entries=[
                    {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "test",
                     "actor": "test", "action": "test_run", "outcome": "pass", "detail": "1"},
                ],
            )
        with tempfile.TemporaryDirectory() as tmp:
            many_log = [
                {"ts": f"2026-07-07T00:{i:02d}:00Z", "iteration": i, "phase": "test",
                 "actor": "test", "action": "test_run", "outcome": "pass", "detail": str(i)}
                for i in range(1, 31)
            ]
            many = self._render(
                tmp, "demo", "many", self._sprint(phase="done", status="passing", iteration=30),
                log_entries=many_log,
            )
        self.assertIn("panel-scroll", few)
        self.assertIn("panel-scroll", many)

    def test_actor_table_lists_dispatched_actors(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(
                tmp, "demo", "hello", self._sprint(),
                log_entries=[
                    {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
                     "actor": "agent:implementer", "action": "dispatch", "outcome": "pass",
                     "detail": "task 1"},
                    {"ts": "2026-07-07T00:01:00Z", "iteration": 1, "phase": "dev",
                     "actor": "agent:reviewer", "action": "decision", "outcome": "pass",
                     "detail": "approved"},
                ],
            )
            self.assertIn("agent:implementer", page)
            self.assertIn("agent:reviewer", page)
            self.assertIn("board-table", page)

    def test_communication_section_shows_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(
                tmp, "demo", "hello", self._sprint(),
                log_entries=[
                    {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
                     "actor": "agent:implementer", "action": "dispatch", "outcome": "pass",
                     "detail": "task 1 done"},
                ],
            )
            self.assertIn("dispatch", page)
            self.assertIn("task 1 done", page)

    def test_model_per_phase_table_shows_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(
                tmp, "demo", "hello", self._sprint(),
                log_entries=[
                    {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
                     "actor": "agent:implementer", "action": "dispatch", "outcome": "pass",
                     "detail": "d", "model": "sonnet"},
                    {"ts": "2026-07-07T00:01:00Z", "iteration": 1, "phase": "test",
                     "actor": "test", "action": "test_run", "outcome": "pass",
                     "detail": "d", "model": "haiku"},
                ],
            )
            self.assertIn("Model per phase", page)
            self.assertIn("sonnet", page)
            self.assertIn("haiku", page)

    def test_live_refresh_meta_only_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            running_page = self._render(tmp, "demo", "running-one", self._sprint(status="running"))
            self.assertIn('http-equiv="refresh"', running_page)
        with tempfile.TemporaryDirectory() as tmp:
            done_page = self._render(
                tmp, "demo", "done-one",
                self._sprint(status="passing", phase="done", iteration=5))
            self.assertNotIn('http-equiv="refresh"', done_page)

    def test_resume_and_regen_snippets_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = self._render(tmp, "demo", "hello", self._sprint())
            self.assertIn("/sprint demo", page)
            self.assertIn("render_board.py sprints", page)

    def test_malformed_sprint_json_still_renders_full_page_not_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            sprints_root = root_dir / "sprints"
            sprint_dir = sprints_root / "demo" / "orphan"
            sprint_dir.mkdir(parents=True)
            (sprint_dir / "sprint.json").write_text("{not json", encoding="utf-8")
            rc = render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            page = (root_dir / "_board" / "sprints" / "demo" / "orphan" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("<h3>Iterations</h3>", page)
            self.assertIn("<h3>Actors</h3>", page)
            self.assertNotIn("<h3>Tasks</h3>", page)

    def test_running_sprint_with_no_report_yet_still_gets_a_page_and_an_index_row(self):
        """T09/R07: both the per-sprint page and the index row are now
        discovered via sprint.json, not report.md. A sprint still in
        spec/plan/dev/test/iterate has no report.md yet, and this is exactly
        when the old render_dashboard.render_sprint used to be called on
        every phase transition. Gating the INDEX on report.md (the previous
        behaviour) silently hid a running sprint from the list for most of
        its life; it must now appear, with its report marked not yet
        available."""
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            sprints_root = root_dir / "sprints"
            sprint_dir = sprints_root / "demo" / "running"
            sprint_dir.mkdir(parents=True)
            (sprint_dir / "sprint.json").write_text(json.dumps(
                self._sprint(status="running", phase="dev")), encoding="utf-8")
            rc = render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
            self.assertEqual(rc, 0)
            page_path = root_dir / "_board" / "sprints" / "demo" / "running" / "index.html"
            self.assertTrue(page_path.is_file())
            index_json = json.loads(
                (root_dir / "_board" / "sprints" / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(index_json["sprints"]), 1)  # appears before its report exists
            index_html = (root_dir / "_board" / "sprints" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("not yet available", index_html)

    def test_index_row_links_to_per_sprint_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            _write_holding_root(root_dir)
            sprints_root = root_dir / "sprints"
            _write_sprint(sprints_root, "demo", "hello", self._sprint())
            render_board.main(["sprints", str(sprints_root), "--root", str(root_dir)])
            index_html = (root_dir / "_board" / "sprints" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('<a href="demo/hello/index.html">hello</a>', index_html)


class TestNoImportCycle(unittest.TestCase):
    """Decision 13 (2026-09-12 resume guide, step 10b): render_board.py
    imports the kinds; never the other way round. Run in a fresh subprocess
    so this test file's own earlier `import render_board` at the top cannot
    contaminate the check."""

    def test_kinds_do_not_import_render_board(self):
        import subprocess
        import sys as _sys

        script = (
            "import sys, pathlib;"
            f"sys.path.insert(0, r'{BOARD_DIR}');"
            "import kinds.flow, kinds.sprint;"
            "assert 'render_board' not in sys.modules, sorted(sys.modules);"
            "print('OK')"
        )
        result = subprocess.run([_sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
