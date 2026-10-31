import math
from pathlib import Path
import tempfile
import unittest

from strix_core.hotkeys import export_bindings, validate_event
from strix_core.lighting import EFFECTS, sample, validate_lighting
from strix_core.model import Storage


class LightingTests(unittest.TestCase):
    colors = ["#ff0000", "#00ff00", "#0000ff", "#ffffff"]

    def test_wave_blends_smoothly_across_the_last_first_color_boundary(self):
        boundary=(1-.125)/.14
        a=sample('Wave',self.colors,.125,boundary-.00001)
        b=sample('Wave',self.colors,.125,boundary+.00001)
        self.assertLess(max(abs(x-y) for x,y in zip(a,b)),.0001)

    def test_static_colors_are_independent_by_zone(self):
        for position, expected in ((.1, (1, 0, 0)), (.3, (0, 1, 0)), (.6, (0, 0, 1)), (.9, (1, 1, 1))):
            self.assertEqual(sample("Static", self.colors, position), expected)

    def test_all_effects_produce_finite_bounded_colors(self):
        for effect in EFFECTS:
            for seconds in (0, .3, 12, 10000):
                for position in (0, .3, .65, 1):
                    with self.subTest(effect=effect, seconds=seconds, x=position):
                        color = sample(effect, self.colors, position, seconds, speed=3)
                        self.assertTrue(all(math.isfinite(c) and 0 <= c <= 1 for c in color))

    def test_animated_effects_change_over_time(self):
        for effect in ("Breathing", "Spectrum", "Color cycle", "Wave", "Pulse", "Comet", "Starlight"):
            frames = {sample(effect, self.colors, .2, t) for t in (0, .5, 1, 2, 3)}
            with self.subTest(effect=effect):
                self.assertGreater(len(frames), 1)

    def test_lighting_rejects_missing_extra_and_invalid_zones(self):
        for colors in ([], self.colors[:3], self.colors + ["#ff0000"], [None] * 4, ["#invalid"] * 4):
            with self.subTest(colors=colors), self.assertRaises(ValueError):
                validate_lighting({"effect": "Static", "zones": colors})

    def test_lighting_rejects_invalid_speed(self):
        for speed in (float("nan"), float("inf"), True, 0, 4, "fast"):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                validate_lighting({"effect": "Wave", "zones": self.colors, "speed": speed})

    def test_preview_copies_zone_list(self):
        result = validate_lighting({"effect": "Wave", "zones": self.colors, "speed": 2})
        result["zones"][0] = "#000000"
        self.assertEqual(self.colors[0], "#ff0000")

    def test_legacy_single_color_is_expanded_to_four_zones(self):
        result = validate_lighting({"effect": "Static", "color": "#123456"})
        self.assertEqual(result["zones"], ["#123456"] * 4)


class HotkeyTests(unittest.TestCase):
    def test_uncaptured_m5_does_not_guess_a_key(self):
        draft = export_bindings([{"event": None, "action": "Open Strix Core"}])
        self.assertNotIn("hl.bind(", draft)
        self.assertIn("captured key", draft)

    def test_captured_event_produces_only_one_explicit_binding(self):
        draft = export_bindings([{"event": "XF86Launch1", "action": "Open Lighting"}])
        self.assertEqual(draft.count("hl.bind("), 1)
        self.assertIn('hl.bind("XF86Launch1", hl.dsp.exec_cmd("strix-core --page Lighting"))', draft)

    def test_existing_m_buttons_generate_no_bindings(self):
        self.assertNotIn("hl.bind(", export_bindings([{"event": "XF86AudioMute", "action": "Keep existing"}]))

    def test_function_keys_typing_and_reserved_performance_event_rejected(self):
        for event in ("F4", "F5", "F12", "a", "Return", "XF86Launch4", 'XF86Foo\"; os.execute("bad")'):
            with self.subTest(event=event), self.assertRaises(ValueError):
                validate_event(event)

    def test_gdk_media_aliases_are_canonicalized(self):
        self.assertEqual(validate_event('Launch1'), 'XF86Launch1')
        self.assertEqual(validate_event('Launch3'), 'XF86Launch3')
        self.assertEqual(validate_event('Tools'), 'XF86Tools')

    def test_gdk_alias_does_not_bypass_reserved_performance_or_audio(self):
        for name in ('Launch4', 'AudioMicMute', 'AudioRaiseVolume'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_event(name)

    def test_duplicate_events_rejected(self):
        with self.assertRaises(ValueError):
            export_bindings([{"event": "XF86Launch1", "action": "Open Strix Core"},
                             {"event": "XF86Launch1", "action": "Open Lighting"}])

    def test_theme_migration_and_new_options(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            for before, after in (("Graphite", "Dark"), ("Midnight", "Dark"), ("System", "System"),
                                  ("Dark", "Dark"), ("Light", "Light")):
                storage.write("preferences.json", {"theme": before})
                self.assertEqual(storage.preferences()["theme"], after)


if __name__ == "__main__":
    unittest.main()
