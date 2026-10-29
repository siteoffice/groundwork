"""Collector for the retro skill: find transcripts, digest each one, never keep raw content.

OpenAudit.open_text is the only place in this module that opens a file for reading.
"""
import io
import json
import posixpath
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, TextIO, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_scrub import Scrubber  # noqa: E402
from retro_touch import GATE_VERDICT, classify_touch, is_finished_record, load_gate_patterns  # noqa: E402

METRIC_VERSION = 1
KNOWN_TYPES = frozenset({
    "user", "assistant", "system", "attachment", "summary", "file-history-snapshot",
    "file-history-delta", "queue-operation", "custom-title", "agent-name", "mode",
    "permission-mode", "last-prompt", "tools-used", "api-request", "api-request-blob",
    "api-request-shape", "atis-latch"})

Source = NamedTuple("Source", [("kind", str), ("transcript", Path), ("meta", Optional[Path])])

_META_KEYS = ("sessionId", "cwd", "userSelectedFolders", "createdAt", "lastActivityAt", "model")
_META_NAME = re.compile(r"local_[^/\\]+\.json")
_SCHEDULED = re.compile(r"<scheduled-task[^>]*\bname=\"([^\"]+)\"")
_SLASH = re.compile(r"<command-name>(/[^<]+)</command-name>")
_MCP_FAIL = re.compile(r"MCP server \"?([A-Za-z0-9_.-]+)\"? failed")
_FINAL_ERR = re.compile(r"(?i)API Error|invalid_request_error|authentication_error|401 Unauthorized"
                        r"|403 Forbidden|OAuth token|Please run /login")
_SUCCESS = re.compile(r"(?i)\b(done|completed|succeeded|success|all tests passed|exit 0)\b")
_ERR_CATEGORIES = (
    ("permission_denied", re.compile(r"(?i)permission|denied|not allowed|doesn't want to proceed")),
    ("file_not_found", re.compile(r"(?i)no such file|not found|does not exist")),
    ("timeout", re.compile(r"(?i)timed? ?out")),
    ("exit_code", re.compile(r"(?i)exit code|exited with")),
)


class RefusedPath(Exception):
    pass


