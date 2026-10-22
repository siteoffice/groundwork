"""Tests for render_dashboard.py. Stdlib unittest, no external deps."""
import json
import pathlib
import tempfile
import unittest

import render_dashboard as rd


def _write_sprint(tmp: pathlib.Path, sprint: dict, log_lines: list) -> pathlib.Path:
    sprint_dir = tmp / sprint["project"] / sprint["slug"]
    sprint_dir.mkdir(parents=True, exist_ok=True)
    (sprint_dir / "sprint.json").write_text(json.dumps(sprint), encoding="utf-8")
    (sprint_dir / "action-log.ndjson").write_text(
        "\n".join(json.dumps(x) for x in log_lines) + "\n", encoding="utf-8"
    )
    return sprint_dir


class ComputeProgressTests(unittest.TestCase):
    def test_spec_phase_returns_zero_progress_for_zero_iteration(self):
        s = {"phase": "spec", "iteration": 0, "max_iterations": 5, "status": "running"}
        self.assertEqual(rd.compute_progress(s), 0)

    def test_dev_phase_iteration_1_of_5_adds_fractional_dev_weight(self):
        s = {"phase": "dev", "iteration": 1, "max_iterations": 5, "status": "running"}
        # completed: spec (5) + plan (10) = 15; current: dev weight 40 * 1/5 = 8; total 23
        self.assertEqual(rd.compute_progress(s), 23)

    def test_running_never_reaches_100(self):
        s = {"phase": "report", "iteration": 5, "max_iterations": 5, "status": "running"}
        self.assertLessEqual(rd.compute_progress(s), 99)

    def test_done_is_exactly_100(self):
        s = {"phase": "done", "iteration": 5, "max_iterations": 5, "status": "passing"}
        self.assertEqual(rd.compute_progress(s), 100)

    def test_dev_phase_with_tasks_uses_tasks_done_over_total(self):
        tasks = [{"n": i, "state": "done" if i <= 3 else "pending"} for i in range(1, 10)]
        s = {"phase": "dev", "iteration": 1, "max_iterations": 5, "status": "running",
             "tasks": tasks}
        # completed: spec (5) + plan (10) = 15; current: dev weight 40 * 3/9 = 13; total 28
        self.assertEqual(rd.compute_progress(s), 28)

    def test_dev_phase_with_empty_tasks_falls_back_to_iteration_formula(self):
        s = {"phase": "dev", "iteration": 1, "max_iterations": 5, "status": "running",
             "tasks": []}
        self.assertEqual(rd.compute_progress(s), 23)


