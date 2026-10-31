import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from strix_core.backend import COMMANDS, Reader, command, number, parse_updates


class LinuxFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home/test"
        self.calls = []

    def put(self, path, value):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(value))
        return target

    def runner(self, name):
        self.calls.append(name)
        return (0, "NVIDIA Test, 51, 12, 100, 8000", "") if name == "nvidia" else (0, "", "")

    def reader(self):
        return Reader(self.root, self.home, self.runner)

    def gpu(self, active="active"):
        self.put("sys/class/drm/card0/device/vendor", "0x10de")
        self.put("sys/class/drm/card0/device/power/runtime_status", active)

    def test_empty_system_keeps_missing_readings_unknown(self):
        snap = self.reader().snapshot()
        self.assertIsNone(snap["cpu_temp"])
        self.assertIsNone(snap["battery"]["percent"])
        self.assertIsNone(snap["memory"]["percent"])
        self.assertEqual(snap["gpu_mode"], "Unknown")
        self.assertEqual(self.calls, [])

    def test_temperature_driver_discovery_not_hwmon_number(self):
        self.put("sys/class/hwmon/hwmon87/name", "k10temp")
        self.put("sys/class/hwmon/hwmon87/temp1_input", "61500")
        self.assertEqual(self.reader().snapshot()["cpu_temp"], 61.5)

    def test_fans_use_labels_and_preserve_zero_rpm(self):
        self.put("sys/class/hwmon/hwmon23/name", "asus")
        self.put("sys/class/hwmon/hwmon23/fan3_label", "mid_fan")
        self.put("sys/class/hwmon/hwmon23/fan3_input", 0)
        self.assertEqual(self.reader().snapshot()["fans"], [{"name": "mid_fan", "rpm": 0}])

    def test_cpu_first_sample_and_delta(self):
        self.put("proc/stat", "cpu  100 0 100 700 100 0 0 0 300 0\n")
        reader = self.reader()
        self.assertIsNone(reader.cpu_usage())
        self.put("proc/stat", "cpu  125 0 125 750 100 0 0 0 300 0\n")
        self.assertEqual(reader.cpu_usage(), 50)
        self.assertIsNone(reader.cpu_usage())

    def test_cpu_counter_reset_is_unknown(self):
        self.put("proc/stat", "cpu 100 0 100 700 100 0 0 0\n")
        reader = self.reader(); reader.cpu_usage()
        self.put("proc/stat", "cpu 1 0 1 7 1 0 0 0\n")
        self.assertIsNone(reader.cpu_usage())

    def test_memory_uses_available_not_free(self):
        self.put("proc/meminfo", "MemTotal: 10000 kB\nMemFree: 1000 kB\nMemAvailable: 6000 kB\n")
        self.assertEqual(self.reader().memory(), {"total": 10240000, "used": 4096000, "percent": 40})

    def test_laptop_battery_not_wireless_peripheral(self):
        self.put("sys/class/power_supply/aaa/type", "Battery")
        self.put("sys/class/power_supply/aaa/scope", "Device")
        self.put("sys/class/power_supply/BAT0/type", "Battery")
        self.put("sys/class/power_supply/BAT0/capacity", 70)
        self.put("sys/class/power_supply/BAT0/charge_control_end_threshold", 80)
        reader = self.reader()
        self.assertTrue(reader.capabilities["charge_limit"])
        self.assertEqual(reader.snapshot()["battery"]["percent"], 70)

    def test_incomplete_fan_curve_not_offered(self):
        self.put("sys/class/hwmon/hwmon0/name", "asus_custom_fan_curve")
        self.put("sys/class/hwmon/hwmon0/pwm1_auto_point1_temp", 40)
        self.put("sys/class/hwmon/hwmon0/pwm1_auto_point1_pwm", 30)
        self.assertEqual(self.reader().capabilities["curves"], {})

    def test_detected_curve_keeps_repeated_and_decreasing_values(self):
        base = "sys/class/hwmon/hwmon19"
        self.put(base + "/name", "asus_custom_fan_curve")
        points = [[40, 30], [43, 53], [60, 68], [65, 76], [75, 119], [75, 142], [80, 132], [80, 132]]
        for i, (temp, pwm) in enumerate(points, 1):
            self.put(f"{base}/pwm1_auto_point{i}_temp", temp)
            self.put(f"{base}/pwm1_auto_point{i}_pwm", pwm)
        self.assertEqual(self.reader().capabilities["curves"]["CPU"], points)

    def test_rgb_brightness_is_not_effect_support(self):
        self.put("sys/class/leds/asus::kbd_backlight/max_brightness", 3)
        cap = self.reader().capabilities
        self.assertEqual(cap["brightness_max"], 3)
        self.assertEqual(cap["rgb_effects"], [])

    def test_nvidia_not_queried_by_default(self):
        self.gpu()
        self.reader().snapshot()
        self.assertEqual(self.calls, [])

    def test_nvidia_never_queries_suspended_gpu(self):
        self.gpu("suspended")
        self.reader().snapshot(nvidia=True)
        self.assertEqual(self.calls, [])

    def test_nvidia_optional_active_query(self):
        self.gpu()
        gpu = self.reader().snapshot(nvidia=True)["gpus"][0]
        self.assertEqual(gpu["temperature"], 51)
        self.assertEqual(gpu["usage"], 12)
        self.assertEqual(self.calls, ["nvidia"])

    def test_nvidia_driver_error_does_not_fabricate_zeros(self):
        self.gpu()
        reader = Reader(self.root, self.home, lambda _: (1, "", "Driver error"))
        gpu = reader.snapshot(nvidia=True)["gpus"][0]
        self.assertIsNone(gpu["temperature"])
        self.assertIsNone(gpu["usage"])

    def test_drm_connector_names_not_treated_as_gpu(self):
        self.put("sys/class/drm/card0-HDMI-A-1/device/vendor", "0x10de")
        self.assertEqual(self.reader().capabilities["gpus"], [])

    def test_nonfinite_nvidia_readings_remain_unknown(self):
        self.gpu()
        reader = Reader(self.root, self.home, lambda _: (0, "NVIDIA Test, nan, inf, 0, 8000", ""))
        gpu = reader.snapshot(nvidia=True)["gpus"][0]
        self.assertIsNone(gpu["temperature"])
        self.assertIsNone(gpu["usage"])

    def test_legacy_mux_mapping(self):
        mux = self.put("sys/devices/platform/asus-nb-wmi/gpu_mux_mode", 1)
        disabled = self.put("sys/devices/platform/asus-nb-wmi/dgpu_disable", 0)
        reader = self.reader()
        self.assertEqual(reader.snapshot()["gpu_mode"], "Hybrid")
        disabled.write_text("1")
        self.assertEqual(reader.snapshot()["gpu_mode"], "Integrated")
        mux.write_text("0")
        self.assertEqual(reader.snapshot()["gpu_mode"], "Dedicated")

    def test_firmware_attribute_preferred_over_legacy(self):
        self.put("sys/devices/platform/asus-nb-wmi/gpu_mux_mode", 0)
        self.put("sys/class/firmware-attributes/asus-armoury/attributes/gpu_mux_mode/current_value", 1)
        self.put("sys/devices/platform/asus-nb-wmi/dgpu_disable", 0)
        self.assertEqual(self.reader().snapshot()["gpu_mode"], "Hybrid")

    def test_wallpaper_accent_validation(self):
        accent = self.put("home/test/.local/state/hypr/accent", "cd467a\n")
        self.assertEqual(self.reader().accent(), "#cd467a")
        accent.write_text("color; unwanted-command")
        self.assertIsNone(self.reader().accent())

    def test_diagnostics_remove_paths_and_private_data(self):
        reader = self.reader()
        snap = reader.snapshot()
        snap.update(processes=["private"], serial="1234", home=str(self.home))
        diag = reader.diagnostics(snap)
        text = str(diag)
        self.assertNotIn(str(self.home), text)
        self.assertNotIn("private", text)
        self.assertNotIn("serial", text)
        self.assertNotIn("hwmon", str(diag["capabilities"]))

    def test_number_rejects_invalid_and_nonfinite_values(self):
        path = self.put("test", "nan")
        for value in ("nan", "inf", "bad", ""):
            path.write_text(value)
            self.assertIsNone(number(path))
        self.assertIsNone(number(self.root / "missing"))

    def test_profile_parser_ignores_driver_metadata(self):
        reader = Reader(self.root, self.home, lambda _: (0, "* balanced:\n  Driver: fake\n  performance:\n  power-saver:", ""))
        self.assertEqual(reader.profiles()["profiles"], ["balanced", "performance", "power-saver"])

    def test_memory_candidates_are_windowed_apps_not_session_services(self):
        self.put("proc/101/status", f"Name: app\nUid: {os.getuid()}\nVmRSS: 1024 kB\n")
        self.put("proc/102/status", f"Name: Hyprland\nUid: {os.getuid()}\nVmRSS: 99999 kB\n")
        windows = [{"pid": 101, "class": "app", "mapped": True},
                   {"pid": 102, "class": "Hyprland", "mapped": True}]
        reader = Reader(self.root, self.home, lambda _: (0, json.dumps(windows), ""))
        self.assertEqual(reader.processes(), [{"pid": 101, "name": "app", "rss": 1048576}])

    def test_memory_does_not_fall_back_to_all_processes(self):
        self.put("proc/101/status", f"Name: app\nUid: {os.getuid()}\nVmRSS: 99999 kB\n")
        reader = Reader(self.root, self.home, lambda _: (1, "", "Cannot connect"))
        self.assertEqual(reader.processes(), [])

    def test_memory_window_query_handles_malformed_output(self):
        for data in ("bad json", "{}", "null", '[null,{"pid":"101","class":"app","mapped":true}]'):
            reader = Reader(self.root, self.home, lambda _: (0, data, ""))
            self.assertEqual(reader.processes(), [])


