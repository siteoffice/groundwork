"""Tests for ship_preflight.py. Plain pytest, stdlib only, hermetic.

The pure section is tested with literal fixtures; the host section (Task 3)
is tested against a throwaway git repo built per test under tmp_path.
"""
import ship_preflight as sp

NOW = "2026-08-17T12:00:00Z"

SPEC_WITH_SCOPE = """# A spec

## Scope (locked)

1. do the thing.

```
flows/_lib/ship_preflight.py
flows/_lib/test_ship_preflight.py
```

## New tests

Something else.
"""

SPEC_WITHOUT_SCOPE = """# A spec

## Scope (locked)

1. do the thing, with no fenced path list at all.

## New tests

Something else.
"""


def _facts(**over):
    """Facts for a clean, in-scope, budget-respecting, secret-free branch."""
    facts = {
        "status_porcelain": "",
        "current_branch": "sprint/demo",
        "expected_branch": "sprint/demo",
        "sprint": {
            "last_test_result": {"exit_code": 0, "summary": "680 passed"},
            "last_verify_result": {"outcome": "pass", "summary": "verified"},
        },
        "diff_text": "--- a/x.py\n+++ b/x.py\n@@\n+def f():\n+    return 1\n",
        "diff_names": ["flows/_lib/ship_preflight.py"],
        "diff_shortstat": " 1 file changed, 2 insertions(+)\n",
        "scope_globs": ["flows/_lib/*.py"],
        "diff_budget_lines": sp.DEFAULT_DIFF_BUDGET_LINES,
        "revert_point": "a1b2c3d4e5f6",
    }
    facts.update(over)
    return facts


# --- the 13-key contract ------------------------------------------------------

EXPECTED_KEYS = {
    "clean_tree", "right_branch", "worktree_branch", "tests_pass",
    "verify_pass", "no_secrets", "secrets_found", "scope_respected",
    "out_of_scope_paths", "diff_budget", "diff_lines", "no_em_dash",
    "revert_point",
}


def test_evaluate_returns_exactly_the_thirteen_specified_keys():
    assert set(sp.evaluate(_facts())) == EXPECTED_KEYS


def test_evaluate_on_a_clean_passing_branch_passes_every_check():
    out = sp.evaluate(_facts())
    assert out["clean_tree"] is True
    assert out["right_branch"] is True
    assert out["worktree_branch"] == "sprint/demo"
    assert out["tests_pass"] is True
    assert out["verify_pass"] is True
    assert out["no_secrets"] is True
    assert out["secrets_found"] == []
    assert out["scope_respected"] is True
    assert out["out_of_scope_paths"] == []
    assert out["diff_budget"] is True
    assert out["diff_lines"] == 2
    assert out["no_em_dash"] is True
    assert out["revert_point"] == "a1b2c3d4e5f6"


def test_evaluate_never_raises_on_completely_empty_facts():
    out = sp.evaluate({})
    assert set(out) == EXPECTED_KEYS
    assert out["worktree_branch"] == ""
    assert out["revert_point"] == ""
    assert out["scope_respected"] is None
    assert out["diff_lines"] == 0


# --- one check at a time, pass and fail ---------------------------------------

def test_clean_tree_fails_on_a_dirty_porcelain_status():
    out = sp.evaluate(_facts(status_porcelain="?? untracked.txt\n"))
    assert out["clean_tree"] is False


def test_right_branch_fails_and_still_reports_the_branch_it_found():
    out = sp.evaluate(_facts(current_branch="some/other"))
    assert out["right_branch"] is False
    assert out["worktree_branch"] == "some/other"


def test_right_branch_is_false_when_the_branch_is_unknown():
    out = sp.evaluate(_facts(current_branch="", expected_branch=""))
    assert out["right_branch"] is False


def test_tests_pass_fails_on_a_non_zero_exit_code():
    out = sp.evaluate(_facts(sprint={"last_test_result": {"exit_code": 1}}))
    assert out["tests_pass"] is False


def test_tests_pass_fails_when_the_sprint_never_ran_tests():
    """sprints\\_lib\\schema.md line 49: either result block may be null."""
    out = sp.evaluate(_facts(sprint={"last_test_result": None}))
    assert out["tests_pass"] is False


def test_verify_pass_fails_on_a_fail_outcome():
    out = sp.evaluate(_facts(sprint={"last_verify_result": {"outcome": "fail"}}))
    assert out["verify_pass"] is False


