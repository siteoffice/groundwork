"""Tests for scripts/bump-version.ps1: one version clock across the three manifests."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bump-version.ps1"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed, the bump script runs on pwsh")

MARKET = Path(".claude-plugin") / "marketplace.json"
CORE = Path("plugins") / "groundwork" / ".claude-plugin" / "plugin.json"
MODS = Path("plugins") / "groundwork-mods" / ".claude-plugin" / "plugin.json"
MANIFESTS = (MARKET, CORE, MODS)


def clock_versions(root: Path) -> set:
    """The four clock fields: both marketplace plugin entries and both plugin.json files."""
    market = json.loads((root / MARKET).read_text(encoding="utf-8"))
    values = [p["version"] for p in market["plugins"]]
    for rel in (CORE, MODS):
        values.append(json.loads((root / rel).read_text(encoding="utf-8"))["version"])
    return set(values)


def copy_manifests(dst: Path) -> None:
    for rel in MANIFESTS:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst / rel)


def snapshot(root: Path) -> dict:
    return {rel: (root / rel).read_bytes() for rel in MANIFESTS}


def set_version(path: Path, old: str, new: str) -> None:
    """Replace one exact version line in the copy, keeping everything else byte-identical."""
    text = path.read_bytes().decode("utf-8")
    needle = '"version": "' + old + '"'
    assert needle in text
    path.write_bytes(text.replace(needle, '"version": "' + new + '"', 1).encode("utf-8"))


def run_bump(root: Path, *args: str):
    return subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), *args, "-Root", str(root)],
                          capture_output=True, text=True, timeout=120)


def current() -> str:
    (v,) = clock_versions(ROOT)
    return v


def bumped(level: str) -> str:
    maj, mnr, pat = (int(x) for x in current().split("."))
    if level == "major":
        return "%d.0.0" % (maj + 1)
    if level == "minor":
        return "%d.%d.0" % (maj, mnr + 1)
    return "%d.%d.%d" % (maj, mnr, pat + 1)


@needs_pwsh
def test_dry_run_changes_no_file(tmp_path):
    copy_manifests(tmp_path)
    before = snapshot(tmp_path)
    r = run_bump(tmp_path, "-Bump", "minor")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("DRY RUN"), r.stdout
    assert current() + " -> " + bumped("minor") in r.stdout
    assert snapshot(tmp_path) == before


@needs_pwsh
def test_apply_writes_one_version_everywhere(tmp_path):
    copy_manifests(tmp_path)
    meta_before = json.loads((tmp_path / MARKET).read_text(encoding="utf-8"))["metadata"]["version"]
    new = bumped("minor")
    r = run_bump(tmp_path, "-Bump", "minor", "-Apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("APPLY"), r.stdout
    assert clock_versions(tmp_path) == {new}
    market = json.loads((tmp_path / MARKET).read_text(encoding="utf-8"))
    assert market["metadata"]["version"] == meta_before
    assert "git tag -a v" + new in r.stdout
    # Only the version lines changed: same line count, same line endings.
    for rel in MANIFESTS:
        old_lines = (ROOT / rel).read_bytes().splitlines(keepends=True)
        new_lines = (tmp_path / rel).read_bytes().splitlines(keepends=True)
        assert len(old_lines) == len(new_lines)
        diff = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        assert all(b'"version"' in old_lines[i] for i in diff)


@needs_pwsh
@pytest.mark.parametrize("level", ["patch", "major"])
def test_patch_and_major(tmp_path, level):
    copy_manifests(tmp_path)
    r = run_bump(tmp_path, "-Bump", level, "-Apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert clock_versions(tmp_path) == {bumped(level)}


@needs_pwsh
def test_disagreeing_manifests_refuse(tmp_path):
    copy_manifests(tmp_path)
    set_version(tmp_path / MODS, current(), "0.2.9")
    before = snapshot(tmp_path)
    r = run_bump(tmp_path, "-Bump", "minor", "-Apply")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "groundwork-mods" in out and "plugin.json" in out
    assert "0.2.9" in out
    assert snapshot(tmp_path) == before


def test_repo_manifests_agree():
    assert len(clock_versions(ROOT)) == 1, clock_versions(ROOT)


def test_agreement_check_catches_a_mismatch(tmp_path):
    copy_manifests(tmp_path)
    set_version(tmp_path / MARKET, current(), "9.9.9")
    assert len(clock_versions(tmp_path)) == 2


def test_script_is_ascii():
    SCRIPT.read_bytes().decode("ascii")
