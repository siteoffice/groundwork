"""Panel-level tests for the five real collectors/templates added in
sprint 1 step 5 (waiting_on_you, next_weeks, roadmap, children, activity):
one test per panel per state, using temp directories with synthetic files,
plus dateparse unit tests, a `tree` test on a synthetic root with two
children, and a hub-shaped node with all five panels normal. Stdlib
compatible: run with `python -m pytest board\\tests -q` or plain unittest.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import render_board  # noqa: E402
import dateparse  # noqa: E402

TEMPLATES_DIR = BOARD_DIR / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))
import next_weeks as tpl_next_weeks  # noqa: E402
import roadmap as tpl_roadmap  # noqa: E402
import children as tpl_children  # noqa: E402
import activity as tpl_activity  # noqa: E402
import waiting_on_you as tpl_waiting_on_you  # noqa: E402
import funnel as tpl_funnel  # noqa: E402
import receipts as tpl_receipts  # noqa: E402
from board_common import load_module_from_path  # noqa: E402

# board\templates\hours.py is also a top-level module named "hours"
# (board\hours.py, the reader): load it by explicit path rather than a
# plain `import hours`, which would silently return whichever of the two
# same-named files some other test module happened to import first.
tpl_hours = load_module_from_path("_siteoffice_hours_panel_test", TEMPLATES_DIR / "hours.py")


def _write_manifest(node_dir: pathlib.Path, **overrides) -> dict:
    raw = {
        "schemaVersion": 2,
        "type": "project",
        "name": node_dir.name,
        "parent": "",
        "children": [],
        "language": "en",
        "settings": {
            "planningHorizonWeeks": 2,
            "languages": ["en"],
            "timeRegistration": False,
        },
    }
    raw.update(overrides)
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")
    return raw


def _age(path: pathlib.Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


class TestNextWeeksPanel(unittest.TestCase):
    def test_unavailable_when_deadlines_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_next_weeks.collect(manifest)
            self.assertEqual(state, tpl_next_weeks.STATE_UNAVAILABLE)

    def test_empty_when_no_row_in_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            far = (date.today() + timedelta(weeks=52)).isoformat()
            (node_dir / "DEADLINES.md").write_text(
                f"# deadlines\n\n| Date | Item | Notes |\n|---|---|---|\n"
                f"| {far} | Far off thing | note |\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_next_weeks.collect(manifest)
            self.assertEqual(state, tpl_next_weeks.STATE_EMPTY)

    def test_normal_with_row_inside_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            soon = (date.today() + timedelta(days=3)).isoformat()
            (node_dir / "DEADLINES.md").write_text(
                f"# deadlines\n\n| Date | Item | Notes |\n|---|---|---|\n"
                f"| {soon} | Something due soon | note |\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_next_weeks.collect(manifest)
            self.assertEqual(state, tpl_next_weeks.STATE_NORMAL)
            self.assertEqual(len(data["rows"]), 1)
            html = tpl_next_weeks.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("Something due soon", html)

    def test_stale_when_deadlines_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "refreshDays": 1,
            })
            soon = (date.today() + timedelta(days=3)).isoformat()
            path = node_dir / "DEADLINES.md"
            path.write_text(
                f"# deadlines\n\n| Date | Item | Notes |\n|---|---|---|\n"
                f"| {soon} | Something | note |\n",
                encoding="utf-8",
            )
            _age(path, 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_next_weeks.collect(manifest)
            self.assertEqual(state, tpl_next_weeks.STATE_STALE)


class TestRoadmapPanel(unittest.TestCase):
    def test_unavailable_when_roadmap_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            self.assertEqual(state, tpl_roadmap.STATE_UNAVAILABLE)

    def test_empty_when_no_numbered_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\nJust prose, no priority list.\n", encoding="utf-8"
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            self.assertEqual(state, tpl_roadmap.STATE_EMPTY)

    def test_normal_with_priorities_and_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n"
                "1. First priority, due 15 Sep 2026\n"
                "2. Second priority\n\n"
                "## Backlog\n\n- idea one\n- idea two\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            self.assertEqual(state, tpl_roadmap.STATE_NORMAL)
            self.assertEqual(len(data["priorities"]), 2)
            self.assertEqual(data["priorities"][0]["date"], date(2026, 9, 15))
            self.assertEqual(data["backlog"], ["idea one", "idea two"])
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("First priority", html)
            self.assertIn("<details>", html)

    def test_stale_when_roadmap_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "refreshDays": 1,
            })
            (node_dir / "02_roadmap").mkdir()
            path = node_dir / "02_roadmap" / "roadmap.md"
            path.write_text("# roadmap\n\n1. Only priority\n", encoding="utf-8")
            _age(path, 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            self.assertEqual(state, tpl_roadmap.STATE_STALE)

    def test_timeline_renders_three_zoom_variants_with_a_dated_priority(self):
        # Plan item 1.11, pattern 1: date-range bars, three pre-rendered
        # zoom variants, only "month" starts active.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            soon = (date.today() + timedelta(days=5)).isoformat()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                f"# roadmap\n\n1. Ship the thing, due {soon}\n", encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertEqual(html.count('data-zoom="month"'), 1)
            self.assertEqual(html.count('data-zoom="quarter"'), 1)
            self.assertEqual(html.count('data-zoom="year"'), 1)
            month_variant = html.split('data-zoom="month"')[1].split("data-zoom=")[0]
            self.assertIn("tl-mark", month_variant)
            self.assertIn('class="tl-variant tl-active" data-zoom="month"', html)
            self.assertNotIn('class="tl-variant tl-active" data-zoom="quarter"', html)

    def test_dated_priority_with_gate_tag_renders_as_a_gate_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            soon = (date.today() + timedelta(days=5)).isoformat()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                f"# roadmap\n\n1. Ship the thing, due {soon} [gate:G4]\n", encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("tl-gate", html)
            self.assertIn('<span class="tl-gate-label">G4</span>', html)
            # The tag is stripped from both the timeline title and the
            # priority-list line, never shown verbatim.
            self.assertNotIn("[gate:", html)

    def test_no_timeline_when_no_priority_carries_a_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. No date on this one\n", encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertNotIn("tl-zoom-toggle", html)

    def test_backlog_groups_by_gate_with_item_count_and_slice_rail(self):
        # Plan item 1.11, patterns 2 and 3: group headers with sums (here,
        # item counts) and a slice-by rail keyed on the same data-gate a
        # bullet's own [gate:GN] suffix tag names.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. Only priority\n\n"
                "## Backlog\n\n- idea one [gate:G2]\n- idea two [gate:G2]\n- plain idea\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn('<div class="group"><div class="group-head"><h3>G2</h3>', html)
            self.assertIn("2 items", html)
            self.assertIn(
                '<div class="group-head"><h3>Ungrouped</h3>', html,
            )
            self.assertIn("1 item<", html)
            self.assertIn('data-slice-btn="G2"', html)
            self.assertIn('data-slice-btn="all"', html)
            self.assertEqual(html.count('data-gate="G2"'), 2)
            # The tag itself never leaks into the displayed item text.
            self.assertNotIn("[gate:", html)

    def test_no_slice_rail_when_backlog_has_no_gate_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. Only priority\n\n## Backlog\n\n- idea one\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertNotIn("slice-rail", html)
            self.assertIn('<div class="group"><div class="group-head"><h3>Ungrouped</h3>', html)

    def test_bare_gate_text_without_tag_is_not_treated_as_a_gate_marker(self):
        # Decision 2026-09-13: formalized the heuristic into a structured
        # `[gate:GN]` tag. A stray "G2" in ordinary prose (not the suffix
        # tag) must not group or slice any more, only the tag does.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. Only priority\n\n"
                "## Backlog\n\n- G2 is mentioned here but not tagged\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertNotIn("slice-rail", html)
            self.assertIn('<div class="group-head"><h3>Ungrouped</h3>', html)

    def test_backlog_groups_render_in_gate_execution_order_not_numeric(self):
        # Foreman's real sequence is G1 -> G2 -> G4 -> G3 (G4 fires before
        # G3, docs\znd_os_flow_operator_guide.html section 06). Group
        # headers and the slice rail must follow that order, not G1 < G2 <
        # G3 < G4, so the rail reads left to right as gates actually happen.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "02_roadmap").mkdir()
            (node_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. Only priority\n\n"
                "## Backlog\n\n"
                "- learn item [gate:G3]\n"
                "- ship item [gate:G4]\n"
                "- breakdown item [gate:G1]\n"
                "- grill item [gate:G2]\n",
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_roadmap.collect(manifest)
            html = tpl_roadmap.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            headings_order = [
                g for g in ("G1", "G2", "G4", "G3")
            ]
            positions = [html.index(f"<h3>{g}</h3>") for g in headings_order]
            self.assertEqual(positions, sorted(positions))


class TestChildrenPanel(unittest.TestCase):
    def test_empty_when_no_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_children.collect(manifest)
            self.assertEqual(state, tpl_children.STATE_EMPTY)

    def test_unavailable_when_a_listed_child_folder_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, children=["ghost"])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_children.collect(manifest)
            self.assertEqual(state, tpl_children.STATE_UNAVAILABLE)
            self.assertEqual(data["missing"], ["ghost"])

    def test_normal_with_an_existing_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, children=["kid"])
            _write_manifest(node_dir / "kid")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_children.collect(manifest)
            self.assertEqual(state, tpl_children.STATE_NORMAL)
            html = tpl_children.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("kid", html)

    def test_stale_when_child_board_json_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, children=["kid"], settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "refreshDays": 1,
            })
            kid_dir = node_dir / "kid"
            _write_manifest(kid_dir)
            (kid_dir / "_board").mkdir()
            board_json = kid_dir / "_board" / "board.json"
            board_json.write_text(json.dumps({"displayName": "Kid"}), encoding="utf-8")
            _age(board_json, 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_children.collect(manifest)
            self.assertEqual(state, tpl_children.STATE_STALE)


class TestActivityPanel(unittest.TestCase):
    def test_unavailable_when_log_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_activity.collect(manifest)
            self.assertEqual(state, tpl_activity.STATE_UNAVAILABLE)

    def test_empty_when_log_dir_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "_log").mkdir()
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_activity.collect(manifest)
            self.assertEqual(state, tpl_activity.STATE_EMPTY)

    def test_normal_with_a_recent_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "_log").mkdir()
            (node_dir / "_log" / "2026-09-11_did_a_thing.md").write_text(
                "did a thing", encoding="utf-8"
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_activity.collect(manifest)
            self.assertEqual(state, tpl_activity.STATE_NORMAL)
            html = tpl_activity.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("did_a_thing", html)

    def test_stale_when_newest_entry_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "refreshDays": 1,
            })
            (node_dir / "_log").mkdir()
            entry = node_dir / "_log" / "old_entry.md"
            entry.write_text("old", encoding="utf-8")
            _age(entry, 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_activity.collect(manifest)
            self.assertEqual(state, tpl_activity.STATE_STALE)

    def test_orders_by_a_timestamp_embedded_in_the_filename_over_mtime(self):
        """A git checkout resets every checked-out file's own mtime to
        checkout time; ordering must not silently follow that reset mtime
        when a file's own name already carries its real, earlier
        timestamp (T19's own real-repo finding)."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "_log").mkdir()
            older = node_dir / "_log" / "sync-receipt-2026-09-13T19-32-29Z.json"
            newer = node_dir / "_log" / "sync-receipt-2026-09-13T19-55-03Z.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            # Simulate a git checkout: both files get the SAME mtime,
            # touched in an order that would put the OLDER file last by
            # mtime alone if mtime were still the sort key.
            same_checkout_time = time.time()
            os.utime(older, (same_checkout_time, same_checkout_time))
            os.utime(newer, (same_checkout_time, same_checkout_time))
            manifest = render_board.load_manifest(node_dir)
            _, data = tpl_activity.collect(manifest)
            self.assertEqual(
                [e["name"] for e in data["entries"]],
                ["sync-receipt-2026-09-13T19-55-03Z.json", "sync-receipt-2026-09-13T19-32-29Z.json"],
            )


class TestWaitingOnYouPanel(unittest.TestCase):
    def test_empty_with_no_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_waiting_on_you.collect(manifest)
            self.assertEqual(state, tpl_waiting_on_you.STATE_EMPTY)

    def test_unavailable_when_declared_flows_root_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="no_such_flows_dir")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_waiting_on_you.collect(manifest)
            self.assertEqual(state, tpl_waiting_on_you.STATE_UNAVAILABLE)

    def test_normal_with_an_open_funnel_card(self):
        # Two numbered stages so the first one (01_leads) still has a next
        # gate (G1) to wait on; a lone terminal stage carries no gate.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "00_funnel" / "02_scored").mkdir(parents=True)
            stage_dir = node_dir / "00_funnel" / "01_leads"
            stage_dir.mkdir(parents=True)
            (stage_dir / "acme.md").write_text("a lead", encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_waiting_on_you.collect(manifest)
            self.assertEqual(state, tpl_waiting_on_you.STATE_NORMAL)
            html = tpl_waiting_on_you.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("acme", html)

    def test_normal_with_an_open_flow_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            gate_dir = node_dir / "flows" / "proj" / "slug"
            gate_dir.mkdir(parents=True)
            (gate_dir / "flow.json").write_text(
                json.dumps({
                    "project": "proj", "slug": "slug",
                    "status": "open", "gate": {"name": "G1", "status": "awaiting-owner"},
                }),
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_waiting_on_you.collect(manifest)
            self.assertEqual(state, tpl_waiting_on_you.STATE_NORMAL)
            self.assertEqual(len(data["items"]), 1)

    def test_a_decided_gate_does_not_appear(self):
        """R05: a gate that was decided (status advanced past
        awaiting-owner) must disappear from the list, not just gain a
        status field nobody reads."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            gate_dir = node_dir / "flows" / "proj" / "slug"
            gate_dir.mkdir(parents=True)
            (gate_dir / "flow.json").write_text(
                json.dumps({
                    "project": "proj", "slug": "slug", "status": "running",
                    "gate": {"name": "G1", "status": "decided",
                             "decision": {"verdict": "approve"}},
                }),
                encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_waiting_on_you.collect(manifest)
            self.assertEqual(state, tpl_waiting_on_you.STATE_EMPTY)

    def test_repo_slug_parses_github_https_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            git_dir = node_dir / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/exampleuser/siteoffice.git\n',
                encoding="utf-8",
            )
            self.assertEqual(
                tpl_waiting_on_you._repo_slug(node_dir), "exampleuser/siteoffice"
            )

    def test_collect_open_pr_count_uses_injected_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            git_dir = node_dir / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/exampleuser/siteoffice.git\n',
                encoding="utf-8",
            )
            old_token = os.environ.get("GITHUB_TOKEN")
            os.environ["GITHUB_TOKEN"] = "fake-token-for-test"
            try:
                count = tpl_waiting_on_you.collect_open_pr_count(
                    node_dir, fetch=lambda slug, token: 3
                )
            finally:
                if old_token is None:
                    os.environ.pop("GITHUB_TOKEN", None)
                else:
                    os.environ["GITHUB_TOKEN"] = old_token
            self.assertEqual(count, 3)


class TestFunnelPanel(unittest.TestCase):
    def test_unavailable_when_no_00_funnel_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp) / "isolated"
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_funnel.collect(manifest)
            self.assertEqual(state, tpl_funnel.STATE_UNAVAILABLE)

    def test_empty_when_00_funnel_has_no_stages_and_no_loose_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            (node_dir / "00_funnel").mkdir()
            (node_dir / "00_funnel" / "README.md").write_text("inbox", encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_funnel.collect(manifest)
            self.assertEqual(state, tpl_funnel.STATE_EMPTY)
            self.assertEqual(data["columns"], [])

    def test_stale_when_00_funnel_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "refreshDays": 1,
            })
            funnel_dir = node_dir / "00_funnel"
            funnel_dir.mkdir()
            (funnel_dir / "01_inbox").mkdir()
            _age(funnel_dir, 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_funnel.collect(manifest)
            self.assertEqual(state, tpl_funnel.STATE_STALE)

    def test_normal_dual_read_loose_file_only_one_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            funnel_dir = node_dir / "00_funnel"
            funnel_dir.mkdir()
            (funnel_dir / "README.md").write_text("inbox", encoding="utf-8")
            (funnel_dir / "2026-09-11_wilco_new-idea.md").write_text(
                "# A new idea\n\nrequest: look at this\n", encoding="utf-8",
            )
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_funnel.collect(manifest)
            self.assertEqual(state, tpl_funnel.STATE_NORMAL)
            self.assertEqual(len(data["columns"]), 1)
            self.assertEqual(data["columns"][0]["stage"], "inbox")
            card = data["columns"][0]["cards"][0]
            self.assertEqual(card["title"], "A new idea")
            self.assertEqual(card["date"], "2026-09-11")
            self.assertEqual(card["author"], "wilco")
            self.assertEqual(card["request"], "look at this")
            html = tpl_funnel.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("A new idea", html)

    def test_normal_with_stage_folders_gate_and_column_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"],
                "timeRegistration": False, "funnel": {"columnLimit": 1},
            })
            funnel_dir = node_dir / "00_funnel"
            stage1 = funnel_dir / "01_inbox"
            stage1.mkdir(parents=True)
            (stage1 / "a.md").write_text("no heading here", encoding="utf-8")
            (stage1 / "b.md").write_text("no heading here either", encoding="utf-8")
            (funnel_dir / "02_shaped").mkdir()
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_funnel.collect(manifest)
            self.assertEqual(state, tpl_funnel.STATE_NORMAL)
            columns = {c["stage"]: c for c in data["columns"]}
            self.assertEqual(len(columns["01_inbox"]["cards"]), 2)
            self.assertEqual(columns["01_inbox"]["cards"][0]["gate"], "G1")
            self.assertTrue(columns["01_inbox"]["overLimit"])
            self.assertFalse(columns["02_shaped"]["overLimit"])
            html = tpl_funnel.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("chip dirty", html)

    def test_filename_parsing_bad_filename_falls_back_to_mtime_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            (node_dir / "not-the-convention.md").write_text("x", encoding="utf-8")
            card = tpl_funnel._card_from_file(node_dir / "not-the-convention.md", gate=None)
            self.assertEqual(card["author"], "unknown")
            self.assertTrue(card["date"])  # mtime-derived, never empty


def _write_hours_month(node_dir: pathlib.Path, year_month: str, lines):
    time_dir = node_dir / "03_logs" / "time"
    time_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(line) for line in lines)
    (time_dir / f"{year_month}.ndjson").write_text(text + "\n", encoding="utf-8")


class TestHoursPanel(unittest.TestCase):
    def test_disabled_when_time_registration_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)  # default settings: timeRegistration False
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_DISABLED)

    def test_unavailable_when_time_dir_path_is_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
            })
            (node_dir / "03_logs").mkdir()
            (node_dir / "03_logs" / "time").write_text("not a directory", encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_UNAVAILABLE)

    def test_empty_when_enabled_but_no_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
            })
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_EMPTY)

    def test_stale_when_ndjson_older_than_refresh_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
                "refreshDays": 1,
            })
            today = date.today()
            _write_hours_month(node_dir, today.strftime("%Y-%m"), [
                {"date": today.isoformat(), "person": "wilco", "node": "x", "hours": 1.0,
                 "so": False, "tag": "", "note": "", "loggedOn": today.isoformat()},
            ])
            _age(node_dir / "03_logs" / "time" / f"{today.strftime('%Y-%m')}.ndjson", 10)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_STALE)

    def test_normal_renders_month_year_and_criterion_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
            })
            today = date.today()
            _write_hours_month(node_dir, today.strftime("%Y-%m"), [
                {"date": today.isoformat(), "person": "wilco", "node": "x", "hours": 3.0,
                 "so": True, "tag": "build", "note": "", "loggedOn": today.isoformat()},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_NORMAL)
            html = tpl_hours.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("wilco", html)
            self.assertIn("1225", html)

    def test_holding_node_sums_over_children_with_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "hub"
            _write_manifest(root, type="holding", children=["child"], settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
            })
            today = date.today()
            _write_hours_month(root / "child", today.strftime("%Y-%m"), [
                {"date": today.isoformat(), "person": "sam", "node": "child", "hours": 2.0,
                 "so": False, "tag": "", "note": "", "loggedOn": today.isoformat()},
            ])
            manifest = render_board.load_manifest(root)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(state, tpl_hours.STATE_NORMAL)
            self.assertEqual(data["summary"]["perPerson"]["sam"], 2.0)

    def test_group_header_shows_sum_and_entry_count_per_person(self):
        # Plan item 1.11, pattern 2: a group header per owner, sum plus a
        # real item (entry) count, not just the flat hours total.
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, settings={
                "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
            })
            today = date.today()
            _write_hours_month(node_dir, today.strftime("%Y-%m"), [
                {"date": today.isoformat(), "person": "wilco", "node": "x", "hours": 3.0,
                 "so": True, "tag": "build", "note": "", "loggedOn": today.isoformat()},
                {"date": today.isoformat(), "person": "wilco", "node": "x", "hours": 1.5,
                 "so": True, "tag": "build", "note": "", "loggedOn": today.isoformat()},
                {"date": today.isoformat(), "person": "sam", "node": "x", "hours": 2.0,
                 "so": False, "tag": "", "note": "", "loggedOn": today.isoformat()},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_hours.collect(manifest)
            self.assertEqual(data["summary"]["perPersonCount"], {"wilco": 2, "sam": 1})
            html = tpl_hours.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn('<div class="group" data-owner="wilco">', html)
            self.assertIn("2 entries", html)
            self.assertIn('<div class="group" data-owner="sam">', html)
            self.assertIn("1 entry<", html)


class TestScrollNotGrow(unittest.TestCase):
    """Item 1.7: activity, roadmap and hours must wrap their unbounded list
    in the fixed-height, internally-scrolling panel-scroll class whether
    there is one row or many, so the panel's own grid footprint never
    grows with content length."""

    def test_activity_panel_scroll_present_for_few_and_many_entries(self):
        for n, label in ((1, "few"), (25, "many")):
            with tempfile.TemporaryDirectory() as tmp:
                node_dir = pathlib.Path(tmp)
                _write_manifest(node_dir)
                (node_dir / "_log").mkdir()
                for i in range(n):
                    (node_dir / "_log" / f"entry-{i}.md").write_text("x", encoding="utf-8")
                manifest = render_board.load_manifest(node_dir)
                state, data = tpl_activity.collect(manifest)
                html = tpl_activity.render(
                    data, render_board.all_labels_for("en", manifest["labels"]), state
                )
                self.assertIn("panel-scroll", html, f"{label}: {n} entries")

    def test_roadmap_panel_scroll_present_for_few_and_many_priorities(self):
        for n, label in ((1, "few"), (25, "many")):
            with tempfile.TemporaryDirectory() as tmp:
                node_dir = pathlib.Path(tmp)
                _write_manifest(node_dir)
                (node_dir / "02_roadmap").mkdir()
                lines = "\n".join(f"{i + 1}. Priority {i}" for i in range(n))
                (node_dir / "02_roadmap" / "roadmap.md").write_text(
                    f"# roadmap\n\n{lines}\n", encoding="utf-8"
                )
                manifest = render_board.load_manifest(node_dir)
                state, data = tpl_roadmap.collect(manifest)
                html = tpl_roadmap.render(
                    data, render_board.all_labels_for("en", manifest["labels"]), state
                )
                self.assertIn("panel-scroll", html, f"{label}: {n} priorities")

    def test_hours_panel_scroll_present_for_few_and_many_people(self):
        for n, label in ((1, "few"), (12, "many")):
            with tempfile.TemporaryDirectory() as tmp:
                node_dir = pathlib.Path(tmp)
                _write_manifest(node_dir, settings={
                    "planningHorizonWeeks": 2, "languages": ["en"], "timeRegistration": True,
                })
                today = date.today()
                _write_hours_month(node_dir, today.strftime("%Y-%m"), [
                    {"date": today.isoformat(), "person": f"person-{i}", "node": "x",
                     "hours": 1.0, "so": False, "tag": "", "note": "",
                     "loggedOn": today.isoformat()}
                    for i in range(n)
                ])
                manifest = render_board.load_manifest(node_dir)
                state, data = tpl_hours.collect(manifest)
                html = tpl_hours.render(
                    data, render_board.all_labels_for("en", manifest["labels"]), state
                )
                self.assertIn("panel-scroll", html, f"{label}: {n} people")


class TestAsOfCommand(unittest.TestCase):
    """Item 1.9: render a node as it existed on a past date via a disposable
    local clone (never a worktree, never a mutation of the real repo).
    Builds a real throwaway git repo with two dated commits so the test
    exercises the actual git plumbing, not a mock."""

    def _git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
                    GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")
        subprocess.run(["git", "-C", str(cwd), *args], check=True,
                        capture_output=True, text=True, env=env)

    def _git_commit_dated(self, cwd, message, iso_datetime):
        # git log --before filters on committer date; --date alone only
        # sets the author date, so both must be forced or the commit
        # appears "now" to any --before query regardless of --date.
        env = dict(
            os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com",
            GIT_AUTHOR_DATE=iso_datetime, GIT_COMMITTER_DATE=iso_datetime,
        )
        subprocess.run(["git", "-C", str(cwd), "commit", "-q", "-m", message],
                        check=True, capture_output=True, text=True, env=env)

    def test_as_of_renders_a_past_commit_and_differs_from_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = pathlib.Path(tmp) / "repo"
            repo_dir.mkdir()
            self._git(repo_dir, "init", "-q", "-b", "main")
            _write_manifest(repo_dir, type="holding")
            (repo_dir / "02_roadmap").mkdir()
            (repo_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. First priority\n", encoding="utf-8"
            )
            self._git(repo_dir, "add", "-A")
            self._git_commit_dated(repo_dir, "first", "2026-09-01T10:00:00")

            (repo_dir / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. First priority\n2. Second priority, added later\n",
                encoding="utf-8",
            )
            self._git(repo_dir, "add", "-A")
            self._git_commit_dated(repo_dir, "second", "2026-09-10T10:00:00")

            rc_early = render_board.main([
                "as-of", str(repo_dir), "2026-09-01",
                "--out", str(repo_dir / "_board" / "early.html"),
            ])
            rc_late = render_board.main([
                "as-of", str(repo_dir), "2026-09-10",
                "--out", str(repo_dir / "_board" / "late.html"),
            ])
            self.assertEqual(rc_early, 0)
            self.assertEqual(rc_late, 0)

            early = (repo_dir / "_board" / "early.html").read_text(encoding="utf-8")
            late = (repo_dir / "_board" / "late.html").read_text(encoding="utf-8")
            self.assertIn("First priority", early)
            self.assertNotIn("Second priority", early)
            self.assertIn("Second priority", late)
            # The real repo's own tracked files, working tree and branch must
            # be untouched: as-of only ever acts inside a disposable clone.
            # (--out deliberately writes new, untracked files into _board\,
            # so only tracked-file status is checked here.)
            self.assertEqual(
                subprocess.run(["git", "-C", str(repo_dir), "status", "--porcelain",
                                 "--untracked-files=no"],
                                capture_output=True, text=True, check=True).stdout.strip(),
                "",
            )
            self.assertEqual(
                subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip(),
                "main",
            )

    def test_as_of_before_first_commit_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = pathlib.Path(tmp) / "repo"
            repo_dir.mkdir()
            self._git(repo_dir, "init", "-q", "-b", "main")
            _write_manifest(repo_dir, type="holding")
            self._git(repo_dir, "add", "-A")
            self._git_commit_dated(repo_dir, "first", "2026-09-10T10:00:00")

            with self.assertRaises(ValueError):
                render_board.main(["as-of", str(repo_dir), "2026-01-01"])


class TestReceiptsPanel(unittest.TestCase):
    def _write_flow_log(self, node_dir, project, slug, lines):
        flow_dir = node_dir / "flows" / project / slug
        flow_dir.mkdir(parents=True)
        (flow_dir / "flow-log.ndjson").write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )

    def test_empty_when_no_flows_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir)
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_EMPTY)

    def test_unavailable_when_declared_flows_root_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="no_such_flows_dir")
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_UNAVAILABLE)

    def test_empty_when_flows_root_exists_but_no_gate_decided_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            (node_dir / "flows").mkdir()
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_EMPTY)

    def test_normal_counts_decided_within_each_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            now = datetime.now(timezone.utc)
            recent = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            mid = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
            old = (now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_flow_log(node_dir, "proj", "a", [
                {"ts": recent, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=approve"},
            ])
            self._write_flow_log(node_dir, "proj", "b", [
                {"ts": mid, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=approve"},
            ])
            self._write_flow_log(node_dir, "proj", "c", [
                {"ts": old, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=approve"},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_NORMAL)
            self.assertEqual(data["window_30"]["decided"], 1)
            self.assertEqual(data["window_90"]["decided"], 2)  # recent + mid, not old

    def test_a_gate_decided_after_a_blocked_entry_counts_as_overruled(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t2 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_flow_log(node_dir, "proj", "clean", [
                {"ts": t1, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=approve"},
            ])
            self._write_flow_log(node_dir, "proj", "rocky", [
                {"ts": t1, "action": "driver_fence", "outcome": "blocked",
                 "detail": "execute_error: something broke"},
                {"ts": t2, "action": "gate_decided", "outcome": "pass",
                 "detail": "G4 verdict=ship"},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_NORMAL)
            self.assertEqual(data["window_30"]["decided"], 2)
            self.assertEqual(data["window_30"]["overruled"], 1)  # only "rocky"'s gate
            html = tpl_receipts.render(
                data, render_board.all_labels_for("en", manifest["labels"]), state
            )
            self.assertIn("overruled", html)

    def test_reject_after_a_blocked_entry_is_not_an_override(self):
        """R06: even rejecting the work must not increase the override
        count; the human honoured the finding instead of overriding it."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t2 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_flow_log(node_dir, "proj", "rejected", [
                {"ts": t1, "action": "driver_fence", "outcome": "blocked",
                 "detail": "execute_error: something broke"},
                {"ts": t2, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=reject"},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_NORMAL)
            self.assertEqual(data["window_30"]["decided"], 1)
            self.assertEqual(data["window_30"]["overruled"], 0)

    def test_fail_then_fix_before_deciding_is_not_an_override(self):
        """R06: a trouble that was actually resolved (a later passing entry)
        before the decision must not count as an override either."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t2 = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t3 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_flow_log(node_dir, "proj", "fixed", [
                {"ts": t1, "action": "test_run", "outcome": "fail",
                 "detail": "2 failing"},
                {"ts": t2, "action": "test_run", "outcome": "pass",
                 "detail": "all green"},
                {"ts": t3, "action": "gate_decided", "outcome": "pass",
                 "detail": "G4 verdict=ship"},
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_NORMAL)
            self.assertEqual(data["window_30"]["decided"], 1)
            self.assertEqual(data["window_30"]["overruled"], 0)

    def test_an_old_resolved_decision_does_not_taint_a_later_unrelated_one(self):
        """R06: the trouble window resets after every decision, so a
        problem already decided on in an earlier round is not charged to a
        later, unrelated decision that had no trouble of its own."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t2 = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            t3 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_flow_log(node_dir, "proj", "two_rounds", [
                {"ts": t1, "action": "driver_fence", "outcome": "blocked",
                 "detail": "execute_error: something broke"},
                {"ts": t2, "action": "gate_decided", "outcome": "pass",
                 "detail": "G1 verdict=ship"},  # round 1: overruled
                {"ts": t3, "action": "gate_decided", "outcome": "pass",
                 "detail": "G4 verdict=ship"},  # round 2: no new trouble
            ])
            manifest = render_board.load_manifest(node_dir)
            state, data = tpl_receipts.collect(manifest)
            self.assertEqual(state, tpl_receipts.STATE_NORMAL)
            self.assertEqual(data["window_30"]["decided"], 2)
            self.assertEqual(data["window_30"]["overruled"], 1)

    # --- sprint 2 item 1.5b flow B: by_confidence split ---------------------
    _NOFIELD = object()

    @staticmethod
    def _ts(days_ago):
        t = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def _opened(cls, ts, conf=_NOFIELD):
        line = {"ts": ts, "action": "gate_opened", "outcome": "info",
                "detail": "G1 token T"}
        if conf is not cls._NOFIELD:
            line["agent_confidence"] = conf
            line["agent_confidence_source"] = "rule"
        return line

    @staticmethod
    def _decided(ts, verdict="approve"):
        return {"ts": ts, "action": "gate_decided", "outcome": "pass",
                "detail": f"G1 verdict={verdict}"}

    @staticmethod
    def _expect(**given):
        out = {"high": (0, 0), "medium": (0, 0), "low": (0, 0), "unrated": (0, 0)}
        out.update(given)
        return out

    @staticmethod
    def _split(window):
        return {b: (v["decided"], v["overruled"])
                for b, v in window["by_confidence"].items()}

    def _assert_buckets_sum(self, data):
        for name in ("window_30", "window_90"):
            if name not in data:
                continue
            window = data[name]
            buckets = window["by_confidence"].values()
            self.assertEqual(sum(b["decided"] for b in buckets), window["decided"], name)
            self.assertEqual(sum(b["overruled"] for b in buckets), window["overruled"], name)

    def _collect_logs(self, logs):
        """logs: {slug: [log lines]}, all under one project. Returns
        (state, data) and checks, for every fixture, that the four buckets
        sum to the window totals."""
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, flows="flows")
            for slug, lines in logs.items():
                self._write_flow_log(node_dir, "proj", slug, lines)
            state, data = tpl_receipts.collect(render_board.load_manifest(node_dir))
        self._assert_buckets_sum(data)
        return state, data

    def test_decision_pairs_with_the_confidence_of_its_gate_opened(self):
        state, data = self._collect_logs({"a": [
            self._opened(self._ts(3), "high"),
            self._decided(self._ts(2)),
        ]})
        self.assertEqual(state, tpl_receipts.STATE_NORMAL)
        for name in ("window_30", "window_90"):
            self.assertEqual(data[name]["decided"], 1)
            self.assertEqual(data[name]["overruled"], 0)
            self.assertEqual(list(data[name]["by_confidence"]),
                             ["high", "medium", "low", "unrated"])
            self.assertEqual(self._split(data[name]), self._expect(high=(1, 0)))

    def test_two_gates_in_one_log_each_take_their_own_confidence(self):
        _, data = self._collect_logs({"a": [
            self._opened(self._ts(5), "low"),
            self._decided(self._ts(4)),
            self._opened(self._ts(3), "high"),
            self._decided(self._ts(2)),
        ]})
        self.assertEqual(self._split(data["window_30"]),
                         self._expect(low=(1, 0), high=(1, 0)))

    def test_decided_takes_the_latest_opened_line_before_it(self):
        _, data = self._collect_logs({"a": [
            self._opened(self._ts(5), "high"),
            self._opened(self._ts(4), "low"),
            self._decided(self._ts(3)),
        ]})
        self.assertEqual(self._split(data["window_30"]), self._expect(low=(1, 0)))

    def test_pairing_never_crosses_flow_logs(self):
        # Logs are walked in sorted path order: a, b, y, z. "b" and "y" have a
        # decision and no opened line of their own; "a" opened before them and
        # "z" opened after them, both with high. Neither may leak across.
        state, data = self._collect_logs({
            "a": [self._opened(self._ts(6), "high")],
            "b": [self._decided(self._ts(5))],
            "y": [self._decided(self._ts(4))],
            "z": [self._opened(self._ts(3), "high")],
        })
        self.assertEqual(state, tpl_receipts.STATE_NORMAL)
        self.assertEqual(data["window_30"]["decided"], 2)
        self.assertEqual(self._split(data["window_30"]), self._expect(unrated=(2, 0)))

    def test_missing_field_reads_as_unrated(self):
        _, data = self._collect_logs({"a": [
            self._opened(self._ts(3)),  # old style line, no agent_confidence key
            self._decided(self._ts(2)),
        ]})
        self.assertEqual(self._split(data["window_30"]), self._expect(unrated=(1, 0)))

    def test_decision_with_no_earlier_gate_opened_reads_as_unrated(self):
        _, data = self._collect_logs({
            "alone": [self._decided(self._ts(3))],
            "late": [self._decided(self._ts(2)),
                     self._opened(self._ts(1), "high")],  # opened AFTER the decision
        })
        self.assertEqual(data["window_30"]["decided"], 2)
        self.assertEqual(self._split(data["window_30"]), self._expect(unrated=(2, 0)))

    def test_invalid_values_read_as_unrated_without_raising(self):
        for value in ("High", "HIGH", "", "none", 7, ["high"], None, True, {"a": 1}):
            with self.subTest(value=repr(value)):
                _, data = self._collect_logs({"a": [
                    self._opened(self._ts(3), value),
                    self._decided(self._ts(2)),
                ]})
                self.assertEqual(self._split(data["window_30"]),
                                 self._expect(unrated=(1, 0)))

    def test_reject_with_confidence_is_never_overruled_but_ship_is(self):
        def log(verdict):
            return {"a": [
                self._opened(self._ts(4), "low"),
                {"ts": self._ts(3), "action": "driver_fence", "outcome": "blocked",
                 "detail": "execute_error: something broke"},
                self._decided(self._ts(2), verdict),
            ]}
        _, rejected = self._collect_logs(log("reject"))
        self.assertEqual(rejected["window_30"]["decided"], 1)
        self.assertEqual(rejected["window_30"]["overruled"], 0)
        self.assertEqual(self._split(rejected["window_30"]), self._expect(low=(1, 0)))
        _, shipped = self._collect_logs(log("ship"))
        self.assertEqual(shipped["window_30"]["overruled"], 1)
        self.assertEqual(self._split(shipped["window_30"]), self._expect(low=(1, 1)))

    def test_both_windows_split_by_confidence(self):
        state, data = self._collect_logs({
            "a": [self._opened(self._ts(6), "high"), self._decided(self._ts(5))],
            "b": [self._opened(self._ts(61), "medium"), self._decided(self._ts(60))],
            "c": [self._opened(self._ts(201), "low"), self._decided(self._ts(200))],
            "d": [self._opened(self._ts(11)), self._decided(self._ts(10))],
        })
        self.assertEqual(state, tpl_receipts.STATE_NORMAL)
        self.assertEqual(data["window_30"]["decided"], 2)
        self.assertEqual(self._split(data["window_30"]),
                         self._expect(high=(1, 0), unrated=(1, 0)))
        self.assertEqual(data["window_90"]["decided"], 3)  # the 200 day one is in neither
        self.assertEqual(self._split(data["window_90"]),
                         self._expect(high=(1, 0), medium=(1, 0), unrated=(1, 0)))

    def test_buckets_sum_to_totals_on_a_mixed_fixture(self):
        _, data = self._collect_logs({
            "m1": [
                self._opened(self._ts(9), "high"),
                {"ts": self._ts(8.5), "action": "driver_fence", "outcome": "blocked",
                 "detail": "execute_error: something broke"},
                self._decided(self._ts(8), "ship"),       # high, overruled
                self._opened(self._ts(7), "low"),
                self._decided(self._ts(6), "approve"),    # low, trouble window reset
            ],
            "m2": [
                self._opened(self._ts(5), "High"),
                self._decided(self._ts(4), "approve"),    # invalid value: unrated
            ],
            "m3": [self._decided(self._ts(3), "reject")],  # no opened line: unrated
            "m4": [
                self._opened(self._ts(2), "medium"),
                {"ts": self._ts(1.9), "action": "test_run", "outcome": "fail",
                 "detail": "2 failing"},
                self._decided(self._ts(1.8), "proceed"),  # medium, overruled
            ],
        })
        self.assertEqual(data["window_30"]["decided"], 5)
        self.assertEqual(data["window_30"]["overruled"], 2)
        self.assertEqual(self._split(data["window_30"]), self._expect(
            high=(1, 1), medium=(1, 1), low=(1, 0), unrated=(2, 0)))

    def test_second_decision_after_one_opened_reuses_its_confidence(self):
        _, data = self._collect_logs({"a": [
            self._opened(self._ts(4), "high"),
            self._decided(self._ts(3)),
            self._decided(self._ts(2)),  # no new gate_opened in between
        ]})
        self.assertEqual(self._split(data["window_30"]), self._expect(high=(2, 0)))

    def test_unparsable_decision_timestamp_is_in_no_bucket(self):
        state, data = self._collect_logs({"a": [
            self._opened(self._ts(3), "high"),
            {"ts": "not a timestamp", "action": "gate_decided", "outcome": "pass",
             "detail": "G1 verdict=approve"},
        ]})
        self.assertEqual(state, tpl_receipts.STATE_EMPTY)
        self.assertEqual(data["window_30"]["decided"], 0)
        self.assertEqual(self._split(data["window_30"]), self._expect())

    def test_by_confidence_has_four_zero_buckets_when_state_is_empty(self):
        state, data = self._collect_logs({"a": [
            self._opened(self._ts(201), "high"),
            self._decided(self._ts(200)),  # older than both windows
        ]})
        self.assertEqual(state, tpl_receipts.STATE_EMPTY)
        for name in ("window_30", "window_90"):
            self.assertEqual(list(data[name]["by_confidence"]),
                             ["high", "medium", "low", "unrated"])
            self.assertEqual(self._split(data[name]), self._expect())

    def test_a_gate_opened_with_blocked_outcome_still_opens_trouble(self):
        """The override rule is unchanged: pairing reads a gate_opened line
        but must not stop the rule from also seeing its outcome."""
        opened = self._opened(self._ts(3), "medium")
        opened["outcome"] = "blocked"
        _, data = self._collect_logs({"a": [opened, self._decided(self._ts(2), "ship")]})
        self.assertEqual(data["window_30"]["overruled"], 1)
        self.assertEqual(self._split(data["window_30"]), self._expect(medium=(1, 1)))

    def test_a_gate_opened_line_does_not_clear_earlier_trouble(self):
        """Guard for the unchanged override rule: trouble raised before a
        gate_opened line is still open when the decision goes ahead."""
        _, data = self._collect_logs({"a": [
            {"ts": self._ts(4), "action": "driver_fence", "outcome": "blocked",
             "detail": "execute_error: something broke"},
            self._opened(self._ts(3), "high"),
            self._decided(self._ts(2), "ship"),
        ]})
        self.assertEqual(data["window_30"]["decided"], 1)
        self.assertEqual(data["window_30"]["overruled"], 1)
        self.assertEqual(self._split(data["window_30"]), self._expect(high=(1, 1)))

    def test_whitespace_padded_values_read_as_unrated_not_coerced(self):
        for value in (" high", "high ", "high\n"):
            with self.subTest(value=repr(value)):
                _, data = self._collect_logs({"a": [
                    self._opened(self._ts(3), value),
                    self._decided(self._ts(2)),
                ]})
                self.assertEqual(self._split(data["window_30"]),
                                 self._expect(unrated=(1, 0)))

    # --- flow B render -------------------------------------------------------
    @staticmethod
    def _render_labels():
        return {
            "receipts": "Receipts", "decided": "decided", "overruled": "overruled",
            "last_30_days": "Last 30 days", "last_90_days": "Last 90 days",
            "confidence_high": "high-word", "confidence_medium": "medium-word",
            "confidence_low": "low-word", "confidence_unrated": "unrated-word",
        }

    @staticmethod
    def _window(decided, overruled, **given):
        by_confidence = {b: {"decided": 0, "overruled": 0}
                         for b in ("high", "medium", "low", "unrated")}
        for bucket, (d, o) in given.items():
            by_confidence[bucket] = {"decided": d, "overruled": o}
        return {"decided": decided, "overruled": overruled,
                "by_confidence": by_confidence}

    def _render_data(self):
        return {
            "window_30": self._window(4, 1, high=(2, 1), low=(1, 0), unrated=(1, 0)),
            "window_90": self._window(7, 2, high=(3, 1), medium=(1, 0), low=(1, 1),
                                      unrated=(2, 0)),
        }

    @staticmethod
    def _row(word, decided, overruled):
        return (f'<li>{word}: <strong>{decided}</strong> decided, '
                f'<strong>{overruled}</strong> overruled</li>')

    def test_render_shows_four_confidence_rows_per_window_in_fixed_order(self):
        html = tpl_receipts.render(
            self._render_data(), self._render_labels(), tpl_receipts.STATE_NORMAL)
        # The per-window total rows stay, now followed by the nested rows.
        self.assertIn('<li>Last 30 days: <strong>4</strong> decided, '
                      '<strong>1</strong> overruled<ul class="rows">', html)
        self.assertIn('<li>Last 90 days: <strong>7</strong> decided, '
                      '<strong>2</strong> overruled<ul class="rows">', html)
        part_30, part_90 = html.split("Last 90 days")
        expected = {
            "30": (part_30, [("high-word", 2, 1), ("medium-word", 0, 0),
                             ("low-word", 1, 0), ("unrated-word", 1, 0)]),
            "90": (part_90, [("high-word", 3, 1), ("medium-word", 1, 0),
                             ("low-word", 1, 1), ("unrated-word", 2, 0)]),
        }
        for window, (part, rows) in expected.items():
            with self.subTest(window=window):
                positions = []
                for word, decided, overruled in rows:  # zero rows included
                    row = self._row(word, decided, overruled)
                    self.assertIn(row, part)
                    positions.append(part.index(row))
                self.assertEqual(positions, sorted(positions))  # fixed order

    def test_render_body_sits_inside_one_panel_scroll_wrapper(self):
        html = tpl_receipts.render(
            self._render_data(), self._render_labels(), tpl_receipts.STATE_NORMAL)
        self.assertEqual(html.count('class="panel-scroll"'), 1)
        self.assertIn('<div class="body"><div class="panel-scroll"><ul class="rows">', html)
        self.assertLess(html.index('class="panel-scroll"'), html.index("Last 30 days"))
        self.assertTrue(html.endswith("</ul></div></div></section>"))

    def test_render_falls_back_to_key_names_without_confidence_labels(self):
        html = tpl_receipts.render(self._render_data(), {}, tpl_receipts.STATE_NORMAL)
        for key in ("confidence_high", "confidence_medium", "confidence_low",
                    "confidence_unrated"):
            self.assertEqual(html.count(key + ":"), 2, key)  # one per window

    def test_render_empty_and_unavailable_states_are_unchanged(self):
        empty = tpl_receipts.render({}, {}, tpl_receipts.STATE_EMPTY)
        self.assertEqual(
            empty,
            '<section class="panel" data-panel="receipts"><div class="head">'
            '<h2 data-i18n="receipts">receipts</h2></div><div class="body">'
            '<p class="empty"><span class="msg" data-i18n="state_empty">state_empty'
            '</span></p></div></section>')
        unavailable = tpl_receipts.render({}, {}, tpl_receipts.STATE_UNAVAILABLE)
        self.assertEqual(
            unavailable,
            '<section class="panel" data-panel="receipts"><div class="head">'
            '<h2 data-i18n="receipts">receipts</h2></div><div class="body">'
            '<p class="empty warn"><span class="msg" data-i18n="state_unavailable">'
            'state_unavailable</span></p></div></section>')
        for html in (empty, unavailable):
            self.assertNotIn("panel-scroll", html)

    def test_render_end_to_end_from_collect(self):
        state, data = self._collect_logs({"a": [
            self._opened(self._ts(3), "high"),
            self._decided(self._ts(2)),
        ]})
        html = tpl_receipts.render(data, self._render_labels(), state)
        self.assertEqual(html.count(self._row("high-word", 1, 0)), 2)  # both windows
        self.assertEqual(html.count(self._row("unrated-word", 0, 0)), 2)

    def test_render_escapes_confidence_words(self):
        labels = self._render_labels()
        labels["confidence_high"] = "<b>h&i</b>"
        html = tpl_receipts.render(self._render_data(), labels, tpl_receipts.STATE_NORMAL)
        self.assertIn("&lt;b&gt;h&amp;i&lt;/b&gt;", html)
        self.assertNotIn("<b>h", html)

    def test_every_language_renders_the_four_confidence_words(self):
        keys = ("confidence_high", "confidence_medium", "confidence_low",
                "confidence_unrated")
        en = render_board.all_labels_for("en", {})
        self.assertEqual([en.get(k) for k in keys], ["high", "medium", "low", "unrated"])
        for tag in ("en", "nl", "de", "es", "fi", "fr", "nb"):
            with self.subTest(lang=tag):
                labels = render_board.all_labels_for(tag, {})
                words = [labels.get(k) for k in keys]
                for word in words:
                    self.assertTrue(word)  # present and non-empty
                    self.assertLessEqual(len(word), 14)
                html = tpl_receipts.render(
                    self._render_data(), labels, tpl_receipts.STATE_NORMAL)
                for word in words:
                    self.assertIn(word, html)


