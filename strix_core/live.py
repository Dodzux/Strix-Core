"""Real action adapters. Each operation reports its actual result, never simulated success."""
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import time

HELPER = Path("/usr/lib/strix-core/control-helper")
PROFILE_KEYS = {"quiet": "power-saver", "balanced": "balanced", "performance": "performance"}


def direct_brightness_available():
    # Use existing access only. Never change sysfs ownership, mode or polkit rules.
    return os.access('/sys/class/leds/asus::kbd_backlight/brightness', os.W_OK)


def run(args, timeout=10):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                message = data["error"]
        except (ValueError, TypeError):
            pass
        raise RuntimeError(message or "The operation was cancelled or failed.")
    return result.stdout.strip()


def helper_available(path=HELPER):
    try:
        info = path.lstat()
        parent = path.parent.stat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and parent.st_uid == 0 and not parent.st_mode & 0o022)
    except OSError:
        return False


class LiveController:
    def __init__(self, reader, runner=run):
        self.reader, self.runner = reader, runner
        self.history = []
        self.rgb_session = None

    def supported(self, action):
        cap = self.reader.capabilities
        if action == "lighting":
            from .rgb_runtime import RGB_OUTPUT_VERIFIED
            return RGB_OUTPUT_VERIFIED and 'G614PH' in self.reader.capabilities.get('model', '')
        if action == 'profile':
            return bool(cap.get('profiles')) and bool(shutil.which('powerprofilesctl'))
        if action == 'close':
            return bool(shutil.which('hyprctl'))
        if action == 'upgrade':
            return bool(shutil.which('pacman')) and self.terminal() is not None
        return (action in ('battery', 'brightness') and (helper_available() or
                action == 'brightness' and direct_brightness_available()) and
                (not cap.get('model') or 'G614PH' in cap['model']) and
                (cap.get('charge_limit') if action == 'battery' else cap.get('brightness_max') is not None))

    @staticmethod
    def terminal():
        for name, prefix in (('kitty', ('--class=cachy.update', '-e')), ('foot', ('-e',)),
                             ('konsole', ('-e',)), ('gnome-terminal', ('--',)), ('xterm', ('-e',))):
            path = shutil.which(name)
            if path:
                return (path, *prefix)
        return None

    def availability(self):
        reasons = {'profile': 'Needs an exposed platform profile and powerprofilesctl.',
                   'lighting': 'Native RGB output is reviewed for the ASUS G614PH only.',
                   'battery': 'Needs an exposed charge limit and the installed authenticated helper.',
                   'brightness': 'Needs the ASUS brightness interface and existing user access or the installed helper.',
                   'close': 'Application closing currently needs Hyprland.',
                   'upgrade': 'Needs pacman and a supported terminal.',
                   'curve': 'Custom fan writes have not passed hardware validation.',
                   'gpu': 'GPU switching has not passed a display/reboot validation.'}
        result = {k: {'available': self.supported(k), 'reason': '' if self.supported(k) else v} for k, v in reasons.items()}
        if result['profile']['available']:
            service = self.reader.profiles()
            if not service.get('ok'):
                result['profile'] = {'available': False, 'reason': service.get('error') or 'The power-profile service is not responding.'}
        if result['lighting']['available']:
            try:
                from .aura_device import find_native_device
                device = find_native_device()
                if not os.access(device, os.R_OK | os.W_OK):
                    result['lighting'] = {'available': False, 'reason': 'The keyboard RGB device is not accessible to the logged-in user.'}
            except (OSError, ValueError) as error:
                result['lighting'] = {'available': False, 'reason': str(error)}
        return result

    def apply(self, action, value):
        # Use the same validation as previews before any executable operation.
        from .model import Preview
        Preview(self.reader.capabilities).plan(action, value)
        if not self.supported(action):
            raise ValueError("This control is not enabled. GPU and custom fan writes still require model-specific verification; battery and brightness require the installed helper.")
        if action == "profile":
            result = self.profile(value)
        elif action in ("battery", "brightness"):
            result = None
            if action == 'brightness' and direct_brightness_available():
                from .control_helper import HardwareWriter
                try:
                    result = HardwareWriter().apply('brightness', value)
                    result['authentication'] = 'not-required'
                except PermissionError:
                    # Access can change after discovery; preserve the normal
                    # authenticated fallback rather than broadening permissions.
                    if not helper_available():
                        raise
            if result is None:
                raw = self.runner(("/usr/bin/pkexec", str(HELPER), action, str(value)), timeout=120)
                result = json.loads(raw)
            if not result.get("ok") or not result.get("verified") or result.get("actual") != value:
                raise RuntimeError(result.get("error", "Hardware read-back did not confirm the requested value."))
        elif action == "close":
            result = self.close_apps(value)
        elif action == "upgrade":
            result = self.upgrade()
        elif action == "lighting":
            if self.reader.snapshot().get("brightness") == 0:
                raise ValueError("Keyboard brightness is zero. Set a nonzero brightness before applying RGB.")
            if self.rgb_session is None:
                from .rgb_runtime import RGBSession
                self.rgb_session = RGBSession(self.reader)
            result = self.rgb_session.apply(value)
        event = {"timestamp": time.time(), "action": action, "value": value, "result": "executed", **result}
        self.history.append(event)
        return event

    def profile(self, value):
        desired = PROFILE_KEYS[value]
        previous = self.runner(("/usr/bin/powerprofilesctl", "get"))
        try:
            self.runner(("/usr/bin/powerprofilesctl", "set", desired))
            actual = self.runner(("/usr/bin/powerprofilesctl", "get"))
            platform = self.reader.snapshot()["profile"]
            if actual != desired or platform != value:
                raise RuntimeError("The service and kernel did not confirm the requested profile.")
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            recovery = "not attempted"
            if previous in PROFILE_KEYS.values():
                try:
                    self.runner(("/usr/bin/powerprofilesctl", "set", previous))
                    recovery = "confirmed" if self.runner(("/usr/bin/powerprofilesctl", "get")) == previous else "failed"
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    recovery = "failed"
            raise RuntimeError(f"{error} Previous-profile recovery: {recovery}.") from error
        return {"ok": True, "actual": value, "previous": previous, "verified": True}

    def close_apps(self, pids):
        # Re-resolve current window addresses, rather than signalling potentially reused PIDs.
        raw = self.runner(("/usr/bin/hyprctl", "-j", "clients"))
        clients = json.loads(raw)
        eligible = {p["pid"] for p in self.reader.processes()}
        if not set(pids).issubset(eligible):
            raise ValueError("The application list changed. Refresh and review the selection again.")
        addresses = []
        for client in clients:
            if client.get("pid") in pids and client.get("mapped"):
                address = client.get("address", "")
                import re
                if not re.fullmatch(r"0x[0-9a-fA-F]+", address):
                    raise ValueError("Invalid window address.")
                addresses.append(address)
        if not addresses:
            raise ValueError("No matching application windows remain.")
        sent = []
        for address in addresses:
            self.runner(("/usr/bin/hyprctl", "dispatch", f'hl.dsp.window.close({{window="address:{address}"}})'))
            sent.append(address)
        return {"ok": True, "status": "close-requested", "windows": len(sent), "verified": False}

    def upgrade(self):
        # The interactive terminal owns sudo and pacman; the GUI never receives a password.
        terminal = self.terminal()
        if not terminal:
            raise RuntimeError('Install a supported terminal to run an interactive system upgrade.')
        subprocess.Popen((*terminal, "/bin/sh", "-c",
                          'sudo pacman -Syu; code=$?; printf "\\nPacman exited with %s. Press Enter to close.\\n" "$code"; read -r answer'),
                         start_new_session=True)
        return {"ok": True, "status": "terminal-opened", "verified": False}

    def stop_lighting(self):
        if self.rgb_session is None:
            return {"ok": True, "firmware_control_requested": False}
        session = self.rgb_session
        try:
            return session.stop()
        finally:
            if session.thread is None or not session.thread.is_alive():
                self.rgb_session = None

    def close(self):
        return self.stop_lighting()
