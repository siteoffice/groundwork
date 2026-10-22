"""Tests for slack_web_api.py + slack_sweep_runner.py. Plain pytest, stdlib
only, fixtures only. No live Slack call anywhere: urlopen is always injected."""
import io
import json

import slack_web_api as swa
import slack_sweep_runner as runner


# --- slack_web_api ----------------------------------------------------------

class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(responses):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return _FakeResp(json.dumps(responses.pop(0)).encode("utf-8"))

    return fake_urlopen, calls


def test_load_token_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nSLACK_BOT_TOKEN=xoxb-test-123\n", encoding="utf-8")
    assert swa.load_token(env) == "xoxb-test-123"


def test_load_token_bare_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("xoxb-bare-456\n", encoding="utf-8")
    assert swa.load_token(env) == "xoxb-bare-456"


def test_send_message_returns_ts():
    fake, calls = _fake_urlopen_factory(
        [{"ok": True, "ts": "111.222", "channel": "C0BK70BTFQ9"}])
    send, _ = swa.make_transport(token="xoxb-x", urlopen=fake)
    out = send("C0BK70BTFQ9", "hello")
    assert out == {"ts": "111.222", "channel_id": "C0BK70BTFQ9"}
    assert "chat.postMessage" in calls[0].full_url


def test_send_message_threaded_and_error():
    fake, calls = _fake_urlopen_factory([{"ok": False, "error": "not_in_channel"}])
    send, _ = swa.make_transport(token="xoxb-x", urlopen=fake)
    try:
        send("C1", "nag", thread_ts="99.1")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "not_in_channel" in str(exc)
    assert b"thread_ts" in calls[0].data


def test_read_thread_returns_messages():
    msgs = [{"ts": "1.0", "text": "parent"}, {"ts": "2.0", "text": "reply"}]
    fake, calls = _fake_urlopen_factory([{"ok": True, "messages": msgs}])
    _, read = swa.make_transport(token="xoxb-x", urlopen=fake)
    assert read("C1", "1.0") == msgs
    assert "conversations.replies" in calls[0].full_url


# --- slack_sweep_runner -----------------------------------------------------

def _write_flow(root, project, slug, gate_status):
    d = root / project / slug
    d.mkdir(parents=True)
    gate = None
    if gate_status:
        gate = {"id": "G2", "step_index": 2, "status": gate_status,
                "decision": None, "notified": False,
                "token": "TOK", "opened_at": "2026-07-27T08:00:00Z"}
    (d / "flow.json").write_text(json.dumps(
        {"flow_id": "F1", "project": project, "slug": slug,
         "stage": "execute", "status": "active", "gate": gate}),
        encoding="utf-8")
    return d


def test_awaiting_gates_filters(tmp_path):
    _write_flow(tmp_path, "p1", "closed-flow", None)
    open_dir = _write_flow(tmp_path, "p1", "open-flow", "awaiting-owner")
    got = runner.awaiting_gates(tmp_path)
    assert got == [open_dir]


def test_main_noop_without_open_gates(tmp_path, monkeypatch):
    _write_flow(tmp_path, "p1", "closed-flow", None)
    log = tmp_path / "log.ndjson"
    monkeypatch.setattr(runner, "FLOWS_ROOT", tmp_path)
    monkeypatch.setattr(runner, "LOG_PATH", log)
    assert runner.main() == 0
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["open_gates"] == 0 and rec["host"] == "slack-sweep-runner"


def test_main_fatal_when_token_missing(tmp_path, monkeypatch):
    _write_flow(tmp_path, "p1", "open-flow", "awaiting-owner")
    log = tmp_path / "log.ndjson"
    monkeypatch.setattr(runner, "FLOWS_ROOT", tmp_path)
    monkeypatch.setattr(runner, "LOG_PATH", log)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setattr(swa, "ENV_FILE", tmp_path / "missing.env")
    assert runner.main() == 2
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["errors"] and rec["open_gates"] == 1
