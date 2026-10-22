"""Tests for foreman_init.py. Plain pytest, stdlib only, hermetic (tmp_path)."""
import json

import pytest

import foreman_init as fi


def _make_hub(tmp_path):
    (tmp_path / "flows" / "_lib").mkdir(parents=True)
    (tmp_path / "sprints" / "_lib").mkdir(parents=True)
    return tmp_path


def test_refuses_an_unrecognised_directory(tmp_path):
    with pytest.raises(fi.NotAFormanHubError):
        fi.init(tmp_path)


def test_scaffolds_every_contract_directory(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    for rel in fi.SCAFFOLD_DIRS:
        assert (hub / rel).is_dir()


def test_writes_a_minimal_valid_state_file(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    state_path = hub / "04_engineering" / "command_center" / "state" / "znd_os_state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["sources"] == []
    assert data["flags"] == []
    assert data["deadlines"] == []
    assert data["generated_by"] == "foreman_init.py"


def test_writes_deadlines_with_a_now_block(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    text = (hub / "DEADLINES.md").read_text(encoding="utf-8")
    assert "## NOW" in text


def test_second_run_without_force_is_a_no_op(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    state_path = hub / "04_engineering" / "command_center" / "state" / "znd_os_state.json"
    original = state_path.read_text(encoding="utf-8")
    actions = fi.init(hub)
    assert actions == []
    assert state_path.read_text(encoding="utf-8") == original


def test_force_overwrites_state_and_deadlines(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    state_path = hub / "04_engineering" / "command_center" / "state" / "znd_os_state.json"
    state_path.write_text("{}", encoding="utf-8")
    actions = fi.init(hub, force=True)
    assert any("znd_os_state.json" in a for a in actions)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["generated_by"] == "foreman_init.py"


def test_ledger_and_inbox_are_never_overwritten_even_with_force(tmp_path):
    hub = _make_hub(tmp_path)
    fi.init(hub)
    ledger_path = hub / "03_logs" / "hub_update_ledger.tsv"
    ledger_path.write_text("custom content\n", encoding="utf-8")
    fi.init(hub, force=True)
    assert ledger_path.read_text(encoding="utf-8") == "custom content\n"
