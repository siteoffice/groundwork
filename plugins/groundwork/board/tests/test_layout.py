"""settings.layout: generated default, validation, inheritance and override
(sprint 2 lane 1 row 1.1). Same manifest-loading test style as
test_render_board.py's planningHorizonWeeks tests; run with
`python -m pytest board\\tests -q` from C:\\work\\50_projects\\siteoffice.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import render_board  # noqa: E402


def _write_manifest(node_dir: pathlib.Path, raw: dict) -> None:
    (node_dir / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")


class TestDefaultLayoutGenerated(unittest.TestCase):
    def test_default_layout_for_project_type_matches_default_panel_order(self):
        layout = render_board._default_layout_for("project")
        self.assertEqual(layout["columns"], 4)
        ids = [p["id"] for p in layout["panels"]]
        self.assertEqual(ids, list(render_board._DEFAULT_PANEL_ORDER))
        for i, panel in enumerate(layout["panels"]):
            self.assertEqual(panel["col"], 1)
            self.assertEqual(panel["row"], i + 1)
            self.assertEqual(panel["w"], 4)
            self.assertEqual(panel["h"], 1)

    def test_default_layout_for_holding_and_hub_types_matches_holding_hub_order(self):
        for node_type in ("holding", "hub"):
            layout = render_board._default_layout_for(node_type)
            ids = [p["id"] for p in layout["panels"]]
            self.assertEqual(ids, list(render_board._HOLDING_HUB_PANEL_ORDER))

    def test_node_with_no_layout_key_gets_generated_default_no_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, {
                "schemaVersion": 2, "type": "project", "name": "no-layout-node",
                "parent": "", "children": [], "language": "en",
            })
            manifest = render_board.load_manifest(node_dir)
            self.assertEqual(
                manifest["settings"]["layout"], render_board._default_layout_for("project")
            )
            self.assertNotIn("_layout_note", manifest["settings"])


class TestLayoutInheritanceAndOverride(unittest.TestCase):
    def _root_and_child(self, root_layout=None, child_layout=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root_dir = pathlib.Path(tmp.name)
        root_settings = {"planningHorizonWeeks": 2}
        if root_layout is not None:
            root_settings["layout"] = root_layout
        _write_manifest(root_dir, {
            "schemaVersion": 2, "type": "holding", "name": "root-node",
            "parent": "", "children": ["child"], "language": "en",
            "settings": root_settings,
        })
        child_dir = root_dir / "child"
        child_dir.mkdir()
        child_settings = {}
        if child_layout is not None:
            child_settings["layout"] = child_layout
        _write_manifest(child_dir, {
            "schemaVersion": 2, "type": "project", "name": "child-node",
            "parent": "root-node", "children": [], "language": "en",
            "settings": child_settings,
        })
        return root_dir, child_dir

    def test_child_with_no_own_layout_inherits_root_layout(self):
        root_layout = {
            "columns": 2,
            "panels": [{"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1}],
        }
        _root_dir, child_dir = self._root_and_child(root_layout=root_layout)
        manifest = render_board.load_manifest(child_dir)
        self.assertEqual(manifest["settings"]["layout"], root_layout)

    def test_child_own_layout_fully_replaces_root_layout_not_merged(self):
        root_layout = {
            "columns": 2,
            "panels": [{"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1}],
        }
        child_layout = {
            "columns": 1,
            "panels": [{"id": "hours", "col": 1, "row": 1, "w": 1, "h": 1}],
        }
        _root_dir, child_dir = self._root_and_child(
            root_layout=root_layout, child_layout=child_layout
        )
        manifest = render_board.load_manifest(child_dir)
        self.assertEqual(manifest["settings"]["layout"], child_layout)


class TestLayoutValidation(unittest.TestCase):
    def _load_with_layout(self, raw_layout, node_type="project"):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            _write_manifest(node_dir, {
                "schemaVersion": 2, "type": node_type, "name": "layout-node",
                "parent": "", "children": [], "language": "en",
                "settings": {"layout": raw_layout},
            })
            return render_board.load_manifest(node_dir)

    def test_valid_layout_passes_through_unchanged_no_note(self):
        valid = {
            "columns": 3,
            "panels": [{"id": "hours", "col": 1, "row": 1, "w": 3, "h": 2}],
        }
        manifest = self._load_with_layout(valid)
        self.assertEqual(manifest["settings"]["layout"], valid)
        self.assertNotIn("_layout_note", manifest["settings"])

    def test_malformed_shapes_fall_back_to_default_with_note_never_raise(self):
        malformed_cases = {
            "not_a_dict": ["columns", "panels"],
            "columns_out_of_range": {"columns": 5, "panels": []},
            "panels_not_a_list": {"columns": 2, "panels": {"id": "hours"}},
            "panel_missing_required_field": {
                "columns": 2,
                "panels": [{"id": "hours", "col": 1, "row": 1, "w": 1}],  # no "h"
            },
            "panel_unknown_id": {
                "columns": 2,
                "panels": [{"id": "not_a_panel", "col": 1, "row": 1, "w": 1, "h": 1}],
            },
            "panel_col_out_of_range": {
                "columns": 2,
                "panels": [{"id": "hours", "col": 5, "row": 1, "w": 1, "h": 1}],
            },
            "panel_w_out_of_range": {
                "columns": 2,
                "panels": [{"id": "hours", "col": 1, "row": 1, "w": 0, "h": 1}],
            },
            "columns_is_bool": {"columns": True, "panels": []},
            "columns_is_float": {"columns": 2.0, "panels": []},
            "panel_col_is_bool": {
                "columns": 2,
                "panels": [{"id": "hours", "col": True, "row": 1, "w": 1, "h": 1}],
            },
            "panel_w_is_float": {
                "columns": 2,
                "panels": [{"id": "hours", "col": 1, "row": 1, "w": 1.0, "h": 1}],
            },
            "panel_overflows_declared_columns": {
                "columns": 2,
                "panels": [{"id": "hours", "col": 2, "row": 1, "w": 2, "h": 1}],
            },
        }
        expected_default = render_board._default_layout_for("project")
        for case_name, raw_layout in malformed_cases.items():
            with self.subTest(case=case_name):
                manifest = self._load_with_layout(raw_layout)
                self.assertEqual(manifest["settings"]["layout"], expected_default)
                self.assertEqual(
                    manifest["settings"]["_layout_note"],
                    "settings: layout is malformed, using the generated default",
                )


class TestResolveLayout(unittest.TestCase):
    def test_declared_panels_sorted_row_then_col(self):
        layout = {
            "columns": 2,
            "panels": [
                {"id": "hours", "col": 1, "row": 2, "w": 1, "h": 1},
                {"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1},
                {"id": "funnel", "col": 2, "row": 2, "w": 1, "h": 1},
            ],
        }
        resolved = render_board._resolve_layout(layout)
        declared_ids = [p["id"] for p in resolved if p["id"] in ("hours", "roadmap", "funnel")]
        self.assertEqual(declared_ids[:3], ["roadmap", "hours", "funnel"])

    def test_omitted_panel_keys_appended_collapsed_at_bottom_in_panel_keys_order(self):
        layout = {
            "columns": 2,
            "panels": [
                {"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1},
                {"id": "hours", "col": 1, "row": 2, "w": 1, "h": 1},
                {"id": "funnel", "col": 2, "row": 2, "w": 1, "h": 1},
            ],
        }
        resolved = render_board._resolve_layout(layout)
        ids = [p["id"] for p in resolved]
        # declared ones first (row-then-col order), then every PANEL_KEYS id
        # not declared, in PANEL_KEYS declaration order.
        expected_omitted = [
            k for k in render_board.PANEL_KEYS if k not in {"roadmap", "hours", "funnel"}
        ]
        self.assertEqual(ids, ["roadmap", "hours", "funnel"] + expected_omitted)
        # each omitted panel gets its own row below the last declared row (2),
        # full width (w == columns), one row apart.
        by_id = {p["id"]: p for p in resolved}
        for i, key in enumerate(expected_omitted):
            self.assertEqual(by_id[key]["row"], 3 + i)
            self.assertEqual(by_id[key]["col"], 1)
            self.assertEqual(by_id[key]["w"], layout["columns"])
            self.assertEqual(by_id[key]["h"], 1)

    def test_no_declared_panels_all_panel_keys_appended_in_order(self):
        layout = {"columns": 4, "panels": []}
        resolved = render_board._resolve_layout(layout)
        ids = [p["id"] for p in resolved]
        self.assertEqual(ids, list(render_board.PANEL_KEYS))
        for i, entry in enumerate(resolved):
            self.assertEqual(entry["row"], i + 1)
            self.assertEqual(entry["col"], 1)
            self.assertEqual(entry["w"], 4)


from datetime import datetime, timezone
import re


def _panel_order_in_html(html: str) -> list:
    """Order of grid panels only, read off the grid-placement style
    Task 3 adds. This deliberately does NOT match on bare
    `data-panel="..."` alone: the children and activity strips (rendered
    outside the .panels grid, board\\templates\\children.py and
    activity.py) also carry a data-panel attribute but never go through
    _place_panel_html, so they never get a style="grid-column..." suffix.
    Requiring that suffix in the pattern is what keeps the two strips out
    of this list without needing to locate the matching closing </div> of
    <div class="panels"> by hand (each panel section has its own inner
    </div> tags, so a naive index("</div>", ...) would stop after the
    first panel).
    """
    return re.findall(r'data-panel="([a-z_]+)" style="grid-column', html)


def _build_html_for(node_type: str, layout=None, extra_settings=None):
    with tempfile.TemporaryDirectory() as tmp:
        node_dir = pathlib.Path(tmp)
        settings = {"timeRegistration": True}
        if layout is not None:
            settings["layout"] = layout
        if extra_settings:
            settings.update(extra_settings)
        _write_manifest(node_dir, {
            "schemaVersion": 2, "type": node_type, "name": "wired-node",
            "parent": "", "children": [], "language": "en",
            "settings": settings,
        })
        manifest = render_board.load_manifest(node_dir)
        panel_results = render_board.collect_all_panels(manifest)
        return render_board.build_board_html(manifest, datetime.now(timezone.utc), panel_results)


class TestBuildBoardHtmlUsesResolvedLayout(unittest.TestCase):
    def test_no_layout_key_regression_pin_matches_pre_existing_fixed_order_project(self):
        html = _build_html_for("project")
        self.assertEqual(_panel_order_in_html(html), list(render_board._DEFAULT_PANEL_ORDER))

    def test_no_layout_key_regression_pin_matches_pre_existing_fixed_order_holding(self):
        html = _build_html_for("holding")
        self.assertEqual(_panel_order_in_html(html), list(render_board._HOLDING_HUB_PANEL_ORDER))

    def test_declared_layout_places_panels_and_collapses_omitted_ones(self):
        layout = {
            "columns": 2,
            "panels": [
                {"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1},
                {"id": "hours", "col": 1, "row": 2, "w": 1, "h": 1},
                {"id": "funnel", "col": 2, "row": 2, "w": 1, "h": 1},
            ],
        }
        html = _build_html_for("project", layout=layout)
        expected_omitted = [
            k for k in render_board.PANEL_KEYS if k not in {"roadmap", "hours", "funnel"}
        ]
        self.assertEqual(
            _panel_order_in_html(html),
            ["roadmap", "hours", "funnel"] + expected_omitted,
        )
        self.assertIn(
            'data-panel="roadmap" style="grid-column: 1 / span 2; grid-row: 1 / span 1;"',
            html,
        )
        self.assertIn(
            'data-panel="hours" style="grid-column: 1 / span 1; grid-row: 2 / span 1;"',
            html,
        )
        self.assertIn(
            'data-panel="funnel" style="grid-column: 2 / span 1; grid-row: 2 / span 1;"',
            html,
        )

    def test_malformed_layout_falls_back_and_still_renders(self):
        html = _build_html_for("project", layout={"columns": 99, "panels": []})
        self.assertEqual(_panel_order_in_html(html), list(render_board._DEFAULT_PANEL_ORDER))


class TestPanelsGridBreakpoints(unittest.TestCase):
    def test_style_block_uses_node_declared_columns_at_widest_breakpoint(self):
        html = _build_html_for("project")  # default layout, columns == 4
        style_start = html.index("<style>")
        style_end = html.index("</style>")
        style = html[style_start:style_end]
        self.assertIn(
            "@media (min-width: 1200px) { .panels { grid-template-columns: repeat(4, 1fr); } }",
            style,
        )
        self.assertIn(
            "@media (max-width: 1199px) { .panels .panel { grid-column: 1 / -1 !important; "
            "grid-row: auto !important; } }",
            style,
        )
        self.assertIn(
            '@media (max-width: 699px) { .board-table { display: block; overflow-x: auto; } }',
            style,
        )
        self.assertNotIn("900px", style)

    def test_a_2_column_declared_layout_renders_2_tracks_at_widest_breakpoint(self):
        layout = {"columns": 2, "panels": [{"id": "roadmap", "col": 1, "row": 1, "w": 2, "h": 1}]}
        html = _build_html_for("project", layout=layout)
        style_start = html.index("<style>")
        style_end = html.index("</style>")
        style = html[style_start:style_end]
        self.assertIn(
            "@media (min-width: 1200px) { .panels { grid-template-columns: repeat(2, 1fr); } }",
            style,
        )


class TestDisabledPanelRowCompaction(unittest.TestCase):
    def test_disabled_panel_row_is_removed_not_left_blank(self):
        html = _build_html_for(
            "project",
            layout={
                "columns": 4,
                "panels": [
                    {"id": "roadmap", "col": 1, "row": 1, "w": 4, "h": 1},
                    {"id": "hours", "col": 1, "row": 2, "w": 4, "h": 1},
                    {"id": "sync_status", "col": 1, "row": 3, "w": 4, "h": 1},
                ],
            },
            extra_settings={"timeRegistration": False},
        )
        # hours is disabled (timeRegistration False): it must not render at
        # all, and sync_status must move up to row 2, not stay at row 3
        # leaving row 2 empty.
        self.assertNotIn('data-panel="hours"', html)
        self.assertIn(
            'data-panel="roadmap" style="grid-column: 1 / span 4; grid-row: 1 / span 1;"',
            html,
        )
        self.assertIn(
            'data-panel="sync_status" style="grid-column: 1 / span 4; grid-row: 2 / span 1;"',
            html,
        )

    def test_default_layout_with_hours_disabled_compacts_around_it(self):
        html = _build_html_for("holding", extra_settings={"timeRegistration": False})
        ids = _panel_order_in_html(html)
        self.assertNotIn("hours", ids)
        # rows must be a contiguous 1..N sequence with no gap, N = number of
        # panels that actually rendered.
        rows = []
        for pid in ids:
            m = re.search(
                r'data-panel="%s" style="grid-column: \d+ / span \d+; grid-row: (\d+)' % pid,
                html,
            )
            self.assertIsNotNone(m, pid)
            rows.append(int(m.group(1)))
        self.assertEqual(rows, list(range(1, len(ids) + 1)))

    def test_h_spanning_panel_row_never_compacted_away(self):
        layout = {
            "columns": 4,
            "panels": [
                {"id": "roadmap", "col": 1, "row": 1, "w": 4, "h": 2},
                {"id": "status", "col": 1, "row": 3, "w": 4, "h": 1},
            ],
        }
        html = _build_html_for("project", layout=layout, extra_settings={"timeRegistration": True})
        self.assertIn(
            'data-panel="roadmap" style="grid-column: 1 / span 4; grid-row: 1 / span 2;"',
            html,
        )
        self.assertIn(
            'data-panel="status" style="grid-column: 1 / span 4; grid-row: 3 / span 1;"',
            html,
        )


if __name__ == "__main__":
    unittest.main()
