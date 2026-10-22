"""Tests for spec_lint.py. Plain pytest, stdlib only, hermetic.

Every test points spec_lint.DEV_ROOT at a temp directory via monkeypatch so
nothing depends on the real C:\\work contents.
"""
import pathlib

import pytest

import spec_lint


def _good_spec_text(project: str, spec_path: str) -> str:
    """A structurally well-formed spec: all required sections, a Source line,
    a valid trailing /sprint invocation, and no em dash anywhere."""
    return "\n".join(
        [
            "# Spec: demo feature",
            "",
            "Source: docs/roadmap.md lines 1-5",
            "",
            "## Problem",
            "The thing is broken.",
            "",
            "## Scope (locked)",
            "Fix the thing.",
            "",
            "## New tests (required pattern)",
            "A test that checks the thing.",
            "",
            "## Verification",
            "pytest -q is green.",
            "",
            "## Explicitly out of scope",
            "Everything else.",
            "",
            "## Success criteria",
            "The thing works.",
            "",
            "## /sprint invocation",
            "",
            "```",
            f"/sprint {project} {spec_path}",
            "```",
            "",
        ]
    )


def _setup_project(root: pathlib.Path, name: str, config: str = "pyproject.toml") -> pathlib.Path:
    """Create root/name as a directory. If config is non-empty, drop that
    config file inside so rule 3 can detect a test command."""
    proj = root / name
    proj.mkdir(parents=True, exist_ok=True)
    if config:
        (proj / config).write_text("", encoding="utf-8")
    return proj


def _dev_root(tmp_path, monkeypatch) -> pathlib.Path:
    dev_root = tmp_path / "dev"
    dev_root.mkdir()
    monkeypatch.setattr(spec_lint, "DEV_ROOT", dev_root)
    return dev_root


def test_well_formed_spec_passes(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_good_spec_text("demoproj", str(spec_file)), encoding="utf-8")
    assert spec_lint.lint(spec_file) == []


@pytest.mark.parametrize(
    "heading, expected_label",
    [
        ("## Problem", "problem/objective"),
        ("## Scope (locked)", "scope"),
        ("## New tests (required pattern)", "tests"),
        ("## Verification", "verification"),
        ("## Explicitly out of scope", "out-of-scope"),
        ("## Success criteria", "success-criteria"),
    ],
)
def test_missing_each_required_section_fails_that_rule(
    tmp_path, monkeypatch, heading, expected_label
):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    text = _good_spec_text("demoproj", str(spec_file)).replace(heading + "\n", "", 1)
    spec_file.write_text(text, encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert f"missing required section: {expected_label}" in failures


def test_missing_source_header_fails_rule_2(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    text = _good_spec_text("demoproj", str(spec_file)).replace(
        "Source: docs/roadmap.md lines 1-5\n", "", 1
    )
    spec_file.write_text(text, encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("Source" in f for f in failures)


def test_em_dash_fails_rule_5(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    text = _good_spec_text("demoproj", str(spec_file)).replace(
        "The thing is broken.", "The thing " + spec_lint.EM_DASH + " broken."
    )
    spec_file.write_text(text, encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("U+2014" in f for f in failures)


def test_project_without_test_config_fails_rule_3(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "cfgless", config="")  # dir exists, no config file
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_good_spec_text("cfgless", str(spec_file)), encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("no detectable test config" in f for f in failures)


def test_nonexistent_project_fails_rule_4(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    # "ghost" is never created under dev_root.
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_good_spec_text("ghost", str(spec_file)), encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("not a directory" in f for f in failures)


def test_missing_spec_path_fails_rule_4(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    missing_path = str(tmp_path / "nope.md")  # never created
    spec_file.write_text(_good_spec_text("demoproj", missing_path), encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("spec-path does not exist" in f for f in failures)


def test_nested_projects_layout_satisfies_rule_3_and_4(tmp_path, monkeypatch):
    """A project under <dev_root>/projects/<name> (this workspace's real
    convention) must resolve correctly, not just the flat <dev_root>/<name>
    layout the other tests above use."""
    dev_root = _dev_root(tmp_path, monkeypatch)
    nested_root = dev_root / "50_projects"
    nested_root.mkdir()
    _setup_project(nested_root, "nestedproj")
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_good_spec_text("nestedproj", str(spec_file)), encoding="utf-8")
    assert spec_lint.lint(spec_file) == []


def test_nested_projects_layout_is_preferred_over_a_stray_flat_directory(tmp_path, monkeypatch):
    """A stray flat-layout directory (no test config) must never shadow the
    real nested project directory (which has one)."""
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "dualproj", config="")  # stray flat dir, no config
    nested_root = dev_root / "50_projects"
    nested_root.mkdir()
    _setup_project(nested_root, "dualproj")  # real nested dir, has config
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_good_spec_text("dualproj", str(spec_file)), encoding="utf-8")
    assert spec_lint.lint(spec_file) == []


def test_no_invocation_line_fails_rule_4(tmp_path, monkeypatch):
    dev_root = _dev_root(tmp_path, monkeypatch)
    _setup_project(dev_root, "demoproj")
    spec_file = tmp_path / "spec.md"
    text = _good_spec_text("demoproj", str(spec_file))
    # Drop the /sprint line, leaving only the fence and headings.
    text = text.replace(f"/sprint demoproj {spec_file}\n", "", 1)
    spec_file.write_text(text, encoding="utf-8")
    failures = spec_lint.lint(spec_file)
    assert any("must end with a valid" in f for f in failures)
