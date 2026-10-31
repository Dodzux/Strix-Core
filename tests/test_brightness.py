import json
import unittest
from unittest.mock import patch,Mock
from strix_core.model import BrightnessSelection
from strix_core.live import LiveController


class BrightnessSelectionTests(unittest.TestCase):
    def test_initial_and_external_levels_follow_without_a_draft(self):
        s=BrightnessSelection(3)
        for value in (3,2,0,1):
            s.update(value)
            self.assertEqual((s.actual,s.selected,s.pending),(value,value,False))

    def test_live_value_updates_while_a_different_selection_is_pending(self):
        s=BrightnessSelection(3);s.update(3);s.select(1);s.update(2)
        self.assertEqual((s.actual,s.selected,s.pending),(2,1,True))

    def test_apply_clears_the_draft_and_resumes_external_tracking(self):
        s=BrightnessSelection(3);s.update(3);s.select(1);s.applied(1,1);s.update(2)
        self.assertEqual((s.actual,s.selected,s.pending),(2,2,False))

    def test_apply_completion_preserves_a_newer_selection(self):
        s=BrightnessSelection(3);s.update(3);s.select(1);s.select(2);s.applied(1,1)
        self.assertEqual((s.actual,s.selected,s.pending),(1,2,True))

    def test_matching_hardware_change_resolves_a_pending_selection(self):
        s=BrightnessSelection(3);s.update(3);s.select(1);s.update(1)
        self.assertFalse(s.pending)

    def test_missing_or_invalid_readings_remain_unknown(self):
        s=BrightnessSelection(3)
        for value in (None,-1,4,1.5,True,float('nan')):
            s.update(value)
            self.assertIsNone(s.actual)
        with self.assertRaises(ValueError):s.select(4)


class Reader:
    capabilities={'model':'ROG G614PH','brightness_max':3,'profiles':[],'charge_limit':True}


class BrightnessAccessTests(unittest.TestCase):
    @patch('strix_core.live.helper_available',return_value=False)
    @patch('strix_core.live.direct_brightness_available',return_value=True)
    @patch('strix_core.control_helper.HardwareWriter.apply',return_value={'ok':True,'verified':True,'actual':1})
    def test_existing_user_access_avoids_authentication_even_without_helper(self,write,*_):
        runner=Mock(side_effect=AssertionError('No privileged process expected'))
        result=LiveController(Reader(),runner).apply('brightness',1)
        write.assert_called_once_with('brightness',1)
        self.assertEqual(result['authentication'],'not-required')
        runner.assert_not_called()

    @patch('strix_core.live.helper_available',return_value=True)
    @patch('strix_core.live.direct_brightness_available',return_value=False)
    def test_protected_systems_keep_the_authenticated_fallback(self,*_):
        runner=Mock(return_value=json.dumps({'ok':True,'verified':True,'actual':2}))
        LiveController(Reader(),runner).apply('brightness',2)
        self.assertEqual(runner.call_args.args[0][0],'/usr/bin/pkexec')

    @patch('strix_core.live.helper_available',return_value=True)
    @patch('strix_core.live.direct_brightness_available',return_value=True)
    @patch('strix_core.control_helper.HardwareWriter.apply',side_effect=RuntimeError('read-back failure'))
    def test_write_failure_is_not_retried_through_a_privileged_process(self,*_):
        runner=Mock()
        with self.assertRaisesRegex(RuntimeError,'read-back failure'):
            LiveController(Reader(),runner).apply('brightness',1)
        runner.assert_not_called()

    @patch('strix_core.live.helper_available',return_value=True)
    @patch('strix_core.live.direct_brightness_available',return_value=True)
    @patch('strix_core.control_helper.HardwareWriter.apply')
    def test_battery_still_requires_its_authenticated_helper(self,write,*_):
        runner=Mock(return_value=json.dumps({'ok':True,'verified':True,'actual':80}))
        LiveController(Reader(),runner).apply('battery',80)
        write.assert_not_called()
        self.assertEqual(runner.call_args.args[0][0],'/usr/bin/pkexec')
