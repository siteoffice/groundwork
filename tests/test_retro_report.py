import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

import retro_report as rr  # noqa: E402
from retro_fixture_util import materialize_fixtures  # noqa: E402
from test_retro_scrub import SAMPLES  # noqa: E402

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def _run(tmp_path, **kw):
    # The world session is routed away by default, so the main totals match EXP["main_totals"].
    fx = materialize_fixtures(tmp_path / "fx")
    kw.setdefault("worlds", [{"folder": EXP["world_folder"], "report_dir": str(tmp_path / "world-out")}])
    return rr.run_retro(tmp_path / "root", NOW, fixtures=fx, **kw)


def _text(out, key="report_md"):
    return out[key].read_text(encoding="utf-8")


def _session_file(base, sid, cwd, text, entry="cli"):
    d = base / "code" / sid
    d.mkdir(parents=True, exist_ok=True)
    rec = {"type": "user", "timestamp": "2026-10-02T10:00:00Z", "sessionId": sid, "cwd": cwd,
           "entrypoint": entry, "message": {"role": "user", "content": text}}
    (d / (sid + ".jsonl")).write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_md_html_metrics_written(tmp_path):
    out = _run(tmp_path)
    for key in ("report_md", "report_html", "metrics"):
        assert out[key].is_file()
    m = json.loads(_text(out, "metrics"))
    assert m["metric_version"] == 1
    for k, v in EXP["main_totals"].items():
        assert m[k] == v, k
    assert m["secret_exposures"] >= 1 and m["silent_failures"] >= 1


def test_headings_in_order(tmp_path):
    md = _text(_run(tmp_path))
    heads = re.findall(r"^#+ .*$", md, re.M)
    assert heads == ["# Retro 2026-10", "## Headline and trend", "## Silent failures", "## Top friction",
                     "## Secret exposures", "## Hook and MCP health", "## Skills used and unused",
                     "## Estimate for the deep pass", "## Separate worlds", "## Limitations"]
    assert "6.0 touches per finished piece of work (6 touches, 4 sessions, 1 finished)." in md


def test_counts_only_marker_present(tmp_path):
    out = _run(tmp_path)
    assert rr.COUNTS_ONLY_LINE in _text(out)
    assert "counts only" in _text(out, "report_html")


def test_trend_reads_previous_month(tmp_path):
    prev = tmp_path / "root" / "retro" / "2026-09"
    prev.mkdir(parents=True)
    shutil.copy(FIX / "previous_metrics.json", prev / "metrics.json")
    assert "was 2.0 in 2026-09 (+4.0)" in _text(_run(tmp_path))


@pytest.mark.parametrize("content,expected", [
    (None, "no trend: no metrics.json for 2026-09"),
    ("{", "no trend: previous metrics.json unreadable"),
    ("[]", "no trend: previous metrics.json unreadable"),
    ('{"metric_version": 1}', "no trend: previous metrics.json unreadable"),
    ('{"metric_version": 0, "headline": 1.0}', "metric definition changed (v0 to v1), not comparable"),
])
def test_trend_handles_missing_corrupt_and_other_version(tmp_path, content, expected):
    if content is not None:
        prev = tmp_path / "root" / "retro" / "2026-09"
        prev.mkdir(parents=True)
        (prev / "metrics.json").write_text(content, encoding="utf-8")
    assert expected in _text(_run(tmp_path))


def test_month_helpers():
    assert rr.month_of(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)) == "2026-10"
    assert rr.previous_month("2026-01") == "2025-12"
    assert rr.previous_month("2026-10") == "2026-09"


def test_injection_appears_only_as_quoted_snippet(tmp_path):
    out = _run(tmp_path)
    lines = [ln for ln in _text(out).splitlines() if EXP["injection"] in ln]
    assert lines and all(ln.startswith("> ") for ln in lines)
    html = _text(out, "report_html")
    quoted = "".join(re.findall(r"<blockquote>(.*?)</blockquote>", html, re.S))
    assert html.count(EXP["injection"]) == quoted.count(EXP["injection"]) >= 1


