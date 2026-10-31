"""Desktop appearance reads and explicitly requested self-notifications."""
import gi
import time
import os
import errno
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk


class BrightnessMonitor:
    """Read-only kernel notifications plus a lightweight 100 ms fallback."""
    def __init__(self, read, changed, enabled, event_path=None, current=None):
        self.read, self.changed, self.enabled = read, changed, enabled
        self.current = current
        self.fd = self.watch = None
        self.last = object()
        if event_path is not None:
            try:
                self.fd = os.open(event_path, os.O_RDONLY | os.O_CLOEXEC)
                self.acknowledge()
                self.watch = GLib.io_add_watch(self.fd, GLib.PRIORITY_DEFAULT,
                    GLib.IO_PRI | GLib.IO_ERR | GLib.IO_HUP, self.notified)
            except OSError:
                if self.fd is not None:os.close(self.fd)
                self.fd = None
        self.timer = GLib.timeout_add(100, self.refresh)

    def acknowledge(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        try:os.read(self.fd, 64)
        except OSError as error:
            if error.errno != errno.ENODATA:raise

    def notified(self, fd, condition):
        if condition & GLib.IO_HUP:
            self.watch = None
            return GLib.SOURCE_REMOVE
        try:self.acknowledge()
        except OSError:
            self.watch = None
            return GLib.SOURCE_REMOVE
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        if self.enabled():
            value = self.read()  # Read current brightness, never stale hw-event payload.
            if value != (self.current() if self.current else self.last):
                self.last = value
                self.changed(value)
        return GLib.SOURCE_CONTINUE

    def close(self):
        for source in (self.timer, self.watch):
            if source is not None:GLib.source_remove(source)
        self.timer = self.watch = None
        if self.fd is not None:os.close(self.fd)
        self.fd = None


class Desktop:
    def __init__(self, changed):
        self.changed = changed
        self.scheme = None
        self.connection = None
        self.notification_id = 0
        self.notification_sent_at = 0
        self.last_notification = None
        self.closed = False
        self.subscription = None
        self.settings = Gtk.Settings.get_default()
        self.handlers = [self.settings.connect("notify::" + key, lambda *_: changed())
                         for key in ("gtk-theme-name", "gtk-application-prefer-dark-theme", "gtk-font-name")]
        Gio.bus_get(Gio.BusType.SESSION, None, self.connected)

    @property
    def dark(self):
        if self.scheme in (1, 2):
            return self.scheme == 1
        name = self.settings.get_property("gtk-theme-name").lower()
        return "dark" in name or bool(self.settings.get_property("gtk-application-prefer-dark-theme"))

    def connected(self, _, result):
        if self.closed:
            return
        try:
            self.connection = Gio.bus_get_finish(result)
        except GLib.Error:
            return
        self.subscription = self.connection.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Settings", "SettingChanged",
            "/org/freedesktop/portal/desktop", None, Gio.DBusSignalFlags.NONE, self.setting_changed)
        self.connection.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                             "org.freedesktop.portal.Settings", "Read",
                             GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                             None, Gio.DBusCallFlags.NONE, 1500, None, self.read_scheme)

    def read_scheme(self, connection, result):
        try:
            value = connection.call_finish(result).unpack()[0]
            while isinstance(value, GLib.Variant):
                value = value.unpack()
            self.scheme = value if value in (1, 2) else None
            if not self.closed:
                self.changed()
        except GLib.Error:
            pass

    def setting_changed(self, connection, sender, path, interface, signal, parameters):
        namespace, key, value = parameters.unpack()
        if namespace == "org.freedesktop.appearance" and key == "color-scheme":
            self.scheme = value if value in (1, 2) else None
            if not self.closed:
                self.changed()

    def notify_profile(self, profile, preview=True):
        title = profile.title() + (" mode · preview" if preview else " mode")
        body = ("Performance profile preview selected. Laptop settings are unchanged." if preview else
                "Performance profile applied and confirmed by the service and kernel.")
        self.last_notification = {"title": title, "body": body, "status": "pending"}
        if not self.connection:
            self.last_notification["status"] = "unavailable"
            return
        now = time.monotonic()
        replacement = self.notification_id if now - self.notification_sent_at < 2.5 else 0
        self.notification_sent_at = now
        self.connection.call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", ("Strix Core", replacement,
                         "power-profile-" + {"quiet": "power-saver", "balanced": "balanced",
                                              "performance": "performance"}[profile] + "-symbolic",
                         title, body, [], {"urgency": GLib.Variant("y", 1)}, 2500)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 1500, None, self.notified,
            self.last_notification)

    def notified(self, connection, result, record):
        try:
            notification_id = connection.call_finish(result).unpack()[0]
            if record is self.last_notification:
                self.notification_id = notification_id
            record["status"] = "accepted"
        except GLib.Error:
            record["status"] = "unavailable"

    def close(self):
        self.closed = True
        for handler in self.handlers:
            self.settings.disconnect(handler)
        if self.connection and self.subscription:
            self.connection.signal_unsubscribe(self.subscription)
