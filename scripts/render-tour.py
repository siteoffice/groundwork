"""Render the guided tour: one demo flow through the engine, screenshotted light and dark.

Copies the Molenwerk example into a temp folder, shifts its dates to the render day (the board
then shows no overdue line and a filled next two weeks), drives one flow from idea to close with
gw.py, renders the board, the flow page at each gate and the receipt, and screenshots each
page in a light and a dark theme with Playwright (Chromium). The temp copy is always removed.

Usage: python scripts/render-tour.py [--out DIR] [--no-screenshots] [--write-doc DOC]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GW = REPO / "plugins" / "groundwork" / "engine" / "groundwork" / "gw.py"
RENDER_BOARD = REPO / "plugins" / "groundwork" / "board" / "render_board.py"
MOLENWERK = REPO / "templates" / "molenwerk"
DEFAULT_OUT = REPO / "assets" / "screenshots"

PROJECT = "molenwerk"
IDEA = "add a stock check"
SLUG = "add-a-stock-check"
REF = PROJECT + "/" + SLUG

# Numbering follows what a user meets: the engine opens G4 (ship) before G3 (learn).
SCREENS = [
    ("01", "board"),
    ("02", "flow-g1"),
    ("03", "flow-g2"),
    ("04", "flow-g4"),
    ("05", "receipt"),
    ("06", "flow-g3"),
]
THEMES = ("light", "dark")
VIEWPORT = {"width": 1280, "height": 800}
DISPLAY_ROOT = "~/molenwerk-demo"
STAMP = re.compile(r"\d{8}T\d{6}Z-[0-9A-F]{4}")


def png_names() -> list:
    return [
        "tour-{}-{}-{}.png".format(nn, name, theme)
        for nn, name in SCREENS
        for theme in THEMES
    ]


def _run(cmd) -> str:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "command failed (exit {}): {}\n{}{}".format(
                proc.returncode, " ".join(cmd), proc.stdout, proc.stderr
            )
        )
    return proc.stdout


def run_gw(copy_root, *args) -> str:
    cmd = [sys.executable, str(GW), "--root", str(copy_root)] + [str(a) for a in args]
    return _run(cmd)


def _short_path(path: str) -> str:
    """The Windows 8.3 short form of path, or path itself elsewhere or on failure."""
    if os.name != "nt":
        return path
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        return buf.value if 0 < n < len(buf) else path
    except Exception:
        return path


def _root_forms(work_root) -> list:
    """Every spelling of work_root gw may print: as given, absolute, resolved, 8.3 short."""
    raw = str(work_root)
    bases = {raw, os.path.abspath(raw), str(Path(raw).resolve())}
    bases |= {_short_path(b) for b in list(bases)}
    forms = set()
    for base in bases:
        base = base.rstrip("\\/")
        if base:
            forms.add(base)
            forms.add(base.replace("\\", "/"))
    return sorted(forms, key=len, reverse=True)


def normalize(text: str, work_root) -> str:
    for form in _root_forms(work_root):
        text = text.replace(form, "<copy>")
    return STAMP.sub("<stamp>", text)


ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")  # not \b: it would miss 2026-08-20_name.md
TEXT_SUFFIXES = (".md", ".json", ".txt")
FIRST_DEADLINE_IN_DAYS = 2


def _shift_text(text: str, delta: timedelta) -> str:
    def move(match):
        try:
            return (date.fromisoformat(match.group(0)) + delta).isoformat()
        except ValueError:
            return match.group(0)

    return ISO_DATE.sub(move, text)


def shift_dates(copy: Path, today: date) -> int:
    """Shift every ISO date in the copy (text and file-name prefixes) by one number of days.

    The earliest deadline lands FIRST_DEADLINE_IN_DAYS after today, so the board shows no overdue
    line and the next two weeks fill. One shift for all dates keeps their order and spacing, and
    keeps every Updated date in the past. Returns the shift in days. Files are rewritten, so the
    board's freshness check (file age) sees them as just updated. The template is never touched.
    """
    rows = ISO_DATE.findall((copy / "DEADLINES.md").read_text(encoding="utf-8").split("| Date |")[-1])
    first = min(date(int(y), int(m), int(d)) for y, m, d in rows)
    delta = timedelta(days=(today + timedelta(days=FIRST_DEADLINE_IN_DAYS) - first).days)
    for path in sorted(copy.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            raw = path.read_bytes().decode("utf-8")
            moved = _shift_text(raw, delta)
            if moved != raw:
                path.write_bytes(moved.encode("utf-8"))
    for path in sorted(copy.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        name = _shift_text(path.name, delta)
        if name != path.name:
            path.rename(path.with_name(name))
    return delta.days


def playwright_available() -> tuple:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright is not installed (pip install -r scripts/requirements-tour.txt)"
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception as exc:
        return False, "playwright could not start: {}".format(exc)
    if not path or not Path(path).exists():
        return False, "playwright Chromium is missing (python -m playwright install chromium)"
    return True, ""


def _render_board(*args) -> None:
    _run([sys.executable, str(RENDER_BOARD)] + [str(a) for a in args])


def _snapshot_flow(copy: Path, tour_dir: Path, nn: str, name: str) -> None:
    _render_board("flows", copy / "flows")
    src = copy / "_board" / "flows" / PROJECT / SLUG / "index.html"
    shutil.copyfile(src, tour_dir / "{}-{}.html".format(nn, name))


def _quote(arg) -> str:
    text = str(arg)
    return '"{}"'.format(text) if (" " in text or not text) else text


def _entry(args, output) -> str:
    line = "> gw " + " ".join(_quote(a) for a in args)
    return line + "\n" + output.rstrip("\n") + "\n"


def _drive(copy: Path, tour_dir: Path) -> dict:
    """Run the flow from idea to close; return the raw (unnormalized) transcript per gate."""
    transcript = {}

    def gw(*args) -> str:
        return run_gw(copy, *args)

    def gate(key, nn, name, answer, before=None):
        opener = ("advance", REF)
        out = gw(*opener)
        _snapshot_flow(copy, tour_dir, nn, name)
        if before:
            before()
        transcript[key] = _entry(opener, out) + _entry(answer, gw(*answer))

    def board():
        _render_board("node", copy)
        shutil.copyfile(copy / "_board" / "board.html", tour_dir / "01-board.html")

    gw("new", PROJECT, IDEA)
    gw("advance", REF)
    gw("advance", REF)
    gate("G1", "02", "flow-g1", ("gate", REF, "approve"), before=board)

    gw("advance", REF, "--steps", "build the stock check")
    gate("G2", "03", "flow-g2", ("gate", REF, "approve"))

    spec = copy / "spec.md"
    spec.write_text("A stock check before an order is confirmed.\n", encoding="utf-8")
    gw("advance", REF, "--spec", spec.as_posix())
    gate("G4", "04", "flow-g4", ("gate", REF, "ship", "looks right"))

    gw("advance", REF)
    gate("G3", "06", "flow-g3", ("gate", REF, "proceed"))

    gw("advance", REF)
    return transcript


def _find_receipts(copy: Path) -> list:
    return sorted(p for p in copy.rglob("step-*.html") if p.parent.name == "receipts")


def _screenshot(files: dict, out_dir: Path) -> list:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for theme in THEMES:
                context = browser.new_context(viewport=VIEWPORT, color_scheme=theme)
                try:
                    page = context.new_page()
                    for nn, name in SCREENS:
                        page.goto(files[name].resolve().as_uri())
                        page.evaluate("t => { document.body.dataset.theme = t; }", theme)
                        target = out_dir / "tour-{}-{}-{}.png".format(nn, name, theme)
                        page.screenshot(path=str(target), full_page=True)
                finally:
                    context.close()
        finally:
            browser.close()
    return [out_dir / name for name in png_names()]


def _scrub_page(page: Path, work: Path) -> str:
    """Rewrite page in place so no local temp path (and so no OS username) ships."""
    text = page.read_text(encoding="utf-8")
    for form in _root_forms(work):
        text = text.replace(form.replace("\\", "\\\\"), DISPLAY_ROOT)  # JSON-escaped copies
        text = text.replace(form, DISPLAY_ROOT)
    page.write_text(text, encoding="utf-8")
    return text


def _on_rm_error(func, path, _exc) -> None:
    """Windows: clear the read-only bit and retry once."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def render_tour(out_dir, *, screenshots=True, work_parent=None, today=None) -> dict:
    out_dir = Path(out_dir)
    work = Path(tempfile.mkdtemp(prefix="groundwork-tour-", dir=work_parent))
    try:
        copy = work / PROJECT
        shutil.copytree(MOLENWERK, copy)
        shift_dates(copy, today or date.today())
        tour_dir = copy / "_tour"
        tour_dir.mkdir()
        raw = _drive(copy, tour_dir)
        receipts = _find_receipts(copy)
        if receipts:
            shutil.copyfile(receipts[0], tour_dir / "05-receipt.html")
        files = {name: tour_dir / "{}-{}.html".format(nn, name) for nn, name in SCREENS}
        missing = [str(p) for p in files.values() if not p.exists()]
        if missing:
            raise RuntimeError("tour pages missing: " + ", ".join(missing))
        pages = {name: _scrub_page(p, work) for name, p in files.items()}
        transcript = {key: normalize(text, copy) for key, text in raw.items()}
        pngs = _screenshot(files, out_dir) if screenshots else []
        return {
            "pages": pages,
            "pngs": pngs,
            "transcript": transcript,
            "receipts": len(receipts),
        }
    finally:
        if sys.version_info >= (3, 12):
            shutil.rmtree(work, onexc=_on_rm_error)
        else:
            shutil.rmtree(work, onerror=_on_rm_error)


