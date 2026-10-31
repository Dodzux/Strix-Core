import unittest
from unittest.mock import patch

from strix_core.aura_device import AuraDevice, APPLY, effect_packet


class NativePacketTests(unittest.TestCase):
    @patch('strix_core.aura_device.BATCHED_APPLY_VERIFIED',True)
    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_batched_frame_has_four_colors_and_one_apply(self,write):
        device=AuraDevice();device.fd=123;device.owns_lighting=True
        device.write_colors([(1,2,3),(4,5,6),(7,8,9),(10,11,12)]*2)
        packets=[c.args[1] for c in write.call_args_list]
        self.assertEqual([p[1] for p in packets],[0xb3]*4+[0xb5])
        self.assertEqual([p[2] for p in packets[:-1]],[1,2,3,4])

    @patch('strix_core.aura_device.BATCHED_APPLY_VERIFIED',True)
    @patch('strix_core.aura_device.os.write',side_effect=[64,64,64,64,OSError('apply failed')])
    def test_failed_apply_does_not_cache_unsent_colors(self,write):
        device=AuraDevice();device.fd=123;device.owns_lighting=True
        with self.assertRaises(OSError):device.write_colors([(1,2,3)]*8)
        self.assertFalse(device.previous)

    def test_packet_is_full_descriptor_size_with_reviewed_fields(self):
        packet=effect_packet(3,0,(10,20,30))
        self.assertEqual(len(packet),64)
        self.assertEqual(packet[:9],bytes((0x5d,0xb3,3,0,10,20,30,0xeb,0)))
        self.assertEqual(packet[9:],bytes(55))

    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_four_keyboard_zones_are_written_without_save(self,write):
        device=AuraDevice();device.fd=123;device.owns_lighting=True
        colors=[(1,2,3),(4,5,6),(7,8,9),(10,11,12)]*2
        device.write_colors(colors)
        packets=[call.args[1] for call in write.call_args_list]
        self.assertEqual(len(packets),5)
        self.assertEqual([p[2] for p in packets[:-1]],[1,2,3,4])
        self.assertEqual([p[4:7] for p in packets[:-1]],[bytes(c) for c in colors[:4]])
        self.assertTrue(all(p[1] in (0xb3,0xb5) for p in packets))

    @patch('strix_core.aura_device.os.write')
    def test_save_flash_unreviewed_modes_and_zone_commands_are_rejected(self,write):
        device=AuraDevice();device.fd=123
        for packet in (bytes((0x5d,0xb4))+bytes(62),bytes((0x5d,0xbc))+bytes(62),APPLY[:-1],APPLY[:-1]+b'\x01'):
            with self.subTest(packet=packet[:3]),self.assertRaises(ValueError):device.send(packet)
        for zone,mode in ((5,0),(1,2),(1,255),(True,0)):
            with self.assertRaises(ValueError):effect_packet(zone,mode,(0,0,0))
        write.assert_not_called()

    @patch('strix_core.aura_device.LampArrayDevice.restore_firmware')
    def test_software_lamparray_mode_is_never_enabled(self,restore):
        device=AuraDevice();device.fd=123
        device.take_control();device.take_control()
        restore.assert_called_once()
        self.assertTrue(device.owns_lighting)

    @patch('strix_core.aura_device.os.close')
    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_quit_requests_the_known_static_desktop_color(self,write,close):
        device=AuraDevice(lambda:'#cd467a');device.fd=123;device.owns_lighting=True
        device.close()
        packets=[call.args[1] for call in write.call_args_list]
        self.assertEqual(packets,[effect_packet(0,0,(205,70,122)),APPLY])
        self.assertIsNone(device.fd)
        close.assert_called_once_with(123)

    def test_output_requires_explicit_ownership(self):
        device=AuraDevice();device.fd=123
        with self.assertRaises(ValueError):device.write_colors([(0,0,0)]*8)

    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_unchanged_zones_are_skipped_until_refresh(self,write):
        device=AuraDevice();device.fd=123;device.owns_lighting=True
        colors=[(1,2,3)]*8
        device.write_colors(colors)
        write.reset_mock();device.write_colors(colors)
        write.assert_not_called()
        colors[2]=(4,5,6);device.write_colors(colors)
        self.assertEqual(write.call_count,2)
        self.assertEqual(write.call_args_list[0].args[1][2],3)
        device.invalidate();write.reset_mock();device.write_colors(colors)
        self.assertEqual(write.call_count,5)

    @patch('strix_core.aura_device.NATIVE_BREATHING_VERIFIED',True)
    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_firmware_breathing_keeps_four_colors_and_bounds_speeds(self,write):
        device=AuraDevice();device.fd=123;device.owns_lighting=True
        pattern={'effect':'Breathing','zones':['#ff0000','#00ff00','#0000ff','#ffffff'],'intensity':20,'speed':1,'paused':False}
        self.assertTrue(device.can_animate(pattern));device.write_pattern(pattern)
        packets=[c.args[1] for c in write.call_args_list]
        self.assertEqual([p[3] for p in packets[:-1]],[1]*4)
        self.assertEqual([p[4:7] for p in packets[:-1]], [bytes(c) for c in ((51,0,0),(0,51,0),(0,0,51),(51,51,51))])
        self.assertTrue(all(p[1] in (0xb3,0xb5) for p in packets))
        self.assertFalse(device.can_animate({**pattern,'paused':True}))
        write.reset_mock();device.write_colors([(51,0,0)]*8)
        self.assertEqual(write.call_args_list[0].args[1][3],0)