class ComputeStatsTests(unittest.TestCase):
    def test_actor_and_outcome_counts(self):
        sprint = {
            "created_at": "2026-07-07T00:00:00Z",
            "updated_at": "2026-07-07T00:10:00Z",
            "phase": "test", "status": "running",
            "iteration": 1, "max_iterations": 5,
        }
        log = [
            {"ts": "2026-07-07T00:00:00Z", "iteration": 0, "phase": "spec",
             "actor": "orchestrator", "action": "sprint_start",
             "outcome": "info", "detail": "d", "ref": None},
            {"ts": "2026-07-07T00:01:00Z", "iteration": 1, "phase": "dev",
             "actor": "agent:implementer", "action": "dispatch",
             "outcome": "pass", "detail": "d", "ref": None},
            {"ts": "2026-07-07T00:02:00Z", "iteration": 1, "phase": "test",
             "actor": "test", "action": "test_run",
             "outcome": "fail", "detail": "3 failed", "ref": None},
        ]
        stats = rd.compute_stats(sprint, log)
        self.assertEqual(stats["outcome_counts"]["pass"], 1)
        self.assertEqual(stats["outcome_counts"]["fail"], 1)
        self.assertEqual(stats["outcome_counts"]["info"], 1)
        self.assertEqual(stats["actor_counts"]["orchestrator"]["total"], 1)
        self.assertEqual(stats["actor_counts"]["agent:implementer"]["pass"], 1)
        self.assertEqual(stats["actor_counts"]["test"]["fail"], 1)
        self.assertEqual(stats["last_error"], "3 failed")

    def test_iterations_rollup_captures_test_and_verify_outcomes(self):
        sprint = {"phase": "iterate", "status": "running",
                  "iteration": 1, "max_iterations": 5,
                  "created_at": "2026-07-07T00:00:00Z",
                  "updated_at": "2026-07-07T00:10:00Z"}
        log = [
            {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "test",
             "actor": "test", "action": "test_run", "outcome": "pass",
             "detail": "47 passed", "ref": None},
            {"ts": "2026-07-07T00:05:00Z", "iteration": 1, "phase": "test",
             "actor": "verify", "action": "verify_run", "outcome": "fail",
             "detail": "regression", "ref": None},
        ]
        stats = rd.compute_stats(sprint, log)
        self.assertEqual(len(stats["iterations"]), 1)
        it = stats["iterations"][0]
        self.assertEqual(it["iteration"], 1)
        self.assertEqual(it["test_outcome"], "pass")
        self.assertEqual(it["verify_outcome"], "fail")
        self.assertEqual(it["entries"], 2)

    def test_completed_phases_reflect_current_phase(self):
        sprint = {"phase": "dev", "status": "running",
                  "iteration": 1, "max_iterations": 5,
                  "created_at": "2026-07-07T00:00:00Z",
                  "updated_at": "2026-07-07T00:00:00Z"}
        stats = rd.compute_stats(sprint, [])
        self.assertEqual(stats["completed_phases"], ["spec", "plan"])

    def test_done_phase_marks_all_complete(self):
        sprint = {"phase": "done", "status": "passing",
                  "iteration": 5, "max_iterations": 5,
                  "created_at": "2026-07-07T00:00:00Z",
                  "updated_at": "2026-07-07T00:10:00Z"}
        stats = rd.compute_stats(sprint, [])
        self.assertEqual(stats["completed_phases"], rd.PHASE_ORDER)

    def test_phase_models_aggregates_optional_model_field(self):
        sprint = {
            "created_at": "2026-07-07T00:00:00Z",
            "updated_at": "2026-07-07T00:10:00Z",
            "phase": "test", "status": "running",
            "iteration": 1, "max_iterations": 5,
        }
        log = [
            {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
             "actor": "agent:implementer", "action": "dispatch",
             "outcome": "pass", "detail": "task 1", "ref": None,
             "model": "sonnet"},
            {"ts": "2026-07-07T00:01:00Z", "iteration": 1, "phase": "dev",
             "actor": "agent:implementer", "action": "dispatch",
             "outcome": "pass", "detail": "task 2", "ref": None,
             "model": "sonnet"},
            {"ts": "2026-07-07T00:02:00Z", "iteration": 1, "phase": "test",
             "actor": "test", "action": "test_run",
             "outcome": "pass", "detail": "50 passed", "ref": None,
             "model": "haiku"},
            {"ts": "2026-07-07T00:03:00Z", "iteration": 1, "phase": "spec",
             "actor": "orchestrator", "action": "sprint_start",
             "outcome": "info", "detail": "no model field on this one",
             "ref": None},
        ]
        stats = rd.compute_stats(sprint, log)
        self.assertEqual(stats["phase_models"]["dev"], {"sonnet": 2})
        self.assertEqual(stats["phase_models"]["test"], {"haiku": 1})
        self.assertNotIn("spec", stats["phase_models"])


class DurationFormatTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(rd._format_duration(45), "45s")

    def test_minutes(self):
        self.assertEqual(rd._format_duration(125), "2m 05s")

    def test_hours(self):
        self.assertEqual(rd._format_duration(3725), "1h 02m 05s")

    def test_negative_or_none_is_zero(self):
        self.assertEqual(rd._format_duration(-5), "0s")
        self.assertEqual(rd._format_duration(None), "0s")


