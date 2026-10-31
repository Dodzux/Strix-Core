import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strix_core.control_helper import HardwareWriter, main as helper_main
from strix_core.live import HELPER, LiveController, helper_available


class FakeReader:
    capabilities = {"profiles": ["quiet", "balanced", "performance"], "charge_limit": True,
                    "brightness_max": 3, "mux_path": "present", "dgpu_path": "present", "curves": {}}

    def snapshot(self):
        return {"profile": "balanced"}

    def processes(self):
        return [{"pid": 101, "name": "fixture"}]


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = HardwareWriter(self.root)
        self.put("sys/class/dmi/id/board_name", "G614PH")
        self.put("sys/class/power_supply/BAT0/type", "Battery")
        self.battery = self.put("sys/class/power_supply/BAT0/charge_control_end_threshold", "100\n")
        self.brightness = self.put("sys/class/leds/asus::kbd_backlight/brightness", "2\n")
        self.put("sys/class/leds/asus::kbd_backlight/max_brightness", "3")

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_writes_and_verifies_fixed_charge_attribute(self):
        result = self.writer.apply("battery", 80)
        self.assertTrue(result["verified"])
        self.assertEqual(result["previous"], 100)
        self.assertEqual(int(self.battery.read_text()), 80)

    def test_writes_and_verifies_brightness(self):
        result = self.writer.apply("brightness", 1)
        self.assertEqual(result["actual"], 1)
        self.assertEqual(int(self.brightness.read_text()), 1)

    def test_unchanged_value_does_not_write(self):
        with patch.object(self.writer, "write_value") as write:
            result = self.writer.apply("battery", 100)
        write.assert_not_called()
        self.assertFalse(result["changed"])

    def test_wrong_laptop_is_rejected_without_writing(self):
        self.put("sys/class/dmi/id/board_name", "AnotherModel")
        with self.assertRaises(ValueError):
            self.writer.apply("battery", 80)
        self.assertEqual(int(self.battery.read_text()), 100)

    def test_unknown_actions_and_bad_values_do_not_write(self):
        for action, value in (("gpu", 1), ("fan", 255), ("rgb", 0), ("battery", 0), ("brightness", 4),
                              ("battery", True), ("battery", "80")):
            with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                self.writer.apply(action, value)
        self.assertEqual(int(self.battery.read_text()), 100)
        self.assertEqual(int(self.brightness.read_text()), 2)

    def test_sysfs_escape_is_rejected(self):
        outside = self.put("outside", "100\n")
        self.battery.unlink(); self.battery.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.writer.apply("battery", 80)
        self.assertEqual(int(outside.read_text()), 100)

    def test_rounding_or_bad_readback_restores_previous_setting(self):
        calls = []
        def rounded(path, value):
            calls.append(value)
            path.write_text("79" if value == 80 else str(value))
        with patch.object(self.writer, "write_value", side_effect=rounded):
            with self.assertRaisesRegex(RuntimeError, "recovery: confirmed"):
                self.writer.apply("battery", 80)
        self.assertEqual(calls, [80, 100])
        self.assertEqual(int(self.battery.read_text()), 100)

    def test_helper_requires_admin_for_actual_writes(self):
        with patch("strix_core.control_helper.os.geteuid", return_value=1000), \
             patch("strix_core.control_helper.HardwareWriter.apply") as apply, \
             patch("builtins.print"):
            self.assertEqual(helper_main(["battery", "80"]), 1)
            apply.assert_not_called()

    def test_helper_entry_uses_isolated_python(self):
        entry = (Path(__file__).resolve().parent.parent / "packaging/control-helper").read_text()
        self.assertEqual(entry.splitlines()[0], "#!/usr/bin/python -I")