def doc_block(transcript: str) -> str:
    """The fenced block for docs/tour.md: forward slashes so every OS writes the same text."""
    return "```text\n" + transcript.replace("\\", "/").rstrip("\n") + "\n```\n"


def write_doc(doc, transcript: dict) -> None:
    """Rewrite only the text between each <!-- tour:GX --> and <!-- /tour:GX --> pair."""
    doc = Path(doc)
    text = doc.read_text(encoding="utf-8")
    for key, value in transcript.items():
        pattern = re.compile(
            r"(<!-- tour:{0} -->\n).*?(<!-- /tour:{0} -->)".format(re.escape(key)), re.S
        )
        block = doc_block(value)
        text = pattern.sub(lambda m: m.group(1) + block + m.group(2), text)
    with open(doc, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="render-tour.py", description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where the PNGs go")
    parser.add_argument("--no-screenshots", action="store_true", help="render pages only")
    parser.add_argument("--write-doc", metavar="DOC", help="refill the transcript blocks in DOC")
    args = parser.parse_args(argv)
    try:
        if not args.no_screenshots:
            ok, reason = playwright_available()
            if not ok:
                raise RuntimeError(reason)
        result = render_tour(Path(args.out), screenshots=not args.no_screenshots)
        if args.write_doc:
            write_doc(args.write_doc, result["transcript"])
    except Exception as exc:
        print("render-tour: {}".format(exc), file=sys.stderr)
        return 1
    for key in ("G1", "G2", "G4", "G3"):
        print(result["transcript"][key])
    for png in result["pngs"]:
        print("wrote {}".format(png))
    print("pages: {}, receipts: {}, pngs: {}".format(
        len(result["pages"]), result["receipts"], len(result["pngs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
