"""Reviewable Lua drafts. No global grabs or keybinding installation."""
import re

ACTIONS = {"Keep existing": None, "Open Strix Core": "strix-core --page Dashboard",
           "Open Lighting": "strix-core --page Lighting", "Open Performance": "strix-core --page Performance",
           "Open Battery": "strix-core --page Battery", "Cycle lighting effects": "strix-core --cycle-lighting",
           "Previous lighting effect": "strix-core --previous-lighting"}


def validate_event(name):
    # GDK omits XF86 on many media key names (Launch1, AudioMicMute, ...).
    if isinstance(name, str) and (re.fullmatch(r'Launch[0-9A-F]', name) or name in {
            'Tools', 'Calculator', 'Explorer', 'Mail', 'AudioRaiseVolume', 'AudioLowerVolume',
            'AudioMute', 'AudioMicMute', 'MonBrightnessUp', 'MonBrightnessDown',
            'KbdLightOnOff', 'KbdBrightnessUp', 'KbdBrightnessDown',
            'TouchpadToggle', 'TouchpadOn', 'TouchpadOff'}):
        name = 'XF86' + name
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise ValueError("Capture a key symbol before exporting a binding.")
    if re.fullmatch(r"F(?:[1-9]|1[0-2])", name):
        raise ValueError("This is an ordinary function key. Capture the Fn/media event instead.")
    if name in ("XF86Launch4", "XF86AudioRaiseVolume", "XF86AudioLowerVolume", "XF86AudioMute",
                "XF86AudioMicMute", "XF86MonBrightnessUp", "XF86MonBrightnessDown",
                "Escape", "Return", "space", "Super_L", "Super_R"):
        raise ValueError("That event is reserved; the existing performance binding is preserved.")
    if not name.startswith("XF86") and not re.fullmatch(r"F(?:1[3-9]|2[0-9]|3[0-5])", name):
        raise ValueError("Capture M5's media event, not a typing key.")
    return name


def export_bindings(records):
    lines = ["-- Strix Core key draft · not installed",
             "-- Keep the existing XF86Launch4 performance binding (physical key not captured).",
             "-- Fn is handled by firmware; ordinary F4 and F5 are never rebound here."]
    seen = set()
    for item in records:
        action = item.get("action", "Keep existing")
        if action not in ACTIONS:
            raise ValueError("Unknown shortcut action.")
        if action == "Keep existing":
            continue
        key = item.get("event")
        if not key:
            lines.append("-- A selected action still needs a captured key event.")
            continue
        key = validate_event(key)
        if key in seen:
            raise ValueError("Two draft buttons use the same event. Resolve the duplicate first.")
        seen.add(key)
        # Both tokens come from a validated symbol and a closed command table.
        lines.append(f'hl.bind("{key}", hl.dsp.exec_cmd("{ACTIONS[action]}"))')
    return "\n".join(lines) + "\n"


# The installed ASUS HID driver maps the ROG key to PROG1 and AURA to PROG2.
# These are defaults to review, not a claim that a physical press was captured.
DEFAULT_M5 = 'XF86Launch1'
DEFAULT_AURA = 'XF86Launch3'
PREVIOUS_AURA = 'XF86Launch2'
OWNER = 'Strix Core:'
KEYCODES = {'XF86Launch1': 156, 'XF86Launch2': 157, 'XF86Launch3': 210, 'XF86Launch4': 211}
BEGIN = '-- >>> Strix Core shortcuts (managed)'
END = '-- <<< Strix Core shortcuts (managed)'


def lua_quote(value):
    import json
    return json.dumps(str(value), ensure_ascii=False)


def records_for(m5=DEFAULT_M5, aura=DEFAULT_AURA, action='Open Strix Core', include_previous=False):
    records = [{'event': m5, 'action': action}, {'event': aura, 'action': 'Cycle lighting effects'}]
    if include_previous and aura == DEFAULT_AURA and m5 != PREVIOUS_AURA:
        records.append({'event': PREVIOUS_AURA, 'action': 'Previous lighting effect'})
    keys = []
    for item in records:
        if item['action'] not in ACTIONS:
            raise ValueError('Unknown shortcut action.')
        if item['action'] != 'Keep existing':
            key = validate_event(item['event'])
            if key in keys:
                raise ValueError('M5 and AURA must use different events.')
            keys.append(key)
            item['event'] = key
    return records


