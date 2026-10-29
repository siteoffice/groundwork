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
from retro_fixture_util import materialize_fixtures, secret_values


def _planted(sid):
    kind, _placeholder = EXP["planted_secrets"][sid]
    return kind, secret_values()[kind]


def _all(tmp_path, patterns=()):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    audit = rd.OpenAudit(tmp_path / "opened.log")
    srcs = rd.discover_sources(code, cowork, None, inner)
    return {d["session_id"]: d for d in (rd.digest_session(s, audit, list(patterns)) for s in srcs)}


def test_digests_both_formats(tmp_path):
    ds = _all(tmp_path)
    assert set(ds) == set(EXP["sessions"])
    for sid, exp in EXP["sessions"].items():
        assert ds[sid]["source"] == exp["source"]


def test_metric_version_is_1(tmp_path):
    assert {d["metric_version"] for d in _all(tmp_path).values()} == {1}


def test_digest_keys_are_exactly_the_contract(tmp_path):
    keys = {"metric_version", "session_id", "source", "project", "cwd", "folders", "scheduled_task",
            "headless", "model", "start", "end", "transcript_bytes", "user_turns", "tool_errors",
            "permission_denials", "hook_errors", "mcp_servers", "mcp_failures", "interrupts",
            "compactions", "skills", "slash_commands", "ask_user_question", "output_tokens",
            "unknown_record_types", "malformed_lines", "final_error", "success_line", "touches",
            "gate_verdicts_excluded", "finished", "secret_findings"}
    for d in _all(tmp_path).values():
        assert set(d) == keys


def test_unknown_record_types_counted_not_fatal(tmp_path):
    for sid, d in _all(tmp_path).items():
        assert d["unknown_record_types"] == {EXP["unknown_types"][sid]: 1}


def test_interactive_counts(tmp_path):
    d = _all(tmp_path)["s-interactive"]
    for key in ("permission_denials", "interrupts", "compactions", "ask_user_question", "skills",
                "slash_commands", "mcp_servers", "output_tokens", "user_turns", "headless"):
        assert d[key] == EXP["sessions"]["s-interactive"][key], key
    assert d["hook_errors"] == {EXP["hook_error"]["text"]: 1}
    assert d["mcp_failures"] == ["demo-server"]


def test_headless_and_scheduled_fields(tmp_path):
    ds = _all(tmp_path)
    assert ds["s-headless"]["headless"] is True and "API Error: 400" in ds["s-headless"]["final_error"]
    assert ds["s-scheduled"]["scheduled_task"] == "weekly-digest"
    assert ds["s-scheduled"]["success_line"] is False and ds["s-scheduled"]["final_error"] is None
    assert ds["cw1"]["model"] == "claude-demo-model"


def test_cowork_fields(tmp_path):
    d = _all(tmp_path)["cw1"]
    assert d["folders"] == ["/sessions/cw1", "/home/demo/projects/acme"]
    assert d["project"] == "acme" and d["start"] == "2026-10-06T10:01:00Z"


def test_planted_secrets_found_and_value_absent(tmp_path):
    for sid, d in _all(tmp_path).items():
        kind, value = _planted(sid)
        assert kind in {f["kind"] for f in d["secret_findings"]}, sid
        assert value not in json.dumps(d), sid