class LiveTests(unittest.TestCase):
    def setUp(self):
        programs = {'powerprofilesctl', 'hyprctl', 'pacman', 'kitty'}
        available = patch('strix_core.live.shutil.which', side_effect=lambda name: '/usr/bin/' + name if name in programs else None)
        available.start()
        self.addCleanup(available.stop)

    def test_missing_programs_are_reported_without_running_commands(self):
        with patch('strix_core.live.shutil.which', return_value=None):
            controller = LiveController(FakeReader(), lambda *_: self.fail('Unexpected command'))
            for action in ('profile', 'close', 'upgrade'):
                self.assertFalse(controller.supported(action))

    def test_performance_is_applied_and_checked(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[-1] == "get":
                return "power-saver" if len(calls) == 1 else "balanced"
            return ""
        result = LiveController(FakeReader(), runner).apply("profile", "balanced")
        self.assertEqual(calls[1], ("/usr/bin/powerprofilesctl", "set", "balanced"))
        self.assertEqual(result["result"], "executed")
        self.assertTrue(result["verified"])

    def test_profile_failure_restores_previous_profile(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return "power-saver" if args[-1] == "get" else ""
        with self.assertRaisesRegex(RuntimeError, "recovery: confirmed"):
            LiveController(FakeReader(), runner).apply("profile", "balanced")
        self.assertIn(("/usr/bin/powerprofilesctl", "set", "power-saver"), calls)

    @patch("strix_core.rgb_runtime.RGB_OUTPUT_VERIFIED", False)
    def test_no_unverified_gpu_rgb_or_fan_execution(self):
        controller = LiveController(FakeReader(), lambda *_: self.fail("Unexpected command"))
        self.assertFalse(controller.supported("gpu"))
        self.assertFalse(controller.supported("curve"))
        with self.assertRaises(ValueError):
            controller.apply("lighting", {"effect": "Static", "color": "#123456"})

    @patch("strix_core.live.helper_available", return_value=True)
    def test_privileged_action_uses_only_fixed_helper_and_arguments(self, _):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return json.dumps({"ok": True, "verified": True, "actual": 80})
        LiveController(FakeReader(), runner).apply("battery", 80)
        self.assertEqual(calls, [("/usr/bin/pkexec", str(HELPER), "battery", "80")])

    def test_graceful_close_uses_revalidated_window_address(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return json.dumps([{"pid": 101, "mapped": True, "address": "0x1234"}]) if args[-1] == "clients" else "ok"
        result = LiveController(FakeReader(), runner).apply("close", [101])
        self.assertEqual(calls[-1], ("/usr/bin/hyprctl", "dispatch", 'hl.dsp.window.close({window="address:0x1234"})'))
        self.assertEqual(result["status"], "close-requested")
        self.assertFalse(result["verified"])

    def test_stale_selection_does_not_close_any_window(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return json.dumps([{"pid": 999, "mapped": True, "address": "0x1234"}])
        with self.assertRaises(ValueError):
            LiveController(FakeReader(), runner).apply("close", [999])
        self.assertEqual(len(calls), 1)

    @patch('strix_core.rgb_runtime.RGBSession')
    def test_verified_rgb_routes_to_injected_session_not_real_device(self, factory):
        reader = FakeReader()
        reader.capabilities = {**reader.capabilities, 'model': 'ROG G614PH'}
        reader.snapshot = lambda: {'profile': 'balanced', 'brightness': 3}
        factory.return_value.apply.return_value = {'ok': True, 'verified': False, 'transport_acknowledged': True}
        controller = LiveController(reader)
        result = controller.apply('lighting', {'effect': 'Static', 'color': '#123456'})
        factory.assert_called_once_with(reader)
        self.assertTrue(result['transport_acknowledged'])
        controller.close()
        factory.return_value.stop.assert_called_once()

    @patch("strix_core.live.subprocess.Popen")
    def test_upgrade_opens_interactive_terminal_without_running_sudo_in_gui(self, popen):
        result = LiveController(FakeReader()).apply("upgrade", "sudo pacman -Syu")
        args = popen.call_args.args[0]
        self.assertEqual(args[0], "/usr/bin/kitty")
        self.assertIn('--class=cachy.update',args)
        self.assertIn("sudo pacman -Syu", args[-1])
        self.assertEqual(result["status"], "terminal-opened")
        self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
