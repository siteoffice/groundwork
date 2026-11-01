"""Static checks on .github/workflows/install-matrix.yml (line based, no yaml dependency)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "install-matrix.yml"

# Root suite floor; Task 8 raises it when the tour tests land.
ROOT_FLOOR = 556


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
    for floor in (991, 190, 206, ROOT_FLOOR):
        assert re.search(r"--floor %d\b" % floor, text), "--floor %d missing" % floor


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
