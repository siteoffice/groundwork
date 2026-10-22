"""Tests for the label files (guide step 14): key parity across all seven
files, the five drafts carry the required provenance fields, and an
overflow check for `de` and `fi` (architecture note, plan scope item 12):
no browser available, so the longest panel-title label per language is
checked against a per-key character budget instead of an actual rendered
width.
"""
from __future__ import annotations

import json
import pathlib
import unittest

LABELS_DIR = pathlib.Path(__file__).resolve().parents[1] / "labels"
ALL_LANGS = ["en", "nl", "de", "fr", "es", "fi", "nb"]
DRAFT_LANGS = ["de", "fr", "es", "fi", "nb"]

# The keys a panel or strip renders as its own title (render_panel_shell,
# render_sync_status_panel, the children/activity strip headers): the ones
# that actually sit inside the fixed-width 4-column grid header row at
# 1200 px and up. A generous budget, not a real measured width: 4 columns
# at 1200 px leaves roughly 240 px of usable header space per panel after
# padding; at the header's ~13 px font, that comfortably holds ~30
# characters of a typical Latin-script label, so 30 is the ceiling checked
# here, not a tight fit.
PANEL_TITLE_KEYS = [
    "status", "waiting_on_you", "next_weeks", "funnel", "roadmap",
    "hours", "sync_status", "children", "activity",
]
PANEL_TITLE_BUDGET = 30

# Review row (2026-09-11 overnight rules, section 3 item 2): the panel-title
# check above was step 14's own scope; the review row extends it to the
# other two fixed-width chrome contexts every panel can show, which a long
# `de`/`fi` string can break just as easily as a title can. Badge words
# render inside a `.badge` pill sized to fit a short status word (badge.run/
# wait/done/halt, board_common CSS): a narrower budget than a panel header.
# Theme/language toggle words render inside the small `.theme-toggle .ti`
# pill in the header, the tightest fixed-width spot on the page.
BADGE_WORD_KEYS = ["green", "amber", "red", "grey"]
BADGE_WORD_BUDGET = 20
TOGGLE_WORD_KEYS = ["theme_system", "theme_light", "theme_dark", "language_switch"]
TOGGLE_WORD_BUDGET = 14
# Body copy (state_* sentences, no_sync_status, breadcrumb_root, skip_to_content,
# up_to_date_as_of): these sit in a paragraph or an off-screen-until-focus
# link, not a fixed-width box, so they wrap freely. Not a fit test, only a
# sanity ceiling against a genuinely runaway string (e.g. a pasted paragraph
# where a one-line sentence belongs).
BODY_COPY_KEYS = [
    "state_unavailable", "state_stale", "state_empty", "state_loading",
    "no_sync_status", "breadcrumb_root", "skip_to_content", "up_to_date_as_of",
]
BODY_COPY_CEILING = 150


def _load(tag: str) -> dict:
    return json.loads((LABELS_DIR / f"{tag}.json").read_text(encoding="utf-8"))


class TestLabelKeyParity(unittest.TestCase):
    def test_every_language_has_every_english_key(self):
        en_keys = set(_load("en")["labels"])
        for tag in ALL_LANGS:
            with self.subTest(lang=tag):
                self.assertEqual(set(_load(tag)["labels"]), en_keys)


class TestDraftProvenance(unittest.TestCase):
    def test_five_languages_are_marked_as_unreviewed_drafts(self):
        for tag in DRAFT_LANGS:
            with self.subTest(lang=tag):
                obj = _load(tag)
                self.assertIn("_draft", obj)
                self.assertIn("2026", obj["_draft"])
                self.assertEqual(obj["reviewedBy"], "")
                self.assertEqual(obj["reviewedOn"], "")
                self.assertEqual(obj["source"], "en")

    def test_en_and_nl_are_not_marked_as_drafts(self):
        for tag in ("en", "nl"):
            with self.subTest(lang=tag):
                self.assertNotIn("_draft", _load(tag))


class TestPanelTitleOverflow(unittest.TestCase):
    def test_de_and_fi_panel_titles_fit_the_budget(self):
        for tag in ("de", "fi"):
            labels = _load(tag)["labels"]
            for key in PANEL_TITLE_KEYS:
                with self.subTest(lang=tag, key=key):
                    self.assertLessEqual(
                        len(labels[key]), PANEL_TITLE_BUDGET,
                        f"{tag}.{key} ({labels[key]!r}) exceeds the {PANEL_TITLE_BUDGET}-char budget",
                    )


class TestBadgeAndToggleOverflow(unittest.TestCase):
    """Review row extension (section 3 item 2): every fixed-width chrome
    context across all languages, not just `de`/`fi` (badge words and toggle
    words are short enough in every draft that testing all seven catches a
    mis-translation as readily as it catches a genuine overflow)."""

    def test_badge_words_fit_the_pill_budget(self):
        for tag in ALL_LANGS:
            labels = _load(tag)["labels"]
            for key in BADGE_WORD_KEYS:
                with self.subTest(lang=tag, key=key):
                    self.assertLessEqual(
                        len(labels[key]), BADGE_WORD_BUDGET,
                        f"{tag}.{key} ({labels[key]!r}) exceeds the {BADGE_WORD_BUDGET}-char badge budget",
                    )

    def test_toggle_words_fit_the_pill_budget(self):
        for tag in ALL_LANGS:
            labels = _load(tag)["labels"]
            for key in TOGGLE_WORD_KEYS:
                with self.subTest(lang=tag, key=key):
                    self.assertLessEqual(
                        len(labels[key]), TOGGLE_WORD_BUDGET,
                        f"{tag}.{key} ({labels[key]!r}) exceeds the {TOGGLE_WORD_BUDGET}-char toggle budget",
                    )


class TestBodyCopyCeiling(unittest.TestCase):
    def test_body_sentences_stay_under_the_runaway_ceiling(self):
        for tag in ALL_LANGS:
            labels = _load(tag)["labels"]
            for key in BODY_COPY_KEYS:
                with self.subTest(lang=tag, key=key):
                    self.assertLessEqual(
                        len(labels[key]), BODY_COPY_CEILING,
                        f"{tag}.{key} ({labels[key]!r}) exceeds the {BODY_COPY_CEILING}-char sanity ceiling",
                    )


if __name__ == "__main__":
    unittest.main()
