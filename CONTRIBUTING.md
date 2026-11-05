# Contributing

Strix Core is an early local hardware-control project. Its current reviewed transport is specific to the G614PH. Do not remove model/descriptor checks to claim broader support.

Run the unit suite before proposing a change. Fake-device tests must cover validation, failures, recovery and shutdown; they must not open real USB or sysfs writers. Display tests must identify their own windows and use explicit Test Mode or injected devices. Keep screenshots, personal configuration, logs and package artifacts under ignored local directories.

```sh
python -m unittest discover -s tests -v
python -m compileall -q strix_core scripts tests
desktop-file-validate packaging/io.github.hyprdodz.StrixCore.desktop
```

Display-backed checks in `scripts/` require a running Hyprland session. Some assertions, such as the updater's icon and floating size, require the development desktop's existing configuration. The unit suite is the portable CI check. Source checkouts use `.preview/state`; installed applications use XDG state. Validation scripts create their ignored output directories on demand.

A transport acknowledgment is not a color read-back. A status check is not a successful setting-change test. New hardware support needs descriptor/attribute evidence, bounded explicitly approved output tests, recovery evidence and clear limits. Keep changes to fans, GPU/display routing, firmware persistence and reboot handling separate from presentation changes.

Hotkey integration must preserve existing bindings, avoid input grabs, offer removal and reject conflicting or edited configurations. Never capture or log typing keys to identify a media button.

## Release acceptance

Prioritize reliable controls verified on the reviewed laptop. Custom fan writes and GPU switching remain unavailable until their separate validation is complete.

Before the next public release, require both a sustained RGB session and a real suspend/resume check on the reviewed laptop. Record the agreed test duration, effects, intensity, software/firmware environment, USB failures and the operator's physical observations. Verify recovery and the Stop/Quit lighting hand-off. Short animation tests and fake-device tests do not satisfy these requirements. Obtain explicit, bounded approval before each new hardware test; this release rule does not authorize running one.

## Bug reports

Bug reports should include the app version, model, compositor/version, failing control, exact visible error and expected/observed behavior. Optional Diagnostics exports are allowlisted; review them before sharing. Do not attach full journals or serial numbers by default.

Contributions are distributed under the project's GNU General Public License version 3 only (`GPL-3.0-only`). See [LICENSE](LICENSE).
