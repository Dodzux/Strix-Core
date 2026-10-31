import unittest
from unittest.mock import patch
try:
    from strix_core.desktop import Desktop
except (ImportError, ValueError):
    Desktop = None


@unittest.skipIf(Desktop is None, 'Desktop notifications require the optional GTK test environment')
class NotificationTests(unittest.TestCase):
    def make_desktop(self):
        desktop = Desktop.__new__(Desktop)
        desktop.notification_id = 17
        desktop.notification_sent_at = 100
        desktop.last_notification = None
        class Bus:
            def __init__(self):self.calls=[]
            def call(self,*args):self.calls.append(args)
        desktop.connection = Bus()
        return desktop

    def test_rapid_profile_notifications_replace_only_the_active_popup(self):
        desktop = self.make_desktop()
        with patch('strix_core.desktop.time.monotonic', return_value=101):
            desktop.notify_profile('balanced', preview=False)
        args = desktop.connection.calls[-1][4].unpack()
        self.assertEqual(args[1], 17)
        self.assertEqual(args[3], 'Balanced mode')
        self.assertNotIn('preview', args[4])

    def test_expired_popup_uses_a_fresh_id(self):
        desktop = self.make_desktop()
        with patch('strix_core.desktop.time.monotonic', return_value=104):
            desktop.notify_profile('quiet', preview=False)
        self.assertEqual(desktop.connection.calls[-1][4].unpack()[1], 0)

    def test_late_reply_cannot_overwrite_the_newest_notification_id(self):
        desktop = self.make_desktop()
        old = {'status': 'pending'}
        desktop.last_notification = {'status': 'pending'}
        class Reply:
            def call_finish(self,result):
                class Value:
                    def unpack(self):return (99,)
                return Value()
        desktop.notified(Reply(), None, old)
        self.assertEqual(desktop.notification_id, 17)
        self.assertEqual(old['status'], 'accepted')
