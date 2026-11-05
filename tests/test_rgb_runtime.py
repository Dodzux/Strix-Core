from pathlib import Path
import fcntl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from strix_core.rgb_runtime import RGBSession, next_deadline


class Reader:
    def accent(self):
        return '#123456'


class Device:
    def __init__(self):
        self.frames = []
        self.acquired = 0
        self.closed = False

    def open(self):
        pass

    def take_control(self):
        self.acquired += 1

    def write_colors(self, colors):
        self.frames.append(colors)

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.device = Device()
        self.session = RGBSession(Reader(), Path(self.directory.name), lambda: self.device)

    def tearDown(self):
        if self.session.thread:
            self.session.stop()

    def test_explicit_start_maps_four_independent_zones_and_stops(self):
        colors = ['#ff0000', '#00ff00', '#0000ff', '#ffffff']
        result = self.session.apply({'effect': 'Static', 'zones': colors})
        self.assertTrue(result['transport_acknowledged'])
        self.assertFalse(result['verified'])
        self.assertEqual(self.device.frames[0][:4], [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
        self.session.stop()
        self.assertTrue(self.device.closed)

    def test_wallpaper_sync_uses_desktop_accent(self):
        self.session.apply({'effect': 'Wallpaper sync', 'zones': ['#000000'] * 4})
        self.assertEqual(self.device.frames[0], [(18, 52, 86)] * 8)

    def test_static_does_not_stream_redundant_frames(self):
        self.session.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        time.sleep(.3)
        self.assertEqual(len(self.device.frames), 1)

    def test_new_pattern_is_acknowledged_before_return(self):
        self.session.apply({'effect': 'Static', 'zones': ['#112233'] * 4})
        self.session.apply({'effect': 'Static', 'zones': ['#aabbcc'] * 4})
        self.assertEqual(self.device.frames[-1], [(170, 187, 204)] * 8)

    def test_paused_effect_holds_one_frame(self):
        self.session.apply({'effect': 'Wave', 'zones': ['#ff0000', '#00ff00', '#0000ff', '#ffffff'], 'paused': True})
        time.sleep(.3)
        self.assertEqual(len(self.device.frames), 1)


class RecoveryTests(unittest.TestCase):
    def make_session(self, factory):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        session = RGBSession(Reader(), Path(directory.name), factory)
        def cleanup():
            try:
                session.stop()
            except RuntimeError:
                pass
        self.addCleanup(cleanup)
        return session

    def test_animation_streams_multiple_distinct_frames(self):
        device = Device()
        session = self.make_session(lambda: device)
        session.apply({'effect': 'Color cycle', 'zones': ['#112233'] * 4})
        time.sleep(.3)
        self.assertGreaterEqual(len(device.frames), 3)
        self.assertNotEqual(device.frames[0], device.frames[-1])
        self.assertEqual(session.status()['state'], 'active')
        session.stop()
        count = len(device.frames)
        time.sleep(.1)
        self.assertEqual(len(device.frames), count)

    def test_transient_usb_failure_reopens_and_acknowledges_pattern(self):
        import errno
        class Broken(Device):
            def write_colors(self, colors):
                raise OSError(errno.EPIPE, 'simulated USB stall')
        broken, healthy = Broken(), Device()
        devices = iter((broken, healthy))
        session = self.make_session(lambda: next(devices))
        session.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        self.assertTrue(broken.closed)
        self.assertTrue(healthy.frames)
        self.assertEqual(session.status()['reconnects'], 1)

    def test_retry_after_terminal_failure_uses_a_fresh_device(self):
        class Broken(Device):
            def open(self):
                raise ValueError('simulated unavailable device')
        broken, healthy = Broken(), Device()
        devices = iter((broken, healthy))
        session = self.make_session(lambda: next(devices))
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            session.apply({'effect': 'Static', 'zones': ['#112233'] * 4})
        session.thread.join(timeout=1)
        session.apply({'effect': 'Static', 'zones': ['#112233'] * 4})
        self.assertTrue(healthy.frames)

    def test_software_dimmer_does_not_change_kernel_brightness(self):
        device = Device()
        session = self.make_session(lambda: device)
        session.apply({'effect': 'Static', 'zones': ['#ffffff'] * 4, 'intensity': 20})
        self.assertEqual(device.frames[0], [(51, 51, 51)] * 8)

    def test_second_stream_does_not_take_over_the_first(self):
        first_device, second_device = Device(), Device()
        first = self.make_session(lambda: first_device)
        second = RGBSession(Reader(), first.runtime, lambda: second_device)
        self.addCleanup(lambda: second.thread and second.thread.join(timeout=1))
        first.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        with self.assertRaisesRegex(RuntimeError, 'already controls'):
            second.apply({'effect': 'Static', 'zones': ['#ffffff'] * 4})
        self.assertFalse(second_device.frames)
        self.assertFalse(first_device.closed)

    def test_repeated_resets_stop_instead_of_looping_forever(self):
        import errno
        class Unstable(Device):
            def __init__(self):
                super().__init__()
                self.calls = 0
            def write_colors(self, colors):
                self.calls += 1
                if self.calls > 1:
                    raise OSError(errno.EPROTO, 'simulated repeated USB reset')
                super().write_colors(colors)
        session = self.make_session(Unstable)
        session.apply({'effect': 'Color cycle', 'zones': ['#112233'] * 4})
        session.thread.join(timeout=3)
        self.assertFalse(session.thread.is_alive())
        self.assertIn('reset repeatedly', session.error)
        self.assertEqual(len(session.usb_events), 3)

    def test_unchanged_static_color_is_refreshed_after_a_silent_reset(self):
        device = Device()
        session = self.make_session(lambda: device)
        session.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        time.sleep(.9)
        self.assertGreaterEqual(device.acquired, 2)
        self.assertGreaterEqual(len(device.frames), 2)
        self.assertEqual(device.frames[0], device.frames[-1])

    def test_factory_failure_is_reported_without_acknowledgment_timeout(self):
        def broken():
            raise ValueError('factory unavailable')
        session = self.make_session(broken)
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'factory unavailable'):
            session.apply({'effect':'Static','zones':['#123456']*4})
        self.assertLess(time.monotonic()-start, 1)

    def test_timed_out_start_does_not_apply_when_desktop_lock_is_released(self):
        device = Device()
        session = self.make_session(lambda: device)
        with (session.runtime / 'wallpaper-rgb.keyboard.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with patch('strix_core.rgb_runtime.APPLY_TIMEOUT_SECONDS', .1):
                with self.assertRaises(RuntimeError):
                    session.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        session.thread.join(timeout=1)
        self.assertEqual(device.frames, [])
        self.assertFalse(session.thread.is_alive())
        self.assertEqual(device.acquired, 0)

        # A deliberate retry starts a new worker after the cancelled one exits.
        session.apply({'effect': 'Static', 'zones': ['#abcdef'] * 4})
        self.assertEqual(device.frames[-1], [(171, 205, 239)] * 8)

    def test_timed_out_update_does_not_apply_later(self):
        device = Device()
        session = self.make_session(lambda: device)
        session.apply({'effect': 'Static', 'zones': ['#123456'] * 4})
        with (session.runtime / 'wallpaper-rgb.keyboard.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with patch('strix_core.rgb_runtime.APPLY_TIMEOUT_SECONDS', .1):
                with self.assertRaises(RuntimeError):
                    session.apply({'effect': 'Static', 'zones': ['#abcdef'] * 4})
        session.thread.join(timeout=1)
        self.assertEqual(device.frames, [[(18, 52, 86)] * 8])
        self.assertFalse(session.thread.is_alive())
        self.assertTrue(device.closed)

    def test_apply_rejected_while_a_timed_out_write_is_still_stopping(self):
        release = threading.Event()

        class Slow(Device):
            def write_colors(self, colors):
                release.wait(2)
                super().write_colors(colors)

        device = Slow()
        session = self.make_session(lambda: device)
        self.addCleanup(release.set)
        pattern = {'effect': 'Static', 'zones': ['#123456'] * 4}
        with patch('strix_core.rgb_runtime.APPLY_TIMEOUT_SECONDS', .1):
            with self.assertRaisesRegex(RuntimeError, 'stopping'):
                session.apply(pattern)
        with self.assertRaisesRegex(RuntimeError, 'still stopping'):
            session.apply({'effect': 'Static', 'zones': ['#abcdef'] * 4})
        release.set()
        session.thread.join(timeout=1)
        self.assertFalse(session.thread.is_alive())
        self.assertEqual(device.frames, [[(18, 52, 86)] * 8])
        self.assertTrue(device.closed)

    def test_native_animation_runs_without_restarting_at_heartbeat(self):
        class Native(Device):
            def can_animate(self, p):return p['effect']=='Breathing' and not p['paused']
            def write_pattern(self, p):self.frames.append(p)
        device = Native();session = self.make_session(lambda:device)
        pattern = {'effect':'Breathing','zones':['#123456']*4}
        session.apply(pattern)
        time.sleep(.9)
        self.assertEqual(len(device.frames),1)
        self.assertEqual(session.status()['output_mode'],'firmware')
        stamp = session.runtime/'wallpaper-rgb.keyboard.applied'
        stamp.touch()
        time.sleep(.3)
        self.assertEqual(len(device.frames),2)
        session.apply({**pattern,'paused':True})
        self.assertEqual(session.status()['output_mode'],'software')
        self.assertIsInstance(device.frames[-1],list)


class CadenceTests(unittest.TestCase):
    def test_usb_write_time_is_subtracted_from_frame_wait(self):
        self.assertAlmostEqual(next_deadline(10,10.016)-10.016,1/30-.016)

    def test_missed_frames_are_skipped_without_catch_up_bursts(self):
        self.assertAlmostEqual(next_deadline(10,10.085),10.1)
        self.assertGreater(next_deadline(10,10.1),10.1)


if __name__ == '__main__':
    unittest.main()