def test_verify_pass_fails_when_the_sprint_never_verified():
    out = sp.evaluate(_facts(sprint={"last_verify_result": None}))
    assert out["verify_pass"] is False


def test_diff_budget_fails_past_an_explicit_override():
    out = sp.evaluate(_facts(diff_budget_lines=1))
    assert out["diff_budget"] is False
    assert out["diff_lines"] == 2


def test_diff_budget_uses_the_eight_hundred_line_default_when_none_is_given():
    facts = _facts(diff_shortstat=" 9 files changed, 700 insertions(+), 101 deletions(-)\n")
    del facts["diff_budget_lines"]
    out = sp.evaluate(facts)
    assert out["diff_lines"] == 801
    assert out["diff_budget"] is False


def test_no_em_dash_fails_on_an_added_line_carrying_one():
    out = sp.evaluate(_facts(
        diff_text="+++ b/x.py\n+# a line with " + sp.EM_DASH + " in it\n"))
    assert out["no_em_dash"] is False


def test_no_em_dash_ignores_a_removed_line_carrying_one():
    out = sp.evaluate(_facts(
        diff_text="--- a/x.py\n-# a line with " + sp.EM_DASH + " in it\n+clean\n"))
    assert out["no_em_dash"] is True


# --- secrets ------------------------------------------------------------------

# One sample per pattern brief_composer.find_secrets knows
# (brief_composer.py:36-57): eight token patterns, one URL-param pattern, one
# key=value assignment pattern.
SECRET_SAMPLES = [
    "ghp_" + "a" * 36,
    "github_pat_" + "b" * 44,
    "sk-" + "c" * 24,
    "AKIA" + "D" * 16,
    "xoxb-" + "e" * 20,
    "Bearer " + "f" * 24,
    "AccountKey=abc123def456;",
    "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.dBjftJeZ4CVPmB92",
    "?access_token=1234567890abcdef",
    "api_key = 0123456789abcdef0123",
]


def test_every_known_secret_shape_trips_the_check_and_comes_back_redacted():
    for sample in SECRET_SAMPLES:
        out = sp.evaluate(_facts(diff_text="+++ b/x.py\n+" + sample + "\n"))
        assert out["no_secrets"] is False, sample
        assert out["secrets_found"], sample
        joined = " ".join(out["secrets_found"])
        assert "[REDACTED]" in joined, (sample, joined)


def test_a_clean_diff_reports_no_secrets_and_an_empty_list():
    out = sp.evaluate(_facts())
    assert out["no_secrets"] is True
    assert out["secrets_found"] == []


# --- the scope convention -----------------------------------------------------

def test_scope_globs_reads_the_fenced_path_list_out_of_the_scope_section():
    assert sp.scope_globs(SPEC_WITH_SCOPE) == [
        "flows/_lib/ship_preflight.py", "flows/_lib/test_ship_preflight.py"]


def test_scope_globs_is_none_when_the_scope_section_has_no_fenced_block():
    assert sp.scope_globs(SPEC_WITHOUT_SCOPE) is None


def test_scope_globs_is_none_when_there_is_no_scope_section_at_all():
    assert sp.scope_globs("# A spec\n\nprose only\n") is None
    assert sp.scope_globs("") is None
    assert sp.scope_globs(None) is None


def test_scope_globs_ignores_a_fenced_block_in_a_later_section():
    text = SPEC_WITHOUT_SCOPE + "\n```\nnot/a/scope/list.py\n```\n"
    assert sp.scope_globs(text) is None


def test_scope_respected_is_true_when_every_changed_path_matches_a_glob():
    out = sp.evaluate(_facts(diff_names=["flows/_lib/ship_preflight.py"],
                             scope_globs=["flows/_lib/*.py"]))
    assert out["scope_respected"] is True
    assert out["out_of_scope_paths"] == []


def test_scope_respected_is_false_and_names_the_path_that_escaped():
    out = sp.evaluate(_facts(
        diff_names=["flows/_lib/ship_preflight.py", "foreman/index.html"],
        scope_globs=["flows/_lib/*.py"]))
    assert out["scope_respected"] is False
    assert out["out_of_scope_paths"] == ["foreman/index.html"]


def test_scope_respected_is_none_when_no_list_was_declared_never_a_false_pass():
    out = sp.evaluate(_facts(diff_names=["anything/at/all.py"], scope_globs=None))
    assert out["scope_respected"] is None
    assert out["out_of_scope_paths"] == []


def test_out_of_scope_compares_windows_separators_as_forward_slashes():
    assert sp.out_of_scope(["flows\\_lib\\x.py"], ["flows/_lib/*.py"]) == []


