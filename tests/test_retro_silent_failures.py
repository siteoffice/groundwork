import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

import retro_digest as rd
from retro_fixture_util import materialize_fixtures
from retro_report import find_silent_failures


def _digests(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    audit = rd.OpenAudit(tmp_path / "opened.log")
    return [rd.digest_session(s, audit, []) for s in rd.discover_sources(code, cowork, None, inner)]


def test_headless_api_400_and_repeating_hook_are_listed(tmp_path):
    sf = find_silent_failures(_digests(tmp_path))
    assert [f["session"] for f in sf["final_errors"]] == ["s-headless"]
    assert "API Error: 400" in sf["final_errors"][0]["snippet"]
    assert sf["hooks"] == [{"text": EXP["hook_error"]["text"], "sessions": 3}]
    assert sf["mcp"] == [{"server": "demo-server", "sessions": 3}]
    assert [f["scheduled_task"] for f in sf["no_success_line"]] == ["weekly-digest"]


def test_a_hook_in_two_sessions_is_not_listed():
    ds = [{"session_id": str(i), "start": "2026-10-01T00:00:00Z", "headless": False, "final_error": None,
           "scheduled_task": None, "success_line": False, "hook_errors": {"h": 1}, "mcp_failures": []}
          for i in range(2)]
    assert find_silent_failures(ds)["hooks"] == []
