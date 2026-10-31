import time
import unittest
from unittest.mock import Mock
from gi.repository import GLib
from strix_core.desktop import BrightnessMonitor


class MonitorTests(unittest.TestCase):
    def test_other_ui_updates_cannot_leave_the_monitor_cache_stale(self):
        current=[2];changed=Mock()
        monitor=BrightnessMonitor(lambda:3,changed,lambda:True,current=lambda:current[0])
        try:
            monitor.last=3;monitor.refresh();changed.assert_called_once_with(3)
        finally:monitor.close()

    def test_fallback_updates_without_waiting_for_the_sensor_worker(self):
        loop=GLib.MainLoop();value=[3];events=[];started=[None]
        def changed(level):
            if level==1:
                events.append(time.monotonic()-started[0]);loop.quit()
        monitor=BrightnessMonitor(lambda:value[0],changed,lambda:True)
        def change():
            started[0]=time.monotonic();value[0]=1
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(20,change)
        deadline=GLib.timeout_add(500,lambda:loop.quit() or GLib.SOURCE_REMOVE)
        try:
            loop.run();self.assertEqual(len(events),1);self.assertLess(events[0],.3)
        finally:
            monitor.close()
            if events:GLib.source_remove(deadline)

    def test_hidden_pages_do_not_poll_and_unchanged_levels_do_not_redraw(self):
        active=[False];read=Mock(return_value=3);changed=Mock()
        monitor=BrightnessMonitor(read,changed,lambda:active[0])
        try:
            monitor.refresh();read.assert_not_called()
            active[0]=True;monitor.refresh();monitor.refresh()
            changed.assert_called_once_with(3)
        finally:monitor.close();monitor.close()