class DelegationTests(unittest.TestCase):
    """Siteoffice fold (sprint 1 step 12): render_sprint and render_index no
    longer build HTML themselves, they delegate to board.render_board and
    return its new _board\\ path. Content assertions moved to
    board\\tests\\test_board_kinds.py::TestSprintDetailPage and TestSprintsKind
    in step 11; these two tests only prove the delegation happened and the
    output path exists, per the guide's own instruction."""

    def _sprint(self, **overrides):
        base = {
            "project": "demo", "slug": "hello", "phase": "dev", "status": "running",
            "iteration": 1, "max_iterations": 5,
            "created_at": "2026-07-07T00:00:00Z", "updated_at": "2026-07-07T00:10:00Z",
        }
        base.update(overrides)
        return base

    def test_render_sprint_delegates_and_returns_new_board_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [
                {"ts": "2026-07-07T00:00:00Z", "iteration": 0, "phase": "spec",
                 "actor": "orchestrator", "action": "sprint_start",
                 "outcome": "info", "detail": "started", "ref": None},
            ])
            out = rd.render_sprint(sd)
            self.assertEqual(
                out, sd.parent.parent.parent / "_board" / "sprints" / "demo" / "hello" / "index.html"
            )
            self.assertTrue(out.exists())
            html = out.read_text(encoding="utf-8")
            self.assertIn("demo", html)
            self.assertIn("hello", html)

    def test_render_index_delegates_and_returns_new_board_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_sprint(root, self._sprint(project="a", slug="one"), [])
            out = rd.render_index(root)
            self.assertEqual(out, root.parent / "_board" / "sprints" / "index.html")
            self.assertTrue(out.exists())


