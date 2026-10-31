"""G614PH standard HID LampArray transport. No vendor/save/flash commands.

The descriptor and eight-lamp layout were read from this exact model. A bounded
static color test and return to firmware lighting were visually confirmed.
"""
import fcntl
import errno
import hashlib
import os
from pathlib import Path
import struct
import time

DESCRIPTOR_HASH = "6ae88aeb15120171f2112d116c2bc987a173368b6197dbce3b87bcdbde9c9d8d"


def frame_packet(colors):
    if not isinstance(colors, (list, tuple)) or len(colors) != 8:
        raise ValueError("Eight lamp colors are required.")
    levels = []
    for color in colors:
        if not isinstance(color, (list, tuple)) or len(color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in color):
            raise ValueError("RGB channels must be integers from 0 to 255.")
        levels.extend((*color, 255))
    # Report 4: count, update-complete flag, eight u16 IDs, eight RGBA records.
    return struct.pack("<BBB8H32B", 4, 8, 1, *range(8), *levels)


def find_device():
    if Path("/sys/class/dmi/id/board_name").read_text().strip() != "G614PH":
        raise ValueError("Lighting is restricted to the reviewed G614PH model.")
    candidates = []
    for node in Path("/sys/class/hidraw").glob("hidraw*"):
        device = node / "device"
        try:
            if "HID_ID=0003:00000B05:000019B6" not in (device / "uevent").read_text().splitlines():
                continue
            if hashlib.sha256((device / "report_descriptor").read_bytes()).hexdigest() == DESCRIPTOR_HASH:
                candidates.append(Path("/dev") / node.name)
        except OSError:
            continue
    if not candidates:
        raise OSError(errno.ENODEV, 'The reviewed keyboard is disconnected or reconnecting.')
    if len(candidates) != 1:
        raise ValueError("Exactly one matching ASUS LampArray interface is required.")
    return candidates[0]


class LampArrayDevice:
    def __init__(self):
        self.fd = None
        self.owns_lighting = False

    def get_feature(self, report, length):
        if (report, length) not in ((1, 23), (3, 29)):
            raise ValueError("Unapproved lighting query.")
        data = bytearray(length); data[0] = report
        fcntl.ioctl(self.fd, (3 << 30) | (length << 16) | (ord("H") << 8) | 7, data, True)
        return data

    def set_feature(self, data):
        # Metadata selection (2), volatile color frame (4), autonomous mode (6).
        if not data or (data[0], len(data)) not in ((2, 3), (4, 51), (6, 2)):
            raise ValueError("Unapproved lighting report.")
        if data[0] == 6 and data[1] not in (0, 1):
            raise ValueError("Invalid autonomous-mode flag.")
        if data[0] == 2 and struct.unpack('<H', bytes(data[1:]))[0] not in range(8):
            raise ValueError("Unapproved lamp ID.")
        if data[0] == 4 and (data[1:3] != bytes((8, 1)) or
                             struct.unpack('<8H', bytes(data[3:19])) != tuple(range(8))):
            raise ValueError("Unexpected lamp frame layout.")
        fcntl.ioctl(self.fd, (3 << 30) | (len(data) << 16) | (ord("H") << 8) | 6, bytes(data))

    def open(self):
        if self.fd is not None:
            raise ValueError("Lighting device is already open.")
        self.fd = os.open(find_device(), os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            attributes = self.get_feature(1, 23)
            count, width, height, depth, kind, interval = struct.unpack("<H5I", attributes[1:])
            if count != 8 or kind != 1 or interval > 50000:
                raise ValueError("Unexpected LampArray capabilities.")
            for lamp in range(8):
                self.set_feature(struct.pack("<BH", 2, lamp))
                data = struct.unpack("<H5I6B", self.get_feature(3, 29)[1:])
                if data[0] != lamp or data[10] != 1 or data[6:9] != (255, 255, 255):
                    raise ValueError("Unexpected programmable-lamp attributes.")
                if lamp < 4 and (data[2] != 99000 or data[1] != (65000, 140000, 214000, 289000)[lamp]):
                    raise ValueError("Keyboard zone mapping differs from the reviewed layout.")
                if lamp >= 4 and data[2] not in (224000, 258000):
                    raise ValueError("Accent lamp layout differs from the reviewed layout.")
        except Exception:
            os.close(self.fd); self.fd = None
            raise

    def take_control(self):
        if self.fd is None:
            raise ValueError("Open the validated device first.")
        # Standard autonomous-mode flag only. No firmware-store command is used.
        self.set_feature(bytes((6, 0)))
        self.owns_lighting = True

    def write_colors(self, colors):
        if not self.owns_lighting:
            raise ValueError("Software lighting control has not been requested.")
        self.set_feature(frame_packet(colors))

    def close(self):
        if self.fd is None:
            return
        try:
            if self.owns_lighting:
                # The device rejects GET for report 6: restoration can be requested,
                # but cannot be reported as confirmed by a mode read-back.
                self.set_feature(bytes((6, 1)))
        finally:
            os.close(self.fd); self.fd = None; self.owns_lighting = False

    def abandon(self):
        """Release an unusable descriptor without issuing another USB operation."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.owns_lighting = False

    @classmethod
    def restore_firmware(cls):
        """Fresh validated handle for hand-off after a reset invalidates an old fd."""
        last_error = None
        for delay in (0, .2, .5, .8):
            if delay:
                time.sleep(delay)
            device = cls()
            try:
                device.fd = os.open(find_device(), os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
                device.set_feature(bytes((6, 1)))
                return
            except OSError as error:
                last_error = error
                if error.errno not in (errno.EIO, errno.EPIPE, errno.EPROTO, errno.ENODEV, errno.ENOENT, errno.ETIMEDOUT):
                    raise
            finally:
                device.abandon()
        raise RuntimeError('Firmware lighting hand-off failed after reconnect retries: ' + str(last_error)) from last_error
