# vkb-hotas

Use VKB flight sticks (or any other evdev joysticks) in Proton games that only
accept HOTAS models they know, by re-presenting them as Thrustmaster devices: one
stick as a **T.16000M**, and optionally a second one (e.g. a left-hand Omni
Throttle) as a **TWCS Throttle**, together Thrustmaster's T.16000M FCS HOTAS set.

> **Note:** this project was vibe coded with Claude Opus 5.5.

Some games, such as *Ace Combat 8: Wings of Theve* and *Star Wars: Squadrons*,
only enable their flight-stick controls for specific sticks. Under Proton,
DirectInput sees an unsupported stick correctly as a joystick, but the game
never opens it, or the stick ends up treated as a gamepad. vkb-hotas runs a
small root daemon that:

1. grabs the real stick(s) exclusively,
2. creates a virtual T.16000M (`044f:b10a`: X, Y, Rz twist, Slider throttle,
   hat, 16 or 32 buttons) and, with a second stick, a virtual TWCS Throttle
   (`044f:b687`: throttle, mini-stick, rocker, antenna, toe brakes, hat, 14 or
   32 buttons) through `/dev/uhid`, so Wine gets real hidraw devices with proper
   HID descriptors,
3. hides the real sticks from your session (udev drops their `uaccess` tag), so
   games see only the Thrustmaster devices.

Any button or axis of either stick can drive either virtual device. A real stick
can also be handed to games as it is, without stopping the service
(`vkb-mapper --expose`). Stop the service and everything returns to normal.

## Status

| Hardware / game | Result |
|---|---|
| VKB Gladiator NXT EVO Omni Throttle (R), `231d:3200` | axes, hat, 32 buttons verified through the virtual stick |
| Star Wars: Squadrons (Proton Experimental) | game acquires and polls the stick |
| Ace Combat 8 (Proton Hotfix) | game acquires and polls the stick; shows controller button icons; flight not yet confirmed |
| Project Wingman (Proton Experimental) | game opens the stick and receives input; bind controls in the game's binding menu (click a binding, then move the axis or press the button) |
| Second stick as TWCS Throttle | tested with simulated sticks only; not yet with real hardware or games |

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
sudo ./install.sh                   # pick your stick (and an optional throttle stick) from a list
sudo ./install.sh --device 231d:3200 --throttle 231d:3201   # or name them (any vendor works)
./tools/proton-setup.py             # as your user, games closed: pick games from a checklist
```

The installer asks which stick games should see as the T.16000M, then which
(if any) as the TWCS Throttle. It asks when nothing is configured yet, when the
configured stick isn't plugged in, or with `--pick`; otherwise a re-install keeps
your choice. Without a terminal it autodetects a single VKB stick, as before.
Sticks are named by `VID:PID`; if two connected sticks report the same one (two
identical models), the USB port is added, e.g. `231d:3200@usb-0000:18:00.3-2.1`,
so keep each in its port. You can switch sticks later in HOTAS Mapper without
reinstalling.

**Every Proton game you fly with the stick needs this once.** Run with no
arguments, `proton-setup.py` shows a checklist of your installed Proton games,
each marked *set up* or *not set up*. Use the arrow keys, Space to check or
uncheck, `a` for all/none, Enter to apply (it confirms first), and `q` to cancel.
Games already set up start checked; unchecking one restores Wine's default for
it. On a terminal without cursor control you get a numbered list instead.

You can also pass app ids directly, e.g. `./tools/proton-setup.py 895870` for
Project Wingman; `--list` shows ids and status. Without a terminal and with no
ids, it configures Squadrons and Ace Combat 8.

Without this setup, Wine can present the stick as a gamepad, and a game may then
ignore it (Project Wingman saw no input until its prefix was set up). It sets the winebus
`Enable SDL`=0 in each prefix, so Wine passes the virtual stick through its
hidraw backend, and clears stale DirectInput cache entries. Backups are written
next to each edited `.reg` file.

Then **restart Steam** (it may still hold the real stick open) and set each
game's launch options, with Steam Input off:

```
PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687 %command%
```

(The second id is the TWCS Throttle; it does no harm without one. Older setups
with only `0x044F/0xB10A` keep working for the T.16000M.)

## Verify

```bash
./tools/vkb-check.py
```

This checks the service, that each real stick is hidden (or visible, if you
exposed it), that the virtual devices are accessible, and how SDL classifies
them. To check a game, launch it once with
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687 PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%`,
open its controls menu, quit, and run `./tools/vkb-check.py` again. A working game
shows the virtual devices created as *joysticks*, the real sticks not visible,
and joystick devices "acquired, read N times".

## Remapping buttons and axes (HOTAS Mapper)

Some games, Ace Combat 8 among them, have no rebinding for the T.16000M, so
remapping happens before the game sees anything. Run:

```bash
vkb-mapper          # or "HOTAS Mapper" in your application menu
```

A local page opens in your browser. The tabs at the top switch between the
**Stick** (real stick → T.16000M) and the **Throttle** (second real stick → TWCS
Throttle); each tab has its own panels:

