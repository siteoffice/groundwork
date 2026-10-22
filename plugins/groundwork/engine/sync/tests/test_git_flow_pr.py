"""Tests for the propose, land and abort steps of sync/git_flow.py and for its
gh wrapper. Stdlib unittest, run through pytest. Every test builds a throwaway
clone plus a bare remote in a TemporaryDirectory and reaches GitHub only through
sync/tests/gh_stub.py, a stateful fake named in git_flow.GH_COMMAND. No network,
no real gh. Nothing here patches os.name.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

HERE = pathlib.Path(__file__).resolve().parent
SYNC_DIR = HERE.parent
for _path in (str(HERE), str(SYNC_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import git_flow  # noqa: E402
import git_ops  # noqa: E402
from test_git_flow import (  # noqa: E402
    AWS_KEY, RECEIPT_FIELDS, _branches, _cli, _head, _make_flow_repo, _receipt_lines, _script, _write,
)
from test_git_ops import _init_repo_pair, _run, _second_clone  # noqa: E402

GH_STUB = HERE / "gh_stub.py"


class StubGh:
    """The fake gh: its state file, its call log and the GH_COMMAND value."""

    def __init__(self, tmp, remote):
        self.state_path = pathlib.Path(tmp) / "gh_state.json"
        self.log_path = pathlib.Path(tmp) / "gh_calls.ndjson"
        self.log_path.write_text("", encoding="utf-8")
        self.write({"auth": True, "remote": str(remote), "prs": [], "fail": {}})

    def write(self, state):
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def update(self, **fields):
        state = self.state()
        state.update(fields)
        self.write(state)

    def update_pr(self, index=0, **fields):
        state = self.state()
        state["prs"][index].update(fields)
        self.write(state)

    def calls(self, *prefix):
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        calls = [json.loads(line) for line in lines if line]
        return [call for call in calls if call[:len(prefix)] == list(prefix)]

    def command(self):
        return [sys.executable, str(GH_STUB), str(self.state_path), str(self.log_path)]


def flag_value(call, name):
    """The argument that follows name in one recorded gh call."""
    return call[call.index(name) + 1]


def completed(returncode, stdout="", stderr=""):
    """A finished process with the given result, for patching a git or gh call."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class PrCase(unittest.TestCase):
    """A temp folder per test and a repo helper that points GH_COMMAND at the stub."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = pathlib.Path(holder.name)

    def repo(self, git=None):
        """(clone, remote, stub): a clean clone on main whose gh is the stub."""
        clone, remote = _make_flow_repo(self.tmp, git=git)
        stub = StubGh(self.tmp, remote)
        patcher = mock.patch.object(git_flow, "GH_COMMAND", stub.command())
        patcher.start()
        self.addCleanup(patcher.stop)
        return clone, remote, stub


class TestGhWrapper(PrCase):
    def test_a_missing_gh_raises_the_install_sentence(self):
        clone, _remote, _stub = self.repo()
        with mock.patch.object(git_flow, "GH_COMMAND", None), \
                mock.patch.object(git_flow.shutil, "which", return_value=None):
            with self.assertRaises(git_flow.GhUnavailable) as caught:
                git_flow.ensure_gh(clone)
        self.assertEqual(
            caught.exception.sentence,
            "Install GitHub CLI: winget install --id GitHub.cli, then run gh auth login.")

    def test_gh_is_found_with_shutil_which_when_no_command_is_set(self):
        with mock.patch.object(git_flow, "GH_COMMAND", None), \
                mock.patch.object(git_flow.shutil, "which", return_value="/opt/tools/gh") as which:
            self.assertEqual(git_flow._gh_argv(), ["/opt/tools/gh"])
        which.assert_called_once_with("gh")

    def test_a_failing_auth_status_raises_the_login_sentence(self):
        clone, _remote, stub = self.repo()
        stub.update(auth=False)
        with self.assertRaises(git_flow.GhUnavailable) as caught:
            git_flow.ensure_gh(clone)
        self.assertEqual(caught.exception.sentence, "Run gh auth login.")
        self.assertEqual(stub.calls("auth", "status"), [["auth", "status"]])

    def test_gh_blocked_is_none_when_logged_in_and_a_blocked_result_when_not(self):
        clone, _remote, stub = self.repo()
        self.assertIsNone(git_flow._gh_blocked("propose", clone))
        stub.update(auth=False)
        result = git_flow._gh_blocked("propose", clone, branch="work/x/y")
        self.assertEqual((result["step"], result["outcome"]), ("propose", "blocked"))
        self.assertEqual(result["refusal"], "Run gh auth login.")
        self.assertEqual(result["branch"], "work/x/y")

    def test_run_gh_records_its_arguments_and_runs_in_the_repo(self):
        clone, _remote, stub = self.repo()
        done = git_flow._run_gh(["pr", "list", "--head", "work/a/b"], clone)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), [])
        self.assertEqual(stub.calls("pr", "list"), [["pr", "list", "--head", "work/a/b"]])

    def test_stderr_is_redacted_but_stdout_is_returned_as_printed(self):
        clone, _remote, stub = self.repo()
        stub.update(fail={"pr create": "boom " + AWS_KEY}, raw_stdout="plain " + AWS_KEY)
        failed = git_flow._run_gh(["pr", "create"], clone)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn(AWS_KEY, failed.stderr)
        self.assertIn("[redacted]", failed.stderr)
        listed = git_flow._run_gh(["pr", "list"], clone)
        self.assertIn(AWS_KEY, listed.stdout)  # parsed, never displayed

    def test_a_timeout_is_returncode_124_with_a_sentence(self):
        clone, _remote, stub = self.repo()
        stub.update(sleep=5)
        done = git_flow._run_gh(["pr", "list"], clone, timeout=1)
        self.assertEqual(done.returncode, git_ops.TIMEOUT_RETURNCODE)
        self.assertIn("timed out after 1s", done.stderr)

    def test_gh_json_parses_success_and_returns_none_for_anything_else(self):
        ok = mock.Mock(returncode=0, stdout='[{"number": 3}]')
        self.assertEqual(git_flow._gh_json(ok), [{"number": 3}])
        for bad in (mock.Mock(returncode=1, stdout="[]"), mock.Mock(returncode=0, stdout="not json"),
                    mock.Mock(returncode=0, stdout="[" * 100000)):
            self.assertIsNone(git_flow._gh_json(bad))


def _probe_with_pr(pr):
    def inner(settings):
        return git_flow.make_result("probe", "pass", "would probe", pr=pr)
    return inner


class TestReceiptPrKey(PrCase):
    def test_a_result_with_pr_writes_the_optional_pr_key(self):
        clone, _remote, _stub = self.repo()
        pr = {"number": 7, "url": "https://github.example/test/repo/pull/7", "merge_commit": ""}
        result = git_flow._run_step("probe", clone, True, None, _probe_with_pr(pr))
        receipt = _receipt_lines(clone)[0]
        self.assertEqual(set(receipt), RECEIPT_FIELDS | {"pr"})
        self.assertEqual(receipt["pr"], pr)
        self.assertEqual(result["pr"], pr)

    def test_a_result_without_pr_keeps_exactly_the_sixteen_keys(self):
        clone, _remote, _stub = self.repo()
        git_flow._run_step("probe", clone, True, None, _probe_with_pr(None))
        self.assertEqual(set(_receipt_lines(clone)[0]), RECEIPT_FIELDS)

    def test_a_refused_step_writes_no_pr_key(self):
        clone, _remote, _stub = self.repo()
        git_flow._run_step(
            "probe", clone, True, None, lambda settings: git_flow.make_result("probe", "refused", "", "no"))
        self.assertEqual(set(_receipt_lines(clone)[0]), RECEIPT_FIELDS)

    def test_the_pr_fields_are_normalised_and_redacted(self):
        clone, _remote, _stub = self.repo()
        pr = {"number": "7", "url": "https://h.example/" + AWS_KEY, "merge_commit": None}
        result = git_flow._run_step("probe", clone, True, None, _probe_with_pr(pr))
        receipt = _receipt_lines(clone)[0]
        self.assertEqual(receipt["pr"], {"number": None, "url": "https://h.example/[redacted]", "merge_commit": ""})
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn(AWS_KEY, raw)
        self.assertNotIn(AWS_KEY, json.dumps(result))

    def test_a_preview_carries_the_normalised_pr_and_writes_no_receipt(self):
        clone, _remote, _stub = self.repo()
        pr = {"number": 3, "url": "https://h.example/pull/3"}
        result = git_flow._run_step("probe", clone, False, None, _probe_with_pr(pr))
        self.assertEqual(result["pr"], {"number": 3, "url": "https://h.example/pull/3", "merge_commit": ""})
        self.assertEqual(_receipt_lines(clone), [])


def branch_with_commit(clone, slug="feat", name="notes/a.md", text="a\n", subject="docs(notes): add a"):
    """Raw git, no receipts: work/test-clone/<slug> with one commit on top of main."""
    branch = f"work/test-clone/{slug}"
    _run(clone, "checkout", "-b", branch)
    _write(clone, name, text)
    _run(clone, "add", "--", name)
    _run(clone, "commit", "-m", subject)
    return branch


def _remote_heads(clone):
    return _run(clone, "ls-remote", "--heads", "origin").stdout


class TestAbort(PrCase):
    def test_a_preview_changes_nothing_and_names_the_parked_branch(self):
        clone, _remote, _stub = self.repo()
        branch = branch_with_commit(clone)
        before = (_head(clone), _branches(clone))
        result = git_flow.abort(clone)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("parked/test-clone/feat", result["preview"])
        self.assertEqual((_head(clone), _branches(clone)), before)
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), branch)
        self.assertEqual(_receipt_lines(clone), [])

    def test_a_clean_branch_is_renamed_and_main_is_checked_out(self):
        clone, _remote, _stub = self.repo()
        branch = branch_with_commit(clone)
        commit = _head(clone)
        result = git_flow.abort(clone, apply=True)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(sorted(_branches(clone)), ["main", "parked/test-clone/feat"])
        self.assertNotIn(branch, _branches(clone))
        self.assertEqual(_run(clone, "rev-parse", "parked/test-clone/feat").stdout.strip(), commit)
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        lines = _receipt_lines(clone)
        self.assertEqual([(l["step"], l["outcome"], l["exit_code"]) for l in lines], [("abort", "pass", 0)])
        self.assertEqual(set(lines[0]), RECEIPT_FIELDS)

    def test_a_dirty_branch_is_stashed_with_tracked_and_untracked_changes(self):
        clone, _remote, _stub = self.repo()
        branch_with_commit(clone)
        _write(clone, "notes/a.md", "a changed\n")
        _write(clone, "notes/new.md", "untracked\n")
        preview = git_flow.abort(clone)
        self.assertIn("Would stash 2 uncommitted change(s)", preview["preview"])
        self.assertIn("`parked/test-clone/feat`", preview["preview"])
        result = git_flow.abort(clone, apply=True)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("stash", result["preview"])
        self.assertEqual(_run(clone, "status", "--porcelain").stdout.strip(), "")
        listing = _run(clone, "stash", "list").stdout
        self.assertIn("parked/test-clone/feat", listing)
        held = _run(clone, "stash", "show", "--include-untracked", "--name-only", "stash@{0}").stdout.split()
        self.assertEqual(sorted(held), ["notes/a.md", "notes/new.md"])
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")

    def test_it_refuses_on_main_on_a_parked_branch_and_when_detached(self):
        clone, _remote, _stub = self.repo()
        self.assertEqual(git_flow.abort(clone, apply=True)["outcome"], "refused")
        _run(clone, "checkout", "-b", "parked/test-clone/old")
        result = git_flow.abort(clone, apply=True)
        self.assertEqual(result["outcome"], "refused")
        self.assertIn("parked/test-clone/old", result["refusal"])
        self.assertIn("not a work branch", result["refusal"])
        _run(clone, "checkout", "--detach")
        self.assertIn("detached", git_flow.abort(clone, apply=True)["refusal"])
        self.assertEqual([l["exit_code"] for l in _receipt_lines(clone)], [2, 2, 2])
        _run(clone, "checkout", "main")
        self.assertEqual(sorted(_branches(clone)), ["main", "parked/test-clone/old"])

    def test_it_refuses_when_the_parked_name_is_taken(self):
        clone, _remote, _stub = self.repo()
        branch = branch_with_commit(clone)
        _run(clone, "branch", "parked/test-clone/feat")
        result = git_flow.abort(clone, apply=True)
        self.assertEqual(result["outcome"], "refused")
        self.assertIn("already exists", result["refusal"])
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), branch)

    def test_the_remote_branch_is_never_touched(self):
        clone, _remote, _stub = self.repo()
        branch = branch_with_commit(clone)
        _run(clone, "push", "-u", "origin", branch)
        before = _remote_heads(clone)
        git_flow.abort(clone, apply=True)
        self.assertEqual(_remote_heads(clone), before)
        self.assertIn(branch, before)

    def test_a_main_git_cannot_check_out_is_blocked_after_the_rename_and_says_so(self):
        clone, _remote, _stub = self.repo(git={"main": "no-such-main"})
        branch_with_commit(clone)
        result = git_flow.abort(clone, apply=True)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("Renamed `work/test-clone/feat` to `parked/test-clone/feat`", result["refusal"])
        self.assertIn("still on `parked/test-clone/feat`", result["refusal"])
        self.assertEqual(_receipt_lines(clone)[0]["exit_code"], 3)


def commit_file(clone, name, text, subject):
    """Raw git, no receipts: one more commit on the current branch."""
    _write(clone, name, text)
    _run(clone, "add", "--", name)
    _run(clone, "commit", "-m", subject)


def open_pr(number=1, head="work/test-clone/feat", **fields):
    """A pull request dict in the shape the stub keeps and gh prints."""
    pr = {
        "number": number, "url": f"https://github.example/test/repo/pull/{number}", "state": "OPEN",
        "headRefName": head, "headRefOid": "0" * 40, "baseRefName": "main", "reviewDecision": "",
        "latestReviews": [], "mergeCommit": None,
    }
    pr.update(fields)
    return pr


class ProposedCase(PrCase):
    """Builds a work branch through the real start, save and check steps."""

    def ok(self, result):
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        return result

    def assert_refused(self, result, *words):
        self.assertEqual(result["outcome"], "refused", result["preview"])
        for word in words:
            self.assertIn(word, result["refusal"])

    def ready(self, checks=None, expect_check="pass", git_extra=None):
        """(clone, remote, stub) on work/test-clone/feat: started, saved and checked.
        git_extra adds keys to settings.git, for example {"pr": {"approvals": 1}}."""
        ok = _script(self.tmp, "ok.py", "pass\n")
        git = {"checks": [ok] if checks is None else checks, **(git_extra or {})}
        clone, remote, stub = self.repo(git=git)
        self.ctx = git_flow.RunContext(harness="test", model="test")
        self.ok(git_flow.start(clone, "feat", apply=True, ctx=self.ctx))
        _write(clone, "notes/a.md", "a\n")
        self.ok(git_flow.save(clone, apply=True, ctx=self.ctx))
        checked = git_flow.check(clone, apply=True, ctx=self.ctx)
        self.assertEqual(checked["outcome"], expect_check)
        return clone, remote, stub

    def proposed(self, checks=None, git_extra=None):
        """Like ready, then propose applied: the stub now holds pull request 1."""
        clone, remote, stub = self.ready(checks=checks, git_extra=git_extra)
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx))
        return clone, remote, stub


class TestProposeRefusals(ProposedCase):
    def test_it_refuses_on_main_off_a_work_branch_and_when_detached(self):
        clone, _remote, stub = self.ready()
        _run(clone, "checkout", "main")
        self.assert_refused(git_flow.propose(clone), "`main`", "protected")
        _run(clone, "checkout", "-b", "scratch")
        self.assert_refused(git_flow.propose(clone), "`scratch`", "not a work branch")
        _run(clone, "checkout", "--detach")
        self.assert_refused(git_flow.propose(clone), "detached")
        self.assertEqual(stub.calls("pr"), [])

    def test_it_refuses_a_dirty_tree_but_ignores_changes_under_log(self):
        clone, _remote, _stub = self.ready()
        _write(clone, "_log/noise.json", "{}\n")
        self.assertEqual(git_flow.propose(clone)["outcome"], "pass")
        _write(clone, "notes/b.md", "b\n")
        self.assert_refused(git_flow.propose(clone), "uncommitted", "notes/b.md", "save")

    def test_it_refuses_a_branch_with_no_commit_on_top_of_origin_main(self):
        clone, _remote, _stub = self.repo()
        self.ok(git_flow.start(clone, "feat", apply=True))
        self.assert_refused(git_flow.propose(clone), "Nothing to propose", "origin/main")

    def test_it_refuses_with_no_check_receipt_for_head(self):
        clone, _remote, _stub = self.repo()
        self.ok(git_flow.start(clone, "feat", apply=True))
        _write(clone, "notes/a.md", "a\n")
        self.ok(git_flow.save(clone, apply=True))
        self.assert_refused(git_flow.propose(clone), "Run check on this commit first.")

    def test_it_refuses_a_red_check_receipt(self):
        bad = _script(self.tmp, "bad.py", "import sys\nsys.exit(1)\n")
        clone, _remote, _stub = self.ready(checks=[bad], expect_check="blocked")
        self.assert_refused(git_flow.propose(clone), "Run check on this commit first.")

    def test_it_refuses_a_stale_receipt_after_head_moved(self):
        clone, _remote, _stub = self.ready()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.assert_refused(git_flow.propose(clone), "Run check on this commit first.")

    def test_the_newest_check_receipt_for_head_decides(self):
        clone, _remote, _stub = self.ready()
        git_flow.write_receipt(clone, {
            "step": "check", "outcome": "blocked", "exit_code": 3, "sha_after": _head(clone),
            "branch": "work/test-clone/feat", "checks": []})
        self.assert_refused(git_flow.propose(clone), "Run check on this commit first.")
        self.ok(git_flow.check(clone, apply=True))
        self.assertEqual(git_flow.propose(clone)["outcome"], "pass")

    def test_a_garbage_line_in_the_receipt_log_is_skipped(self):
        clone, _remote, _stub = self.ready()
        log = clone / "_log" / "runs" / "git-flow.ndjson"
        log.write_text("{broken\n[1, 2]\n" + log.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(git_flow.propose(clone)["outcome"], "pass")

    def secret_subject_refuses(self, *subjects, field=None):
        """Commit each subject (oldest first), then propose. field is "title" or
        "body" when the refusal must name only that part of the pull request."""
        clone, remote, stub = self.ready()
        for subject in subjects:
            _run(clone, "commit", "--allow-empty", "-m", subject)
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assert_refused(result, "aws-access-key", "pull request")
        if field:
            other = "body" if field == "title" else "title"
            self.assertIn(f"pull request {field}", result["refusal"])
            self.assertNotIn(f"pull request {other}", result["refusal"])
        self.assertNotIn(AWS_KEY, json.dumps(result))
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn(AWS_KEY, raw)
        self.assertEqual(stub.calls("pr", "create"), [])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))

    def test_a_key_in_a_conventional_commit_subject_refuses_without_the_text(self):
        self.secret_subject_refuses("fix(notes): rotate " + AWS_KEY)

    def test_a_key_in_a_free_form_commit_subject_refuses_without_the_text(self):
        self.secret_subject_refuses("rotate " + AWS_KEY)

    def test_a_key_only_in_the_title_refuses(self):
        # An old commit with a long subject fills the 8000-character body limit, so the
        # newest subject (the title) is cut out of the body and only the title carries the key.
        filler = "filler " * 1200
        self.secret_subject_refuses(filler, "rotate " + AWS_KEY, field="title")

    def test_a_key_only_in_the_body_refuses(self):
        # The key is in an older commit subject; the title is the newest, clean subject.
        self.secret_subject_refuses("rotate " + AWS_KEY, "docs(notes): later work", field="body")


class TestProposePasses(ProposedCase):
    def test_a_preview_pushes_and_creates_nothing(self):
        clone, _remote, stub = self.ready()
        result = git_flow.propose(clone)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("Would push `work/test-clone/feat`", result["preview"])
        self.assertIn("Title: docs(notes): update 1 file(s)", result["preview"])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(stub.calls("pr", "create"), [])
        self.assertEqual([l["step"] for l in _receipt_lines(clone)], ["start", "save", "check"])

    def test_apply_pushes_the_branch_and_creates_the_pull_request(self):
        clone, _remote, stub = self.ready()
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(
            _run(clone, "rev-parse", "--abbrev-ref", "work/test-clone/feat@{upstream}").stdout.strip(),
            "origin/work/test-clone/feat")
        (call,) = stub.calls("pr", "create")
        self.assertEqual(flag_value(call, "--base"), "main")
        self.assertEqual(flag_value(call, "--head"), "work/test-clone/feat")
        self.assertEqual(flag_value(call, "--title"), "docs(notes): update 1 file(s)")
        body = flag_value(call, "--body")
        self.assertIn("## Commits", body)
        self.assertIn("- docs(notes): update 1 file(s) (", body)
        self.assertIn("## Receipts since start", body)
        for line in ("- start: pass", "- save: pass", "- check: pass"):
            self.assertIn(line, body)
        self.assertIn("exit 0", body)
        self.assertNotIn("No checks ran", body)
        self.assertEqual(result["pr"]["number"], 1)
        self.assertEqual(result["pr"]["url"], "https://github.example/test/repo/pull/1")

    def test_the_receipt_carries_the_pr_key_and_the_sixteen_others(self):
        clone, _remote, _stub = self.ready()
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx))
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual(set(receipt), RECEIPT_FIELDS | {"pr"})
        self.assertEqual(receipt["step"], "propose")
        self.assertEqual(receipt["pr"], {
            "number": 1, "url": "https://github.example/test/repo/pull/1", "merge_commit": ""})
        self.assertEqual(receipt["run_id"], self.ctx.run_id)

    def test_a_check_that_passed_with_zero_checks_counts_and_the_body_says_so(self):
        clone, _remote, stub = self.ready(checks=[])
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("No checks ran (0 checks configured).", result["preview"])
        (call,) = stub.calls("pr", "create")
        self.assertIn("No checks ran: 0 checks are configured for this repo.", flag_value(call, "--body"))

    def test_an_open_pull_request_is_updated_not_created_twice(self):
        clone, _remote, stub = self.proposed()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        preview = git_flow.propose(clone)
        self.assertIn("Pull request #1 is already open: https://github.example/test/repo/pull/1", preview["preview"])
        self.assertEqual(preview["pr"]["number"], 1)
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(len(stub.calls("pr", "create")), 1)
        remote_tip = _run(clone, "ls-remote", "origin", "refs/heads/work/test-clone/feat").stdout.split()[0]
        self.assertEqual(remote_tip, _head(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["pr"]["number"], 1)

    def test_a_subject_with_quotes_a_dash_and_shell_characters_reaches_gh_verbatim(self):
        clone, _remote, stub = self.ready()
        subject = 'fix: handle "quotes" and -x $HOME %PATH% `tick`'
        _run(clone, "commit", "--allow-empty", "-m", subject)
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx))
        (call,) = stub.calls("pr", "create")
        self.assertEqual(flag_value(call, "--title"), subject)
        self.assertIn(subject, flag_value(call, "--body"))


class TestProposeBlocked(ProposedCase):
    def test_a_missing_gh_is_blocked_with_the_install_sentence_and_nothing_is_pushed(self):
        clone, _remote, _stub = self.ready()
        with mock.patch.object(git_flow, "GH_COMMAND", None), \
                mock.patch.object(git_flow.shutil, "which", return_value=None):
            result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(
            result["refusal"],
            "Install GitHub CLI: winget install --id GitHub.cli, then run gh auth login.")
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 3)

    def test_a_failing_auth_status_is_blocked_with_the_login_sentence(self):
        clone, _remote, stub = self.ready()
        stub.update(auth=False)
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual((result["outcome"], result["refusal"]), ("blocked", "Run gh auth login."))
        self.assertEqual(stub.calls("pr"), [])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))

    def test_a_failed_push_is_blocked_and_creates_no_pull_request(self):
        clone, _remote, stub = self.ready()
        real = git_ops.run_git

        def failing_push(repo_dir, args, *more, **named):
            if args[:1] == ["push"]:
                return completed(1, "", "remote: denied")
            return real(repo_dir, args, *more, **named)

        with mock.patch.object(git_ops, "run_git", side_effect=failing_push):
            result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("git push of `work/test-clone/feat` failed: remote: denied", result["refusal"])
        self.assertEqual(stub.calls("pr", "create"), [])

    def test_a_failed_pr_create_is_blocked_and_says_the_branch_was_pushed(self):
        clone, _remote, stub = self.ready()
        stub.update(fail={"pr create": "GraphQL: rate limited " + AWS_KEY})
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("was pushed but `gh pr create` failed: GraphQL: rate limited", result["refusal"])
        self.assertNotIn(AWS_KEY, json.dumps(result))
        self.assertNotIn(AWS_KEY, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))
        self.assertIn("work/test-clone/feat", _remote_heads(clone))
        self.assertNotIn("pr", _receipt_lines(clone)[-1])

    def test_gh_output_that_is_not_json_is_blocked_not_a_crash(self):
        clone, _remote, stub = self.ready()
        stub.update(raw_stdout="<html>maintenance</html>")
        result = git_flow.propose(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("could not list the pull requests", result["refusal"])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))

    def test_two_open_pull_requests_for_the_branch_are_refused(self):
        clone, _remote, stub = self.ready()
        stub.update(prs=[open_pr(1), open_pr(2)])
        self.assert_refused(git_flow.propose(clone), "More than one open pull request", "#1, #2")


def push_to_main(tmp, remote, name, text, who="other"):
    """A second clone commits one file to main and pushes it: main moves on origin."""
    other = _second_clone(pathlib.Path(tmp), remote, who)
    commit_file(other, name, text, "docs(notes): other work")
    _run(other, "push", "origin", "main")
    return other


def worktree_count(clone):
    out = _run(clone, "worktree", "list", "--porcelain").stdout
    return sum(1 for line in out.splitlines() if line.startswith("worktree "))


def review(login, state):
    return {"author": {"login": login}, "state": state}


class TestLandRefusals(ProposedCase):
    def test_it_refuses_on_main_off_a_work_branch_and_with_a_dirty_tree(self):
        clone, _remote, _stub = self.proposed()
        _write(clone, "notes/b.md", "b\n")
        self.assert_refused(git_flow.land(clone), "uncommitted", "notes/b.md")
        (clone / "notes" / "b.md").unlink()
        _run(clone, "checkout", "main")
        self.assert_refused(git_flow.land(clone), "`main`", "protected")
        _run(clone, "checkout", "-b", "scratch")
        self.assert_refused(git_flow.land(clone), "not a work branch")

    def test_it_refuses_when_no_pull_request_is_open(self):
        clone, _remote, _stub = self.ready()
        self.assert_refused(git_flow.land(clone), "No open pull request", "propose")

    def test_it_refuses_when_the_pull_request_head_differs_from_head(self):
        clone, _remote, stub = self.proposed()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.assert_refused(git_flow.land(clone), "Push first: run propose.")
        self.assertEqual(stub.calls("pr", "merge"), [])

    def test_it_refuses_while_changes_are_requested(self):
        clone, _remote, stub = self.proposed()
        stub.update_pr(reviewDecision="CHANGES_REQUESTED")
        self.assert_refused(git_flow.land(clone), "requested changes", "#1")

    def test_it_refuses_with_too_few_approvals(self):
        clone, _remote, stub = self.proposed(git_extra={"pr": {"approvals": 1}})
        self.assert_refused(git_flow.land(clone), "0 approval(s)", "requires 1")
        stub.update_pr(latestReviews=[review("ann", "COMMENTED")])
        self.assert_refused(git_flow.land(clone), "0 approval(s)")
        stub.update_pr(latestReviews=[review("ann", "APPROVED"), review("ann", "COMMENTED")])
        self.assert_refused(git_flow.land(clone), "0 approval(s)")
        stub.update_pr(latestReviews=[review("ann", "APPROVED"), review("bob", "CHANGES_REQUESTED")])
        self.assertEqual(git_flow.land(clone)["outcome"], "pass")

    def test_the_default_needs_no_approval(self):
        clone, _remote, _stub = self.proposed()
        self.assertEqual(git_flow.land(clone)["outcome"], "pass")

    def test_a_conflict_with_main_is_refused_and_names_its_paths(self):
        clone, remote, stub = self.proposed()
        push_to_main(self.tmp, remote, "notes/a.md", "a from main\n")
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assert_refused(result, "conflicts in `notes/a.md`")
        self.assertEqual(stub.calls("pr", "merge"), [])
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 2)

    def test_a_pull_request_into_another_base_is_refused_in_preview_and_apply(self):
        clone, remote, stub = self.proposed()
        stub.update_pr(baseRefName="release")
        work = "work/test-clone/feat"

        def state():
            return (_head(clone), _run(clone, "rev-parse", "main").stdout.strip(),
                    _run(remote, "rev-parse", "refs/heads/main").stdout.strip(), sorted(_branches(clone)))

        before = state()
        self.assert_refused(git_flow.land(clone), "#1", "`release`", "`main`", "Nothing was changed.")
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assert_refused(result, "#1", "`release`", "`main`", "Nothing was changed.")
        self.assertEqual(stub.calls("pr", "merge"), [])
        self.assertEqual(state(), before)
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), work)
        self.assertIn(work, _remote_heads(clone))
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual((receipt["step"], receipt["outcome"], receipt["exit_code"]), ("land", "refused", 2))
        stub.update_pr(baseRefName="rel-" + AWS_KEY)
        keyed = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assert_refused(keyed, "#1")
        self.assertNotIn(AWS_KEY, json.dumps(keyed))
        self.assertNotIn(AWS_KEY, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))

    def test_a_failed_fetch_is_blocked(self):
        clone, _remote, _stub = self.proposed()
        real = git_ops.run_git

        def failing_fetch(repo_dir, args, *more, **named):
            if args[:1] == ["fetch"]:
                return completed(1, "", "fatal: unable to access remote")
            return real(repo_dir, args, *more, **named)

        with mock.patch.object(git_ops, "run_git", side_effect=failing_fetch):
            result = git_flow.land(clone)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("git fetch failed: fatal: unable to access remote", result["refusal"])

    def test_a_missing_gh_and_a_failing_login_are_blocked_with_their_sentences(self):
        clone, _remote, stub = self.proposed()
        with mock.patch.object(git_flow, "GH_COMMAND", None), \
                mock.patch.object(git_flow.shutil, "which", return_value=None):
            missing = git_flow.land(clone)
        self.assertEqual(missing["outcome"], "blocked")
        self.assertEqual(
            missing["refusal"],
            "Install GitHub CLI: winget install --id GitHub.cli, then run gh auth login.")
        stub.update(auth=False)
        login = git_flow.land(clone)
        self.assertEqual((login["outcome"], login["refusal"]), ("blocked", "Run gh auth login."))

    def test_gh_output_that_is_not_json_is_blocked_and_two_pull_requests_are_refused(self):
        clone, _remote, stub = self.proposed()
        stub.update(raw_stdout="oops")
        self.assertEqual(git_flow.land(clone)["outcome"], "blocked")
        stub.update(raw_stdout=None, prs=[open_pr(1), open_pr(2)])
        self.assert_refused(git_flow.land(clone), "More than one open pull request")

    def test_with_the_branch_up_to_date_a_missing_or_red_check_receipt_stops_it(self):
        clone, _remote, _stub = self.proposed()
        log = clone / "_log" / "runs" / "git-flow.ndjson"
        kept = log.read_text(encoding="utf-8")
        log.write_text("", encoding="utf-8")
        self.assert_refused(git_flow.land(clone), "Run check on this commit first.")
        git_flow.write_receipt(clone, {
            "step": "check", "outcome": "blocked", "exit_code": 3, "sha_after": _head(clone),
            "branch": "work/test-clone/feat", "checks": []})
        red = git_flow.land(clone)
        self.assertEqual(red["outcome"], "blocked")
        self.assertIn("last check on this commit failed", red["refusal"])
        log.write_text(kept, encoding="utf-8")
        self.assertEqual(git_flow.land(clone)["outcome"], "pass")


class TestLandPreview(ProposedCase):
    def test_an_up_to_date_branch_previews_the_receipt_reuse_and_changes_nothing(self):
        clone, _remote, stub = self.proposed()
        before = (_head(clone), sorted(_branches(clone)), worktree_count(clone), len(_receipt_lines(clone)))
        result = git_flow.land(clone)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("Pull request #1: https://github.example/test/repo/pull/1", result["preview"])
        self.assertIn("with a merge commit", result["preview"])
        self.assertIn("reuse the passing check receipt", result["preview"])
        self.assertEqual(result["pr"]["number"], 1)
        after = (_head(clone), sorted(_branches(clone)), worktree_count(clone), len(_receipt_lines(clone)))
        self.assertEqual(after, before)
        self.assertEqual(stub.calls("pr", "merge"), [])

    def test_a_branch_behind_main_previews_the_check_on_the_merge_result(self):
        clone, remote, _stub = self.proposed()
        push_to_main(self.tmp, remote, "notes/other.md", "other\n")
        result = git_flow.land(clone)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("run 1 check(s) on the merge result in a temporary worktree", result["preview"])
        self.assertEqual(worktree_count(clone), 1)

    def test_a_branch_behind_main_with_zero_checks_says_so_in_amber(self):
        clone, remote, _stub = self.proposed(checks=[])
        push_to_main(self.tmp, remote, "notes/other.md", "other\n")
        result = git_flow.land(clone)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("run no checks on the merge result", result["preview"])
        self.assertEqual(result["amber"], "0 checks configured")


def probe_check(tmp, name="probe.py", body="", log_name="probe.log"):
    """(command, log path): a check that appends 'cwd|other.md exists' to a log,
    then runs body (Python text) which may call sys.exit. It passes on a branch
    that lacks other.md, so the check before propose is green."""
    log = pathlib.Path(tmp) / log_name
    text = ("import os, sys, time\n"
            f"open({str(log)!r}, 'a').write(os.getcwd() + '|' + str(os.path.exists('other.md')) + chr(10))\n"
            + body)
    return _script(tmp, name, text), log


def probe_lines(log):
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


class TestLandApply(ProposedCase):
    def recorded_temp_dirs(self):
        """Patch tempfile.mkdtemp in git_flow so a test can see the worktree folder."""
        made = []
        real = tempfile.mkdtemp

        def recording(*args, **named):
            path = real(*args, **named)
            made.append(pathlib.Path(path))
            return path

        patcher = mock.patch.object(git_flow.tempfile, "mkdtemp", side_effect=recording)
        patcher.start()
        self.addCleanup(patcher.stop)
        return made

    def test_land_merges_with_a_merge_commit_and_cleans_up_everything(self):
        clone, remote, stub = self.proposed()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(result["amber"], "")
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        merge_commit = result["pr"]["merge_commit"]
        self.assertRegex(merge_commit, r"^[0-9a-f]{40}$")
        self.assertEqual(_head(clone), merge_commit)
        self.assertEqual(_run(remote, "rev-parse", "refs/heads/main").stdout.strip(), merge_commit)
        parents = _run(clone, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 3)  # the commit and its two parents: a real merge commit
        self.assertNotIn("work/test-clone/feat", _branches(clone))
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))
        self.assertTrue((clone / "notes" / "a.md").exists())
        (call,) = stub.calls("pr", "merge")
        self.assertEqual(call[:4], ["pr", "merge", "1", "--merge"])
        self.assertEqual(flag_value(call, "--match-head-commit"), parents[2])
        for banned in ("--squash", "--rebase", "--delete-branch", "--admin"):
            self.assertNotIn(banned, call)

    def test_the_land_receipt_has_sixteen_keys_the_pr_key_and_the_new_main_head(self):
        clone, _remote, _stub = self.proposed()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual(set(receipt), RECEIPT_FIELDS | {"pr"})
        self.assertEqual((receipt["step"], receipt["outcome"], receipt["exit_code"]), ("land", "pass", 0))
        self.assertEqual(receipt["sha_after"], result["pr"]["merge_commit"])
        self.assertNotEqual(receipt["sha_before"], receipt["sha_after"])
        self.assertEqual(receipt["branch"], "main")
        self.assertEqual(receipt["pr"]["number"], 1)
        self.assertEqual(receipt["pr"]["merge_commit"], receipt["sha_after"])

    def test_an_up_to_date_branch_reuses_the_receipt_and_makes_no_worktree(self):
        command, log = probe_check(self.tmp)
        clone, _remote, _stub = self.proposed(checks=[command])
        self.assertEqual(len(probe_lines(log)), 1)  # the check step before propose
        made = self.recorded_temp_dirs()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(len(probe_lines(log)), 1)  # not run again
        self.assertEqual(made, [])
        self.assertEqual(result["checks"], [])

    def test_main_moved_and_clean_runs_the_checks_on_the_merge_result_and_removes_the_worktree(self):
        command, log = probe_check(self.tmp)
        clone, remote, _stub = self.proposed(checks=[command])
        push_to_main(self.tmp, remote, "other.md", "other\n")
        made = self.recorded_temp_dirs()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        lines = probe_lines(log)
        self.assertEqual(len(lines), 2)
        cwd, other_exists = lines[1].rsplit("|", 1)
        self.assertEqual(other_exists, "True")  # it ran on the merge result
        self.assertEqual(len(made), 1)
        self.assertEqual(pathlib.Path(cwd).resolve(), made[0].resolve())
        self.assertFalse(made[0].exists())
        self.assertEqual(worktree_count(clone), 1)
        self.assertEqual([r["exit"] for r in result["checks"]], [0])
        self.assertEqual(_receipt_lines(clone)[-1]["checks"], result["checks"])
        self.assertTrue((clone / "other.md").exists())  # main now holds both changes
        self.assertTrue((clone / "notes" / "a.md").exists())

    def test_main_moved_with_a_failing_check_is_blocked_and_the_worktree_is_gone(self):
        command, _log = probe_check(self.tmp, body="sys.exit(1 if os.path.exists('other.md') else 0)\n")
        clone, remote, stub = self.proposed(checks=[command])
        push_to_main(self.tmp, remote, "other.md", "other\n")
        made = self.recorded_temp_dirs()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("failed with exit 1 on the merge result", result["refusal"])
        self.assertIn("Nothing was merged.", result["refusal"])
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0].exists())
        self.assertEqual(worktree_count(clone), 1)
        self.assertEqual(stub.calls("pr", "merge"), [])
        self.assertIn("work/test-clone/feat", _branches(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 3)
        self.assertEqual(_receipt_lines(clone)[-1]["checks"][0]["exit"], 1)

    def test_a_check_that_times_out_on_the_merge_result_still_removes_the_worktree(self):
        command, _log = probe_check(self.tmp, body="time.sleep(30 if os.path.exists('other.md') else 0)\n")
        clone, remote, stub = self.proposed(checks=[command], git_extra={"checkTimeoutSeconds": 1})
        push_to_main(self.tmp, remote, "other.md", "other\n")
        made = self.recorded_temp_dirs()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("timed out after 1s on the merge result", result["refusal"])
        self.assertFalse(made[0].exists())
        self.assertEqual(worktree_count(clone), 1)
        self.assertEqual(stub.calls("pr", "merge"), [])

    def test_a_check_that_cannot_start_on_the_merge_result_still_removes_the_worktree(self):
        command, _log = probe_check(self.tmp)
        clone, remote, stub = self.proposed(checks=[command])
        push_to_main(self.tmp, remote, "other.md", "other\n")
        made = self.recorded_temp_dirs()
        with mock.patch.object(git_flow, "run_check", side_effect=OSError("cannot start")):
            result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("could not be started on the merge result", result["refusal"])
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0].exists())
        self.assertEqual(worktree_count(clone), 1)
        self.assertEqual(stub.calls("pr", "merge"), [])

    def test_main_moved_with_zero_checks_merges_and_the_receipt_notes_it(self):
        clone, remote, _stub = self.proposed(checks=[])
        push_to_main(self.tmp, remote, "other.md", "other\n")
        made = self.recorded_temp_dirs()
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(made, [])
        self.assertEqual(result["amber"], "0 checks configured")

    def test_a_failed_gh_merge_is_blocked_and_leaves_everything_in_place(self):
        clone, _remote, stub = self.proposed()
        stub.update(fail={"pr merge": "GraphQL: Pull request is not mergeable"})
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("`gh pr merge` failed: GraphQL: Pull request is not mergeable", result["refusal"])
        self.assertIn("work/test-clone/feat", _branches(clone))
        self.assertIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "work/test-clone/feat")

    def test_a_failed_remote_delete_after_the_merge_is_a_pass_with_an_amber_note(self):
        clone, _remote, _stub = self.proposed()
        real = git_ops.run_git

        def failing_delete(repo_dir, args, *more, **named):
            if args[:3] == ["push", "origin", "--delete"]:
                return completed(1, "", "remote: permission denied")
            return real(repo_dir, args, *more, **named)

        with mock.patch.object(git_ops, "run_git", side_effect=failing_delete):
            result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("merged, but still to do", result["amber"])
        self.assertIn("could not delete `origin/work/test-clone/feat`", result["amber"])
        self.assertIn("git push origin --delete work/test-clone/feat", result["amber"])
        self.assertEqual(_head(clone), result["pr"]["merge_commit"])
        self.assertNotIn("work/test-clone/feat", _branches(clone))
        self.assertIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 0)

    def test_a_remote_branch_github_already_deleted_is_not_a_problem(self):
        clone, _remote, stub = self.proposed()
        stub.update(delete_head_on_merge=True)
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertEqual(result["amber"], "")
        self.assertNotIn("work/test-clone/feat", _branches(clone))

    def test_a_local_main_that_cannot_fast_forward_is_a_pass_with_an_amber_note(self):
        clone, _remote, _stub = self.proposed()
        _run(clone, "checkout", "main")
        commit_file(clone, "notes/local.md", "local\n", "docs(notes): local only")
        _run(clone, "checkout", "work/test-clone/feat")
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("could not fast-forward `main` to `origin/main`", result["amber"])
        self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        self.assertTrue(result["pr"]["merge_commit"])

    def test_a_local_branch_git_will_not_delete_is_a_pass_with_an_amber_note(self):
        clone, _remote, _stub = self.proposed()
        # Without an upstream, git branch -d asks whether main holds the branch. Local main
        # has a commit origin/main lacks, so it cannot fast-forward to the merge commit and
        # does not hold the branch tip either: -d refuses (-D would delete it).
        _run(clone, "branch", "--unset-upstream")
        _run(clone, "checkout", "main")
        commit_file(clone, "notes/local.md", "local\n", "docs(notes): local only")
        _run(clone, "checkout", "work/test-clone/feat")
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("could not delete the local branch `work/test-clone/feat`", result["amber"])
        self.assertIn("run `git branch -d work/test-clone/feat`", result["amber"])
        self.assertIn("work/test-clone/feat", _branches(clone))
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 0)

    def test_a_main_git_cannot_check_out_is_a_pass_with_an_amber_note(self):
        clone, _remote, _stub = self.proposed()
        real = git_ops.run_git

        def failing_checkout(repo_dir, args, *more, **named):
            if args[:1] == ["checkout"]:
                return completed(1, "", "error: your local changes would be overwritten")
            return real(repo_dir, args, *more, **named)

        with mock.patch.object(git_ops, "run_git", side_effect=failing_checkout):
            result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("could not check out `main`", result["amber"])
        self.assertIn("work/test-clone/feat", _branches(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 0)

    def test_no_gh_output_carrying_a_key_reaches_the_receipt(self):
        clone, _remote, stub = self.proposed()
        stub.update_pr(url="https://github.example/test/repo/pull/1?token=" + AWS_KEY)
        result = git_flow.land(clone, apply=True, ctx=self.ctx)
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn(AWS_KEY, raw)
        self.assertNotIn(AWS_KEY, json.dumps(result))
        self.assertIn("[redacted]", _receipt_lines(clone)[-1]["pr"]["url"])


class TestProposeTitle(ProposedCase):
    """propose --title: a custom pull request title, validated like the generated one."""

    def ready_with_commits(self):
        clone, _remote, stub = self.ready()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        return clone, stub

    def assert_title_refused(self, clone, stub, title, *words):
        result = git_flow.propose(clone, apply=True, ctx=self.ctx, title=title)
        self.assert_refused(result, "title", *words)
        self.assertEqual(stub.calls(), [])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))
        self.assertEqual(_receipt_lines(clone)[-1]["step"], "propose")
        return result

    def test_without_title_the_newest_commit_subject_is_still_the_title(self):
        clone, stub = self.ready_with_commits()
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx, title=None))
        (call,) = stub.calls("pr", "create")
        self.assertEqual(flag_value(call, "--title"), "docs(notes): add b")

    def test_a_title_replaces_the_commit_subject_and_the_body_still_lists_the_commits(self):
        clone, stub = self.ready_with_commits()
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx, title="  Add notes a and b  "))
        (call,) = stub.calls("pr", "create")
        self.assertEqual(flag_value(call, "--title"), "Add notes a and b")
        self.assertIn("- docs(notes): add b (", flag_value(call, "--body"))
        self.assertEqual(stub.state()["prs"][0]["title"], "Add notes a and b")

    def test_the_preview_shows_the_chosen_title_and_changes_nothing(self):
        clone, stub = self.ready_with_commits()
        result = git_flow.propose(clone, title="Add notes a and b")
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("Title: Add notes a and b", result["preview"])
        self.assertNotIn("docs(notes): add b\n", result["preview"])
        self.assertEqual(stub.calls("pr", "create"), [])
        self.assertNotIn("work/test-clone/feat", _remote_heads(clone))

    def test_an_empty_or_blank_title_is_refused_and_nothing_is_pushed(self):
        clone, stub = self.ready_with_commits()
        for title in ("", "   "):
            with self.subTest(title=title):
                self.assert_title_refused(clone, stub, title, "empty")

    def test_a_title_over_the_limit_is_refused_not_truncated(self):
        clone, stub = self.ready_with_commits()
        result = self.assert_title_refused(clone, stub, "x" * (git_flow.PR_TITLE_LIMIT + 1), "longer than")
        self.assertIn(str(git_flow.PR_TITLE_LIMIT), result["refusal"])
        exact = git_flow.propose(clone, title="x" * git_flow.PR_TITLE_LIMIT)
        self.assertEqual(exact["outcome"], "pass", exact["refusal"])

    def test_a_title_with_a_line_break_is_refused(self):
        clone, stub = self.ready_with_commits()
        self.assert_title_refused(clone, stub, "first\nsecond", "one line")

    def test_a_secret_shaped_title_is_refused_naming_the_field_and_rule_never_the_text(self):
        clone, stub = self.ready_with_commits()
        result = self.assert_title_refused(clone, stub, "rotate " + AWS_KEY, "aws-access-key")
        self.assertIn("pull request title", result["refusal"])
        self.assertNotIn(AWS_KEY, json.dumps(result))
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn(AWS_KEY, raw)
        self.assertEqual(git_flow.propose(clone, title="rotate " + AWS_KEY)["outcome"], "refused")

    def test_with_an_open_pull_request_nothing_is_created_and_the_old_title_stands(self):
        clone, _remote, stub = self.proposed()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        old_title = stub.state()["prs"][0]["title"]
        preview = git_flow.propose(clone, title="A new title")
        self.assertIn("Pull request #1 is already open", preview["preview"])
        self.assertIn("title was not changed", preview["preview"])
        result = git_flow.propose(clone, apply=True, ctx=self.ctx, title="A new title")
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("title was not changed", result["preview"])
        self.assertEqual(len(stub.calls("pr", "create")), 1)
        self.assertEqual(stub.state()["prs"][0]["title"], old_title)
        remote_tip = _run(clone, "ls-remote", "origin", "refs/heads/work/test-clone/feat").stdout.split()[0]
        self.assertEqual(remote_tip, _head(clone))

    def test_without_title_an_open_pull_request_says_nothing_about_a_title(self):
        clone, _remote, _stub = self.proposed()
        commit_file(clone, "notes/b.md", "b\n", "docs(notes): add b")
        self.ok(git_flow.check(clone, apply=True, ctx=self.ctx))
        self.assertNotIn("title was not changed", git_flow.propose(clone, apply=True, ctx=self.ctx)["preview"])

    def test_the_receipt_keeps_its_keys_and_never_holds_the_title(self):
        clone, _stub = self.ready_with_commits()
        self.ok(git_flow.propose(clone, apply=True, ctx=self.ctx, title="Add notes a and b"))
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual(set(receipt), RECEIPT_FIELDS | {"pr"})
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn("Add notes a and b", raw)

    def test_the_command_line_passes_title_through_and_refuses_an_empty_one(self):
        self.assertEqual(git_flow.build_parser().parse_args(["propose", "--title", "T"]).title, "T")
        self.assertIsNone(git_flow.build_parser().parse_args(["propose"]).title)
        clone, stub = self.ready_with_commits()
        code, out = _cli(["propose", "--title", "", "--repo", str(clone)])
        self.assertEqual(code, 2, out)
        self.assertIn("Refusal:", out)
        code, out = _cli(["propose", "--title", "Add notes a and b", "--apply", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        (call,) = stub.calls("pr", "create")
        self.assertEqual(flag_value(call, "--title"), "Add notes a and b")


class TestLandSubject(ProposedCase):
    """land --subject: a custom merge commit subject, validated like a title."""

    def land_argv_without_subject(self, clone):
        return ["pr", "merge", "1", "--merge", "--match-head-commit", _head(clone)]

    def test_without_subject_the_gh_argv_is_exactly_what_it_was(self):
        clone, _remote, stub = self.proposed()
        expected = self.land_argv_without_subject(clone)
        self.ok(git_flow.land(clone, apply=True, ctx=self.ctx, subject=None))
        self.assertEqual(stub.calls("pr", "merge"), [expected])

    def test_a_subject_reaches_gh_pr_merge_as_dash_dash_subject_and_names_the_merge_commit(self):
        clone, _remote, stub = self.proposed()
        expected = self.land_argv_without_subject(clone) + ["--subject", "Add notes a and b (#1)"]
        self.ok(git_flow.land(clone, apply=True, ctx=self.ctx, subject="  Add notes a and b (#1) "))
        self.assertEqual(stub.calls("pr", "merge"), [expected])
        self.assertEqual(_run(clone, "log", "-1", "--format=%s", "main").stdout.strip(), "Add notes a and b (#1)")

    def test_the_preview_shows_the_subject_and_merges_nothing(self):
        clone, remote, stub = self.proposed()
        result = git_flow.land(clone, subject="Add notes a and b (#1)")
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        self.assertIn("Merge commit subject: Add notes a and b (#1)", result["preview"])
        self.assertNotIn("Merge commit subject", git_flow.land(clone)["preview"])
        self.assertEqual(stub.calls("pr", "merge"), [])
        self.assertIn("work/test-clone/feat", _remote_heads(clone))

    def assert_subject_refused(self, clone, stub, subject, *words):
        for apply in (False, True):
            result = git_flow.land(clone, apply=apply, ctx=self.ctx, subject=subject)
            self.assert_refused(result, "merge commit subject", *words)
            self.assertEqual(stub.calls("pr", "merge"), [])
            self.assertIn("work/test-clone/feat", _remote_heads(clone))
        return result

    def test_an_empty_blank_over_long_or_multi_line_subject_is_refused(self):
        clone, _remote, stub = self.proposed()
        self.assert_subject_refused(clone, stub, "", "empty")
        self.assert_subject_refused(clone, stub, "   ", "empty")
        result = self.assert_subject_refused(
            clone, stub, "x" * (git_flow.MERGE_SUBJECT_LIMIT + 1), "longer than")
        self.assertIn(str(git_flow.MERGE_SUBJECT_LIMIT), result["refusal"])
        self.assert_subject_refused(clone, stub, "first\nsecond", "one line")
        exact = git_flow.land(clone, subject="x" * git_flow.MERGE_SUBJECT_LIMIT)
        self.assertEqual(exact["outcome"], "pass", exact["refusal"])

    def test_a_secret_shaped_subject_is_refused_naming_the_rule_never_the_text(self):
        clone, _remote, stub = self.proposed()
        result = self.assert_subject_refused(clone, stub, "rotate " + AWS_KEY, "aws-access-key")
        self.assertNotIn(AWS_KEY, json.dumps(result))
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn(AWS_KEY, raw)

    def test_the_receipt_keeps_its_keys_and_never_holds_the_subject(self):
        clone, _remote, _stub = self.proposed()
        self.ok(git_flow.land(clone, apply=True, ctx=self.ctx, subject="Add notes a and b (#1)"))
        self.assertEqual(set(_receipt_lines(clone)[-1]), RECEIPT_FIELDS | {"pr"})
        raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
        self.assertNotIn("Add notes a and b (#1)", raw)

    def test_the_command_line_passes_subject_through(self):
        self.assertEqual(git_flow.build_parser().parse_args(["land", "--subject", "S"]).subject, "S")
        self.assertIsNone(git_flow.build_parser().parse_args(["land"]).subject)
        clone, _remote, stub = self.proposed()
        code, out = _cli(["land", "--subject", "", "--repo", str(clone)])
        self.assertEqual(code, 2, out)
        code, out = _cli(["land", "--subject", "Add notes (#1)", "--apply", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        (call,) = stub.calls("pr", "merge")
        self.assertEqual(flag_value(call, "--subject"), "Add notes (#1)")


class TestCliSubcommands(ProposedCase):
    def test_the_parser_knows_the_three_new_steps_with_the_common_flags(self):
        parser = git_flow.build_parser()
        for step in ("propose", "land", "abort"):
            args = parser.parse_args([step, "--apply", "--repo", "x", "--harness", "h", "--model", "m"])
            self.assertEqual((args.step, args.apply, args.repo, args.harness, args.model),
                             (step, True, "x", "h", "m"))

    def test_a_bad_flag_on_a_new_step_is_a_usage_error_not_a_refusal(self):
        for argv in (["land", "--nope"], ["abort", "extra"], ["propose", "--type", "feat"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        git_flow.main(argv)
                self.assertEqual(caught.exception.code, 2)
                self.assertTrue(err.getvalue().startswith("usage error:"))

    def test_propose_land_and_abort_run_from_the_command_line_with_their_exit_codes(self):
        clone, _remote, _stub = self.ready()
        code, out = _cli(["propose", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("[propose] pass"))
        code, out = _cli(["propose", "--apply", "--repo", str(clone), "--harness", "cli", "--model", "m1"])
        self.assertEqual(code, 0, out)
        self.assertIn("pull request #1", out)
        self.assertRegex(out, r"receipt [0-9a-f]{32} written")
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual((receipt["step"], receipt["harness"], receipt["model"]), ("propose", "cli", "m1"))
        code, out = _cli(["land", "--apply", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        self.assertIn("Merged pull request #1", out)
        self.assertEqual(_receipt_lines(clone)[-1]["step"], "land")
        code, out = _cli(["abort", "--repo", str(clone)])
        self.assertEqual(code, 2, out)  # back on main: nothing to park
        self.assertIn("Refusal:", out)

    def test_abort_previews_and_applies_from_the_command_line(self):
        clone, _remote, _stub = self.ready()
        code, out = _cli(["abort", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        self.assertIn("parked/test-clone/feat", out)
        code, out = _cli(["abort", "--apply", "--repo", str(clone)])
        self.assertEqual(code, 0, out)
        self.assertEqual(_receipt_lines(clone)[-1]["step"], "abort")

    def test_a_blocked_step_exits_3_and_prints_the_sentence(self):
        clone, _remote, stub = self.ready()
        stub.update(auth=False)
        code, out = _cli(["propose", "--repo", str(clone)])
        self.assertEqual(code, 3, out)
        self.assertIn("Refusal: Run gh auth login.", out)


class TestAgentsSyncLine(unittest.TestCase):
    def test_the_sync_rule_allows_the_gh_cli_and_the_file_holds_no_em_dash(self):
        text = (SYNC_DIR.parent / "AGENTS.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertIn(
            "today) and, through the `gh` CLI, the GitHub API of that remote's repository; "
            "nothing else, still no pip installs.", flat)
        self.assertNotIn(chr(0x2014), text)



class TestScriptedRun(ProposedCase):
    """The scripted throwaway-clone run of the spec: real steps through the
    command line, the stub for gh. Run alone and show the table with
    `py -3 -m pytest sync/tests/test_git_flow_pr.py -q -k scripted -s`."""

    def test_scripted_run_start_save_check_propose_land_then_start_abort(self):
        ok = _script(self.tmp, "ok.py", "pass\n")
        clone, remote, _stub = self.repo(git={"checks": [ok]})
        common = ["--repo", str(clone), "--harness", "script", "--model", "stub"]

        def run(step_args, expect_step, extra_keys=()):
            before = len(_receipt_lines(clone))
            code, out = _cli(step_args + ["--apply"] + common)
            self.assertEqual(code, 0, out)
            lines = _receipt_lines(clone)
            self.assertEqual(len(lines), before + 1, out)  # exactly one receipt line per step
            line = lines[-1]
            self.assertEqual((line["step"], line["outcome"], line["exit_code"]), (expect_step, "pass", 0))
            self.assertEqual(set(line), RECEIPT_FIELDS | set(extra_keys))
            self.assertIn(f"receipt {line['receipt_id']} written", out)
            print(f"{expect_step:8} pass exit=0 receipt={line['receipt_id'][:8]} branch={line['branch']}")
            return line

        run(["start", "run-one"], "start")
        _write(clone, "notes/one.md", "one\n")
        run(["save"], "save")
        run(["check"], "check")
        proposed = run(["propose"], "propose", ["pr"])
        landed = run(["land"], "land", ["pr"])
        self.assertEqual(proposed["pr"]["number"], landed["pr"]["number"])
        self.assertEqual(landed["pr"]["merge_commit"], landed["sha_after"])
        self.assertEqual(_head(clone), landed["sha_after"])
        run(["start", "run-two"], "start")
        aborted = run(["abort"], "abort")
        self.assertEqual(aborted["branch"], "main")
        self.assertEqual(sorted(_branches(clone)), ["main", "parked/test-clone/run-two"])
        self.assertEqual([l["step"] for l in _receipt_lines(clone)],
                         ["start", "save", "check", "propose", "land", "start", "abort"])
