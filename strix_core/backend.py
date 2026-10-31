"""Read-only Linux adapters. No hardware writer or privileged helper exists here."""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import time
from . import __version__


COMMANDS = {
    "windows": ("/usr/bin/hyprctl", "-j", "clients"),
    "profiles": ("/usr/bin/powerprofilesctl", "list"),
    "updates": ("/usr/bin/pacman", "-Qu"),
    "nvidia": (
        "/usr/bin/nvidia-smi",
        "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ),
}


def command(name: str) -> tuple[int, str, str]:
    """Only fixed read-only argument vectors; never shell commands or user input."""
    args = COMMANDS[name]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=4,
                                env={**os.environ, "LC_ALL": "C"})
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return -1, "", type(error).__name__


def read(path: Path, default=None):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return default


def number(path: Path, divisor=1):
    try:
        value = float(read(path)) / divisor
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def parse_updates(code: int, output: str, error: str) -> dict:
    if code not in (0, 1) or error or (code == 1 and output):
        return {"ok": False, "packages": [], "error": error or "Package query failed"}
    packages = []
    for line in output.splitlines():
        match = re.fullmatch(r"(\S+) (\S+) -> (\S+)(?: \[ignored\])?", line)
        if not match:
            return {"ok": False, "packages": [], "error": "Unrecognized package response"}
        packages.append(dict(zip(("name", "installed", "available"), match.groups())))
    return {"ok": True, "packages": packages, "error": None}


