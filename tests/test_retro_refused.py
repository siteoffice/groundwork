import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import retro_report as rr  # noqa: E402
from retro_fixture_util import materialize_fixtures, secret_values  # noqa: E402

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
LINE_WORDS = ("transcript files were refused", "could not be read")


def _session(path, sid):
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"type": "user", "timestamp": "2026-10-02T10:00:00Z", "sessionId": sid, "cwd": "C:/demo/proj",
           "entrypoint": "cli", "message": {"role": "user", "content": "please add a feature"}}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _outputs(out):
    return [out[k].read_text(encoding="utf-8") for k in ("report_md", "report_html", "metrics")]


def test_all_refused_probe_finishes_with_zero_sessions(tmp_path, capsys):
    fx = materialize_fixtures(tmp_path / ".env-home" / "fx")
    root = tmp_path / "root"
    rc = rr.main(["--root", str(root), "--fixtures", str(fx), "--now", "2026-10-15"])
    assert rc == 0
    month = root / "retro" / "2026-10"
    for name in ("report.md", "report.html", "metrics.json"):
        assert (month / name).is_file(), name
    m = json.loads((month / "metrics.json").read_text(encoding="utf-8"))
    assert m["sessions"] == 0
    assert m["refused"] > 0 and m["unreadable"] == 0
    md = (month / "report.md").read_text(encoding="utf-8")
    assert "transcript files were refused (a path component starts with .env)" in md
    assert "not in these counts" in md
    for name in ("report.md", "report.html", "metrics.json"):
        text = (month / name).read_text(encoding="utf-8")
        assert ".env-home" not in text
        for v in secret_values().values():
            assert v not in text


def test_mixed_refused_and_good(tmp_path):
    fx = tmp_path / "fx"
    _session(fx / "code" / "good" / "good.jsonl", "good")
    _session(fx / "code" / ".env-x" / "bad.jsonl", "bad")
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    m = json.loads(out["metrics"].read_text(encoding="utf-8"))
    assert m["sessions"] == 1 and m["refused"] == 1 and m["unreadable"] == 0
    assert "1 transcript files were refused" in out["report_md"].read_text(encoding="utf-8")
    assert ".env-x" not in "".join(_outputs(out))


def test_clean_run_has_no_refusal_line(tmp_path):
    fx = tmp_path / "fx"
    _session(fx / "code" / "good" / "good.jsonl", "good")
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    m = json.loads(out["metrics"].read_text(encoding="utf-8"))
    assert m["refused"] == 0 and m["unreadable"] == 0
    assert "transcript files were refused" not in out["report_md"].read_text(encoding="utf-8")


def test_directory_named_jsonl_is_unreadable_and_skipped(tmp_path):
    fx = tmp_path / "fx"
    _session(fx / "code" / "good" / "good.jsonl", "good")
    (fx / "code" / "p" / "x.jsonl").mkdir(parents=True)
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    m = json.loads(out["metrics"].read_text(encoding="utf-8"))
    assert m["sessions"] == 1 and m["unreadable"] == 1 and m["refused"] == 0
    assert "1 could not be read" in out["report_md"].read_text(encoding="utf-8")


def test_permission_error_on_open_is_counted(tmp_path, monkeypatch):
    fx = tmp_path / "fx"
    _session(fx / "code" / "good" / "good.jsonl", "good")
    _session(fx / "code" / "locked" / "locked.jsonl", "locked")
    real = Path.open

    def fake(self, *a, **kw):
        if self.name == "locked.jsonl":
            raise PermissionError("denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "open", fake)
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    m = json.loads(out["metrics"].read_text(encoding="utf-8"))
    assert m["sessions"] == 1 and m["unreadable"] == 1


def test_three_texts_agree_on_zero_sessions():
    sk = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    model = {"platform": "linux", "metrics": {"refused": 0, "unreadable": 0}, "assumptions": ""}
    lim = " ".join(rr._limitations(model))
    for text in (sk, readme, lim):
        flat = " ".join(text.split())
        assert "zero sessions" in flat
        assert "how many files were refused" in flat or "how many files it refused" in flat
