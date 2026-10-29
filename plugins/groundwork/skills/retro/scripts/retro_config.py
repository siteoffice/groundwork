"""Retro setup and config: ROOT discovery, the pointer file, config.json load, check and save.

The skill asks the setup questions; this script stores and checks the answers. `init` takes
everything as flags and never blocks: it prompts only for a missing root, period or ceiling,
and only when stdin is a terminal. It never writes secrets and prints a summary, not the config.

Gate patterns are case-insensitive globs: * is any run of characters, ? is one character, all
else is literal, matched against the whole text of a user turn (first 200 characters). A pattern
that is empty or longer than 80 characters is refused. The token ceiling is stored for R2 only.
"""
import argparse
import json
import math
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_digest import is_under  # noqa: E402
from retro_touch import load_gate_patterns  # noqa: E402

CONFIG_VERSION = 1
DEFAULTS = {"config_version": 1, "period_days": 30, "token_ceiling": None, "worlds": [], "gate_patterns": []}
MAX_PERIOD_DAYS = 3650


class ConfigError(Exception):
    pass


def _abs(p: Any) -> Path:
    return Path(os.path.abspath(str(p)))


def find_groundwork_root(start: Path) -> Optional[Path]:
    """The nearest folder at or above start holding siteoffice.json or a flows folder."""
    here = _abs(start)
    for d in [here] + list(here.parents):
        try:
            if (d / "siteoffice.json").is_file() or (d / "flows").is_dir():
                return d
        except OSError:
            continue
    return None


def _pointer(home: Path) -> Path:
    return Path(home) / ".groundwork" / "retro.json"


def check_root(root: Any) -> Optional[str]:
    """None when the retro ROOT is usable, else the reason it is refused."""
    try:
        r = _abs(root)
    except (ValueError, TypeError):
        return "the retro folder path is not valid"
    if not str(root).strip():
        return "the retro folder path is empty"
    if r.parent == r:
        return "the retro folder is a drive or filesystem root, choose a real folder: %s" % r
    if any(part.lower().startswith(".env") for part in re.split(r"[\\/]+", str(r))):
        return "the retro folder has a component starting with .env, which is never read: %s" % r
    return None


def resolve_root(start: Path, home: Path) -> Tuple[Path, str]:
    """A usable groundwork root, else a usable pointer target, else home/Documents.
    A found root or pointer that is not absolute, not text, or fails check_root is skipped."""
    found = find_groundwork_root(start)
    if found is not None and check_root(found) is None:
        return found, "groundwork"
    try:
        data = json.loads(_pointer(home).read_text(encoding="utf-8"))
        root = data.get("root") if isinstance(data, dict) else None
    except (OSError, ValueError, RecursionError):
        root = None
    if isinstance(root, str) and root.strip() and Path(root).is_absolute() and check_root(root) is None:
        return Path(root), "pointer"
    return Path(home) / "Documents", "default"

def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def write_pointer(home: Path, root: Path) -> Path:
    return _write_json(_pointer(home), {"root": str(root)})


def _config_path(root: Path) -> Path:
    return Path(root) / "retro" / "config.json"


def load_config(root: Path) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = _config_path(root)
    if not path.is_file():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ConfigError("retro config at %s is not valid JSON (%s)" % (path, type(exc).__name__))
    if not isinstance(data, dict):
        raise ConfigError("retro config at %s must be a JSON object" % path)
    cfg.update(data)
    return cfg


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_DRIVE_STYLE = re.compile(r"^/[A-Za-z](/|$)")


def _posix_rooted(p: str) -> bool:
    return p.startswith("/")


