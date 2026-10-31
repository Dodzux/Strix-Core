"""Preview state and validation, separate from actual telemetry."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import os
import re
import tempfile
import time
from .lighting import validate_lighting

PAGES = ("Dashboard", "Performance", "Fans", "GPU", "Lighting", "Battery", "Memory",
         "Update Center", "Hotkeys", "Appearance", "Diagnostics")
OPTIONAL = ("Fans", "GPU", "Lighting", "Battery", "Memory", "Update Center", "Hotkeys")


class BrightnessSelection:
    """Keep a live hardware level distinct from a pending user selection."""
    def __init__(self, maximum):
        self.maximum = int(maximum)
        self.actual = self.selected = None

    @property
    def pending(self):
        return self.selected is not None and self.selected != self.actual

    def level(self, value):
        if type(value) in (int, float) and math.isfinite(value) and value == int(value) and 0 <= value <= self.maximum:
            return int(value)
        return None

    def update(self, value):
        pending = self.pending
        self.actual = self.level(value)
        if not pending or self.selected == self.actual:
            self.selected = self.actual

    def select(self, value):
        selected = self.level(value)
        if selected is None:
            raise ValueError('Choose an available keyboard brightness level.')
        self.selected = selected

    def applied(self, requested, actual):
        self.actual = self.level(actual)
        if self.selected == requested:
            self.selected = self.actual


def validate_curve(points):
    if not isinstance(points, list) or len(points) != 8:
        raise ValueError("A fan curve needs eight points.")
    previous_temp, previous_pwm = -1, -1
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each point needs a temperature and fan speed.")
        temp, pwm = point
        if any(type(x) is not int for x in point) or not 20 <= temp <= 105 or not 0 <= pwm <= 255:
            raise ValueError("Use temperatures from 20–105 °C and fan values from 0–255.")
        if temp < previous_temp or pwm < previous_pwm:
            raise ValueError("Temperature and fan speed must not decrease along the curve.")
        previous_temp, previous_pwm = temp, pwm
    return deepcopy(points)


class Storage:
    def __init__(self, root: Path):
        self.root = root

    def write(self, name, data):
        return self.write_text(name, json.dumps(data, indent=2, allow_nan=False) + "\n")

    def write_text(self, name, text):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name.startswith("."):
            raise ValueError("Invalid local artifact name")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / name
        fd, temp = tempfile.mkstemp(prefix=".strix_core-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return path

    def preferences(self):
        result = {"theme": "System", "start_page": "Dashboard", "wallpaper_accent": True,
                  "modules": list(OPTIONAL), "nvidia_sampling": False,
                  "close_to_tray": True, "restore_lighting": False}
        try:
            data = json.loads((self.root / "preferences.json").read_text())
            if not isinstance(data, dict):
                return result
            if data.get("theme") in ("System", "Dark", "Light"):
                result["theme"] = data["theme"]
            elif data.get("theme") in ("Graphite", "Midnight"):
                result["theme"] = "Dark"
            if data.get("start_page") in PAGES:
                result["start_page"] = data["start_page"]
            for key in ("wallpaper_accent", "nvidia_sampling", "close_to_tray", "restore_lighting"):
                if type(data.get(key)) is bool:
                    result[key] = data[key]
            if isinstance(data.get("modules"), list):
                result["modules"] = [p for p in OPTIONAL if p in data["modules"]]
        except (OSError, ValueError, TypeError):
            pass
        return result


class Preview:
    """Validate and record simulated actions without executing controls."""
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.drafts = {}
        self.history = []

    def plan(self, kind, value):
        cap = self.capabilities
        if kind == "profile":
            if value not in cap["profiles"]:
                raise ValueError("This profile is not exposed by the laptop.")
        elif kind == "battery":
            if not cap["charge_limit"]:
                raise ValueError("A charge limit is not exposed by this laptop.")
            if type(value) is not int or not 40 <= value <= 100:
                raise ValueError("Choose a charge limit from 40–100%.")
        elif kind == "brightness":
            maximum = cap["brightness_max"]
            if maximum is None or type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("Choose an available keyboard brightness level.")
        elif kind == "gpu":
            if value not in ("Integrated", "Hybrid", "Dedicated") or not cap["mux_path"]:
                raise ValueError("The requested GPU mode is not exposed.")
            if value == "Integrated" and not cap["dgpu_path"]:
                raise ValueError("Integrated mode is not exposed.")
        elif kind == "curve":
            channel = value.get("channel")
            if channel not in cap["curves"]:
                raise ValueError("This fan channel is not exposed.")
            value = {"channel": channel, "points": validate_curve(value.get("points"))}
        elif kind == "lighting":
            value = validate_lighting(value)
        elif kind == "close":
            if not isinstance(value, list) or not value or any(type(p) is not int or p <= 0 for p in value):
                raise ValueError("Select at least one process.")
        elif kind == "upgrade":
            if value != "sudo pacman -Syu":
                raise ValueError("Only a full system upgrade can be previewed.")
        else:
            raise ValueError("Unknown preview action.")
        self.drafts[kind] = deepcopy(value)
        event = {"timestamp": time.time(), "action": kind, "value": deepcopy(value),
                 "result": "simulated", "hardware_writes": 0}
        self.history.append(event)
        return event

    def clear(self):
        self.drafts.clear()
        self.history.clear()