class TextStatusTests(unittest.TestCase):
    def _sprint(self, **overrides):
        base = {
            "schema_version": 1,
            "sprint_id": "demo__hello__20260707T000000Z",
            "project": "demo", "slug": "hello",
            "spec_path": "docs/hello.md", "plan_path": "docs/hello-plan.md",
            "worktree_path": "w", "worktree_branch": "b",
            "max_iterations": 5, "iteration": 1,
            "phase": "dev", "status": "running",
            "created_at": "2026-07-07T00:00:00Z",
            "updated_at": "2026-07-07T00:10:00Z",
            "last_test_result": None, "last_verify_result": None,
        }
        base.update(overrides)
        return base

    def test_status_reports_error_when_no_sprint_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "empty"
            root.mkdir()
            out = rd.render_status_text(root, colour=False)
            self.assertIn("error:", out)
            self.assertIn("no sprint.json", out)

    def test_status_reports_malformed_sprint_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bad"
            root.mkdir()
            (root / "sprint.json").write_text("not json", encoding="utf-8")
            out = rd.render_status_text(root, colour=False)
            self.assertIn("error:", out)
            self.assertIn("malformed", out)

    def test_status_shows_project_slug_status_and_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp),
                self._sprint(iteration=2, max_iterations=5, status="running"),
                [{"ts": "2026-07-07T00:00:00Z", "iteration": 0, "phase": "spec",
                  "actor": "orchestrator", "action": "sprint_start",
                  "outcome": "info", "detail": "started", "ref": None}])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("/sprint demo / hello", out)
            self.assertIn("RUNNING", out)
            self.assertIn("2 of 5", out)
            self.assertIn("Timeline:", out)
            self.assertIn("[dev]", out)

    def test_status_shows_task_table_when_tasks_present(self):
        tasks = [
            {"n": 1, "title": "scaffold package", "state": "done", "attempts": 1,
             "commit": "3f9a1c2", "started_at": "2026-07-07T00:00:00Z",
             "ended_at": "2026-07-07T00:01:44Z", "model": "haiku"},
            {"n": 2, "title": "tool dispatcher", "state": "running", "attempts": 1,
             "commit": None, "started_at": "2026-07-07T00:02:00Z",
             "ended_at": None, "model": "sonnet"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(tasks=tasks), [])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("Tasks: 1 of 2 done, 1 running", out)
            self.assertIn("scaffold package", out)
            self.assertIn("tool dispatcher", out)

    def test_status_omits_task_section_when_no_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [])
            out = rd.render_status_text(sd, colour=False)
            self.assertNotIn("Tasks:", out)

    def test_status_progress_bar_renders_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp),
                self._sprint(phase="dev", iteration=1, max_iterations=5), [])
            out = rd.render_status_text(sd, colour=False)
            self.assertRegex(out, r"\[[#\-]+\]\s+\d+%")

    def test_status_shows_failure_when_test_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = self._sprint(last_test_result={
                "command": "pytest", "exit_code": 1,
                "summary": "3 failed", "at": "2026-07-07T00:05:00Z"})
            sd = _write_sprint(pathlib.Path(tmp), sp, [
                {"ts": "2026-07-07T00:05:00Z", "iteration": 1, "phase": "test",
                 "actor": "test", "action": "test_run", "outcome": "fail",
                 "detail": "3 failed", "ref": None}])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("FAILURE", out)
            self.assertIn("tests exit 1", out)

    def test_status_omits_failure_banner_when_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(),
                [{"ts": "2026-07-07T00:00:00Z", "iteration": 0, "phase": "spec",
                  "actor": "orchestrator", "action": "sprint_start",
                  "outcome": "info", "detail": "started", "ref": None}])
            out = rd.render_status_text(sd, colour=False)
            self.assertNotIn("FAILURE", out)

    def test_status_lists_actors_with_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [
                {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
                 "actor": "agent:implementer", "action": "dispatch",
                 "outcome": "pass", "detail": "d", "ref": None},
                {"ts": "2026-07-07T00:00:00Z", "iteration": 1, "phase": "dev",
                 "actor": "agent:reviewer", "action": "decision",
                 "outcome": "pass", "detail": "approved", "ref": None}])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("Actors:", out)
            self.assertIn("agent:implementer", out)
            self.assertIn("agent:reviewer", out)

    def test_status_includes_recent_log_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [
                {"ts": "2026-07-07T00:05:00Z", "iteration": 1, "phase": "dev",
                 "actor": "agent:implementer", "action": "dispatch",
                 "outcome": "pass", "detail": "task 1 finished", "ref": None}])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("Last", out)
            self.assertIn("task 1 finished", out)
            # Timestamps rendered as HH:MM:SS
            self.assertIn("00:05:00", out)

    def test_status_includes_resume_and_html_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [])
            out = rd.render_status_text(sd, colour=False)
            self.assertIn("Resume:", out)
            self.assertIn("/sprint demo", out)
            self.assertIn("dashboard.html", out)

    def test_status_ansi_disabled_when_no_color_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [])
            out = rd.render_status_text(sd, colour=False)
            self.assertNotIn("\x1b[", out)

    def test_status_ansi_present_when_color_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = _write_sprint(pathlib.Path(tmp), self._sprint(), [])
            out = rd.render_status_text(sd, colour=True)
            self.assertIn("\x1b[", out)


class WatchStatusTests(unittest.TestCase):
    def test_watch_exits_immediately_on_terminal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = {
                "schema_version": 1, "sprint_id": "x__y__z",
                "project": "x", "slug": "y",
                "spec_path": "s", "plan_path": "p",
                "worktree_path": "w", "worktree_branch": "b",
                "max_iterations": 3, "iteration": 3,
                "phase": "report", "status": "passing",
                "created_at": "2026-07-07T00:00:00Z",
                "updated_at": "2026-07-07T00:01:00Z",
                "last_test_result": None, "last_verify_result": None,
            }
            sd = _write_sprint(pathlib.Path(tmp), sp, [])
            rc = rd.watch_status_text(sd, interval=0.01, colour=False, max_iterations=5)
            self.assertEqual(rc, 0)

    def test_watch_respects_max_iterations_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = {
                "schema_version": 1, "sprint_id": "x__y__z",
                "project": "x", "slug": "y",
                "spec_path": "s", "plan_path": "p",
                "worktree_path": "w", "worktree_branch": "b",
                "max_iterations": 5, "iteration": 1,
                "phase": "dev", "status": "running",
                "created_at": "2026-07-07T00:00:00Z",
                "updated_at": "2026-07-07T00:01:00Z",
                "last_test_result": None, "last_verify_result": None,
            }
            sd = _write_sprint(pathlib.Path(tmp), sp, [])
            rc = rd.watch_status_text(sd, interval=0.01, colour=False, max_iterations=2)
            self.assertEqual(rc, 0)


