#!/usr/bin/env python3
"""siteoffice-checkup: weekly read-only health check of a Siteoffice tree.

Checks skills, plugins, instruction files and folders. Reads only. Writes nothing unless
--out is given, and then exactly one report file.

Runs anywhere Python 3.8+ runs: the Cowork sandbox (bash) and the laptop (PowerShell).

  python3 checkup.py --root /sessions/<id>/mnt/ZND            (Cowork sandbox)
  python  checkup.py --root C:\\siteoffice\\acme                (laptop)

--win-root is the Windows spelling of the same root (default: the value of --root). Paths
written in prose under that root are mapped onto --root so the existence checks work inside
the sandbox too.

Exit code: 0 when nothing failed (warnings allowed), 1 when at least one check failed,
2 when the root itself is unusable.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

EM_DASH = "\u2014"
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".worktrees",
             "_archive", "graphify-out", "_graphify", "dist", ".pytest_cache", "_dumps"}
INSTRUCTION_NAMES = {"CLAUDE.md", "AGENTS.md"}

results = []  # (area, check, level, message)


def add(area, check, level, message):
    results.append((area, check, level, message))


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def walk(root, max_depth):
    base = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root):
        depth = cur.count(os.sep) - base
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if depth >= max_depth:
            dirs[:] = []
        yield cur, dirs, files, depth


class PathMap:
    def __init__(self, root, win_root):
        self.root = os.path.abspath(root)
        self.win = win_root.rstrip("\\").lower()

    def to_local(self, win_path):
        p = win_path.rstrip(".,;:)`'\"")
        if not p.lower().startswith(self.win):
            return None
        rest = p[len(self.win):].lstrip("\\/")
        if any(ch in rest for ch in "<>*?|{}$%"):
            return None
        return os.path.join(self.root, *[x for x in re.split(r"[\\/]", rest) if x])


def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    fm, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km and not line.startswith(" "):
            key = km.group(1)
            fm[key] = km.group(2).strip()
        elif key and line.startswith((" ", "\t")):
            fm[key] = (fm[key] + " " + line.strip()).strip()
    for k, v in fm.items():
        if v[:2] in (">-", "|-") or v[:1] in (">", "|"):
            v = v.lstrip(">|-").strip()
        fm[k] = v.strip('"').strip("'")
    return fm


# --------------------------------------------------------------------------- skills
def check_skills(skills_repo):
    skills_dir = os.path.join(skills_repo, "skills")
    routing_path = os.path.join(skills_repo, "routing.json")
    if not os.path.isdir(skills_dir):
        add("skills", "S0", "fail", f"no skills folder at {skills_dir}")
        return
    try:
        routing = json.load(open(routing_path, encoding="utf-8"))
        routed = {k for k in routing.get("skills", {}) if not k.startswith("_")}
    except Exception as exc:  # noqa: BLE001
        add("skills", "S4", "fail", f"routing.json unreadable: {exc}")
        routed = set()
    names = sorted(d for d in os.listdir(skills_dir)
                   if os.path.isdir(os.path.join(skills_dir, d)) and not d.startswith((".", "_")))
    for n in sorted(set(names) - routed):
        add("skills", "S4", "fail", f"{n}: folder has no routing.json entry, publish will refuse it")
    for n in sorted(routed - set(names)):
        add("skills", "S4", "fail", f"{n}: routing.json entry without a skill folder")

    npx_pkg = re.compile(r"\bnpx\s+(?:-y\s+|--yes\s+)?(@?[a-z0-9][\w./-]*)(?:@([\w.^~-]+))?(?=[\s`'\"]|$)", re.I)
    env_read = re.compile(r"(?:open|cat|Get-Content|read_text|load_dotenv)\W[^\n]{0,40}\.env\b", re.I)
    pipe_sh = re.compile(r"(?:curl|wget|iwr|Invoke-WebRequest)[^\n|]*\|\s*(?:ba|z)?sh\b|\|\s*iex\b", re.I)

    for n in names:
        sdir = os.path.join(skills_dir, n)
        sk = os.path.join(sdir, "SKILL.md")
        if not os.path.isfile(sk):
            add("skills", "S1", "fail", f"{n}: no SKILL.md")
            continue
        text = read(sk)
        fm = frontmatter(text)
        if fm is None:
            add("skills", "S1", "fail", f"{n}: SKILL.md has no frontmatter")
        else:
            if fm.get("name") != n:
                add("skills", "S1", "fail", f"{n}: frontmatter name '{fm.get('name')}' differs from the folder")
            dlen = len(fm.get("description", ""))
            if dlen == 0:
                add("skills", "S1", "fail", f"{n}: empty description")
            elif dlen > 1024:
                add("skills", "S1", "fail", f"{n}: description {dlen} chars, spec cap is 1024")
            elif dlen > 900:
                add("skills", "S1", "warn", f"{n}: description {dlen} chars, house aim is under 900")
        lines = text.count("\n") + 1
        if lines > 500:
            add("skills", "S2", "warn", f"{n}: SKILL.md is {lines} lines, spec guidance is under 500; move detail to references/")
        for cur, _dirs, files, _d in walk(sdir, 6):
            for f in files:
                if f == ".env" or f.startswith(".env."):
                    add("skills", "S6", "fail", f"{os.path.relpath(os.path.join(cur, f), skills_dir)}: secrets file inside a skill folder, move it to the user profile")
                    continue
                if not f.endswith((".md", ".ps1", ".py", ".sh", ".json", ".txt", ".yaml", ".yml", ".js", ".ts")):
                    continue
                fp = os.path.join(cur, f)
                rel = os.path.relpath(fp, skills_dir)
                body = read(fp)
                if EM_DASH in body:
                    add("skills", "S3", "fail", f"{rel}: {body.count(EM_DASH)} em dash(es), house rule bans them")
                if ZERO_WIDTH.search(body):
                    add("skills", "S5", "fail", f"{rel}: zero-width character found, check for hidden text")
                if f == "checkup.py" and n == "siteoffice-checkup":
                    continue  # this file holds the patterns themselves
                for m in npx_pkg.finditer(body):
                    pkg, ver = m.group(1), m.group(2)
                    if ver is None or ver.lower() in ("latest", "next"):
                        add("skills", "S5", "warn", f"{rel}: unpinned 'npx {m.group(0).split()[-1]}', pin an exact version")
                        break
                if env_read.search(body):
                    add("skills", "S5", "warn", f"{rel}: reads a .env file, confirm no secret reaches the model")
                if pipe_sh.search(body):
                    add("skills", "S5", "warn", f"{rel}: pipes a download into a shell")


# --------------------------------------------------------------------------- plugins
def check_plugins(skills_repo, public_repo):
    try:
        routing = json.load(open(os.path.join(skills_repo, "routing.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    versions = {}
    for repo in (skills_repo, public_repo):
        mp = os.path.join(repo, ".claude-plugin", "marketplace.json")
        if not os.path.isfile(mp):
            add("plugins", "P1", "fail", f"missing {mp}")
            continue
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            add("plugins", "P1", "fail", f"{mp}: invalid JSON ({exc})")
            continue
        for entry in m.get("plugins", []):
            src = os.path.normpath(os.path.join(repo, entry.get("source", "")))
            pj = os.path.join(src, ".claude-plugin", "plugin.json")
            label = f"{m.get('name')}/{entry.get('name')}"
            if not os.path.isfile(pj):
                add("plugins", "P1", "fail", f"{label}: no plugin.json at {pj}")
                continue
            p = json.load(open(pj, encoding="utf-8"))
            if p.get("name") != entry.get("name"):
                add("plugins", "P1", "fail", f"{label}: marketplace name differs from plugin.json name '{p.get('name')}', installs fail")
            versions[f"{label} (marketplace)"] = entry.get("version")
            versions[f"{label} (plugin.json)"] = p.get("version")
            # A plugin that ships hooks, commands, agents or an MCP config instead of skills
            # (burnmon-cloud, foreman-sprint-pane) never gets a skills folder: not a gap.
            other = ("hooks", "commands", "agents", ".mcp.json")
            if not os.path.isdir(os.path.join(src, "skills")) and not any(
                    os.path.exists(os.path.join(src, o)) for o in other):
                add("plugins", "P2", "warn", f"{label}: no skills folder yet, run publish-skills.ps1 -Apply")
    distinct = sorted({v for v in versions.values() if v})
    if len(distinct) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in versions.items())
        add("plugins", "P1", "fail", f"version clock split: {detail}")
    elif distinct:
        add("plugins", "P1", "pass", f"all manifests on {distinct[0]}")

    # P2: what is published versus what routing.json says should be there
    defaults = routing.get("defaults", {}).get("targets", [])
    for tname, t in routing.get("targets", {}).items():
        if not t.get("enabled") or not t.get("repoRelative"):
            continue
        tdir = os.path.normpath(os.path.join(skills_repo, t["path"].replace("\\", os.sep)))
        want = {n for n, v in routing["skills"].items()
                if not n.startswith("_") and tname in v.get("targets", defaults)}
        have = set(os.listdir(tdir)) if os.path.isdir(tdir) else set()
        have = {h for h in have if os.path.isdir(os.path.join(tdir, h)) and not h.startswith("_")}
        for n in sorted(want - have):
            add("plugins", "P2", "warn", f"{tname}: {n} routed but not published yet")
        for n in sorted(have - want):
            add("plugins", "P2", "warn", f"{tname}: {n} published but no longer routed, next publish prunes it")

    drift = os.path.join(skills_repo, "scripts", "check-drift.py")
    if os.path.isfile(drift):
        try:
            r = subprocess.run([sys.executable, drift], capture_output=True, text=True, timeout=300)
            tail = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()][-12:]
            level = "pass" if r.returncode == 0 else "warn"
            add("plugins", "P3", level, "check-drift.py exit %d: %s" % (r.returncode, " | ".join(tail)))
        except Exception as exc:  # noqa: BLE001
            add("plugins", "P3", "warn", f"check-drift.py did not run: {exc}")


# --------------------------------------------------------------------------- instruction files
def check_instructions(root, pm, depth):
    win_path = re.compile(r"`(C:\\[^`\n]+)`")
    for cur, _dirs, files, _d in walk(root, depth):
        for f in files:
            if f not in INSTRUCTION_NAMES and f != "MEMORY.md":
                continue
            fp = os.path.join(cur, f)
            rel = os.path.relpath(fp, root)
            text = read(fp)
            lines = text.count("\n") + 1
            size = len(text.encode("utf-8"))
            if lines > 200:
                add("instructions", "I1", "warn", f"{rel}: {lines} lines, official guidance is under 200 (siteoffice-cleanup compacts it)")
            if size > 25_000:
                add("instructions", "I1", "warn", f"{rel}: {size} bytes, over 25 KB")
            if EM_DASH in text:
                add("instructions", "I2", "fail", f"{rel}: {text.count(EM_DASH)} em dash(es)")
            for m in re.finditer(r"^@(\S+)", text, re.M):
                target = os.path.normpath(os.path.join(cur, m.group(1)))
                if not os.path.exists(target):
                    add("instructions", "I4", "fail", f"{rel}: import @{m.group(1)} does not resolve")
            missing = []
            for m in win_path.finditer(text):
                local = pm.to_local(m.group(1))
                if local and not os.path.exists(local):
                    missing.append(m.group(1))
            if missing:
                add("instructions", "I5", "warn", f"{rel}: {len(missing)} backticked path(s) do not exist, first: {missing[0]}")


# --------------------------------------------------------------------------- folders
def check_folders(root, depth):
    for cur, dirs, files, _d in walk(root, depth):
        if ".git" in os.listdir(cur):
            lock = os.path.join(cur, ".git", "index.lock")
            if os.path.exists(lock):
                add("folders", "F1", "fail", f"{os.path.relpath(lock, root)}: stale git lock, blocks the next commit")
    projects = os.path.join(root, "50_projects")
    if os.path.isdir(projects):
        for unit in sorted(os.listdir(projects)):
            up = os.path.join(projects, unit)
            if not os.path.isdir(up) or unit.startswith((".", "_")):
                continue
            entries = [e for e in os.listdir(up) if not e.startswith(".")]
            if not entries:
                add("folders", "F2", "warn", f"50_projects/{unit}: empty unit folder")
                continue
            if not ({"AGENTS.md", "CLAUDE.md"} & set(entries)):
                add("folders", "F2", "warn", f"50_projects/{unit}: no AGENTS.md or CLAUDE.md")
            if "siteoffice.json" not in entries:
                add("folders", "F2", "warn", f"50_projects/{unit}: no siteoffice.json manifest")


# --------------------------------------------------------------------------- report
def render(root, started):
    order = {"fail": 0, "warn": 1, "pass": 2}
    counts = {k: sum(1 for r in results if r[2] == k) for k in order}
    out = [f"# siteoffice-checkup report, {started:%Y-%m-%d %H:%M}", "",
           f"Root: `{root}`. Fail {counts['fail']}, warn {counts['warn']}, pass {counts['pass']}.", ""]
    for area in ("skills", "plugins", "instructions", "folders"):
        rows = sorted((r for r in results if r[0] == area), key=lambda r: (order[r[2]], r[1], r[3]))
        out.append(f"## {area}")
        out.append("")
        if not rows:
            out.append("Nothing found.")
        for _a, check, level, msg in rows:
            out.append(f"- {level.upper()} {check}: {msg}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="the tree on this filesystem, for example C:\\siteoffice\\acme")
    ap.add_argument("--win-root", default=None, help="Windows spelling of the same root (default: --root)")
    ap.add_argument("--skills-repo", default="50_projects/znd-skills", help="relative to --root")
    ap.add_argument("--public-repo", default="50_projects/znd-skills-public", help="relative to --root")
    ap.add_argument("--depth", type=int, default=3, help="folder depth for instruction and folder checks")
    ap.add_argument("--out", help="write the report to this file as well as stdout")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        print(f"FAIL: root {root} is not a folder. Attach it to the session and rerun.")
        return 2
    started = dt.datetime.now()
    pm = PathMap(root, a.win_root or a.root)
    skills_repo = os.path.join(root, a.skills_repo)
    public_repo = os.path.join(root, a.public_repo)
    if os.path.isdir(skills_repo):
        check_skills(skills_repo)
        check_plugins(skills_repo, public_repo)
    else:
        add("skills", "S0", "warn", f"no skills repo at {skills_repo}, skill and plugin checks skipped")
    check_instructions(root, pm, a.depth)
    check_folders(root, a.depth)
    report = render(root, started)
    print(report)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report + "\n")
    return 1 if any(r[2] == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
