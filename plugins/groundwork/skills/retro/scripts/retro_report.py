"""Retro report: silent failures, headline, metrics and the Markdown and HTML report.

Counts only, no agent analysis. The report takes main digests only; a separate world
contributes one line (its count and configured report folder) and gets its own files.
"""
import argparse
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_config import ConfigError, ceiling_problem, check_root, load_config, merge_worlds, normalise_worlds, validate_config  # noqa: E402
from retro_digest import METRIC_VERSION, collect, discover_sources, fixture_locations, live_locations  # noqa: E402
from retro_estimate import RATES_AS_OF, estimate, format_estimate  # noqa: E402
from retro_scrub import FIX_TEXT, Scrubber  # noqa: E402

HOOK_MIN_SESSIONS = 3
MCP_MIN_SESSIONS = 3
COUNTS_ONLY_LINE = "**counts only**: script counts, no agent analysis. The deep pass arrives in R2."
LIVE_TEST_LINE = "live test SKIPPED (not armed): structural pass is necessary-but-not-sufficient"
LIVE_ARMED_LINE = "live test armed (GROUNDWORK_RETRO_LIVE=1): its result is in the pytest output, not in this report"
FINISHED_UNIT = "finished piece of work"


def find_silent_failures(digests: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    final_errors = []  # type: List[Dict[str, Any]]
    no_success = []  # type: List[Dict[str, Any]]
    hooks = {}  # type: Dict[str, int]
    mcp = {}  # type: Dict[str, int]
    for d in digests:
        sid = d.get("session_id")
        task = d.get("scheduled_task")
        if d.get("headless") and d.get("final_error"):
            final_errors.append({"session": sid, "date": (d.get("start") or "")[:10],
                                 "scheduled_task": task, "snippet": d["final_error"]})
        if task and not d.get("success_line"):
            no_success.append({"session": sid, "scheduled_task": task,
                               "date": (d.get("start") or "")[:10]})
        for text in (d.get("hook_errors") or {}):
            hooks[text] = hooks.get(text, 0) + 1
        for server in set(d.get("mcp_failures") or []):
            mcp[server] = mcp.get(server, 0) + 1
    return {
        "final_errors": final_errors,
        "hooks": [{"text": t, "sessions": n}
                  for t, n in sorted(hooks.items(), key=lambda kv: (-kv[1], kv[0]))
                  if n >= HOOK_MIN_SESSIONS],
        "mcp": [{"server": s, "sessions": n}
                for s, n in sorted(mcp.items(), key=lambda kv: (-kv[1], kv[0]))
                if n >= MCP_MIN_SESSIONS],
        "no_success_line": no_success,
    }


def _utc(now: datetime) -> datetime:
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def month_of(now: datetime) -> str:
    return _utc(now).strftime("%Y-%m")


def previous_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return "%04d-%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_previous(retro_dir: Path, month: str) -> Tuple[Optional[Dict[str, Any]], str]:
    p = Path(retro_dir) / previous_month(month) / "metrics.json"
    if not p.is_file():
        return None, "missing"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None, "corrupt"
    ver = data.get("metric_version") if isinstance(data, dict) else None
    if not isinstance(ver, int) or isinstance(ver, bool):
        return None, "corrupt"
    if ver != METRIC_VERSION:
        return data, "version"
    try:
        ok = _is_num(data.get("headline")) and math.isfinite(float(data["headline"]))
    except OverflowError:
        ok = False
    return (data, "ok") if ok else (None, "corrupt")


def _add_counts(into: Dict[str, int], src: Any) -> None:
    if isinstance(src, dict):
        for k, n in src.items():
            if isinstance(n, int) and not isinstance(n, bool):
                into[k] = into.get(k, 0) + n


def headline(digests: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_class = {}  # type: Dict[str, int]
    for d in digests:
        _add_counts(by_class, d.get("touches"))
    touches = sum(by_class.values())
    sessions = len(digests)
    finished = sum(1 for d in digests if d.get("finished"))
    if finished:
        value, unit = touches / finished, FINISHED_UNIT
    elif sessions:
        value, unit = touches / sessions, "session"
    else:
        value, unit = 0.0, "session"
    return {"sessions": sessions, "touches": touches, "touches_by_class": by_class,
            "finished": finished, "unit": unit, "headline": round(value, 2)}


def _failure_total(sf: Dict[str, List[Dict[str, Any]]]) -> int:
    return sum(len(v) for v in sf.values())


def _findings(digests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [f for d in digests for f in (d.get("secret_findings") or []) if isinstance(f, dict)]


def build_metrics(digests: List[Dict[str, Any]], month: str) -> Dict[str, Any]:
    m = {"metric_version": METRIC_VERSION, "month": month}
    m.update(headline(digests))
    m["silent_failures"] = _failure_total(find_silent_failures(digests))
    m["secret_exposures"] = len(_findings(digests))
    return m


def installed_skills(dirs: List[Path]) -> List[str]:
    names = set()
    for d in dirs:
        try:
            children = sorted(Path(d).iterdir())
        except OSError:
            continue
        for c in children:
            try:
                if c.is_dir() and (c / "SKILL.md").is_file():
                    names.add(c.name)
            except OSError:
                continue
    return sorted(names)


def quote_snippet(text: str, scrubber: Scrubber, session: str, date: str, max_len: int = 160) -> str:
    return re.sub(r"[`\s]", " ", scrubber.scrub(str(text), session, date))[:max_len]


def _sorted_counts(d: Dict[str, int]) -> List[Tuple[str, int]]:
    return sorted(((k, n) for k, n in d.items() if n > 0), key=lambda kv: (-kv[1], kv[0]))


def _signed(x: float) -> str:
    return ("+" if x >= 0 else "") + str(round(x, 2))


def build_model(digests: List[Dict[str, Any]], month: str, previous: Tuple[Optional[Dict[str, Any]], str],
                skill_dirs: List[Path], est: Optional[Dict[str, Any]], assumptions: str,
                rejected: List[str], worlds_line: Optional[str], platform: str) -> Dict[str, Any]:
    scr = Scrubber()

    def clean(t: Any, n: int = 120) -> str:
        return quote_snippet(t, scr, "", "", n).replace("[", "\\[").replace("]", "\\]")

    metrics = build_metrics(digests, month)
    by_project = {}  # type: Dict[str, int]
    errors = {}  # type: Dict[str, int]
    hooks = {}  # type: Dict[str, List[int]]
    mcp_used = {}  # type: Dict[str, int]
    mcp_failed = {}  # type: Dict[str, int]
    used = {}  # type: Dict[str, int]
    totals = {"permission_denials": 0, "interrupts": 0, "compactions": 0}
    for d in digests:
        n = sum(v for v in (d.get("touches") or {}).values() if isinstance(v, int))
        if n:
            p = clean(d.get("project") or "(unknown)")
            by_project[p] = by_project.get(p, 0) + n
        _add_counts(errors, d.get("tool_errors"))
        for k in totals:
            totals[k] += d.get(k) if isinstance(d.get(k), int) else 0
        for text, c in (d.get("hook_errors") or {}).items():
            h = hooks.setdefault(clean(text, 160), [0, 0])
            h[0] += 1
            h[1] += c if isinstance(c, int) else 1
        for s in (d.get("mcp_servers") or {}):
            mcp_used[clean(s)] = mcp_used.get(clean(s), 0) + 1
        for s in set(d.get("mcp_failures") or []):
            mcp_failed[clean(s)] = mcp_failed.get(clean(s), 0) + 1
        for name, c in (d.get("skills") or {}).items():
            short = clean(str(name).rsplit(":", 1)[-1])
            used[short] = used.get(short, 0) + (c if isinstance(c, int) else 1)
    installed = installed_skills(skill_dirs)
    return {
        "month": month, "metrics": metrics, "previous": previous, "platform": platform,
        "by_project": by_project, "tool_errors": errors, "totals": totals,
        "silent": find_silent_failures(digests), "findings": _findings(digests),
        "hooks": hooks, "mcp_used": mcp_used, "mcp_failed": mcp_failed,
        "skills_used": used, "skills_unused": [clean(s) for s in installed if clean(s) not in used],
        "skills_installed": len(installed), "est": est, "assumptions": assumptions,
        "rejected": [clean(r, 80) for r in rejected], "worlds_line": worlds_line,
        "clean": clean,
    }


def _trend(model: Dict[str, Any]) -> str:
    prev, status = model["previous"]
    m = model["metrics"]
    pm = previous_month(model["month"])
    if status == "missing":
        return "no trend: no metrics.json for %s" % pm
    if status == "corrupt":
        return "no trend: previous metrics.json unreadable"
    if status == "version":
        return "metric definition changed (v%s to v%d), not comparable" % (prev["metric_version"], METRIC_VERSION)
    note = ""
    if prev.get("unit") in ("session", FINISHED_UNIT) and prev["unit"] != m["unit"]:
        note = " The unit changed from per %s, compare with care." % prev["unit"]
    return "Trend: %s touches per %s, was %s in %s (%s).%s" % (
        m["headline"], m["unit"], round(float(prev["headline"]), 2), pm,
        _signed(m["headline"] - float(prev["headline"])), note)


def _limitations(model: Dict[str, Any]) -> List[str]:
    return [
        "Counts only: script counts, no agent analysis, no judgment of quality.",
        "Touch heuristics are metric_version %d; a touch is a guess from the shape of a user turn." % METRIC_VERSION,
        "The estimate rates are assumptions as of %s, not live prices. %s" % (RATES_AS_OF, model["assumptions"]),
        "opened.log also lists world transcript paths (paths only, never content).",
        "The file audit refuses any path with a component starting \".env\", even above the scan root. A user whose "
        "home path has such a component gets zero digests, which shows here as zero sessions; opened.log then "
        "holds REFUSED lines.",
        "The macOS path for Cowork transcripts is unverified%s." % (
            " (this run used it)" if model["platform"] == "darwin" else ""),
        LIVE_TEST_LINE if os.environ.get("GROUNDWORK_RETRO_LIVE") != "1" else LIVE_ARMED_LINE,
    ]


def _sections(model: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The report as (kind, text) blocks: title, marker, h2, p, li, quote. Both renderers walk this."""
    clean = model["clean"]
    m = model["metrics"]
    sf = model["silent"]
    out = [("title", "Retro %s" % model["month"]), ("marker", COUNTS_ONLY_LINE)]

    def h2(t: str) -> None:
        out.append(("h2", t))

    def none(t: str = "None.") -> None:
        out.append(("p", t))

    h2("Headline and trend")
    out.append(("p", "%s touches per %s (%d touches, %d sessions, %d finished)."
                % (m["headline"], m["unit"], m["touches"], m["sessions"], m["finished"])))
    out.append(("p", _trend(model)))
    if model["rejected"]:
        out.append(("p", "%d declared gate patterns refused as unsafe: %s"
                    % (len(model["rejected"]), "; ".join('"%s"' % r for r in model["rejected"]))))

    h2("Silent failures")
    if not _failure_total(sf):
        none()
    for f in sf["final_errors"]:
        task = (", scheduled task %s" % clean(f["scheduled_task"])) if f["scheduled_task"] else ""
        out.append(("li", "Final API error in headless session %s, %s%s:" % (clean(f["session"]), f["date"], task)))
        out.append(("quote", clean(f["snippet"], 160)))
    for f in sf["no_success_line"]:
        out.append(("li", "Scheduled task %s (session %s, %s) ended with no success line."
                    % (clean(f["scheduled_task"]), clean(f["session"]), f["date"])))
    for f in sf["hooks"]:
        out.append(("li", "Hook error repeated in %d sessions:" % f["sessions"]))
        out.append(("quote", clean(f["text"], 160)))
    for f in sf["mcp"]:
        out.append(("li", "MCP server %s failed in %d sessions." % (clean(f["server"]), f["sessions"])))

    h2("Top friction")
    classes = _sorted_counts(m["touches_by_class"])
    projects = _sorted_counts(model["by_project"])
    if not classes:
        none("No touches recorded.")
    else:
        out.append(("p", "Touches by class, highest first."))
        out += [("li", "%s: %d" % (clean(k), n)) for k, n in classes]
        out.append(("p", "Touches by project, highest first."))
        out += [("li", "%s: %d" % (k, n)) for k, n in projects]
    t = model["totals"]
    out.append(("p", "Permission denials %d, interrupts %d, compactions %d."
                % (t["permission_denials"], t["interrupts"], t["compactions"])))
    errs = _sorted_counts(model["tool_errors"])
    if errs:
        out.append(("p", "Tool errors by category: %s." % ", ".join("%s %d" % (clean(k), n) for k, n in errs)))

    h2("Secret exposures")
    if not model["findings"]:
        none("No secret-shaped text found.")
    for f in model["findings"]:
        out.append(("li", "%s, %s, %s. Fix: %s" % (clean(f.get("session", "")), clean(f.get("date", "")),
                                                   clean(f.get("kind", "")), FIX_TEXT)))

    h2("Hook and MCP health")
    if not model["hooks"]:
        out.append(("p", "No hook errors recorded."))
    for text, (sessions, occ) in sorted(model["hooks"].items(), key=lambda kv: (-kv[1][0], kv[0])):
        out.append(("li", "Hook error in %d sessions (%d occurrences):" % (sessions, occ)))
        out.append(("quote", text))
    servers = sorted(set(model["mcp_used"]) | set(model["mcp_failed"]))
    if not servers:
        out.append(("p", "No MCP servers used."))
    for s in servers:
        out.append(("li", "MCP server %s: used in %d sessions, failed in %d sessions."
                    % (s, model["mcp_used"].get(s, 0), model["mcp_failed"].get(s, 0))))

    h2("Skills used and unused")
    used = _sorted_counts(model["skills_used"])
    out.append(("li", "Used: %s." % (", ".join("%s (%d)" % kv for kv in used) or "none")))
    out.append(("li", "Installed and unused: %s." % (", ".join(model["skills_unused"]) or "none")))
    out.append(("li", "Installed skills found: %d." % model["skills_installed"]))

    h2("Estimate for the deep pass")
    if model["est"] is None:
        out.append(("p", "Not estimated here: the estimate covers the main report only."))
    else:
        lines = format_estimate(model["est"])
        out.append(("p", lines[0]))
        out += [("li", ln[2:] if ln.startswith("- ") else ln) for ln in lines[1:]]

    if model["worlds_line"]:
        h2("Separate worlds")
        out.append(("p", model["worlds_line"]))

    h2("Limitations")
    out += [("li", x) for x in _limitations(model)]
    return out


def render_md(model: Dict[str, Any]) -> str:
    lines = []  # type: List[str]
    prev = None
    for kind, text in _sections(model):
        if prev is not None and (kind != prev or kind in ("title", "h2", "p", "marker")):
            lines.append("")
        lines.append({"title": "# ", "h2": "## ", "li": "- ", "quote": "> "}.get(kind, "") + text)
        prev = kind
    return "\n".join(lines) + "\n"


_CSS = ("body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 16px;"
        "background:#fff;color:#1a1a1a}blockquote{margin:.4rem 0 .4rem 1rem;padding-left:.8rem;"
        "border-left:3px solid #999;color:#444}"
        "@media(prefers-color-scheme:dark){body{background:#161616;color:#e6e6e6}blockquote{color:#bbb}}")


def render_html(model: Dict[str, Any]) -> str:
    e = html.escape
    parts = []  # type: List[str]
    in_ul = False
    for kind, text in _sections(model):
        if in_ul and kind != "li":
            parts.append("</ul>")
            in_ul = False
        if kind == "li" and not in_ul:
            parts.append("<ul>")
            in_ul = True
        if kind == "title":
            parts.append("<h1>%s</h1>" % e(text))
        elif kind == "h2":
            parts.append("<h2>%s</h2>" % e(text))
        elif kind == "li":
            parts.append("<li>%s</li>" % e(text))
        elif kind == "quote":
            parts.append("<blockquote>%s</blockquote>" % e(text))
        elif kind == "marker":
            parts.append("<p>%s</p>" % re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e(text)))
        else:
            parts.append("<p>%s</p>" % e(text))
    if in_ul:
        parts.append("</ul>")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body>\n%s\n</body></html>\n"
            % (e("Retro %s" % model["month"]), _CSS, "\n".join(parts)))


def _write_report(out_dir: Path, model: Dict[str, Any]) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"report_md": out_dir / "report.md", "report_html": out_dir / "report.html",
             "metrics": out_dir / "metrics.json"}
    paths["report_md"].write_text(render_md(model), encoding="utf-8")
    paths["report_html"].write_text(render_html(model), encoding="utf-8")
    paths["metrics"].write_text(json.dumps(model["metrics"], indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return paths


def run_retro(root: Path, now: datetime, fixtures: Optional[Path] = None, home: Optional[Path] = None,
              localappdata: Optional[Path] = None, platform: str = sys.platform, days: Optional[int] = 30,
              worlds: Optional[List[Dict[str, str]]] = None, gate_patterns: Optional[List[str]] = None,
              config: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    root = Path(root)
    worlds = list(worlds or [])
    gate_patterns = list(gate_patterns or [])
    config = config if isinstance(config, dict) else {}
    now = _utc(now)
    month = month_of(now)
    retro_dir = root / "retro"
    month_dir = retro_dir / month
    if fixtures is not None:
        code, cowork, inner = fixture_locations(Path(fixtures))
        since = None
        skill_dirs = [Path(fixtures) / "skills"]
    else:
        home = Path(home) if home else Path.home()
        if localappdata is None and os.environ.get("LOCALAPPDATA"):
            localappdata = Path(os.environ["LOCALAPPDATA"])
        code, cowork, _ = live_locations(home, localappdata, platform)
        inner = (".claude", "projects")
        since = now - timedelta(days=days) if days else None
        skill_dirs = [home / ".claude" / "skills"] + sorted(home.glob(".claude/plugins/cache/*/*/*/skills"))
    res = collect(discover_sources(code, cowork, since, inner), month_dir, month, worlds, gate_patterns)
    main = res["main"]
    digest_bytes = sum(len(json.dumps(d, sort_keys=True, indent=1).encode("utf-8")) for d in main)
    est = estimate(len(main), res["transcript_bytes"], digest_bytes, config)
    analysed = [h for h in res["worlds"] if h["digests"]]
    seen = []  # type: List[str]
    for h in analysed:
        if h["world"]["report_dir"] not in seen:
            seen.append(h["world"]["report_dir"])
    worlds_line = ("%d separate worlds analysed, report in %s" % (len(analysed), "; ".join(seen))
                   if analysed else None)
    common = dict(skill_dirs=skill_dirs, assumptions=est["assumptions"], rejected=res["rejected_gate_patterns"],
                  platform=platform)
    model = build_model(main, month, load_previous(retro_dir, month), est=est, worlds_line=worlds_line, **common)
    paths = _write_report(month_dir, model)
    for h in analysed:
        out = Path(h["world"]["report_dir"])
        wm = build_model(h["digests"], month, load_previous(out, month), est=None, worlds_line=None, **common)
        _write_report(out / month, wm)
    paths["month_dir"] = month_dir
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Write the retro report, report.html and metrics.json.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--fixtures", help="read transcripts from this fixture folder instead of the live ones")
    ap.add_argument("--days", type=int, help="period in days, default from retro/config.json or 30")
    ap.add_argument("--world", action="append", default=[], metavar="FOLDER=REPORTDIR")
    ap.add_argument("--gate-pattern", action="append", default=[], metavar="GLOB")
    ap.add_argument("--now", help="YYYY-MM-DD, default today (UTC)")
    args = ap.parse_args(argv)
    if args.days is not None and not 1 <= args.days <= 3650:
        ap.error("--days must be between 1 and 3650")
    root = Path(args.root)
    bad_root = check_root(root)
    if bad_root:
        print("retro config error: %s" % bad_root, file=sys.stderr)
        return 2
    try:
        config = load_config(root)
    except ConfigError as exc:
        print("retro config error: %s" % exc, file=sys.stderr)
        return 2
    cp = ceiling_problem(config)
    if cp:  # the ceiling is stored for R2 only, so a bad one must not stop the monthly scan
        print("token ceiling in config.json is invalid, ignoring it: %s" % cp, file=sys.stderr)
        config["token_ceiling"] = None
    worlds = []
    for w in args.world:
        folder, sep, out = w.partition("=")
        if not sep or not folder.strip() or not out.strip():
            ap.error("--world needs FOLDER=REPORTDIR, got %r" % w)
        worlds.append({"folder": folder.strip(), "report_dir": out.strip()})
    # CLI flags win; the CLI values then pass the same checks as the config, with the config worlds.
    if isinstance(config["worlds"], list):
        config["worlds"] = merge_worlds(worlds, config["worlds"])
    config["gate_patterns"] = args.gate_pattern or config["gate_patterns"]
    problems = validate_config(config, root)
    if problems:
        print("retro config error: %s" % "; ".join(problems), file=sys.stderr)
        return 2
    config["worlds"] = normalise_worlds(config["worlds"])
    if args.now:
        try:
            now = datetime.strptime(args.now, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            ap.error("--now needs YYYY-MM-DD")
    else:
        now = datetime.now(timezone.utc)
    out = run_retro(root, now, fixtures=Path(args.fixtures) if args.fixtures else None,
                    days=args.days if args.days is not None else config["period_days"],
                    worlds=config["worlds"], gate_patterns=config["gate_patterns"],
                    config=config)
    for key in ("report_md", "report_html", "metrics"):
        print(out[key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