class TailLogTests(unittest.TestCase):
    def test_tail_missing_log_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = rd.tail_log(pathlib.Path(tmp), follow=False, colour=False)
            self.assertNotEqual(rc, 0)

    def test_tail_prints_existing_entries_without_follow(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "action-log.ndjson").write_text(
                json.dumps({"ts": "2026-07-07T00:05:00Z", "iteration": 1,
                            "phase": "dev", "actor": "orchestrator",
                            "action": "sprint_start", "outcome": "info",
                            "detail": "hello world", "ref": None}) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rd.tail_log(root, follow=False, colour=False)
            self.assertEqual(rc, 0)
            self.assertIn("hello world", buf.getvalue())
            self.assertIn("sprint_start", buf.getvalue())

    def test_tail_skips_malformed_lines(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "action-log.ndjson").write_text(
                "this is not json\n" +
                json.dumps({"ts": "2026-07-07T00:05:00Z", "iteration": 1,
                            "phase": "dev", "actor": "test", "action": "test_run",
                            "outcome": "pass", "detail": "ok", "ref": None}) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rd.tail_log(root, follow=False, colour=False)
            self.assertEqual(rc, 0)
            self.assertIn("test_run", buf.getvalue())


class CLIExtraCommandsTests(unittest.TestCase):
    def test_status_cli_prints_dashboard(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sp = {
                "schema_version": 1, "sprint_id": "x__y__z",
                "project": "x", "slug": "y",
                "spec_path": "s", "plan_path": "p",
                "worktree_path": "w", "worktree_branch": "b",
                "max_iterations": 5, "iteration": 1,
                "phase": "dev", "status": "running",
                "created_at": "2026-07-07T00:00:00Z",
                "updated_at": "2026-07-07T00:01:00Z",
                "last_test_result": None, "last_verify_result": None,
            }
            sd = _write_sprint(root, sp, [
                {"ts": "2026-07-07T00:00:00Z", "iteration": 0, "phase": "spec",
                 "actor": "orchestrator", "action": "sprint_start",
                 "outcome": "info", "detail": "d", "ref": None}])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rd.main(["status", str(sd), "--no-color"])
            self.assertEqual(rc, 0)
            self.assertIn("/sprint x / y", buf.getvalue())
            self.assertIn("RUNNING", buf.getvalue())

    def test_tail_cli_reads_existing_entries(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "action-log.ndjson").write_text(
                json.dumps({"ts": "2026-07-07T00:05:00Z", "iteration": 1,
                            "phase": "dev", "actor": "orchestrator",
                            "action": "phase_transition", "outcome": "info",
                            "detail": "moved to dev", "ref": None}) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rd.main(["tail", str(root), "--no-color"])
            self.assertEqual(rc, 0)
            self.assertIn("moved to dev", buf.getvalue())


class SiteofficeRootTests(unittest.TestCase):
    def test_repo_root_checkout_is_found_without_a_50_projects_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "board").mkdir()
            lib = root / "foreman" / "sprints" / "_lib"
            lib.mkdir(parents=True)
            self.assertEqual(rd._find_siteoffice_root(lib), root)

    def test_walk_up_still_finds_50_projects_siteoffice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            site = root / "50_projects" / "siteoffice"
            site.mkdir(parents=True)
            lib = root / "x" / "y" / "z" / "_lib"
            lib.mkdir(parents=True)
            self.assertEqual(rd._find_siteoffice_root(lib), site)


if __name__ == "__main__":
    unittest.main()
