import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"))

import retro_digest as rd
from retro_scrub import PATTERNS

SKIP = "live test SKIPPED (not armed): structural pass is necessary-but-not-sufficient"


@pytest.mark.skipif(os.environ.get("GROUNDWORK_RETRO_LIVE") != "1", reason=SKIP)
def test_live_collect_last_7_days(tmp_path):
    lad = os.environ.get("LOCALAPPDATA")
    code, cowork, _ = rd.live_locations(Path.home(), Path(lad) if lad else None, sys.platform)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    month_dir = tmp_path / "retro" / "live"
    rd.collect(rd.discover_sources(code, cowork, since), month_dir, "live", [], [])
    for line in (month_dir / "opened.log").read_text(encoding="utf-8").splitlines():
        # Discovery never even tries a .env file, so no line (OPENED or REFUSED) may name one.
        assert not Path(line.split("\t")[1]).name.lower().startswith(".env"), "env path in opened.log"
    digests = sorted((month_dir / "digests").glob("*.json"))
    assert digests, "no digest written"
    rx = [(k, re.compile(r)) for k, r in PATTERNS]
    for p in digests:
        text = p.read_text(encoding="utf-8")
        hit = [k for k, r in rx if r.search(text)]
        assert not hit, "secret pattern kind %s in digest %s" % (hit, p.name)
