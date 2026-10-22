"""Tests for model_map.py. Stdlib unittest, no external deps."""
import unittest

import model_map as mm


class DefaultTableTests(unittest.TestCase):
    def test_all_seven_phases_resolve_to_a_valid_class(self):
        phases = ["spec", "plan", "dev", "test", "verify", "iterate", "report"]
        self.assertEqual(sorted(mm.DEFAULT_TABLE.keys()), sorted(phases))
        for phase in phases:
            self.assertIn(mm.resolve(phase, {}), mm.VALID_CLASSES)

    def test_specific_default_assignments(self):
        self.assertEqual(mm.DEFAULT_TABLE["plan"], "opus")
        self.assertEqual(mm.DEFAULT_TABLE["dev"], "sonnet")
        self.assertEqual(mm.DEFAULT_TABLE["test"], "haiku")
        self.assertEqual(mm.DEFAULT_TABLE["verify"], "opus")
        self.assertEqual(mm.DEFAULT_TABLE["report"], "haiku")


class ParseTests(unittest.TestCase):
    def test_parses_valid_map(self):
        result = mm.parse("plan=opus,dev=sonnet,report=haiku")
        self.assertEqual(result, {"plan": "opus", "dev": "sonnet", "report": "haiku"})

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(mm.parse(""), {})

    def test_rejects_unknown_phase(self):
        with self.assertRaises(ValueError):
            mm.parse("bogus=opus")

    def test_rejects_unknown_class(self):
        with self.assertRaises(ValueError):
            mm.parse("dev=gpt5")

    def test_tolerates_surrounding_whitespace(self):
        result = mm.parse(" plan = opus , dev=sonnet ")
        self.assertEqual(result, {"plan": "opus", "dev": "sonnet"})


class ResolveTests(unittest.TestCase):
    def test_empty_override_returns_table_default_for_every_phase(self):
        for phase, cls in mm.DEFAULT_TABLE.items():
            self.assertEqual(mm.resolve(phase, {}), cls)

    def test_override_wins_for_listed_phase(self):
        self.assertEqual(mm.resolve("dev", {"dev": "opus"}), "opus")

    def test_unlisted_phase_falls_back_to_table(self):
        override = {"dev": "opus"}
        self.assertEqual(mm.resolve("report", override), mm.DEFAULT_TABLE["report"])


class ClassToModelTests(unittest.TestCase):
    def test_registry_covers_exactly_valid_classes(self):
        self.assertEqual(set(mm.CLASS_TO_MODEL.keys()), set(mm.VALID_CLASSES))

    def test_registry_records_the_expected_bare_aliases(self):
        self.assertEqual(mm.CLASS_TO_MODEL["opus"], "claude-opus-5")
        self.assertEqual(mm.CLASS_TO_MODEL["sonnet"], "claude-sonnet-5")
        self.assertEqual(mm.CLASS_TO_MODEL["haiku"], "claude-haiku-4-5")
        self.assertEqual(mm.CLASS_TO_MODEL["fable"], "claude-fable-5")

    def test_every_default_table_class_has_a_registry_entry(self):
        for phase, cls in mm.DEFAULT_TABLE.items():
            self.assertIn(cls, mm.CLASS_TO_MODEL, f"no registry entry for {phase}={cls}")

    def test_module_docstring_frames_registry_as_recorded_expectation(self):
        doc = (mm.__doc__ or "").lower()
        self.assertIn("expected", doc)
        self.assertIn("receipt", doc)
        # Must NOT overclaim: the dispatch passes a class, so editing the
        # registry cannot change which model runs.
        self.assertNotIn("only place a version changes", doc)


class ResolveModelTests(unittest.TestCase):
    def test_returns_registry_id_for_every_class(self):
        for cls, model_id in mm.CLASS_TO_MODEL.items():
            self.assertEqual(mm.resolve_model(cls), model_id)

    def test_raises_on_unknown_class(self):
        with self.assertRaises(ValueError):
            mm.resolve_model("gpt5")

    def test_raises_on_unknown_class_even_when_pinned(self):
        with self.assertRaises(ValueError):
            mm.resolve_model("gpt5", {"gpt5": "whatever"})

    def test_pin_overrides_exactly_one_class_and_leaves_the_others(self):
        pin = {"opus": "claude-opus-5-20260601"}
        self.assertEqual(mm.resolve_model("opus", pin), "claude-opus-5-20260601")
        self.assertEqual(mm.resolve_model("sonnet", pin), mm.CLASS_TO_MODEL["sonnet"])
        self.assertEqual(mm.resolve_model("haiku", pin), mm.CLASS_TO_MODEL["haiku"])
        self.assertEqual(mm.resolve_model("fable", pin), mm.CLASS_TO_MODEL["fable"])

    def test_empty_pin_falls_back_to_registry(self):
        self.assertEqual(mm.resolve_model("sonnet", {}), mm.CLASS_TO_MODEL["sonnet"])


if __name__ == "__main__":
    unittest.main()
