"""Tests for sprint_step.py. Stdlib unittest, no external deps."""
import json
import pathlib
import tempfile
import unittest

import sprint_step as ss


def _write_sprint(root: pathlib.Path, tasks=None) -> pathlib.Path:
    sprint_dir = root / "demo" / "hello"
    sprint_dir.mkdir(parents=True, exist_ok=True)
    sprint = {
        "schema_version": 2 if tasks is not None else 1,
        "sprint_id": "demo__hello__20260707T000000Z",
        "project": "demo", "slug": "hello",
        "spec_path": "docs/superpowers/specs/hello.md",
        "plan_path": "docs/superpowers/plans/hello.md",
        "worktree_path": "demo/.worktrees/sprint-hello",
        "worktree_branch": "sprint/hello",
        "max_iterations": 5, "iteration": 1,
        "phase": "dev", "status": "running",
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:00:00Z",
        "last_test_result": None, "last_verify_result": None,
    }
    if tasks is not None:
        sprint["tasks"] = tasks
    (sprint_dir / "sprint.json").write_text(json.dumps(sprint), encoding="utf-8")
    (sprint_dir / "action-log.ndjson").write_text("", encoding="utf-8")
    return sprint_dir


def _task(n, state="pending"):
    return {"n": n, "title": f"task {n}", "state": state, "attempts": 0,
            "commit": None, "started_at": None, "ended_at": None, "model": None}


class ApplyStepTests(unittest.TestCase):
    def test_task_start_sets_running_and_increments_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)])
            sprint = ss.apply_step(
                sprint_dir, phase="dev", action="task_start", actor="agent:implementer",
                detail="starting task 1", task=1, model="sonnet",
            )
            t1 = next(t for t in sprint["tasks"] if t["n"] == 1)
            self.assertEqual(t1["state"], "running")
            self.assertEqual(t1["attempts"], 1)
            self.assertEqual(t1["model"], "sonnet")
            self.assertIsNotNone(t1["started_at"])

    def test_task_done_pass_sets_done_and_stamps_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1, state="running")])
            sprint = ss.apply_step(
                sprint_dir, phase="dev", action="task_done", actor="agent:implementer",
                detail="task 1 done", outcome="pass", task=1, commit="3f9a1c2",
            )
            t1 = next(t for t in sprint["tasks"] if t["n"] == 1)
            self.assertEqual(t1["state"], "done")
            self.assertEqual(t1["commit"], "3f9a1c2")
            self.assertIsNotNone(t1["ended_at"])

    def test_task_done_fail_sets_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1, state="running")])
            sprint = ss.apply_step(
                sprint_dir, phase="dev", action="task_done", actor="agent:implementer",
                detail="task 1 blocked", outcome="fail", task=1,
            )
            t1 = next(t for t in sprint["tasks"] if t["n"] == 1)
            self.assertEqual(t1["state"], "blocked")

    def test_unknown_action_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp))
            with self.assertRaises(ValueError):
                ss.apply_step(sprint_dir, phase="dev", action="bogus_action",
                               actor="orchestrator", detail="x")

    def test_task_action_without_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)])
            with self.assertRaises(ValueError):
                ss.apply_step(sprint_dir, phase="dev", action="task_start",
                               actor="agent:implementer", detail="missing task arg")

    def test_detail_with_newline_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp))
            with self.assertRaises(ValueError):
                ss.apply_step(sprint_dir, phase="dev", action="dispatch",
                               actor="orchestrator", detail="line one\nline two")

    def test_detail_too_long_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp))
            with self.assertRaises(ValueError):
                ss.apply_step(sprint_dir, phase="dev", action="dispatch",
                               actor="orchestrator", detail="x" * 241)

    def test_appends_exactly_one_log_line_and_writes_dashboard(self):
        # Siteoffice fold (sprint 1 step 12): the dashboard render_sprint()
        # triggers now lands at _board\sprints\<project>\<slug>\index.html,
        # not the old dashboard.html next to sprint.json.
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)])
            ss.main([
                str(sprint_dir), "--phase", "dev", "--action", "task_start",
                "--actor", "agent:implementer", "--detail", "starting task 1",
                "--task", "1",
            ])
            log_lines = (sprint_dir / "action-log.ndjson").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(log_lines), 1)
            entry = json.loads(log_lines[0])
            self.assertEqual(entry["action"], "task_start")
            self.assertEqual(entry["task"], 1)
            sprints_root = sprint_dir.parent.parent
            board_page = (sprints_root.parent / "_board" / "sprints"
                          / sprint_dir.relative_to(sprints_root) / "index.html")
            self.assertTrue(board_page.exists())


if __name__ == "__main__":
    unittest.main()
