import struct
import unittest
from unittest.mock import patch

from strix_core.rgb_device import LampArrayDevice, frame_packet


class PacketTests(unittest.TestCase):
    def test_frame_is_the_verified_standard_report_layout(self):
        colors = [(10 + i, 20 + i, 30 + i) for i in range(8)]
        packet = frame_packet(colors)
        self.assertEqual(len(packet), 51)
        self.assertEqual(packet[:3], bytes((4, 8, 1)))
        self.assertEqual(struct.unpack('<8H', packet[3:19]), tuple(range(8)))
        self.assertEqual(packet[19:23], bytes((10, 20, 30, 255)))
        self.assertEqual(packet[-4:], bytes((17, 27, 37, 255)))

    def test_invalid_frame_rejected(self):
        for colors in ([], [(0, 0, 0)] * 4, [(0, 0, 256)] * 8, [(True, 0, 0)] * 8):
            with self.subTest(colors=colors), self.assertRaises(ValueError):
                frame_packet(colors)

    @patch('strix_core.rgb_device.fcntl.ioctl')
    def test_transport_rejects_vendor_save_flash_and_unknown_reports(self, ioctl):
        device = LampArrayDevice(); device.fd = 123
        for packet in (bytes((0x5d, 0xb5)) + bytes(62), bytes((5, 0)) + bytes(9), bytes((7, 0))):
            with self.assertRaises(ValueError):
                device.set_feature(packet)
        ioctl.assert_not_called()

    @patch('strix_core.rgb_device.fcntl.ioctl')
    def test_control_mode_packet_is_separate_from_color_data(self, ioctl):
        device = LampArrayDevice(); device.fd = 123
        device.take_control()
        self.assertEqual(ioctl.call_args.args[-1], bytes((6, 0)))
        self.assertTrue(device.owns_lighting)

    def test_frame_before_explicit_control_is_refused(self):
        with self.assertRaises(ValueError):
            LampArrayDevice().write_colors([(0, 0, 0)] * 8)

    @patch('strix_core.rgb_device.os.close')
    @patch('strix_core.rgb_device.fcntl.ioctl')
    def test_close_requests_firmware_control(self, ioctl, close):
        device = LampArrayDevice(); device.fd = 123; device.owns_lighting = True
        device.close()
        self.assertEqual(ioctl.call_args.args[-1], bytes((6, 1)))
        self.assertIsNone(device.fd)
        close.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