def _checked(p: Any, label: str, folder: bool = False) -> Tuple[str, Optional[str]]:
    """Normalise first (so ".." cannot sneak past), then check the result. Returns (path, problem).
    A world folder is only compared with transcript cwd text, so a POSIX-rooted folder read on
    Windows is kept POSIX, never given a drive. report_dir must be a real absolute path."""
    if not isinstance(p, str) or not p.strip():
        return "", "%s is missing or empty" % label
    posix = folder and _posix_rooted(p) and not Path(p).is_absolute()
    if not posix and not Path(p).is_absolute():
        return "", "%s must be an absolute path, got %s" % (label, p)
    norm = posixpath.normpath(p) if posix else os.path.normpath(p)
    if posix:
        if norm == "/":
            return "", "%s is the filesystem root once cleaned, choose a real folder: %s" % (label, p)
        if _DRIVE_STYLE.match(norm):
            return "", "%s %s looks like a drive path, use the Windows form such as C:\\Users\\name" % (label, p)
    elif Path(norm).parent == Path(norm):
        return "", "%s is a drive or filesystem root once cleaned, choose a real folder: %s" % (label, p)
    if any(part.lower().startswith(".env") for part in re.split(r"[\\/]+", norm)):
        return "", "%s has a component starting with .env, which is never read: %s" % (label, p)
    return norm, None


