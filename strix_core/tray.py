"""StatusNotifier tray item and a small DBusMenu, using the existing Gio runtime."""
from pathlib import Path

from gi.repository import Gio, GLib

ITEM_XML = '''<node><interface name="org.kde.StatusNotifierItem">
 <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
 <property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
 <property name="IconThemePath" type="s" access="read"/><property name="IconPixmap" type="a(iiay)" access="read"/>
 <property name="OverlayIconName" type="s" access="read"/><property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
 <property name="AttentionIconName" type="s" access="read"/><property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
 <property name="AttentionMovieName" type="s" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
 <property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
 <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
 <signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>'''
MENU_XML = '''<node><interface name="com.canonical.dbusmenu">
 <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
 <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
 <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
 <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
 <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
 <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
 <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
 <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
 <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
 <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
 <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
 <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
</interface></node>'''


class StatusNotifier:
    labels = {1: 'Open Strix Core', 2: 'Lighting', 3: 'Next lighting effect',
              4: 'Return to desktop lighting', 5: 'Quit Strix Core'}

    def __init__(self, callbacks, assets=None):
        self.callbacks = callbacks
        self.assets = str(assets or Path(__file__).parent / 'assets')
        self.pixmap = []
        try:
            import gi
            gi.require_version('GdkPixbuf', '2.0')
            from gi.repository import GdkPixbuf
            picture = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(Path(self.assets) / 'strix-core.svg'), 48, 48, True)
            width, height = picture.get_width(), picture.get_height()
            pixels, stride, channels = picture.get_pixels(), picture.get_rowstride(), picture.get_n_channels()
            argb = bytearray()
            for y in range(height):
                for x in range(width):
                    offset = y * stride + x * channels
                    argb.extend((pixels[offset + 3] if channels == 4 else 255, *pixels[offset:offset + 3]))
            self.pixmap = [(width, height, bytes(argb))]
        except (ImportError, ValueError, GLib.Error, OSError):
            pass
        self.connection = None
        self.registrations = []
        self.watch = None
        self.available = False
        self.closed = False
        self.status_text = 'Laptop controls and background lighting'
        self.attention = False
        Gio.bus_get(Gio.BusType.SESSION, None, self.connected)

    def connected(self, _, result):
        if self.closed:
            return
        try:
            self.connection = Gio.bus_get_finish(result)
            for path, xml in (('/StatusNotifierItem', ITEM_XML), ('/MenuBar', MENU_XML)):
                info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                self.registrations.append(self.connection.register_object(path, info, self.method, self.property, None))
            self.watch = Gio.bus_watch_name_on_connection(self.connection, 'org.kde.StatusNotifierWatcher',
                Gio.BusNameWatcherFlags.NONE, self.watcher_appeared, self.watcher_vanished)
        except GLib.Error:
            self.available = False

    def watcher_appeared(self, connection, name, owner):
        if self.closed:
            return
        connection.call(name, '/StatusNotifierWatcher', 'org.kde.StatusNotifierWatcher',
            'RegisterStatusNotifierItem', GLib.Variant('(s)', ('/StatusNotifierItem',)), None,
            Gio.DBusCallFlags.NONE, 2000, None, self.registered)

    def registered(self, connection, result):
        try:
            connection.call_finish(result)
            self.available = not self.closed
        except GLib.Error:
            self.available = False

    def watcher_vanished(self, *_):
        self.available = False

    def property(self, connection, sender, path, interface, name):
        if interface == 'com.canonical.dbusmenu':
            values = {'Version': ('u', 3), 'TextDirection': ('s', 'ltr'), 'Status': ('s', 'normal'),
                      'IconThemePath': ('as', [self.assets])}
        else:
            values = {'Category': ('s', 'Hardware'), 'Id': ('s', 'strix-core'), 'Title': ('s', 'Strix Core'),
                      'Status': ('s', 'NeedsAttention' if self.attention else 'Active'), 'WindowId': ('i', 0), 'IconName': ('s', 'io.github.hyprdodz.StrixCore'),
                      'IconThemePath': ('s', self.assets), 'IconPixmap': ('a(iiay)', self.pixmap),
                      'OverlayIconName': ('s', ''), 'OverlayIconPixmap': ('a(iiay)', []),
                      'AttentionIconName': ('s', 'dialog-warning-symbolic'), 'AttentionIconPixmap': ('a(iiay)', []),
                      'AttentionMovieName': ('s', ''), 'ToolTip': ('(sa(iiay)ss)', ('io.github.hyprdodz.StrixCore', self.pixmap, 'Strix Core', self.status_text)),
                      'ItemIsMenu': ('b', False), 'Menu': ('o', '/MenuBar')}
        return GLib.Variant(*values[name]) if name in values else None

    def update(self, text, attention=False):
        if (text, attention) == (self.status_text, self.attention):
            return
        self.status_text, self.attention = text, attention
        if self.connection and not self.closed:
            self.connection.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'NewToolTip', None)
            self.connection.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'NewStatus',
                                        GLib.Variant('(s)', ('NeedsAttention' if attention else 'Active',)))

    def properties(self, item, names=()):
        data = {'children-display': GLib.Variant('s', 'submenu')} if item == 0 else {
            'label': GLib.Variant('s', self.labels.get(item, '')),
            'enabled': GLib.Variant('b', item in self.labels), 'visible': GLib.Variant('b', True)}
        return {k: v for k, v in data.items() if not names or k in names}

    def layout(self, parent, depth, names=()):
        children = [GLib.Variant('(ia{sv}av)', (i, self.properties(i, names), [])) for i in self.labels] if parent == 0 and depth != 0 else []
        return parent, self.properties(parent, names), children

    def invoke(self, item):
        callback = self.callbacks.get(item)
        if callback:
            def fire():
                if not self.closed:
                    callback()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(fire)

    def method(self, connection, sender, path, interface, method, parameters, invocation):
        args = parameters.unpack()
        if interface == 'org.kde.StatusNotifierItem':
            invocation.return_value(GLib.Variant('()', ()))
            if method in ('Activate', 'SecondaryActivate', 'ContextMenu'):
                self.invoke(1)
            return
        if method == 'GetLayout':
            value = GLib.Variant('(u(ia{sv}av))', (1, self.layout(*args)))
        elif method == 'GetGroupProperties':
            value = GLib.Variant('(a(ia{sv}))', ([(i, self.properties(i, args[1])) for i in args[0] if i == 0 or i in self.labels],))
        elif method == 'GetProperty':
            prop = self.properties(args[0]).get(args[1], GLib.Variant('s', ''))
            value = GLib.Variant('(v)', (prop,))
        elif method == 'AboutToShow':
            value = GLib.Variant('(b)', (False,))
        elif method == 'AboutToShowGroup':
            value = GLib.Variant('(aiai)', ([], []))
        elif method in ('Event', 'EventGroup'):
            events = [args] if method == 'Event' else args[0]
            for item, event, data, timestamp in events:
                if event == 'clicked':
                    self.invoke(item)
            value = GLib.Variant('()', ()) if method == 'Event' else GLib.Variant('(ai)', ([],))
        else:
            invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownMethod', method)
            return
        invocation.return_value(value)

    def close(self):
        self.closed = True
        if self.watch is not None:
            Gio.bus_unwatch_name(self.watch)
            self.watch = None
        if self.connection:
            for registration in self.registrations:
                self.connection.unregister_object(registration)
        self.registrations.clear()
        self.available = False