class OpenAudit:
    def __init__(self, log_path: Path) -> None:
        self.log_path = Path(log_path)

    def _log(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        f = io.FileIO(str(self.log_path), "a")
        try:
            f.write((line + "\n").encode("utf-8"))
        finally:
            f.close()

    def open_text(self, path: Path) -> TextIO:
        path = Path(path)
        resolved = path.resolve()
        shown = str(resolved)
        name = path.name
        reason = None
        if any(p.lower().startswith(".env") for p in path.parts + resolved.parts):
            reason = "env file"
        elif not ((path.suffix == ".jsonl" and name != "audit.jsonl") or _META_NAME.fullmatch(name)):
            reason = "not a transcript or metadata file"
        if reason:
            self._log("REFUSED\t%s\t%s" % (shown, reason))
            raise RefusedPath("%s: %s" % (shown, reason))
        self._log("OPENED\t%s" % shown)
        return path.open("r", encoding="utf-8", errors="replace")


def live_locations(home: Path, localappdata: Optional[Path], platform: str) -> Tuple[Path, List[Path], bool]:
    code = Path(home) / ".claude" / "projects"
    base = None
    unverified = False
    if platform.startswith("win"):
        base = Path(localappdata) / "Claude-Data" if localappdata else None
    elif platform == "darwin":
        base = Path(home) / "Library" / "Application Support" / "Claude-Data"
        unverified = True
    roots = sorted(base.glob("claude_*/local-agent-mode-sessions")) if base else []
    return code, roots, unverified


def fixture_locations(fixtures: Path) -> Tuple[Path, List[Path], Tuple[str, ...]]:
    fixtures = Path(fixtures)
    return (fixtures / "code", sorted(fixtures.glob("cowork/claude_*/local-agent-mode-sessions")),
            ("claude-projects",))


def discover_sources(code_projects: Path, cowork_roots: List[Path], since: Optional[datetime],
                     cowork_inner: Tuple[str, ...] = (".claude", "projects")) -> List[Source]:
    out = []  # type: List[Source]
    for t in sorted(Path(code_projects).glob("*/*.jsonl")):
        out.append(Source("code", t, None))
    for root in cowork_roots:
        for meta in sorted(Path(root).glob("*/*/local_*.json")):
            folder = meta.with_suffix("")
            for t in sorted(folder.joinpath(*cowork_inner).glob("*/*.jsonl")):
                out.append(Source("cowork", t, meta))
    if since is not None:
        cut = since.timestamp()
        kept = []
        for s in out:
            try:
                if s.transcript.stat().st_mtime >= cut:
                    kept.append(s)
            except OSError:
                continue
        out = kept
    return out


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _blocks(rec: Dict[str, Any]) -> List[Any]:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text(rec: Dict[str, Any]) -> str:
    return "\n".join(b["text"] for b in _blocks(rec)
                     if b.get("type") == "text" and isinstance(b.get("text"), str))


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _iso_ms(ms: Any) -> Optional[str]:
    if isinstance(ms, (int, float)) and not isinstance(ms, bool):
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _last_part(p: str) -> str:
    parts = [x for x in re.split(r"[\\/]+", p) if x]
    return parts[-1] if parts else ""


def _bump(d: Dict[str, int], key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


def _read_meta(src: Source, audit: OpenAudit, scr: Scrubber, sid: str) -> Dict[str, Any]:
    if src.meta is None:
        return {}
    with audit.open_text(src.meta) as fh:
        try:
            raw = json.load(fh)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    for s in _strings(raw):
        scr.scan(s, sid, "")
    return {k: raw[k] for k in _META_KEYS if k in raw}


def digest_session(src: Source, audit: OpenAudit, gate_patterns: List[str]) -> Dict[str, Any]:
    scr = Scrubber()
    sid = src.transcript.stem
    meta = _read_meta(src, audit, scr, sid)
    sid = str(meta.get("sessionId") or sid)
    recs = []  # type: List[Dict[str, Any]]
    malformed = 0
    with audit.open_text(src.transcript) as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except (ValueError, RecursionError):
                malformed += 1
                continue
            if not isinstance(rec, dict):
                malformed += 1
                continue
            recs.append(rec)
    if recs and isinstance(recs[0].get("sessionId"), str):
        sid = recs[0]["sessionId"]
    for rec in recs:
        ts = rec.get("timestamp")
        for s in _strings(rec):
            scr.scan(s, sid, ts[:10] if isinstance(ts, str) else "")

    meta_cwd = meta.get("cwd") if isinstance(meta.get("cwd"), str) else None
    cwd = next((r["cwd"] for r in recs if isinstance(r.get("cwd"), str)), None) or meta_cwd
    extra = meta.get("userSelectedFolders")
    folders = [cwd] if cwd else []
    if isinstance(extra, list):
        folders += [x for x in extra if isinstance(x, str)]
    base = (folders[-1] if src.kind == "cowork" else cwd) if folders else ""
    stamps = sorted(r["timestamp"] for r in recs if isinstance(r.get("timestamp"), str))
    start = stamps[0] if stamps else _iso_ms(meta.get("createdAt"))
    end = stamps[-1] if stamps else _iso_ms(meta.get("lastActivityAt"))
    model = next((r["message"]["model"] for r in recs if r.get("type") == "assistant"
                  and isinstance(r.get("message"), dict)
                  and isinstance(r["message"].get("model"), str)), None) or meta.get("model")
    day = (start or "")[:10]

    user_turns = interrupts = compactions = ask = out_tokens = perm = 0
    tool_errors = {}  # type: Dict[str, int]
    hook_errors = {}  # type: Dict[str, int]
    mcp_servers = {}  # type: Dict[str, int]
    skills = {}  # type: Dict[str, int]
    slashes = {}  # type: Dict[str, int]
    unknown = {}  # type: Dict[str, int]
    touches = {}  # type: Dict[str, int]
    gate_excluded = 0
    finished = False
    mcp_fail = set()
    first_user = None
    last_assistant = None
    entry = ""

    def hook_err(text: str) -> None:
        _bump(hook_errors, scr.scrub(text, sid, day)[:160])

    for rec in recs:
        typ = rec.get("type")
        if not entry and isinstance(rec.get("entrypoint"), str):
            entry = rec["entrypoint"]
        if not isinstance(typ, str):
            _bump(unknown, "non_string_type")
        elif typ not in KNOWN_TYPES:
            _bump(unknown, typ)
        finished = finished or is_finished_record(rec)
        he = rec.get("hookErrors")
        if isinstance(he, list):
            for h in he:
                if isinstance(h, str):
                    hook_err(h)
        if typ == "attachment" and isinstance(rec.get("attachment"), dict):
            att = rec["attachment"]
            at = str(att.get("type", "")).lower()
            if "hook" in at and "error" in at:
                t = att.get("stderr") or att.get("content")
                if isinstance(t, str):
                    hook_err(t)
        if typ == "system":
            if rec.get("subtype") == "compact_boundary":
                compactions += 1
            if isinstance(rec.get("content"), str):
                mcp_fail.update(_MCP_FAIL.findall(rec["content"]))
        elif typ == "user":
            for b in _blocks(rec):
                if b.get("type") == "tool_result" and b.get("is_error"):
                    t = _result_text(b.get("content"))
                    cat = next((c for c, rx in _ERR_CATEGORIES if rx.search(t)), "other")
                    _bump(tool_errors, cat)
                    perm += cat == "permission_denied"
            text = _text(rec)
            if any(b.get("type") == "text" for b in _blocks(rec)) and not rec.get("isMeta"):
                user_turns += 1
                if first_user is None:
                    first_user = text
                if text.startswith("[Request interrupted"):
                    interrupts += 1
                for c in _SLASH.findall(text):
                    _bump(slashes, c)
                injected = text.startswith(("<scheduled-task", "<system-reminder"))
                touch = None if injected else classify_touch(text, gate_patterns)
                if touch == GATE_VERDICT:
                    gate_excluded += 1
                elif touch:
                    _bump(touches, touch)
        elif typ == "assistant":
            last_assistant = rec
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            usage = msg.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
                out_tokens += usage["output_tokens"]
            for b in _blocks(rec):
                if b.get("type") != "tool_use":
                    continue
                name = b.get("name")
                parts = name.split("__") if isinstance(name, str) else []
                if len(parts) >= 3 and parts[0] == "mcp":
                    _bump(mcp_servers, parts[1])
                elif name == "Skill" and isinstance(b.get("input"), dict) \
                        and isinstance(b["input"].get("skill"), str):
                    _bump(skills, b["input"]["skill"])
                elif name == "AskUserQuestion":
                    ask += 1

    task = None
    m = _SCHEDULED.search(first_user or "")
    if m:
        task = m.group(1)
    final_error = None
    success = False
    if last_assistant is not None:
        at = _text(last_assistant)
        if last_assistant.get("isApiErrorMessage") is True or _FINAL_ERR.search(at):
            final_error = scr.scrub(at, sid, day)[:300]
        success = bool(_SUCCESS.search(at))

    digest = {
        "metric_version": METRIC_VERSION, "session_id": sid, "source": src.kind,
        "project": _last_part(base), "cwd": cwd, "folders": folders, "scheduled_task": task,
        "headless": entry.startswith("sdk") or task is not None, "model": model,
        "start": start, "end": end, "transcript_bytes": src.transcript.stat().st_size,
        "user_turns": user_turns, "tool_errors": tool_errors, "permission_denials": perm,
        "hook_errors": hook_errors, "mcp_servers": mcp_servers, "mcp_failures": sorted(mcp_fail),
        "interrupts": interrupts, "compactions": compactions, "skills": skills,
        "slash_commands": slashes, "ask_user_question": ask, "output_tokens": out_tokens,
        "unknown_record_types": unknown, "malformed_lines": malformed, "final_error": final_error,
        "success_line": success, "touches": touches, "gate_verdicts_excluded": gate_excluded, "finished": finished,
    }
    digest = scr.scrub_obj(digest, sid, day)
    digest["secret_findings"] = list(scr.findings)
    return digest


def write_digest(digest: Dict[str, Any], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(digest["session_id"])).lstrip(".") or "session"
    kind = re.sub(r"[^A-Za-z0-9._-]", "_", str(digest["source"])).lstrip(".") or "source"
    p = out_dir / ("%s-%s.json" % (kind, sid))
    p.write_text(json.dumps(digest, sort_keys=True, indent=1), encoding="utf-8")
    return p


def _norm(p: str) -> str:
    q = p.replace("\\", "/").casefold()
    return posixpath.normpath(q).rstrip("/") if q.strip() else ""


def is_under(path: str, folder: str) -> bool:
    p, f = _norm(path), _norm(folder)
    return p == f or p.startswith(f + "/")


def _route_index(digest: Dict[str, Any], worlds: List[Dict[str, str]]) -> Optional[int]:
    for i, world in enumerate(worlds):
        if any(is_under(f, world["folder"]) for f in digest.get("folders", []) if isinstance(f, str)):
            return i
    return None


def route(digest: Dict[str, Any], worlds: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    i = _route_index(digest, worlds)
    return None if i is None else worlds[i]


def _overlaps(a: Path, b: Path) -> bool:
    for x, y in ((a, b), (b, a)):
        try:
            x.relative_to(y)
            return True
        except ValueError:
            pass
    return False


def _check_worlds(worlds: List[Dict[str, str]], month_dir: Path, month: str) -> None:
    main = Path(month_dir).resolve()
    for world in worlds:
        if _norm(world["folder"]) in ("", ".", "/"):
            raise ValueError("a world folder is empty or the filesystem root, set a real folder")
        out = Path(world["report_dir"]).resolve()
        if _overlaps(out / month / "digests", main) or _overlaps(out, main):
            raise ValueError("the report location of the world at %s overlaps the main report "
                             "folder %s, choose a separate location" % (world["folder"], main))


def collect(sources: List[Source], month_dir: Path, month: str, worlds: List[Dict[str, str]],
            gate_patterns: List[str]) -> Dict[str, Any]:
    month_dir = Path(month_dir)
    _check_worlds(worlds, month_dir, month)
    audit = OpenAudit(month_dir / "opened.log")
    _, rejected = load_gate_patterns(gate_patterns)
    main = []  # type: List[Dict[str, Any]]
    held = [{"world": w, "digests": [], "transcript_bytes": 0} for w in worlds]
    main_bytes = 0
    for src in sources:
        digest = digest_session(src, audit, gate_patterns)
        i = _route_index(digest, worlds)
        if i is None:
            write_digest(digest, month_dir / "digests")
            main.append(digest)
            main_bytes += digest["transcript_bytes"]
            continue
        write_digest(digest, Path(worlds[i]["report_dir"]) / month / "digests")
        held[i]["digests"].append(digest)
        held[i]["transcript_bytes"] += digest["transcript_bytes"]
    return {"main": main, "worlds": held, "transcript_bytes": main_bytes,
            "rejected_gate_patterns": rejected}
