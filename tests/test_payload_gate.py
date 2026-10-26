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