class TestDateparse(unittest.TestCase):
    def test_find_date_iso(self):
        self.assertEqual(dateparse.find_date("due 2026-09-27, ship it"), date(2026, 9, 27))

    def test_find_date_d_mon_yyyy(self):
        self.assertEqual(dateparse.find_date("PoC starts 1 Nov 2026"), date(2026, 11, 1))

    def test_find_date_none_when_no_date(self):
        self.assertIsNone(dateparse.find_date("no date in this prose at all"))

    def test_extract_deadline_rows_skips_unparseable(self):
        text = (
            "| Date | Item | Notes |\n|---|---|---|\n"
            "| GATE B2 - no hard date | Something | x |\n"
            "| 2026-09-27 | Real row | y |\n"
        )
        rows = dateparse.extract_deadline_rows(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], date(2026, 9, 27))

    def test_extract_roadmap_first_list_only(self):
        text = (
            "# roadmap\n\n"
            "```\n1. In the fence\n2. Also fenced\n```\n\n"
            "prose in between\n\n"
            "Active build:\n1. Later list\n2. Ignored\n"
        )
        priorities, backlog = dateparse.extract_roadmap(text)
        self.assertEqual([p["text"] for p in priorities], ["In the fence", "Also fenced"])
        self.assertEqual(backlog, [])

    def test_extract_roadmap_backlog_any_suffix(self):
        text = "# roadmap\n\n1. One\n\n## Backlog (v1.x)\n\n- item a\n"
        priorities, backlog = dateparse.extract_roadmap(text)
        self.assertEqual(backlog, ["item a"])


