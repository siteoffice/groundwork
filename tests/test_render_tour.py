"""Tests for scripts/render-tour.py: one demo flow through the engine, rendered for the tour."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "render-tour.py"


def load_tour():
    spec = importlib.util.spec_from_file_location("render_tour", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tour():
    return load_tour()


@pytest.fixture(scope="module")
def rendered(tour, tmp_path_factory):
    """One non-screenshot render, shared by the tests that only read its result."""
    base = tmp_path_factory.mktemp("tour")
    work = base / "work"
    work.mkdir()
    result = tour.render_tour(base / "out", screenshots=False, work_parent=work)
    return result, work


def test_png_names_are_twelve_pairs(tour):
    names = tour.png_names()
    assert len(names) == 12
    pattern = re.compile(r"^tour-\d\d-[a-z0-9-]+-(light|dark)\.png$")
    for name in names:
        assert pattern.match(name), name
    for light, dark in zip(names[0::2], names[1::2]):
        assert light.endswith("-light.png")
        assert dark == light[: -len("-light.png")] + "-dark.png"
    assert [n for n, _ in tour.SCREENS] == ["01", "02", "03", "04", "05", "06"]


def test_shift_dates_puts_deadlines_ahead_and_leaves_the_template_alone(tour, tmp_path):
    before = (tour.MOLENWERK / "DEADLINES.md").read_bytes()
    copy = tmp_path / "molenwerk"
    shutil.copytree(tour.MOLENWERK, copy)
    today = date(2027, 3, 10)
    days = tour.shift_dates(copy, today)
    rows = [date.fromisoformat(d) for d in re.findall(
        r"^\| (\d{4}-\d\d-\d\d) \|", (copy / "DEADLINES.md").read_text(encoding="utf-8"), re.M)]
    assert rows[0] == today + timedelta(days=2)
    assert rows == sorted(rows) and all(r > today for r in rows)
    # spacing kept: the template's own gaps between deadlines
    original = [date.fromisoformat(d) for d in re.findall(
        r"^\| (\d{4}-\d\d-\d\d) \|", before.decode("utf-8"), re.M)]
    assert [b - a for a, b in zip(rows, rows[1:])] == [b - a for a, b in zip(original, original[1:])]
    assert (original[0] + timedelta(days=days)) == rows[0]
    # an Updated date stays in the past, and the dated file name moves with its date
    assert "Updated: 2026-08-28" not in (copy / "DEADLINES.md").read_text(encoding="utf-8")
    assert not any(p.name.startswith("2026-08-20_") for p in copy.rglob("*"))
    assert (tour.MOLENWERK / "DEADLINES.md").read_bytes() == before


def test_six_pages_render_and_one_receipt(tour, rendered):
    result, _ = rendered
    pages = result["pages"]
    assert sorted(pages) == sorted(name for _, name in tour.SCREENS)
    for name, html in pages.items():
        assert html.lower().startswith("<!doctype html"), name
        assert len(html) > 500, name
    assert result["receipts"] == 1
    assert "self-issued, not signed" in pages["receipt"]


def test_temp_copy_removed(rendered):
    _, work = rendered
    assert work.is_dir()
    assert list(work.iterdir()) == []


def test_temp_copy_removed_after_failure(tour, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    real = tour.run_gw
    calls = {"n": 0}

    def flaky(copy_root, *args):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("gw failed on purpose")
        return real(copy_root, *args)

    monkeypatch.setattr(tour, "run_gw", flaky)
    with pytest.raises(RuntimeError):
        tour.render_tour(tmp_path / "out", screenshots=False, work_parent=work)
    assert list(work.iterdir()) == []


def test_transcript_is_normalized(tour, rendered):
    result, work = rendered
    transcript = result["transcript"]
    assert sorted(transcript) == ["G1", "G2", "G3", "G4"]
    forms = set(tour._root_forms(work)) | {str(work), work.as_posix()}
    for gate, text in transcript.items():
        assert text.startswith("> gw "), gate
        for form in forms:
            assert form not in text, (gate, form)
        assert not re.search(r"\d{8}T\d{6}Z", text), gate
    assert "receipt (self-issued, not signed): <copy>" in transcript["G4"]


def test_pages_carry_no_local_path(tour, rendered):
    result, work = rendered
    temp = tempfile.gettempdir()
    forms = set(tour._root_forms(work)) | set(tour._root_forms(temp))
    forms |= {temp, temp.replace("\\", "/")}
    for name, html in result["pages"].items():
        for form in forms:
            assert form not in html, (name, form)
            assert form.replace("\\", "\\\\") not in html, (name, form)
        if os.name == "nt":
            assert "~1\\" not in html, name
    assert "~/molenwerk-demo" in result["pages"]["flow-g4"]


def test_normalize_replaces_root_and_stamp(tour, tmp_path):
    root = tmp_path / "molenwerk"
    text = "token FLOW-X-G1-20261101T101132Z-EC17 at {}{}flows and {}/flows".format(
        root, "\\" if "\\" in str(root) else "/", root.as_posix()
    )
    out = tour.normalize(text, root)
    assert str(root) not in out
    assert root.as_posix() not in out
    assert "FLOW-X-G1-<stamp>" in out
    assert out.count("<copy>") == 2


def test_twelve_pngs(tour, tmp_path):
    ok, reason = tour.playwright_available()
    if not ok:
        pytest.skip(reason)
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "out"
    result = tour.render_tour(out, work_parent=work)
    assert sorted(p.name for p in out.iterdir()) == sorted(tour.png_names())
    assert [p.name for p in result["pngs"]] == tour.png_names()
    for png in result["pngs"]:
        data = png.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), png.name
        assert len(data) > 10 * 1024, png.name
    assert list(work.iterdir()) == []


def test_script_is_ascii():
    SCRIPT.read_bytes().decode("ascii")


DOC = ROOT / "docs" / "tour.md"
GATES = ("G1", "G2", "G4", "G3")


def read_doc() -> str:
    assert DOC.is_file(), "docs/tour.md is missing"
    return DOC.read_text(encoding="utf-8")


def test_tour_doc_references_twelve_pngs_in_pairs(tour):
    text = read_doc()
    positions = []
    for name in tour.png_names():
        link = "](../assets/screenshots/{})".format(name)
        assert text.count(link) == 1, name
        positions.append(text.index(link))
    assert positions == sorted(positions)
    images = re.findall(r"!\[([^\]]*)\]\(\.\./assets/screenshots/([^)]+)\)", text)
    assert len(images) == 12
    for alt, name in images:
        assert len(alt) >= 20, name


def test_tour_doc_has_three_lines_per_screen(tour):
    text = read_doc()
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    labels = ("**What you see:**", "**What it means:**", "**What you do next:**")
    screen_sections = [s for s in sections if "../assets/screenshots/tour-" in s]
    assert len(screen_sections) == len(tour.SCREENS)
    for (nn, name), section in zip(tour.SCREENS, screen_sections):
        assert "tour-{}-{}-light.png".format(nn, name) in section, name
        lines = [line for line in section.splitlines() if line.startswith("**What ")]
        assert [line.split("**")[1] for line in lines] == [l.strip("*") for l in labels], name


def test_tour_doc_transcripts_match_a_real_run(tour, rendered):
    result, _ = rendered
    text = read_doc()
    for gate in GATES:
        match = re.search(
            r"<!-- tour:{0} -->\n(.*?)<!-- /tour:{0} -->".format(gate), text, flags=re.S
        )
        assert match, gate
        expected = tour.doc_block(result["transcript"][gate])
        assert match.group(1) == expected, gate


def test_write_doc_rewrites_only_the_marker_blocks(tour, rendered, tmp_path):
    result, _ = rendered
    doc = tmp_path / "tour.md"
    doc.write_text(
        "intro\n<!-- tour:G1 -->\nold\n<!-- /tour:G1 -->\nmiddle\n"
        "<!-- tour:G3 -->\n<!-- /tour:G3 -->\nend\n",
        encoding="utf-8",
    )
    tour.write_doc(doc, result["transcript"])
    out = doc.read_text(encoding="utf-8")
    assert out.startswith("intro\n<!-- tour:G1 -->\n```text\n> gw ")
    assert "old" not in out
    assert "\nmiddle\n" in out and out.endswith("<!-- /tour:G3 -->\nend\n")
    assert tour.doc_block(result["transcript"]["G3"]) in out


def test_tour_doc_has_no_em_dash():
    assert "\u2014" not in read_doc()
