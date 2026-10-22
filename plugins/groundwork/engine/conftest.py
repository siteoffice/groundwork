"""Test configuration for the engine folder.

The vendored siteoffice-git tests (engine\\sync\\tests) include five that check the Siteoffice
repository's own files: its AGENTS.md, its .gitignore, its list of row files. They read those
files one folder above sync\\, so they fail in any other checkout. They test that repository, not
the engine, so they are skipped here. The other 448 tests of that suite still run.

This file sits outside engine\\sync so scripts\\sync-engine.ps1 does not delete it.
"""
import pytest

SITEOFFICE_REPO_LAYOUT_TESTS = {
    "test_after_a_passing_save_the_tree_is_clean_and_start_is_not_refused_as_dirty",
    "test_the_repo_gitignore_lists_the_receipt_log",
    "test_every_listed_row_file_exists",
    "test_no_em_dash_and_scripts_stay_ascii_in_every_changed_file",
    "test_the_sync_rule_allows_the_gh_cli_and_the_file_holds_no_em_dash",
}


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="checks the Siteoffice repository's own layout, not the engine")
    for item in items:
        if item.name in SITEOFFICE_REPO_LAYOUT_TESTS and "sync" in item.nodeid:
            item.add_marker(skip)
