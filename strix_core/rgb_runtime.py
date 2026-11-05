"""One user-owned RGB stream, independent of window visibility."""
import errno
import fcntl
import os
import math
from pathlib import Path
import threading
import time

from .lighting import ANIMATED, sample, validate_lighting

RGB_OUTPUT_VERIFIED = True
TRANSIENT = {errno.EIO, errno.ENODEV, errno.EPIPE, errno.EPROTO, errno.ETIMEDOUT, errno.EBADF}
FRAME_SECONDS = 1 / 30
APPLY_TIMEOUT_SECONDS = 6
POSITIONS = (.125, .375, .625, .875, .875, .125, .875, .125)


def next_deadline(previous, completed, period=FRAME_SECONDS):
    """Keep frame cadence independent of USB time; skip missed slots, never burst."""
    target = previous + period
    if target <= completed:
        target += (math.floor((completed - target) / period) + 1) * period
        if target <= completed:  # Rounding exactly on a slot boundary.
            target += period
    return target


class RGBSession:
    def __init__(self, reader, runtime=None, device_factory=None):
        self.reader = reader
        self.runtime = Path(runtime or os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
        if device_factory is None:
            from .aura_device import AuraDevice
            self.device_factory = lambda: AuraDevice(lambda: self.reader.accent() or self.pattern['zones'][0])
        else:
            self.device_factory = device_factory
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = None
        self.pattern = None
        self.revision = self.applied_revision = 0
        self.error = None
        self.restoration_requested = False
        self.restoration_kind = 'firmware'
        self.state = 'stopped'
        self.frames = 0
        self.reconnects = 0
        self.usb_events = []
        self.output_mode = 'software'
        self.frame_times = []
        self.sample_times = []
        self.write_ms = 0

    def status(self):
        with self.condition:
            return {'state': self.state, 'error': self.error, 'frames': self.frames,
                    'reconnects': self.reconnects, 'running': bool(self.thread and self.thread.is_alive()),
                    'effect': self.pattern['effect'] if self.pattern else None,
                    'restoration_kind': self.restoration_kind,
                    'output_mode': self.output_mode,
                    'fps': round((len(self.sample_times)-1)/(self.sample_times[-1]-self.sample_times[0]), 1)
                           if len(self.sample_times) > 1 else 0,
                    'updates_per_second': round((len(self.frame_times)-1)/(self.frame_times[-1]-self.frame_times[0]), 1)
                           if len(self.frame_times) > 1 else 0,
                    'last_write_ms': round(self.write_ms, 2),
                    'usb_events': list(self.usb_events),
                    'firmware_control_requested': self.restoration_requested and self.restoration_kind == 'firmware',
                    'desktop_color_requested': self.restoration_requested and self.restoration_kind == 'desktop-color'}

    def apply(self, value):
        pattern = validate_lighting(value)
        with self.condition:
            if self.thread and not self.thread.is_alive():
                self.thread = None
                self.error = None
            if self.thread and self.stop_event.is_set():
                raise RuntimeError('Lighting is still stopping. Retry shortly.')
            self.pattern = pattern
            self.revision += 1
            target = self.revision
            if self.thread is None:
                self.stop_event.clear()
                self.error = None
                self.restoration_requested = False
                self.frames = self.reconnects = 0
                self.usb_events = []
                self.frame_times = []
                self.sample_times = []
                self.thread = threading.Thread(target=self.run, name='strix-core-rgb', daemon=False)
                self.thread.start()
            self.condition.notify_all()
            deadline = time.monotonic() + APPLY_TIMEOUT_SECONDS
            while self.applied_revision < target and not self.error:
                if self.stop_event.is_set():
                    raise RuntimeError('Lighting stopped before the requested frame was applied.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # A failed Apply must not remain queued behind the desktop
                    # writer and take over later without another user request.
                    self.stop_event.set()
                    self.state = 'stopping'
                    self.condition.notify_all()
                    raise RuntimeError('Lighting did not acknowledge the requested frame in time. '
                                       'The effect is stopping; retry after it stops.')
                self.condition.wait(remaining)
            if self.error:
                raise RuntimeError(self.error)
        return {'ok': True, 'status': 'lighting-active', 'verified': False,
                'transport_acknowledged': True, 'readback_available': False}

    def desktop_stamp(self):
        try:
            return (self.runtime / 'wallpaper-rgb.keyboard.applied').stat().st_mtime_ns
        except OSError:
            return None

    def run(self):
        device = None
        last_time, effect_time = time.monotonic(), 0
        started = last_time
        next_frame = started
        last_revision = 0
        last_stamp, previous_colors = self.desktop_stamp(), None
        last_write = last_control = 0
        initialized, controlled = False, False
        owner = lock = None
        try:
            device = self.device_factory()
            self.restoration_kind = getattr(device, 'restore_kind', 'firmware')
            self.runtime.mkdir(parents=True, exist_ok=True)
            owner = (self.runtime / 'strix-core.rgb.lock').open('a')
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another Strix Core instance already controls lighting.') from None
            lock = (self.runtime / 'wallpaper-rgb.keyboard.lock').open('a')
            failures = 0
            while not self.stop_event.is_set():
                with self.condition:
                    pattern, revision = self.pattern, self.revision
                stamp = self.desktop_stamp()
                zones = pattern['zones']
                if pattern['effect'] == 'Wallpaper sync':
                    zones = [self.reader.accent() or zones[0]] * 4
                now = time.monotonic()
                if not pattern['paused']:
                    effect_time += now - last_time
                last_time = now
                scale = 255 * pattern['intensity'] / 100
                colors = [tuple(round(channel * scale) for channel in sample(
                    pattern['effect'], zones, x, effect_time, pattern['speed'])) for x in POSITIONS]
                native = getattr(device, 'can_animate', lambda _: False)(pattern)
                animated = pattern['effect'] in ANIMATED and not pattern['paused'] and not native
                with self.condition:
                    if revision != last_revision:
                        self.frame_times = []
                        self.sample_times = []
                        next_frame = now
                        last_revision = revision
                    if animated:
                        self.sample_times = (self.sample_times + [now])[-60:]
                refresh = not initialized or stamp != last_stamp or (not native and now-last_write >= .75)
                if (refresh or (not native and colors != previous_colors) or self.applied_revision < revision):
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.state = 'waiting-for-desktop'
                        self.stop_event.wait(.05)
                        continue
                    try:
                        # Stop may arrive while acquiring the desktop lock.
                        if self.stop_event.is_set():
                            break
                        if not initialized:
                            device.open()
                        if not initialized or stamp != last_stamp or now-last_control >= .75:
                            device.take_control()
                            last_control = now
                            controlled = True
                        write_started = time.monotonic()
                        if refresh:
                            getattr(device, 'invalidate', lambda: None)()
                        if native:
                            device.write_pattern(pattern)
                        else:
                            device.write_colors(colors)
                        completed = time.monotonic()
                        initialized = True
                        failures = 0
                        last_stamp, previous_colors = stamp, colors
                        last_write = completed
                        with self.condition:
                            self.output_mode = 'firmware' if native else 'software'
                            self.write_ms = (completed-write_started)*1000
                            self.frame_times = (self.frame_times + [write_started])[-60:]
                            self.state = 'active'
                            self.frames += 1
                            self.applied_revision = revision
                            self.condition.notify_all()
                    except OSError as error:
                        self.usb_events = (self.usb_events + [{'seconds': round(time.monotonic()-started, 3),
                            'frames': self.frames, 'errno': error.errno}])[-10:]
                        recent = [event for event in self.usb_events if time.monotonic()-started-event['seconds'] < 10]
                        if len(recent) >= 3:
                            raise RuntimeError('The keyboard reset repeatedly. Streaming stopped; return to desktop lighting before retrying.') from error
                        if error.errno not in TRANSIENT or failures >= 4:
                            raise
                        failures += 1
                        self.reconnects += 1
                        self.state = 'reconnecting'
                        try:
                            device.close()
                        except OSError:
                            abandon = getattr(device, 'abandon', None)
                            if abandon:
                                abandon()
                        device = self.device_factory()
                        initialized = False
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    if not initialized:
                        self.stop_event.wait(.35 * failures)
                        continue
                # A condition wakes immediately for a new pattern or shutdown.
                next_frame = next_deadline(next_frame, time.monotonic()) if animated else time.monotonic()
                with self.condition:
                    if self.revision == revision and not self.stop_event.is_set():
                        self.condition.wait(max(0, next_frame-time.monotonic()) if animated else .25)
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.state = 'failed'
                self.condition.notify_all()
        finally:
            try:
                if controlled and lock:
                    deadline = time.monotonic() + 2
                    while True:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise RuntimeError('Desktop RGB writer is busy; firmware lighting hand-off could not be requested.')
                            time.sleep(.04)
                    try:
                        try:
                            device.close()
                        except OSError:
                            restore = getattr(device, 'restore_firmware', None)
                            if restore is None:
                                raise
                            restore()
                        if not initialized and hasattr(device, 'restore_firmware'):
                            device.restore_firmware()
                        self.restoration_requested = True
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                else:
                    if device is not None:
                        device.close()
            except Exception as error:
                self.error = (self.error + '; ' if self.error else '') + 'Lighting hand-off: ' + str(error)
                # Close the file descriptor without racing the desktop writer.
                abandon = getattr(device, 'abandon', None)
                if abandon:
                    abandon()
            for handle in (lock, owner):
                if handle:
                    handle.close()
            with self.condition:
                self.state = 'failed' if self.error else 'stopped'
                self.condition.notify_all()

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=4)
            if self.thread.is_alive():
                raise RuntimeError('Lighting shutdown is waiting for a USB operation. Retry shortly.')
        if self.error:
            raise RuntimeError(self.error)
        return {'ok': True, 'firmware_control_requested': self.restoration_requested and self.restoration_kind == 'firmware',
                'desktop_color_requested': self.restoration_requested and self.restoration_kind == 'desktop-color',
                'readback_available': False}
