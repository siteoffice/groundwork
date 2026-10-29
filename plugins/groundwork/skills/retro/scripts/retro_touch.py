"""Touch classes and finished work for the retro skill (metric_version 1).

A touch is a user turn that steers or relays instead of asking for something new.
Only the class name leaves this module, never the text.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

TOUCH_CLASSES = ("pasted_shell_output", "approval_word", "relay", "continue_restart",
                 "permission_answer")
GATE_VERDICT = "gate_verdict"
GATE_VERDICTS = frozenset({"approve", "reject", "park", "proceed", "adapt", "close", "ship", "hold"})

_PERMISSION = re.compile(r"(?i)[1-3]|allow( once| always)?|always allow|deny|don'?t allow")
_APPROVAL = frozenset({"yes", "y", "ok", "okay", "go", "go ahead", "do it", "sure", "lgtm", "yep",
                       "looks good", "ja", "prima", "doe maar"})
_CONTINUE = re.compile(r"(?i)(continue|go on|keep going|resume|retry|try again|restart|start over"
                       r"|ga door|verder)\b.{0,50}")
_SHELL_LINE = re.compile(r"^(PS [^>]*>|\$ |>>> |Traceback \(|\s+File \"|\s+at |[A-Za-z]:\\[^>]*>"
                         r"|FAILED|PASSED|ERROR|error[:\[]|warning:|fatal:|npm ERR!|\d+ (passed|failed))")
_RELAY = re.compile(r"(?i)\b(other|previous|parallel|second|first) (session|chat|agent|window|thread)\b"
                    r"|^(relay|forwarding)\b")
_COMMIT = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b"
                     r"|git_flow\.ps1\b.*\b(save|propose|land)\b.*--apply")
_RECEIPT = re.compile(r"receipts[/\\]step-")


MAX_PATTERN_LEN = 80
MAX_GATE_TEXT = 200
MAX_RELAY_TEXT = 200


def load_gate_patterns(patterns: List[str]) -> Tuple[List[str], List[str]]:
    """Vet user gate patterns, which are case-insensitive globs (see glob_match).
    Returns (accepted, rejected): accepted holds lower-cased globs, rejected holds the
    pattern text of any that is empty, not a string, or longer than 80 characters."""
    accepted = []  # type: List[str]
    rejected = []  # type: List[str]
    for pat in patterns:
        if not isinstance(pat, str) or not pat or len(pat) > MAX_PATTERN_LEN:
            rejected.append(pat if isinstance(pat, str) else repr(pat))
        else:
            accepted.append(pat.lower())
    return accepted, rejected


def glob_match(pattern: str, text: str) -> bool:
    """Whole-text match. '*' is any run of characters, '?' exactly one, all else literal.
    Iterative, no recursion and no re module: worst case O(len(text) * len(pattern))."""
    p = s = 0
    star = -1
    mark = 0
    while s < len(text):
        if p < len(pattern) and pattern[p] != "*" and (pattern[p] == "?" or pattern[p] == text[s]):
            p += 1
            s += 1
        elif p < len(pattern) and pattern[p] == "*":
            star = p
            mark = s
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            s = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def classify_touch(text: str, gate_patterns: List[str]) -> Optional[str]:
    t = text.strip() if isinstance(text, str) else ""
    if not t or t.startswith("[Request interrupted") or "<command-name>" in t:
        return None
    words = t.split()
    if words and words[0].lower().strip(".,!:;") in GATE_VERDICTS and len(words) <= 12:
        return GATE_VERDICT
    if len(t) <= MAX_GATE_TEXT:
        low = t.lower()
        for g in load_gate_patterns(gate_patterns)[0]:
            if glob_match(g, low):
                return GATE_VERDICT
    if _PERMISSION.fullmatch(t):
        return "permission_answer"
    if t.lower().rstrip(".!") in _APPROVAL:
        return "approval_word"
    if _CONTINUE.fullmatch(t):
        return "continue_restart"
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 3 and sum(1 for ln in lines if _SHELL_LINE.search(ln)) * 2 >= len(lines):
        return "pasted_shell_output"
    if len(t) <= MAX_RELAY_TEXT and _RELAY.search(t):
        return "relay"
    return None


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def is_finished_record(record: Dict[str, Any]) -> bool:
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return False
    for b in content:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "tool_use":
            inp = b.get("input")
            if b.get("name") == "Bash" and isinstance(inp, dict) \
                    and isinstance(inp.get("command"), str) and _COMMIT.search(inp["command"]):
                return True
            if any(_RECEIPT.search(s) for s in _strings(inp)):
                return True
        elif kind == "tool_result":
            if any(_RECEIPT.search(s) for s in _strings(b.get("content"))):
                return True
        elif kind == "text" and record.get("type") == "assistant":
            if isinstance(b.get("text"), str) and "## Close-out" in b["text"]:
                return True
    return False