def test_truncated_and_malformed_lines_are_counted_not_fatal(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    src_file = fx / "code" / "demo-acme" / "s-headless.jsonl"
    proj = tmp_path / "code" / "p"
    proj.mkdir(parents=True)
    t = proj / "s-cut.jsonl"
    t.write_bytes(src_file.read_bytes() + b"\xff\xfe not json\n" + b'{"type":"user","mess')
    audit = rd.OpenAudit(tmp_path / "opened.log")
    d = rd.digest_session(rd.Source("code", t, None), audit, [])
    assert d["malformed_lines"] == 2 and d["user_turns"] >= 1


def test_cowork_extra_files_not_opened_and_private_metadata_not_copied(tmp_path):
    d = _all(tmp_path)["cw1"]
    log = (tmp_path / "opened.log").read_text(encoding="utf-8")
    assert "audit.jsonl" not in log and ".audit-key" not in log
    for marker in EXP["private_markers"]:
        assert marker not in json.dumps(d), marker


def test_since_filters_on_mtime_and_write_digest(tmp_path):
    from datetime import datetime, timedelta
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    assert len(rd.discover_sources(code, cowork, None, inner)) == 5
    assert rd.discover_sources(code, cowork, datetime.now() + timedelta(days=1), inner) == []
    d = _all(tmp_path / "again")["s-headless"]
    out = rd.write_digest(d, tmp_path / "out")
    assert out.name == "code-s-headless.json" and json.loads(out.read_text(encoding="utf-8")) == d


def test_live_locations(tmp_path):
    code, roots, flag = rd.live_locations(tmp_path, tmp_path / "la", "win32")
    assert code == tmp_path / ".claude" / "projects" and flag is False
    (tmp_path / "la" / "Claude-Data" / "claude_x" / "local-agent-mode-sessions").mkdir(parents=True)
    assert len(rd.live_locations(tmp_path, tmp_path / "la", "win32")[1]) == 1
    assert rd.live_locations(tmp_path, None, "linux")[1:] == ([], False)
    assert rd.live_locations(tmp_path, None, "darwin")[2] is True


def _code_transcript(tmp_path, lines):
    proj = tmp_path / "code" / "p"
    proj.mkdir(parents=True, exist_ok=True)
    t = proj / "s-x.jsonl"
    t.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rd.Source("code", t, None)


def test_non_string_record_type_is_counted_not_fatal(tmp_path):
    src = _code_transcript(tmp_path, ['{"type": ["a"]}', '{"type": {"b": 1}}', '{"type": "user", "message": {"content": "hi"}}'])
    d = rd.digest_session(src, rd.OpenAudit(tmp_path / "opened.log"), [])
    assert d["unknown_record_types"] == {"non_string_type": 2} and d["user_turns"] == 1


def test_deeply_nested_line_is_malformed_not_fatal(tmp_path):
    deep = "[" * 200000 + "]" * 200000
    src = _code_transcript(tmp_path, [deep, '{"type": "user", "message": {"content": "hi"}}'])
    d = rd.digest_session(src, rd.OpenAudit(tmp_path / "opened.log"), [])
    assert d["malformed_lines"] == 1 and d["user_turns"] == 1


def test_non_string_metadata_cwd_is_ignored(tmp_path):
    meta = tmp_path / "local_x.json"
    meta.write_text('{"sessionId": "x", "cwd": 5, "userSelectedFolders": [7, "/a/b"]}', encoding="utf-8")
    src = _code_transcript(tmp_path, ['{"type": "user", "message": {"content": "hi"}}'])
    d = rd.digest_session(rd.Source("cowork", src.transcript, meta), rd.OpenAudit(tmp_path / "opened.log"), [])
    assert d["cwd"] is None and d["folders"] == ["/a/b"] and d["project"] == "b"


def test_since_tolerates_a_file_vanishing_before_stat(tmp_path, monkeypatch):
    from datetime import datetime
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    real = Path.stat

    def fake(self, *a, **k):
        if self.name == "s-headless.jsonl":
            raise FileNotFoundError(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "stat", fake)
    got = rd.discover_sources(code, cowork, datetime(2000, 1, 1), inner)
    assert len(got) == 4 and "s-headless" not in {s.transcript.stem for s in got}


def test_touch_classes_per_fixture_expectation(tmp_path):
    for sid, d in _all(tmp_path).items():
        assert d["touches"] == EXP["sessions"][sid]["touches"], sid


def test_finished_and_gate_exclusion(tmp_path):
    ds = _all(tmp_path)
    assert ds["s-interactive"]["finished"] is True and ds["s-interactive"]["gate_verdicts_excluded"] == 1
    assert ds["s-headless"]["finished"] is False


def test_injected_turns_are_not_touches(tmp_path):
    import retro_digest as rdg
    proj = tmp_path / "p" / "proj"
    proj.mkdir(parents=True)
    lines = [{"type": "user", "message": {"role": "user", "content": c}, "sessionId": "inj", "cwd": "/home/demo/x"}
             for c in ("<scheduled-task name=\"t\">yes", "<system-reminder>the other session is green</system-reminder>", "yes")]
    (proj / "inj.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    d = rdg.digest_session(rdg.Source("code", proj / "inj.jsonl", None), rdg.OpenAudit(tmp_path / "o.log"), [])
    assert d["touches"] == {"approval_word": 1}


def test_collect_reports_rejected_gate_patterns(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    res = rd.collect(rd.discover_sources(code, cowork, None, inner), tmp_path / "m", "2026-10", [],
                     ["(a+)+$", "^ok$"])
    assert res["rejected_gate_patterns"] == ["(a+)+$"]
