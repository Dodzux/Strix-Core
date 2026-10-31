import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strix_core.hotkeys import ShortcutManager, records_for, OWNER
from strix_core.lighting_state import LightingStore
from strix_core.model import Storage


class LightingPersistenceTests(unittest.TestCase):
    def test_palette_survives_cycle_and_reload_without_device_access(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LightingStore(Storage(Path(folder)))
            zones = ['#112233', '#334455', '#556677', '#778899']
            store.save({'effect': 'Wallpaper sync', 'zones': zones, 'intensity': 30})
            next_pattern = store.next_effect()
            self.assertEqual(next_pattern['effect'], 'Color cycle')
            self.assertEqual(next_pattern['zones'], zones)
            self.assertEqual(next_pattern['intensity'], 30)
            self.assertEqual(store.load()['effect'], 'Wallpaper sync')

    def test_corrupt_saved_data_falls_back_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder))
            for bad in ('{', '[]', '{"effect":"Static","zones":["bad"]}'):
                (storage.root / 'lighting.json').write_text(bad)
                self.assertEqual(LightingStore(storage).load()['effect'], 'Static')


class ShortcutTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main = self.root / 'config/hypr/hyprland.lua'
        self.main.parent.mkdir(parents=True)
        self.original = '-- user config\nhl.bind("XF86Launch4", function() end)\n'
        self.main.write_text(self.original)
        self.active = [{'key': 'XF86Launch4', 'modmask': 0, 'description': 'Existing performance'}]
        self.fail_activation = False
        self.manager = ShortcutManager(Storage(self.root / 'state'), self.root / 'config', self.runner)

    def runner(self, args, **kwargs):
        if args[-1] == 'binds':
            return json.dumps(self.active)
        if args[1] in ('eval', 'reload'):
            if self.fail_activation:
                self.fail_activation = False
                raise RuntimeError('synthetic compositor failure')
            self.active = [b for b in self.active if not b['description'].startswith(OWNER)]
            if self.manager.managed.exists():
                text = self.manager.managed.read_text()
                for key, name in (('XF86Launch1', 'Open Strix Core'), ('XF86Launch3', 'Cycle lighting effects'), ('XF86Launch2', 'Previous lighting effect')):
                    if key in text:
                        self.active.append({'key': key, 'modmask': 0, 'description': OWNER + ' ' + name})
            return 'ok'
        raise AssertionError(args)

    def test_install_is_idempotent_preserves_performance_and_can_be_removed(self):
        self.manager.install(records_for())
        self.manager.install(records_for())
        self.assertEqual(self.main.read_text().count('dofile('), 1)
        self.assertEqual(len(self.active), 3)
        self.assertEqual(self.active[0]['key'], 'XF86Launch4')
        self.manager.remove()
        self.assertEqual(self.main.read_text().strip(), self.original.strip())
        self.assertFalse(self.manager.managed.exists())
        self.assertEqual(len(self.active), 1)

    def test_existing_foreign_binding_is_not_overwritten(self):
        self.active.append({'key': 'XF86Launch1', 'modmask': 0, 'description': 'User command'})
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.manager.install(records_for())
        self.assertEqual(self.main.read_text(), self.original)
        self.assertFalse(self.manager.managed.exists())

    def test_activation_failure_rolls_back_both_files(self):
        self.fail_activation = True
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.manager.install(records_for())
        self.assertEqual(self.main.read_text(), self.original)
        self.assertFalse(self.manager.managed.exists())
        self.assertEqual(len(self.active), 1)

    def test_symlinked_main_config_is_not_replaced(self):
        target = self.root / 'original.lua'
        self.main.rename(target)
        self.main.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.manager.install(records_for())
        self.assertTrue(self.main.is_symlink())
        self.assertEqual(target.read_text(), self.original)

    def test_raw_keycode_conflict_is_not_overwritten(self):
        self.active.append({'key': '', 'keycode': 156, 'modmask': 0, 'description': 'User raw binding'})
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.manager.install(records_for())
        self.assertEqual(self.main.read_text(), self.original)

    def test_external_edits_to_companion_are_preserved(self):
        self.manager.install(records_for())
        edited = self.manager.managed.read_text() + '-- user edit\n'
        self.manager.managed.write_text(edited)
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            self.manager.install(records_for())
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            self.manager.remove()
        self.assertEqual(self.manager.managed.read_text(), edited)

    def test_edited_include_is_not_silently_deleted(self):
        self.manager.install(records_for())
        edited = self.main.read_text().replace('dofile(', 'print(')
        self.main.write_text(edited)
        with self.assertRaisesRegex(ValueError, 'edited'):
            self.manager.remove()
        self.assertEqual(self.main.read_text(), edited)

    def test_removal_failure_restores_live_bindings(self):
        self.manager.install(records_for())
        previous = self.main.read_text()
        self.fail_activation = True
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.manager.remove()
        self.assertEqual(self.main.read_text(), previous)
        self.assertEqual(len(self.active), 3)

    def test_legacy_include_uses_valid_comment_markers(self):
        self.main.unlink()
        self.main = self.main.with_suffix('.conf')
        self.main.write_text('# original user config\n')
        self.manager = ShortcutManager(Storage(self.root / 'state'), self.root / 'config', self.runner)
        self.manager.install(records_for())
        self.assertIn('# >>> Strix Core', self.main.read_text())
        self.assertNotIn('-- >>>', self.main.read_text())
        self.assertIn('source = ', self.main.read_text())
        self.manager.remove()
        self.assertEqual(self.main.read_text().strip(), '# original user config')

    def test_reserved_audio_and_performance_keys_are_rejected(self):
        for key in ('F4', 'F5', 'XF86Launch4', 'XF86AudioMicMute', 'XF86AudioRaiseVolume'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.manager.install(records_for(m5=key))
        self.assertEqual(self.main.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
