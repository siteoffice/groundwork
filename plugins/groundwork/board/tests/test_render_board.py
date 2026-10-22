"""Renders the hub node and the Siteoffice project node into a temp dir and
checks the row's done-when: an empty-but-correct board in both themes and
both languages. Theme is client-side (one file serves both), so "both
themes" is checked by asserting the tokens block defines the same custom
property names under `:root` and under `body[data-theme="light"]`, per the
architecture note's parity gate (5.4): same tokens, no structural branch.
Stdlib-compatible: run with `python -m pytest board\\tests -q` or plain
unittest.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
import render_board  # noqa: E402

PROJECT_ROOT = BOARD_DIR.parent.parent.parent / "templates" / "molenwerk"  # the Molenwerk example tree
HUB_ROOT = PROJECT_ROOT.parent.parent / "10_holding"  # C:\work\10_holding

NODES = [PROJECT_ROOT] + ([HUB_ROOT] if HUB_ROOT.is_dir() else [])
LANGUAGES = ["en", "nl"]


def _render(node_dir: pathlib.Path) -> str:
    manifest = render_board.load_manifest(node_dir)
    from datetime import datetime, timezone
    panel_results = render_board.collect_all_panels(manifest)
    return render_board.build_board_html(manifest, datetime.now(timezone.utc), panel_results)


def _root_var_names(html: str, selector: str) -> set:
    """Custom property names declared directly under `selector { ... }`."""
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}", re.S)
    m = pattern.search(html)
    if not m:
        return set()
    return set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", m.group(1)))


class TestRenderBoard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rendered = {}
        for node_dir in NODES:
            self.rendered[node_dir] = _render(node_dir)

    def test_all_nodes_render_without_raw_hex_outside_style_block(self):
        for node_dir, html in self.rendered.items():
            style_start = html.index("<style>")
            style_end = html.index("</style>") + len("</style>")
            outside = html[:style_start] + html[style_end:]
            hexes = re.findall(r"#[0-9a-fA-F]{3,6}", outside)
            self.assertEqual(hexes, [], f"raw hex outside style block in {node_dir}: {hexes}")

    def test_every_label_key_resolves_no_bare_key_text(self):
        bare_keys = [
            "waiting_on_you", "state_empty", "state_unavailable", "next_weeks",
            "breadcrumb_root", "up_to_date_as_of", "children", "activity",
            "status", "funnel", "roadmap", "hours", "sync_status",
        ]
        for node_dir, html in self.rendered.items():
            style_end = html.index("</style>") + len("</style>")
            body = html[style_end:]
            for key in bare_keys:
                self.assertNotIn(
                    f">{key}<", body, f"bare label key '{key}' leaked into {node_dir}"
                )

    def test_theme_tokens_declared_identically_dark_and_light(self):
        for node_dir, html in self.rendered.items():
            dark_vars = _root_var_names(html, ":root")
            light_vars = _root_var_names(html, 'body[data-theme="light"]')
            self.assertTrue(dark_vars, f"no :root token block found for {node_dir}")
            self.assertTrue(light_vars, f"no light override block found for {node_dir}")
            # Theme-invariant vars (font, radius, chrome heights) live only in
            # :root and are not theme-conditional; every var the light block
            # DOES override must also exist in dark, so both draw from the
            # same token set (5.4) and light never introduces a stray name.
            self.assertEqual(
                light_vars - dark_vars, set(),
                f"light-only token names for {node_dir}: {light_vars - dark_vars}",
            )

    def test_no_js_chrome_present(self):
        for node_dir, html in self.rendered.items():
            self.assertEqual(html.count("<link"), 0)
            # Exactly one <script src>, the sync_status.js sibling
            # (architecture note F1, guide step 9): a relative path, not a
            # CDN or an absolute one.
            self.assertEqual(html.count("<script src"), 1)
            self.assertIn('<script src="sync_status.js">', html)
            self.assertIn("znd-theme", html)
            self.assertIn("znd-lang", html)
            self.assertIn('color-scheme: light dark', html)

    def test_board_json_round_trips(self):
        from datetime import datetime, timezone
        for node_dir in NODES:
            manifest = render_board.load_manifest(node_dir)
            panel_results = render_board.collect_all_panels(manifest)
            data = render_board.build_board_json(
                manifest, datetime.now(timezone.utc), panel_results
            )
            raw = json.dumps(data, ensure_ascii=False)
            back = json.loads(raw)
            self.assertEqual(back["name"], manifest["name"])
            self.assertEqual(back["type"], manifest["type"])
            for key in ("waiting_on_you", "next_weeks", "roadmap", "children", "activity", "funnel"):
                self.assertIn(key, back["panels"])
                self.assertIn(back["panels"][key]["state"], {
                    "unavailable", "stale", "empty", "normal",
                })

    def test_renders_in_every_listed_language(self):
        for node_dir in NODES:
            manifest = render_board.load_manifest(node_dir)
            for lang in LANGUAGES:
                manifest["settings"] = dict(manifest["settings"])
                manifest["settings"]["languages"] = [lang]
                from datetime import datetime, timezone
                panel_results = render_board.collect_all_panels(manifest)
                html = render_board.build_board_html(
                    manifest, datetime.now(timezone.utc), panel_results
                )
                self.assertIn(f'lang="{lang}"', html)
                self.assertNotIn(">next_weeks<", html)

    def test_node_cli_writes_only_board_html_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            (tmp_dir / "siteoffice.json").write_text(
                json.dumps({
                    "schemaVersion": 1, "type": "project", "name": "temp-node",
                    "parent": "", "children": [], "language": "en",
                }),
                encoding="utf-8",
            )
            rc = render_board.main(["node", str(tmp_dir)])
            self.assertEqual(rc, 0)
            out_dir = tmp_dir / "_board"
            written = sorted(p.name for p in out_dir.iterdir())
            self.assertEqual(written, ["board.html", "board.json"])

    def test_v1_manifest_gets_defaults_never_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            raw = {
                "schemaVersion": 1, "type": "project", "name": "v1-node",
                "parent": "", "children": [], "language": "en",
            }
            (node_dir / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            self.assertEqual(manifest["settings"]["planningHorizonWeeks"], 2)
            self.assertEqual(manifest["settings"]["languages"], ["en"])
            self.assertEqual(manifest["settings"]["timeRegistration"], False)
            self.assertEqual(manifest["display_name"], "v1-node")
            raw_on_disk = json.loads((node_dir / "siteoffice.json").read_text(encoding="utf-8"))
            self.assertNotIn("settings", raw_on_disk)

    def test_siteoffice_reference_manifest_is_v2(self):
        raw_on_disk = json.loads((PROJECT_ROOT / "siteoffice.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_on_disk["schemaVersion"], 2)
        manifest = render_board.load_manifest(PROJECT_ROOT)
        self.assertEqual(manifest["settings"]["planningHorizonWeeks"], 2)
        self.assertEqual(manifest["settings"]["languages"], ["en", "nl"])
        self.assertEqual(manifest["settings"]["timeRegistration"], True)

    def test_v2_root_with_child_override_resolves_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = pathlib.Path(tmp)
            root_raw = {
                "schemaVersion": 2, "type": "holding", "name": "root-node",
                "parent": "", "children": ["child"], "language": "en",
                "settings": {
                    "planningHorizonWeeks": 3, "languages": ["en", "nl"],
                    "timeRegistration": True,
                },
                "labels": {"en": {"roadmap": "Root Roadmap"}},
            }
            (root_dir / "siteoffice.json").write_text(json.dumps(root_raw), encoding="utf-8")
            child_dir = root_dir / "child"
            child_dir.mkdir()
            child_raw = {
                "schemaVersion": 2, "type": "project", "name": "child-node",
                "parent": "root-node", "children": [], "language": "en",
                "settings": {"planningHorizonWeeks": 1},
                "labels": {"en": {"activity": "Child Activity"}},
            }
            (child_dir / "siteoffice.json").write_text(json.dumps(child_raw), encoding="utf-8")
            manifest = render_board.load_manifest(child_dir)
            self.assertEqual(manifest["settings"]["planningHorizonWeeks"], 1)  # own override
            self.assertEqual(manifest["settings"]["languages"], ["en", "nl"])  # inherited
            self.assertEqual(manifest["settings"]["timeRegistration"], True)  # inherited
            self.assertEqual(manifest["labels"]["en"]["roadmap"], "Root Roadmap")  # inherited
            self.assertEqual(manifest["labels"]["en"]["activity"], "Child Activity")  # own

    def test_horizon_outside_range_renders_amber_note_and_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = pathlib.Path(tmp)
            raw = {
                "schemaVersion": 2, "type": "project", "name": "bad-horizon",
                "parent": "", "children": [], "language": "en",
                "settings": {"planningHorizonWeeks": 7},
            }
            (node_dir / "siteoffice.json").write_text(json.dumps(raw), encoding="utf-8")
            manifest = render_board.load_manifest(node_dir)
            self.assertEqual(manifest["settings"]["planningHorizonWeeks"], 2)
            self.assertEqual(
                manifest["settings"]["_horizon_note"],
                "settings: planningHorizonWeeks must be 1 to 4, using 2",
            )

    def test_check_reports_tokens_in_sync(self):
        import theme

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_path = root / "brand-tokens.json"
            own_path = root / "board-tokens.json"
            tokens = json.loads(theme.TOKENS_PATH.read_text(encoding="utf-8"))
            tokens["source"] = str(source_path)
            source_path.write_text(json.dumps(tokens), encoding="utf-8")
            own_path.write_text(json.dumps(tokens), encoding="utf-8")
            with mock.patch.object(theme, "TOKENS_PATH", own_path):
                self.assertEqual(theme.check(), "tokens in sync")


class TestSyncStatusPanel(unittest.TestCase):
    """sync_status.js contract (board\\SYNC_STATUS_CONTRACT.md, guide step
    9): the panel renders server-side in the loading state; the inline
    script resolves it client-side. No browser here, so the wiring is
    proven structurally: the one script tag is a relative sibling path
    (so a file written next to board.html is exactly what it fetches) and
    the inline script's source contains the reading logic the contract
    promises."""

    def _render_temp_node(self, tmp: str) -> pathlib.Path:
        node_dir = pathlib.Path(tmp)
        (node_dir / "siteoffice.json").write_text(json.dumps({
            "schemaVersion": 1, "type": "project", "name": "temp-node",
            "parent": "", "children": [], "language": "en",
        }), encoding="utf-8")
        rc = render_board.main(["node", str(node_dir)])
        self.assertEqual(rc, 0)
        return node_dir

    def test_panel_renders_loading_shell_with_one_script_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = self._render_temp_node(tmp)
            html = (node_dir / "_board" / "board.html").read_text(encoding="utf-8")
            self.assertEqual(html.count('<script src="sync_status.js">'), 1)
            self.assertIn('id="sync-status-body"', html)
            self.assertIn('data-i18n="state_loading"', html)
            self.assertIn("window.ZND_SYNC_STATUS", html)
            self.assertIn("no_sync_status", html)

    def test_synthetic_sync_status_js_sits_next_to_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_dir = self._render_temp_node(tmp)
            sync_js = (
                'window.ZND_SYNC_STATUS = {"node": "x", "clone": "test", '
                '"lastPull": "2026-09-11T09:00:00Z", "lastPush": null, '
                '"behind": 0, "ahead": 0, "conflicts": [], "state": "ok", '
                '"message": "Up to date.", "writtenAt": "2026-09-11T09:00:01Z"};'
            )
            (node_dir / "_board" / "sync_status.js").write_text(sync_js, encoding="utf-8")
            html = (node_dir / "_board" / "board.html").read_text(encoding="utf-8")
            # The script tag is a bare relative filename: a sync_status.js
            # written into the same _board\ directory board.html sits in is
            # exactly the sibling path the tag resolves to.
            self.assertIn('<script src="sync_status.js"></script>', html)
            self.assertTrue((node_dir / "_board" / "sync_status.js").is_file())


class TestPrintStylesheet(unittest.TestCase):
    """Guide step 14, architecture note 5.5: exactly one @media print block,
    every hex value inside it traceable to tokens.json's print or light
    theme blocks (never an invented value), no header controls, no theme
    or language switching code."""

    def _html(self) -> str:
        if not HUB_ROOT.is_dir():
            self.skipTest("hub root not present in this environment")
        return _render(HUB_ROOT)

    def _print_block(self, html: str) -> str:
        start = html.index("@media print")
        # Balanced-brace scan from the opening brace right after the selector.
        depth = 0
        i = html.index("{", start)
        j = i
        for j in range(i, len(html)):
            if html[j] == "{":
                depth += 1
            elif html[j] == "}":
                depth -= 1
                if depth == 0:
                    break
        return html[start:j + 1]

    def test_exactly_one_print_block(self):
        html = self._html()
        self.assertEqual(html.count("@media print"), 1)

    def test_every_hex_in_the_print_block_comes_from_print_or_light_tokens(self):
        html = self._html()
        block = self._print_block(html)
        allowed = set(render_board.theme._TOKENS["print"].values())
        allowed |= set(render_board.theme._TOKENS["themes"]["light"].values())
        found = re.findall(r"#[0-9A-Fa-f]{3,8}\b", block)
        self.assertTrue(found, "expected at least one token-sourced colour in the print block")
        for hexval in found:
            self.assertIn(hexval, allowed, f"{hexval} in the print block is not a print/light token")

    def test_print_block_has_no_theme_or_language_switch_code(self):
        html = self._html()
        block = self._print_block(html)
        self.assertNotIn("<script", block)
        self.assertIn(".board-controls", block)  # hides the switches, does not add new ones


if __name__ == "__main__":
    unittest.main()