def test_quote_snippet_scrubs_and_flattens():
    from retro_scrub import Scrubber
    q = rr.quote_snippet("a `b`\nkey " + SAMPLES["aws_access_key"], Scrubber(), "s", "2026-10-01")
    assert SAMPLES["aws_access_key"] not in q and "`" not in q and "\n" not in q
    assert len(rr.quote_snippet("x" * 500, Scrubber(), "s", "d")) == 160


def test_html_escapes_every_string(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    _session_file(fx, "s-xss", "/home/demo/<script>alert(1)", "yes")
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    html = _text(out, "report_html")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_world_strings_absent_from_main_report_and_metrics(tmp_path):
    world_dir = tmp_path / "world-out"
    out = _run(tmp_path)
    for key in ("report_md", "report_html", "metrics"):
        text = _text(out, key)
        assert EXP["world_marker"] not in text and "private-world" not in text, key
    assert "1 separate worlds analysed, report in %s" % world_dir in _text(out)
    for name in ("report.md", "report.html", "metrics.json"):
        assert (world_dir / "2026-10" / name).is_file()


def test_world_task_cwd_and_project_never_reach_main_outputs(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    task = "world-secret-task"
    first = "<scheduled-task name=" + chr(34) + task + chr(34) + "> yes"
    _session_file(fx, "s-w2", EXP["world_folder"] + "/deep-project", first, entry="sdk-cli")
    world = {"folder": EXP["world_folder"], "report_dir": str(tmp_path / "world-out")}
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx, worlds=[world])
    for key in ("report_md", "report_html", "metrics"):
        text = _text(out, key)
        for needle in (task, "deep-project", "private-world", EXP["world_folder"], EXP["world_marker"], "s-w2"):
            assert needle not in text, (key, needle)


def test_world_line_counts_only_nonempty_worlds(tmp_path):
    worlds = [{"folder": EXP["world_folder"], "report_dir": str(tmp_path / "w1")},
              {"folder": "/home/demo/nothing-here", "report_dir": str(tmp_path / "w2")}]
    md = _text(_run(tmp_path, worlds=worlds))
    assert "1 separate worlds analysed, report in %s" % (tmp_path / "w1") in md
    assert "w2" not in md


def test_no_world_section_when_none_analysed(tmp_path):
    md = _text(_run(tmp_path, worlds=[{"folder": "/home/demo/nothing-here", "report_dir": str(tmp_path / "w")}]))
    assert "## Separate worlds" not in md and "separate worlds analysed" not in md


def test_rejected_gate_patterns_are_reported(tmp_path):
    md = _text(_run(tmp_path, gate_patterns=["x" * 81, "ok*"]))
    assert "1 declared gate patterns refused as unsafe" in md


@pytest.mark.parametrize("kind", list(SAMPLES))
def test_secret_absent_from_digest_and_report(tmp_path, kind):
    fx = tmp_path / "fx"
    _session_file(fx, "s-sec", "/home/demo/p", "here: " + SAMPLES[kind])
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    for p in (tmp_path / "root").rglob("*"):
        if p.is_file():
            assert SAMPLES[kind] not in p.read_text(encoding="utf-8"), p
    assert "s-sec, 2026-10-02, %s. Fix: %s" % (kind, rr.FIX_TEXT) in _text(out)


def test_empty_period_writes_a_report(tmp_path):
    fx = tmp_path / "fx"
    (fx / "code").mkdir(parents=True)
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    m = json.loads(_text(out, "metrics"))
    assert (m["sessions"], m["headline"], m["unit"]) == (0, 0.0, "session")
    assert "0.0 touches per session (0 touches, 0 sessions, 0 finished)." in _text(out)


def test_sessions_but_nothing_finished_uses_session_unit():
    h = rr.headline([{"touches": {"relay": 3}, "finished": False}, {"touches": {}, "finished": False}])
    assert (h["headline"], h["unit"], h["finished"]) == (1.5, "session", 0)


def test_skills_used_and_unused(tmp_path):
    md = _text(_run(tmp_path))
    section = md.split("## Skills used and unused")[1].split("## ")[0]
    assert "Used: one-loop (1)" in section and "Installed and unused: never-used" in section


def test_installed_skills_checks_folders_with_skill_md_only(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("x", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "loose.md").write_text("x", encoding="utf-8")
    assert rr.installed_skills([tmp_path, tmp_path / "missing"]) == ["a"]


def test_limitations_state_the_required_facts(tmp_path, monkeypatch):
    monkeypatch.delenv("GROUNDWORK_RETRO_LIVE", raising=False)
    from retro_estimate import RATES_AS_OF
    md = _text(_run(tmp_path))
    lim = md.split("## Limitations")[1]
    needles = ["metric_version 1", RATES_AS_OF, "Assumptions: USD per Mtok",
               "opened.log also lists world transcript paths", "component starting", ".env", "zero sessions",
               "macOS path", "unverified", "no agent analysis",
               "live test SKIPPED (not armed): structural pass is necessary-but-not-sufficient"]
    for needle in needles:
        assert needle in lim, needle
    assert chr(8212) not in md


def test_cli_dry_run_from_materialized_fixtures(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    cmd = [sys.executable, "-I", str(SCRIPTS / "retro_report.py"), "--root", str(tmp_path / "r"),
           "--fixtures", str(fx), "--now", "2026-10-15"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("report.md", "report.html", "metrics.json"):
        assert (tmp_path / "r" / "retro" / "2026-10" / name).is_file()
    assert len(r.stdout.split()) == 3


def test_cli_world_option_and_bad_world(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    base = [sys.executable, "-I", str(SCRIPTS / "retro_report.py"), "--root", str(tmp_path / "r"),
            "--fixtures", str(fx), "--now", "2026-10-15"]
    ok = subprocess.run(base + ["--world", EXP["world_folder"] + "=" + str(tmp_path / "w")],
                        capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0, ok.stderr
    assert (tmp_path / "w" / "2026-10" / "report.md").is_file()
    bad = subprocess.run(base + ["--world", "nofolder"], capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2


XSS = "<script>alert(1)</script>"
BS = chr(92)
ESC = "&lt;script&gt;alert(1)&lt;/script&gt;"


def _jl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _inject_session(fx, sid, task, hook, skill, final, headless=True):
    first = "<scheduled-task name=" + chr(34) + task + chr(34) + "> run"
    base = {"sessionId": sid, "cwd": "/home/demo/p", "entrypoint": "sdk-cli" if headless else "cli"}
    ts = "2026-10-07T10:00:0%dZ"
    recs = [
        dict(base, type="user", timestamp=ts % 1, message={"role": "user", "content": first}),
        dict(base, type="system", timestamp=ts % 2, content="x", hookErrors=[hook]),
        dict(base, type="assistant", timestamp=ts % 3, message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": skill}}]}),
        dict(base, type="assistant", timestamp=ts % 4, message={"role": "assistant", "content": [
            {"type": "text", "text": final}]}),
    ]
    _jl(fx / "code" / sid / (sid + ".jsonl"), recs)


def test_script_text_is_escaped_in_every_html_branch(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    _inject_session(fx, "s-inj", XSS, "hook " + XSS, XSS, "API Error: 400 " + XSS)
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx, gate_patterns=[XSS + "x" * 80])
    html = _text(out, "report_html")
    assert "<script>" not in html and "</script>" not in html.replace("</script></head>", "")
    assert html.count(ESC) >= 5   # task, snippet, hook, skill, refused gate pattern
    md = _text(out)
    assert "<script>" in md       # Markdown is plain text; only HTML must be escaped


def test_render_html_escapes_title_and_heading():
    m = rr.build_model([], "2026-10<b>", (None, "missing"), [], None, "a", [], None, "linux")
    html = rr.render_html(m)
    assert "<b>" not in html and html.count("2026-10&lt;b&gt;") == 2


@pytest.mark.parametrize("kind", ["aws_access_key", "anthropic_key", "jwt", "connection_string", "github_pat"])
def test_secret_in_hook_and_final_error_absent_from_all_outputs(tmp_path, kind):
    from retro_fixture_util import secret_values
    secret = secret_values()[kind]
    fx = materialize_fixtures(tmp_path / "fx")
    _inject_session(fx, "s-sec2", "t", "hook failed " + secret, "plain", "API Error: 400 " + secret)
    out = rr.run_retro(tmp_path / "root", NOW, fixtures=fx)
    for key in ("report_md", "report_html", "metrics"):
        assert secret not in _text(out, key), key
    assert "s-sec2, 2026-10-07, %s. Fix:" % kind in _text(out)


@pytest.mark.parametrize("kind", ["aws_access_key", "jwt", "connection_string"])
def test_render_scrubs_an_unscrubbed_digest(kind):
    from retro_fixture_util import secret_values
    secret = secret_values()[kind]
    d = {"session_id": "s1", "start": "2026-10-01T00:00:00Z", "headless": True, "final_error": "E " + secret,
         "hook_errors": {"H " + secret: 1}, "mcp_failures": [], "touches": {}, "project": "p " + secret,
         "skills": {"sk " + secret: 1}, "scheduled_task": "t " + secret, "success_line": False}
    m = rr.build_model([d], "2026-10", (None, "missing"), [], None, "a", [], None, "linux")
    for text in (rr.render_md(m), rr.render_html(m)):
        assert secret not in text


@pytest.mark.parametrize("value", ["9" * 400, "1e999", "NaN"])
def test_previous_headline_overflow_or_nonfinite_is_no_trend(tmp_path, value):
    prev = tmp_path / "root" / "retro" / "2026-09"
    prev.mkdir(parents=True)
    (prev / "metrics.json").write_text('{"metric_version": 1, "headline": %s}' % value, encoding="utf-8")
    assert "no trend: previous metrics.json unreadable" in _text(_run(tmp_path))


@pytest.mark.parametrize("days", ["0", "3651", "99999999999999999999"])
def test_cli_rejects_out_of_range_days(tmp_path, days):
    cmd = [sys.executable, "-I", str(SCRIPTS / "retro_report.py"), "--root", str(tmp_path), "--days", days]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "--days must be between 1 and 3650" in r.stderr
    assert "Traceback" not in r.stderr


def test_unused_skill_names_are_cleaned(tmp_path):
    fx = materialize_fixtures(tmp_path / "fx")
    name = "unused[x](evil)"
    (fx / "skills" / name).mkdir()
    (fx / "skills" / name / "SKILL.md").write_text("x", encoding="utf-8")
    md = _text(rr.run_retro(tmp_path / "root", NOW, fixtures=fx))
    assert "unused" + BS + "[x" + BS + "](evil)" in md and "[x](evil)" not in md


@pytest.mark.parametrize("project", ["[x](evil)", "![i](evil)"])
def test_markdown_links_in_project_names_are_inert(tmp_path, project):
    fx = materialize_fixtures(tmp_path / "fx")
    _session_file(fx, "s-link", "/home/demo/" + project, "yes")
    md = _text(rr.run_retro(tmp_path / "root", NOW, fixtures=fx))
    assert project.replace("[", BS + "[").replace("]", BS + "]") in md
    assert "[x](evil)" not in md and "[i](evil)" not in md


def test_live_test_line_follows_the_guard(tmp_path, monkeypatch):
    skipped = "live test SKIPPED (not armed): structural pass is necessary-but-not-sufficient"
    monkeypatch.delenv("GROUNDWORK_RETRO_LIVE", raising=False)
    assert skipped in _text(_run(tmp_path / "a"))
    monkeypatch.setenv("GROUNDWORK_RETRO_LIVE", "1")
    md = _text(_run(tmp_path / "b"))
    assert skipped not in md and "live test armed (GROUNDWORK_RETRO_LIVE=1)" in md