class TestTreeCommand(unittest.TestCase):
    def test_tree_renders_root_and_two_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp) / "root"
            _write_manifest(root_dir, type="holding", children=["child-a", "child-b"])
            _write_manifest(root_dir / "child-a")
            _write_manifest(root_dir / "child-b")
            rc = render_board.main(["tree", str(root_dir)])
            self.assertEqual(rc, 0)
            for node_dir in (root_dir, root_dir / "child-a", root_dir / "child-b"):
                self.assertTrue((node_dir / "_board" / "board.html").is_file())
                self.assertTrue((node_dir / "_board" / "board.json").is_file())

    def test_tree_reports_a_declared_child_with_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp) / "root"
            _write_manifest(root_dir, type="holding", children=["ghost"])
            rc = render_board.main(["tree", str(root_dir)])
            self.assertEqual(rc, 0)  # a missing declared child is not a render failure

    def test_a_malformed_children_entry_fails_that_node_not_the_whole_walk(self):
        """Found live: a znd-skills scratch fixture declared a `children`
        entry as a dict, not a path string, crashing pathlib.Path(child)
        before this was caught and reported like any other render failure."""
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp) / "root"
            _write_manifest(root_dir, type="holding", children=[{"name": "not-a-path"}])
            rc = render_board.main(["tree", str(root_dir)])
            self.assertEqual(rc, 1)  # reported as a failure, not a crash
            self.assertTrue((root_dir / "_board" / "board.html").is_file())  # the node itself still rendered