def render_shortcuts(records, lua=True):
    # Reuse validation, including duplicate/reserved-event rejection.
    export_bindings(records)
    if not lua:
        return '\n'.join(f"bindd = , {validate_event(r['event'])}, {OWNER} {r['action']}, exec, {ACTIONS[r['action']]}"
                         for r in records if ACTIONS[r['action']]) + '\n'
    lines = ['-- Only bindings owned by Strix Core are replaced.',
             'for _, b in ipairs(_G.strix_core_shortcuts or {}) do pcall(function() b:unbind() end) end',
             '_G.strix_core_shortcuts = {}']
    for item in records:
        command = ACTIONS[item['action']]
        if command:
            lines.append('table.insert(_G.strix_core_shortcuts, hl.bind(' + lua_quote(validate_event(item['event'])) +
                         ', hl.dsp.exec_cmd(' + lua_quote(command) + '), {description=' +
                         lua_quote(OWNER + ' ' + item['action']) + '}))')
    return '\n'.join(lines) + '\n'


class ShortcutManager:
    """Install/remove a dedicated user config with conflict checks and rollback."""
    def __init__(self, storage, config_root=None, runner=None):
        import os
        from pathlib import Path
        from .live import run
        self.storage = storage
        self.config = Path(config_root or os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
        self.runner = runner or run
        self.main = self.config / 'hypr/hyprland.lua'
        if not self.main.exists():
            self.main = self.config / 'hypr/hyprland.conf'
        self.lua = self.main.suffix == '.lua'
        self.begin = BEGIN if self.lua else BEGIN.replace('--', '#', 1)
        self.end = END if self.lua else END.replace('--', '#', 1)
        self.managed = self.config / 'strix-core' / ('hyprland.lua' if self.lua else 'hyprland.conf')

    def bindings(self):
        import json
        data = json.loads(self.runner(('/usr/bin/hyprctl', '-j', 'binds')))
        if not isinstance(data, list):
            raise ValueError('Hyprland returned an invalid binding list.')
        return data

    def status(self):
        owned = [b for b in self.bindings() if str(b.get('description', '')).startswith(OWNER)]
        return {'installed': bool(owned), 'bindings': owned, 'config': str(self.managed)}

    def load(self):
        import json
        try:
            data = json.loads((self.storage.root / 'hotkeys.json').read_text())
            return records_for(data.get('m5', DEFAULT_M5), data.get('aura', DEFAULT_AURA), data.get('action', 'Open Strix Core'), data.get('include_previous', False))
        except (OSError, ValueError, TypeError, AttributeError):
            return records_for()

    @staticmethod
    def checked_text(path, missing=False):
        import os
        import stat
        try:
            info = path.lstat()
        except FileNotFoundError:
            if missing:
                return None
            raise ValueError('No supported Hyprland config was found.') from None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Shortcut config must be a regular file owned by your user; symlinked configs need manual integration.')
        if info.st_size > 1024 * 1024:
            raise ValueError('Config is too large for automatic integration.')
        return path.read_text()

    @staticmethod
    def write(path, text):
        import os
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Do not follow a symlink in the destination or its direct parent.
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError('Refusing a symlinked shortcut destination.')
        previous_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, temp = tempfile.mkstemp(prefix='.strix-core-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, previous_mode)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def without_include(self, text):
        if text.count(self.begin) != text.count(self.end) or text.count(self.begin) > 1:
            raise ValueError('The managed shortcut include was edited; review it manually.')
        if self.begin in text:
            start, tail = text.split(self.begin, 1)
            block, end = tail.split(self.end, 1)
            source = 'dofile(' + lua_quote(self.managed) + ')' if self.lua else 'source = ' + str(self.managed)
            if block != '\n' + source + '\n':
                raise ValueError('The managed include was edited. Review it before applying shortcuts.')
            return start.rstrip('\n') + end
        return text

    def check_managed(self, text):
        if text is None:
            return
        import hashlib
        import json
        try:
            data = json.loads((self.storage.root / 'hotkeys.json').read_text())
            expected = data.get('managed_sha256')
        except (OSError, ValueError, AttributeError):
            expected = None
        if expected is None or hashlib.sha256(text.encode()).hexdigest() != expected:
            raise ValueError('The companion shortcut file was changed outside the app. Review/export it before replacing it.')

    def activate(self):
        if self.lua:
            code = ('for _, b in ipairs(_G.strix_core_shortcuts or {}) do pcall(function() b:unbind() end) end; '
                    '_G.strix_core_shortcuts = {}; ')
            if self.managed.is_file():
                code += 'dofile(' + lua_quote(self.managed) + ')'
            result = self.runner(('/usr/bin/hyprctl', 'eval', code))
        else:
            result = self.runner(('/usr/bin/hyprctl', 'reload'))
        if 'error' in result.lower():
            raise RuntimeError(result)

    def install(self, records):
        import time
        export_bindings(records)
        if self.main.parent.is_symlink() or self.managed.parent.is_symlink():
            raise ValueError('Symlinked config directories require manual integration using the export.')
        records = [{**r, 'event': validate_event(r['event']) if ACTIONS[r['action']] else r.get('event')} for r in records]
        active = [r for r in records if ACTIONS[r['action']]]
        if not active:
            raise ValueError('Choose at least one shortcut action.')
        before = self.bindings()
        for record in active:
            for binding in before:
                if (binding.get('modmask', 0) == 0 and
                        (binding.get('key') == record['event'] or
                         (KEYCODES.get(record['event']) is not None and binding.get('keycode') == KEYCODES[record['event']])) and
                        not str(binding.get('description', '')).startswith(OWNER)):
                    raise ValueError(record['event'] + ' is already bound: ' + str(binding.get('description') or 'existing desktop shortcut'))
        original = self.checked_text(self.main)
        old_managed = self.checked_text(self.managed, missing=True)
        self.check_managed(old_managed)
        clean = self.without_include(original)
        source = 'dofile(' + lua_quote(self.managed) + ')' if self.lua else 'source = ' + str(self.managed)
        include = f'\n{self.begin}\n{source}\n{self.end}\n'
        backup = self.storage.write('shortcut-backup-' + str(time.time_ns()) + '.json',
                                    {'config': str(self.main), 'text': original, 'managed_text': old_managed})
        try:
            self.write(self.managed, render_shortcuts(records, self.lua))
            self.write(self.main, clean.rstrip('\n') + include)
            self.activate()
            after = self.bindings()
            for record in active:
                matches = [b for b in after if b.get('modmask', 0) == 0 and b.get('key') == record['event'] and
                           str(b.get('description', '')).startswith(OWNER)]
                if len(matches) != 1:
                    raise RuntimeError('Hyprland did not confirm exactly one binding for ' + record['event'])
            import hashlib
            self.storage.write('hotkeys.json', {'m5': records[0]['event'], 'action': records[0]['action'],
                                           'aura': records[1]['event'] if len(records) > 1 else DEFAULT_AURA,
                                           'include_previous': any(r['event'] == PREVIOUS_AURA and r['action'] == 'Previous lighting effect' for r in records),
                                               'managed_sha256': hashlib.sha256(self.managed.read_bytes()).hexdigest()})
        except Exception as error:
            try:
                self.write(self.main, original)
                if old_managed is None:
                    self.managed.unlink(missing_ok=True)
                else:
                    self.write(self.managed, old_managed)
                self.activate()
                recovery = 'previous files and bindings restored'
            except Exception:
                recovery = 'automatic recovery failed; use ' + str(backup)
            raise RuntimeError(str(error) + '. ' + recovery) from error
        return {'ok': True, 'bindings': len(active), 'backup': str(backup), 'config': str(self.managed)}

    def remove(self):
        original = self.checked_text(self.main)
        old_managed = self.checked_text(self.managed, missing=True)
        self.check_managed(old_managed)
        clean = self.without_include(original)
        try:
            if self.managed.exists():
                self.managed.unlink()
            self.write(self.main, clean)
            self.activate()
            if any(str(b.get('description', '')).startswith(OWNER) for b in self.bindings()):
                raise RuntimeError('Hyprland still reports an app-owned shortcut.')
        except Exception as error:
            self.write(self.main, original)
            if old_managed is not None:
                self.write(self.managed, old_managed)
            self.activate()
            raise RuntimeError('Shortcut removal failed; the previous configuration was restored: ' + str(error)) from error
        return {'ok': True, 'status': 'Strix Core shortcuts removed; other bindings preserved.'}
