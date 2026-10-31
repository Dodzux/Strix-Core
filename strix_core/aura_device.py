"""G614PH native four-zone output. Only volatile color/mode + apply, never store.

Four distinct zones, native breathing and return to the saved desktop color were
physically confirmed. B4 (store/save) and every unrelated vendor command are
rejected. The packet facts were checked against the device's output descriptor
and the Linux HID ASUS RGB patch; no external controller package is used.
"""
import hashlib
import os
from pathlib import Path
import re
import time

from .rgb_device import LampArrayDevice, frame_packet

NATIVE_OUTPUT_VERIFIED = True
NATIVE_BREATHING_VERIFIED = True
BATCHED_APPLY_VERIFIED = True
DESCRIPTOR_HASH = 'bdcf63294f0793588d96a966c08b1e28062b36b5fdf5d54e714b0102bf1e1094'
APPLY = bytes((0x5d, 0xb5)) + bytes(62)


def effect_packet(zone, mode, color, speed=0xeb):
    if type(zone) is not int or zone not in range(5) or type(mode) is not int or mode not in (0, 1):
        raise ValueError('Only the reviewed all/four keyboard zones and Static/Breathing modes are allowed.')
    if not isinstance(color, (tuple, list)) or len(color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in color):
        raise ValueError('Three bounded integer RGB channels are required.')
    if type(speed) is not int or speed not in (0xe1, 0xeb, 0xf5):
        raise ValueError('Unreviewed native speed.')
    data = bytearray(64)
    data[:9] = bytes((0x5d, 0xb3, zone, mode, *color, speed, 0))
    return bytes(data)


def find_native_device():
    if Path('/sys/class/dmi/id/board_name').read_text().strip() != 'G614PH':
        raise ValueError('Native lighting is restricted to the reviewed G614PH board.')
    nodes = []
    for node in Path('/sys/class/hidraw').glob('hidraw*'):
        device = node / 'device'
        try:
            if 'HID_ID=0003:00000B05:000019B6' not in (device / 'uevent').read_text().splitlines():
                continue
            if hashlib.sha256((device / 'report_descriptor').read_bytes()).hexdigest() == DESCRIPTOR_HASH:
                nodes.append(Path('/dev') / node.name)
        except OSError:
            continue
    if len(nodes) != 1:
        raise ValueError('Exactly one reviewed native ASUS lighting interface is required.')
    return nodes[0]


class AuraDevice:
    restore_kind = 'desktop-color'
    transport = 'asus-native-volatile'

    def __init__(self, restore_color=None):
        self.fd = None
        self.owns_lighting = False
        self.restore_color = restore_color or (lambda: '#86d6b3')
        self.previous = {}

    def invalidate(self):
        self.previous.clear()

    @staticmethod
    def can_animate(pattern):
        return NATIVE_BREATHING_VERIFIED and pattern['effect'] == 'Breathing' and not pattern['paused']

    def write_pattern(self, pattern):
        if not self.owns_lighting or not self.can_animate(pattern):
            raise ValueError('This pattern requires software rendering.')
        speed = 0xe1 if pattern['speed'] < .75 else 0xf5 if pattern['speed'] > 1.5 else 0xeb
        scale = pattern['intensity'] / 100
        packets = []
        for zone, color in enumerate(pattern['zones'], 1):
            rgb = tuple(round(int(color[i:i+2],16)*scale) for i in (1,3,5))
            packets.append(effect_packet(zone, 1, rgb, speed))
        self.commit(packets)
        self.invalidate()  # A following Static frame must replace the firmware mode.

    def commit(self, packets):
        for packet in packets:
            self.send(packet)
            if not BATCHED_APPLY_VERIFIED:
                self.send(APPLY)
        if packets and BATCHED_APPLY_VERIFIED:
            self.send(APPLY)

    def open(self):
        if self.fd is not None:
            raise ValueError('Lighting device is already open.')
        if not NATIVE_OUTPUT_VERIFIED:
            raise ValueError('Native output has not been verified.')
        self.fd = os.open(find_native_device(), os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)

    def send(self, packet):
        if self.fd is None:
            raise ValueError('Open the validated lighting device first.')
        if not isinstance(packet, bytes) or len(packet) != 64 or packet[:2] not in (bytes((0x5d, 0xb3)), APPLY[:2]):
            raise ValueError('Unapproved native command; store/save/flash commands are forbidden.')
        if packet[:2] == APPLY[:2] and packet != APPLY:
            raise ValueError('Malformed native apply report.')
        if packet[1] == 0xb3 and packet != effect_packet(packet[2], packet[3], list(packet[4:7]), packet[7]):
            raise ValueError('Unexpected native color fields.')
        if os.write(self.fd, packet) != 64:
            raise OSError('Incomplete native lighting report.')

    def take_control(self):
        if self.fd is None:
            raise ValueError('Open the validated lighting device first.')
        if not self.owns_lighting:
            # Leave autonomous mode enabled: software LampArray mode repeatedly
            # reset this exact keyboard during the approved physical tests.
            LampArrayDevice.restore_firmware()
            self.owns_lighting = True

    def write_colors(self, colors):
        if not self.owns_lighting:
            raise ValueError('Explicit lighting control is required.')
        frame_packet(colors)  # Exactly eight validated RGB entries from the renderer.
        # Native keyboard zones 1..4 were physically tested. Accent lamps remain
        # at their firmware/desktop color; untested native bar zones are not used.
        changed = {zone:color for zone,color in enumerate(colors[:4],1) if self.previous.get(zone) != color}
        self.commit([effect_packet(zone,0,color) for zone,color in changed.items()])
        self.previous.update(changed)  # Never cache an unsuccessful apply.

    def desktop_color(self):
        color = self.restore_color()
        if not isinstance(color, str) or not re.fullmatch('#[A-Fa-f0-9]{6}', color):
            raise ValueError('A valid saved desktop restoration color is required.')
        return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))

    def restore_firmware(self):
        error = None
        for delay in (0, .2, .5, .8):
            if delay:
                time.sleep(delay)
            fresh = AuraDevice(self.restore_color)
            try:
                fresh.open()
                LampArrayDevice.restore_firmware()
                fresh.send(effect_packet(0, 0, fresh.desktop_color()))
                fresh.send(APPLY)
                return
            except OSError as failure:
                error = failure
            finally:
                fresh.abandon()
        raise RuntimeError('Desktop color restoration failed after reconnect retries: ' + str(error)) from error

    def close(self):
        if self.fd is None:
            return
        try:
            if self.owns_lighting:
                self.send(effect_packet(0, 0, self.desktop_color()))
                self.send(APPLY)
        finally:
            self.abandon()

    def abandon(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.owns_lighting = False
        self.invalidate()
