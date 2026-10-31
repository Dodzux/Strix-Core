#!/usr/bin/env python3
"""Display-backed UI checks. Opens only this app; changes no laptop settings.

Artifacts and screenshots are written under .preview/qa. Screenshot commands
are restricted to this test process's own Hyprland window rectangle.
"""
from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from strix_core.backend import Reader
from strix_core.model import PAGES, Storage
from strix_core.ui import Application

OUTPUT = PROJECT / ".preview/strix-core-qa"
OUTPUT.mkdir(parents=True, exist_ok=True)
reader = Reader()
app = Application(reader, Storage(OUTPUT / "state"), unique=False)
results = []
errors = []
steps = deque()


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    results.append(description)


def capture(name):
    # Get only our own app coordinates. No window titles or other clients are saved.
    clients = json.loads(subprocess.run(["hyprctl", "-j", "clients"], capture_output=True,
                                       text=True, check=True, timeout=3).stdout)
    client = next(c for c in clients if c["pid"] == os.getpid() and c["mapped"])
    check(client["visible"], name + " window is visible")
    x, y = client["at"]; width, height = client["size"]
    check(app.window.get_width() <= width, name + " GTK content fits compositor width")
    subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", str(OUTPUT / (name + ".png"))],
                   check=True, capture_output=True, timeout=4)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def click(text, page):
    roots = [app.window.pages[page]]
    if page == 'Lighting':
        roots.append(app.window.lighting_footer)
    matches = [w for root in roots for w in walk(root) if isinstance(w, Gtk.Button) and w.get_label() == text]
    check(bool(matches), "Found button: " + text)
    check(matches[0].get_sensitive(), "Enabled button: " + text)
    matches[0].emit("clicked")


def assert_plan(kind):
    check(kind in app.window.preview.drafts, kind + " produced a preview draft")
    check(app.window.preview.history[-1]["result"] == "simulated", kind + " action was simulated")


def start():
    w = app.window
    check(bool(w.latest), "Live telemetry received")
    check(w.latest["cpu_temp"] is not None, "CPU temperature is available on G614PH")
    check(len(w.latest["fans"]) == 3, "All three G614PH fan sensors read")
    for page in PAGES:
        steps.append(lambda p=page: w.navigate(p))
        steps.append(lambda p=page: capture(p.lower().replace(" ", "-")))
    steps.extend((test_profiles, check_notification, test_fans, test_gpu, test_lighting, capture_zones,
                  test_animation, check_animation, test_battery, test_memory,
                  test_memory_result, test_updates, test_updates_result, test_appearance,
                  capture_light, test_exports, test_modules, test_clear, finish))
    GLib.timeout_add(400, advance)
    return GLib.SOURCE_REMOVE


def test_profiles():
    w = app.window; w.navigate("Performance")
    before = w.latest["profile"]
    w.profile_buttons["balanced"].emit("clicked")
    assert_plan("profile")
    check(w.latest["profile"] == before, "Profile preview does not change actual telemetry")
    click("Check available profiles", "Performance")


def check_notification():
    w = app.window
    check(w.toast.get_reveal_child(), "Profile preview shows an in-app notification")
    check(w.desktop.last_notification["status"] == "accepted", "Desktop notification service accepted profile preview")
    check("preview" in w.desktop.last_notification["title"], "Desktop notification explicitly labels the preview")
    capture("profile-notification")
    layers = json.loads(subprocess.run(["hyprctl", "-j", "layers"], capture_output=True,
                                       text=True, check=True, timeout=3).stdout)
    def find_popup(value):
        if isinstance(value, dict):
            if value.get("namespace") == "notification-osd":
                return value
            for child in value.values():
                result = find_popup(child)
                if result:
                    return result
        elif isinstance(value, list):
            for child in value:
                result = find_popup(child)
                if result:
                    return result
        return None
    popup = find_popup(layers)
    if popup:
        x, y, width, height = (popup[k] for k in ("x", "y", "w", "h"))
        subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", str(OUTPUT / "desktop-notification.png")],
                       check=True, capture_output=True, timeout=4)
        check(True, "Desktop notification popup was mapped and captured")


def test_fans():
    w = app.window; w.navigate("Fans")
    original = deepcopy(w.cap["curves"])
    check(not w.fan_editor.get_expanded(), "Advanced fan editing is collapsed initially")
    w.fan_editor.set_expanded(True)
    for i, (temp, pwm) in enumerate(w.curve_inputs):
        temp.set_value(40 + i * 7); pwm.set_value(15 + i * 10)
    click("Preview curve", "Fans"); assert_plan("curve")
    check(w.cap["curves"] == original, "Editing a curve preserves detected firmware data")
    count = len(w.preview.history)
    w.curve_inputs[-1][1].set_value(0)
    click("Preview curve", "Fans")
    check(len(w.preview.history) == count, "Decreasing fan curve rejected in GUI")
    click("Reset draft", "Fans")
    restore = next(x for x in walk(w.pages["Fans"]) if isinstance(x, Gtk.Button) and x.get_label() == "Restore firmware defaults")
    check(not restore.get_sensitive(), "Hardware fan reset remains disabled")
    check(w.curve_inputs[0][1].get_value() <= 100, "Fan editor uses percentages rather than raw PWM")
    def scroll_to_editor():
        adjustment = w.stack.get_child_by_name("Fans").get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    steps.appendleft(lambda: capture("fans-advanced"))
    steps.appendleft(scroll_to_editor)


