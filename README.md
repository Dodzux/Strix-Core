# Strix Core

<p align="center"><img src="strix_core/assets/strix-core.svg" width="96" alt="Strix Core Wing logo"></p>

Native GTK4 controls for the ASUS ROG Strix G16 G614PH · CachyOS / Hyprland · Version 0.3.6

A native GTK 4 laptop control center with a system tray, background four-zone RGB, performance profiles and a small authenticated helper for charging limits and keyboard brightness.

The hardware-control backend is currently reviewed for the **ASUS ROG Strix G16 G614PH**. Development and desktop integration are tested on CachyOS with Hyprland. Other laptops are not implicitly supported because they share the ASUS name.

## Current functionality

| Feature | Scope |
| --- | --- |
| Monitoring | Kernel-exposed CPU/GPU temperatures and usage, fans, memory and battery; unavailable readings remain unavailable |
| Performance | Existing powerprofilesctl service, with read-back and desktop notifications |
| Lighting | Four individual keyboard zones, ten effects including firmware breathing, saved settings, software dimming and wallpaper accent input |
| Background operation | Closing minimizes to a StatusNotifier tray; effects continue. Explicit Quit stops them |
| Hotkeys | Installable M5/AURA bindings for Hyprland Lua or legacy configuration; conflict detection and removal |
| Battery / keyboard brightness | G614PH range validation/read-back; existing user access for brightness, authenticated helper for protected writes |
| Memory | Confirmed graceful window-close requests on Hyprland; no force-kill or guaranteed RAM claim |
| Updates | Pacman cached queries and a normal interactive full-upgrade terminal |
| Custom fan curves / GPU switching | **Hardware writes remain unavailable**; inspection is supported |

The old LampArray output path repeatedly reset this keyboard. The current implementation uses physically tested native four-zone color/apply commands, one apply per frame and deadline-based animation timing. Firmware breathing avoids repeated USB updates. Two bounded 15-second smoothness tests completed without USB resets; grouped updates and restoration were physically confirmed. Store/save commands are blocked. Accent lamps retain their desktop/firmware color. See the [validation record](docs/VALIDATION.md) for the limits: long-running, suspend/resume and cross-boot behavior are not certified.

## Installation on Arch/CachyOS

Requirements: Python 3.11+, GTK 4.24+, PyGObject, Pycairo and polkit. Clone and build using the repository's local recipe:

```sh
git clone https://github.com/Dodzux/Strix-Core.git
cd Strix-Core
makepkg --dir packaging -si
strix-core
```

Optional integrations: `power-profiles-daemon` for performance; Hyprland for shortcuts/application closing; pacman and Kitty, Foot, Konsole, GNOME Terminal or Xterm for upgrades. No asusctl or ROG Control Center dependency is installed.

The package does not create a privileged daemon, change firmware settings during installation, install global input permissions, or enable an autostart entry. RGB uses the logged-in user's existing device access. The privileged helper accepts only battery and keyboard-brightness values and runs with isolated Python imports.

## Everyday use

See [USAGE.md](USAGE.md) for lighting, tray and hotkey instructions. Normal launch uses live adapters; explicit simulation is available with `strix-core --test-mode`.

```sh
strix-core --background          # tray without showing the window
strix-core --cycle-lighting      # apply next effect in the existing instance
strix-core --previous-lighting   # apply previous effect
strix-core --stop-lighting       # request normal desktop/firmware control
strix-core --quit                # stop lighting and exit
```

Cycling an effect is a real hardware action. Loading saved settings or exporting a shortcut preview does not apply them.

## Development

Run a source checkout with simulated controls first:

```sh
./bin/strix-core --test-mode
```

Checkout preferences and test artifacts stay in ignored `.preview/` directories. The installed app uses the normal XDG state directory. Source and installed app launches use live controls unless `--test-mode` is specified.

```sh
python -m unittest discover -s tests -v
python -m compileall -q strix_core scripts tests
python scripts/gui_smoke.py
python scripts/check_background.py
python scripts/check_appearance.py
python scripts/check_dialog_updater.py
python scripts/check_brightness.py
python scripts/profile_app.py
```

The portable tests use fake sysfs/USB objects and mocked commands. Notification tests require PyGObject. The GUI smoke test requires a running Hyprland desktop, opens a separate simulation, and does not write laptop controls. Physical tests require separate explicit approval and observation.

[CONTRIBUTING.md](CONTRIBUTING.md) explains verification expectations.

## License

Copyright © 2026 Dodzux. Strix Core is licensed under the **GNU General Public License version 3 only** (`GPL-3.0-only`). See [LICENSE](LICENSE).
