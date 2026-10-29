import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from retro_scrub import PATTERNS, Scrubber, FIX_TEXT

SAMPLES = {
    "private_key": "-----BEGIN" + " RSA " + "PRIVATE KEY-----",
    "aws_access_key": "AKIA" + "IOSFODNN7EXAMPLE",
    "github_pat": "github" + "_pat_" + "11ABCDEFG0123456789_abcdefghij",
    "github_token": "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "anthropic_key": "sk-" + "ant-" + "api03-abcdefghijklmnopqrstuv",
    "stripe_key": "sk" + "_live_" + "abcdefghijklmnopqrstuvwx",
    "openai_key": "sk-" + "proj-" + "abcdefghijklmnopqrstuvwx",
    "slack_token": "xo" + "xb-" + "1234567890-abcdefghij",
    "google_api_key": "AI" + "za" + "SyA1234567890abcdefghijklmnopqrstuv",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "." + "abcdefghijklmnop",
    "connection_string": "postgres://demo:" + "s3cretpass@localhost/db",
    "password_assignment": "pass" + "word=" + "correcthorsebattery9",
}


def test_every_pattern_has_a_sample():
    assert [k for k, _ in PATTERNS] == list(SAMPLES)


@pytest.mark.parametrize("kind", list(SAMPLES))
def test_pattern_scrubs_and_records_kind(kind):
    s = Scrubber()
    out = s.scrub("see " + SAMPLES[kind] + " end", "s1", "2026-10-01")
    assert SAMPLES[kind] not in out
    assert {"type": "secret_exposure", "session": "s1", "date": "2026-10-01", "kind": kind, "fix": FIX_TEXT} in s.findings


def test_high_entropy_token_is_scrubbed():
    s = Scrubber()
    tok = "Zq8xK2mP9vL4" + "nR7tW1yB6cF3hJ5dG0sQ"
    out = s.scrub("tok " + tok, "s1", "2026-10-01")
    assert tok not in out
    assert [f["kind"] for f in s.findings] == ["high_entropy"]


def test_hex_hash_and_uuid_are_not_secrets():
    s = Scrubber()
    text = "commit 3f2a9c0d1e4b5a6978c0d1e2f3a4b5c6d7e8f901 id 9731d067-95c4-4ebc-9edb-3dce64fb2678"
    assert s.scrub(text, "s1", "2026-10-01") == text and s.findings == []


def test_long_base64_blob_is_not_a_secret():
    s = Scrubber()
    blob = "iVBORw0KGgoAAAANSUhEUgAA" + ("Zq8xK2mP9vL4" + "nR7tW1yB6cF3hJ5dG0sQ") * 10
    assert s.scrub(blob, "s1", "2026-10-01") == blob and s.findings == []


def test_scan_records_without_returning():
    s = Scrubber()
    assert s.scan("my key is " + SAMPLES["aws_access_key"], "s1", "2026-10-01") is None
    assert [f["kind"] for f in s.findings] == ["aws_access_key"]


def test_scrub_obj_recurses_and_dedupes():
    s = Scrubber()
    out = s.scrub_obj({"a": [SAMPLES["aws_access_key"], {"b": SAMPLES["aws_access_key"]}]}, "s1", "2026-10-01")
    assert SAMPLES["aws_access_key"] not in json.dumps(out)
    assert len(s.findings) == 1


def test_scrub_obj_recurses_into_tuples():
    s = Scrubber()
    aws = SAMPLES["aws_access_key"]
    out = s.scrub_obj((aws, ["x", (aws, "ok")], {"k": (aws,)}), "s1", "2026-10-01")
    assert isinstance(out, tuple) and isinstance(out[1][1], tuple)
    assert aws not in json.dumps(out)
    assert out[1][1][1] == "ok"


@pytest.mark.parametrize("unit", ["a.", "a-", "a+", "a:", "a/", "a=", "a_"])
def test_no_quadratic_scan_on_long_runs(unit):
    import time
    for text in (unit * 50000, unit * 50000 + "://", "Aa1" * 33000):
        t0 = time.perf_counter()
        Scrubber().scrub(text, "s1", "2026-10-01")
        assert time.perf_counter() - t0 < 1.0


def test_connection_string_still_redacted_in_context():
    cs = SAMPLES["connection_string"]
    for text in (cs, "see " + cs, "prose, then " + cs + " end", "x-" + cs):
        s = Scrubber()
        out = s.scrub(text, "s1", "2026-10-01")
        assert "s3cretpass" not in out
        assert [f["kind"] for f in s.findings] == ["connection_string"]
