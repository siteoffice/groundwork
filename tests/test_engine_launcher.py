"""Tests for the engine rule of the siteoffice-git launcher (git_flow.ps1)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugins" / "groundwork" / "skills" / "siteoffice-git" / "scripts" / "git_flow.ps1"
PWSH = shutil.which("pwsh")
PYTHON = shutil.which("python")

pytestmark = pytest.mark.skipif(
    PWSH is None or PYTHON is None, reason="pwsh and python are both needed to run the launcher"
)


def layout(tmp_path, bundled=True):
    launcher = tmp_path / "plug" / "skills" / "siteoffice-git" / "scripts" / "git_flow.ps1"
    launcher.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, launcher)
    if bundled:
        engine = tmp_path / "plug" / "engine" / "sync"
        engine.mkdir(parents=True)
        (engine / "git_flow.py").write_text('print("BUNDLED")\n', encoding="ascii")
    repo = tmp_path / "repo"
    (repo / "evil").mkdir(parents=True)
    (repo / "sync").mkdir()
    (repo / "siteoffice.json").write_text(
        '{"settings": {"git": {"engine": "evil/git_flow.py"}}}', encoding="ascii"
    )
    for sub in ("evil", "sync"):
        (repo / sub / "git_flow.py").write_text('print("REPO")\n', encoding="ascii")
    return launcher, repo


def run(launcher, repo, sync_dir=None):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SITEOFFICE_SYNC_DIR", "SITEOFFICE_HARNESS", "CLAUDECODE")
    }
    if sync_dir is not None:
        env["SITEOFFICE_SYNC_DIR"] = sync_dir
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(launcher), "check"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_repo_named_engines_are_ignored_and_the_bundled_one_runs(tmp_path):
    launcher, repo = layout(tmp_path)
    r = run(launcher, repo)
    assert r.returncode == 0, r.stderr
    assert "BUNDLED" in r.stdout
    assert "REPO" not in r.stdout


def test_sync_dir_inside_the_repo_is_ignored(tmp_path):
    launcher, repo = layout(tmp_path)
    r = run(launcher, repo, str(repo / "evil"))
    assert "BUNDLED" in r.stdout and "REPO" not in r.stdout
    assert "is ignored" in r.stderr


def test_sync_dir_spelled_with_a_detour_and_other_case_is_ignored(tmp_path):
    launcher, repo = layout(tmp_path)
    spelled = str(tmp_path / "other" / ".." / "REPO" / "Evil") + os.sep
    r = run(launcher, repo, spelled)
    if os.name != "nt" and not (repo / "Evil").exists():
        pytest.skip("case-insensitive spelling only applies on Windows")
    assert "BUNDLED" in r.stdout and "REPO" not in r.stdout


def test_relative_sync_dir_is_ignored(tmp_path):
    launcher, repo = layout(tmp_path)
    r = run(launcher, repo, "evil")
    assert "BUNDLED" in r.stdout and "REPO" not in r.stdout
    assert "is ignored" in r.stderr


def test_sync_dir_inside_a_worktree_is_ignored(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    launcher, repo = layout(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-q", "-m", "x"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "wt"], cwd=repo, check=True, capture_output=True)
    (wt / "evil").mkdir()
    (wt / "evil" / "git_flow.py").write_text('print("WORKTREE")\n', encoding="ascii")
    r = run(launcher, repo, str(wt / "evil"))
    assert "BUNDLED" in r.stdout and "WORKTREE" not in r.stdout


def test_sync_dir_outside_the_repo_is_honoured(tmp_path):
    launcher, repo = layout(tmp_path)
    own = tmp_path / "own"
    own.mkdir()
    (own / "git_flow.py").write_text('print("OWN")\n', encoding="ascii")
    r = run(launcher, repo, str(own))
    assert "OWN" in r.stdout


def test_link_from_outside_into_the_repo_is_ignored(tmp_path):
    launcher, repo = layout(tmp_path)
    link = tmp_path / "linked"
    target = repo / "evil"
    try:
        if os.name == "nt":
            made = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
            )
            if made.returncode != 0:
                raise OSError(made.stdout + made.stderr)
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError as e:
        pytest.skip("this OS refused to create a link without privileges: %s" % e)
    r = run(launcher, repo, str(link))
    assert "BUNDLED" in r.stdout and "REPO" not in r.stdout


def test_no_engine_refuses_with_exit_2(tmp_path):
    launcher, repo = layout(tmp_path, bundled=False)
    r = run(launcher, repo)
    assert r.returncode == 2
    assert "Engine not found" in r.stderr
    assert "REPO" not in r.stdout


@pytest.mark.parametrize("value", ["/", "\\", "//", "\\\\"])
def test_sync_dir_that_is_only_separators_is_ignored_without_a_raw_error(tmp_path, value):
    launcher, repo = layout(tmp_path)
    r = run(launcher, repo, value)
    assert "BUNDLED" in r.stdout and "REPO" not in r.stdout
    assert "is ignored" in r.stderr
    assert "Join-Path" not in r.stderr and "Cannot bind" not in r.stderr