# --- the small pure helpers ---------------------------------------------------

def test_expected_branch_is_sprint_slash_slug():
    assert sp.expected_branch({"slug": "demo"}) == "sprint/demo"


def test_repo_root_comes_from_the_sprints_worktree_path():
    sprint = {"worktree_path": "C:\\work\\_archive\\jarvis\\.worktrees\\sprint-demo"}
    assert sp.repo_root_for({"project": "jarvis"}, sprint) == "C:\\work\\_archive\\jarvis"


def test_repo_root_falls_back_to_dev_root_plus_project():
    got = sp.repo_root_for({"project": "jarvis"}, None, dev_root="C:\\work")
    assert got.replace("/", "\\") == "C:\\work\\jarvis"


def test_repo_root_is_empty_when_nothing_resolves_rather_than_guessed():
    assert sp.repo_root_for({}, None) == ""


def test_added_lines_drops_the_plus_and_the_file_header():
    lines = sp.added_lines("+++ b/x.py\n+one\n-two\n three\n+four\n")
    assert lines == ["one", "four"]


def test_diff_line_total_sums_insertions_and_deletions():
    assert sp.diff_line_total(" 3 files changed, 40 insertions(+), 2 deletions(-)") == 42
    assert sp.diff_line_total(" 1 file changed, 5 insertions(+)") == 5
    assert sp.diff_line_total("") == 0


# --- host section: a real throwaway git repo ---------------------------------

import subprocess

import pytest


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True)


def _commit(cwd, message):
    _git(["add", "-A"], cwd)
    _git(["commit", "-m", message], cwd)


