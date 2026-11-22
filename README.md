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
(`vkb-mapper --expose`), or feed a virtual **Xbox 360 controller** instead, for
games that support pads but not flight sticks (`vkb-mapper --xbox`, see
[Xbox controller mode](#xbox-controller-mode)). Stop the service and everything
returns to normal.

## Status

| Hardware / game | Result |
|---|---|
| VKB Gladiator NXT EVO Omni Throttle (R), `231d:3200` | axes, hat, 32 buttons verified through the virtual stick |
| Star Wars: Squadrons (Proton Experimental) | game acquires and polls the stick |
| Ace Combat 8 (Proton Hotfix) | flown with both sticks (T.16000M + TWCS); no rebinding in game, so remap in HOTAS Mapper; shows controller button icons regardless |
| MechWarrior 5 (Proton) | works with both sticks |
| Project Wingman (Proton Experimental) | game opens the stick and receives input; bind controls in the game's binding menu (click a binding, then move the axis or press the button) |
| Second stick as TWCS Throttle | VKB Gladiator NXT EVO Omni Throttle (L), `231d:3201`, Y as its throttle: works in Ace Combat 8 and MechWarrior 5 |
| Xbox controller mode | virtual pad recognised by SDL as an Xbox 360 controller; not yet in a game |

Other sticks work too (see [Not a VKB?](#not-a-vkb)); they've been tested with simulated
layouts, not yet with real hardware. Reports from other sticks and games are welcome.

## Requirements

- Linux with systemd + udev, and a kernel with `uhid` (all mainstream distros); Xbox
  controller mode also needs `uinput` (standard too)
- Python 3 and python-evdev available to **root's** `python3`
  (Arch `python-evdev`, Debian/Ubuntu `python3-evdev`, Fedora `python3-evdev`)
- Steam with Proton. Native Steam, Flatpak and Snap Steam installs are all
  detected by `proton-setup.py`.
- Optional, for HOTAS Mapper's own window: WebKitGTK 6.0 and PyGObject (Arch `webkitgtk-6.0`
  `python-gobject`, Debian/Ubuntu `gir1.2-webkit-6.0` `python3-gi`, Fedora `webkitgtk6.0`
  `python3-gobject`). Without them it opens in your web browser instead.
- Optional, for the tray icon: PyGObject with GTK 3 and libayatana-appindicator (Arch
  `libayatana-appindicator`, Debian/Ubuntu `gir1.2-ayatanaappindicator3-0.1`, Fedora
  `libayatana-appindicator-gtk3`). KDE Plasma shows it out of the box; GNOME needs the
  AppIndicator extension.

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
your choice. Without a terminal it takes the only connected VKB stick.
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

### Not a VKB?

Nothing here needs a VKB stick; it's just what this was built with. With any other joystick:

1. **Install:** `sudo ./install.sh` lists every connected joystick; pick yours (and an optional
   second one for the TWCS Throttle). Without a terminal, pass `--device VID:PID` (see `--list`).
2. **Axes:** the first mapping follows your stick's axes: X/Y, twist (`ABS_RZ`, or `ABS_RUDDER`)
   and throttle (`ABS_Z`, or `ABS_THROTTLE` as on a Logitech Extreme 3D). Check them in HOTAS
   Mapper by moving each control: the left panel shows the live axes, and an axis row's
   **Learn** picks whichever axis you move. An axis your stick doesn't have shows **— none —**
   and rests at the centre.
3. **Buttons and hats:** buttons are numbered in the order Linux reports them, and buttons 1–16
   pass through 1:1 (set the T.16000M to 32 buttons for more). Up to four hats are shown and can
   be assigned like buttons (`HAT`, `HAT2` …).
4. **Stick map:** a new one starts with generic grip and base outlines. Add photos of your own
   stick with **Photo…** and place pins on them.
5. **Proton setup** is the same as for everyone: `proton-setup.py` for each game, plus the
   launch options above.

If you also own a real Thrustmaster T.16000M or TWCS, games will see two of them (see
[Limitations](#limitations)).

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

HOTAS Mapper opens in its own window (or, without WebKitGTK, as a local page in your browser;
`vkb-mapper --browser` picks the browser anyway). Launching it again brings the open window to
the front, and closing the window stops it. Export asks where to save, Photo… and Import open a
file dialog, and links to other sites open in your browser. The tabs at the top switch between the
**Stick** (real stick → T.16000M), the **Throttle** (second real stick → TWCS
Throttle) and the **Xbox** controller (see [Xbox controller mode](#xbox-controller-mode));
each tab has its own panels:

- **Choosing sticks:** click the real stick's name (amber, ▾) in a tab for a quick list, or open
  **Devices** for the same choices with each role's status. Choose which connected stick feeds the
  T.16000M and which (or none) feeds the TWCS Throttle. Picking the stick the other role uses swaps
  them. The choice takes effect at once and is saved for the service.
- **What games see:** each stick tab shows a **VIRTUAL**, **XBOX** or **REAL** badge, and the
  **Games see: Virtual | Xbox pad | Real stick** switch at the top of the stick panel (also in
  **Devices**) changes it, the same as `vkb-mapper --hide/--xbox/--expose` (see
  [Everyday use](#everyday-use)).
- **Your stick:** live axes, hats (up to four) and every physical button. Pressed buttons light up, and
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
  - **One throttle at a time:** while a stick is selected as the TWCS Throttle (even if it's
    unplugged for a moment), the right stick's own throttle lever stops driving the T.16000M
    **Slider**, so games don't get two throttles. What the Slider does instead is set per profile
    with **With a throttle stick** on its row:
    - **centre** (default): it rests in the middle. Games that treat the throttle as
      accelerate/brake read that as neutral. Ace Combat 8 adds the T.16000M Slider to the TWCS
      throttle, so a Slider held at an end kept the throttle from ever going below half.
    - **idle end:** the minimum, or the maximum if its **Invert** is on, for games that read it
      as a plain 0–100% throttle.
    - **keep live:** the right lever keeps driving it.

    The row says **CENTRED** or **AT IDLE END · throttle stick set** while it applies. Your
    mapping isn't changed: set the throttle to none and the lever works again. A Slider you've
    pointed elsewhere (**— none —**, the throttle stick) is left alone.
- **Stick map:** a picture of your stick with a pin on each button. Pins glow when pressed and are
  tagged with what they drive; hover one for details, and its game-button card lights up (and vice
  versa). Click **Place pins**, press a button on the stick, then click where it is (drag to adjust,
  × to remove). Zoom with the mouse wheel or the −/+ buttons (up to 8×) and drag to pan; pins
  keep their size on screen, so small hats are easy to hit, and **FIT** shows the whole picture.
  Each real stick has its own stick map. What a new one starts with depends on the stick:
  - **VKB Gladiator NXT EVO Omni Throttle:** built-in photos of the right- or left-hand stick
    (from its id or name), from the front, trigger side, thumb side and logo side.
  - **Other VKB sticks:** drawings of the Gladiator NXT EVO Omni Throttle (whole stick, SCG grip
    head, triggers in side profile, base front), mirrored for the throttle stick.
  - **Anything else:** generic grip and base outlines.

  The dropdown next to **Photo…** offers all of them for any view, plus a **Straight stick**
  drawing (same grip, no Omni Throttle angle) for each hand and a **Thrustmaster TWCS Throttle**
  in two views (thumb side with the hats; front with the mini-stick, rocker switch, wheel and
  rudder paddle). The built-in photos come with pins for the author's button numbering. VKB
  button numbering depends on grip and firmware profile, so press each control to check its pin
  lights up, then drag or × to fix it. 5-way hat pins attach to your own hats with the same
  buttons; a new map gets them once you've set up the hats and pick the photo. Drawings start
  without pins.
  **Pins belong to the picture**: each drawing and each photo keeps its own pins, so changing a
  view's drawing shows that drawing's pins and changing back restores the old ones. Add your own
  views, and swap any drawing for a photo of your stick with **Photo…** (PNG/JPEG/WebP, up to
  10 MB).
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
- **Two-stage triggers:** a dual-stage trigger's full pull (e.g. B2) also holds stage 1 (B1),
  so whatever you map to B1 fires too. Under **Two-stage triggers** (below the physical
  buttons), click **+ Two-stage trigger** and pick stage 1 and stage 2: while stage 2 is held,
  stage 1 counts as released, so the game gets only stage 2's action and you can map the two
  separately. Stage 1 still sends instantly; a full pull shows stage 1 only for the instant
  before stage 2 closes. To hide even that, set a **Delay** (ms): stage 1 then only counts once
  held that long, which adds that much lag to it. Set per profile and per stick.
  **Assign** knows about it too: after **Assign**, pull through to assign stage 2, or press and
  let go of stage 1 to assign stage 1 (it waits for one or the other).
- **5-way hat badges:** a chip whose button belongs to a 5-way hat shows the hat and direction,
  e.g. `BTN 11 · Hat 2 ▲`.
- **Game defaults:** pick a game in **Game defaults** (profile bar) and every card shows, read-only,
  what that emulated button, hat direction or axis does in the game by default, e.g.
  `GAME Fire machine gun` on B1, so you know what to map without trying every button in the
  game. Your remapping and notes stay separate. Included: **Ace Combat 8 (Default flight stick
  layout)**, checked in game.
- **Your own game defaults** (**Manage ▾** next to Game defaults):
  - **New from my notes** turns the current profile's card notes into a set: try each button in
    the game once, write what it did in its card's note, then create the set.
  - **New empty set** / **Copy to my sets** (to change a shipped one), then **Edit on the cards**:
    the GAME lines become text fields and save as you type.
  - **Export…** saves a set as a small `.json` file to share; **Import…** loads one someone sent
    you. Your sets live in `~/.config/vkb-hotas/game-defaults/` (marked "yours" in the list).
  - To make a set part of HOTAS Mapper for everyone, add it to `mapper/game-defaults.json` in a
    pull request.
- **Profiles:** keep one per game. A profile covers both devices; the selected one is applied.

Changes save to `~/.config/vkb-hotas/mapping.json`, and the daemon applies them within half a
second, even mid-game. No sudo or restart is needed. The stick map and button names are saved to
`layout.json` and `images/` beside it, shared by all profiles. Changing the button
count (16/32, or 14/32 for the TWCS) briefly re-creates that virtual device, so do it
with the game closed.

The mapper talks to the daemon over `/run/vkb-hotas/ctl.sock`, which only the
user in `VKB_HOTAS_USER` can open (the installer sets it to whoever ran
`sudo`). HOTAS Mapper's server listens on 127.0.0.1 only and needs a per-session token.
Closing the window stops it; in browser mode it exits about 45 s after its last tab closes.

After updating (re-running `sudo ./install.sh`), close HOTAS Mapper and reopen it. An older
mapper still running would serve the new page, so the page detects that and saves nothing
until you restart. Updates never change an existing stick map's views: pick a different
picture per view from the dropdown next to **Photo…**, or add one with **+ View**. Stick maps
from before October 2026 are loaded as they are (pins on the Omni Throttle drawings may need
dragging into place; ones from before the two-stick update start fresh), and the old file
is kept as `layout.json.bak-v<old version>`.

## Xbox controller mode

Some games support Xbox controllers but not flight sticks or HOTAS. For those, a stick can
feed a virtual **Xbox 360 controller** instead of its Thrustmaster device. Games and Steam
see an ordinary wired Xbox 360 pad (`045e:028e`, the layout of Linux's own Xbox driver),
and Proton hands it to the game as an XInput controller.

- **Turn it on:** click the stick name on the **Xbox** tab (▾) and pick the sticks that should
  feed it. Picking a stick that's currently the T.16000M (or TWCS) asks first, because that
  device then disappears for games; picking a stick no role uses makes it the throttle stick.
  Picking a stick already feeding the pad offers to switch it back. **Devices** has the same
  choices as checkboxes in its **Xbox controller** section. Or set **Games see** to
  **Xbox pad** for a stick (Stick/Throttle tab, Devices, or the Xbox tab), or run
  `vkb-mapper --xbox stick` (`throttle`, `all`).
  `vkb-mapper --hide stick` goes back to the T.16000M. The choice is remembered across
  restarts, and only one device per stick is visible to games at a time.
- **One pad for both sticks:** every stick set to Xbox feeds the same controller. Only sticks
  set to Xbox reach it, so nothing drives two devices a game can see.
- **Mapping:** the **Xbox** tab works like the others (Assign, Learn, Test, notes, sources
  from either stick). Its mapping is separate from the T.16000M/TWCS ones, so switching
  modes keeps both. Defaults are flight-style:
  - stick X/Y → left stick, twist → right stick X;
  - Omni Throttle lever → triggers: back half **LT**, front half **RT** (each trigger's
    **Back half / Front half** setting; **Invert** flips which end is back);
  - buttons 1–10 → A, B, X, Y, LB, RB, Back, Start, left/right stick click, hat → D-pad.
    Guide is unassigned (it usually opens the Steam overlay).
  - **LT (full)** / **RT (full)** cards let a button pull a trigger all the way, e.g. the
    stick's trigger as RT.
  - **With a throttle stick:** while it's set to Xbox too, triggers set to the right stick's
    throttle take the throttle stick's throttle instead (the one the TWCS uses as its throttle),
    keeping their half and Invert settings; their rows say **NOW FROM THR Z**. Your mapping isn't
    changed, and a trigger you've pointed somewhere else is left alone. Assign the throttle's
    buttons on the Xbox tab as you like (they show a THR tag).
- **Proton:** don't run `proton-setup.py` for a game you play in Xbox mode. It turns off
  Proton's SDL gamepad support in that game's prefix (needed for the Thrustmaster devices),
  and SDL is what presents the pad to the game as an Xbox controller. Already did? Undo it
  with `./tools/proton-setup.py --undo`.
- **Steam Input** treats it like any Xbox controller, so a per-game Steam Input layout
  applies on top if you've set one.
- **Limits:** a pad has 11 buttons, 2 triggers, 2 sticks and a D-pad, so only the buttons you
  choose are used. Rumble is accepted and ignored.
- **Needs the `uinput` kernel module.** The installer loads it now and at every boot. If it
  warns that it can't load it because the kernel was updated, reboot.

## Configuration

`/etc/default/vkb-hotas` (a systemd EnvironmentFile: **no comments after values**):

```sh
VKB_HOTAS_DEVICE="231d:3200"        # stick -> T.16000M
VKB_HOTAS_THROTTLE="231d:3201"      # optional stick -> TWCS Throttle ("" = none)
VKB_HOTAS_EXPOSE=""                 # roles given to games untouched: stick, throttle
VKB_HOTAS_XBOX=""                   # roles feeding the virtual Xbox controller instead
VKB_HOTAS_USER="yourname"
VKB_HOTAS_ARGS=""                   # extra daemon options, for troubleshooting
```

(The comments above are for this README only; in the real file, comments go on their own lines.)
The installer and HOTAS Mapper write the device lines, and `vkb-mapper --expose/--xbox/--hide`
(or the **Games see** switch) writes `VKB_HOTAS_EXPOSE` and `VKB_HOTAS_XBOX`.

Buttons, axes and button counts are set in HOTAS Mapper, not here. Until you save a
mapping there, the daemon uses the built-in default. (Older versions took `--axis`,
`--invert`, `--buttons` and `--button-count` here; the installer removes them.)

Apply hand edits with `sudo systemctl restart vkb-hotas`. Nothing needs a re-install.

| Option for `VKB_HOTAS_ARGS` | Meaning |
|---|---|
| `--verbose` | log every open/close of the virtual devices |
| `--no-hide` | don't hide the real sticks from your session |
| `--no-grab` | don't grab the real sticks exclusively |

### Mapping

The default mapping suits Gladiator NXT EVO bases:
`X←ABS_X, Y←ABS_Y, RZ←ABS_RZ (twist), SLIDER←ABS_Z (Omni Throttle lever)`.
Until you save a mapping, these follow the connected stick: a stick without `ABS_Z` gets its
`ABS_THROTTLE` as the throttle, one without `ABS_RZ` its `ABS_RUDDER` as the twist, and any
other axis it lacks is left unmapped (held at neutral). New profiles start the same way. To check
or change them, open HOTAS Mapper: the left panel shows its live axes, and an axis row's **Learn** picks
whichever axis you move. Its buttons appear in the physical grid as you press them; set
the T.16000M to 32 buttons to pass buttons 17–32 through.

## Everyday use

- The service starts whenever a joystick is plugged in, including at boot, and quits
  again if none of your configured sticks is there. Sticks unplugged while it runs are
  picked up again when plugged back in; the virtual device stays put meanwhile.
- **Tray icon (HOTAS Tray):** a flight stick icon in the system tray, started at login. Click it for
  a menu with each stick's **Virtual T.16000M/TWCS / Xbox pad / Real stick** (the current one
  ticked) and **Open HOTAS Mapper**. Its tooltip says what games see, and it gets an amber
  warning badge while a stick is given to games as the real one, so you notice. No root, no
  terminal; it follows changes made in HOTAS Mapper or with `vkb-mapper`. It's part of HOTAS
  Mapper rather than a separate app: after **Quit tray icon**, right-click HOTAS Mapper in the
  application menu and pick **Show tray icon** (or run `vkb-mapper --tray`). Turn off the
  login start in your desktop's Autostart settings ("HOTAS Tray"; that survives reinstalls).
- **Give games the real stick** (for VKBDevCfg, or games that support it natively)
  without stopping the service: pick **Real stick** in the tray menu, flip **Games see** to
  **Real stick** in HOTAS Mapper, or

  ```bash
  vkb-mapper --expose stick         # or throttle, or all
  vkb-mapper --xbox stick           # feed the virtual Xbox controller instead
  vkb-mapper --hide stick           # back to the Thrustmaster device
  vkb-mapper --status               # which stick feeds what
  vkb-mapper --tray                 # the tray icon, if you quit it
  ```

  While exposed, the stick isn't grabbed or hidden and its virtual device is removed, so
  games see only the real one; restart a running game to pick up the change. The choice
  is remembered across restarts. Its buttons still feed the other virtual device if you
  cross-mapped them.
- If a stick is unplugged and plugged back in (or its USB hub resets) while a game is running,
  it stays hidden from the game, and the virtual devices stay put meanwhile.
- `sudo systemctl stop vkb-hotas` turns everything off and gives the real sticks back to your
  session (even after a crash); `start` turns it back on. Plugging in any joystick also starts
  it. If a stopped service ever still hides a stick, `./tools/vkb-check.py` flags it; replug
  the stick once.
- Logs: `journalctl -u vkb-hotas -b`. A line "kernel event buffer overflowed" means some
  stick events were lost before the daemon read them; it re-reads the stick's state right
  away, so no button stays stuck. If a button ever seems held after you let go, check for it.

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
throttle stick ┘     ▲              ▲      │    ├─▶ virtual TWCS Throttle (hidraw + evdev) ─┘
                     │  mapping.json┘      │    └─▶ or: virtual Xbox 360 pad (uinput evdev) ──▶ Wine winebus (SDL) ─▶ XInput ─▶ game
  hidden: runtime udev rule drops uaccess  │ ctl.sock (live state, test presses, device choice, mode)
                                           ▼
                                     vkb-mapper.py ◀──▶ HOTAS Mapper (window or browser), tray
```

| Path | Purpose |
|---|---|
| `install.sh`, `uninstall.sh` | system install/removal (everything is installed flat into `/usr/local/lib/vkb-hotas`) |
| `service/` | the root daemon `vkb-hotas.py`, its systemd unit, config template `vkb-hotas.default` and udev rule `72-vkb-hotas.rules` |
| `mapper/` | HOTAS Mapper: server `vkb-mapper.py`, page `vkb-mapper.html` (standard library only, no build), its window `vkb_mapper_app.py` (GTK4 + WebKitGTK, optional), built-in stick photos `pictures/` (+ `pictures.json` with their default pins), the tray icon `vkb-tray.py` (+ `vkb-tray.desktop`), the app icons `icons/`, game default references `game-defaults.json` and menu entry `vkb-mapper.desktop` |
| `common/` | `vkb_common.py`: config, roles, device selection, the mapping format and terminal pickers shared by everything |
| `tools/` | helpers you run as your user: `proton-setup.py` (per-game prefix setup), `vkb-check.py` (setup and Proton-log diagnostics) |
| `dev/` | `fake-daemon.py`: the daemon's real event loop with simulated left and right Omni Throttles (and a stand-in Xbox pad), for working on the GUI without root |
| `CLAUDE.md`, `.claude/skills/` | guidance and workflows for Claude Code sessions: verify an install, test the installer and the mapper UI without root, ship a change, analyse Proton logs |

## License

MIT, see [LICENSE](LICENSE).
