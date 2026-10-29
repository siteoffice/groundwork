import json
import re
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


@pytest.mark.parametrize("name", [".env", ".env.local", ".ENV.production"])
def test_env_path_is_refused_and_logged(tmp_path, name):
    p = tmp_path / name
    p.write_text("X=1\n", encoding="ascii")
    audit = rd.OpenAudit(tmp_path / "opened.log")
    with pytest.raises(rd.RefusedPath):
        audit.open_text(p)
    lines = (tmp_path / "opened.log").read_text(encoding="utf-8").splitlines()
    assert lines == ["REFUSED\t%s\tenv file" % p.resolve()]


@pytest.mark.parametrize("name", ["audit.jsonl", ".audit-key", "notes.txt", "settings.json"])
def test_other_files_are_refused(tmp_path, name):
    p = tmp_path / name
    p.write_text("x\n", encoding="ascii")
    with pytest.raises(rd.RefusedPath):
        rd.OpenAudit(tmp_path / "opened.log").open_text(p)


def test_opened_log_lists_only_transcript_and_metadata_files(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    audit = rd.OpenAudit(tmp_path / "opened.log")
    for s in rd.discover_sources(code, cowork, None, inner):
        rd.digest_session(s, audit, [])
    lines = (tmp_path / "opened.log").read_text(encoding="utf-8").splitlines()
    assert lines and all(l.startswith("OPENED\t") for l in lines)
    for l in lines:
        name = Path(l.split("\t")[1]).name
        assert (name.endswith(".jsonl") and name != "audit.jsonl") or re.fullmatch(r"local_[^/\\]+\.json", name)
        assert ".env" not in name


def test_digest_module_opens_files_in_one_place():
    src = (SCRIPTS / "retro_digest.py").read_text(encoding="utf-8")
    assert len(re.findall(r"\b(?:open|read_text|read_bytes)\(", src)) == 1


def _refused(audit, p, reason="env file"):
    with pytest.raises(rd.RefusedPath):
        audit.open_text(p)
    last = (audit.log_path).read_text(encoding="utf-8").splitlines()[-1]
    assert last.startswith("REFUSED\t") and last.endswith("\t" + reason)


def test_symlink_alias_to_env_file_is_refused(tmp_path):
    target = tmp_path / ".env"
    target.write_text("X=1\n", encoding="ascii")
    link = tmp_path / "s.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip("symlinks not permitted on this account: %s" % exc)
    _refused(rd.OpenAudit(tmp_path / "opened.log"), link)


def test_resolved_env_name_is_refused_with_patched_resolve(tmp_path, monkeypatch):
    alias = tmp_path / "s.jsonl"
    alias.write_text("{}\n", encoding="ascii")
    real = Path.resolve

    def fake(self, *a, **k):
        return real(tmp_path / ".env") if self.name == "s.jsonl" else real(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", fake)
    _refused(rd.OpenAudit(tmp_path / "opened.log"), alias)


@pytest.mark.parametrize("comp", [".env-x", ".ENV-Y"])
def test_env_directory_component_is_refused(tmp_path, comp):
    d = tmp_path / comp / "proj"
    d.mkdir(parents=True)
    p = d / "s.jsonl"
    p.write_text("{}\n", encoding="ascii")
    _refused(rd.OpenAudit(tmp_path / "opened.log"), p)