class Reader:
    def __init__(self, root: Path = Path("/"), home: Path | None = None,
                 runner=command):
        self.root = root
        self.home = home or Path.home()
        self.runner = runner
        self.previous_cpu = None
        self.capabilities = self.discover()

    def path(self, value: str) -> Path:
        return self.root / value.lstrip("/")

    def battery(self) -> Path | None:
        for path in sorted(self.path("/sys/class/power_supply").glob("*")):
            if read(path / "type") == "Battery" and not path.name.startswith("hidpp"):
                if read(path / "scope") != "Device":
                    return path
        return None

    def discover(self) -> dict:
        hwmon = {}
        for path in sorted(self.path("/sys/class/hwmon").glob("hwmon*")):
            name = read(path / "name")
            if name:
                hwmon.setdefault(name, []).append(str(path))
        battery = self.battery()
        fans, curves = [], {}
        for raw in hwmon.get("asus", []):
            path = Path(raw)
            for fan in sorted(path.glob("fan*_input")):
                stem = fan.name.removesuffix("_input")
                fans.append({"name": read(path / (stem + "_label"), stem), "path": str(fan)})
        for raw in hwmon.get("asus_custom_fan_curve", []):
            for index, title in ((1, "CPU"), (2, "GPU"), (3, "Middle")):
                points = []
                for point in range(1, 9):
                    base = Path(raw) / f"pwm{index}_auto_point{point}"
                    temp = number(Path(str(base) + "_temp"))
                    pwm = number(Path(str(base) + "_pwm"))
                    if temp is not None and pwm is not None:
                        points.append([int(temp), int(pwm)])
                if len(points) == 8:
                    curves[title] = points
        gpus = []
        for card in sorted(self.path("/sys/class/drm").glob("card[0-9]*")):
            if not re.fullmatch(r"card\d+", card.name):
                continue
            vendor = read(card / "device/vendor")
            if vendor in ("0x1002", "0x10de", "0x8086"):
                gpus.append({"card": card.name, "vendor": vendor,
                             "path": str(card / "device")})
        old = self.path("/sys/devices/platform/asus-nb-wmi")
        attrs = self.path("/sys/class/firmware-attributes/asus-armoury/attributes")
        def attribute(new, legacy):
            current = attrs / new / "current_value"
            return str(current if current.is_file() else old / legacy) if (
                current.is_file() or (old / legacy).is_file()) else None
        brightness = self.path("/sys/class/leds/asus::kbd_backlight")
        native_rgb = False
        if read(self.path('/sys/class/dmi/id/board_name')) == 'G614PH':
            from .aura_device import DESCRIPTOR_HASH, NATIVE_OUTPUT_VERIFIED
            for node in self.path('/sys/class/hidraw').glob('hidraw*'):
                try:
                    device = node / 'device'
                    if ('HID_ID=0003:00000B05:000019B6' in (device / 'uevent').read_text().splitlines() and
                            hashlib.sha256((device / 'report_descriptor').read_bytes()).hexdigest() == DESCRIPTOR_HASH):
                        native_rgb = NATIVE_OUTPUT_VERIFIED
                except OSError:
                    continue
        from .lighting import EFFECTS
        return {
            "model": read(self.path("/sys/class/dmi/id/product_name"), "Linux laptop"),
            "profiles": (read(self.path("/sys/firmware/acpi/platform_profile_choices"), "")).split(),
            "hwmon": hwmon, "fans": fans, "curves": curves, "gpus": gpus,
            "battery": str(battery) if battery else None,
            "charge_limit": bool(battery and (battery / "charge_control_end_threshold").is_file()),
            "brightness_max": number(brightness / "max_brightness"),
            "mux_path": attribute("gpu_mux_mode", "gpu_mux_mode"),
            "dgpu_path": attribute("dgpu_disable", "dgpu_disable"),
            "rgb_effects": list(EFFECTS) if native_rgb else [],
            "rgb_transport": 'asus-native-volatile' if native_rgb else None,
            "wallpaper_sync_present": (self.home / ".config/hypr/scripts/openrgb_color.sh").is_file(),
        }

    def cpu_usage(self):
        line = read(self.path("/proc/stat"), "").splitlines()
        if not line or not line[0].startswith("cpu "):
            return None
        try:
            values = [int(x) for x in line[0].split()[1:9]]
            total, idle = sum(values), values[3] + values[4]
            previous, self.previous_cpu = self.previous_cpu, (total, idle)
            if previous is None or total <= previous[0]:
                return None
            return max(0, min(100, 100 * (1 - (idle - previous[1]) / (total - previous[0]))))
        except (ValueError, IndexError):
            return None

    def memory(self):
        values = {}
        for line in read(self.path("/proc/meminfo"), "").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                values[fields[0].rstrip(":")] = int(fields[1]) * 1024
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if not total or available is None:
            return {"total": None, "used": None, "percent": None}
        used = max(0, total - available)
        return {"total": total, "used": used, "percent": used / total * 100}

    def temperature(self, names):
        for name in names:
            for raw in self.capabilities["hwmon"].get(name, []):
                value = number(Path(raw) / "temp1_input", 1000)
                if value is not None:
                    return value
        return None

    def accent(self):
        state = self.home / ".local/state"
        if self.home == Path.home():
            state = Path(os.environ.get("XDG_STATE_HOME") or state)
        color = read(state / "hypr/accent", "")
        return "#" + color.lstrip("#") if re.fullmatch(r"#?[a-fA-F0-9]{6}", color) else None

    def snapshot(self, nvidia=False):
        cap = self.capabilities
        battery = Path(cap["battery"]) if cap["battery"] else None
        battery_data = {"percent": number(battery / "capacity") if battery else None,
                        "status": read(battery / "status", "Unavailable") if battery else "Unavailable",
                        "limit": number(battery / "charge_control_end_threshold") if battery else None,
                        "ac_online": None}
        online = [number(p / "online") for p in self.path("/sys/class/power_supply").glob("*")
                  if read(p / "type") in ("Mains", "USB", "USB_C", "USB_PD")]
        if any(x is not None for x in online):
            battery_data["ac_online"] = any(x == 1 for x in online)
        gpu_data = []
        for gpu in cap["gpus"]:
            path = Path(gpu["path"])
            active = read(path / "power/runtime_status", "unknown")
            data = {"card": gpu["card"], "vendor": gpu["vendor"], "runtime": active,
                    "name": {"0x10de": "NVIDIA discrete GPU", "0x1002": "AMD Radeon integrated GPU",
                             "0x8086": "Intel graphics"}[gpu["vendor"]],
                    "usage": number(path / "gpu_busy_percent"), "temperature": None}
            if gpu["vendor"] == "0x1002":
                data["temperature"] = self.temperature(["amdgpu"])
            if gpu["vendor"] == "0x10de" and nvidia and active == "active":
                code, out, _ = self.runner("nvidia")
                try:
                    fields = out.splitlines()[0].split(",") if code == 0 else []
                    temp, usage = float(fields[1]), float(fields[2])
                    if not math.isfinite(temp) or not math.isfinite(usage):
                        raise ValueError("Invalid NVIDIA reading")
                    data.update(name=fields[0].strip(), temperature=temp, usage=usage)
                except (IndexError, ValueError):
                    data["query_status"] = "Unavailable"
            gpu_data.append(data)
        mux = read(Path(cap["mux_path"])) if cap["mux_path"] else None
        disabled = read(Path(cap["dgpu_path"])) if cap["dgpu_path"] else None
        # Firmware value describes the configured boot MUX, not proof of the running display route.
        mode = "Dedicated" if mux == "0" else "Integrated" if mux == "1" and disabled == "1" else (
            "Hybrid" if mux == "1" and disabled == "0" else "Unknown")
        return {"timestamp": time.time(), "cpu_usage": self.cpu_usage(),
                "cpu_temp": self.temperature(["k10temp", "coretemp"]), "memory": self.memory(),
                "battery": battery_data, "gpus": gpu_data,
                "fans": [{"name": fan["name"], "rpm": number(Path(fan["path"]))} for fan in cap["fans"]],
                "profile": read(self.path("/sys/firmware/acpi/platform_profile"), "Unavailable"),
                "gpu_mode": mode, "mux_value": mux, "dgpu_disabled": disabled,
                "brightness": self.brightness(),
                "accent": self.accent()}

    def brightness(self):
        return number(self.path('/sys/class/leds/asus::kbd_backlight/brightness'))

    def profiles(self):
        code, out, error = self.runner("profiles")
        # Match only profile headings, not Driver/Degraded metadata.
        keys = re.findall(r"^\s*\*?\s*(power-saver|balanced|performance):\s*$", out, re.M)
        return {"ok": code == 0, "profiles": keys, "error": error if code else None}

    def updates(self):
        result = parse_updates(*self.runner("updates"))
        databases = list(self.path("/var/lib/pacman/sync").glob("*.db"))
        result["database_timestamp"] = min((p.stat().st_mtime for p in databases), default=None)
        return result

    def processes(self):
        # Only windowed apps are cleanup candidates; never enumerate session services.
        code, output, _ = self.runner("windows")
        try:
            windows = json.loads(output) if code == 0 else []
        except (ValueError, TypeError):
            return []
        if not isinstance(windows, list):
            return []
        candidates = {}
        for window in windows:
            if not isinstance(window, dict):
                continue
            pid, app_class = window.get("pid"), window.get("class", "")
            if (type(pid) is int and pid > 0 and pid != os.getpid() and window.get("mapped") and
                isinstance(app_class, str) and app_class and
                app_class not in ("io.github.hyprdodz.StrixCore", "quickshell", "Hyprland", "Xwayland")):
                candidates[pid] = app_class
        result = []
        for pid, app_class in candidates.items():
            path = self.path(f"/proc/{pid}")
            data = {}
            for line in read(path / "status", "").splitlines():
                key, _, value = line.partition(":")
                data[key] = value.strip()
            try:
                # This user's windowed apps only; no window titles or command lines.
                if int(data["Uid"].split()[0]) != os.getuid():
                    continue
                rss = int(data.get("VmRSS", "0").split()[0]) * 1024
                if rss > 0:
                    result.append({"pid": pid, "name": app_class, "rss": rss})
            except (KeyError, ValueError, IndexError):
                continue
        return sorted(result, key=lambda x: x["rss"], reverse=True)[:40]

    def diagnostics(self, snapshot):
        cap = self.capabilities
        # Deliberate field allowlist. No usernames, serials, process lists, journal or raw paths.
        return {"format": 1, "app_version": __version__, "mode": "preview-only",
                "kernel": read(self.path("/proc/sys/kernel/osrelease"), "Unavailable"),
                "model": cap["model"], "capabilities": {
                    key: cap[key] for key in ("profiles", "charge_limit", "brightness_max", "rgb_effects", "rgb_transport")},
                "fan_curve_channels": list(cap["curves"]),
                "telemetry": {key: snapshot.get(key) for key in (
                    "timestamp", "cpu_usage", "cpu_temp", "memory", "battery", "gpus", "fans", "profile",
                    "gpu_mode", "brightness")}, "hardware_writes": 0}
