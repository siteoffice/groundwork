"""Static checks on .github/workflows/install-matrix.yml (line based, no yaml dependency)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "install-matrix.yml"

# Root suite floor (passed plus skipped of the root suite); raise it when root tests are added.
# Windows runs the os.name == "nt" test blocks, Linux and macOS collect 8 fewer (621, run 4e079bf).
ROOT_FLOOR = 628
ROOT_FLOOR_POSIX = 621


def _text() -> str:
    assert WORKFLOW.is_file(), "missing %s" % WORKFLOW
    return WORKFLOW.read_text(encoding="utf-8")


def _lines() -> list:
    return _text().splitlines()


def _strip_comment(value: str) -> str:
    return re.sub(r"\s+#.*$", "", value).strip()


def _top_level_block(name: str) -> list:
    lines = _lines()
    out = []
    inside = False
    for line in lines:
        if not inside:
            if line.rstrip() == "%s:" % name:
                inside = True
            continue
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        out.append(line)
    assert inside, "no top-level %s: block" % name
    return out


def _flow_list(key: str) -> list:
    for line in _lines():
        m = re.match(r"^%s:\s*\[(.*)\]$" % re.escape(key), _strip_comment(line))
        if m:
            return [item.strip() for item in m.group(1).split(",") if item.strip()]
    pytest.fail("no inline list for %s:" % key)


def test_permissions_read_only():
    block = _top_level_block("permissions")
    body = [_strip_comment(l) for l in block if l.strip() and not l.strip().startswith("#")]
    assert "contents: read" in body
    assert not any(re.search(r":\s*write\b", l) for l in body), body
    assert "write-all" not in _text()


def test_every_uses_is_pinned():
    pattern = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
    uses = []
    for line in _lines():
        m = re.match(r"^\s*(?:-\s+)?uses:\s*(.+)$", line)
        if m:
            uses.append(_strip_comment(m.group(1)))
    assert uses, "no uses: lines found"
    bad = [u for u in uses if not pattern.match(u)]
    assert not bad, bad


def test_matrix_is_exact():
    assert _flow_list("os") == ["windows-latest", "macos-latest", "ubuntu-24.04"]
    assert _flow_list("python") == ["'3.9'", "'3.13'"]
    assert "ubuntu-latest" not in _text()


def test_no_secrets():
    assert "secrets." not in _text()


def test_triggers():
    on_block = _top_level_block("on")
    keys = [l.strip() for l in on_block]
    assert "pull_request:" in keys
    assert "workflow_dispatch:" in keys
    assert "tags:" in keys
    assert any(re.match(r"^-\s*['\"]?v\*['\"]?$", k) or re.match(r"^tags:\s*\[['\"]?v\*", k) for k in keys), keys


def test_floors_in_workflow():
    text = _text()
    for floor in (991, 190, 206):
        assert re.search(r"--floor %d\b" % floor, text), "--floor %d missing" % floor
    # The root floor is per OS, read from the matrix.
    assert "--suite root --floor ${{ matrix.root_floor }}" in text
    for os_name, floor in (("windows-latest", ROOT_FLOOR), ("macos-latest", ROOT_FLOOR_POSIX),
                           ("ubuntu-24.04", ROOT_FLOOR_POSIX)):
        pattern = r"-\s*os:\s*%s\s+root_floor:\s*%d\b" % (re.escape(os_name), floor)
        assert re.search(pattern, text), "root_floor %d for %s missing" % (floor, os_name)


def test_sync_suite_not_run():
    offenders = [l for l in _lines() if "pytest" in l and "sync" in l]
    assert not offenders, offenders


def test_suite_steps_do_not_continue_on_error():
    assert not re.search(r"continue-on-error:\s*true", _text())


def test_actionlint_passes():
    exe = shutil.which("actionlint")
    if exe is None:
        pytest.skip("actionlint is not on PATH; install it with winget install --id rhysd.actionlint -e")
    assert WORKFLOW.is_file(), "missing %s" % WORKFLOW
    r = subprocess.run([exe, str(WORKFLOW)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=300)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def _step_containing(needle: str) -> list:
    lines = _lines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^\s+- name:", l)]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = lines[start:end]
        if any(needle in l for l in block):
            return block
    pytest.fail("no step contains %r" % needle)


def test_mods_probe_matches_readme():
    block = _step_containing("claude plugin test")
    text = "\n".join(block)
    assert re.search(r"claude plugin test \.\s*$", text, re.M), text
    assert any(_strip_comment(l) == "working-directory: plugins/groundwork-mods" for l in block), text


def test_ci_requirements_carry_tzdata():
    req = ROOT / "scripts" / "requirements-ci.txt"
    lines = [l.strip() for l in req.read_text(encoding="utf-8").splitlines()]
    assert any(re.match(r"^tzdata==\d", l) for l in lines), lines
