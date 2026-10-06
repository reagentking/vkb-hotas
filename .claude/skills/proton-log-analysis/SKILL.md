---
name: proton-log-analysis
description: Diagnose whether a Proton/Steam game actually accepts the vkb-hotas virtual T.16000M (and TWCS Throttle), from Proton logs - set the right launch options, run vkb-check.py on steam-APPID.log, and interpret Wine's +hid/+dinput traces without the classic pitfalls. Use this whenever the user says a game doesn't detect the stick, sees it as a controller/gamepad, asks you to "check the log" after running Squadrons, Ace Combat 8 or another flight game, or when deciding whether a game needs a different emulated device.
---

# Does the game accept the stick? Reading Proton logs

The chain is: real stick → vkb-hotas → virtual **T.16000M (044f:b10a)** (and, with a second stick,
a virtual **TWCS Throttle (044f:b687)**) over hidraw → Wine winebus → DirectInput → game. A log
answers which link breaks. The host side is checked separately by `tools/vkb-check.py`'s host
section, which follows the roles the daemon reports: an *exposed* role is meant to have its real
stick visible and no virtual device.

## 1. Get a log that can answer the question

- **Prefix:** must have winebus `"Enable SDL"=dword:0`; run `tools/proton-setup.py [APPID]` with the
  game closed. Otherwise Steam-injected SDL mappings can make the stick a gamepad.
- **Launch options** (Steam → game → Properties), with Steam Input off for the game:
  `PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687 PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%`
  (older launch options with only `0x044F/0xB10A` still cover the T.16000M).
- **What the user should do:** launch, open the controls screen, ideally move the stick in flight
  or in a menu, then quit.
- **Where the log lands:** `~/steam-<appid>.log` (or `$PROTON_LOG_DIR`). Known app ids:
  Squadrons 1222730, Ace Combat 8 2288340, Project Wingman 895870.
- **Restart Steam after an install:** Steam holds the real stick open from before.

## 2. Analyse

```bash
tools/vkb-check.py                     # host checks + newest log
tools/vkb-check.py ~/steam-2288340.log # a specific log
```

A good result looks like this:
- `winebus SDL backend: disabled (hidraw path)`;
- `Wine created virtual Thrustmaster T.16000M: joystick via hidraw` (and the same for the TWCS
  Throttle, when a throttle stick is configured and not exposed);
- `real sticks not visible to the game` (an exposed stick is reported as visible "on purpose");
- at least one `joystick, acquired, read N times` with N > 0.

## 3. Interpret, and don't fall for these

- **Wine log prefixes are thread IDs, not processes.** Games acquire on one thread and poll on
  others. Count reads per device iface (vkb-check does this). An early version of the checker
  reported "never read" for a game that read 22,874 times.
- **Data format tells you the device kind.** 272 bytes (DIJOYSTATE2) or 80 is a joystick; 256 is
  the keyboard; 16 or 20 is the mouse. A keyboard showing "acquired" isn't the stick.
- **Two joystick devices for one stick is normal.** AC8 reads it directly *and* via
  `joyGetPosEx`; Wine's winmm implements that on DirectInput.
- **DirectInput type `0x10114` ("limited joystick") is expected for the T.16000M:** it has no Z
  axis. `0x10214` would be "standard". The TWCS has a Z axis, so it may show differently.
- **Two virtual devices:** with a throttle stick there are two joystick ifaces; tell them apart by
  the order of `bus_create_hid_device` lines and their data formats.
- **"Detected as a controller":** look for `is_gamepad 1` in winebus lines. If none exists, Wine
  exposed no gamepad, and controller *icons* in the game are its own UI choice. AC8 shows them
  even when DirectInput works.
- **XInput / Windows.Gaming.Input / GameInput** aren't covered by `+hid,+dinput`. Add `+xinput`
  if needed. `GameInputCreate ... stub!` means GameInput returned nothing.
- **Games that read joysticks through their own SDL2** (Project Wingman: bundled `SDL2.dll` plus
  the UE4 JoystickPlugin with `IgnoreGameControllers`) skip anything that looks like a gamepad. Check
  the prefix's `Enable SDL` first.
- **Enumerates but never acquires:** the game rejected the device. Usually a whitelist of known
  HOTAS. The fallback is emulating another supported device; CLAUDE.md notes a Logitech X56 as
  the next candidate for AC8.
- **The real stick appears in winebus:** hiding failed (service down when the game started, or
  Steam/game started before the install). Check `systemctl status vkb-hotas` and restart the game.

## Manual digging (when vkb-check isn't enough)

```bash
L=~/steam-<appid>.log
grep -n 'bus_create_hid_device desc' $L                     # devices Wine created (vid/pid, is_gamepad, is_hidraw)
grep -n 'dinput8_EnumDevices\|detected device type' $L      # enumeration + type per device
grep -n 'dinput_device_Acquire\|SetDataFormat iface' $L     # which iface was acquired, which data format
grep -o '^[0-9a-f]\{4\}:trace:dinput:[a-zA-Z_]*' $L | cut -d: -f4 | sort | uniq -c | sort -rn | head   # call histogram
```

Use `command grep` if `grep` is aliased (e.g. to ugrep) and a complex pattern errors out.

## Reporting

Lead with the answer ("Squadrons now accepts the stick", or "it never opens it"). Then give a
small table of before and after, or per check. Be explicit about what the log *can't* show: whether
in-flight controls feel right, or whether the throttle direction is correct. Those need the user
to test in game.
