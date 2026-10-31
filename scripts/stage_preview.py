#!/usr/bin/env python3
"""Prepare an uninstalled desktop entry inside this checkout. Does not launch it."""
from pathlib import Path
import os
import shlex
import sys

project = Path(__file__).resolve().parent.parent
stage = project / ".preview/share"
apps = stage / "applications"
apps.mkdir(parents=True, exist_ok=True)
launcher = project / "bin/strix-core"
launcher.chmod(0o755)
# Desktop Exec has its own quoting rules, not shell quoting rules.
def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'

text = (project / "packaging/io.github.hyprdodz.StrixCore.desktop").read_text()
text = text.replace("Exec=strix-core", "Exec=" + desktop_quote(launcher))
text = text.replace("Icon=io.github.hyprdodz.StrixCore", "Icon=" + str(project / "strix_core/assets/strix-core.svg"))
target = apps / "io.github.hyprdodz.StrixCore.desktop"
target.write_text(text)
# Remove only the workspace entry generated under the old prototype identity.
legacy = apps / "io.github.hyprdodz.MinimalAC.desktop"
if legacy.is_file():
    legacy.unlink()
print("Staged: " + str(target))
print("Preview launcher command (temporary environment only):")
print("XDG_DATA_DIRS=" + shlex.quote(str(stage) + ":" + os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")) +
      " gtk4-launch io.github.hyprdodz.StrixCore")