def normalise_worlds(worlds: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [{"folder": _checked(w["folder"], "folder", True)[0], "report_dir": _checked(w["report_dir"], "report_dir")[0]}
            for w in worlds]


def ceiling_problem(cfg: Dict[str, Any]) -> Optional[str]:
    ceil = cfg.get("token_ceiling")
    if ceil is not None and (not _is_int(ceil) or ceil <= 0):
        return "token_ceiling must be empty or a whole number above 0, got %r" % (ceil,)
    return None


def validate_config(cfg: Dict[str, Any], root: Path) -> List[str]:
    problems = []  # type: List[str]
    days = cfg.get("period_days")
    if not _is_int(days) or not 1 <= days <= MAX_PERIOD_DAYS:
        problems.append("period_days must be a whole number from 1 to %d, got %r" % (MAX_PERIOD_DAYS, days))
    cp = ceiling_problem(cfg)
    if cp:
        problems.append(cp)
    retro = str(_abs(Path(root) / "retro"))
    worlds = cfg.get("worlds")
    clean = []  # type: List[Tuple[int, str, str]]
    if not isinstance(worlds, list):
        problems.append("worlds must be a list")
        worlds = []
    for i, w in enumerate(worlds):
        if not isinstance(w, dict):
            problems.append("world %d must have a folder and a report_dir" % (i + 1))
            continue
        folder, ferr = _checked(w.get("folder"), "world %d folder" % (i + 1), folder=True)
        out, oerr = _checked(w.get("report_dir"), "world %d report_dir" % (i + 1))
        problems += [e for e in (ferr, oerr) if e]
        if ferr or oerr:
            continue
        if is_under(out, retro):
            problems.append("world %d report_dir %s is inside the main retro folder %s" % (i + 1, out, retro))
        if is_under(out, folder):
            problems.append("world %d report_dir %s is inside its own folder %s" % (i + 1, out, folder))
        clean.append((i + 1, folder, out))
    for n, folder, out in clean:
        for m, other_folder, other_out in clean:
            if m == n:
                continue
            if m > n and is_under(out, other_out) and is_under(other_out, out):
                problems.append("worlds %d and %d share the report_dir %s" % (n, m, out))
            if m > n and (is_under(folder, other_folder) or is_under(other_folder, folder)):
                problems.append("world folders %d and %d are nested, keep them apart: %s and %s"
                                % (n, m, folder, other_folder))
            if is_under(out, other_folder):
                problems.append("world %d report_dir %s is inside the folder of world %d" % (n, out, m))
    pats = cfg.get("gate_patterns")
    if not isinstance(pats, list):
        problems.append("gate_patterns must be a list")
    else:
        _, rejected = load_gate_patterns(pats)
        for r in rejected:
            problems.append("gate pattern refused (empty, not text, or over 80 characters): %s" % str(r)[:40])
    return problems

def save_config(root: Path, cfg: Dict[str, Any]) -> Path:
    return _write_json(_config_path(root), cfg)


def _ask(label: str, default: str) -> str:
    if not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        answer = input("%s [%s]: " % (label, default)).strip()
    except EOFError:
        return default
    return answer or default


def _parse_int(text: str, label: str, problems: List[str]) -> Optional[int]:
    try:
        return int(text.strip())
    except ValueError:
        problems.append("%s must be a whole number, got %r" % (label, text))
        return None


def _cmd_init(args: argparse.Namespace) -> int:
    home = Path(args.home) if args.home else Path.home()
    problems = []  # type: List[str]
    if args.root:
        root = _abs(args.root)
    else:
        default_root, _ = resolve_root(Path.cwd(), home)
        root = _abs(_ask("Folder for retro (ROOT)", str(default_root)))
    bad_root = check_root(root)
    if bad_root:
        print("problem: %s" % bad_root)
        print("nothing saved")
        return 2
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        print(str(exc))
        return 2
    existed = _config_path(root).is_file()
    old_counts = {k: len(cfg.get(k) or []) if isinstance(cfg.get(k), list) else 0 for k in ("worlds", "gate_patterns")}
    period = args.period
    if period is None and not os.path.exists(str(_config_path(root))):
        period = _ask("Default period in days", str(DEFAULTS["period_days"]))
    if period is not None:
        v = _parse_int(period, "--period", problems)
        if v is not None:
            cfg["period_days"] = v
    ceiling = args.ceiling
    if ceiling is None and not os.path.exists(str(_config_path(root))):
        ceiling = _ask("Token ceiling for the deep pass (R2 only, empty for none)", "")
    if ceiling is not None and ceiling.strip():
        v = _parse_int(ceiling, "--ceiling", problems)
        if v is not None:
            cfg["token_ceiling"] = v
    if args.world:
        worlds = []
        for w in args.world:
            folder, sep, out = w.partition("=")
            if not sep:
                problems.append("--world needs FOLDER=REPORTDIR, got %r" % w)
                continue
            worlds.append({"folder": folder.strip(), "report_dir": out.strip()})
        cfg["worlds"] = worlds
    if args.gate_pattern:
        cfg["gate_patterns"] = list(args.gate_pattern)
    problems += validate_config(cfg, root) if not problems else []
    if problems:
        for p in problems:
            print("problem: %s" % p)
        print("nothing saved")
        return 2
    cfg["config_version"] = CONFIG_VERSION
    cfg["gate_patterns"], _ = load_gate_patterns(cfg["gate_patterns"])
    cfg["worlds"] = normalise_worlds(cfg["worlds"])
    path = save_config(root, cfg)
    if find_groundwork_root(root) is None:
        write_pointer(home, root)
    print("%s %s" % ("updated" if existed else "created", path))
    if existed:
        for name, flag in (("worlds", args.world), ("gate_patterns", args.gate_pattern)):
            if flag:
                print("replaced %s: %d earlier entries replaced by %d" % (name, old_counts[name], len(cfg[name])))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    bad_root = check_root(args.root)
    if bad_root:
        print(bad_root)
        return 2
    try:
        cfg = load_config(Path(args.root))
    except ConfigError as exc:
        print(str(exc))
        return 2
    print("period_days: %s" % cfg.get("period_days"))
    print("token_ceiling: %s (R2 only)" % cfg.get("token_ceiling"))
    print("worlds: %d" % len(cfg.get("worlds") or []))
    print("gate_patterns: %d" % len(cfg.get("gate_patterns") or []))
    return 0


def _cmd_where(args: argparse.Namespace) -> int:
    root, how = resolve_root(Path(args.start) if args.start else Path.cwd(),
                             Path(args.home) if args.home else Path.home())
    print("%s\t%s" % (root, how))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="validate and store the retro config")
    i.add_argument("--root")
    i.add_argument("--home", help="home folder for the pointer file, default the real home")
    i.add_argument("--period")
    i.add_argument("--ceiling")
    i.add_argument("--world", action="append", default=[], metavar="FOLDER=REPORTDIR")
    i.add_argument("--gate-pattern", action="append", default=[], metavar="GLOB")
    s = sub.add_parser("show", help="print a summary of the stored config")
    s.add_argument("--root", required=True)
    w = sub.add_parser("where", help="print the retro root and how it was found")
    w.add_argument("--start")
    w.add_argument("--home")
    args = ap.parse_args(argv)
    return {"init": _cmd_init, "show": _cmd_show, "where": _cmd_where}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())