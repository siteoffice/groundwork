"""The ship drill: G4 end to end against a real throwaway git repository.

Integration-style, matching the parent spec's verification item 5. Real git,
real flow.json, real foreman.json, real receipts. NO monkeypatching of git and
NO network: every run passes push=False and no bare "origin" repo is built.

Plain pytest, stdlib only, hermetic.
"""
import subprocess

import flow_transition as ft
import foreman_plan as fp
import foreman_run as fr

NOW = "2026-08-17T12:00:00Z"


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True)


def _commit(cwd, message):
    _git(["add", "-A"], cwd)
    _git(["commit", "-m", message], cwd)


def _build_repo(tmp_path, slug):
    """A repo on `main` with one commit, plus a linked worktree on
    `sprint/<slug>` carrying one extra commit. Per-repo git config so the
    fixture never touches the operator's global config, and symbolic-ref rather
    than `git init -b main`, which needs git 2.28 or newer."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Foreman Test"], root)
    _git(["config", "commit.gpgsign", "false"], root)
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _commit(root, "initial commit")
    _git(["branch", "sprint/" + slug], root)
    worktree = tmp_path / "wt"
    _git(["worktree", "add", str(worktree), "sprint/" + slug], root)
    (worktree / "feature.py").write_text("def f():\n    return 1\n",
                                         encoding="utf-8")
    _commit(worktree, "add feature")
    return root, worktree


def _build_flow(tmp_path, slug):
    """A flow at execute/phase=sprint with no gate, on disk."""
    flows_root = tmp_path / "flows"
    flows_root.mkdir(exist_ok=True)
    flow_dir, flow = ft.cmd_new(flows_root, "znd", "ship the thing", now=NOW,
                                slug=slug)
    flow["stage"] = "execute"
    flow["steps"] = [{"index": 0, "title": "build it", "status": "running"}]
    flow["current_step"] = {"index": 0, "of": 1, "phase": "sprint"}
    ft.write_flow(flow_dir, flow)
    return flow_dir


def _preflight_action():
    return fp.NextAction(
        kind="ship_preflight", why="the sprint passed, so the checks run",
        blocking="", command="", gate_id=None, step_index=0,
        role="ship_preflight", guidance_key="ship_preflight")


def _ship_action():
    return fp.NextAction(
        kind="ship_execute", why="G4 is decided ship, so the branch merges",
        blocking="", command="", gate_id="G4", step_index=0, role="none",
        guidance_key="ship_execute")


def _drive_preflight(flow_dir, root, worktree, slug, monkeypatch):
    """Run the real preflight against the real repo.

    The one thing injected is WHICH repository: repo_root_for derives it from a
    sprint.json this drill has no reason to write to disk, so the resolver is
    pointed at the throwaway repo. Everything downstream of it, every git call
    included, is real.
    """
    sprint = {
        "sprint_id": "znd__{}__20260817T090000Z".format(slug),
        "project": "znd", "worktree_path": str(worktree),
        "worktree_branch": "sprint/" + slug, "status": "passing",
        "last_test_result": {"exit_code": 0, "summary": "680 passed"},
        "last_verify_result": {"outcome": "pass", "summary": "verified"},
    }
    monkeypatch.setattr(fr, "resolve_sprint_for",
                        lambda flow_dir, flow, sprints_root=None: sprint)
    monkeypatch.setattr(fr.sp, "repo_root_for",
                        lambda flow, sprint=None, dev_root=None: str(root))
    return fr.execute_once(flow_dir, _preflight_action(), "drill", now=NOW,
                           stage="execute")


def test_ship_drill_merges_with_no_ff_and_records_a_revertible_sha(
        tmp_path, monkeypatch):
    slug = "drill"
    root, worktree = _build_repo(tmp_path, slug)
    flow_dir = _build_flow(tmp_path, slug)
    pre_merge = _git(["rev-parse", "main"], root).stdout.strip()

    decision = _drive_preflight(flow_dir, root, worktree, slug, monkeypatch)
    assert decision.execute is True, decision.detail
    preflight = fr.load_driver(flow_dir, halt=False)["preflight"]
    assert preflight["clean_tree"] is True
    assert preflight["right_branch"] is True
    assert preflight["revert_point"] == pre_merge

    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G4", 0, NOW)
    ft.cmd_gate(flow_dir, "ship", note="looks right", now=NOW)

    decision = fr.execute_once(flow_dir, _ship_action(), "drill", now=NOW,
                               stage="execute", push=False)
    assert decision.execute is True, decision.detail

    # the merge landed on main
    log = _git(["log", "--oneline", "main"], root).stdout
    assert "add feature" in log
    # and it is a merge commit, not a fast-forward: two parents
    parents = _git(["rev-list", "--parents", "-n", "1", "main"],
                   root).stdout.split()
    assert len(parents) == 3, parents
    # the recorded pre-merge sha is the sha main had BEFORE the merge
    driver = fr.load_driver(flow_dir, halt=False)
    assert driver["shipped"] is True
    assert driver["ship"]["revert_point"] == pre_merge
    assert driver["ship"]["merge_sha"] == parents[0]
    assert driver["ship"]["pushed"] is False

    # unship prints the exact revert line for the real merge sha, and runs
    # nothing: main is unchanged afterwards
    text = fr.cmd_unship(flow_dir)
    assert "git revert -m 1 " + parents[0] in text
    assert _git(["rev-parse", "main"], root).stdout.strip() == parents[0]


def test_ship_drill_hold_leaves_the_branch_unmerged(tmp_path, monkeypatch):
    slug = "holddrill"
    root, worktree = _build_repo(tmp_path, slug)
    flow_dir = _build_flow(tmp_path, slug)
    before = _git(["rev-parse", "main"], root).stdout.strip()

    _drive_preflight(flow_dir, root, worktree, slug, monkeypatch)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G4", 0, NOW)
    ft.cmd_gate(flow_dir, "hold", note="wait for the partner call", now=NOW)

    driver = fr.load_driver(flow_dir, halt=False)
    flow, _line = ft.cmd_advance(flow_dir, now=NOW, driver=driver)
    fr.write_driver(flow_dir, driver)

    assert flow["current_step"]["phase"] == "learn"
    assert flow["gate"] is None
    assert _git(["rev-parse", "main"], root).stdout.strip() == before
    assert "add feature" not in _git(["log", "--oneline", "main"], root).stdout
    held = fr.load_driver(flow_dir, halt=False)["held"]
    assert held["reason"] == "wait for the partner call"


def test_ship_drill_ship_after_a_hold_reopens_g4_and_then_merges(
        tmp_path, monkeypatch):
    slug = "reopendrill"
    root, worktree = _build_repo(tmp_path, slug)
    flow_dir = _build_flow(tmp_path, slug)

    _drive_preflight(flow_dir, root, worktree, slug, monkeypatch)
    flow = ft.load_flow(flow_dir)
    ft.persist_open_gate(flow_dir, flow, "G4", 0, NOW)
    ft.cmd_gate(flow_dir, "hold", note="not yet", now=NOW)
    driver = fr.load_driver(flow_dir, halt=False)
    ft.cmd_advance(flow_dir, now=NOW, driver=driver)
    fr.write_driver(flow_dir, driver)

    reopened = fr.cmd_ship(flow_dir, now=NOW)
    assert reopened["gate"]["id"] == "G4"
    assert not fr.load_driver(flow_dir, halt=False).get("held")

    ft.cmd_gate(flow_dir, "ship", now=NOW)
    decision = fr.execute_once(flow_dir, _ship_action(), "drill", now=NOW,
                               stage="execute", push=False)
    assert decision.execute is True, decision.detail
    assert "add feature" in _git(["log", "--oneline", "main"], root).stdout


def test_ship_drill_unship_says_nothing_to_run_before_any_ship(tmp_path,
                                                               monkeypatch):
    slug = "nevershipped"
    root, worktree = _build_repo(tmp_path, slug)
    flow_dir = _build_flow(tmp_path, slug)
    _drive_preflight(flow_dir, root, worktree, slug, monkeypatch)
    text = fr.cmd_unship(flow_dir)
    assert "git revert" not in text
    assert "znd/nevershipped" in text