def test_gpu():
    w = app.window; w.navigate("GPU")
    target = "Hybrid" if baseline["gpu_mode"] == "Dedicated" else "Dedicated"
    w.gpu_buttons[target].emit("clicked"); assert_plan("gpu")
    check("reboot would" in w.gpu_draft.get_text(), "GPU preview identifies future reboot requirement")
    check(w.latest["gpu_mode"] == baseline["gpu_mode"], "GPU preview leaves actual MUX unchanged")


def test_lighting():
    w = app.window; w.navigate("Lighting")
    count = len(w.preview.history)
    w.color.set_text("#zzzzzz")
    click("Preview lighting", "Lighting")
    check(len(w.preview.history) == count, "Invalid lighting color rejected in GUI")
    w.color.set_text("#86d6b3"); w.effect.set_selected(3); w.brightness.set_value(2)
    click("Preview lighting", "Lighting")
    assert_plan("brightness")
    check(w.keyboard.wallpaper_color == reader.accent(), "Wallpaper illustration reads desktop accent without replacing the custom palette")
    w.effect.set_selected(0)
    for i, color in enumerate(("#ff6688", "#cc88ff", "#66bbff", "#77ddaa")):
        w.zone_buttons[i].set_active(True); w.color.set_text(color)
    click("Preview lighting", "Lighting")
    check(w.preview.drafts["lighting"]["zones"] == ["#ff6688", "#cc88ff", "#66bbff", "#77ddaa"], "All four lighting zones retain independent colors")


def capture_zones():
    capture("lighting-four-zones")


def test_animation():
    w = app.window
    w.effect.set_selected(5)
    check(w.animation_timer is not None, "Wave preview starts an animation timer")
    w.animation_test_phase = w.keyboard.phase


def check_animation():
    w = app.window
    check(w.keyboard.phase > w.animation_test_phase, "Animated lighting advances with time")
    w.animation_paused.set_active(True)
    check(w.animation_timer is None, "Pause stops the animation timer")
    w.animation_paused.set_active(False)
    check(w.animation_timer is not None, "Unpause resumes the animation")
    w.navigate("Dashboard")
    check(w.animation_timer is None, "Leaving Lighting stops animation work")


def test_battery():
    w = app.window; w.navigate("Battery")
    w.charge_scale.set_value(80); click("Preview limit", "Battery"); assert_plan("battery")
    check(w.latest["battery"]["limit"] == baseline["battery"]["limit"], "Battery preview leaves charge limit unchanged")


def test_memory():
    app.window.navigate("Memory"); click("Refresh", "Memory")


def test_memory_result():
    w = app.window
    if "processes" in w.busy:
        steps.appendleft(test_memory_result); return
    check(bool(w.process_checks), "User process list populated")
    check(not w.free_button.get_sensitive(), "Free up RAM is disabled without a selection")
    w.select_all.set_active(True)
    check(all(c.get_active() for c, _ in w.process_checks), "Select all selects every listed process")
    w.set_process_selection(False)
    check(not any(c.get_active() for c, _ in w.process_checks), "Clear removes every selection")
    w.process_checks[0][0].set_active(True)
    check(w.select_all.get_inconsistent() if len(w.process_checks) > 1 else w.select_all.get_active(),
          "Select all reflects the current application selection")
    check(w.memory_footer.get_visible(), "Memory action bar is visible outside the scroll area")
    w.free_button.emit("clicked"); assert_plan("close")
    pid = w.process_checks[0][1]["pid"]
    check(Path(f"/proc/{pid}").is_dir(), "Selected process still exists after preview")
    steps.appendleft(lambda: capture("memory-selected"))


def test_updates():
    app.window.navigate("Update Center"); click("Check cached updates", "Update Center")


def test_updates_result():
    w = app.window
    if "updates" in w.busy:
        steps.appendleft(test_updates_result); return
    check("Checking" not in w.updates_summary.get_text(), "Cached package query completed")
    check("cached update" in w.updates_summary.get_text(), "Live pacman cached query succeeded")
    click("Preview full upgrade", "Update Center"); assert_plan("upgrade")
    capture("updates-checked")


def test_appearance():
    w = app.window; w.navigate("Appearance")
    themes = [x for x in walk(w.pages["Appearance"]) if isinstance(x, Gtk.DropDown)][0]
    themes.set_selected(2)
    check(w.preferences["theme"] == "Light", "Theme selector applies Light theme")
    check(w.storage.preferences()["theme"] == "Light", "Theme preference persists only in QA state")


