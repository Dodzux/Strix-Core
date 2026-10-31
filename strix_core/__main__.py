"""Desktop entry point. --test-mode explicitly isolates simulated controls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .backend import Reader
from .model import PAGES, Preview, Storage


def default_state():
    project = Path(__file__).resolve().parent.parent
    if (project / 'pyproject.toml').is_file() and (project / 'packaging/PKGBUILD').is_file():
        return project / ".preview/state"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "strix-core"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strix Core · local ASUS laptop controls")
    parser.add_argument("--version", action="version", version="Strix Core " + __version__)
    parser.add_argument("--state-dir", type=Path, default=default_state(), help="local preferences and explicit exports")
    parser.add_argument("--page", choices=PAGES, help="open a page")
    parser.add_argument("--test-mode", action="store_true", help="simulate all controls without hardware writes")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument('--cycle-lighting', action='store_true', help='cycle a saved RGB effect in the running app')
    controls.add_argument('--previous-lighting', action='store_true', help='select the previous saved RGB effect')
    controls.add_argument('--stop-lighting', action='store_true', help='return RGB control to desktop/firmware')
    controls.add_argument('--background', action='store_true', help='start in the system tray')
    controls.add_argument('--quit', action='store_true', help='stop lighting and quit the running app')
    controls.add_argument('--install-shortcuts', action='store_true', help='install reviewed M5/AURA bindings in your Hyprland config')
    controls.add_argument('--remove-shortcuts', action='store_true', help='remove only Strix Core managed shortcuts')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--snapshot", action="store_true", help="print read-only sensor data as JSON")
    actions.add_argument("--diagnostics", action="store_true", help="print a privacy-filtered diagnostic snapshot")
    actions.add_argument("--preview-profile", choices=("quiet", "balanced", "performance"), help="simulate a profile choice")
    actions.add_argument("--cycle-profile", action="store_true", help="simulate the next available performance profile")
    args = parser.parse_args(argv)
    if args.test_mode and any((args.cycle_lighting, args.previous_lighting, args.stop_lighting, args.background)):
        parser.error('Background hardware controls are unavailable in test mode.')
    reader, storage = Reader(), Storage(args.state_dir)
    if args.snapshot or args.diagnostics:
        snapshot = reader.snapshot()
        print(json.dumps(reader.diagnostics(snapshot) if args.diagnostics else snapshot, indent=2))
        return 0
    if args.install_shortcuts or args.remove_shortcuts:
        if args.test_mode:
            parser.error('Shortcut installation is disabled in Test Mode.')
        from .hotkeys import ShortcutManager
        manager = ShortcutManager(storage)
        try:
            result = manager.install(manager.load()) if args.install_shortcuts else manager.remove()
        except (OSError, ValueError, RuntimeError) as error:
            print(json.dumps({'ok': False, 'error': str(error)}))
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.preview_profile or args.cycle_profile:
        preview = Preview(reader.capabilities)
        choice = args.preview_profile
        if args.cycle_profile:
            modes = [p for p in ("quiet", "balanced", "performance") if p in reader.capabilities["profiles"]]
            if not modes:
                parser.error("No platform performance profiles are exposed.")
            current = reader.snapshot()["profile"]
            choice = modes[(modes.index(current) + 1) % len(modes)] if current in modes else modes[0]
        try:
            event = preview.plan("profile", choice)
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(event, indent=2))
        return 0
    try:
        from .ui import Application
    except (ImportError, ValueError) as error:
        print("Strix Core needs GTK 4, PyGObject and Pycairo from your distribution. "
              "See README.md for installation requirements.\n" + str(error), file=sys.stderr)
        return 1
    gui_args = [sys.argv[0]] + (["--page", args.page] if args.page else [])
    for flag in ('cycle_lighting', 'previous_lighting', 'stop_lighting', 'background', 'quit'):
        if getattr(args, flag):
            gui_args.append('--' + flag.replace('_', '-'))
    try:
        return Application(reader, storage, args.page, test_mode=args.test_mode).run(gui_args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
