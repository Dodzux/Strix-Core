from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strix_core.model import Preview, Storage, validate_curve


def capabilities():
    return {"profiles": ["quiet", "balanced", "performance"], "charge_limit": True,
            "brightness_max": 3, "mux_path": "/not-opened", "dgpu_path": "/not-opened",
            "curves": {"CPU": [[40 + i * 5, 30 + i * 30] for i in range(8)]}}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.cap = capabilities()
        self.preview = Preview(self.cap)

    def test_all_actions_simulated_without_external_calls(self):
        with patch("subprocess.run", side_effect=AssertionError("No external commands allowed")), \
             patch("builtins.open", side_effect=AssertionError("No file writes allowed")):
            for kind, value in (("profile", "quiet"), ("battery", 80), ("brightness", 2),
                                ("gpu", "Hybrid"), ("curve", {"channel": "CPU", "points": self.cap["curves"]["CPU"]}),
                                ("lighting", {"effect": "Wallpaper sync", "color": "#cd467a"}),
                                ("close", [123, 456]), ("upgrade", "sudo pacman -Syu")):
                with self.subTest(kind=kind):
                    event = self.preview.plan(kind, value)
                    self.assertEqual(event["result"], "simulated")
                    self.assertEqual(event["hardware_writes"], 0)
        self.assertEqual(len(self.preview.history), 8)

    def test_invalid_actions_do_not_create_history_or_drafts(self):
        for kind, value in (("battery", -1), ("battery", 101), ("battery", True), ("profile", "turbo"),
                            ("brightness", 4), ("brightness", 1.5), ("gpu", "mux-auto"), ("close", []),
                            ("close", [True]), ("upgrade", "sudo pacman -Sy"), ("flash", "anything"),
                            ("lighting", {"effect": "Static", "color": "#xxzz00"})):
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                self.preview.plan(kind, value)
        self.assertEqual(self.preview.history, [])
        self.assertEqual(self.preview.drafts, {})

    def test_capability_missing_fails_closed(self):
        self.cap.update(charge_limit=False, brightness_max=None, mux_path=None)
        for kind, value in (("battery", 80), ("brightness", 1), ("gpu", "Hybrid")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.preview.plan(kind, value)

    def test_integrated_mode_needs_disable_capability(self):
        self.cap["dgpu_path"] = None
        with self.assertRaises(ValueError):
            self.preview.plan("gpu", "Integrated")

    def test_drafts_do_not_mutate_detected_curves_or_history(self):
        original = deepcopy(self.cap)
        value = {"channel": "CPU", "points": self.cap["curves"]["CPU"]}
        self.preview.plan("curve", value)
        self.preview.drafts["curve"]["points"][0][0] = 21
        self.assertEqual(self.cap, original)
        self.assertEqual(self.preview.history[0]["value"]["points"][0][0], 40)

    def test_clear_only_clears_preview(self):
        self.preview.plan("profile", "quiet")
        self.preview.clear()
        self.assertEqual(self.preview.history, [])
        self.assertEqual(self.preview.drafts, {})
        self.assertEqual(self.cap["profiles"], ["quiet", "balanced", "performance"])

    def test_monotonic_curves_accept_equal_temperatures(self):
        points = [[40, 20], [40, 30], [50, 40], [60, 80], [70, 100], [80, 130], [90, 180], [100, 255]]
        self.assertEqual(validate_curve(points), points)

    def test_bad_curve_rejected(self):
        good = self.cap["curves"]["CPU"]
        cases = [[], good[:7], [[40, 999]] * 8, [[19, 50]] * 8,
                 [[40, 50]] * 7 + [[39, 60]], [[40, 50]] * 7 + [[50, 49]], [[40, True]] * 8]
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ValueError):
                validate_curve(points)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = Storage(self.root)

    def test_preferences_only_save_to_requested_directory(self):
        preferences = self.storage.preferences()
        preferences["theme"] = "Light"
        path = self.storage.write("preferences.json", preferences)
        self.assertEqual(path.parent, self.root)
        self.assertEqual(self.storage.preferences()["theme"], "Light")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_preferences_fall_back(self):
        for content in ("bad json", "[]", "null", '{"theme": "Invalid", "modules": 42}'):
            (self.root / "preferences.json").write_text(content)
            self.assertEqual(self.storage.preferences()["theme"], "System")

    def test_untrusted_preferences_are_filtered(self):
        self.storage.write("preferences.json", {"theme": "Midnight", "start_page": "Unknown",
                            "wallpaper_accent": "false", "modules": ["Fans", "Unknown"], "apply": True})
        loaded = self.storage.preferences()
        self.assertEqual(loaded["start_page"], "Dashboard")
        self.assertTrue(loaded["wallpaper_accent"])
        self.assertEqual(loaded["modules"], ["Fans"])
        self.assertNotIn("apply", loaded)

    def test_export_rejects_traversal(self):
        for name in ("../outside.json", "/tmp/outside", ".secret", "foo/bar"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.storage.write(name, {})

    def test_export_preserves_previous_file_on_serialization_error(self):
        self.storage.write("diagnostics.json", {"valid": True})
        with self.assertRaises(ValueError):
            self.storage.write("diagnostics.json", {"invalid": float("nan")})
        self.assertEqual(json.loads((self.root / "diagnostics.json").read_text()), {"valid": True})
        self.assertEqual(len(list(self.root.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
