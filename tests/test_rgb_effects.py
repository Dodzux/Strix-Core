"""Exercise every effect/speed/pause transition through the native packet path.

The device is fully injected. No real descriptors, USB handles or mode control.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from strix_core.aura_device import AuraDevice
from strix_core.lighting import EFFECTS
from strix_core.rgb_runtime import RGBSession


class Reader:
    def accent(self):return '#cd467a'


class FakeNative(AuraDevice):
    def open(self):self.fd=123
    def take_control(self):self.owns_lighting=True
    def abandon(self):self.fd=None;self.owns_lighting=False;self.invalidate()


class EffectMatrixTests(unittest.TestCase):
    @patch('strix_core.aura_device.os.write',return_value=64)
    def test_all_effects_speeds_and_pause_transitions_never_send_save(self,write):
        with tempfile.TemporaryDirectory() as root:
            session=RGBSession(Reader(),Path(root),FakeNative)
            try:
                for effect in EFFECTS:
                    for speed in (.25,1,3):
                        for paused in (False,True):
                            with self.subTest(effect=effect,speed=speed,paused=paused):
                                session.apply({'effect':effect,'zones':['#ff0000','#00ff00','#0000ff','#ffff00'],
                                               'speed':speed,'paused':paused,'intensity':20})
                                self.assertEqual(session.status()['state'],'active')
                                self.assertEqual(session.status()['output_mode'],
                                                 'firmware' if effect=='Breathing' and not paused else 'software')
                count=write.call_count
                with self.assertRaises(ValueError):session.apply({'effect':'Static','zones':['bad']*4})
                self.assertEqual(write.call_count,count)
            finally:session.stop()
            self.assertGreater(write.call_count,60)
            for call in write.call_args_list:
                packet=call.args[1]
                self.assertEqual(len(packet),64)
                self.assertIn(packet[1],(0xb3,0xb5))
