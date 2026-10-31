# Using Strix Core 0.3.6

Launch **Strix Core** normally. Its header says **LIVE CONTROLS**. `--test-mode` selects the separate simulation.

## Lighting and background operation

1. Open **Lighting** and select Zone 1–4, left to right.
2. Enter a hex color or use **Pick** for each zone. Static displays the separate colors; other effects animate or blend them.
3. Choose an effect, speed and **Effect brightness**. Software dimming needs no administrator access.
4. Press **Apply lighting** in the fixed bottom action bar. The initial application asks for confirmation.

**Save lighting settings** saves a draft without writing hardware. Successful applications also save the pattern. Wallpaper sync reads the desktop accent without erasing your individual zone palette.

Breathing runs on the keyboard firmware, keeping the four zone colors and using three speed steps (slow below 0.75×, medium through 1.5×, fast above). Pausing holds a software-rendered frame. Other animated effects target 30 frames/second with evenly scheduled updates. Four colors are staged before one apply command; unchanged colors are skipped. At low brightness, 8-bit color rounding can produce fewer distinct color updates than animation samples.

Closing the window, including Super+Q, minimizes it to the tray by default. The worker continues its effect. Click the Wing tray icon or launch Strix Core again to reopen the existing instance.

The tray menu offers Open, Lighting, Next effect, Return to desktop lighting and Quit. **Return to desktop lighting** stops the stream and requests your saved wallpaper color in Static mode. Without a desktop accent source it uses the first zone color. The prior firmware effect cannot be read back. **Quit Strix Core**, Ctrl+Q or `strix-core --quit` stops the worker and exits. Color read-back is unavailable; restoration is reported as requested. Accent lamps keep their desktop/firmware color; the native renderer controls the four keyboard zones.

Appearance has **Close to system tray** and **Restore lighting on launch** switches. Restore is off by default. There is no login autostart installed automatically. `strix-core --background` starts only the tray; it applies a saved pattern only when Restore is enabled.

Appearance → **Wallpaper accent** controls the app's colors. Its saved on/off choice survives closing and restarting. Starting, reopening, or enabling the switch reads the current desktop accent immediately; you do not need to switch it off and on to refresh the theme. This appearance setting is separate from selecting the keyboard's Wallpaper sync effect.

**Keyboard brightness** shows the current hardware level, including 0 for Off, separately from a pending slider selection. After a successful **Apply keyboard brightness**, the slider resumes following live readings. Reopening reads the current level immediately. Moving the slider does not apply a change by itself.

The original unmarked slider appearance is restored. Apply uses the nearest supported level. Changes made with keyboard brightness keys update through kernel notifications, with an independent 100 ms read fallback while Lighting is visible. They do not wait for the general sensor poll. Sensor polling does not snap an unfinished drag back to an integer, and the drag handler does not probe hardware access.

Brightness uses your existing user access when permitted; it does not ask for authentication on the development laptop, whose keyboard-brightness attribute is already writable by the logged-in user. On protected systems it retains the authenticated helper fallback. No permissions or polkit rules are broadened. Effect brightness is a separate software dimmer; its percentage is independent of the keyboard's hardware level.

The native path passed bounded four-zone/breathing and 15-second smoothness checks without USB resets. Grouped updates and static wallpaper restoration were physically confirmed. Longer use and power-state transitions still need validation. Read the session status in the bottom bar; Diagnostics includes rendering rate, USB write duration and worker errors/reconnect counts. A failed session can be retried without restarting the app.

## M5 and Fn/AURA keys

Open **Hotkeys**, review the events/actions and press **Apply shortcuts** in the fixed bottom action bar. This installs a dedicated companion file and a managed include in your Hyprland config. It checks existing bindings, saves a private backup and verifies the resulting live bindings. Conflicting desktop shortcuts are not overwritten.

The ASUS driver defaults are:

| Event | Action |
| --- | --- |
| XF86Launch1, ROG/M5 | Open Strix Core |
| XF86Launch3, next AURA | Next saved lighting effect |
| XF86Launch2, previous AURA | Optional previous effect; off by default |
| XF86Launch4, performance | Existing desktop action is preserved |

M5/XF86Launch1 and Fn+F4/XF86Launch3 were physically captured on the development laptop. The physical event can vary on other hardware. GTK aliases such as Launch1/Launch3 are normalized automatically. **Capture M5** listens while its dialog is focused; the symbol fields can also be edited. Compositor shortcuts may consume a key before GTK receives it, so capture is optional when a known driver default is used. Ordinary F4/F5 and M1/M2 audio/M3 microphone bindings are preserved.

**Check shortcuts** reads the actual compositor bindings. **Export shortcut preview** only exports text. **Remove app shortcuts** removes the companion/include and this app's runtime bindings. The same install/removal is available without opening GTK:

```sh
strix-core --install-shortcuts
strix-core --remove-shortcuts
```

Symlinked or unusually structured compositor configurations require manual integration using the export; the installer refuses to replace them blindly.

## Other controls

Performance selections apply immediately through the existing service, then read back the service and kernel before notifying. Battery changes need **Apply charge limit** and system authentication; hardware read-back must match.

Memory lists windowed apps. Review the selection, use Select all if appropriate, then the bottom **Free up RAM** button and confirm. Applications may prompt for unsaved work. No process is force-killed and recovered RAM is not guaranteed.

Update Center reads cached pacman data. **Open system upgrade** shows an app-styled translucent confirmation, then opens the normal interactive `sudo pacman -Syu` flow in a supported terminal. Kitty uses `cachy.update`, matching this desktop's Super+U floating window and pacman taskbar icon. Review and authenticate the transaction in that terminal.

Diagnostics → **Check service availability** reports missing interfaces, programs or a nonresponding profile service. Availability is separate from a passed physical setting-change test.

Custom fan writes/reset and GPU switching remain unavailable. Use existing performance profiles for automatic cooling; the GPU page reports the configured mode.
