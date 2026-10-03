# vkb-hotas

Use a VKB flight stick (or any other evdev joystick) in Proton games that only
accept HOTAS models they know, by re-presenting it as a **Thrustmaster T.16000M**.

> **Note:** this project was vibe coded with Claude Opus 5.5.

Some games, such as *Ace Combat 8: Wings of Theve* and *Star Wars: Squadrons*,
only enable their flight-stick controls for specific sticks. Under Proton,
DirectInput sees an unsupported stick correctly as a joystick, but the game
never opens it, or the stick ends up treated as a gamepad. vkb-hotas runs a
small root daemon that:

1. grabs the real stick exclusively,
2. creates a virtual T.16000M (`044f:b10a`) through `/dev/uhid`, so Wine gets a
   real hidraw device with a proper HID descriptor (X, Y, Rz twist, Slider
   throttle, hat, 16 or 32 buttons),
3. hides the real stick from your session (udev drops its `uaccess` tag), so
   games see exactly one stick.

Unplug the stick, or stop the service, and everything returns to normal.

## Status

| Hardware / game | Result |
|---|---|
| VKB Gladiator NXT EVO Omni Throttle (R), `231d:3200` | axes, hat, 32 buttons verified through the virtual stick |
| Star Wars: Squadrons (Proton Experimental) | game acquires and polls the stick |
| Ace Combat 8 (Proton Hotfix) | game acquires and polls the stick; shows controller button icons; flight not yet confirmed |

