import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from retro_estimate import estimate, format_estimate, main  # noqa: E402

FIELDS = {"sessions", "transcript_bytes", "digest_bytes", "standard_tokens", "deep_tokens", "ceiling_tokens",
          "standard_share_of_ceiling", "deep_share_of_ceiling", "standard_share_of_5h_window",
          "deep_share_of_5h_window", "standard_eur", "deep_eur", "rates_as_of"}


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


def test_main_sums_digests(tmp_path, capsys):
    d = tmp_path / "retro" / "2026-10" / "digests"
    d.mkdir(parents=True)
    (d / "a.json").write_text(json.dumps({"transcript_bytes": 4000}), encoding="utf-8")
    (d / "b.json").write_text("not json", encoding="utf-8")
    (tmp_path / "retro" / "config.json").write_text(json.dumps({"token_ceiling": 100000}), encoding="utf-8")
    assert main(["--root", str(tmp_path), "--month", "2026-10"]) == 0
    out = capsys.readouterr().out
    assert "2 sessions" in out and "no ceiling set" not in out


def test_main_empty_and_corrupt_config(tmp_path, capsys):
    (tmp_path / "retro").mkdir()
    (tmp_path / "retro" / "config.json").write_text("{bad", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--month", "2026-10"]) == 0
    assert "no ceiling set" in capsys.readouterr().out
