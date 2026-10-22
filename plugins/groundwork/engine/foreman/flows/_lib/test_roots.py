"""Tests for roots.py. Plain pytest, stdlib only, hermetic.

Every test isolates the module-level `_DERIVED_ROOT` and the `ZND_HUB_ROOT`
env var so the suite never depends on, or leaks into, the real hub tree.
"""
import json

import pytest

import roots


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(roots.ENV_VAR, raising=False)
    monkeypatch.delenv(roots.DATA_ROOT_ENV_VAR, raising=False)
    monkeypatch.delenv(roots.DEV_ROOT_ENV_VAR, raising=False)


def test_derives_from_file_when_nothing_else_set(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    assert roots.hub_root() == tmp_path


def test_config_file_overrides_derived(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    configured = tmp_path / "elsewhere"
    (tmp_path / roots.CONFIG_FILENAME).write_text(
        json.dumps({"hub_root": str(configured)}), encoding="utf-8",
    )
    assert roots.hub_root() == configured.resolve()


def test_data_root_is_an_alias_for_hub_root(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    assert roots.data_root() == roots.hub_root() == tmp_path


def test_data_root_env_var_takes_precedence_over_hub_root_env_var(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    from_hub = tmp_path / "from-hub-env"
    from_data = tmp_path / "from-data-env"
    monkeypatch.setenv(roots.ENV_VAR, str(from_hub))
    monkeypatch.setenv(roots.DATA_ROOT_ENV_VAR, str(from_data))
    assert roots.data_root() == from_data.resolve()
    assert roots.hub_root() == from_data.resolve()


def test_hub_root_env_var_still_works_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    from_hub = tmp_path / "from-hub-env"
    monkeypatch.setenv(roots.ENV_VAR, str(from_hub))
    assert roots.data_root() == from_hub.resolve()


def test_status_doc_paths_empty_without_a_legacy_hub_layout(tmp_path):
    assert roots.status_doc_paths(tmp_path) == []


def test_status_doc_paths_finds_the_legacy_hub_files_when_present(tmp_path):
    hub = tmp_path / "10_holding"
    (hub / "01_projects").mkdir(parents=True)
    (hub / "01_projects" / "portfolio.md").write_text("x", encoding="utf-8")
    (hub / "STATUS.md").write_text("x", encoding="utf-8")
    got = roots.status_doc_paths(tmp_path)
    assert got == [
        str(hub / "01_projects" / "portfolio.md"),
        str(hub / "STATUS.md"),
    ]


def test_malformed_config_file_falls_back_to_derived(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    (tmp_path / roots.CONFIG_FILENAME).write_text("not json", encoding="utf-8")
    assert roots.hub_root() == tmp_path


def test_env_var_overrides_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    (tmp_path / roots.CONFIG_FILENAME).write_text(
        json.dumps({"hub_root": str(tmp_path / "elsewhere")}), encoding="utf-8",
    )
    from_env = tmp_path / "from-env"
    monkeypatch.setenv(roots.ENV_VAR, str(from_env))
    assert roots.hub_root() == from_env.resolve()


def test_explicit_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(roots.ENV_VAR, str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"
    assert roots.hub_root(str(explicit)) == explicit.resolve()


def test_flows_sprints_board_paths_derive_from_hub_root(monkeypatch, tmp_path):
    monkeypatch.setattr(roots, "_DERIVED_ROOT", tmp_path)
    assert roots.flows_root() == tmp_path / "flows"
    assert roots.sprints_root() == tmp_path / "sprints"
    assert roots.foreman_board_path() == tmp_path / "foreman" / "index.html"


def test_dev_root_defaults_to_hub_root_parent(monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(roots, "_DERIVED_ROOT", hub)
    assert roots.dev_root() == tmp_path


def test_dev_root_env_var_overrides_derived(monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(roots, "_DERIVED_ROOT", hub)
    from_env = tmp_path / "from-env"
    monkeypatch.setenv(roots.DEV_ROOT_ENV_VAR, str(from_env))
    assert roots.dev_root() == from_env.resolve()


def test_dev_root_explicit_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(roots.DEV_ROOT_ENV_VAR, str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"
    assert roots.dev_root(str(explicit)) == explicit.resolve()


def test_resolve_project_dir_prefers_nested_projects_layout(tmp_path):
    dev_root = tmp_path
    nested = dev_root / "50_projects" / "siteoffice"
    nested.mkdir(parents=True)
    (dev_root / "siteoffice").mkdir()  # a stray flat-layout directory too
    assert roots.resolve_project_dir(dev_root, "siteoffice") == nested


def test_resolve_project_dir_falls_back_to_flat_layout(tmp_path):
    dev_root = tmp_path
    (dev_root / "10_holding").mkdir()  # the hub itself, no nested projects\ entry
    assert roots.resolve_project_dir(dev_root, "10_holding") == dev_root / "10_holding"


def test_resolve_project_dir_neither_exists_returns_flat_path(tmp_path):
    dev_root = tmp_path
    assert roots.resolve_project_dir(dev_root, "ghost") == dev_root / "ghost"