Other VKB models should work after adjusting axes in HOTAS Mapper (or with `--axis`, see [Mapping](#mapping)).
Reports from other sticks and games are welcome.

## Requirements

- Linux with systemd + udev, and a kernel with `uhid` (all mainstream distros)
- Python 3 and python-evdev available to **root's** `python3`
  (Arch `python-evdev`, Debian/Ubuntu `python3-evdev`, Fedora `python3-evdev`)
- Steam with Proton. Native Steam, Flatpak and Snap Steam installs are all
  detected by `proton-setup.py`.

## Install

```bash
sudo ./install.sh --list            # show connected sticks
sudo ./install.sh                   # autodetects if exactly one VKB device is plugged in
sudo ./install.sh --device 231d:3200   # or pick one explicitly (any vendor works)
./proton-setup.py                   # as your user, games closed
```

`proton-setup.py` configures Squadrons and Ace Combat 8 by default. Pass app ids
for other games (`./proton-setup.py --list` shows them). It sets the winebus
`Enable SDL`=0 in each prefix, so Wine passes the virtual stick through its
hidraw backend, and clears stale DirectInput cache entries. Backups are written
next to each edited `.reg` file.

Then **restart Steam** (it may still hold the real stick open) and set each
game's launch options, with Steam Input off:

```
PROTON_ENABLE_HIDRAW=0x044F/0xB10A %command%
```

## Verify

```bash
./vkb-check.py
```

This checks the service, that the real stick is hidden, that the virtual stick
is accessible, and how SDL classifies it. To check a game, launch it once with
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%`,
open its controls menu, quit, and run `./vkb-check.py` again. A working game
shows the virtual T.16000M created as a *joystick*, the real stick not visible,
and a joystick device "acquired, read N times".

## Remapping buttons and axes (HOTAS Mapper)

Some games, Ace Combat 8 among them, have no rebinding for the T.16000M, so
remapping happens before the game sees anything. Run:

```bash
vkb-mapper          # or "HOTAS Mapper" in your application menu
```

A local page opens in your browser:

- **Your stick:** live axes, hat and every physical button. Pressed buttons light up, and
  each one is tagged with the emulated control it drives.
- **What the game sees:** the emulated T.16000M's buttons, hat directions and axes.
  - **Assign**, then press a button or hat direction on your stick. Assigning moves
    that button off any other slot, and **Shift+Assign** adds a second source.
  - **Test** presses that button in-game for a moment, so you can work out what
    each T.16000M button does in a game, then note it in the slot's label.
  - Axes: pick the source (or **Learn** by moving it), invert, and set a center deadzone.
- **Stick map:** a picture of your stick with a pin on each button. Pins glow when pressed and are
  tagged with what they drive; hover one for details, and its game-button card lights up (and vice
  versa). Click **Place pins**, press a button on the stick, then click where it is (drag to adjust,
  × to remove). Zoom with the mouse wheel or the −/+ buttons (up to 8×) and drag to pan; pins
  keep their size on screen, so small hats are easy to hit, and **FIT** shows the whole picture.
  The default views are drawings of the Gladiator NXT EVO Omni Throttle (whole stick, SCG grip
  head, triggers in side profile, base front); generic outlines are available for other sticks. Add your own
  views, and swap any drawing for a photo of your stick with **Photo…** (PNG/JPEG/WebP, up to
  10 MB). VKB button numbering depends on grip and firmware profile, so pins are placed by you
  rather than pre-filled.
- **5-way hats:** VKB's 4-way hats with center push report as five separate buttons. Click
  **+ 5-way hat**, name it, push up, right, down and left, press it in (or choose "No center
  push"), then click where it is. It's drawn as one cross-shaped widget. Each segment lights up
  and dims when unmapped, hovering shows every direction's mapping, and double-clicking renames
  it. Its buttons are still mapped individually in the cards.
- **Profiles:** keep one per game. The selected profile is the one applied.

Changes save to `~/.config/vkb-hotas/mapping.json` (the stick map to `layout.json` and
`images/` beside it, shared by all profiles) and the daemon applies them
within half a second, even mid-game. No sudo or restart is needed. Switching
between 16 and 32 buttons briefly re-creates the virtual stick, so do that with
the game closed.

The mapper talks to the daemon over `/run/vkb-hotas/ctl.sock`, which only the
user in `VKB_HOTAS_USER` can open (the installer sets it to whoever ran
`sudo`). The web UI listens on 127.0.0.1 only, needs a per-session token, and
exits about 45 s after its last tab closes.

After updating (re-running `sudo ./install.sh`), close HOTAS Mapper and reopen it once it
has exited. An older mapper still running would serve the new page, so the page detects
that and disables the stick map until you restart. Stick maps saved before an update keep
their views and drawings: pick a new drawing per view from the dropdown next to **Photo…**, or
add a view with **+ View** (e.g. one using "SCG grip side (triggers)").

## Configuration

`/etc/default/vkb-hotas` (a systemd EnvironmentFile: **no comments after values**):

```sh
VKB_HOTAS_DEVICE="231d:3200"
VKB_HOTAS_USER="yourname"
VKB_HOTAS_ARGS="--button-count 32 --invert SLIDER"
```

The axis/button options in `VKB_HOTAS_ARGS` define the built-in default
profile. Once you save a mapping in HOTAS Mapper, its active profile takes
precedence, and the mapper's first-run defaults are taken from these options.

Apply changes with `sudo systemctl restart vkb-hotas`, or re-run `sudo ./install.sh`
after changing `VKB_HOTAS_DEVICE`, since the udev rule is generated from it.

| Option | Meaning |
|---|---|
| `--button-count 16\|32` | 16 = like a real T.16000M (default). 32 also passes buttons 17–32. |
| `--axis DST=ABS_SRC` | map an emulated axis (`X`, `Y`, `RZ`, `SLIDER`) to a source axis |
| `--invert DST` | invert an emulated axis (e.g. a backwards throttle) |
| `--buttons A,B,...` | choose which source buttons become emulated buttons 1..N |
| `--verbose` | log every open/close of the virtual stick |

### Mapping

The default mapping suits Gladiator NXT EVO bases:
`X←ABS_X, Y←ABS_Y, RZ←ABS_RZ (twist), SLIDER←ABS_Z (Omni Throttle lever)`.
If a source axis doesn't exist, the emulated axis is held at neutral.

To find your stick's axes, stop the service (so the real stick is visible to
you) and record yourself moving every control:

```bash
sudo systemctl stop vkb-hotas
./vkb-learn.py 60
sudo systemctl start vkb-hotas
```

It reports which axes spring back to centre and which hold position (a
throttle), plus button numbers above 16.

## Everyday use

- The service starts whenever the stick is plugged in, including at boot.
- `sudo systemctl stop vkb-hotas` makes the real stick visible again (for
  VKBDevCfg, or games that support it natively); `start` re-enables the remap.
- Logs: `journalctl -u vkb-hotas -b`

## Uninstall

```bash
sudo ./uninstall.sh
./proton-setup.py --undo            # restore Wine's default input backend in the prefixes
```

## Limitations

- One source stick per install. The emulated device always uses the same
  VID:PID, so two remapped sticks would be indistinguishable to games.
- If you also own a real T.16000M, games will see two identical sticks.
- Games with a fixed T.16000M profile use its layout: 4 axes, hat, button
  numbers. Games that support rebinding let you use all buttons.
- Anti-cheat: the virtual stick is an ordinary HID device, and no game files are
  modified. It has been used with Ace Combat 8 (Easy Anti-Cheat) without issue,
  but no guarantees.

## How it works

```
real stick ──evdev (grabbed)──▶ vkb-hotas.py ──/dev/uhid──▶ virtual T.16000M (hidraw + evdev)
     │                           ▲      │                           │
     │      ~/.config/vkb-hotas/ │      │ ctl.sock (live state,     └─▶ Wine winebus (hidraw) ──▶ DirectInput ──▶ game
     │      mapping.json ────────┘      ▼  test presses)
     └─ hidden: udev drops uaccess   vkb-mapper.py ◀──▶ browser UI
```

| File | Purpose |
|---|---|
| `vkb-hotas.py`, `vkb_common.py` | daemon and shared device-matching helpers (installed to `/usr/local/lib/vkb-hotas`) |
| `72-vkb-hotas.rules.in` | udev rule template: autostart, hiding, virtual-stick access |
| `vkb-hotas.service`, `vkb-hotas.default` | systemd unit and config template |
| `install.sh`, `uninstall.sh` | system install/removal |
| `proton-setup.py` | per-game Proton prefix configuration |
| `vkb-mapper.py`, `vkb-mapper.html`, `vkb-mapper.desktop` | HOTAS Mapper GUI (local web UI, standard library only) |
| `vkb-check.py`, `vkb-learn.py` | diagnostics: setup and Proton-log analysis; axis discovery |
| `dev/fake-daemon.py` | development: the daemon's mapping/socket logic with a simulated stick, for working on the GUI without root |

## License

MIT, see [LICENSE](LICENSE).
