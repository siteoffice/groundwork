"""git_ops.py exercised against throwaway temp repos only, never the real
siteoffice-private clone (Global Constraint). Each test builds a fresh local
repo plus a bare "remote" repo in a TemporaryDirectory, so pull/push are real
local git operations with zero network egress.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SYNC_DIR = HERE.parent
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))
import git_ops  # noqa: E402


def _run(repo_dir, *args):
    return subprocess.run(
        ["git", *args], cwd=str(repo_dir), capture_output=True, text=True, check=True,
    )


def _init_repo_pair(tmp: pathlib.Path):
    """Returns (clone_dir, remote_bare_dir). clone_dir has one commit,
    pushed to remote_bare_dir, with origin/main tracked as upstream."""
    remote_dir = tmp / "remote.git"
    remote_dir.mkdir()
    _run(remote_dir, "init", "--bare", "-b", "main")

    clone_dir = tmp / "clone"
    clone_dir.mkdir()
    _run(clone_dir, "init", "-b", "main")
    _run(clone_dir, "config", "user.email", "test@example.com")
    _run(clone_dir, "config", "user.name", "Test Clone")
    (clone_dir / "hello.md").write_text("hello\n", encoding="utf-8")
    _run(clone_dir, "add", "hello.md")
    _run(clone_dir, "commit", "-m", "initial")
    _run(clone_dir, "remote", "add", "origin", str(remote_dir))
    _run(clone_dir, "push", "-u", "origin", "main")
    return clone_dir, remote_dir


def _second_clone(tmp: pathlib.Path, remote_dir: pathlib.Path, name: str) -> pathlib.Path:
    clone_dir = tmp / name
    _run(tmp, "clone", str(remote_dir), name)
    _run(clone_dir, "config", "user.email", "test2@example.com")
    _run(clone_dir, "config", "user.name", "Test Clone Two")
    return clone_dir


class TestGetStatus(unittest.TestCase):
    def test_clean_clone_reports_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            status = git_ops.get_status(clone_dir, "test/node", "clone-a")
            self.assertEqual(status["state"], "ok")
            self.assertEqual(status["behind"], 0)
            self.assertEqual(status["ahead"], 0)
            self.assertEqual(status["conflicts"], [])
            self.assertEqual(status["node"], "test/node")
            self.assertEqual(status["clone"], "clone-a")

    def test_clone_behind_remote_reports_behind_with_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote_dir = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote_dir, "clone_b")
            (clone_a / "second.md").write_text("more\n", encoding="utf-8")
            _run(clone_a, "add", "second.md")
            _run(clone_a, "commit", "-m", "second commit")
            _run(clone_a, "push", "origin", "main")
            git_ops.run_git(clone_b, ["fetch", "origin"])
            status = git_ops.get_status(clone_b, "test/node", "clone-b")
            self.assertEqual(status["state"], "behind")
            self.assertEqual(status["behind"], 1)
            self.assertEqual(status["ahead"], 0)

    def test_clone_ahead_of_remote_reports_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "local_only.md").write_text("wip\n", encoding="utf-8")
            _run(clone_dir, "add", "local_only.md")
            _run(clone_dir, "commit", "-m", "not pushed yet")
            status = git_ops.get_status(clone_dir, "test/node", "clone-a")
            self.assertEqual(status["state"], "ok")  # ahead alone is not "behind"
            self.assertEqual(status["ahead"], 1)
            self.assertEqual(status["behind"], 0)


class TestPullConflict(unittest.TestCase):
    def test_conflicting_pull_writes_incoming_copy_and_leaves_clean_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote_dir = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote_dir, "clone_b")

            (clone_a / "hello.md").write_text("hello from A\n", encoding="utf-8")
            _run(clone_a, "add", "hello.md")
            _run(clone_a, "commit", "-m", "A edits hello")
            _run(clone_a, "push", "origin", "main")

            (clone_b / "hello.md").write_text("hello from B\n", encoding="utf-8")
            _run(clone_b, "add", "hello.md")
            _run(clone_b, "commit", "-m", "B edits hello, conflicting")

            status = git_ops.pull(clone_b, "test/node", "clone-b")

            self.assertEqual(status["state"], "conflict")
            self.assertIn("hello.md", status["conflicts"])
            incoming_path = clone_b / "hello.incoming.md"
            self.assertTrue(incoming_path.exists())
            self.assertIn("hello from A", incoming_path.read_text(encoding="utf-8"))
            # local file untouched
            self.assertEqual((clone_b / "hello.md").read_text(encoding="utf-8"), "hello from B\n")
            # no unmerged git state
            porcelain = _run(clone_b, "status", "--porcelain").stdout
            self.assertNotIn("UU", porcelain)

    def test_conflicting_pull_on_a_non_md_file_is_still_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote_dir = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote_dir, "clone_b")

            # clone_a and clone_b already share _init_repo_pair's initial
            # hello.md commit; no extra seed commit is needed before the
            # two clones diverge on config.json below.
            (clone_a / "config.json").write_text('{"v": "a"}', encoding="utf-8")
            _run(clone_a, "add", "config.json")
            _run(clone_a, "commit", "-m", "A adds config.json")
            _run(clone_a, "push", "origin", "main")

            (clone_b / "config.json").write_text('{"v": "b"}', encoding="utf-8")
            _run(clone_b, "add", "config.json")
            _run(clone_b, "commit", "-m", "B adds config.json, conflicting")

            status = git_ops.pull(clone_b, "test/node", "clone-b")

            self.assertEqual(status["state"], "conflict")
            self.assertIn("config.json", status["conflicts"])
            self.assertTrue((clone_b / "config.incoming.json").exists())

    def test_write_failure_during_conflict_still_aborts_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote_dir = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote_dir, "clone_b")

            (clone_a / "hello.md").write_text("hello from A\n", encoding="utf-8")
            _run(clone_a, "add", "hello.md")
            _run(clone_a, "commit", "-m", "A edits hello")
            _run(clone_a, "push", "origin", "main")

            (clone_b / "hello.md").write_text("hello from B\n", encoding="utf-8")
            _run(clone_b, "add", "hello.md")
            _run(clone_b, "commit", "-m", "B edits hello, conflicting")

            import unittest.mock as mock
            with mock.patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
                status = git_ops.pull(clone_b, "test/node", "clone-b")

            self.assertEqual(status["state"], "error")
            porcelain = _run(clone_b, "status", "--porcelain").stdout
            self.assertNotIn("UU", porcelain)
            # confirm no merge is in progress
            merge_head = clone_b / ".git" / "MERGE_HEAD"
            self.assertFalse(merge_head.exists())


class TestCommitRefusal(unittest.TestCase):
    def _owners_stub(self, mapping):
        def resolve_owner(repo_dir, path):
            return mapping.get(path)
        return resolve_owner

    def test_commit_with_no_owners_map_commits_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "a.md").write_text("a\n", encoding="utf-8")
            (clone_dir / "b.md").write_text("b\n", encoding="utf-8")
            resolve_owner = self._owners_stub({})  # no owners anywhere
            result = git_ops.commit(clone_dir, "add a and b", "wilco", resolve_owner)
            self.assertEqual(sorted(result["committed"]), ["a.md", "b.md"])
            self.assertEqual(result["refused"], [])
            # _log/ is expected here: a successful commit writes its own
            # receipt file there (untracked by design, never part of the
            # commit itself).
            porcelain = _run(clone_dir, "status", "--porcelain").stdout
            self.assertEqual(porcelain.strip(), "?? _log/")

    def test_commit_with_one_path_owned_by_someone_else_refuses_only_that_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "mine.md").write_text("mine\n", encoding="utf-8")
            (clone_dir / "theirs.md").write_text("theirs\n", encoding="utf-8")
            resolve_owner = self._owners_stub({"theirs.md": "someone-else"})
            result = git_ops.commit(clone_dir, "attempt both", "wilco", resolve_owner)
            self.assertEqual(result["committed"], ["mine.md"])
            self.assertEqual(len(result["refused"]), 1)
            self.assertEqual(result["refused"][0]["path"], "theirs.md")
            self.assertIn("someone-else", result["refused"][0]["message"])
            self.assertEqual(result["state"], "refused")
            # theirs.md still shows as an uncommitted change
            porcelain = _run(clone_dir, "status", "--porcelain").stdout
            self.assertIn("theirs.md", porcelain)
            self.assertNotIn("mine.md", porcelain)

    def test_new_untracked_directory_is_committed_per_file_not_as_one_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "newdir").mkdir()
            (clone_dir / "newdir" / "one.md").write_text("one\n", encoding="utf-8")
            (clone_dir / "newdir" / "two.md").write_text("two\n", encoding="utf-8")
            resolve_owner = self._owners_stub({"newdir/two.md": "someone-else"})
            result = git_ops.commit(clone_dir, "add newdir", "wilco", resolve_owner)
            self.assertEqual(result["committed"], ["newdir/one.md"])
            self.assertEqual(len(result["refused"]), 1)
            self.assertEqual(result["refused"][0]["path"], "newdir/two.md")

    def test_prestaged_refused_path_is_not_committed(self):
        # C1: a path already staged (by the user, or left over from a
        # prior failed call) before commit() ever runs must never end up
        # in the actual commit just because it sits in the index; only
        # allowed paths may ever be committed.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "mine.md").write_text("mine\n", encoding="utf-8")
            (clone_dir / "theirs.md").write_text("theirs\n", encoding="utf-8")
            _run(clone_dir, "add", "theirs.md")  # pre-staged, simulating a leftover/foreign stage
            resolve_owner = self._owners_stub({"theirs.md": "someone-else"})
            result = git_ops.commit(clone_dir, "attempt both", "wilco", resolve_owner)
            self.assertEqual(result["committed"], ["mine.md"])
            self.assertEqual(len(result["refused"]), 1)
            self.assertEqual(result["refused"][0]["path"], "theirs.md")
            # the real git history is the actual proof: HEAD must not
            # contain theirs.md, regardless of what the response claims
            committed_files = _run(
                clone_dir, "show", "--name-only", "--pretty=format:", "HEAD",
            ).stdout.split()
            self.assertNotIn("theirs.md", committed_files)
            self.assertIn("mine.md", committed_files)
            # still staged (pre-staged, not committed, not lost)
            porcelain = _run(clone_dir, "status", "--porcelain").stdout
            self.assertIn("theirs.md", porcelain)


class TestCommitFailure(unittest.TestCase):
    """C2: a failed commit (empty message, a pre-commit hook rejecting it,
    'nothing to commit') must be reported as state == 'error' with
    committed == [], never as a false success, and nothing may actually
    land in git history."""

    def _owners_stub(self, mapping):
        def resolve_owner(repo_dir, path):
            return mapping.get(path)
        return resolve_owner

    def test_empty_message_is_reported_as_error_and_nothing_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "new.md").write_text("new\n", encoding="utf-8")
            head_before = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()
            result = git_ops.commit(clone_dir, "", "wilco", self._owners_stub({}))
            self.assertEqual(result["state"], "error")
            self.assertEqual(result["committed"], [])
            head_after = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(head_before, head_after)

    def test_whitespace_only_message_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "new.md").write_text("new\n", encoding="utf-8")
            result = git_ops.commit(clone_dir, "   ", "wilco", self._owners_stub({}))
            self.assertEqual(result["state"], "error")
            self.assertEqual(result["committed"], [])

    def test_pre_commit_hook_failure_is_reported_as_error_and_nothing_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            hooks_dir = clone_dir / ".git" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_path = hooks_dir / "pre-commit"
            hook_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook_path.chmod(0o755)
            (clone_dir / "new.md").write_text("new\n", encoding="utf-8")
            head_before = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()
            result = git_ops.commit(clone_dir, "add new.md", "wilco", self._owners_stub({}))
            self.assertEqual(result["state"], "error")
            self.assertEqual(result["committed"], [])
            head_after = _run(clone_dir, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(head_before, head_after)


class TestConflictDetectionExpanded(unittest.TestCase):
    """I1: conflict detection must catch every unmerged porcelain code
    (not just UU/AA/DD), including the delete/modify codes UD and DU."""

    def test_delete_modify_conflict_is_detected_and_incoming_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote_dir = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote_dir, "clone_b")

            # A modifies hello.md and pushes.
            (clone_a / "hello.md").write_text("hello modified by A\n", encoding="utf-8")
            _run(clone_a, "add", "hello.md")
            _run(clone_a, "commit", "-m", "A modifies hello")
            _run(clone_a, "push", "origin", "main")

            # B deletes hello.md locally without having pulled A's change.
            _run(clone_b, "rm", "hello.md")
            _run(clone_b, "commit", "-m", "B deletes hello, conflicting")

            status = git_ops.pull(clone_b, "test/node", "clone-b")

            self.assertEqual(status["state"], "conflict")
            self.assertIn("hello.md", status["conflicts"])
            incoming_path = clone_b / "hello.incoming.md"
            self.assertTrue(incoming_path.exists())
            self.assertIn("hello modified by A", incoming_path.read_text(encoding="utf-8"))
            # no unmerged git state left behind (merge was aborted)
            merge_head = clone_b / ".git" / "MERGE_HEAD"
            self.assertFalse(merge_head.exists())


class TestListChangedPaths(unittest.TestCase):
    """I2: list_changed_paths() must parse `git status --porcelain -z`
    correctly for rename records and non-ASCII filenames, neither of which
    survives naive line[3:] parsing of the default porcelain format."""

    def test_rename_is_reported_as_the_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            _run(clone_dir, "mv", "hello.md", "renamed.md")
            paths = git_ops.list_changed_paths(clone_dir)
            self.assertIn("renamed.md", paths)
            self.assertNotIn("hello.md", paths)
            self.assertNotIn("hello.md -> renamed.md", paths)

    def test_non_ascii_filename_is_reported_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            # NL diacritic (e-diaeresis, U+00EB): octal-escaped and quoted
            # by the default porcelain format unless core.quotePath=false.
            # Built with chr(0xEB) rather than a literal character in this
            # source file, so the single codepoint is exact and the test
            # is immune to any source-file encoding/normalization drift.
            name = "notiti" + chr(0xEB) + "n.md"
            (clone_dir / name).write_text("dutch estate note\n", encoding="utf-8")
            paths = git_ops.list_changed_paths(clone_dir)
            self.assertIn(name, paths)


class TestStatePathInWorktree(unittest.TestCase):
    """I8: _state_path() must resolve the real gitdir via `git rev-parse
    --absolute-git-dir` rather than assuming repo_dir / ".git" is a
    directory, since in a git worktree .git is a FILE (a pointer to the
    real gitdir elsewhere)."""

    def test_state_reads_and_writes_correctly_from_within_a_git_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            main_repo = tmp / "main_repo"
            main_repo.mkdir()
            _run(main_repo, "init", "-b", "main")
            _run(main_repo, "config", "user.email", "test@example.com")
            _run(main_repo, "config", "user.name", "Test")
            (main_repo / "hello.md").write_text("hello\n", encoding="utf-8")
            _run(main_repo, "add", "hello.md")
            _run(main_repo, "commit", "-m", "initial")

            worktree_dir = tmp / "wt"
            _run(main_repo, "worktree", "add", str(worktree_dir), "-b", "wt-branch")

            self.assertTrue((worktree_dir / ".git").is_file())

            git_ops._write_state(worktree_dir, lastPull="2026-01-01T00:00:00Z")
            last_pull, last_push = git_ops._read_state(worktree_dir)
            self.assertEqual(last_pull, "2026-01-01T00:00:00Z")
            self.assertIsNone(last_push)

            # the state file must live under the real gitdir (outside the
            # main repo's own .git, which is a separate physical location
            # for a worktree), not silently dropped or misplaced
            real_gitdir = pathlib.Path(
                _run(worktree_dir, "rev-parse", "--absolute-git-dir").stdout.strip()
            )
            self.assertTrue((real_gitdir / git_ops.STATE_FILENAME).exists())


class TestCommitReceiptAndSha(unittest.TestCase):
    def test_successful_commit_returns_sha_matching_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "new.md").write_text("x\n", encoding="utf-8")
            status = git_ops.commit(
                clone_dir, "add new.md", "clone-a", lambda repo, path: None,
                "test/node", "clone-a",
            )
            self.assertIn("sha", status)
            head = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(clone_dir),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(status["sha"], head)

    def test_successful_commit_writes_exactly_one_receipt_with_five_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "new2.md").write_text("x\n", encoding="utf-8")
            status = git_ops.commit(
                clone_dir, "add new2.md", "clone-a", lambda repo, path: None,
                "test/node", "clone-a",
            )
            log_dir = clone_dir / "_log"
            receipts = list(log_dir.glob("sync-receipt-*.json"))
            self.assertEqual(len(receipts), 1)
            data = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(
                set(data), {"sha", "message", "node", "clone", "committedAt"},
            )
            self.assertEqual(data["sha"], status["sha"])
            self.assertEqual(data["node"], "test/node")
            self.assertEqual(data["clone"], "clone-a")

    def test_refused_commit_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "blocked.md").write_text("x\n", encoding="utf-8")
            status = git_ops.commit(
                clone_dir, "add blocked.md", "clone-a",
                lambda repo, path: "someone-else",
                "test/node", "clone-a",
            )
            self.assertEqual(status["state"], "refused")
            log_dir = clone_dir / "_log"
            self.assertFalse(log_dir.exists() and list(log_dir.glob("sync-receipt-*.json")))
            self.assertNotIn("sha", status)

    def test_error_commit_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            status = git_ops.commit(
                clone_dir, "", "clone-a", lambda repo, path: None, "test/node", "clone-a",
            )
            self.assertEqual(status["state"], "error")
            log_dir = clone_dir / "_log"
            self.assertFalse(log_dir.exists() and list(log_dir.glob("sync-receipt-*.json")))
            self.assertNotIn("sha", status)


class TestLayoutShapeError(unittest.TestCase):
    """layout_shape_error() re-checks render_board.py's own settings.layout
    schema without importing board\\ (separate trust domains)."""

    def _valid_layout(self):
        return {"columns": 4, "panels": [{"id": "status", "col": 1, "row": 1, "w": 4, "h": 1}]}

    def test_valid_layout_has_no_error(self):
        self.assertIsNone(git_ops.layout_shape_error(self._valid_layout()))

    def test_non_dict_layout_is_an_error(self):
        self.assertIsNotNone(git_ops.layout_shape_error(["not", "a", "dict"]))

    def test_columns_out_of_range_is_an_error(self):
        layout = self._valid_layout()
        layout["columns"] = 5
        self.assertIsNotNone(git_ops.layout_shape_error(layout))

    def test_panels_not_a_list_is_an_error(self):
        layout = self._valid_layout()
        layout["panels"] = {"id": "status"}
        self.assertIsNotNone(git_ops.layout_shape_error(layout))

    def test_panel_entry_missing_a_field_is_an_error(self):
        layout = {"columns": 4, "panels": [{"id": "status", "col": 1, "row": 1, "w": 4}]}
        self.assertIsNotNone(git_ops.layout_shape_error(layout))

    def test_panel_col_out_of_range_is_an_error(self):
        layout = {"columns": 4, "panels": [{"id": "status", "col": 5, "row": 1, "w": 4, "h": 1}]}
        self.assertIsNotNone(git_ops.layout_shape_error(layout))

    def test_unknown_panel_id_is_not_an_error(self):
        # Deliberately looser than render_board.py's own PANEL_KEYS check
        # (see the docstring): an id it doesn't recognise still round-trips
        # safely on the read side.
        layout = {"columns": 4, "panels": [{"id": "not_a_real_panel", "col": 1, "row": 1, "w": 4, "h": 1}]}
        self.assertIsNone(git_ops.layout_shape_error(layout))


class TestWriteLayout(unittest.TestCase):
    def test_writes_layout_into_settings_preserving_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "siteoffice.json").write_text(json.dumps({
                "schemaVersion": 2, "type": "project", "name": "test-node",
                "settings": {"languages": ["en"]},
            }), encoding="utf-8")
            new_layout = {"columns": 4, "panels": [{"id": "status", "col": 1, "row": 2, "w": 4, "h": 1}]}
            result = git_ops.write_layout(tmp, new_layout)
            self.assertTrue(result["ok"])
            data = json.loads((tmp / "siteoffice.json").read_text(encoding="utf-8"))
            self.assertEqual(data["settings"]["layout"], new_layout)
            self.assertEqual(data["settings"]["languages"], ["en"])
            self.assertEqual(data["name"], "test-node")

    def test_writes_layout_when_settings_block_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "siteoffice.json").write_text(json.dumps({
                "schemaVersion": 2, "type": "project", "name": "test-node",
            }), encoding="utf-8")
            new_layout = {"columns": 4, "panels": []}
            result = git_ops.write_layout(tmp, new_layout)
            self.assertTrue(result["ok"])
            data = json.loads((tmp / "siteoffice.json").read_text(encoding="utf-8"))
            self.assertEqual(data["settings"]["layout"], new_layout)

    def test_missing_manifest_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            result = git_ops.write_layout(tmp, {"columns": 4, "panels": []})
            self.assertFalse(result["ok"])
            self.assertIn("message", result)

    def test_no_leftover_tmp_file_after_a_successful_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "siteoffice.json").write_text(json.dumps({
                "schemaVersion": 2, "type": "project", "name": "test-node",
            }), encoding="utf-8")
            result = git_ops.write_layout(tmp, {"columns": 4, "panels": []})
            self.assertTrue(result["ok"])
            # T10 (R11): the write is atomic (temp file + os.replace), so a
            # restart between the two steps can never observe a stray
            # .tmp file next to a half-written manifest.
            self.assertFalse((tmp / "siteoffice.json.tmp").exists())


class TestRemoteFailures(unittest.TestCase):
    """T10 (R02, N05): expired access, absent remotes, rejected writes and
    timeouts must never be reported as a successful push/pull."""

    def test_has_remote_false_when_none_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            _run(tmp, "init", "-b", "main")
            self.assertFalse(git_ops.has_remote(tmp))

    def test_has_remote_true_when_origin_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            self.assertTrue(git_ops.has_remote(clone_dir))

    def test_pull_with_no_remote_is_reported_as_a_failure_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            _run(tmp, "init", "-b", "main")
            _run(tmp, "config", "user.email", "test@example.com")
            _run(tmp, "config", "user.name", "Test")
            (tmp / "hello.md").write_text("hello\n", encoding="utf-8")
            _run(tmp, "add", "hello.md")
            _run(tmp, "commit", "-m", "initial")
            status = git_ops.pull(tmp, "test/node", "clone-a")
            self.assertFalse(status["opOk"])
            self.assertEqual(status["state"], "error")
            self.assertIn("no remote", status["message"])

    def test_push_with_no_remote_is_reported_as_a_failure_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            _run(tmp, "init", "-b", "main")
            _run(tmp, "config", "user.email", "test@example.com")
            _run(tmp, "config", "user.name", "Test")
            (tmp / "hello.md").write_text("hello\n", encoding="utf-8")
            _run(tmp, "add", "hello.md")
            _run(tmp, "commit", "-m", "initial")
            status = git_ops.push(tmp, "test/node", "clone-a")
            self.assertFalse(status["opOk"])
            self.assertEqual(status["state"], "error")
            self.assertIn("no remote", status["message"])

    def test_rejected_push_is_reported_as_a_failure_with_the_real_reason(self):
        """A non-fast-forward push (someone else already pushed): the
        classic "rejected write" case (R02), must never come back as ok."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_a, remote = _init_repo_pair(tmp)
            clone_b = _second_clone(tmp, remote, "clone-b")
            (clone_b / "hello.md").write_text("changed by b\n", encoding="utf-8")
            _run(clone_b, "commit", "-am", "b's change")
            _run(clone_b, "push", "origin", "main")

            (clone_a / "second.md").write_text("a's own new file\n", encoding="utf-8")
            _run(clone_a, "add", "second.md")
            _run(clone_a, "commit", "-m", "a's change, now behind origin")

            status = git_ops.push(clone_a, "test/node", "clone-a")
            self.assertFalse(status["opOk"])
            self.assertEqual(status["state"], "error")
            self.assertIn("push rejected", status["message"])

    def test_successful_push_reports_opok_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            (clone_dir / "second.md").write_text("new\n", encoding="utf-8")
            _run(clone_dir, "add", "second.md")
            _run(clone_dir, "commit", "-m", "second commit")
            status = git_ops.push(clone_dir, "test/node", "clone-a")
            self.assertTrue(status["opOk"])
            self.assertEqual(status["state"], "ok")

    def test_run_git_timeout_is_reported_not_raised(self):
        """A hung git command (an expired credential prompt, a dead
        network) must come back as a distinct, named failure, never hang
        the caller or crash it (R02: "A hanging command can block the
        single HTTP server")."""
        import subprocess as _subprocess
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            with mock.patch.object(
                _subprocess, "run",
                side_effect=_subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=1),
            ):
                result = git_ops.run_git(tmp, ["fetch", "origin"], timeout=1)
        self.assertEqual(result.returncode, git_ops.TIMEOUT_RETURNCODE)
        self.assertIn("timed out", result.stderr)

    def test_pull_reports_timeout_state_distinctly(self):
        import subprocess as _subprocess
        import unittest.mock as mock

        real_run = _subprocess.run

        def _fake_run(cmd, *args, **kwargs):
            if "fetch" in cmd:
                raise _subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))
            return real_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            clone_dir, _remote = _init_repo_pair(tmp)
            with mock.patch.object(_subprocess, "run", side_effect=_fake_run):
                status = git_ops.pull(clone_dir, "test/node", "clone-a")
        self.assertFalse(status["opOk"])
        self.assertEqual(status["state"], "timeout")


