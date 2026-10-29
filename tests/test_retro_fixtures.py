import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
from retro_fixture_util import materialize_fixtures, secret_values
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))


def _sessions():
    return sorted(p for p in FIX.rglob("*.jsonl") if p.name != "audit.jsonl")


def test_five_session_transcripts_exist():
    assert {p.stem for p in _sessions()} == {"s-interactive", "s-headless", "s-scheduled", "s-world", "cw1"}


@pytest.mark.parametrize("path", _sessions(), ids=lambda p: p.stem)
def test_every_line_parses_and_carries_the_plants(path):
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    text = path.read_text(encoding="utf-8")
    assert EXP["injection"] in text
    assert "/.env" in text
    assert EXP["planted_secrets"][path.stem][1] in text
    assert EXP["unknown_types"][path.stem] in {r.get("type") for r in records}


def test_committed_tree_holds_no_secret_value():
    texts = [p.read_text(encoding="utf-8") for p in FIX.rglob("*") if p.is_file()]
    for kind, value in secret_values().items():
        assert not any(value in t for t in texts), kind


def test_secret_values_cover_every_kind():
    assert len(secret_values()) == 13 and "high_entropy" in secret_values()


def test_materialize_swaps_placeholders_for_values(tmp_path):
    out = materialize_fixtures(tmp_path / "copy")
    values = secret_values()
    for stem, (kind, placeholder) in EXP["planted_secrets"].items():
        p = next(q for q in out.rglob("*.jsonl") if q.stem == stem)
        text = p.read_text(encoding="utf-8")
        assert placeholder not in text
        assert values[kind] in text
    assert "@@SECRET:" not in "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    for line in (out / "code" / "demo-acme" / "s-interactive.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_fixtures_are_ascii():
    for p in FIX.rglob("*"):
        if p.is_file():
            assert all(b < 128 for b in p.read_bytes()), p
