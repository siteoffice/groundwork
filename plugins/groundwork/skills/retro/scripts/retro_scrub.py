"""Secret scrubber for the retro skill. Findings never hold the value."""
import math
import re
from typing import Any, Dict, List, Tuple

FIX_TEXT = "rotate the secret, fix the writer that put it there, clean the copies"
REDACTED = "[REDACTED:{kind}]"

PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github_pat", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("stripe_key", r"\b[sr]k_live_[0-9A-Za-z]{24,}"),
    ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"),
    ("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("connection_string", r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@"),
    ("password_assignment",
     r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[=:]\s*[\"']?[^\s\"']{8,}"),
)

_COMPILED = tuple((k, re.compile(p)) for k, p in PATTERNS)
_RUN_ANY = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_MARKER = re.compile(r"\[REDACTED:[a-z_]+\]")


def shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in (token.count(ch) for ch in set(token)))


def _is_secret_run(tok: str) -> bool:
    return (any(c.isupper() for c in tok) and any(c.islower() for c in tok)
            and any(c.isdigit() for c in tok) and shannon_entropy(tok) >= 4.3)


class Scrubber:
    def __init__(self) -> None:
        self.findings: List[Dict[str, str]] = []
        self._seen = set()

    def _record(self, kind: str, session: str, date: str) -> None:
        if (session, kind) in self._seen:
            return
        self._seen.add((session, kind))
        self.findings.append({"type": "secret_exposure", "session": session,
                              "date": date, "kind": kind, "fix": FIX_TEXT})

    def scrub(self, text: str, session: str, date: str) -> str:
        for kind, rx in _COMPILED:
            def sub(_m, kind=kind):
                self._record(kind, session, date)
                return REDACTED.format(kind=kind)
            text = rx.sub(sub, text)
        # entropy pass: split around markers so a marker is never rescanned
        parts = []
        last = 0
        for m in _MARKER.finditer(text):
            parts.append(self._entropy(text[last:m.start()], session, date))
            parts.append(m.group(0))
            last = m.end()
        parts.append(self._entropy(text[last:], session, date))
        return "".join(parts)

    def _entropy(self, seg: str, session: str, date: str) -> str:
        def sub(m):
            tok = m.group(0)
            if len(tok) > 200 or not _is_secret_run(tok):
                return tok
            self._record("high_entropy", session, date)
            return REDACTED.format(kind="high_entropy")
        return _RUN_ANY.sub(sub, seg)

    def scan(self, text: str, session: str, date: str) -> None:
        self.scrub(text, session, date)

    def scrub_obj(self, obj: Any, session: str, date: str) -> Any:
        if isinstance(obj, str):
            return self.scrub(obj, session, date)
        if isinstance(obj, list):
            return [self.scrub_obj(v, session, date) for v in obj]
        if isinstance(obj, dict):
            return {self.scrub_obj(k, session, date): self.scrub_obj(v, session, date)
                    for k, v in obj.items()}
        return obj
