import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import retro_digest as rd  # noqa: E402
from retro_fixture_util import materialize_fixtures  # noqa: E402
from retro_estimate import estimate, format_estimate, main  # noqa: E402

FIELDS = {"sessions", "transcript_bytes", "digest_bytes", "standard_tokens", "deep_tokens", "ceiling_tokens",
          "standard_share_of_ceiling", "deep_share_of_ceiling", "standard_share_of_5h_window",
          "deep_share_of_5h_window", "standard_eur", "deep_eur", "rates_as_of", "assumptions"}


def test_fields_and_ceiling_share():
    e = estimate(4, 40000, 8000, {"token_ceiling": 1000000})
    assert set(e) == FIELDS
    assert (e["standard_tokens"], e["deep_tokens"]) == (82000, 4500)
    assert (e["standard_share_of_ceiling"], e["deep_share_of_ceiling"]) == (0.082, 0.0045)
    assert (e["standard_share_of_5h_window"], e["standard_eur"], e["deep_eur"]) == (0.082, 0.32, 0.02)


def test_no_ceiling():
    e = estimate(0, 0, 0, {})
    assert e["standard_share_of_ceiling"] is None
    assert any("no ceiling set" in line for line in format_estimate(e))


def test_config_overrides_rates():
    e = estimate(1, 0, 0, {"rates": {"input": 0.0, "output": 0.0}})
    assert e["standard_eur"] == 0.0


@pytest.mark.parametrize("ceiling", [0, -5, "abc", None, True, float("nan")])
def test_bad_ceiling_means_no_ceiling(ceiling):
    e = estimate(2, 1000, 400, {"token_ceiling": ceiling})
    assert e["ceiling_tokens"] is None
    assert e["standard_share_of_ceiling"] is None
    assert any("no ceiling set" in line for line in format_estimate(e))


@pytest.mark.parametrize("window", [0, -1, "x", None])
def test_bad_window_falls_back_to_default(window):
    e = estimate(1, 0, 0, {"window_5h_tokens": window})
    assert e["standard_share_of_5h_window"] == round(20000 / 1000000, 4)


def test_bad_rates_and_negative_inputs_do_not_crash():
    e = estimate(-3, -10, -10, {"rates": "oops", "usd_to_eur": "no"})
    assert e["sessions"] == 0 and e["standard_tokens"] == 0 and e["deep_tokens"] == 0


def test_window_override_changes_share():
    e = estimate(1, 0, 0, {"window_5h_tokens": 200000})
    assert e["standard_share_of_5h_window"] == 0.1  # 20000 / 200000


def test_rate_and_fx_overrides():
    # standard tokens 20000: usd = (20000*0.9*2.0 + 20000*0.1*10.0) / 1e6 = 0.056; eur = 0.056 * 0.5 = 0.028 -> 0.03
    e = estimate(1, 0, 0, {"rates": {"input": 2.0, "output": 10.0}, "usd_to_eur": 0.5})
    assert e["standard_eur"] == 0.03
    # 1M tokens (50 sessions capped at 10 gives 200000, so use deep): 4,000,000 bytes -> 250000 deep tokens
    # usd = (250000*0.9*2.0 + 250000*0.1*10.0) / 1e6 = 0.7; eur = 0.35
    e = estimate(0, 4000000, 0, {"rates": {"input": 2.0, "output": 10.0}, "usd_to_eur": 0.5})
    assert (e["deep_tokens"], e["deep_eur"]) == (250000, 0.35)


def test_assumptions_line_states_defaults_and_overrides():
    e = estimate(1, 0, 0, {})
    a = e["assumptions"]
    assert isinstance(a, str) and a.isascii() and "\n" not in a
    for part in ("3.0", "15.0", "0.92", "1000000", "10%", "assumptions, not live prices; override in config.json"):
        assert part in a
    assert "- " + a in format_estimate(e)
    e2 = estimate(1, 0, 0, {"rates": {"input": 2.5, "output": 12.5}, "usd_to_eur": 0.5, "window_5h_tokens": 200000})
    a2 = e2["assumptions"]
    assert a2 != a
    for part in ("2.5", "12.5", "0.5", "200000"):
        assert part in a2
    assert "- " + a2 in format_estimate(e2)


def test_main_on_collected_fixture_digests(tmp_path, capsys):
    fx = materialize_fixtures(tmp_path / "fx")
    code, cowork, inner = rd.fixture_locations(fx)
    month_dir = tmp_path / "root" / "retro" / "2026-10"
    res = rd.collect(rd.discover_sources(code, cowork, None, inner), month_dir, "2026-10", [], [])
    files = sorted((month_dir / "digests").glob("*.json"))
    assert len(files) == len(res["main"]) == 5
    dbytes = sum(f.stat().st_size for f in files)
    (tmp_path / "root" / "retro" / "config.json").write_text(json.dumps({"token_ceiling": 100000}), encoding="utf-8")
    assert main(["--root", str(tmp_path / "root"), "--month", "2026-10"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == format_estimate(estimate(5, res["transcript_bytes"], dbytes, {"token_ceiling": 100000}))
    assert "no ceiling set" not in "\n".join(out)


def test_main_empty_and_corrupt_config(tmp_path, capsys):
    (tmp_path / "retro").mkdir()
    (tmp_path / "retro" / "config.json").write_text("{bad", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--month", "2026-10"]) == 0
    assert "no ceiling set" in capsys.readouterr().out