class CommandTests(unittest.TestCase):
    def test_mutating_command_not_in_allowlist(self):
        with self.assertRaises(KeyError):
            command("upgrade")
        self.assertEqual(set(COMMANDS), {"profiles", "updates", "nvidia", "windows"})

    @patch("strix_core.backend.subprocess.run")
    def test_fixed_argv_no_shell_and_bounded_timeout(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        command("updates")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ("/usr/bin/pacman", "-Qu"))
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 4)

    @patch("strix_core.backend.subprocess.run", side_effect=subprocess.TimeoutExpired("pacman", 4))
    def test_timeout_returns_unavailable(self, _):
        self.assertEqual(command("updates")[0], -1)

    def test_no_updates_exit_one(self):
        self.assertTrue(parse_updates(1, "", "")["ok"])
        self.assertEqual(parse_updates(1, "", "")["packages"], [])

    def test_updates_parse_real_pacman_shape(self):
        result = parse_updates(0, "linux 6.1-1 -> 6.2-1\nfoo 1-1 -> 2-1 [ignored]", "")
        self.assertTrue(result["ok"])
        self.assertEqual(result["packages"][1]["available"], "2-1")

    def test_errors_and_unrecognized_output_not_reported_as_up_to_date(self):
        for values in ((1, "", "database error"), (127, "", ""), (0, "unrecognized", ""), (1, "x 1 -> 2", "")):
            with self.subTest(values=values):
                self.assertFalse(parse_updates(*values)["ok"])


if __name__ == "__main__":
    unittest.main()
