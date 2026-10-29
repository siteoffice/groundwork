import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import retro_report as rr  # noqa: E402
from retro_touch import is_finished_record  # noqa: E402

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def _use(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": inp}]}}


@pytest.mark.parametrize("name", ["PowerShell", "powershell", "POWERSHELL", "Bash"])
def test_git_commit_is_finished(name):
    assert is_finished_record(_use(name, command='git commit -m "x"'))


def test_gh_pr_create_is_finished():
    assert is_finished_record(_use("PowerShell", command="gh pr create --fill"))


def test_git_flow_apply_is_finished():
    cmd = '& "$wt\scripts\git_flow.ps1" save --type fix --subject x --apply'
    assert is_finished_record(_use("PowerShell", command=cmd))


def test_read_tool_mentioning_git_commit_is_not_finished():
    assert not is_finished_record(_use("Read", command="git commit -m x", text="git commit"))


def test_other_tool_name_is_not_a_shell_tool():
    assert not is_finished_record(_use("PowerShellX", command='git commit -m "x"'))


def test_powershell_git_status_is_not_finished():
    assert not is_finished_record(_use("PowerShell", command="git status"))


def test_headline_unit_with_powershell_commit(tmp_path):
    d = tmp_path / "fx" / "code" / "s1"
    d.mkdir(parents=True)
    recs = [
        {"type": "user", "timestamp": "2026-10-02T10:00:00Z", "sessionId": "s1", "cwd": "C:/demo/p",
         "entrypoint": "cli", "message": {"role": "user", "content": "please add a feature"}},
        dict(_use("PowerShell", command='git commit -m "x"'), timestamp="2026-10-02T10:05:00Z",
             sessionId="s1"),
    ]
    (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=tmp_path / "fx")
    m = json.loads(out["metrics"].read_text(encoding="utf-8"))
    assert m["finished"] == 1
    assert m["unit"] == "finished piece of work"
