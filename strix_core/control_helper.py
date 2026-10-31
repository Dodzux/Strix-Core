"""One-shot privileged writes to two fixed, model-checked kernel attributes.

No caller-supplied paths, shell commands, HID packets, fan writes or MUX writes.
The installed entry point uses Python isolated mode and is owned by root.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

PROTOCOL = 1


class HardwareWriter:
    def __init__(self, root=Path("/")):
        self.root = root

    def path(self, relative):
        return self.root / relative.lstrip("/")

    def checked_board(self):
        board = self.path("/sys/class/dmi/id/board_name").read_text().strip()
        if board != "G614PH":
            raise ValueError("Hardware writes are restricted to the reviewed G614PH model.")

    def target(self, action):
        self.checked_board()
        if action == "brightness":
            path = self.path("/sys/class/leds/asus::kbd_backlight/brightness")
            maximum = int(path.with_name("max_brightness").read_text())
            if not 0 <= maximum <= 10:
                raise ValueError("Unexpected keyboard brightness range.")
            return path, 0, maximum
        if action == "battery":
            supplies = self.path("/sys/class/power_supply")
            batteries = [p for p in supplies.glob("BAT*") if (p / "type").is_file() and
                         (p / "type").read_text().strip() == "Battery" and
                         (p / "charge_control_end_threshold").is_file()]
            if len(batteries) != 1:
                raise ValueError("Exactly one supported laptop battery is required.")
            return batteries[0] / "charge_control_end_threshold", 40, 100
        raise ValueError("This helper does not support that action.")

    def write_value(self, path, value):
        # Resolve kernel-owned class links, then refuse a final symlink or non-file.
        target = path.resolve(strict=True)
        if not target.is_relative_to(self.path("/sys").resolve()):
            raise ValueError("Refusing a target outside sysfs.")
        fd = os.open(target, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Unexpected kernel attribute type.")
            payload = (str(value) + "\n").encode()
            if os.write(fd, payload) != len(payload):
                raise OSError("Incomplete kernel attribute write.")
        finally:
            os.close(fd)

    def apply(self, action, value):
        if type(value) is not int:
            raise ValueError("An integer is required.")
        path, minimum, maximum = self.target(action)
        if not minimum <= value <= maximum:
            raise ValueError(f"Value must be between {minimum} and {maximum}.")
        previous = int(path.read_text().strip())
        try:
            if value != previous:
                self.write_value(path, value)
            actual = int(path.read_text().strip())
            if actual != value:
                raise ValueError(f"Firmware read-back returned {actual}, requested {value}.")
        except (OSError, ValueError) as error:
            rollback = "not needed" if value == previous else "failed"
            if value != previous:
                try:
                    self.write_value(path, previous)
                    rollback = "confirmed" if int(path.read_text().strip()) == previous else "failed"
                except (OSError, ValueError):
                    pass
            raise RuntimeError(f"{error} Previous-value recovery: {rollback}.") from error
        return {"ok": True, "protocol": PROTOCOL, "action": action, "previous": previous,
                "actual": actual, "changed": actual != previous, "verified": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strix Core fixed hardware helper")
    parser.add_argument("action", choices=("battery", "brightness", "capabilities"))
    parser.add_argument("value", nargs="?", type=int)
    args = parser.parse_args(argv)
    if args.action == "capabilities":
        print(json.dumps({"protocol": PROTOCOL, "actions": ["battery", "brightness"]}))
        return 0
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "Administrator authentication is required."}))
        return 1
    try:
        result = HardwareWriter().apply(args.action, args.value)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