class TestCommitLegacyNonePath(unittest.TestCase):
    """commit(only_paths=None) is the sync helper's path and behaves exactly as it
    did before git_flow existed (fe18be7). These tests pin that: no split renames,
    no literal pathspecs, `git add` on every allowed path."""

    @staticmethod
    def _no_owner(repo_dir, path):
        return None

    def test_a_git_mv_commits_the_new_path_only_and_leaves_the_deletion_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone_dir, _remote = _init_repo_pair(pathlib.Path(tmp))
            _run(clone_dir, "mv", "hello.md", "renamed.md")
            result = git_ops.commit(clone_dir, "msg", "me", self._no_owner, "n", "c")
            self.assertEqual(result["committed"], ["renamed.md"])
            in_head = _run(clone_dir, "show", "--name-only", "--no-renames", "--pretty=format:", "HEAD")
            self.assertEqual(in_head.stdout.split(), ["renamed.md"])
            staged = _run(clone_dir, "diff", "--cached", "--name-status", "--no-renames").stdout.split()
            self.assertEqual(staged, ["D", "hello.md"])

    def test_the_default_still_uses_glob_pathspecs_as_before(self):
        # Legacy quirk, kept on purpose for the None path: the allowed glob-named
        # path `[ab].txt` is passed to git as a pathspec, so it also picks up the
        # owner-refused a.txt that it matches. (only_paths mode uses literal
        # pathspecs and does not; see test_git_flow.TestCommitOnlyPaths.)
        with tempfile.TemporaryDirectory() as tmp:
            clone_dir, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone_dir / "a.txt").write_text("a\n", encoding="utf-8")
            _run(clone_dir, "add", "--", "a.txt")
            _run(clone_dir, "commit", "-m", "seed a")
            (clone_dir / "a.txt").write_text("changed\n", encoding="utf-8")
            (clone_dir / "[ab].txt").write_text("glob\n", encoding="utf-8")
            resolve = lambda repo, path: "someone-else" if path == "a.txt" else None  # noqa: E731
            result = git_ops.commit(clone_dir, "msg", "me", resolve, "n", "c")
            self.assertEqual(result["committed"], ["[ab].txt"])
            self.assertEqual([item["path"] for item in result["refused"]], ["a.txt"])
            in_head = _run(clone_dir, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(sorted(in_head), ["[ab].txt", "a.txt"])
            self.assertEqual(_run(clone_dir, "status", "--porcelain", "-uno").stdout, "")

    def test_git_add_runs_for_every_allowed_path_and_no_command_is_literal(self):
        import unittest.mock as mock
        seen = []
        real = git_ops.run_git

        def spy(repo_dir, args, *rest, **kwargs):
            seen.append(list(args))
            return real(repo_dir, args, *rest, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            clone_dir, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone_dir / "one.md").write_text("1\n", encoding="utf-8")
            with mock.patch.object(git_ops, "run_git", side_effect=spy):
                result = git_ops.commit(clone_dir, "msg", "me", self._no_owner, "n", "c")
        self.assertEqual(result["committed"], ["one.md"])
        self.assertIn(["add", "--", "one.md"], seen)
        self.assertIn(["commit", "--only", "-m", "msg", "--", "one.md"], seen)
        self.assertFalse([args for args in seen if "--literal-pathspecs" in args])
        status = [args for args in seen if args and args[0] == "status"]
        self.assertTrue(status)
        self.assertFalse([args for args in status if "--no-renames" in args])


if __name__ == "__main__":
    unittest.main()
