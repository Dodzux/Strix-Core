# Strix Core 0.3.6 validation

Development hardware: ASUS ROG Strix G16 G614PH, keyboard USB 0B05:19B6. Development environment: CachyOS, Hyprland 0.57, GTK 4.24. These results do not establish support for other ASUS models or keyboard firmware.

## Automated software checks

The portable unit suite uses fake sysfs/USB devices and mocked commands. It covers validation, read-back and recovery, shortcut conflicts and rollback, RGB ownership/reconnection/shutdown, the effect/speed/pause matrix, brightness monitoring and notification replacement. Checkout-state tests verify that a fresh clone needs no private project brief and keeps its preferences separate from the installed app.

The development desktop recorded these additional display-backed checks:

| Check | Scope |
| --- | --- |
| 138 GUI checks | All app pages in explicit Test Mode; controls simulated |
| 19 tray/background checks | Actual tray registration, hiding/reopening, CLI forwarding and shutdown; injected RGB device |
| 14 appearance checks | Accent changes, saved switch state, cold restart and tray reopening; private test preferences |
| 11 dialog/updater checks | Confirmation styling and actions; a harmless terminal payload verifies the development desktop's floating layout and pacman icon mapping |
| 22 brightness checks | Current readings, pending selection, Apply completion, reopening and external-change detection; injected brightness writes |

The brightness timing regression disables the general sensor poll and verifies that an injected external change reaches the current label and slider within 150 ms. The monitor uses read-only Linux LED notifications with a 100 ms fallback while Lighting is visible. It rereads current brightness instead of trusting the last hardware-event value. Hidden pages skip fallback reads; shutdown removes watches and timers. The original slider appearance is retained. Software timing checks are not an instrumented physical key-to-frame measurement.

Display tests require a suitable desktop session. Some assertions, including the updater's 1000×640 floating layout and taskbar icon, depend on the development desktop's existing configuration. These scripts do not install that desktop configuration.

## Physical observations

### RGB

The old standard LampArray software-control path repeatedly caused USB protocol errors and resets on this keyboard. The reviewed native path checks the exact board, VID/PID and report-descriptor hash. It permits only 64-byte mode/color B3 and apply B5 reports; store/save B4 and unrelated commands are rejected.

Explicitly approved tests demonstrated four distinct keyboard zones, firmware breathing and restoration of the saved static wallpaper color. A seven-second renderer check completed 35 frames with zero reconnects or USB reset entries. Two separate 15-second tests at 20% intensity exercised breathing, Wave and Color cycle. Grouping four color reports before one apply reduced observed write time from about 23 ms to 14 ms. The operator confirmed four working zones, smoother transitions and return of the wallpaper color. No save/store command was sent; other measured laptop settings were unchanged.

These are bounded tests. Transport acknowledgments are not color read-back. Stop/Quit requests the saved desktop accent in Static mode, or the first zone color when no accent source exists. Unknown prior firmware effects cannot be reconstructed. Native output controls keyboard zones 1–4; accent lamps retain their desktop/firmware color.

### Brightness and special keys

Real kernel read-back and an installed read-only UI check agreed at keyboard level 3/3. Existing normal-user write access is used when available; protected systems retain the authenticated helper. The package does not broaden device permissions or polkit policy. Brightness writes and rollback have fixture coverage; no new physical setting-change test was used to validate the UI fix.

A recording demonstrated the desktop brightness OSD reacting ahead of the app while using keyboard brightness keys. The two-second sensor poll was the cause. After installing 0.3.6, the operator repeated that workflow and confirmed that the app followed promptly. This is direct visual confirmation, without an instrumented latency guarantee.

Filtered physical event capture identified M5 as XF86Launch1 / XKB 156 and Fn+F4 as XF86Launch3 / XKB 210. GTK aliases Launch1/Launch3 are normalized. Capture excluded typing keys, and temporary listeners were removed. Other hardware may emit different events.

A graceful-close integration test closed only an empty GTK child window. No user application was closed. Updater presentation used harmless test text; no real system upgrade was executed during validation.

## Limits

Custom fan curves/reset and GPU switching remain disabled. Performance, battery-limit and brightness transaction handling have command/fake-sysfs tests; interface availability alone is not a successful physical setting-change test.

Long-running RGB, suspend/resume, Windows dual-boot transitions, other models and other firmware versions remain unverified. The package installs no privileged daemon, autostart entry or global input permissions. A public repository does not broaden the reviewed hardware scope.

## Primary references

- [Linux HID ASUS RGB patch](https://lists.openwall.net/linux-kernel/2025/03/25/1390): mode/color, apply and store semantics; this device's 64-byte report size was measured locally.
- [Linux LED sysfs ABI](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/ABI/testing/sysfs-class-led): current brightness and hardware-change notification semantics.
- [Hyprland Lua events](https://wiki.hypr.land/configuring/core/advanced-configuration/events/): filtered compositor key capture.