- **Choosing sticks:** click the real stick's name (amber, ▾) in a tab for a quick list, or open
  **Devices** for the same choices with each role's status. Choose which connected stick feeds the
  T.16000M and which (or none) feeds the TWCS Throttle. Picking the stick the other role uses swaps
  them. The choice takes effect at once and is saved for the service.
- **Your stick:** live axes, hat and every physical button. Pressed buttons light up, and
  each one is tagged with the emulated control it drives (on either device).
- **What the game sees:** the emulated device's buttons, hat directions and axes.
  - **Assign**, then press a button or hat direction on *either* stick. A source on the
    other stick shows a `THR`/`STK` tag. Assigning moves that button off any other slot (on
    both devices), and **Shift+Assign** adds a second source.
  - **Test** presses that button in-game for a moment, so you can work out what
    each emulated button does in a game, then note it in the slot's label.
  - Axes: pick the source (either stick's axes; or **Learn** by moving it), invert, and set
    a center deadzone. The TWCS defaults suit a left-hand Omni Throttle: lever → throttle,
    grip mini-stick → mini-stick, twist → rocker.
- **Stick map:** a picture of your stick with a pin on each button. Pins glow when pressed and are
  tagged with what they drive; hover one for details, and its game-button card lights up (and vice
  versa). Click **Place pins**, press a button on the stick, then click where it is (drag to adjust,
  × to remove). Zoom with the mouse wheel or the −/+ buttons (up to 8×) and drag to pan; pins
  keep their size on screen, so small hats are easy to hit, and **FIT** shows the whole picture.
  The default views are drawings of the Gladiator NXT EVO Omni Throttle (whole stick, SCG grip
  head, triggers in side profile, base front), mirrored for the throttle stick; generic outlines
  are available for other sticks. Each real stick has its own stick map.
  **Pins belong to the picture**: each drawing and each photo keeps its own pins, so changing a
  view's drawing shows that drawing's pins and changing back restores the old ones. Add your own
  views, and swap any drawing for a photo of your stick with **Photo…** (PNG/JPEG/WebP, up to
  10 MB). VKB button numbering depends on grip and firmware profile, so pins are placed by you
  rather than pre-filled.
- **5-way hats:** VKB's 4-way hats with center push report as five separate buttons. Click
  **+ 5-way hat**, name it, push up, right, down and left, press it in (or choose "No center
  push"), then click where it is. To show the same hat on another drawing, use **Place pins** and
  press any of its buttons: the whole hat gets placed, with no new setup. Its × removes it from
  that drawing only. It's drawn as one cross-shaped widget. Each segment lights up
  and dims when unmapped, hovering shows every direction's mapping, and double-clicking renames
  it. Its buttons are still mapped individually in the cards.
- **Button names:** click a `BTN n` chip on a card, or double-click a button in the physical grid,
  to name that physical button (e.g. "Trigger stage 1", "Rapid-fire paddle up"). The name
  appears under the chip on every card it feeds, in grid and stick-map tooltips and in
  messages. Names describe your hardware, so all profiles share them.
- **5-way hat badges:** a chip whose button belongs to a 5-way hat shows the hat and direction,
  e.g. `BTN 11 · Hat 2 ▲`.
- **Profiles:** keep one per game. A profile covers both devices; the selected one is applied.

Changes save to `~/.config/vkb-hotas/mapping.json`, and the daemon applies them within half a
second, even mid-game. No sudo or restart is needed. The stick map and button names are saved to
`layout.json` and `images/` beside it, shared by all profiles. Changing the button
count (16/32, or 14/32 for the TWCS) briefly re-creates that virtual device, so do it
with the game closed.

The mapper talks to the daemon over `/run/vkb-hotas/ctl.sock`, which only the
user in `VKB_HOTAS_USER` can open (the installer sets it to whoever ran
`sudo`). The web UI listens on 127.0.0.1 only, needs a per-session token, and
exits about 45 s after its last tab closes.

After updating (re-running `sudo ./install.sh`), close HOTAS Mapper and reopen it once it
has exited. An older mapper still running would serve the new page, so the page detects
that and saves nothing until you restart. Mappings and stick maps from before the
two-stick update are converted on load: they become the Stick's, and the Throttle starts
from defaults. Stick maps keep their views and drawings: pick a new drawing per view from the
dropdown next to **Photo…**, or add a view with **+ View** (e.g. one using "SCG grip side
(triggers)").

## Configuration

`/etc/default/vkb-hotas` (a systemd EnvironmentFile: **no comments after values**):

```sh
VKB_HOTAS_DEVICE="231d:3200"        # stick -> T.16000M
VKB_HOTAS_THROTTLE="231d:3201"      # optional stick -> TWCS Throttle ("" = none)
VKB_HOTAS_EXPOSE=""                 # roles given to games untouched: stick, throttle
VKB_HOTAS_USER="yourname"
VKB_HOTAS_ARGS="--button-count 32 --invert SLIDER"
```

(The comments above are for this README only; in the real file, comments go on their own lines.)
The installer and HOTAS Mapper write the device lines, and `vkb-mapper --expose/--hide`
writes `VKB_HOTAS_EXPOSE`.

The axis/button options in `VKB_HOTAS_ARGS` define the built-in default
T.16000M profile. Once you save a mapping in HOTAS Mapper, its active profile takes
precedence, and the mapper's first-run defaults are taken from these options.

Apply hand edits with `sudo systemctl restart vkb-hotas`. Nothing needs a re-install:
the udev rule no longer depends on the device.

| Option | Meaning |
|---|---|
| `--button-count 16\|32` | 16 = like a real T.16000M (default). 32 also passes buttons 17–32. |
| `--axis DST=ABS_SRC` | map an emulated axis (`X`, `Y`, `RZ`, `SLIDER`) to a source axis |
| `--invert DST` | invert an emulated axis (e.g. a backwards throttle) |
| `--buttons A,B,...` | choose which source buttons become emulated buttons 1..N |
| `--verbose` | log every open/close of the virtual devices |

### Mapping

The default mapping suits Gladiator NXT EVO bases:
`X←ABS_X, Y←ABS_Y, RZ←ABS_RZ (twist), SLIDER←ABS_Z (Omni Throttle lever)`.
If a source axis doesn't exist, the emulated axis is held at neutral.

To find your stick's axes, give it to yourself for a minute (so the real stick is
visible to you) and record yourself moving every control:

```bash
vkb-mapper --expose stick           # or: throttle
./tools/vkb-learn.py 60             # --role throttle for the throttle stick
vkb-mapper --hide stick
```

It reports which axes spring back to centre and which hold position (a
throttle), plus button numbers above 16.

## Everyday use

- The service starts whenever a joystick is plugged in, including at boot, and quits
  again if none of your configured sticks is there. Sticks unplugged while it runs are
  picked up again when plugged back in; the virtual device stays put meanwhile.
- **Give games the real stick** (for VKBDevCfg, or games that support it natively)
  without stopping the service:

  ```bash
  vkb-mapper --expose stick         # or throttle, or all
  vkb-mapper --hide stick           # back to the Thrustmaster device
  vkb-mapper --status               # which stick feeds what
  ```

  While exposed, the stick isn't grabbed or hidden and its virtual device is removed, so
  games see only the real one; restart a running game to pick up the change. The choice
  is remembered across restarts. Its buttons still feed the other virtual device if you
  cross-mapped them.
- `sudo systemctl stop vkb-hotas` turns everything off; `start` turns it back on. Plugging
  in any joystick also starts it.
- Logs: `journalctl -u vkb-hotas -b`

## Uninstall

```bash
sudo ./uninstall.sh
./tools/proton-setup.py --undo      # checklist of set-up games to restore to Wine's default backend
```

## Limitations

- At most two sticks: one becomes the T.16000M, one the TWCS Throttle.
- If you also own a real T.16000M or TWCS, games will see two identical devices.
- Games with a fixed Thrustmaster profile use its layout: the T.16000M's 4 axes, the
  TWCS's 7, a hat each, and their button numbers. Games that support rebinding let you use
  all buttons.
- The TWCS Throttle's descriptor is built from its documented controls (axes, 14 buttons,
  hat); its exact report format wasn't available, so axis resolutions may differ from the
  real device. Games identify it by VID:PID and axes.
- Anti-cheat: the virtual stick is an ordinary HID device, and no game files are
  modified. It has been used with Ace Combat 8 (Easy Anti-Cheat) without issue,
  but no guarantees.

## How it works

```
real stick ────┐                                ┌─▶ virtual T.16000M      (hidraw + evdev) ─┐
               ├─evdev (grabbed)─▶ vkb-hotas.py ┤                                           ├─▶ Wine winebus (hidraw) ─▶ DirectInput ─▶ game
throttle stick ┘     ▲              ▲      │    └─▶ virtual TWCS Throttle (hidraw + evdev) ─┘
                     │  mapping.json┘      │ ctl.sock (live state, test presses, device choice, expose)
  hidden: runtime udev rule drops uaccess  ▼
                                     vkb-mapper.py ◀──▶ browser UI
```

| Path | Purpose |
|---|---|
| `install.sh`, `uninstall.sh` | system install/removal (everything is installed flat into `/usr/local/lib/vkb-hotas`) |
| `service/` | the root daemon `vkb-hotas.py`, its systemd unit, config template `vkb-hotas.default` and udev rule `72-vkb-hotas.rules` |
| `mapper/` | HOTAS Mapper: server `vkb-mapper.py`, page `vkb-mapper.html` (standard library only, no build) and menu entry `vkb-mapper.desktop` |
| `common/` | `vkb_common.py`: config, roles, device selection, the mapping format and terminal pickers shared by everything |
| `tools/` | helpers you run as your user: `proton-setup.py` (per-game prefix setup), `vkb-check.py` (setup and Proton-log diagnostics), `vkb-learn.py` (axis discovery) |
| `dev/` | `fake-daemon.py`: the daemon's real event loop with simulated left and right Omni Throttles, for working on the GUI without root |
| `CLAUDE.md`, `.claude/skills/` | guidance and workflows for Claude Code sessions: verify an install, test the installer and the mapper UI without root, ship a change, analyse Proton logs |

## License

MIT, see [LICENSE](LICENSE).