@pytest.fixture
def repo(tmp_path):
    """A throwaway repo on `main` plus a linked worktree on `sprint/demo`.

    Hermetic on purpose: user.name, user.email and commit.gpgsign are set
    per-repo so the fixture neither depends on nor is broken by the operator's
    global git config. HEAD is pointed at `main` with symbolic-ref rather than
    `git init -b main`, which needs git 2.28 or newer.

    Returns (root, worktree). The sprint branch carries one extra commit that
    adds `feature.py`, so `git diff main...sprint/demo` is non-empty.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Foreman Test"], root)
    _git(["config", "commit.gpgsign", "false"], root)
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _commit(root, "initial commit")
    _git(["branch", "sprint/demo"], root)
    worktree = tmp_path / "wt"
    _git(["worktree", "add", str(worktree), "sprint/demo"], root)
    (worktree / "feature.py").write_text("def f():\n    return 1\n",
                                         encoding="utf-8")
    _commit(worktree, "add feature")
    return root, worktree


def _flow():
    return {"project": "ZeroNonsense.dev", "slug": "demo", "spec_path": None}


def _sprint_for(worktree, **over):
    sprint = {
        "sprint_id": "znd__demo__20260817T090000Z",
        "project": "ZeroNonsense.dev",
        "worktree_path": str(worktree),
        "worktree_branch": "sprint/demo",
        "status": "passing",
        "last_test_result": {"command": "pytest -q", "exit_code": 0,
                             "summary": "680 passed", "at": NOW},
        "last_verify_result": {"outcome": "pass", "summary": "verified",
                               "at": NOW},
    }
    sprint.update(over)
    return sprint


def test_run_preflight_on_a_clean_passing_branch(repo):
    root, worktree = repo
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["clean_tree"] is True
    assert out["right_branch"] is True
    assert out["worktree_branch"] == "sprint/demo"
    assert out["tests_pass"] is True
    assert out["verify_pass"] is True
    assert out["no_secrets"] is True
    assert out["scope_respected"] is None
    assert out["diff_budget"] is True
    assert out["diff_lines"] == 2
    assert out["no_em_dash"] is True


def test_run_preflight_records_the_targets_pre_merge_sha_as_revert_point(repo):
    root, worktree = repo
    expected = _git(["rev-parse", "HEAD"], root).stdout.strip()
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["revert_point"] == expected
    assert len(out["revert_point"]) == 40


def test_run_preflight_sees_a_dirty_worktree(repo):
    root, worktree = repo
    (worktree / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["clean_tree"] is False


def test_run_preflight_sees_the_wrong_branch_checked_out(repo):
    root, worktree = repo
    _git(["checkout", "-b", "sprint/typo"], worktree)
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["right_branch"] is False
    assert out["worktree_branch"] == "sprint/typo"


def test_run_preflight_reads_test_and_verify_results_off_the_sprint(repo):
    root, worktree = repo
    sprint = _sprint_for(worktree,
                         last_test_result={"exit_code": 1, "summary": "3 failed"},
                         last_verify_result={"outcome": "fail",
                                             "summary": "regression"})
    out = sp.run_preflight(_flow(), sprint, str(root), spec_text="")
    assert out["tests_pass"] is False
    assert out["verify_pass"] is False


def test_run_preflight_finds_a_secret_in_the_branch_diff_and_redacts_it(repo):
    root, worktree = repo
    token = "ghp_" + "a" * 36
    (worktree / "leak.py").write_text('TOKEN = "' + token + '"\n',
                                      encoding="utf-8")
    _commit(worktree, "oops")
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["no_secrets"] is False
    assert out["secrets_found"]
    assert token not in " ".join(out["secrets_found"])


def test_run_preflight_finds_an_em_dash_in_an_added_line(repo):
    root, worktree = repo
    (worktree / "prose.md").write_text("a line with " + sp.EM_DASH + " in it\n",
                                       encoding="utf-8")
    _commit(worktree, "prose")
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text="")
    assert out["no_em_dash"] is False


def test_run_preflight_scope_check_with_a_fenced_list_present(repo):
    root, worktree = repo
    (worktree / "outside.txt").write_text("not allowed\n", encoding="utf-8")
    _commit(worktree, "one inside, one outside")
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text=SPEC_WITH_SCOPE.replace(
                               "flows/_lib/ship_preflight.py", "feature.py"))
    assert out["scope_respected"] is False
    assert out["out_of_scope_paths"] == ["outside.txt"]


def test_run_preflight_scope_check_is_none_with_no_fenced_list(repo):
    root, worktree = repo
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           spec_text=SPEC_WITHOUT_SCOPE)
    assert out["scope_respected"] is None
    assert out["out_of_scope_paths"] == []


def test_run_preflight_honours_an_explicit_diff_budget_override(repo):
    root, worktree = repo
    out = sp.run_preflight(_flow(), _sprint_for(worktree), str(root),
                           diff_budget_lines=1, spec_text="")
    assert out["diff_budget"] is False
    assert out["diff_lines"] == 2


def test_run_preflight_against_a_directory_that_is_not_a_repo_never_raises(tmp_path):
    """The 'annotates, does not block' rule at the host level: no git repo at
    all still produces a complete 13-key dict."""
    out = sp.run_preflight(_flow(), None, str(tmp_path), spec_text="")
    assert set(out) == EXPECTED_KEYS
    assert out["right_branch"] is False
    assert out["revert_point"] == ""
    assert out["scope_respected"] is None


def test_git_returns_a_non_zero_code_instead_of_raising_when_git_is_missing(
        tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("git not found")
    monkeypatch.setattr(sp.subprocess, "run", boom)
    assert sp.git(["status"], tmp_path) == (1, "")


def test_git_appends_stderr_to_the_output_on_a_non_zero_exit(tmp_path,
                                                              monkeypatch):
    """Finding 2 (2026-08-17 review): git push/merge failures put nearly all
    their diagnostic text on stderr, and the old `return code, proc.stdout`
    dropped it entirely."""
    class _Proc:
        returncode = 1
        stdout = "some stdout\n"
        stderr = "fatal: the real diagnostic text\n"

    monkeypatch.setattr(sp.subprocess, "run", lambda *_a, **_kw: _Proc())
    code, out = sp.git(["push"], tmp_path)
    assert code == 1
    assert "some stdout" in out
    assert "fatal: the real diagnostic text" in out


def test_git_does_not_append_stderr_on_a_zero_exit(tmp_path, monkeypatch):
    """The narrow fix (concatenate only on failure) must not pollute the
    stdout-only contract every successful caller here already relies on
    (branch names, statuses, diff text)."""
    class _Proc:
        returncode = 0
        stdout = "clean stdout\n"
        stderr = "an incidental warning nobody asked for\n"

    monkeypatch.setattr(sp.subprocess, "run", lambda *_a, **_kw: _Proc())
    code, out = sp.git(["status", "--porcelain"], tmp_path)
    assert code == 0
    assert out == "clean stdout\n"


def test_read_spec_text_returns_none_when_the_spec_is_not_on_disk(tmp_path):
    flow = {"project": "ZeroNonsense.dev", "slug": "demo",
            "spec_path": str(tmp_path / "nope.md")}
    assert sp.read_spec_text(flow) is None