class TestHubShapedNodeAllNormal(unittest.TestCase):
    def test_all_five_real_panels_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "10_holding"
            _write_manifest(root, type="holding", children=["child-project"])

            soon = (date.today() + timedelta(days=2)).isoformat()
            (root / "DEADLINES.md").write_text(
                f"# DEADLINES\n\n| Date | Item | Notes |\n|---|---|---|\n"
                f"| {soon} | Ship the thing | note |\n",
                encoding="utf-8",
            )
            (root / "02_roadmap").mkdir()
            (root / "02_roadmap" / "roadmap.md").write_text(
                "# roadmap\n\n1. Priority one\n2. Priority two\n\n"
                "## Backlog\n\n- idea one\n",
                encoding="utf-8",
            )
            (root / "_log").mkdir()
            (root / "_log" / "2026-09-11_entry.md").write_text(
                "did a thing", encoding="utf-8"
            )
            (root / "00_funnel" / "02_scored").mkdir(parents=True)
            stage_dir = root / "00_funnel" / "01_leads"
            stage_dir.mkdir(parents=True)
            (stage_dir / "acme.md").write_text("a lead", encoding="utf-8")

            child_dir = root / "child-project"
            _write_manifest(child_dir, type="project")
            (child_dir / "_board").mkdir()
            (child_dir / "_board" / "board.json").write_text(
                json.dumps({"displayName": "Child Project"}), encoding="utf-8"
            )

            manifest = render_board.load_manifest(root)
            panel_results = render_board.collect_all_panels(manifest)
            states = {k: v[0] for k, v in panel_results.items()}
            for key in ("waiting_on_you", "next_weeks", "roadmap", "children", "activity", "funnel"):
                self.assertEqual(states[key], "normal", f"{key}: {states[key]}")


if __name__ == "__main__":
    unittest.main()
