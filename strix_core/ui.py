"""Native GTK controls, background lighting and desktop integration."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math
import re
from pathlib import Path
import time
import cairo

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import __version__
from .backend import Reader
from .model import OPTIONAL, PAGES, Preview, Storage, BrightnessSelection
from .desktop import Desktop, BrightnessMonitor
from .hotkeys import ACTIONS, DEFAULT_M5, DEFAULT_AURA, PREVIOUS_AURA, ShortcutManager, export_bindings, validate_event, records_for
from .lighting import ANIMATED, EFFECTS, sample, validate_lighting, rgb as lighting_rgb
from .lighting_state import LightingStore
from .live import LiveController

ASSETS = Path(__file__).parent / "assets"
ICONS = ("view-grid-symbolic", "power-profile-performance-symbolic", "weather-windy-symbolic",
         "video-display-symbolic", "keyboard-brightness-symbolic", "battery-good-symbolic",
         "application-x-executable-symbolic", "software-update-available-symbolic",
         "input-keyboard-symbolic", "applications-graphics-symbolic", "dialog-information-symbolic")
DESCRIPTIONS = {
    "Dashboard": "Your laptop, at a glance.",
    "Performance": "Find your balance between speed, temperature and noise.",
    "Fans": "A little more control over your cooling.",
    "GPU": "Choose how your laptop uses its graphics hardware.",
    "Lighting": "Make your keyboard feel at home on your desktop.",
    "Battery": "Keep a little room for a healthier battery.",
    "Memory": "Review what is using your RAM.",
    "Update Center": "Keep your CachyOS system together and up to date.",
    "Hotkeys": "Your most useful controls, a shortcut away.",
    "Appearance": "A small control center that feels like yours.",
    "Diagnostics": "A local snapshot for troubleshooting.",
}


def box(vertical=True, spacing=12, **kwargs):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                   spacing=spacing, **kwargs)


def label(text="", css=None, wrap=False, **kwargs):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, **kwargs)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def button(text, callback, primary=False):
    widget = Gtk.Button(label=text)
    widget.add_css_class("primary" if primary else "secondary")
    widget.connect("clicked", lambda _: callback())
    return widget


def card(title=None, subtitle=None):
    widget = box(spacing=14)
    widget.add_css_class("card")
    if title:
        widget.append(label(title, "section-title"))
    if subtitle:
        widget.append(label(subtitle, "subtitle", wrap=True))
    return widget


def row(title, detail=None, control=None):
    widget = box(False, 18)
    widget.add_css_class("row")
    content = box(spacing=5, hexpand=True)
    content.append(label(title))
    if detail:
        content.append(label(detail, "subtitle", wrap=True))
    widget.append(content)
    if control:
        control.set_valign(Gtk.Align.CENTER)
        widget.append(control)
    return widget


def value_text(value, unit="", digits=0):
    return "—" if value is None else f"{value:,.{digits}f}{unit}"


def gib(value):
    return value_text(value / 1024 ** 3 if value is not None else None, " GiB", 1)


class Plot(Gtk.DrawingArea):
    def __init__(self, window, height=45, curve=False, keyboard=False):
        super().__init__()
        self.owner, self.values, self.points = window, [], []
        self.curve, self.keyboard, self.color, self.effect = curve, keyboard, None, "Static"
        self.zones = ["#cd467a", "#b582d6", "#79bfde", "#8cd3b1"]
        self.phase, self.speed, self.selected_zone = 0, 1, 0
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            "Keyboard lighting preview" if keyboard else "Fan curve preview" if curve else "Recent usage graph"])

    def push(self, value):
        self.values = (self.values + [value])[-45:]
        self.queue_draw()

    @staticmethod
    def rgb(color):
        return lighting_rgb(color)

    def draw_keyboard(self, cr, width, height):
        zones = [getattr(self, 'wallpaper_color', self.owner.accent)] * 4 if self.effect == 'Wallpaper sync' else self.zones
        cache_key = (width, height, tuple(zones), self.selected_zone)
        if getattr(self, '_keyboard_cache_key', None) != cache_key:
            key_width = min(39, (width-36)/14)
            total = 14*key_width
            left, top = (width-total)/2, 28
            surface = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)
            static = cairo.Context(surface)
            static.select_font_face('CaskaydiaCove Nerd Font',0,0);static.set_font_size(10)
            for zone in range(4):
                color = self.rgb(zones[zone])
                static.set_source_rgba(*color,.06)
                static.rectangle(left+zone*total/4-3,top-12,total/4,154);static.fill()
                static.set_source_rgba(*color,.95 if zone==self.selected_zone else .35)
                static.set_line_width(2 if zone==self.selected_zone else 1)
                static.rectangle(left+zone*total/4-3,top-12,total/4,154);static.stroke()
                static.move_to(left+zone*total/4+8,top+164);static.show_text(f'ZONE {zone+1}')
                static.set_source_rgba(*color,.9)
                static.rectangle(left+zone*total/4,top+190,total/4-4,4);static.fill()
            paths=[]
            for zone in range(4):
                static.new_path()
                for y in range(5):
                    for x in range(14):
                        if int((x+.5)/14*4) != zone or (y==4 and 3<x<9):continue
                        size=key_width*6-5 if y==4 and x==3 else key_width-5
                        static.rectangle(left+x*key_width,top+y*25,size,19)
                paths.append(static.copy_path())
            self._keyboard_cache_key=cache_key
            self._keyboard_surface,self._keyboard_paths=surface,paths
        cr.set_source_surface(self._keyboard_surface);cr.paint();cr.set_line_width(1)
        # The hardware has four zones. Sample each once and reuse cached key
        # outlines instead of parsing/repainting 65 independent key animations.
        for zone,path in enumerate(self._keyboard_paths):
            color=tuple(c*getattr(self,'intensity',1) for c in sample(self.effect,zones,(zone+.5)/4,self.phase,self.speed))
            cr.append_path(path);cr.set_source_rgba(*color,.2);cr.fill_preserve()
            cr.set_source_rgba(*color,.9);cr.stroke()

    def draw(self, area, cr, width, height):
        color = self.rgb(self.color or self.owner.accent)
        if self.keyboard:
            self.draw_keyboard(cr,width,height)
            return
        if self.curve:
            left, top, right, bottom = 42, 16, width - 22, height - 34
            cr.set_font_size(10)
            for tick in (0, 25, 50, 75, 100):
                y = bottom - tick / 100 * (bottom - top)
                cr.set_source_rgba(*color, .1)
                cr.move_to(left, y); cr.line_to(right, y); cr.stroke()
                cr.set_source_rgba(*color, .65)
                cr.move_to(4, y + 4); cr.show_text(str(tick) + "%")
            for temp in (20, 40, 60, 80, 100):
                x = left + (temp - 20) / 85 * (right - left)
                cr.set_source_rgba(*color, .65)
                cr.move_to(x - 8, height - 10); cr.show_text(str(temp) + "°")
            coords = [(left + (t - 20) / 85 * (right - left), bottom - pwm / 255 * (bottom - top))
                      for t, pwm in self.points]
        else:
            left, bottom = 0, height - 3
            coords = [(i / 44 * width, bottom - max(0, min(v, 100)) / 100 * (height - 6))
                      if v is not None else None for i, v in enumerate(self.values)]
        cr.set_line_width(2)
        cr.set_source_rgba(*color, .85)
        pen = False
        for point in coords:
            if point is None:
                pen = False
                continue
            (cr.line_to if pen else cr.move_to)(*point)
            pen = True
        cr.stroke()
        if self.curve:
            for x, y in coords:
                cr.arc(x, y, 4, 0, math.tau); cr.fill()


class Metric(Gtk.Box):
    def __init__(self, owner, title, icon):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.add_css_class("card")
        heading = box(False, 8)
        heading.append(label(title, "metric-title", hexpand=True))
        image = Gtk.Image.new_from_icon_name(icon)
        image.add_css_class("muted")
        heading.append(image)
        self.append(heading)
        self.value = label("—", "metric-value")
        self.detail = label("Waiting for sensors", "metric-detail", wrap=True)
        self.plot = Plot(owner)
        self.append(self.value); self.append(self.detail); self.append(self.plot)

    def update(self, value, detail, graph):
        self.value.set_text(value)
        self.detail.set_text(detail)
        self.plot.push(graph)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, reader, storage):
        super().__init__(application=app, title="Strix Core")
        self.add_css_class("app-window")
        self.set_default_size(1160, 850)
        self.set_size_request(900, 650)
        self.reader, self.storage = reader, storage
        self.cap = reader.capabilities
        self.preview = Preview(self.cap)
        self.test_mode = app.test_mode
        self.controller = None if self.test_mode else LiveController(reader)
        self.lighting_store = LightingStore(storage)
        self.shortcut_manager = ShortcutManager(storage)
        self.preferences = storage.preferences()
        self.accent = (reader.accent() or "#86d6b3") if self.preferences['wallpaper_accent'] else "#86d6b3"
        self.syncing_wallpaper_accent = False
        self.latest, self.last_sample = {}, None
        self.pending_lighting = None
        self.pending_rgb_stop = False
        self.last_rgb_error = None
        self.alive, self.pending, self.busy = True, False, set()
        self.animation_timer, self.toast_timer = None, None
        self.animation_start = time.monotonic()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="strix_core-reader")
        self.css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.css_provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.desktop = Desktop(self.desktop_changed)
        self.apply_theme()
        icons = Gtk.IconTheme.get_for_display(self.get_display())
        icons.add_search_path(str(ASSETS))
        self.set_icon_name("strix-core")
        header = Gtk.HeaderBar()
        header.set_show_title_buttons(False)
        header.set_title_widget(label("Strix Core", "small"))
        chip = label("TEST MODE" if self.test_mode else "LIVE CONTROLS", "chip amber" if self.test_mode else "chip")
        chip.set_tooltip_text("Hardware changes are simulated." if self.test_mode else
                             "Supported controls are live. G614PH RGB uses tested native keyboard-zone commands without store/save. GPU and custom fan writes remain unavailable.")
        header.pack_start(chip)
        self.live_chip = label("CONNECTING", "chip neutral")
        header.pack_end(self.live_chip)
        self.set_titlebar(header)
        main = box(False, 0)
        overlay = Gtk.Overlay()
        overlay.set_child(main)
        self.set_child(overlay)
        self.toast = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.toast.set_halign(Gtk.Align.END); self.toast.set_valign(Gtk.Align.END)
        self.toast.set_margin_bottom(58); self.toast.set_margin_end(20)
        toast_content = card()
        toast_content.add_css_class("toast")
        self.toast_title = label("", "section-title", wrap=True, max_width_chars=36)
        self.toast_body = label("", "subtitle", wrap=True, max_width_chars=36)
        toast_content.append(self.toast_title); toast_content.append(self.toast_body)
        self.toast.set_child(toast_content); overlay.add_overlay(self.toast)
        self.sidebar = box(spacing=3)
        self.sidebar.add_css_class("sidebar")
        self.sidebar.set_size_request(204, -1)
        brand = box(False, 10)
        image = Gtk.Image.new_from_file(str(ASSETS / "strix-core.svg"))
        image.set_pixel_size(38)
        brand.append(image)
        brand_text = box(spacing=3)
        brand_text.append(label("Strix Core", "brand")); brand_text.append(label("ROG CONTROL CENTER", "brand-sub"))
        brand.append(brand_text)
        self.sidebar.append(brand)
        self.nav = {}
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                               hexpand=True, vexpand=True)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        for index, page in enumerate(PAGES):
            if index in (0, 1, 6):
                self.sidebar.append(label({0: "OVERVIEW", 1: "LAPTOP", 6: "WORKSPACE"}[index], "nav-section"))
            nav = Gtk.Button()
            nav.add_css_class("nav")
            inner = box(False, 10)
            inner.append(Gtk.Image.new_from_icon_name(ICONS[index])); inner.append(label(page))
            nav.set_child(inner)
            nav.connect("clicked", lambda _, p=page: self.navigate(p))
            self.nav[page] = nav
            self.sidebar.append(nav)
        spacer = box(vexpand=True)
        self.sidebar.append(spacer)
        self.sidebar.append(label("STRIX CORE", "eyebrow"))
        self.sidebar.append(label("LOCAL PREVIEW" if self.test_mode else "LOCAL CONTROL", "small muted"))
        main.append(self.sidebar)
        right = box(spacing=0, hexpand=True)
        right.append(self.stack)
        self.memory_footer = box(False, 12)
        self.memory_footer.add_css_class("memory-footer")
        right.append(self.memory_footer)
        self.lighting_footer = box(False, 10)
        self.lighting_footer.add_css_class("memory-footer")
        right.append(self.lighting_footer)
        self.hotkeys_footer = box(False, 10)
        self.hotkeys_footer.add_css_class('memory-footer')
        right.append(self.hotkeys_footer)
        self.status = label("Starting local monitoring…", "status-bar", wrap=True)
        right.append(self.status)
        main.append(right)
        self.pages = {}
        for page in PAGES:
            content = box(spacing=22)
            content.add_css_class("page")
            heading = box(spacing=7)
            heading.append(label(page, "page-title"))
            heading.append(label(DESCRIPTIONS[page], "subtitle", wrap=True))
            content.append(heading)
            self.pages[page] = content
            getattr(self, "build_" + page.lower().replace(" ", "_"))(content)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(content)
            self.stack.add_named(scroll, page)
        self.update_modules()
        initial = self.preferences["start_page"]
        self.navigate(initial if self.nav[initial].get_visible() else "Dashboard")
        self.connect("close-request", self.close_requested)
        self.connect("map", self.window_mapped)
        self.timer = GLib.timeout_add_seconds(2, self.poll)
        self.brightness_monitor = BrightnessMonitor(self.reader.brightness, self.update_brightness,
            lambda: self.alive and self.get_mapped() and self.stack.get_visible_child_name() == 'Lighting',
            self.reader.path('/sys/class/leds/asus::kbd_backlight/brightness_hw_changed'),
            current=lambda: self.brightness_selection.actual)
        self.poll()
        if not self.test_mode:
            GLib.idle_add(self.refresh_services)

    def apply_theme(self):
        palettes = {
            "Dark": ("#111111", "#181818", "#202020", "#353535", "#eeeeee", "#a6a6a6"),
            "Light": ("#f4f4f4", "#e9e9e9", "#ffffff", "#d1d1d1", "#202020", "#5a5a5a"),
        }
        chosen = self.preferences["theme"]
        self.resolved_theme = ("Dark" if self.desktop.dark else "Light") if chosen == "System" else chosen
        colors = palettes[self.resolved_theme]
        # Keep text contrast usable on light surfaces, while retaining the wallpaper hue.
        accent = self.accent
        if self.resolved_theme == "Light":
            import colorsys
            rgb = Plot.rgb(accent)
            h, _, s = colorsys.rgb_to_hls(*rgb)
            accent = "#" + "".join(f"{round(v * 255):02x}" for v in colorsys.hls_to_rgb(h, .30, max(s, .3)))
        defs = "\n".join(f"@define-color {key} {value};" for key, value in
                         zip(("bg", "side", "card", "line", "text", "muted"), colors))
        font = Pango.FontDescription.from_string(Gtk.Settings.get_default().get_property('gtk-font-name'))
        self.font_family = font.get_family() or 'sans-serif'
        quoted_font = self.font_family.replace('\\', '\\\\').replace('"', '\\"')
        self.css_provider.load_from_data((defs + f"\n@define-color accent {accent};\n" +
                                         f"@define-color on_accent {'#111111' if sum(Plot.rgb(accent)) > 1.7 else '#ffffff'};\n" +
                                         (ASSETS / "style.css").read_text() +
                                         '\nwindow.app-window { font-family: "' + quoted_font + '"; }').encode())

    def desktop_changed(self):
        if self.alive:
            self.refresh_wallpaper_accent(force=True)

    def refresh_wallpaper_accent(self, force=False):
        """Resolve current source and reconcile the control without toggling prefs."""
        saved = self.reader.accent()
        self.latest['accent'] = saved
        enabled = self.preferences['wallpaper_accent']
        accent = (saved or '#86d6b3') if enabled else '#86d6b3'
        changed = accent != self.accent
        self.accent = accent
        switch = getattr(self, 'wallpaper_accent_switch', None)
        if switch and (switch.get_active() != enabled or switch.get_state() != enabled):
            self.syncing_wallpaper_accent = True
            try:
                switch.set_active(enabled)
                switch.set_state(enabled)
            finally:
                self.syncing_wallpaper_accent = False
        if changed or force:
            self.apply_theme()
        if hasattr(self, 'wallpaper_label'):
            self.wallpaper_label.set_text('Wallpaper accent: ' + (saved or 'No saved color found'))
        self.draw_lighting()

    def window_mapped(self, *_):
        self.refresh_wallpaper_accent(force=True)
        if hasattr(self, 'brightness_selection'):
            self.update_brightness(self.reader.brightness(), force=True)
        self.update_animation()

    def show_toast(self, title, body):
        self.toast_title.set_text(title); self.toast_body.set_text(body)
        self.toast.set_reveal_child(True)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)
        def hide():
            self.toast.set_reveal_child(False); self.toast_timer = None
            return GLib.SOURCE_REMOVE
        self.toast_timer = GLib.timeout_add(4000, hide)

    def navigate(self, page):
        if page not in self.nav or not self.nav[page].get_visible():
            return
        self.stack.set_visible_child_name(page)
        self.memory_footer.set_visible(page == "Memory")
        self.lighting_footer.set_visible(page == "Lighting")
        self.hotkeys_footer.set_visible(page == 'Hotkeys')
        self.toast.set_margin_bottom(140 if page in ("Memory", "Lighting", 'Hotkeys') else 58)
        self.toast.set_reveal_child(False)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer); self.toast_timer = None
        self.update_animation()
        for name, nav in self.nav.items():
            if name == page:
                nav.add_css_class("active")
            else:
                nav.remove_css_class("active")

    def update_modules(self):
        for name, nav in self.nav.items():
            nav.set_visible(name not in OPTIONAL or name in self.preferences["modules"])
        current = self.stack.get_visible_child_name()
        if current and not self.nav[current].get_visible():
            self.navigate("Dashboard")

    def message(self, text):
        self.status.set_text(text)

    def perform_action(self, kind, value):
        if not self.test_mode:
            if not self.controller.supported(kind):
                reason = getattr(self, 'service_statuses', {}).get(kind, {}).get('reason')
                self.show_toast("Control unavailable", reason or "This control is not supported by the detected hardware or installed services.")
                return False
            if "control" in self.busy:
                self.message("Wait for the current operation to finish.")
                return False
            if kind in ("close", "upgrade") or (kind == "lighting" and self.controller.rgb_session is None):
                detail = ("Selected applications may have unsaved work. Normal close requests will be sent; no force-kill is used."
                          if kind == "close" else "An interactive terminal will open. Review and authenticate the pacman upgrade there."
                          if kind == "upgrade" else "Strix Core will control the four keyboard zones using native color/apply commands. Closing to the tray keeps effects running. Stop or Quit restores your saved desktop color in Static mode; the prior firmware effect cannot be read back. Store/save commands are blocked.")
                title = {"close": "Close selected applications?", "upgrade": "Open system upgrade?", "lighting": "Apply live lighting?"}[kind]
                dialog = Gtk.AlertDialog(message=title,
                                         detail=detail, buttons=["Cancel", "Continue"], cancel_button=0, default_button=0)
                def chosen(dialog, result):
                    try:
                        if dialog.choose_finish(result) == 1 and self.alive:
                            self.run_control(kind, value)
                    except GLib.Error:
                        pass
                dialog.choose(self, None, chosen)
                # Gtk.AlertDialog creates a separate native MessageDialog; it
                # does not inherit its parent's CSS classes. Give that surface
                # real alpha so compositor blur also works with ignore_opacity.
                for window in Gtk.Window.list_toplevels():
                    if isinstance(window, Gtk.MessageDialog) and window.get_transient_for() == self:
                        window.add_css_class('app-window')
                        window.add_css_class('app-dialog')
            else:
                self.run_control(kind, value)
            return False
        try:
            self.preview.plan(kind, value)
        except (ValueError, TypeError, AttributeError) as error:
            self.message(str(error))
            return False
        self.message("Preview saved · " + {"profile": "performance mode", "curve": "fan curve",
                     "gpu": "GPU mode", "lighting": "lighting", "battery": "charge limit",
                     "brightness": "keyboard brightness", "close": "selected processes",
                     "upgrade": "full system upgrade"}[kind] + " · nothing applied")
        if kind == "profile":
            self.show_toast(value.title() + " mode · preview", "Laptop profile unchanged. Test selection saved.")
            self.desktop.notify_profile(value)
        return True

    def run_control(self, kind, value):
        self.message("Applying " + kind + "…")
        def worker():
            try:
                return {"ok": True, "event": self.controller.apply(kind, value)}
            except Exception as error:
                return {"ok": False, "error": str(error)}
        def finished(result):
            if not result["ok"]:
                if kind == 'lighting':
                    self.last_rgb_error = result['error']
                self.message(result["error"])
                self.show_toast("Operation did not complete", result["error"])
                GLib.idle_add(self.drain_rgb_request)
                return
            event = result["event"]
            if kind == "profile":
                self.desktop.notify_profile(value, preview=False)
                title, detail = value.title() + " mode", "Confirmed by the profile service and kernel."
            elif kind in ("battery", "brightness"):
                title, detail = "Setting applied", "Read-back confirmed: " + str(event["actual"]) + ("% charge limit" if kind == "battery" else " keyboard brightness")
                if kind == 'brightness':
                    self.brightness_selection.applied(value, event['actual'])
                    self.refresh_brightness_controls()
            elif kind == "close":
                title, detail = "Close requests sent", "Applications may ask you to save work. RAM released is not guaranteed."
                self.refresh_processes()
            elif kind == "lighting":
                self.last_rgb_error = None
                title, detail = "RGB output active", "Effects continue in the tray. Device color read-back is unavailable."
                try:
                    self.lighting_store.save(value)
                except (OSError, ValueError) as error:
                    detail += " Settings could not be saved: " + str(error)
            else:
                title, detail = "Upgrade terminal opened", "Pacman will ask for authentication and confirmation there."
            self.message(title + " · " + detail); self.show_toast(title, detail)
            self.poll()
            GLib.idle_add(self.drain_rgb_request)
        self.task("control", worker, finished)

    def drain_rgb_request(self):
        if not self.alive or 'control' in self.busy:
            return GLib.SOURCE_REMOVE
        if self.pending_rgb_stop:
            self.pending_rgb_stop = False
            self.stop_rgb()
        elif self.pending_lighting is not None:
            value, self.pending_lighting = self.pending_lighting, None
            self.run_control('lighting', value)
        return GLib.SOURCE_REMOVE

    def task(self, key, worker, callback):
        if not self.alive or key in self.busy:
            return
        self.busy.add(key)
        def done(future):
            try:
                result, error = future.result(), None
            except Exception as exc:
                result, error = None, type(exc).__name__
            def deliver():
                self.busy.discard(key)
                if self.alive:
                    if error:
                        if key == "telemetry":
                            self.pending = False
                            self.live_chip.set_text("SENSOR ERROR")
                            self.clear_telemetry()
                        self.message("Could not read " + key + ": " + error)
                    else:
                        callback(result)
                return GLib.SOURCE_REMOVE
            if self.alive:
                GLib.idle_add(deliver)
        self.pool.submit(worker).add_done_callback(done)

    def poll(self):
        if not self.alive:
            return GLib.SOURCE_REMOVE
        if self.controller and self.controller.rgb_session:
            session = self.controller.rgb_session.status()
            rendering = ('firmware animation' if session.get('output_mode') == 'firmware' else
                         str(session.get('fps',0)) + ' fps' if session.get('effect') in ANIMATED else
                         str(session['frames']) + ' frames')
            self.rgb_status.set_text((session.get('effect') or 'Lighting') + ' · ' + session['state'] +
                                     ' · ' + rendering)
            if session.get('error'):
                self.rgb_status.set_text('RGB stopped · ' + session['error'])
            tray = getattr(self.get_application(), 'tray', None)
            if tray:
                tray.update(self.rgb_status.get_text(), bool(session.get('error')))
        elif hasattr(self, 'rgb_status') and not self.test_mode:
            self.rgb_status.set_text('RGB needs attention · ' + self.last_rgb_error if self.last_rgb_error else 'Desktop lighting · no active app effect')
            tray = getattr(self.get_application(), 'tray', None)
            if tray:
                tray.update(self.rgb_status.get_text(), bool(self.last_rgb_error))
        if hasattr(self, "metric_flow"):
            columns = 4 if self.metric_flow.get_width() >= 850 else 2
            if self.metric_flow.get_max_children_per_line() != columns:
                self.metric_flow.set_max_children_per_line(columns)
        if not self.pending:
            self.pending = True
            sampling = self.preferences["nvidia_sampling"]
            self.task("telemetry", lambda: self.reader.snapshot(nvidia=sampling), self.receive)
        return GLib.SOURCE_CONTINUE

    def close_requested(self, *_):
        if self.controller and "control" in self.busy:
            self.message("Wait for the current control operation to finish, then close the app.")
            return True
        app = self.get_application()
        if (getattr(app, "background_enabled", False) and self.preferences["close_to_tray"] and
                not getattr(app, "quitting", False)):
            self.set_visible(False)
            self.update_animation()
            app.background_notice()
            return True
        if self.controller:
            try:
                self.controller.close()
            except RuntimeError as error:
                self.message(str(error))
                self.show_toast("Lighting hand-off needs attention", str(error))
                return True
        self.alive = False
        self.pending_lighting = None
        self.pending_rgb_stop = False
        GLib.source_remove(self.timer)
        self.brightness_monitor.close()
        for timer in (self.animation_timer, self.toast_timer):
            if timer:
                GLib.source_remove(timer)
        self.desktop.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.css_provider)
        if getattr(app, "background_enabled", False) and not getattr(app, "quitting", False):
            GLib.idle_add(app.quit)
        return False

    def notice(self, content, text, warning=False):
        widget = box(False, 10)
        widget.add_css_class("notice")
        if warning:
            widget.add_css_class("warning")
        widget.append(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        widget.append(label(text, "small", wrap=True, hexpand=True))
        content.append(widget)
        return widget

    def mode_cards(self, content, kind, modes):
        grid = Gtk.Grid(column_spacing=12, column_homogeneous=True)
        buttons = {}
        for i, (key, title, description, icon) in enumerate(modes):
            widget = Gtk.Button(hexpand=True)
            widget.add_css_class("mode")
            inner = box(spacing=12)
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_halign(Gtk.Align.START); image.set_pixel_size(22); image.add_css_class("accent")
            inner.append(image); inner.append(label(title, "mode-title"))
            inner.append(label(description, "mode-description", wrap=True, max_width_chars=24))
            widget.set_child(inner)
            supported = key in self.cap["profiles"] if kind == "profile" else bool(
                self.cap["mux_path"] and (key != "Integrated" or self.cap["dgpu_path"]))
            if not self.test_mode and not self.controller.supported(kind):
                supported = False
            widget.set_sensitive(supported)
            if not supported:
                widget.set_tooltip_text("Not exposed by this laptop")
            def clicked(_, selected=key):
                if self.perform_action(kind, selected):
                    self.refresh_modes()
            widget.connect("clicked", clicked)
            buttons[key] = widget
            grid.attach(widget, i, 0, 1, 1)
        content.append(grid)
        return buttons

    def profile_cards(self, content):
        return self.mode_cards(content, "profile", (
            ("quiet", "Quiet", "Less noise. A lighter footprint.", "power-profile-power-saver-symbolic"),
            ("balanced", "Balanced", "Everyday speed, kept in balance.", "power-profile-balanced-symbolic"),
            ("performance", "Performance", "More headroom for demanding work.", "power-profile-performance-symbolic")))

    def build_dashboard(self, content):
        hero = box(False, 20)
        hero.add_css_class("hero")
        icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        icon.set_pixel_size(35); icon.add_css_class("hero-icon"); hero.append(icon)
        details = box(spacing=7, hexpand=True)
        details.append(label("YOUR DAILY DRIVER", "eyebrow"))
        details.append(label("ROG Strix G16" if "G614PH" in self.cap["model"] else self.cap["model"], "hero-title", wrap=True))
        details.append(label("G614PH  ·  CachyOS  ·  Hyprland", "subtitle"))
        hero.append(details)
        self.hero_profile = label("—", "chip")
        self.hero_profile.set_valign(Gtk.Align.CENTER); hero.append(self.hero_profile)
        content.append(hero)
        metrics = Gtk.FlowBox(column_spacing=12, row_spacing=12, homogeneous=True,
                              min_children_per_line=2, max_children_per_line=4,
                              selection_mode=Gtk.SelectionMode.NONE)
        metrics.set_activate_on_single_click(False)
        self.metric_flow = metrics
        self.metrics = {}
        for i, (name, icon_name) in enumerate((("CPU", "utilities-system-monitor-symbolic"), ("GPU", "video-display-symbolic"),
                                                 ("Memory", "drive-harddisk-symbolic"), ("Battery", "battery-good-symbolic"))):
            metric = Metric(self, name, icon_name)
            self.metrics[name] = metric
            metric.set_size_request(150, -1)
            metrics.append(metric)
        content.append(metrics)
        title = box(False, 10)
        title.append(label("Performance profile", "section-title", hexpand=True))
        title.append(label("PREVIEW CONTROLS" if self.test_mode else "LIVE PROFILE CONTROL", "eyebrow")); content.append(title)
        self.quick_profiles = self.profile_cards(content)
        bottom = Gtk.Grid(column_spacing=12, column_homogeneous=True)
        cooling = card("Cooling", "Live fan speeds")
        self.fan_labels = []
        for fan in self.cap["fans"]:
            value = label("—", "small")
            self.fan_labels.append(value)
            cooling.append(row(fan["name"].replace("_", " ").replace("mid fan", "Middle fan").title(), control=value))
        if not self.cap["fans"]:
            cooling.append(label("Fan sensors unavailable", "subtitle"))
        graphics = card("Graphics & power")
        self.dashboard_gpu, self.dashboard_ac, self.dashboard_limit = label("—"), label("—"), label("—")
        graphics.append(row("Configured GPU mode", control=self.dashboard_gpu))
        graphics.append(row("Power source", control=self.dashboard_ac))
        graphics.append(row("Charge limit", control=self.dashboard_limit))
        bottom.attach(cooling, 0, 0, 1, 1); bottom.attach(graphics, 1, 0, 1, 1)
        content.append(bottom)

    def build_performance(self, content):
        self.profile_status = label("Current profile: —", "chip")
        self.profile_status.set_halign(Gtk.Align.START); content.append(self.profile_status)
        self.profile_buttons = self.profile_cards(content)
        self.profile_draft = label("No profile preview selected.", "subtitle", wrap=True)
        content.append(self.profile_draft)
        info = card("Desktop integration")
        info.append(row("Existing performance shortcut", "Silent → Performance → Turbo"))
        info.append(row("Strix Core names", "Quiet → Balanced → Performance"))
        self.ppd_status = label("Not checked", "small muted")
        info.append(row("Power profile service", control=self.ppd_status))
        info.append(button("Check available profiles", lambda: self.task("profiles", self.reader.profiles, self.show_profiles)))
        content.append(info)

    def show_profiles(self, data):
        self.ppd_status.set_text(", ".join(data["profiles"]) if data["ok"] else "Unavailable")
        self.message("Profile check complete" if data["ok"] else "Profile service could not be reached")

    def build_fans(self, content):
        guide = card("Automatic cooling is the starting point", "The laptop adjusts its fans as temperatures change. Use a performance profile for everyday control; custom curves are advanced.")
        guide.add_css_class("guide")
        guide.append(row("Temperature → fan power", "A curve tells the firmware how much fan power to request at each temperature."))
        guide.append(row("More power → more cooling and noise", "Lowering a point can reduce cooling. Fan power is a percentage, not RPM; actual speed depends on the fan and firmware."))
        guide.append(row("Example, not a recommendation", "A point at 70 °C / 60% requests 60% fan power at that temperature. It does not mean 60% CPU usage."))
        guide.append(button("Use performance profiles", lambda: self.navigate("Performance")))
        content.append(guide)
        self.notice(content, "This editor is a simulation. It cannot change cooling or restore firmware defaults." if self.test_mode else
                    "Custom fan writes are not enabled yet. The editor is for reviewing curves; firmware cooling remains unchanged.", True)
        if not self.cap["curves"]:
            content.append(card("Custom curves unavailable", "This laptop does not expose complete fan curve data."))
            return
        self.fan_editor = Gtk.Expander(label="Explore a custom curve · advanced preview")
        editor = card()
        editor.append(label("Read the detected curve, change a point, then preview. Reset draft returns to the startup readings. Validation checks the curve's shape, not thermal safety.", "subtitle", wrap=True))
        top = box(False, 12)
        top.append(label("Fan curve", "section-title", hexpand=True))
        self.fan_channels = list(self.cap["curves"])
        self.fan_dropdown = Gtk.DropDown.new_from_strings(self.fan_channels)
        top.append(self.fan_dropdown); editor.append(top)
        self.curve_plot = Plot(self, height=210, curve=True)
        editor.append(self.curve_plot)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        for base_row in (0, 4):
            grid.attach(label("POINT", "eyebrow"), 0, base_row, 1, 1)
            grid.attach(label("°C", "small muted"), 0, base_row + 1, 1, 1)
            grid.attach(label("FAN %", "small muted"), 0, base_row + 2, 1, 1)
        self.curve_inputs = []
        for i in range(8):
            column, base_row = i % 4 + 1, (i // 4) * 4
            grid.attach(label(str(i + 1), "eyebrow"), column, base_row, 1, 1)
            temp = Gtk.SpinButton.new_with_range(20, 105, 1)
            pwm = Gtk.SpinButton.new_with_range(0, 100, .1)
            pwm.set_digits(1)
            for widget, title in ((temp, "temperature"), (pwm, "fan power percentage")):
                widget.set_width_chars(3)
                widget.set_hexpand(True)
                widget.update_property([Gtk.AccessibleProperty.LABEL], [f"Point {i + 1} {title}"])
                widget.connect("value-changed", lambda _: self.draw_curve())
            self.curve_inputs.append((temp, pwm))
            grid.attach(temp, column, base_row + 1, 1, 1); grid.attach(pwm, column, base_row + 2, 1, 1)
        editor.append(grid)
        actions = box(False, 10)
        curve_save = button("Preview curve", self.save_curve, True)
        curve_save.set_sensitive(self.test_mode)
        actions.append(curve_save)
        actions.append(button("Reset draft", self.load_curve))
        restore = button("Restore firmware defaults", lambda: None)
        restore.set_sensitive(False)
        restore.set_tooltip_text("Available after hardware control has been reviewed")
        actions.append(restore)
        editor.append(actions)
        self.fan_editor.set_child(editor)
        content.append(self.fan_editor)
        self.curve_status = label("", "subtitle", wrap=True); content.append(self.curve_status)
        self.fan_dropdown.connect("notify::selected", lambda *_: self.load_curve())
        self.load_curve()

    def load_curve(self):
        if not hasattr(self, "curve_inputs"):
            return
        channel = self.fan_channels[self.fan_dropdown.get_selected()]
        points = self.cap["curves"][channel]
        for (temp, pwm), (t, p) in zip(self.curve_inputs, points):
            temp.set_value(t); pwm.set_value(p / 255 * 100)
        self.draw_curve()
        if hasattr(self, "curve_status"):
            self.curve_status.set_text("Detected " + channel + " curve loaded. These are startup readings, not a recommended or verified factory curve.")
        if hasattr(self, "timer"):
            self.message("Fan draft reset to startup readings · cooling unchanged")

    def draw_curve(self):
        if hasattr(self, "curve_plot") and hasattr(self, "curve_inputs"):
            self.curve_plot.points = self.curve_points()
            self.curve_plot.queue_draw()

    def save_curve(self):
        channel = self.fan_channels[self.fan_dropdown.get_selected()]
        points = self.curve_points()
        if self.perform_action("curve", {"channel": channel, "points": points}):
            self.curve_status.set_text(channel + " curve preview saved. Cooling has not changed.")
        else:
            self.curve_status.set_text(self.status.get_text())

    def curve_points(self):
        return [[t.get_value_as_int(), round(p.get_value() * 255 / 100)] for t, p in self.curve_inputs]

    def build_gpu(self, content):
        self.gpu_current = label("Configured GPU mode: —", "chip")
        self.gpu_current.set_halign(Gtk.Align.START); content.append(self.gpu_current)
        self.gpu_buttons = self.mode_cards(content, "gpu", (
            ("Integrated", "Integrated", "Integrated graphics for lighter tasks.", "power-profile-power-saver-symbolic"),
            ("Hybrid", "Hybrid", "Integrated + discrete, as needed.", "video-display-symbolic"),
            ("Dedicated", "Dedicated", "Discrete GPU through the hardware MUX.", "power-profile-performance-symbolic")))
        self.gpu_draft = label("No GPU change previewed.", "subtitle", wrap=True); content.append(self.gpu_draft)
        devices = card("Graphics devices")
        self.gpu_devices = label("Reading devices…", "subtitle", wrap=True)
        devices.append(self.gpu_devices); content.append(devices)
        self.notice(content, "MUX changes require a reboot when applied. No reboot is pending from this app." if self.test_mode else
                    "GPU switching is not enabled yet. The configured mode shown above is read-only.", True)

    def build_lighting(self, content):
        preview = card()
        heading = box(False, 12)
        heading.append(label("Four-zone keyboard", "section-title", hexpand=True))
        heading.append(label("VISUAL PREVIEW", "chip neutral")); preview.append(heading)
        self.keyboard = Plot(self, 260, keyboard=True); preview.append(self.keyboard)
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self.keyboard_clicked)
        self.keyboard.add_controller(gesture)
        self.zone_buttons = []
        zones = Gtk.Grid(column_spacing=8, column_homogeneous=True)
        for index in range(4):
            widget = Gtk.ToggleButton(label="Zone " + str(index + 1))
            widget.add_css_class("zone-button")
            if index:
                widget.set_group(self.zone_buttons[0])
            widget.connect("toggled", lambda w, i=index: self.select_zone(i) if w.get_active() else None)
            self.zone_buttons.append(widget); zones.attach(widget, index, 0, 1, 1)
        preview.append(zones)
        preview.append(label("Left → right · select a zone to edit its color", "small muted"))
        content.append(preview)
        settings = card()
        self.effect = Gtk.DropDown.new_from_strings(list(EFFECTS))
        self.effect.connect("notify::selected", lambda *_: self.draw_lighting())
        settings.append(row("Effect", control=self.effect))
        self.color = Gtk.Entry(text=self.keyboard.zones[0], max_length=7, width_chars=9)
        self.color.update_property([Gtk.AccessibleProperty.LABEL], ["Selected zone hex color"])
        self.color.connect("changed", lambda *_: self.color_changed())
        color_controls = box(False, 8)
        color_controls.append(self.color)
        color_controls.append(button("Pick", self.pick_color))
        settings.append(row("Selected zone color", control=color_controls))
        settings.append(button("Use this color for all four zones", self.color_all_zones))
        self.speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, .25, 3, .25)
        self.speed.set_value(1); self.speed.set_digits(2); self.speed.set_draw_value(True)
        self.speed.set_size_request(180, -1)
        self.speed.update_property([Gtk.AccessibleProperty.LABEL], ["Lighting animation speed multiplier"])
        self.speed.connect("value-changed", lambda *_: self.draw_lighting())
        settings.append(row("Animation speed", "0.25× slow → 3× fast" if self.test_mode else
                            "0.25× slow → 3× fast · Breathing uses three speed steps", self.speed))
        self.effect_intensity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.effect_intensity.set_size_request(180, -1)
        self.effect_intensity.set_value(100)
        self.effect_intensity.set_draw_value(True)
        self.effect_intensity.set_digits(0)
        self.effect_intensity.connect("value-changed", lambda *_: self.draw_lighting())
        settings.append(row("Effect brightness", "Software dimming · no administrator access", self.effect_intensity))
        self.animation_paused = Gtk.Switch(active=False)
        self.animation_paused.connect("notify::active", lambda *_: self.update_animation())
        settings.append(row("Pause animation", "Changes only the on-screen illustration" if self.test_mode else
                            "Preview pauses now; apply to pause device output", self.animation_paused))
        maximum = self.cap["brightness_max"]
        self.brightness_selection = BrightnessSelection(maximum or 0)
        self.brightness_selection.update(self.reader.brightness())
        self.syncing_brightness = False
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, max(1, maximum or 3), 1)
        self.brightness.set_size_request(180, -1); self.brightness.set_digits(0); self.brightness.set_draw_value(True)
        self.brightness.set_round_digits(-1)  # Smooth pointer motion; selection alone is quantized.
        self.brightness_supported = maximum is not None and (self.test_mode or self.controller.supported('brightness'))
        self.brightness.set_sensitive(maximum is not None)
        self.brightness.update_property([Gtk.AccessibleProperty.LABEL], ["Keyboard brightness level selection"])
        self.brightness.connect("value-changed", self.brightness_changed)
        brightness_row = row('Keyboard brightness', control=self.brightness)
        self.brightness_reading = label('', 'subtitle', wrap=True)
        brightness_row.get_first_child().append(self.brightness_reading)
        settings.append(brightness_row)
        if not self.test_mode:
            self.apply_brightness_button = button("Apply keyboard brightness", lambda: self.perform_action("brightness", round(self.brightness.get_value())), True)
            settings.append(self.apply_brightness_button)
        self.refresh_brightness_controls()
        settings.append(button("Save lighting settings", self.save_lighting_settings))
        self.rgb_status = label("No lighting session active", "small muted", wrap=True, hexpand=True)
        self.lighting_footer.append(self.rgb_status)
        self.apply_lighting_button = button("Preview lighting" if self.test_mode else "Apply lighting", self.save_lighting, True)
        self.lighting_footer.append(self.apply_lighting_button)
        if not self.test_mode:
            self.lighting_footer.append(button("Return to desktop lighting", self.stop_rgb))
        content.append(settings)
        self.wallpaper_label = label("Wallpaper accent: —", "subtitle", wrap=True)
        content.append(self.wallpaper_label)
        self.notice(content, "Four-zone colors and effects are previews. Hardware effect support and zone commands still need review." if self.test_mode else
                    "Closing to the tray keeps effects running. Stop or Quit requests your saved static desktop color. Native keyboard zones are controlled; accent lamps keep their desktop/firmware color. Store/save commands are blocked.")
        self.zone_buttons[0].set_active(True)
        self.load_lighting_controls(self.lighting_store.load())

    def keyboard_clicked(self, gesture, count, x, y):
        width = self.keyboard.get_width()
        total = min(39, (width - 36) / 14) * 14
        left = (width - total) / 2
        if 16 <= y <= 170 and left <= x < left + total:
            self.zone_buttons[min(3, int((x - left) / total * 4))].set_active(True)

    def select_zone(self, index):
        self.keyboard.selected_zone = index
        if hasattr(self, "color"):
            self.color.set_text(self.keyboard.zones[index])
        self.keyboard.queue_draw()

    def color_changed(self):
        color = self.color.get_text()
        if re.fullmatch(r"#[a-fA-F0-9]{6}", color):
            self.color.remove_css_class("error")
            self.keyboard.zones[self.keyboard.selected_zone] = color
            self.draw_lighting()
        else:
            self.color.add_css_class("error")

    def color_all_zones(self):
        if re.fullmatch(r"#[a-fA-F0-9]{6}", self.color.get_text()):
            self.keyboard.zones = [self.color.get_text()] * 4
            self.draw_lighting()
            self.color.set_text(self.keyboard.zones[self.keyboard.selected_zone])
        else:
            self.message("Choose a valid six-digit hex color first.")

    def pick_color(self):
        initial = Gdk.RGBA()
        initial.parse(self.keyboard.zones[self.keyboard.selected_zone])
        dialog = Gtk.ColorDialog(title="Choose zone color", with_alpha=False)
        selected_zone = self.keyboard.selected_zone
        def chosen(dialog, result):
            try:
                rgba = dialog.choose_rgba_finish(result)
                color = "#" + "".join(f"{round(c * 255):02x}" for c in (rgba.red, rgba.green, rgba.blue))
                self.keyboard.zones[selected_zone] = color
                if self.keyboard.selected_zone == selected_zone:
                    self.color.set_text(color)
                self.draw_lighting()
            except GLib.Error:
                pass
        dialog.choose_rgba(self, initial, None, chosen)

    def brightness_changed(self, *_):
        if not self.syncing_brightness:
            self.brightness_selection.select(round(self.brightness.get_value()))
            self.refresh_brightness_controls(sync_slider=False)

    def update_brightness(self, actual, force=False):
        previous = self.brightness_selection.actual
        self.brightness_selection.update(actual)
        if not self.test_mode:
            self.brightness_supported = self.controller.supported('brightness')
        self.refresh_brightness_controls(sync_slider=force or
            (previous != self.brightness_selection.actual and not self.brightness_selection.pending))

    def refresh_brightness_controls(self, sync_slider=True):
        selection = self.brightness_selection
        if sync_slider and selection.selected is not None:
            self.syncing_brightness = True
            try:
                self.brightness.set_value(selection.selected)
            finally:
                self.syncing_brightness = False
        text = ('Current keyboard level: ' + str(selection.actual) + ' / ' + str(selection.maximum)
                if selection.actual is not None else 'Current keyboard level: unavailable')
        if selection.pending:
            text += ' · Selected: ' + str(selection.selected) + ' / ' + str(selection.maximum)
        self.brightness_reading.set_text(text)
        if hasattr(self, 'apply_brightness_button'):
            self.apply_brightness_button.set_sensitive(selection.actual is not None and selection.pending and self.brightness_supported)

    def draw_lighting(self):
        if not hasattr(self, "color"):
            return
        effect = self.effect.get_selected_item().get_string()
        self.keyboard.effect = effect
        # Keep custom zone colors intact when entering/leaving wallpaper sync.
        self.keyboard.wallpaper_color = self.latest.get("accent") or self.accent
        self.keyboard.speed = self.speed.get_value() if hasattr(self, "speed") else 1
        self.keyboard.intensity = self.effect_intensity.get_value()/100 if hasattr(self, 'effect_intensity') else 1
        self.keyboard.queue_draw()
        self.update_animation()

    def update_animation(self):
        running = (hasattr(self, "effect") and hasattr(self, "animation_paused") and self.alive and self.get_mapped() and
                   self.stack.get_visible_child_name() == "Lighting" and
                   self.effect.get_selected_item().get_string() in ANIMATED and
                   not self.animation_paused.get_active())
        if running and self.animation_timer is None:
            self.animation_timer = GLib.timeout_add(40, self.animate_lighting)
        elif not running and self.animation_timer is not None:
            GLib.source_remove(self.animation_timer); self.animation_timer = None

    def animate_lighting(self):
        if not self.alive or not self.get_mapped():
            self.animation_timer = None
            return GLib.SOURCE_REMOVE
        self.keyboard.phase = time.monotonic() - self.animation_start
        self.keyboard.queue_draw()
        return GLib.SOURCE_CONTINUE

    def lighting_value(self):
        return validate_lighting({"effect": self.effect.get_selected_item().get_string(),
            "zones": list(self.keyboard.zones), "speed": self.speed.get_value(),
            "paused": self.animation_paused.get_active(), "intensity": self.effect_intensity.get_value()})

    def load_lighting_controls(self, pattern):
        value = validate_lighting(pattern)
        self.keyboard.zones = list(value["zones"])
        self.effect.set_selected(EFFECTS.index(value["effect"]))
        self.speed.set_value(value["speed"])
        self.animation_paused.set_active(value["paused"])
        self.effect_intensity.set_value(value["intensity"])
        self.color.set_text(self.keyboard.zones[self.keyboard.selected_zone])
        self.draw_lighting()

    def save_lighting_settings(self):
        try:
            self.lighting_store.save(self.lighting_value())
            self.message("Lighting settings saved · hardware unchanged")
        except (OSError, ValueError) as error:
            self.message(str(error))

    def save_lighting(self):
        if not re.fullmatch(r"#[a-fA-F0-9]{6}", self.color.get_text()):
            self.message("Enter a valid color for the selected zone.")
            return
        if self.perform_action("lighting", self.lighting_value()):
            if self.cap["brightness_max"] is not None:
                self.perform_action("brightness", round(self.brightness.get_value()))
            self.message("Lighting preview saved · no device commands sent")
            self.show_toast("Lighting preview saved", "Four zones · no RGB device commands sent")

    def cycle_lighting(self, direction=1):
        if self.test_mode:
            self.show_toast("Test mode", "Lighting shortcuts are disabled in the simulation.")
            return
        value = self.lighting_store.next_effect(self.lighting_value(), direction)
        self.load_lighting_controls(value)
        if 'control' in self.busy:
            self.pending_lighting = value
            self.pending_rgb_stop = False
            self.message('Lighting change queued · latest effect will apply next')
        else:
            self.run_control("lighting", value)

    def stop_rgb(self):
        self.pending_lighting = None
        if 'control' in self.busy:
            self.pending_rgb_stop = True
            self.message('Return to desktop lighting queued')
            return
        def stop():
            try:
                return self.controller.stop_lighting()
            except Exception as error:
                return {'ok': False, 'error': str(error)}
        def finished(result):
            self.last_rgb_error = None if result.get('ok') else result.get('error', 'RGB hand-off failed.')
            self.message(('Saved static desktop color requested.' if result.get('desktop_color_requested') else
                          'Firmware lighting control requested.' if result.get('firmware_control_requested') else
                          'No Strix Core RGB session was active.') if result.get('ok') else result.get('error', 'RGB hand-off failed.'))
            GLib.idle_add(self.drain_rgb_request)
        self.task('control', stop, finished)

    def build_battery(self, content):
        status = card("Battery status")
        self.battery_level = label("—", "metric-value"); status.append(self.battery_level)
        self.battery_bar = Gtk.ProgressBar(); status.append(self.battery_bar)
        self.battery_status = label("—", "subtitle"); status.append(self.battery_status)
        content.append(status)
        limits = card("Charging limit", "Pick a limit to preview. Your current limit stays in place." if self.test_mode else
                      "Apply a charging limit with administrator authentication. The actual value is read back after writing.")
        self.battery_actual = label("Current limit: —", "chip neutral")
        self.battery_actual.set_halign(Gtk.Align.START); limits.append(self.battery_actual)
        self.charge_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 40, 100, 1)
        self.charge_scale.set_draw_value(True); self.charge_scale.set_digits(0); self.charge_scale.set_value(80)
        self.charge_scale.update_property([Gtk.AccessibleProperty.LABEL], ["Charge limit preview"])
        self.charge_scale.set_sensitive(self.cap["charge_limit"]); limits.append(self.charge_scale)
        presets = box(False, 10)
        for percent in (60, 80, 100):
            preset = button(str(percent) + "%", lambda p=percent: self.charge_scale.set_value(p))
            preset.set_sensitive(self.cap["charge_limit"]); presets.append(preset)
        limits.append(presets)
        save = button("Preview limit" if self.test_mode else "Apply charge limit", lambda: self.perform_action("battery", round(self.charge_scale.get_value())), True)
        save.set_sensitive(self.cap["charge_limit"] and (self.test_mode or self.controller.supported("battery"))); limits.append(save)
        if not self.test_mode and not self.controller.supported("battery"):
            limits.append(label("This control needs the matching installed helper and reviewed laptop support.", "subtitle", wrap=True))
        content.append(limits)

    def build_memory(self, content):
        self.notice(content, "Review only · closing is simulated, so unsaved work stays open." if self.test_mode else
                    "Closing applications is real. Review your selection and save work before confirming.", not self.test_mode)
        content.append(label("Select from windowed applications. Desktop services are excluded; RSS is an estimate and does not include every child process.", "subtitle", wrap=True))
        head = box(False, 12)
        self.select_all = Gtk.CheckButton(label="Select all", hexpand=True)
        self.select_all.connect("toggled", self.select_all_processes)
        self.selecting_all = False
        head.append(self.select_all)
        self.memory_refresh = button("Refresh", self.refresh_processes)
        head.append(self.memory_refresh); content.append(head)
        self.process_list = card()
        self.process_list.append(label("Refresh to read your processes.", "subtitle"))
        content.append(self.process_list)
        self.process_checks = []
        summary = box(spacing=4, hexpand=True)
        self.memory_summary = label("No processes selected", "small", wrap=True)
        summary.append(self.memory_summary)
        summary.append(label("PREVIEW · applications stay open" if self.test_mode else "Close requests require confirmation", "small muted"))
        self.memory_footer.append(summary)
        self.memory_footer.append(button("Clear", lambda: self.set_process_selection(False)))
        self.free_button = button("Free up RAM", self.close_processes, True)
        self.free_button.set_sensitive(False)
        self.memory_footer.append(self.free_button)

    def set_process_selection(self, active):
        self.selecting_all = True
        for widget, _ in self.process_checks:
            widget.set_active(active)
        self.selecting_all = False
        self.memory_selection()

    def select_all_processes(self, widget):
        if not self.selecting_all:
            self.set_process_selection(widget.get_active())

    def refresh_processes(self):
        self.message("Reading your applications…")
        self.task("processes", self.reader.processes, self.show_processes)

    def show_processes(self, processes):
        child = self.process_list.get_first_child()
        while child:
            next_child = child.get_next_sibling(); self.process_list.remove(child); child = next_child
        self.process_checks = []
        for process in processes:
            check = Gtk.CheckButton(label=process["name"])
            check.connect("toggled", lambda *_: self.memory_selection())
            self.process_checks.append((check, process))
            item = box(False, 12); item.add_css_class("row")
            check.set_hexpand(True); item.append(check)
            item.append(label(f"PID {process['pid']}", "small muted"))
            item.append(label(value_text(process["rss"] / 1024 ** 2, " MiB"), "small"))
            self.process_list.append(item)
        if not processes:
            self.process_list.append(label("No readable application windows were found. Your session services are excluded.", "subtitle", wrap=True))
        self.memory_selection(); self.message("Application list refreshed · no applications closed")

    def memory_selection(self):
        if self.selecting_all:
            return
        selected = [p for check, p in self.process_checks if check.get_active()]
        total = sum(p["rss"] for p in selected)
        self.memory_summary.set_text(f"{len(selected)} selected · {gib(total)} RSS estimate")
        self.free_button.set_sensitive(bool(selected))
        self.selecting_all = True
        self.select_all.set_inconsistent(0 < len(selected) < len(self.process_checks))
        self.select_all.set_active(bool(self.process_checks) and len(selected) == len(self.process_checks))
        self.selecting_all = False

    def close_processes(self):
        selected = [p["pid"] for check, p in self.process_checks if check.get_active()]
        if self.perform_action("close", selected):
            self.show_toast("Memory cleanup · preview", f"Would request closure of {len(selected)} application processes. Nothing was closed; shared memory may overlap.")

    def build_update_center(self, content):
        summary = card("System packages", "Checks the existing pacman database without refreshing it.")
        self.updates_summary = label("Ready to check", "hero-title"); summary.append(self.updates_summary)
        self.updates_age = label("Database age: not checked", "subtitle", wrap=True); summary.append(self.updates_age)
        actions = box(False, 10)
        actions.append(button("Check cached updates", self.check_updates, True))
        actions.append(button("Preview full upgrade" if self.test_mode else "Open system upgrade", lambda: self.perform_action("upgrade", "sudo pacman -Syu")))
        summary.append(actions); content.append(summary)
        self.package_list = card(); self.package_list.append(label("No query yet.", "subtitle")); content.append(self.package_list)
        command = card("Full system upgrade")
        command.append(label("sudo pacman -Syu", "mono", selectable=True))
        command.append(label("Command preview only. No packages are downloaded or installed." if self.test_mode else
                             "Runs interactively in Kitty. You review and authenticate the transaction there.", "subtitle", wrap=True))
        content.append(command)

    def check_updates(self):
        self.updates_summary.set_text("Checking…")
        self.task("updates", self.reader.updates, self.show_updates)

    def show_updates(self, result):
        child = self.package_list.get_first_child()
        while child:
            nxt = child.get_next_sibling(); self.package_list.remove(child); child = nxt
        if not result["ok"]:
            self.updates_summary.set_text("Check unavailable")
            self.package_list.append(label(result["error"] or "Could not query pacman.", "subtitle", wrap=True))
        else:
            packages = result["packages"]
            self.updates_summary.set_text(f"{len(packages)} cached update" + ("" if len(packages) == 1 else "s"))
            for package in packages[:50]:
                self.package_list.append(row(package["name"], f"{package['installed']} → {package['available']}"))
            if not packages:
                self.package_list.append(label("No updates in the cached database.", "subtitle"))
            if len(packages) > 50:
                self.package_list.append(label(f"+ {len(packages) - 50} more packages", "subtitle"))
        stamp = result.get("database_timestamp")
        self.updates_age.set_text("Oldest repository database: " + (
            datetime.fromtimestamp(stamp).strftime("%d %b %Y, %H:%M") if stamp else "unavailable") + " · latest online versions not checked")
        self.message("Cached package check complete · no database refresh")

    def build_hotkeys(self, content):
        keys = card("Your M buttons", "Audio, microphone and performance bindings stay with the desktop.")
        for key, title in (("M1", "Audio"), ("M2", "Audio"), ("M3", "Microphone"), ("M4", "Performance")):
            keys.append(row(key + "   " + title, control=label("KEEP EXISTING", "chip neutral")))
        content.append(keys)
        saved = self.shortcut_manager.load()
        self.m5_event = None if self.test_mode else saved[0]['event']
        self.aura_event = None if self.test_mode else saved[1]['event']
        spare = card("M5 · open Strix Core", "ASUS driver default: XF86Launch1. You can capture a different event or edit the value.")
        self.m5_entry = Gtk.Entry(text=self.m5_event or '', width_chars=17)
        self.m5_entry.connect('changed', lambda w: self.hotkey_entry_changed('m5', w.get_text()))
        self.m5_event_label = label(self.m5_event or "Not captured", "small muted")
        spare.append(row("M5 event", control=self.m5_entry))
        spare.append(self.m5_event_label)
        spare.append(button("Capture M5", self.capture_m5))
        self.m5_action = self.dropdown(list(ACTIONS), saved[0]['action'], lambda _: self.refresh_hotkey_draft())
        spare.append(row("Action", control=self.m5_action))
        content.append(spare)
        functions = card("Fn + F4 · AURA", "ASUS AURA next/previous events use XF86Launch3 / XF86Launch2. Both can cycle effects.")
        self.aura_entry = Gtk.Entry(text=self.aura_event or '', width_chars=17)
        self.aura_entry.connect('changed', lambda w: self.hotkey_entry_changed('aura', w.get_text()))
        functions.append(row("AURA event", control=self.aura_entry))
        functions.append(button('Capture AURA', lambda: self.capture_special_key('aura')))
        self.previous_aura = Gtk.Switch(active=len(saved) > 2)
        self.previous_aura.connect('notify::active', lambda *_: self.refresh_hotkey_draft())
        functions.append(row('Also bind previous AURA', 'Optional XF86Launch2 → previous effect', self.previous_aura))
        functions.append(row("Fn + F5 / M4", "Existing XF86Launch4 performance shortcut is preserved."))
        functions.append(label("Ordinary F4/F5 are never rebound. Defaults describe the driver mapping; a physical press still needs verification.", "small muted", wrap=True))
        content.append(functions)
        self.shortcut_status = label("Shortcuts have not been checked", "small", wrap=True, hexpand=True)
        self.hotkeys_footer.append(self.shortcut_status)
        install = button("Apply shortcuts", self.install_hotkeys, True)
        install.set_sensitive(not self.test_mode)
        self.hotkeys_footer.append(install)
        remove = button("Remove app shortcuts", self.remove_hotkeys)
        remove.set_sensitive(not self.test_mode)
        self.hotkeys_footer.append(remove)
        content.append(button("Check shortcuts", self.check_hotkeys))
        preview = card("Bindings to apply")
        self.hotkey_preview = label("", "mono", wrap=True, selectable=True)
        preview.append(self.hotkey_preview); content.append(preview)
        content.append(button("Export shortcut preview", self.export_hotkeys))
        self.refresh_hotkey_draft()

    def hotkey_entry_changed(self, target, text):
        if target == 'm5':
            self.m5_event = text.strip() or None
        else:
            self.aura_event = text.strip() or None
        if hasattr(self, 'hotkey_preview'):
            self.refresh_hotkey_draft()

    def hotkey_records(self):
        records = [{"event": self.m5_event, "action": self.m5_action.get_selected_item().get_string()}]
        if self.aura_event:
            records.append({"event": self.aura_event, "action": "Cycle lighting effects"})
            if self.previous_aura.get_active() and self.aura_event == DEFAULT_AURA and self.m5_event != PREVIOUS_AURA:
                records.append({"event": PREVIOUS_AURA, "action": "Previous lighting effect"})
        return records

    def refresh_hotkey_draft(self):
        try:
            self.hotkey_text = export_bindings(self.hotkey_records())
        except ValueError as error:
            self.hotkey_text = '-- ' + str(error)
        self.hotkey_preview.set_text(self.hotkey_text)

    def record_m5(self, name):
        name = validate_event(name)
        self.m5_event = name
        self.m5_event_label.set_text(name)
        self.m5_entry.set_text(name)
        self.refresh_hotkey_draft()

    def shortcut_task(self, operation):
        def work():
            try:
                return {'ok': True, 'data': operation()}
            except Exception as error:
                return {'ok': False, 'error': str(error)}
        def finished(result):
            if result['ok']:
                data = result['data']
                message = data.get('status') or (str(data.get('bindings', 0)) + ' shortcuts installed. M5 opens the app; AURA cycles lighting.' if 'backup' in data else
                    ('Strix Core shortcuts are active.' if data.get('installed') else 'No Strix Core shortcuts are active.'))
                self.shortcut_status.set_text(message)
                self.message(message)
            else:
                self.shortcut_status.set_text(result['error'])
                self.show_toast('Shortcuts were not changed', result['error'])
        self.task('shortcuts', work, finished)

    def install_hotkeys(self):
        try:
            records = self.hotkey_records()
            if not self.m5_event or not self.aura_event:
                raise ValueError('Enter or capture the M5 and AURA events first.')
            export_bindings(records)
        except ValueError as error:
            self.shortcut_status.set_text(str(error))
            return
        self.shortcut_task(lambda: self.shortcut_manager.install(records))

    def remove_hotkeys(self):
        self.shortcut_task(self.shortcut_manager.remove)

    def check_hotkeys(self):
        self.shortcut_task(self.shortcut_manager.status)

    def capture_m5(self):
        self.capture_special_key('m5')

    def capture_special_key(self, target):
        title = 'M5' if target == 'm5' else 'Fn + F4 / AURA'
        dialog = Gtk.Window(title="Capture " + title, transient_for=self, modal=True)
        dialog.set_default_size(460, 250)
        dialog.add_css_class("app-window")
        body = box(spacing=18); body.add_css_class("page")
        body.append(label("Press " + title, "section-title"))
        feedback = label("Only this window listens. Existing desktop shortcuts may consume the event before it reaches the app. Press Escape to cancel.", "subtitle", wrap=True, max_width_chars=42)
        body.append(feedback)
        body.append(button("Cancel", dialog.close))
        controller = Gtk.EventControllerKey()
        def pressed(_, keyval, keycode, state):
            name = Gdk.keyval_name(keyval)
            if name == "Escape":
                dialog.close(); return True
            if name in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R"):
                return True
            try:
                if target == 'm5':
                    self.record_m5(name)
                else:
                    self.aura_entry.set_text(validate_event(name))
                dialog.close()
                self.show_toast(title + " event captured", name + " · Apply shortcuts installs the mapping")
            except ValueError as error:
                feedback.set_text(str(error))
            return True
        controller.connect("key-pressed", pressed)
        dialog.add_controller(controller); dialog.set_child(body); dialog.present()

    def export_hotkeys(self):
        try:
            self.refresh_hotkey_draft()
            path = self.storage.write_text("hyprland-shortcuts.lua", self.hotkey_text)
            self.message("Shortcut preview exported to " + str(path))
        except (OSError, ValueError) as error:
            self.message("Could not export shortcuts: " + str(error))

    def dropdown(self, values, current, changed):
        widget = Gtk.DropDown.new_from_strings(values)
        widget.set_selected(values.index(current))
        widget.connect("notify::selected", lambda *_: changed(widget.get_selected_item().get_string()))
        return widget

    def preference(self, key, value):
        self.preferences[key] = value
        try:
            self.storage.write("preferences.json", self.preferences)
        except (OSError, ValueError) as error:
            self.message("Preference changed for this session; saving failed: " + str(error))
            return
        self.message("App preference saved")

    def build_appearance(self, content):
        general = card("Make it yours")
        def theme_changed(value):
            self.preference("theme", value); self.refresh_wallpaper_accent(force=True)
        themes = self.dropdown(["System", "Dark", "Light"], self.preferences["theme"], theme_changed)
        general.append(row("Theme", "Graphite · follow system, dark or light", themes))
        general.append(row("Desktop font", self.font_family))
        general.append(row("Glass background", "App-scoped blur · your desktop configuration is unchanged"))
        self.start_dropdown = self.dropdown(list(PAGES), self.preferences["start_page"], lambda v: self.preference("start_page", v))
        general.append(row("Starting page", control=self.start_dropdown))
        if not self.test_mode:
            keep_running = Gtk.Switch(active=self.preferences['close_to_tray'])
            keep_running.connect('notify::active', lambda *_: self.preference('close_to_tray', keep_running.get_active()))
            general.append(row('Close to system tray', 'Keep lighting and shortcuts available when the window closes', keep_running))
            restore = Gtk.Switch(active=self.preferences['restore_lighting'])
            restore.connect('notify::active', lambda *_: self.preference('restore_lighting', restore.get_active()))
            general.append(row('Restore lighting on launch', 'Apply your saved pattern when Strix Core starts', restore))
            general.append(button('Quit Strix Core', lambda: self.get_application().request_quit()))
        accent = Gtk.Switch(active=self.preferences["wallpaper_accent"])
        self.wallpaper_accent_switch = accent
        def accent_changed(*_):
            if self.syncing_wallpaper_accent:
                return
            self.preference("wallpaper_accent", accent.get_active())
            self.refresh_wallpaper_accent(force=True)
        accent.connect("notify::active", accent_changed)
        general.append(row("Wallpaper accent", "Follow your saved desktop color", accent))
        nvidia = Gtk.Switch(active=self.preferences["nvidia_sampling"])
        nvidia.connect("notify::active", lambda *_: self.preference("nvidia_sampling", nvidia.get_active()))
        general.append(row("NVIDIA telemetry", "Query only while the discrete GPU is already active", nvidia))
        content.append(general)
        modules = card("Optional pages")
        for name in OPTIONAL:
            toggle = Gtk.Switch(active=name in self.preferences["modules"])
            def changed(widget, _, page=name):
                selected = set(self.preferences["modules"])
                selected.add(page) if widget.get_active() else selected.discard(page)
                self.preference("modules", [p for p in OPTIONAL if p in selected]); self.update_modules()
            toggle.connect("notify::active", changed)
            modules.append(row(name, control=toggle))
        content.append(modules)

    def build_diagnostics(self, content):
        summary = card("Local diagnostics")
        summary.append(row("Strix Core", "Version " + __version__))
        summary.append(row("Hardware", self.cap["model"]))
        summary.append(row("Control mode", "Preview only · hardware writes disabled" if self.test_mode else
                           "Live controls · custom fan and GPU writes disabled"))
        summary.append(row("Included", "Sensor readings, capabilities, kernel and app version"))
        summary.append(row("Privacy", "No serials, usernames, application list or system journal"))
        content.append(summary)
        if not self.test_mode:
            services = card('Control availability', 'Checks required programs, interfaces and the profile service. Ready does not mean a physical setting-change test passed.')
            self.services_list = box(spacing=10)
            services.append(self.services_list)
            services.append(button('Check service availability', self.refresh_services))
            content.append(services)
        actions = box(False, 10)
        actions.append(button("Export diagnostics", self.export_diagnostics, True))
        actions.append(button("Export preview log" if self.test_mode else "Export action log", self.export_preview))
        actions.append(button("Clear previews" if self.test_mode else "Clear action log", self.clear_previews))
        content.append(actions)
        self.diagnostics_path = label("Nothing exported yet.", "subtitle", wrap=True, selectable=True)
        content.append(self.diagnostics_path)

    def refresh_services(self):
        if self.test_mode:
            return GLib.SOURCE_REMOVE
        def work():
            try:
                return self.controller.availability()
            except Exception as error:
                return {'check': {'available': False, 'reason': str(error)}}
        def finished(result):
            self.service_statuses = result
            child = self.services_list.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                self.services_list.remove(child)
                child = nxt
            names = {'profile': 'Performance', 'lighting': 'RGB lighting', 'battery': 'Charge limit',
                     'brightness': 'Keyboard brightness', 'close': 'Application closing',
                     'upgrade': 'System upgrade', 'curve': 'Custom fan curves', 'gpu': 'GPU switching'}
            for key, status in result.items():
                self.services_list.append(row(names.get(key, key), 'Ready' if status['available'] else status['reason']))
            if 'profile' in result:
                for control in [*self.profile_buttons.values(), *self.quick_profiles.values()]:
                    control.set_sensitive(result['profile']['available'])
            if 'lighting' in result:
                self.apply_lighting_button.set_sensitive(result['lighting']['available'])
                self.apply_lighting_button.set_tooltip_text(result['lighting'].get('reason') or 'Apply the draft to your keyboard')
        self.task('services', work, finished)
        return GLib.SOURCE_REMOVE

    def export_diagnostics(self):
        if not self.latest:
            self.message("Wait for the first telemetry sample before exporting.")
            return
        try:
            data = self.reader.diagnostics(self.latest)
            if not self.test_mode:
                data["mode"] = "live-controls"
                data.pop("hardware_writes", None)
                data["operations"] = [{k: event.get(k) for k in ("action", "result", "verified")} for event in self.controller.history]
                data['services'] = getattr(self, 'service_statuses', {})
                if self.controller.rgb_session:
                    data['rgb_session'] = self.controller.rgb_session.status()
            path = self.storage.write("diagnostics.json", data)
            self.diagnostics_path.set_text(str(path)); self.message("Local diagnostics exported")
        except (OSError, ValueError) as error:
            self.message("Could not export diagnostics: " + str(error))

    def export_preview(self):
        try:
            path = self.storage.write("preview-log.json" if self.test_mode else "action-log.json",
                                      {"mode": "preview-only" if self.test_mode else "live-controls",
                                       "events": self.preview.history if self.test_mode else self.controller.history})
            self.diagnostics_path.set_text(str(path)); self.message("Preview log exported locally")
        except (OSError, ValueError) as error:
            self.message("Could not export preview log: " + str(error))

    def clear_previews(self):
        if not self.test_mode:
            self.controller.history.clear()
            self.message("Action log cleared. Applied settings were not reverted.")
            return
        self.preview.clear(); self.refresh_modes()
        self.load_curve()
        self.message("Preview selections cleared · hardware unchanged")

    def refresh_modes(self):
        actual = self.latest.get("profile")
        draft = self.preview.drafts.get("profile")
        for buttons in (self.quick_profiles, self.profile_buttons):
            for key, widget in buttons.items():
                widget.remove_css_class("selected")
                if key == (draft or actual):
                    widget.add_css_class("selected")
        self.profile_draft.set_text(("Preview: " + draft.title() + " · not applied" if draft else "No profile preview selected.") if self.test_mode else
                                    "Profile selections are applied through the existing service, then checked against the kernel.")
        gpu_draft = self.preview.drafts.get("gpu")
        for key, widget in self.gpu_buttons.items():
            widget.remove_css_class("selected")
            if key == (gpu_draft or self.latest.get("gpu_mode")):
                widget.add_css_class("selected")
        actual_gpu = self.latest.get("gpu_mode", "Unknown")
        reboot = gpu_draft is not None and gpu_draft != actual_gpu
        self.gpu_draft.set_text(
            f"Preview: {gpu_draft} · " + ("reboot would be required after applying" if reboot else "no configured mode change")
            if gpu_draft else "No GPU change previewed.")

    def clear_telemetry(self):
        for metric in self.metrics.values():
            metric.update("—", "Sensor read failed", None)
        self.latest = {}
        for widget in (self.hero_profile, self.dashboard_gpu, self.dashboard_ac, self.dashboard_limit,
                       self.profile_status, self.gpu_current, self.gpu_devices, self.battery_level,
                       self.battery_status, self.battery_actual):
            widget.set_text("Unavailable")
        for widget in self.fan_labels:
            widget.set_text("—")
        self.battery_bar.set_fraction(0)
        self.refresh_modes()

    def receive(self, data):
        self.pending = False
        self.latest, self.last_sample = data, time.monotonic()
        self.live_chip.set_text("● LIVE SENSORS")
        self.live_chip.remove_css_class("neutral")
        self.refresh_wallpaper_accent()
        self.hero_profile.set_text(data["profile"].upper())
        self.metrics["CPU"].update(value_text(data["cpu_temp"], "°"),
                                   value_text(data["cpu_usage"], "%") + " utilization", data["cpu_usage"])
        gpu = next((g for g in data["gpus"] if g.get("temperature") is not None and g["vendor"] == "0x10de"),
                   next((g for g in data["gpus"] if g["vendor"] == "0x1002"), {}))
        self.metrics["GPU"].update(value_text(gpu.get("temperature"), "°"),
                                   ("NVIDIA" if gpu.get("vendor") == "0x10de" else "AMD iGPU") + " · " +
                                   value_text(gpu.get("usage"), "%"), gpu.get("usage"))
        memory = data["memory"]
        self.metrics["Memory"].update(gib(memory["used"]), "of " + gib(memory["total"]), memory["percent"])
        battery = data["battery"]
        self.metrics["Battery"].update(value_text(battery["percent"], "%"), battery["status"], battery["percent"])
        for widget, fan in zip(self.fan_labels, data["fans"]):
            widget.set_text(value_text(fan["rpm"], " RPM"))
        self.dashboard_gpu.set_text(data["gpu_mode"])
        self.dashboard_ac.set_text("AC connected" if battery["ac_online"] else (
            "Battery" if battery["ac_online"] is False else "Unavailable"))
        self.dashboard_limit.set_text(value_text(battery["limit"], "%"))
        self.profile_status.set_text("Current profile: " + data["profile"].title())
        self.gpu_current.set_text("Configured GPU mode: " + data["gpu_mode"])
        device_text = []
        for item in data["gpus"]:
            device_text.append(item["name"] + " · " + item["runtime"] + " · " +
                               value_text(item["temperature"], " °C") + " · " + value_text(item["usage"], "% usage"))
        self.gpu_devices.set_text("\n\n".join(device_text) or "No GPU data exposed.")
        self.battery_level.set_text(value_text(battery["percent"], "%"))
        self.battery_bar.set_fraction(max(0, min(1, (battery["percent"] or 0) / 100)))
        self.battery_status.set_text(battery["status"])
        self.battery_actual.set_text("Current limit: " + value_text(battery["limit"], "%"))
        self.update_brightness(self.reader.brightness())
        self.wallpaper_label.set_text("Wallpaper accent: " + (data.get("accent") or "No saved color found"))
        self.draw_lighting(); self.refresh_modes()
        if self.status.get_text() == "Starting local monitoring…":
            self.message("Monitoring locally · controls are in test mode" if self.test_mode else
                         "Live controls ready · custom fan and GPU writes remain unavailable")


class Application(Gtk.Application):
    def __init__(self, reader, storage, page=None, unique=True, test_mode=True):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE if unique else Gio.ApplicationFlags.NON_UNIQUE
        identity = "io.github.hyprdodz.StrixCore" + (".Preview" if test_mode and unique else "")
        super().__init__(application_id=identity, flags=flags)
        self.reader, self.storage, self.page = reader, storage, page
        self.test_mode = test_mode
        self.background_enabled = unique and not test_mode
        self.window = None
        self.tray = None
        self.quitting = False
        self.noticed_background = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name('Strix Core')
        show = Gio.SimpleAction.new('show', None)
        show.connect('activate', lambda *_: self.activate())
        self.add_action(show)
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.request_quit())
        self.add_action(quit_action)
        self.set_accels_for_action('app.quit', ['<Control>q'])
        if self.background_enabled:
            from .tray import StatusNotifier
            self.hold()
            self.tray = StatusNotifier({1: self.activate, 2: lambda: self.show_page('Lighting'),
                3: self.cycle_lighting, 4: self.stop_lighting, 5: self.request_quit})
            import signal
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.request_quit)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.request_quit)

    def ensure_window(self, restore=False):
        if not self.window:
            self.window = Window(self, self.reader, self.storage)
            if restore and not self.test_mode and self.window.preferences['restore_lighting']:
                def apply_saved():
                    self.window.run_control('lighting', self.window.lighting_store.load())
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(apply_saved)
        return self.window

    def show_page(self, page):
        self.page = page
        self.activate()

    def cycle_lighting(self, direction=1):
        self.ensure_window().cycle_lighting(direction)

    def stop_lighting(self):
        self.ensure_window().stop_rgb()

    def do_command_line(self, command_line):
        arguments = command_line.get_arguments()[1:]
        if '--quit' in arguments:
            self.request_quit()
            return 0
        if '--cycle-lighting' in arguments:
            self.cycle_lighting()
            return 0
        if '--previous-lighting' in arguments:
            self.cycle_lighting(-1)
            return 0
        if '--stop-lighting' in arguments:
            self.stop_lighting()
            return 0
        if '--background' in arguments:
            self.ensure_window(restore=True)
            return 0
        self.page = arguments[1] if len(arguments) == 2 and arguments[0] == '--page' and arguments[1] in PAGES else None
        self.activate()
        return 0

    def do_activate(self):
        self.ensure_window(restore=True)
        self.window.refresh_wallpaper_accent()
        self.window.update_brightness(self.reader.brightness(), force=True)
        if self.page:
            self.window.navigate(self.page)
            self.page = None
        self.window.present()

    def background_notice(self):
        if not self.noticed_background:
            notice = Gio.Notification.new('Strix Core is running in the background')
            notice.set_body('Lighting continues. Use the tray or your app launcher to reopen; Quit stops the app.')
            notice.set_default_action('app.show')
            self.send_notification('background', notice)
            self.noticed_background = True

    def request_quit(self):
        self.quitting = True
        if self.window and self.window.alive:
            if self.window.close_requested():
                self.quitting = False
                self.window.present()
                return GLib.SOURCE_CONTINUE
            self.window.destroy()
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        if self.tray:
            self.tray.close()
        if self.window and self.window.alive:
            self.quitting = True
            self.window.close_requested()
        Gtk.Application.do_shutdown(self)
