"""Tests for scripts/ci_report.py, the helper the CI workflow calls for floors, skips, probes and the Tested on row."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci_report.py"


def load():
    spec = importlib.util.spec_from_file_location("ci_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def triple(c):
    return (c["passed"], c["skipped"], c["failed"])


def run_cli(*args, env_extra=None, tmp_path=None):
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    env.pop("GITHUB_OUTPUT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=120, env=env)


def test_parse_summary_variants():
    m = load()
    assert triple(m.parse_counts("987 passed, 4 skipped in 12.3s")) == (987, 4, 0)
    assert triple(m.parse_counts("== 555 passed in 9s ==")) == (555, 0, 0)
    assert triple(m.parse_counts("10 passed, 2 skipped, 3 deselected, 1 xfailed, 5 warnings in 1s")) == (10, 2, 0)
    assert m.parse_counts("1 failed, 9 passed in 1s")["failed"] == 1
    assert m.parse_counts("9 passed, 2 errors in 1s")["failed"] == 2
    assert m.parse_counts("206 pass")["passed"] == 206
    with pytest.raises(ValueError):
        m.parse_counts("nothing")


def test_parse_counts_uses_the_last_summary_line():
    m = load()
    text = "test_x.py 3 passed earlier\nsome output\n== 12 passed, 1 skipped in 2s ==\n"
    assert triple(m.parse_counts(text)) == (12, 1, 0)


def test_parse_skips():
    m = load()
    text = ("SKIPPED [1] tests/x.py:12: pwsh is not installed\n"
            "SKIPPED [1] tests/y.py:40: pwsh is not installed\n"
            "== 2 passed, 2 skipped in 1s ==\n")
    skips = m.parse_skips(text)
    assert skips == ["tests/x.py:12: pwsh is not installed", "tests/y.py:40: pwsh is not installed"]


def test_floor_ok_and_below():
    m = load()
    assert m.check_floor({"passed": 987, "skipped": 4, "failed": 0}, 991)
    assert not m.check_floor({"passed": 987, "skipped": 3, "failed": 0}, 991)
    assert not m.check_floor({"passed": 1000, "skipped": 0, "failed": 1}, 991)


def test_floor_line_text():
    m = load()
    c = {"passed": 987, "skipped": 4, "failed": 0}
    assert m.floor_line("engine", c, 991, True) == "engine: 987 passed, 4 skipped, 0 failed (floor 991): ok"
    assert m.floor_line("engine", c, 991, False).endswith(": BELOW FLOOR")


def test_probe_login_is_skipped():
    assert load().classify_probe(1, "Please run /login") == "skipped: needs a Claude login"


def test_probe_other_failure_fails():
    assert load().classify_probe(1, "ENOENT: no such file") == "failed"


def test_probe_zero_is_proven():
    assert load().classify_probe(0, "anything, even login text") == "proven"


def test_tested_on_row_shape():
    row = load().tested_on_row("ubuntu-24.04", "3.13.1", "v22.18.0", "2.1.300 (Claude Code)", "2026-11-02",
                               "https://github.com/o/r/actions/runs/1")
    assert row.startswith("|") and row.endswith("|")
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert len(cells) == 6
    assert cells[0] == "ubuntu-24.04"
    assert cells[3] == "2.1.300"
    assert cells[5] == "[run](https://github.com/o/r/actions/runs/1)"


def test_floor_cli_exit_codes(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("SKIPPED [1] tests/x.py:12: pwsh is not installed\n== 987 passed, 4 skipped in 12s ==\n",
                   encoding="utf-8")
    assert run_cli("floor", "--suite", "engine", "--floor", "991", "--log", str(log)).returncode == 0
    assert run_cli("floor", "--suite", "engine", "--floor", "992", "--log", str(log)).returncode == 1
    summary = tmp_path / "summary.md"
    summary.write_text("earlier line\n", encoding="utf-8")
    r = run_cli("floor", "--suite", "engine", "--floor", "991", "--log", str(log),
                env_extra={"GITHUB_STEP_SUMMARY": str(summary)})
    assert r.returncode == 0, r.stdout + r.stderr
    body = summary.read_text(encoding="utf-8")
    assert body.startswith("earlier line\n")
    assert "engine: 987 passed, 4 skipped, 0 failed (floor 991): ok" in body
    assert "tests/x.py:12: pwsh is not installed" in body


def test_floor_cli_without_counts_fails(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("crashed before the summary\n", encoding="utf-8")
    assert run_cli("floor", "--suite", "engine", "--floor", "1", "--log", str(log)).returncode == 1


def test_probe_cli_out_file(tmp_path):
    out = tmp_path / "probe.txt"
    r = run_cli("probe", "--name", "t", "--out", str(out), "--", sys.executable, "-c", "print('206 passed')")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "206 passed" in out.read_text(encoding="utf-8")


def test_probe_cli_writes_github_output(tmp_path):
    gh_out = tmp_path / "gh_output"
    gh_out.write_text("earlier=1\n", encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = {"GITHUB_OUTPUT": str(gh_out), "GITHUB_STEP_SUMMARY": str(summary)}
    r = run_cli("probe", "--name", "mods", "--", sys.executable, "-c", "print('ok')", env_extra=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert gh_out.read_text(encoding="utf-8") == "earlier=1\nresult=proven\n"
    assert "mods: proven" in summary.read_text(encoding="utf-8")
    r = run_cli("probe", "--name", "mods", "--", sys.executable, "-c",
                "import sys; print('Please run /login'); sys.exit(1)", env_extra=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert gh_out.read_text(encoding="utf-8").endswith("result=skipped\n")
    assert "mods: skipped: needs a Claude login" in summary.read_text(encoding="utf-8")


def test_probe_cli_failure_exits_one(tmp_path):
    gh_out = tmp_path / "gh_output"
    r = run_cli("probe", "--name", "t", "--", sys.executable, "-c",
                "import sys; sys.stderr.write('ENOENT'); sys.exit(2)", env_extra={"GITHUB_OUTPUT": str(gh_out)})
    assert r.returncode == 1
    assert gh_out.read_text(encoding="utf-8") == "result=failed\n"


def test_probe_cli_missing_executable_is_failed_not_a_crash(tmp_path):
    r = run_cli("probe", "--name", "t", "--", "no-such-program-xyz-123")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "not found" in (r.stdout + r.stderr)


def test_script_is_ascii():
    SCRIPT.read_bytes().decode("ascii")


BUN_WITH_SKIP = " 206 pass\n 6 skip\n 0 fail\n 512 expect() calls\nRan 206 tests across 9 files. [1.23s]\n"
BUN_NO_SKIP = " 206 pass\n 0 fail\n 512 expect() calls\nRan 206 tests across 9 files. [1.23s]\n"


def test_parse_counts_bun_style_block_with_skip():
    assert triple(load().parse_counts(BUN_WITH_SKIP)) == (206, 6, 0)


def test_parse_counts_bun_style_block_without_skip():
    assert triple(load().parse_counts(BUN_NO_SKIP)) == (206, 0, 0)


def test_parse_counts_bun_style_block_after_other_output():
    text = "(pass) a > b [1ms]\n(fail) c > d [2ms]\n\n 205 pass\n 1 fail\n 512 expect() calls\nRan 206 tests across 9 files.\n"
    assert triple(load().parse_counts(text)) == (205, 0, 1)


@pytest.mark.parametrize("text", ["see the log in /tmp/x", "writing log info",
                                  "plugin catalog index missing", "dialog init failed"])
def test_probe_login_lookalikes_fail(text):
    assert load().classify_probe(1, text) == "failed"


@pytest.mark.parametrize("text", ["Please run /login", "Invalid API key", "Not logged in",
                                  "authentication_error", "set ANTHROPIC_API_KEY", "Please log in to continue"])
def test_probe_login_variants_are_skipped(text):
    assert load().classify_probe(1, text) == "skipped: needs a Claude login"
