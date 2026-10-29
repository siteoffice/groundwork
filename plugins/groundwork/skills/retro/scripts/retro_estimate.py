"""Estimate the token cost of a retro deep pass. R1 only shows it; nothing enforces the ceiling.

Takes main digests and main transcript bytes only. World data never enters it.
"""
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RATES_USD_PER_MTOK = {"input": 3.0, "output": 15.0}
USD_TO_EUR = 0.92
RATES_AS_OF = "2026-10-29"
WINDOW_5H_TOKENS = 1000000
OUTPUT_SHARE = 0.10
STANDARD_SESSIONS = 10
STANDARD_TOKENS_PER_SESSION = 20000
DEEP_TRANSCRIPT_SHARE = 0.25


def _num(value: Any, default: Optional[float], positive: bool = True) -> Optional[float]:
    """A finite number (bools excluded); positive when asked, else the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(value) or (positive and value <= 0) or (not positive and value < 0):
        return default
    return value


def _count(value: Any) -> int:
    v = _num(value, 0, positive=False)
    return int(v)


def estimate(session_count: int, transcript_bytes: int, digest_bytes: int,
             config: Dict[str, Any]) -> Dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    sessions = _count(session_count)
    tbytes = _count(transcript_bytes)
    dbytes = _count(digest_bytes)
    rates = config.get("rates") if isinstance(config.get("rates"), dict) else {}
    rate_in = _num(rates.get("input"), RATES_USD_PER_MTOK["input"], positive=False)
    rate_out = _num(rates.get("output"), RATES_USD_PER_MTOK["output"], positive=False)
    fx = _num(config.get("usd_to_eur"), USD_TO_EUR, positive=False)
    window = _num(config.get("window_5h_tokens"), WINDOW_5H_TOKENS)
    ceiling = _num(config.get("token_ceiling"), None)

    digest_tokens = math.ceil(dbytes / 4)
    standard = digest_tokens + min(STANDARD_SESSIONS, sessions) * STANDARD_TOKENS_PER_SESSION
    deep = digest_tokens + math.ceil(tbytes / 4 * DEEP_TRANSCRIPT_SHARE)

    def eur(t: int) -> float:
        usd = (t * (1 - OUTPUT_SHARE) * rate_in + t * OUTPUT_SHARE * rate_out) / 1e6
        return round(usd * fx, 2)

    def share(t: int, base: Optional[float]) -> Optional[float]:
        return None if base is None else round(t / base, 4)

    return {
        "sessions": sessions, "transcript_bytes": tbytes, "digest_bytes": dbytes,
        "standard_tokens": standard, "deep_tokens": deep,
        "ceiling_tokens": None if ceiling is None else int(ceiling),
        "standard_share_of_ceiling": share(standard, ceiling),
        "deep_share_of_ceiling": share(deep, ceiling),
        "standard_share_of_5h_window": share(standard, window),
        "deep_share_of_5h_window": share(deep, window),
        "standard_eur": eur(standard), "deep_eur": eur(deep),
        "rates_as_of": RATES_AS_OF,
        "assumptions": ("Assumptions: USD per Mtok input %s, output %s; USD to EUR %s; 5-hour window %d tokens; "
                        "output share %d%%; rates as of %s; assumptions, not live prices; override in config.json."
                        % (rate_in, rate_out, fx, int(window), round(OUTPUT_SHARE * 100), RATES_AS_OF)),
    }


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (x * 100)


def format_estimate(est: Dict[str, Any]) -> List[str]:
    lines = [
        "Estimate for %d sessions (%d transcript bytes, %d digest bytes), rates as of %s."
        % (est["sessions"], est["transcript_bytes"], est["digest_bytes"], est["rates_as_of"]),
        "- Standard pass: about %d tokens, %s of a 5-hour window, about EUR %.2f."
        % (est["standard_tokens"], _pct(est["standard_share_of_5h_window"]), est["standard_eur"]),
        "- Deep pass: about %d tokens, %s of a 5-hour window, about EUR %.2f."
        % (est["deep_tokens"], _pct(est["deep_share_of_5h_window"]), est["deep_eur"]),
    ]
    lines.append("- " + est["assumptions"])
    if est["ceiling_tokens"] is None:
        lines.append("- Ceiling: no ceiling set.")
    else:
        lines.append("- Ceiling %d tokens: standard uses %s, deep uses %s (shown only, not enforced)."
                     % (est["ceiling_tokens"], _pct(est["standard_share_of_ceiling"]),
                        _pct(est["deep_share_of_ceiling"])))
    return lines


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Estimate the retro deep-pass token cost.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--month", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root)
    files = sorted((root / "retro" / args.month / "digests").glob("*.json"))
    tbytes = 0
    dbytes = 0
    for f in files:
        try:
            dbytes += f.stat().st_size
        except OSError:
            continue
        data = _load_json(f)
        if isinstance(data, dict):
            tbytes += _count(data.get("transcript_bytes"))
    config = _load_json(root / "retro" / "config.json")
    for line in format_estimate(estimate(len(files), tbytes, dbytes, config if isinstance(config, dict) else {})):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
