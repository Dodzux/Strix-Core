#!/usr/bin/env python3
"""Explicit, two-second native dim color test. Requires prior user approval.

This is not part of automated tests or app startup. It uses only validated
G614PH native volatile packets and restores the saved static desktop color.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from strix_core.aura_device import AuraDevice
from strix_core.backend import Reader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-two-second-test", action="store_true")
    args = parser.parse_args()
    if not args.approved_two_second_test:
        parser.error("Obtain user approval for a real lighting change before running this test.")
    (PROJECT/'.preview').mkdir(parents=True,exist_ok=True)
    if int(Path('/sys/class/leds/asus::kbd_backlight/brightness').read_text()) == 0:
        parser.error("Keyboard brightness is zero; this test will not change it automatically.")
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
    reader = Reader()
    device = AuraDevice(lambda: reader.accent() or '#86d6b3')
    with (runtime / 'strix-core.rgb.lock').open('a') as owner, \
            (runtime / 'wallpaper-rgb.keyboard.lock').open('a') as lock:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        device.open()
        try:
            # Dim red / green / blue / yellow across the keyboard. Native
            # output leaves the untested accent lamps at their desktop color.
            colors = [(64, 0, 0), (0, 64, 0), (0, 0, 64), (64, 64, 0),
                      (64, 64, 0), (64, 0, 0), (64, 64, 0), (64, 0, 0)]
            print('Starting native two-second dim RGB test; the saved desktop color will be requested in cleanup.', flush=True)
            device.take_control()
            device.write_colors(colors)
            time.sleep(2)
        finally:
            device.close()
        result = {'color_output_sent': True, 'desktop_color_requested': True,
                  'transport': 'asus-native-volatile',
                  'mode_readback_supported': False, 'visual_confirmation_required': True,
                  'save_or_flash_commands_sent': False}
        output = PROJECT / '.preview/rgb-hardware-test.json'
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
