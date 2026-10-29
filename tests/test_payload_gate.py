"""Tests for the payload gate: a .git pointer file is skipped, real hits still fail."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "check-public-payload.ps1"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed, the gate runs on pwsh")
# Built from fragments so this file does not trip the gate itself.
PRIVATE = "C:/" + "Z" + "ND/projects/x/.git/worktrees/y"


def run_gate(root):
    return subprocess.run([PWSH, "-NoProfile", "-File", str(GATE), "-Root", str(root)], capture_output=True, text=True, timeout=120)


@needs_pwsh
def test_a_git_pointer_file_is_skipped(tmp_path):
    (tmp_path / ".git").write_text("gitdir: " + PRIVATE + "\n", encoding="ascii")
    (tmp_path / "README.md").write_text("clean\n", encoding="ascii")
    r = run_gate(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_pwsh
def test_a_nested_git_pointer_file_is_skipped(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".git").write_text("gitdir: " + PRIVATE + "\n", encoding="ascii")
    (tmp_path / "README.md").write_text("clean\n", encoding="ascii")
    r = run_gate(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_pwsh
def test_a_git_folder_is_still_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("url = " + PRIVATE + "\n", encoding="ascii")
    assert run_gate(tmp_path).returncode == 0


@needs_pwsh
def test_a_private_path_in_a_normal_file_still_fails(tmp_path):
    (tmp_path / "notes.md").write_text(PRIVATE + "\n", encoding="ascii")
    assert run_gate(tmp_path).returncode == 1


GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q")
    (d / ".gitignore").write_text("local/\n", encoding="ascii")
    (d / "README.md").write_text("clean\n", encoding="ascii")
    (d / "local").mkdir()
    # One clean commit: with an empty repo git lists 0 files and the gate falls back to the folder walk,
    # which would hide a missing --others or --cached.
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "base")
    return d


def out(r):
    return r.stdout + r.stderr


@needs_pwsh
@needs_git
def test_an_ignored_file_with_a_hit_passes(repo):
    (repo / "local" / "brief.md").write_text(PRIVATE + "\n", encoding="ascii")
    r = run_gate(repo)
    assert r.returncode == 0, out(r)
    assert "files git knows" in out(r)


@needs_pwsh
@needs_git
def test_a_tracked_file_with_a_hit_fails(repo):
    (repo / "notes.md").write_text(PRIVATE + "\n", encoding="ascii")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    r = run_gate(repo)
    assert r.returncode == 1, out(r)
    assert "notes.md" in out(r)


@needs_pwsh
@needs_git
def test_a_new_unignored_file_with_a_hit_fails(repo):
    (repo / "notes.md").write_text(PRIVATE + "\n", encoding="ascii")
    r = run_gate(repo)
    assert r.returncode == 1, out(r)
    assert "notes.md" in out(r)


@needs_pwsh
@needs_git
def test_a_local_fsmonitor_program_is_not_run_by_the_gate(repo, tmp_path):
    marker = tmp_path / "fsmonitor-ran.txt"
    hook = tmp_path / "fsmon.sh"
    hook.write_text("#!/bin/sh\necho ran > '" + marker.as_posix() + "'\n", encoding="ascii", newline="\n")
    hook.chmod(0o755)
    git(repo, "config", "core.fsmonitor", hook.as_posix())
    # Prove this git runs the program at all, else the check below proves nothing.
    subprocess.run([GIT, "-C", str(repo), "status"], capture_output=True, text=True, timeout=60)
    if not marker.exists():
        pytest.skip("this git does not run a core.fsmonitor script here, the check cannot be exercised")
    marker.unlink()
    r = run_gate(repo)
    assert r.returncode == 0, out(r)
    assert not marker.exists(), "the gate ran the repository's core.fsmonitor program"


@needs_pwsh
@needs_git
def test_a_tracked_file_that_is_ignored_later_still_fails(repo):
    (repo / "local" / "kept.md").write_text(PRIVATE + "\n", encoding="ascii")
    git(repo, "add", "-f", "local/kept.md")
    git(repo, "commit", "-q", "-m", "x")
    assert run_gate(repo).returncode == 1


@needs_pwsh
@needs_git
def test_a_deleted_tracked_file_does_not_break_the_gate(repo):
    (repo / "gone.md").write_text("clean\n", encoding="ascii")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    (repo / "gone.md").unlink()
    r = run_gate(repo)
    assert r.returncode == 0, out(r)


@needs_pwsh
@needs_git
def test_a_nested_repository_does_not_break_the_gate(repo):
    n = repo / "nested"
    n.mkdir()
    git(n, "init", "-q")
    (n / "a.md").write_text("clean\n", encoding="ascii")
    r = run_gate(repo)
    assert r.returncode == 0, out(r)


@needs_pwsh
@needs_git
def test_a_nested_repository_file_with_a_hit_does_not_fail_the_outer_gate(repo):
    # A nested repo is not part of what this repo ships: git lists it as one
    # entry, not its files, so the outer gate does not look inside it.
    n = repo / "nested"
    n.mkdir()
    git(n, "init", "-q")
    (n / "a.md").write_text(PRIVATE + "\n", encoding="ascii")
    r = run_gate(repo)
    assert r.returncode == 0, out(r)


@needs_pwsh
@needs_git
def test_a_root_that_the_parent_repo_ignores_is_walked_and_a_hit_fails(repo):
    # git lists 0 files for an ignored folder; the gate must not report that as clean.
    (repo / "local" / "brief.md").write_text(PRIVATE + "\n", encoding="ascii")
    r = run_gate(repo / "local")
    assert r.returncode == 1, out(r)
    assert "brief.md" in out(r)


@needs_pwsh
def test_outside_a_repository_the_gate_walks_the_folder(tmp_path):
    (tmp_path / "README.md").write_text("clean\n", encoding="ascii")
    r = run_gate(tmp_path)
    assert r.returncode == 0, out(r)
    assert "folder walk" in out(r)


@needs_pwsh
def test_outside_a_repository_an_ignore_file_is_not_honoured(tmp_path):
    (tmp_path / ".gitignore").write_text("local/\n", encoding="ascii")
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "brief.md").write_text(PRIVATE + "\n", encoding="ascii")
    assert run_gate(tmp_path).returncode == 1


@needs_pwsh
@pytest.mark.parametrize("rel", ["plugins/groundwork/skills/retro", "tests/fixtures/retro"])
def test_the_retro_skill_and_fixtures_are_public_clean(rel):
    r = run_gate(ROOT / rel)
    assert r.returncode == 0, out(r)
