"""Tests for sprint_step.py. Stdlib unittest, no external deps."""
import json
import pathlib
import tempfile
import unittest

import sprint_step as ss


def _write_sprint(root: pathlib.Path, tasks=None, run_mode=None) -> pathlib.Path:
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
    if run_mode is not None:
        sprint["run_mode"] = run_mode
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


    def test_task_parked_sets_parked_and_queues_one_outbox_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp),
                                       tasks=[_task(1, state="running"), _task(2)],
                                       run_mode="unattended")
            sprint = ss.apply_step(
                sprint_dir, phase="dev", action="task_parked", actor="orchestrator",
                detail="needs the live API token", outcome="blocked", task=1,
            )
            states = {t["n"]: t["state"] for t in sprint["tasks"]}
            self.assertEqual(states, {1: "parked", 2: "pending"})
            lines = (sprint_dir / "outbox.ndjson").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["kind"], "task_parked")
            self.assertIn("needs the live API token", rec["body"])

    def test_parking_the_same_task_twice_queues_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)],
                                       run_mode="unattended")
            for _ in range(2):
                ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                              actor="orchestrator", detail="needs a person", task=1)
            lines = (sprint_dir / "outbox.ndjson").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)

    def test_reparking_after_resume_queues_a_second_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp),
                                       tasks=[_task(1), _task(2)],
                                       run_mode="unattended")

            def park():
                ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                              actor="orchestrator", detail="needs a person", task=2)

            def ids():
                lines = (sprint_dir / "outbox.ndjson").read_text(
                    encoding="utf-8").strip().splitlines()
                return [json.loads(x)["id"] for x in lines]

            park()
            self.assertEqual(len(ids()), 1)
            self.assertTrue(ids()[0].endswith("-T2-1"))
            ss.apply_step(sprint_dir, phase="dev", action="task_start",
                          actor="agent:implementer", detail="resumed", task=2)
            park()
            self.assertEqual(len(ids()), 2)
            self.assertTrue(ids()[1].endswith("-T2-2"))
            park()  # repeat of the second call while still parked
            self.assertEqual(len(ids()), 2)

    def test_reparking_a_task_parked_under_the_old_code_adds_no_notice(self):
        # Old code: no park_count, notice id OBX-PARK-<sprint_id>-T<n>, state already parked.
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp),
                                       tasks=[_task(1, state="parked")],
                                       run_mode="unattended")
            ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                          actor="orchestrator", detail="needs a person", task=1)
            outbox_file = sprint_dir / "outbox.ndjson"
            if outbox_file.exists():
                self.assertEqual(outbox_file.read_text(encoding="utf-8").strip(), "")
            sprint = json.loads((sprint_dir / "sprint.json").read_text(encoding="utf-8"))
            self.assertEqual(sprint["tasks"][0]["park_count"], 1)
            # A genuine new park after a resume still queues, as park 2.
            ss.apply_step(sprint_dir, phase="dev", action="task_start",
                          actor="agent:implementer", detail="resumed", task=1)
            ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                          actor="orchestrator", detail="needs a person", task=1)
            lines = outbox_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(json.loads(lines[0])["id"].endswith("-T1-2"))

    def test_attended_sprint_parks_without_a_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)],
                                       run_mode="attended")
            sprint = ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                                   actor="orchestrator", detail="needs a person",
                                   task=1)
            self.assertEqual(sprint["tasks"][0]["state"], "parked")
            outbox_file = sprint_dir / "outbox.ndjson"
            if outbox_file.exists():
                self.assertEqual(outbox_file.read_text(encoding="utf-8").strip(), "")
            log = (sprint_dir / "action-log.ndjson").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(json.loads(log[-1])["action"], "task_parked")

    def test_attended_check_ignores_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)],
                                       run_mode="Attended")
            ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                          actor="orchestrator", detail="needs a person", task=1)
            outbox_file = sprint_dir / "outbox.ndjson"
            if outbox_file.exists():
                self.assertEqual(outbox_file.read_text(encoding="utf-8").strip(), "")

    def test_sprint_without_run_mode_still_queues_a_park_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)])
            ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                          actor="orchestrator", detail="needs a person", task=1)
            lines = (sprint_dir / "outbox.ndjson").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(json.loads(lines[0])["id"].endswith("-T1-1"))

    def test_task_parked_without_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)])
            with self.assertRaises(ValueError):
                ss.apply_step(sprint_dir, phase="dev", action="task_parked",
                              actor="orchestrator", detail="no task arg")

    def test_touchpoint_actions_are_accepted_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), tasks=[_task(1)],
                                       run_mode="attended")
            ss.apply_step(sprint_dir, phase="dev", action="touchpoint_open",
                          actor="orchestrator", detail="paste the live token")
            ss.apply_step(sprint_dir, phase="dev", action="touchpoint_done",
                          actor="human", detail="token pasted", task=1)
            entries = [json.loads(x) for x in (sprint_dir / "action-log.ndjson")
                       .read_text(encoding="utf-8").strip().splitlines()]
            self.assertEqual([e["action"] for e in entries],
                             ["touchpoint_open", "touchpoint_done"])
            self.assertEqual({e["run_mode"] for e in entries}, {"HITL"})

    def test_unattended_receipts_carry_afk(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp), run_mode="unattended")
            ss.apply_step(sprint_dir, phase="spec", action="sprint_start",
                          actor="orchestrator", detail="spec resolved")
            entry = json.loads((sprint_dir / "action-log.ndjson")
                               .read_text(encoding="utf-8").strip())
            self.assertEqual(entry["run_mode"], "AFK")

    def test_no_run_mode_field_when_sprint_has_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sprint_dir = _write_sprint(pathlib.Path(tmp))
            ss.apply_step(sprint_dir, phase="spec", action="sprint_start",
                          actor="orchestrator", detail="spec resolved")
            entry = json.loads((sprint_dir / "action-log.ndjson")
                               .read_text(encoding="utf-8").strip())
            self.assertNotIn("run_mode", entry)

if __name__ == "__main__":
    unittest.main()