def capture_light():
    capture("appearance-light")
    w = app.window
    themes = [x for x in walk(w.pages["Appearance"]) if isinstance(x, Gtk.DropDown)][0]
    themes.set_selected(1)
    check(w.preferences["theme"] == "Dark", "Theme selector applies Graphite Dark theme")
    themes.set_selected(0)
    check(w.preferences["theme"] == "System", "System theme mode is available")
    original = w.desktop.scheme
    w.desktop.scheme = 2; w.desktop_changed()
    check(w.resolved_theme == "Light", "System mode follows a simulated portal Light preference")
    w.desktop.scheme = 1; w.desktop_changed()
    check(w.resolved_theme == "Dark", "System mode follows a simulated portal Dark preference")
    w.desktop.scheme = original; w.desktop_changed()


def test_exports():
    w = app.window; w.navigate("Hotkeys")
    check("SUPER + CTRL" not in w.hotkey_text and "hl.bind(" not in w.hotkey_text, "Uncaptured M5 does not generate guessed bindings")
    w.record_m5("Launch1")  # GDK's actual alias; synthetic fixture, not a physical press.
    check(w.hotkey_text.count("hl.bind(") == 1, "Synthetic M5 event generates one reviewed mapping")
    w.aura_entry.set_text('Launch3')
    check('XF86Launch3' in w.hotkey_text and w.hotkey_text.count('hl.bind(')==2,
          'GDK M5/AURA aliases export two canonical Hyprland bindings')
    click("Export shortcut preview", "Hotkeys")
    shortcuts = w.storage.root / "hyprland-shortcuts.lua"
    check(shortcuts.is_file(), "Lua shortcut preview exported")
    subprocess.run(["luac", "-p", str(shortcuts)], check=True, capture_output=True, timeout=3)
    check(True, "Exported Hyprland shortcut Lua parses")
    w.navigate("Diagnostics"); click("Export diagnostics", "Diagnostics"); click("Export preview log", "Diagnostics")
    diag = json.loads((w.storage.root / "diagnostics.json").read_text())
    log = json.loads((w.storage.root / "preview-log.json").read_text())
    check(diag["hardware_writes"] == 0, "Diagnostic export records zero hardware writes")
    check(len(log["events"]) >= 8, "Preview log includes exercised actions")
    check(str(Path.home()) not in json.dumps(diag), "Diagnostics exclude home directory")
    capture("diagnostics-exported")


def test_modules():
    w = app.window; w.navigate("Appearance")
    switches = [x for x in walk(w.pages["Appearance"]) if isinstance(x, Gtk.Switch)]
    switches[2].set_active(False)
    check(not w.nav["Fans"].get_visible(), "Optional Fans page can be hidden")
    switches[2].set_active(True)
    check(w.nav["Fans"].get_visible(), "Optional Fans page can be restored")
    # Service callbacks must have returned without blocking the UI loop.
    check(w.ppd_status.get_text() != "Not checked", "Power-profile service query completed")


def test_clear():
    w = app.window; w.navigate("Diagnostics"); click("Clear previews", "Diagnostics")
    check(not w.preview.drafts and not w.preview.history, "Clear previews resets simulated state")
    after = reader.snapshot()
    for key in ("profile", "mux_value", "dgpu_disabled", "brightness"):
        check(after[key] == baseline[key], key + " unchanged after all preview controls")
    check(after["battery"]["limit"] == baseline["battery"]["limit"], "Charge limit unchanged after all preview controls")
    w.navigate("Dashboard")


def finish():
    capture("dashboard-final")
    output = {"passed": len(results), "checks": results, "errors": errors,
              "hardware_before": {k: baseline[k] for k in ("profile", "mux_value", "dgpu_disabled", "brightness")},
              "scope": "display-backed GTK checks; all hardware actions simulated"}
    (OUTPUT / "results.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"passed": len(results), "errors": errors, "artifacts": str(OUTPUT)}, indent=2))
    app.window.close(); app.quit()


def advance():
    try:
        step = steps.popleft()
        step()
    except Exception:
        errors.append(traceback.format_exc())
        print(errors[-1], file=sys.stderr)
        (OUTPUT / "failure.json").write_text(json.dumps({"checks": results, "errors": errors}, indent=2))
        app.window.close(); app.quit()
        return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE if steps else GLib.SOURCE_REMOVE


baseline = reader.snapshot()
app.connect("activate", lambda *_: GLib.timeout_add(2400, start))
def deadline():
    errors.append("GUI smoke timed out")
    app.window.close(); app.quit()
    return GLib.SOURCE_REMOVE
GLib.timeout_add_seconds(55, deadline)
app.run(["strix_core-gui-smoke"])
raise SystemExit(1 if errors else 0)
