"""Tests for the siteoffice-git PreToolUse guard hook registered by the groundwork plugin."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "groundwork"
HOOKS = PLUGIN / "hooks" / "hooks.json"
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}/"

COMMIT = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
STATUS = {"tool_name": "Bash", "tool_input": {"command": "git status"}}


def _bad_bash(p):
    low = str(p).lower()
    return "system32" in low or "windowsapps" in low


def _find_bash():
    candidates = [shutil.which("bash")]
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for c in candidates:
        if c and Path(c).is_file() and not _bad_bash(c):
            return c
    return None


HOOK_SHELL = _find_bash()
needs_shell = pytest.mark.skipif(
    HOOK_SHELL is None, reason="no POSIX bash found (Git Bash); WSL and WindowsApps bash rejected"
)


def load_hooks():
    return json.loads(HOOKS.read_text(encoding="ascii"))


def hook_command():
    return load_hooks()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def run_hook(payload, path):
    env = {
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
        "PATH": path,
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    return subprocess.run(
        [HOOK_SHELL, "-c", hook_command()],
        input=json.dumps(payload).encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=120,
    )


def test_hooks_file_is_ascii_json_with_one_pretooluse_entry():
    raw = HOOKS.read_bytes()
    text = raw.decode("ascii")
    assert chr(0x2014) not in text
    data = json.loads(text)
    assert list(data["hooks"]) == ["PreToolUse"]
    entries = data["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash|PowerShell"
    assert len(entries[0]["hooks"]) == 1
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert hook["shell"] == "bash"
    assert "onFailure" not in hook


def test_command_names_an_existing_guard():
    command = hook_command()
    m = re.search(r'"' + re.escape(ROOT_VAR) + r'([^"]+)"', command)
    assert m, "no quoted path after ${CLAUDE_PLUGIN_ROOT}/"
    assert m.group(1) == "skills/siteoffice-git/hooks/git_guard.ps1"
    assert (PLUGIN / m.group(1)).is_file()
    assert "-NoProfile -ExecutionPolicy Bypass -File" in command
    assert "command -v powershell" in command and "command -v pwsh" in command
    assert command.index("command -v powershell") < command.index("command -v pwsh")


@needs_shell
def test_commit_is_denied():
    if not (shutil.which("powershell") or shutil.which("pwsh")):
        pytest.skip("neither powershell nor pwsh is installed, the guard cannot run here")
    r = run_hook(COMMIT, os.environ["PATH"])
    assert r.returncode == 2, r.stderr
    assert b"not allowed in an agent session" in r.stderr


@needs_shell
def test_status_is_allowed_silently():
    r = run_hook(STATUS, os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"" and r.stderr == b""


@needs_shell
@pytest.mark.parametrize("payload", [COMMIT, STATUS], ids=["commit", "status"])
def test_neither_shell_is_silent(payload, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run_hook(payload, str(empty))
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"" and r.stderr == b""


@needs_shell
def test_pwsh_fallback():
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is not installed")
    folder = Path(pwsh).parent
    if (folder / "powershell.exe").exists():
        pytest.skip("powershell.exe sits in the pwsh folder, cannot hide it")
    r = run_hook(COMMIT, str(folder))
    assert r.returncode == 2, r.stderr
    assert b"not allowed in an agent session" in r.stderr
    r = run_hook(STATUS, str(folder))
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"" and r.stderr == b""


def test_guard_registered_once_across_plugins():
    hits = [
        p
        for p in sorted((ROOT / "plugins").glob("*/hooks/hooks.json"))
        if "git_guard.ps1" in p.read_text(encoding="utf-8")
    ]
    assert hits == [HOOKS]
