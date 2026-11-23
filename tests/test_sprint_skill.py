"""The sprint skill must stay in step with the engine files it drives.

The skill is plain Markdown. These tests parse its sprint.json template and every record command
line, run them against the vendored engine, and check the skill names only files that ship.
"""
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "groundwork"
SKILL = PLUGIN / "skills" / "sprint" / "SKILL.md"
LIB = PLUGIN / "engine" / "foreman" / "sprints" / "_lib"


def skill_text():
    return SKILL.read_text(encoding="utf-8")


def command_lines(text):
    starts = ("python $STEP $SPRINT ", "python $DASH status $SPRINT")
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(starts)]


def test_sprint_commands_run(tmp_path):
    text = skill_text()
    blocks = re.findall(r"```json\r?\n(.*?)\r?\n```", text, re.S)
    assert len(blocks) == 1
    raw = blocks[0]
    for key, value in (("<project>", "demo"), ("<slug>", "hello-sprint"), ("<now>", "2026-11-22T09:00:00Z")):
        raw = raw.replace(key, value)
    template = json.loads(raw)
    sprint_dir = tmp_path / "sprints" / "demo" / "hello-sprint"
    sprint_dir.mkdir(parents=True)
    (sprint_dir / "sprint.json").write_text(json.dumps(template, indent=2), encoding="utf-8")

    lines = command_lines(text)
    for a in ("sprint_start", "task_start", "task_done", "test_run",
              "verify_run", "iterate", "sprint_done"):
        assert any(("--action " + a) in line for line in lines), a

    mapping = {"python": sys.executable, "$STEP": str(LIB / "sprint_step.py"),
               "$DASH": str(LIB / "render_dashboard.py"), "$SPRINT": str(sprint_dir)}
    commands = []
    detail_values = []
    for line in lines:
        argv = [mapping.get(tok, tok) for tok in shlex.split(line, posix=True)]
        commands.append(argv)
        if "--detail" in argv:
            detail_values.append(argv[argv.index("--detail") + 1])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    status_result = None
    for argv in commands:
        r = subprocess.run(argv, capture_output=True, text=True, env=env)
        assert r.returncode == 0, (argv, r.stdout, r.stderr)
        if "status" in argv:
            status_result = r
    assert status_result is not None
    assert "hello-sprint" in status_result.stdout
    assert (tmp_path / "_board" / "sprints").is_dir()
    for word in ("Resume:", "_board", "240", "py -3", "git switch -c sprint/"):
        assert word in text, word
    assert detail_values
    for detail in detail_values:
        assert len(detail) <= 240 and "\n" not in detail


def test_sprint_names_only_shipped_files():
    sep = "/" + chr(92) + chr(92)
    found = re.findall("engine[" + sep + "][A-Za-z0-9_." + sep + "-]*[A-Za-z0-9_]", skill_text())
    assert len(found) >= 2
    missing = [p for p in found if not (PLUGIN / p.replace(chr(92), "/")).exists()]
    assert not missing, missing


def test_sprint_stays_attended():
    text = skill_text()
    for word in ("sprint_runner", "--unattended", "poll-once", "acceptEdits"):
        assert word not in text, word
    assert not re.search(r"\bclaude\s+(-p|--print)\b", text)


def test_sprint_description_fits():
    m = re.match(r"---\r?\n(.*?)\r?\n---", skill_text(), re.S)
    assert m, "no frontmatter"
    lines = m.group(1).splitlines()
    description = ""
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            value = line.partition(":")[2].strip()
            if value in (">", ">-"):
                rest = []
                for nxt in lines[i + 1:]:
                    if not nxt.startswith((" ", "\t")):
                        break
                    rest.append(nxt.strip())
                value = " ".join(rest)
            description = value
            break
    assert 0 < len(description) <= 350, len(description)


def test_spec_check_matches_the_skill():
    """spec_lint.py has no command line and its rules 3 and 4 need a hub layout,
    so the skill lists a checklist instead of calling it."""
    text = skill_text()
    assert "spec_lint" not in text
    for section in ("Problem", "Scope", "Tests", "Verification", "Out of scope", "Success criteria"):
        assert section in text, section
