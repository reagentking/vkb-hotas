# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

vkb-hotas makes unsupported flight sticks usable in Proton games that only accept HOTAS models they
recognise (Ace Combat 8, Star Wars: Squadrons). It was built for the user's VKB Gladiator NXT EVO
Omni Throttles (right `231d:3200`, left `231d:3201` "VKBsim Gladiator EVO OT L", 6 axes / hat / 79
buttons each), but any evdev joystick works. A root daemon grabs the real sticks, re-creates them
via `/dev/uhid` as Thrustmaster devices, and hides the real ones from the user session. Two
**roles**: *stick* → **T.16000M (`044f:b10a`)**; *throttle* → **TWCS Throttle (`044f:b687`)**,
optional. Together they're Thrustmaster's T.16000M FCS HOTAS set. HOTAS Mapper (its own GTK/WebKit
window, or a browser page) remaps buttons and axes live, because AC8 has no rebinding for this
stick. For games that support pads but not flight sticks, a role can instead feed one shared
virtual **Xbox 360 controller** (uinput, `045e:028e`).

**Goals:**
1. Games see only the Thrustmaster devices, recognised as joysticks (not gamepads).
2. Remapping without sudo and without restarting games.
3. Shareable: no machine-specific assumptions. Required: standard library plus python-evdev.
   Optional: PyGObject with GTK 4 + WebKitGTK 6.0 (the window; else the browser) and GTK 3 +
   libayatana-appindicator (the tray).

**Status:** Squadrons acquires and polls the stick. Ace Combat 8 is flown with both sticks (the
user's left OT as the TWCS, its **Y axis as the throttle**: TWCS Z ← `ABS_Y`); it shows controller
button icons regardless. MechWarrior 5 works with both sticks. Project Wingman (appid 895870)
works once its prefix is set up with `proton-setup.py 895870`; controls are bound in its own menu.
The uinput pad is recognised by SDL3 as "Xbox 360 Controller" (A, sticks, triggers, D-pad read
correctly), not yet tried in a game. Non-VKB sticks have only been tested with the fake daemon's
simulated layouts (`--model extreme3d|multihat`), never with real hardware.

## Repository layout

| Path | Contents |
|---|---|
| `service/` | root daemon `vkb-hotas.py`, systemd unit, config template, udev rule |
| `mapper/` | HOTAS Mapper server `vkb-mapper.py`, page `vkb-mapper.html`, window `vkb_mapper_app.py`, tray `vkb-tray.py`, `.desktop` entries, `icons/`, built-in photos `pictures/`, `game-defaults.json` |
| `common/` | `vkb_common.py`, shared by service, mapper and tools |
| `tools/` | user-run helpers: `proton-setup.py`, `vkb-check.py` (run from the repo; not installed) |
| `dev/` | `fake-daemon.py` (GUI testing without root) |
| top level | `install.sh`, `uninstall.sh`, docs |

- **The install is flat:** `install.sh` copies the service, mapper and common files into
  `/usr/local/lib/vkb-hotas/` (photos in `pictures/`). The systemd unit's `ExecStart` and the
  `/usr/local/bin/vkb-mapper` symlink point there.
- **Finding `vkb_common`:** every script puts both its own directory and `../common` on
  `sys.path` before importing it, so the same file runs from the repo and from the flat install.
  Keep that bootstrap in new scripts.

## Commands

There is no build step, test suite or linter. Python files are run directly; `service/vkb-hotas.py` must
run as root.

```bash
# Syntax-check everything (don't use py_compile: it writes __pycache__, see Gotchas)
for f in */*.py; do python3 -c "import ast; ast.parse(open('$f').read())" || echo "FAIL $f"; done
bash -n install.sh uninstall.sh
t=$(mktemp --suffix=.rules); cp service/72-vkb-hotas.rules $t; udevadm verify $t; rm $t
systemd-analyze verify service/vkb-hotas.service

# Install / reinstall after any change (copies to /usr/local/lib/vkb-hotas, restarts the service)
sudo ./install.sh [--device SEL] [--throttle SEL|none] [--pick] [--user NAME]   # --list shows sticks + selectors
sudo ./uninstall.sh

# As the user, over the control socket; no sudo
vkb-mapper [--browser | --no-browser]                      # HOTAS Mapper
vkb-mapper --status | --expose ROLE | --xbox ROLE | --hide ROLE   # ROLE: stick, throttle or all
vkb-mapper --tray                                          # the tray icon

# Verify the whole chain: service, hiding, virtual devices, SDL view, control socket, mapping file,
# plus analysis of the newest ~/steam-*.log
tools/vkb-check.py            # or: tools/vkb-check.py ~/steam-<appid>.log
journalctl -u vkb-hotas -b

# Proton prefix setup (as user, games closed). No ids on a terminal: curses checklist of all
# prefixes (checked = set up; unchecking restores). No ids without a terminal: 1222730, 2288340
tools/proton-setup.py [APPID...] | --list | --undo [APPID...]
```

A game launch that produces an analysable log uses these launch options:
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687 PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%`.

### Working with the user's installed system

- **sudo is the human's job.** `sudo ./install.sh` and other privileged steps need a password, so the
  user runs them in a real terminal; an inline `!` command box can't take one. Give them the exact
  command, then verify afterwards (the `verify-install` skill).
- **Restart the mapper after a reinstall.** Closing the window stops its server at once (browser
  mode: 45 s after the last tab). The `API_VERSION` check guards the gap.
- **Editing the user's live files** (`~/.config/vkb-hotas/*.json`):
  - only with their go-ahead, and make a timestamped backup first;
  - check that HOTAS Mapper isn't running;
  - validate with the same normalizer the server uses.
  - If you write a new format while an older mapper is still installed, an old server could drop
    fields on save. Tell the user to reinstall before opening the mapper.
- **Feedback can be on an older install.** Before changing something the user reported, check which
  version they're looking at (`grep API_VERSION /usr/local/lib/vkb-hotas/vkb-mapper.py`).

### Project skills (`.claude/skills/`)

| Skill | Use it for |
|---|---|
| `verify-install` | "I ran the install, check if it's working": installed vs repo, stale mapper, health checks, read-only validation of the user's files |
| `test-mapper-ui` | Testing HOTAS Mapper in the browser (and the WebKit window, the tray) against `dev/fake-daemon.py`, isolated from the user's own mapper |
| `ship-change` | "Update docs, commit and push": docs-vs-diff, `API_VERSION` rule, prechecks, commit, push |
| `test-installer` | `install.sh` / `uninstall.sh` changed or files moved: run both in a sandbox root without sudo, and check every installed file against its source (`SANDBOX_CONFIG=FILE` starts from an existing config) |
| `proton-log-analysis` | Whether a game accepts the stick: launch options, `vkb-check.py` log analysis, Wine trace pitfalls |

### Testing without root

**GUI, control protocol and the whole event loop:** `dev/fake-daemon.py` runs the daemon's real
`Daemon` class (imported from `service/vkb-hotas.py`) with a `FakeSource` of simulated Omni Throttles
(right `231d:3200`, left `231d:3201`), a `FakeUhid`, a `FakePad` and no hider. Only uhid, uinput,
grabbing, udev and the real config file are swapped out (selection and modes persist to
`DIR/config`), so device selection, swapping, modes, cross-mapping and replugging are all testable
without root.

```bash
dev/fake-daemon.py [--still] [--sticks 1|2] [--twin] [--throttle 231d:3201] [--model vkb|extreme3d|multihat]
VKB_HOTAS_CTL=$XDG_RUNTIME_DIR/vkb-hotas-dev/ctl.sock mapper/vkb-mapper.py --mapping $XDG_RUNTIME_DIR/vkb-hotas-dev/mapping.json
dev/fake-daemon.py --press 17 [--stick 1]   # simulate a press (or hat:left etc.) on simulated stick N
dev/fake-daemon.py --unplug 1 / --plug 1    # simulate unplugging / replugging stick N
```

- **Extra commands:** `fake_press` and `fake_plug` on the socket; the real daemon doesn't accept them.
- `--twin` gives both sticks the same VID:PID, to test `@port` selectors.
- `--model` swaps simulated stick 0 for a non-VKB layout (`MODELS`): `extreme3d` (Logitech Extreme
  3D Pro `046d:c215`: X Y RZ + `ABS_THROTTLE`, 12 buttons, one hat) or `multihat` (made-up
  `1209:0001`: `ABS_RUDDER` twist, `ABS_THROTTLE`, four hats, 24 buttons, no `ABS_Z`).
  `--press hat3:up` presses extra hats. Use them whenever a change could assume a VKB.
- **Socket path limit:** unix socket paths max out at 107 bytes, so keep `--dir` short.
- The `Daemon` constructor doesn't scan; `run()` / `wait_for_sticks()` / `scan(force=True)` attach
  sticks.

**Mapper logic in isolation:** load the hyphenated daemon file with importlib. A `Mapper` is one
role's output; it reads every role's `PhysState`, which is how cross-mapping works. `FakeStick` in
`dev/fake-daemon.py` is a ready-made fake device: it tracks its own state for `active_keys()` /
`absinfo()` (what `PhysState.resync` reads) and `overflow(*lost)` simulates a SYN_DROPPED. Set
`ps.stages = {1: (2, 0.0)}` to test a two-stage trigger. `Daemon(..., {"stick": "xbox", ...},
make_pad=FakePad)` takes modes; `FakePad` keeps the last `out`.

```python
import importlib.util
sys.path.insert(0, "common"); s = importlib.util.spec_from_file_location("v", "service/vkb-hotas.py"); v = importlib.util.module_from_spec(s); s.loader.exec_module(v)
m = v.Mapper("stick"); m.apply(vkb_common.normalize_profile({...})["stick"])
ps = v.PhysState(FakeStick(0, 0x3200, "R")); ps.feed(evdev.InputEvent(0, 0, E.EV_KEY, 0x2c0, 1))
m.output(time.monotonic(), {"stick": ps, "throttle": None})   # -> (mask, hat, axes)
```

**Socket tests from Claude Code** need `dangerouslyDisableSandbox`: the Bash sandbox blocks unix
sockets and `$XDG_RUNTIME_DIR`. The `test-mapper-ui` scripts already assume that.

**Root-only paths** (uhid, uinput, hiding, udev) can only be tested on the installed service. Live
checks:
- send `{"cmd":"pulse","role":"stick","button":N}` to `/run/vkb-hotas/ctl.sock` (read the `hello`
  line first) and watch `EV_KEY` on the virtual evdev node;
- read-only runs of the daemon's classes against a real stick (no grab, uhid or config writes)
  work without stopping the service if the stick is exposed;
- hiding: watch `/dev` with a fast polling loop while the user replugs a stick.

When killing test processes, select them via `/proc/<pid>/cmdline` (see Gotchas), not `pkill -f`.

## Architecture

```
real stick ─────┐                                     ┌─▶ virtual T.16000M      ─┐
                ├─evdev (EVIOCGRAB)─▶ vkb-hotas.py ───┤                           ├─(hidraw)─▶ Wine winebus ─▶ DirectInput ─▶ game
throttle stick ─┘                       (root)        ├─▶ virtual TWCS Throttle ─┘
                                                      └─▶ or: virtual Xbox 360 pad (uinput) ─(SDL)─▶ Wine winebus ─▶ XInput ─▶ game
                                   ▲            │
          ~/.config/vkb-hotas/mapping.json      └─ /run/vkb-hotas/ctl.sock (JSON lines) ◀─▶ vkb-mapper.py (user, 127.0.0.1 HTTP+SSE) ◀─▶ page, in its window or a browser
                                                                                         ◀─▶ vkb-tray.py
```

**Daemon (`service/vkb-hotas.py`).** `main()` only parses arguments and builds a **`Daemon`**, whose
single-threaded `poll()` loop serves both roles. It takes its collaborators as arguments
(`source`, `make_uhid`, `make_pad`, `hider`, `persist`), which is how `dev/fake-daemon.py` reuses the same loop.
The poll set is rebuilt every iteration, because devices come and go: sticks attach/detach, and a
virtual device is destroyed and re-created when its `button_count` changes the HID descriptor.

- **`Role`** (one per role) holds the selector (`None` = not used), `mode` (`virtual` | `xbox` |
  `real`; `exposed` is `mode == "real"`), the attached evdev device and its `PhysState`, its hidden
  nodes, its `Mapper` and its `Uhid`.
- **`PhysState`**: one real stick's raw axes, pressed button numbers and hats (`hats {1: [x, y],
  2..4 if present}` from `ABS_HAT0-3`, `HAT_AXES`; hat 1 always exists, neutral without one).
- **`Mapper`**: one role's output. Computes that emulated device's report from the role's profile
  section and *every* role's `PhysState` (cross-mapping). `make_spec(role, n)` gives the HID
  descriptor and packer: T.16000M 9/11 bytes; TWCS 17/19 bytes (buttons padded to a byte, hat, 7 u16
  axes X Y Z Rx Ry Rz Slider).
- **Scanning:** every second, `scan()` checks `DeviceSource.signature()` (the names in `/dev/input`
  and `/dev/hidraw*`) and, only when it changed (or `force=True`, as `select` does), lists joysticks
  (`list_sticks`: EV_ABS plus joystick/gamepad buttons, no touchpads, not our own
  `phys == vkb-hotas/uhid`), attaches configured sticks that appeared and publishes the list in
  `hello.devices`. An unplugged stick is detected by POLLHUP/ENODEV and detached, but its **virtual
  device stays** (neutral), so running games keep it; the stick re-attaches when it's back. The
  daemon only exits at start, if no configured stick shows up within `--wait`.
- **Selectors:** `VID:PID`, or `VID:PID@<port>` (the evdev `phys` minus `/inputN`) when two
  connected sticks share a VID:PID; `selector_for()` adds the port only then. Nothing else is
  accepted (`SELECTOR`, `parse_match` raises): an invalid configured value is logged and ignored.
  Vendor-only, "any VKB" (`""`) and `/dev/input/eventN` selectors were removed in 2026-11; the
  installer replaces such a value by asking (or, without a terminal, the only VKB stick).
- **Commands** (control socket): `hello`; `pulse {role, button}` (`role: "xbox"` takes a button
  name); `mode {role|"all", mode}`; `quiet` (this client gets no live `state` lines, only
  hello/result/mapping: the tray; `ControlServer.watchers()`); `select {role, device|null}`
  (validated against connected sticks; picking the other role's stick swaps them; the stick role
  can't be none). `select`/`mode` reply `{"t":"result","ok","error"}` to the caller only, *after*
  broadcasting the new `hello`, and persist to `/etc/default/vkb-hotas` through `write_config`
  (atomic, keeps other lines, refuses odd characters).
- **Modes** (`set_mode`): `real` = ungrab, unhide, destroy its virtual device; its events are still
  read (live view, cross-mapped sources). `xbox` = grabbed and hidden like `virtual`, but its uhid
  device is destroyed and it feeds the pad. Persisted by `mode_config()` as `VKB_HOTAS_EXPOSE` +
  `VKB_HOTAS_XBOX` (EXPOSE wins if both name a role).
- **Xbox pad:** `XboxPad` is an `evdev.UInput` with xpad's 360 layout (BTN_SOUTH…, sticks
  ±32768 with Y up negative, triggers ABS_Z/ABS_RZ 0-255, D-pad as ABS_HAT0X/Y), name
  "Microsoft X-Box 360 pad", `phys = UHID_PHYS` so discovery skips it. uinput, not uhid, because a
  360 pad isn't HID; SDL recognises the VID:PID + layout and Proton's SDL backend makes it XInput.
  `XboxMapper` computes it from the profile's `xbox` section using `pad_phys()`, i.e. **only sticks
  in xbox mode** (so no stick drives two visible devices); axis values are 0..top (sticks centred
  at 32768), `XboxPad.send` makes them signed and writes only changes. `_update_pad()`: created when
  an xbox-mode stick attaches, kept while it's unplugged, dropped when no configured role is in xbox
  mode; on OSError it runs `modprobe uinput` once and retries.
- **Messages:** `hello {roles{r: selector, exposed, mode, device|null, emulated{name,id,buttons,axes,
  held,active}}, xbox{name,id,active,lever,buttons,axes}, devices[], profile, mapping, error}`;
  `device` has `hats` (numbers present); `raw` is `{b, h: hat 1 [x, y], hx: {"2": [x, y], …} (only
  when the stick has hats 2-4), a}`.
  `state {roles{r: raw|null, out}, xbox{out}}` (≤30 Hz, only when something changed; `out` is
  computed even while inactive; the pad's `out.b` is a mask over `XBOX_BUTTONS`); `mapping`;
  `result`.
- **`MappingWatcher`** polls `mapping.json` every 0.5 s (inode, mtime, size). As root it refuses
  symlinks, non-regular files and files not owned by `VKB_HOTAS_USER`. Without the file the daemon
  uses the built-in default, `default_profile({role: axis names of its connected stick})`, refitted
  on attach/detach (`_refit_fallback`); HOTAS Mapper's `initial_mapping()` builds the same from
  `hello`'s device axes, and the page's `freshRole`/`fitAxis` do it for new profiles. Fitting
  (`fit_axis`, `AXIS_ALTERNATIVES`): a default axis the stick lacks becomes its first alternative
  (`ABS_Z` → `ABS_THROTTLE`, `ABS_RZ` → `ABS_RUDDER`), else unmapped. Saved mappings are never
  refitted. (Before 2026-11 a non-VKB stick's throttle was silently dead until remapped.)
  `VKB_HOTAS_ARGS` is only for debugging flags (`--verbose`, `--no-hide`, `--no-grab`); the old
  mapping options (`--axis`, `--invert`, `--buttons`, `--button-count`) were removed in 2026-11
  and `install.sh` strips them from existing configs (argparse would otherwise refuse to start).

**User decisions behind the roles** (asked, not guessed):
- Two Omni Throttles become a T.16000M + TWCS (not two T.16000Ms, not one merged device).
- Sticks are chosen in the installer *and* live in HOTAS Mapper's Devices panel; the root daemon
  accepting a "select" from the socket owner was accepted as the cost.
- Cross-mapping is allowed: any source on either stick can feed either device.
- What games see is switchable per stick (CLI, the mapper's "Games see" switch and VIRTUAL/XBOX/REAL
  badge, the tray) and persists across restarts.
- Automatic "one throttle": while the throttle role has a selector (connected or not), the
  T.16000M Slider ignores a source on the stick's *own* stick and is held per the stick section's
  `slider_with_throttle`: `centre` (default, `top // 2` like an unmapped axis), `idle` (0, or top
  with Invert) or `live`. `Mapper.held {axis: mode}`, set by `Daemon._update_idle()` (init,
  `select`, `set_mode`, `_apply`), reported as `hello.roles.stick.emulated.held`; the page shows a
  "With a throttle stick" dropdown on the Slider row and a CENTRED / AT IDLE END note. The mapping
  is untouched. User chose: automatic, "selected" (not "connected"), T.16000M only, a per-profile
  dropdown. Idle end was the first default, but Ace Combat 8 adds the T.16000M Slider to the TWCS
  Z as accelerate/brake, so the throttle never went below half (seen in the Proton log's raw
  reports); centre fixed it in game.
- Its pad counterpart: while the throttle role has a selector *and* is in xbox mode,
  `XboxMapper.lever = (from, to)` makes LT/RT whose source is the T.16000M Slider's own-stick source
  read the TWCS Z source instead (halves/invert kept; other trigger sources left alone; no lever
  when either source is missing). Computed in `Daemon._update_idle()`, reported as
  `hello.xbox.lever`; the page notes "NOW FROM THR Z".
- Xbox mode: a third "Games see" choice per stick (not tied to profiles); **one shared pad** for
  both sticks; flight-style defaults (stick → left stick, twist → right X, lever split back half LT
  / front half RT).

**Shared model (`common/vkb_common.py`).** Owns config reading/writing (`/etc/default/vkb-hotas`:
`VKB_HOTAS_DEVICE`, `VKB_HOTAS_THROTTLE`, `VKB_HOTAS_EXPOSE`, `VKB_HOTAS_XBOX`, `VKB_HOTAS_USER`,
`VKB_HOTAS_ARGS`; `role_modes()` / `mode_config()`), roles and their emulated ids, axes and button
counts, selectors and stick discovery, the mapping schema and defaults, and the terminal pickers
(`checklist`, `pick_one`, used by `proton-setup.py` and `install.sh`). `normalize_profile` /
`normalize_mapping` are the single validator used by the daemon, the GUI server and the checker.
Change the schema there and nowhere else.

- **Mapping version 2:** a profile is `{stick: SECTION, throttle: SECTION, xbox: XBOX}` (`xbox` is
  filled with defaults when missing). XBOX: `axes{LX LY RX RY LT RT: {src, invert, deadzone, half:
  null|"low"|"high"}}` (`half` = one half of the source drives the whole output; trigger deadzones
  sit at the start of the travel), `buttons{A … RS, LT, RT: [sources]}` (LT/RT buttons pull the
  trigger fully), `hat` (D-pad), `labels`; its sources are relative to `XBOX_HOME = "stick"`. A
  section is `button_count` (stick 16|32, throttle 14|32); `axes{...: {src, invert, deadzone%}}`
  (stick X Y RZ SLIDER; throttle X Y Z RX RY RZ SLIDER); `buttons{"N": [sources]}`;
  `hat{up,down,left,right: [sources]}`; `labels`; `stages[{first, second, delay ms 0-500}]`
  (two-stage triggers, physical buttons of the role's own stick); stick only:
  `slider_with_throttle`. Version 1 (one stick profile at the top level) becomes the stick section.
- **Sources** are relative to the section's role: a physical button number (1-based),
  `"hat:<dir>"` (hat 1) or `"hat2:<dir>"` … `"hat4:<dir>"` (`HAT_SOURCE`) on the role's own stick, or the same prefixed with the other role (`"throttle:5"`,
  `"stick:hat:up"`). Axis sources likewise (`"ABS_Z"`, `"throttle:ABS_Z"`). The normalizer strips a
  prefix naming the section's own role and drops duplicates. `split_source()` resolves them.

**Physical button numbering** is the index into the sorted list of evdev `EV_KEY` codes (+1). That
matches HID / VKBDevCfg numbering (Linux maps HID buttons 1–16 to `0x120–0x12f`, then `0x2c0+`).
Keep every tool consistent with this.

**Hiding a real stick takes two mechanisms; both are needed.**
- The `Hider` writes `/run/udev/rules.d/72-vkb-hotas-hide.rules`, reloads udev and retriggers, so
  logind won't re-add the ACL on login or session switch. It's a runtime file (not generated at
  install) so that a stick chosen later in HOTAS Mapper is hidden too. It sorts between
  `70-uaccess` and `73-seat-late` like the installed rule. It has two kinds of lines:
  - one `KERNEL=="eventN|jsN|hidrawN", TAG-="uaccess", MODE="0600"` line per node of the attached
    sticks it replaces;
  - **identity lines** for every configured, non-exposed role, attached or not
    (`Hider.identity(selector)`): `KERNEL=="event*|js*|hidraw*", ATTRS{idVendor}=="…",
    ATTRS{idProduct}=="…", TAG-="uaccess", MODE="0600"`, plus `ENV{ID_PATH}=="pci-<pci>-usb-0:<port>:*"`
    (after an `IMPORT{builtin}="path_id"` line) for `@usb-<pci>-<port>` selectors. Why: a USB hub
    reset mid-game (the user's cat; MechWarrior 5) re-created both sticks' nodes under new names,
    and in the ~2 s before the daemon re-attached, Wine opened the real sticks' hidraw nodes; an
    opened device stays open, so the game had 4 joysticks until restarted. With identity lines
    udev hides a (re)appearing stick at creation (verified live with a 5 ms `/dev` watcher). A
    port udev names differently gets no identity line. Check the rule with `udevadm verify`.
- The daemon also `chmod 0600`s the nodes. With POSIX ACLs that zeroes the mask, which neutralises
  the ACL that is already applied.
- Detaching, exposing or exiting rewrites/removes the file and retriggers. Kernel names get reused
  after an unplug, so a stale file must never survive: `--cleanup` (run at startup and as
  `ExecStopPost`, i.e. even after a crash) deletes it and retriggers input/hidraw.
- **Un-hiding must also chmod back.** The retrigger re-adds the uaccess ACL entry but only resets
  the mode where a rule sets one (hidraw came back 0660; event/js stayed 0600 with `mask::---`,
  still blocked after `systemctl stop`). The `Hider` records each node's mode before hiding
  (`/run/vkb-hotas/hidden.json`, so `--cleanup` can restore after a crash) and chmods it back after
  the retrigger; a recorded mode without group/other bits restores as 0660. `vkb-check.py` reports
  real-stick nodes that are blocked while the service is stopped.
- The installed `service/72-vkb-hotas.rules` is static: it starts the service on any joystick
  `add` (`ENV{ID_INPUT_JOYSTICK}=="1"`; the daemon exits if no configured stick is there) and
  grants `uaccess` on both virtual devices' hidraw nodes.

**GUI (`mapper/vkb-mapper.py` + `.html`).** The server is stdlib `ThreadingHTTPServer`. It enforces three
guards, and keeps them:
- binds `127.0.0.1` only,
- requires a per-session token (`X-Token` header or `?t=`),
- checks the `Host` header (anti-DNS-rebinding).

- `/api/events` is SSE relaying the daemon socket. It coalesces state lines, and reconnects when
  the daemon restarts.
- `GET|PUT /api/mapping` (GET gives `initial_mapping()` = `default_profile()` before the first
  save); `PUT` validates via `vkb_common` and writes atomically.
- `GET|PUT /api/layout`, `POST /api/image?view=ID&role=R` (raw body), `GET /api/image/<name>`
  (token in the query, so `<img>` works) and `GET /api/picture/<id>` back the stick map.
- `POST /api/pulse {role, button}` (role `xbox`: a button name), `POST /api/select {role, device}`
  and `POST /api/mode {role, mode}` relay to the daemon (`daemon_command`, which reads `hello`
  first). `--status` / `--expose` / `--xbox` / `--hide` are the same relay from the command line
  (`cli_mode`).
- **The page is role-tabbed:** the header's role tabs switch both panels (that role's real stick on
  the left, its emulated device on the right); the Devices dialog and the dropdown on a tab's
  real-stick name (`openDevMenu`) list `hello.devices` per role; both go through `selectDevice()`.
  In the page, `role` is the current tab, `prof()` its section, `layout` its layout section,
  `srcSplit`/`srcJoin` convert sources, and Learn/Assign watch *both* sticks' raw state. Assigning
  takes the physical control off every slot of *both* devices.
- **The Xbox tab** is a pseudo-role `xbox` (`ROLE_INFO.xbox`, not in `ROLES`). Its left panel is the
  stick role's stick: `home(r)` / `physRole()` give the stick behind a tab, and every "this stick"
  use (`physInfo`, `rawOf`, names, hats, stages, stick map, zoom, image upload, `srcSplit`/`srcJoin`)
  goes through them. `group(role)` keeps the pad's mapping separate: Assign and tags only look at
  `["xbox"]` there and at `ROLES` elsewhere. Cards are keyed by button name (`bitOf` gives the
  `out.b` bit); game defaults don't cover the pad (`refField` returns null). The tab's source name
  opens `openPadMenu` (in `#devMenu`): each configured role's stick (● = feeds the pad) and unused
  connected sticks. `pickPadStick` confirms with `ask(…, okText)` before switching a role away from
  its Thrustmaster device (user's request) or back; an unused stick becomes the throttle role via
  `pickNewPadStick`, which sets the mode *before* selecting so no TWCS appears in between. The
  Devices dialog's Xbox section (`padDevices`) offers the same choices through the same functions.
- The page is one self-contained file (CSP forbids external resources). Physical inputs are amber
  and emulated/game outputs are cyan.
- **Own window (default):** `vkb_mapper_app.py` shows the same page in a GTK4 + WebKitGTK 6.0
  window (`load()` falls back to the browser without the bindings or a display; `--browser` forces
  it, `--no-browser` only prints the URL). The user wanted it to "feel like a real app"; a
  native-widget rewrite was rejected (two UIs to maintain, PySide6 dependency). It's a
  `Gtk.Application` (`APP_ID`, plus `.m<instance_key>` for test instances so they never merge with
  the real one): the server starts in `startup` (first instance only) and stops in `shutdown`, so
  closing the window ends it at once; a second launch raises the first window. If a browser-mode
  server already runs for the same mapping file, the window shows it instead. `decide-policy` sends
  other sites to the browser; downloads (Export) ask for a path via `Gtk.FileDialog`; `<input
  type=file>` gets WebKit's own dialog. Window size goes to `window.json` next to the mapping file.
  The `.desktop` entry's `StartupWMClass` matches `APP_ID`.
- **Browser mode:** the server exits 45 s after its last SSE client disconnects.
- **Single-instance file:** `$XDG_RUNTIME_DIR/vkb-mapper/url-<hash of mapping path + socket>`, used
  by browser mode and by the window to find a running browser-mode server. Keyed so test instances
  never hand over to the user's real mapper (one did, before the key existed).
- **Tray (`vkb-tray.py`, "HOTAS Tray"):** asked for because the user kept stopping the service to
  get the real stick back. An AyatanaAppIndicator3 (GTK 3) icon whose menu has, per configured
  role, radio items virtual / Xbox pad / real (user chose: all three modes, menu on click,
  autostart at login). It keeps one control connection (sends `quiet`), sends `mode` commands,
  follows `hello` broadcasts, shows the service-down state and reconnects every 2 s. While any role
  is real it sets status `NeedsAttention`, which shows `vkb-hotas-attention`; the title (tooltip)
  says what games see. A separate process because the window is GTK 4 and one process can't load
  both; single instance via an flock in `$XDG_RUNTIME_DIR`. Presented as part of HOTAS Mapper
  (user's choice): started by `vkb-mapper --tray` (execs `vkb-tray.py`), the HOTAS Mapper menu
  entry's desktop action "Show tray icon", and `/etc/xdg/autostart/vkb-tray.desktop`
  (`NoDisplay=true`, Exec `vkb-mapper --tray --autostart`; keep that file name, users' per-user
  Hidden overrides match it). `--autostart` is hidden in `--help`; then the tray exits quietly when
  the socket belongs to another user or the bindings are missing. Test it without a mouse over
  D-Bus: find it in `org.kde.StatusNotifierWatcher.RegisteredStatusNotifierItems`, read the menu
  with `com.canonical.dbusmenu.GetLayout`, click with `Event(id, "clicked", …)` (test-mapper-ui).
  libayatana prints a deprecation warning at start: its successor, libayatana-appindicator-glib,
  is AUR-only on Arch/CachyOS, so it stays on the GTK 3 one.
- **Icons:** `mapper/icons/vkb-hotas.svg` (a flight stick; the user found a gamepad icon
  misleading) and `vkb-hotas-attention.svg` (amber "!" badge). Installed into
  `/usr/local/share/icons/hicolor/scalable/apps` and used by both `.desktop` entries, the window
  and the tray (from the repo the tray sets `IconThemePath` to `mapper/icons`). No icon cache is
  created there (only an existing one is refreshed): a new cache would hide icons other programs
  add later. Drawn on a 64 grid with bold shapes for 22 px.
- **`API_VERSION` handshake:** the page is re-read from disk on every request, so after a reinstall
  a still-running old server serves the new page. On a mismatch the page is read-only (`readOnly`:
  no mapping or layout saves), because an old server would drop fields it doesn't know. Bump
  `API_VERSION` (and the page's `l.api !== N` check) whenever the page relies on new server
  behaviour or data. History: 2-5 stick map formats and drawings; 6 roles (mapping v2, layout v3,
  `/api/select`); 7 "Games see"; 8-11 drawing corrections (layout formats 4-7); 12 straight-stick
  drawings; 13-14 game defaults (shipped, then user sets); 15 two-stage triggers; 16 Xbox mode
  (`xbox` section, `/api/mode`); 17 TWCS drawings; 18 `slider_with_throttle`; 19 built-in photos;
  20 hats 2-4 (`hat2:` sources and `hat2`-`hat4` pins; an older server rejects both on save).
- **Two-stage triggers:** physically a full pull always passes through stage 1, so remapping alone
  can't free stage 2's button. `PhysState.active(n, now)` treats a `stages` stage-1 button as
  released while its stage-2 button is held, and (with `delay`) until held `delay` ms; the loop's
  poll timeout follows `PhysState.pending()` so a delayed stage 1 turns on on time. It filters
  the real stick's buttons before mapping, so every output fed from stage 1 (also cross-mapped
  ones) follows it; `raw` in the state stream stays unfiltered. User chose: release while stage 2
  is held, zero delay by default, optional delay. Learn/Assign (`detectLearn`) would otherwise
  always catch stage 1 first, so a new press that is a configured stage 1 sets `learn.held` and
  waits: stage 2 closing assigns stage 2, stage 1 released alone assigns stage 1.

**Stick map.** Layout and photos are owned by the mapper server alone; the daemon never reads them.
They live in `layout.json` + `images/` next to the mapping file (so `--mapping` relocates them for
tests). The layout is kept out of `mapping.json` deliberately: `normalize_mapping` drops unknown
keys, and the layout describes hardware, not a game profile.
- **Format 7:** `{"version": 7, "devices": {"stick": DEVICE, "throttle": DEVICE}}`, one section per
  role's real stick. A DEVICE is `views` (≤8: `{id, name, schematic, image}`), `hats`, `pins` and
  `names`. Older files aren't converted any more (removed 2026-11 with ~130 lines of drawing
  geometry): a file with `devices` (format 3-6) loads as it is, with pins on drawings corrected
  since then possibly off; formats 1/2 are refused and the page starts from defaults. Either way
  GET `/api/layout` first copies the file to `layout.json.bak-v<old>`, because the page will save
  over it.
- **Pins are anchored to the picture**, not the view: keys `draw:<schematic>` or `photo:<file>`
  (`picture_key()` / `picKey()`), so changing a view's drawing shows that drawing's own pins (the
  user's bug report when they were per view). Pin keys are a button number, `"hat"` / `"hat2"`-`"hat4"`
  (a real hat; the key is its source prefix, `isHatPin` / `hatOf` / `rawHat` in the page), or `c:<hat id>`;
  0..1 coordinates. `normalize_layout` drops pins of removed photos and deleted hats, and button
  pins that belong to a hat. Photos are unique across sections; image cleanup uses all of them.
- **Real hats vs 5-way hats:** a stick's *real* hats are hat axes (`hat:`, `hat2:` … sources; the
  physical panel shows a compass per hat, labelled once there's more than one). VKB reports only
  hat 1 that way; its other hats are buttons, which the 5-way hats below group.
- **Hats** (5-way: VKB 4-way + center push, reporting as separate buttons) are defined once per
  stick and placed per picture: Place pins plus any of a hat's buttons places the whole hat. In
  page code they're still called clusters (`clusterOf`, `renderClusters`, `.cluster`). Directions
  are learned by pressing (user's choice over assuming numbering). Hats are visual only: mapping
  stays per button (user's choice). A button belongs to a hat or has its own pin, never both.
- **`names`**: the user's names for physical buttons (keys 1–128, ≤32 chars). They describe
  hardware, so they live in the layout (shared across profiles), not in the per-profile `labels`.
  Shown on card chips, in tooltips and toasts (`describeSrc`).
- Image names are server-generated (`IMAGE_NAME`). Uploads are checked by magic bytes and capped
  at 10 MB, and saving a layout deletes images no view references.
- **Drawings** are inline SVGs in the page (viewBox 400×500):
  - `evo-*`: original line drawings of the Gladiator NXT EVO Omni Throttle, from product photos
    (whole stick with the OTA bracket tilted 38°, SCG grip head, SCG grip side, base front);
    `evo-*-l` are the same mirrored (CSS `scaleX(-1)`), separate picture keys so their pins are
    separate. `renderMap` uses an exact `SCHEM[name]` if one exists, else the base drawing
    mirrored. `evo-ot-straight` (user's request) is built from `evo-ot-side` by string
    replacement, so grip fixes apply to both. The whole-stick and grip-head drawings were mirrored
    and corrected several times on the user's reports; original source was a left-hand grip.
  - `twcs-side` / `twcs-front`: the Thrustmaster TWCS Throttle (user's request), laid out from
    Thrustmaster's manual and the HOTAS wiki. Left-hand only, so no `-l` variants.
  - `grip-front` / `grip-back` / `base`: generic outlines.
- **Built-in photos** (`mapper/pictures/`): the user's own photos of their right and left Omni
  Throttles (MIT, theirs to license; they decided against copying another project's unattributed
  product photos). `pictures.json` lists `{id: {file, label, group, pins, hats}}`; ids are added
  to `SCHEMATICS`, so a photo is chosen like a drawing (`view.schematic = id`, pins under
  `draw:<id>`, never ending in `-l`). Odd naming, kept to avoid another format change. Served by
  `GET /api/picture/<id>` and listed in `GET /api/layout`'s `pictures`; the page lists them first
  in the drawing dropdown (`addPictureOptions`; user's request). Picking one seeds
  `layout.pins["draw:<id>"]` once (only when absent: an empty pin set is dropped by the
  normalizer, so seeding on render would bring deleted pins back) from `pins` plus `hats`
  (`{dirs, at}`, attached to the user's hat with exactly those direction buttons). The default
  pins are the user's own placements, in their VKBDevCfg numbering. Production: photos on a plain
  floor in daylight, cut out with rembg (`isnet-general-use`) in a throwaway venv (onnxruntime
  tried to send telemetry; disable it), largest-piece cleanup, a 9 px opening to drop a cable,
  WebP q88. Colour thresholds and hand-tracing were much worse.
- **Default stick maps** (`default_device_layout(role)`, built per request when there's no
  `layout.json`, and for a missing role section), by `stick_kind(role)` from the daemon's `hello`
  (device id and name, else the role's selector; the config file only when the daemon is down):
  a VKB Gladiator (vendor `231d`, "Gladiator" in the name, or pid `3200`/`3201` from a selector)
  gets the four photos of its hand (left if pid `3201` or the name has "OT L" / ends in " L"),
  with their pins and no hats; another VKB stick gets the EVO drawings (`-l` for the throttle
  role); anything else, or no stick, the generic outlines. **Defaults never reach existing
  layouts**: tell the user how to pick a new picture.
- **Zoom/pan:** a CSS transform on `#canvas` (origin 0 0, clamped so the picture covers its box).
  Pins and clusters counter-scale via `--inv`, so they keep their screen size. Click fractions
  come from the transformed `getBoundingClientRect()`, so placement and dragging need no zoom math.
  A drag of more than 4 px on empty stage pans, and suppresses the click that follows. Zoom state
  is per view, for the session only.

**Game defaults (`mapper/game-defaults.json`).** Per game, what each *emulated* control (T.16000M /
TWCS button numbers, hat directions, axes) does by default, shown read-only on the cards (`GAME …`)
when a profile's optional `reference` names it (kept by `normalize_profile`; the daemon ignores it).
Served by `GET /api/references` (`load_game_defaults`). Keyed by emulated controls, so a reference
stays valid however the user remaps.
- **User sets** (the user asked for creating and sharing): one file per set in
  `game-defaults/<id>.json` next to the mapping file, ids `u-…` (`USER_REF_ID`) so they never
  collide with shipped ids; listed with `origin: "user"`. A file is a single SET
  (`{version, id, name, source, verified, stick, throttle}`), the same format Export downloads and
  Import accepts (Import also takes a shipped-style `{"games": {...}}` file). `PUT
  /api/references/<u-id>` validates with `normalize_reference(strict=True)` (unknown controls are
  an error, not dropped) and writes atomically; `DELETE` removes only `u-` files. Broken files are
  skipped, never fatal.
- **Page:** the Manage menu reuses the `#devMenu` popover. Creating: from the profile's notes
  (labels `N` / `hat:dir` of both roles; the user's choice), empty, or a copy of a shipped set.
  `refEdit` turns GAME lines into inputs (`refField`) for user sets only; edits save debounced
  (`saveRefSoon`). Shipped sets are read-only in the UI.
- **Where AC8's data came from:** not public, and the game's content is encrypted (we don't break
  that). The user checked each emulated button in game (1:1 mapping) and wrote notes on the cards;
  those became the reference. Unrecorded buttons stay "no default" rather than guessed (the user
  chose the in-game check over shipping a positional guess).

**Proton side.** Launch options name both virtual devices:
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687` (Proton matches the list by substring, so the
separator doesn't matter). `tools/proton-setup.py` sets winebus `"Enable SDL"=dword:0` in each prefix's
`system.reg`, so Wine uses its hidraw backend for everything. That passes the T.16000M HID
descriptor through untouched, and ignores SDL gamepad mappings that Steam injects. It also drops
cached `DirectInput\VID_…` entries (both configured sticks, both emulated ids) from `user.reg`. "Set
up" status = that value present (`is_configured`). The checklist is desired-state, by the user's
choice: it starts with set-up games checked, then applies the difference after a confirmation. It
falls back to a numbered list when the terminal lacks cursor addressing (`tigetstr("cup")`): under
`TERM=dumb` curses still starts, and typed numbers were read as checklist keys. The user chose
prefix-only: launch options are printed, never written into Steam's `localconfig.vdf`.

## Gotchas (all hit during development)

- **WebKitGTK draws form controls with the desktop's GTK theme.** On the user's light GTK theme
  every `<select>` in the window was a white box under the page's light text. Fix: `color-scheme:
  dark` on `:root` plus `select{appearance:none}` with our own colours and an inline-SVG arrow (CSP
  allows `data:` images). Check page changes in the real window too: render it with WebKitGTK and
  snapshot it (`WebView.get_snapshot` → `Gdk.Texture.save_to_png`, see the test-mapper-ui skill).
- **uinput not loaded (ENODEV):** `/dev/uinput` here carries an ACL for the user (Steam's udev
  rules), but while the module isn't loaded an open gives ENODEV. The real cause on the user's
  machine: the kernel package had been updated, so `/lib/modules/$(uname -r)` was gone and *no*
  module could load until a reboot. `install.sh`'s `load_module` checks `/sys/module`, `modprobe`
  and `modules.builtin`, and says "reboot" when the running kernel's modules are missing (for uhid
  it then installs anyway). The daemon also tries `modprobe uinput`, and
  `modules-load.d/vkb-hotas.conf` loads both at boot.
- **proton-setup vs Xbox mode:** proton-setup sets winebus `Enable SDL`=0 (needed so the
  Thrustmaster devices go through hidraw), but SDL is what turns the uinput pad into an XInput
  controller in Wine. Pad-only games should keep the default prefix (`proton-setup.py --undo`).
  `vkb-check.py` warns when a log shows xbox mode with SDL disabled.
- **Lost button releases (SYN_DROPPED).** The real stick sends ~1000 reports/s and its evdev
  client buffer holds ~128 events, so a stall of a few tens of ms overflows it. The kernel then
  discards events and sends `SYN_DROPPED`. Ignoring it left buttons held in game (B1, B2, B5).
  `Daemon.feed` skips to the next `SYN_REPORT` and calls `PhysState.resync()` (`active_keys()` +
  `absinfo()`); drops are logged at most once a minute ("kernel event buffer overflowed"). The
  stall was the once-a-second full scan, which as root opens all ~33 input devices; it now runs
  only when `/dev` nodes come or go. Keep slow work out of the event loop.
- **Wine log prefixes are thread IDs, not process IDs.** Games read DirectInput from different
  threads than the one that acquired the device, so `vkb-check.py` keys read counts by device iface.
  DirectInput type `0x10114` ("limited joystick") is expected: the T.16000M has no Z axis.
- **systemd EnvironmentFile doesn't allow inline comments.** `VKB_HOTAS_ARGS="--verbose"  # x`
  passes `#` to the daemon. Keep example lines commented out on their own lines.
- **uhid ABI:** `sizeof(struct uhid_event)` is **4380**. `UHID_INPUT_REPORT` is `rtype == 2`.
  Every `UHID_GET_REPORT` / `UHID_SET_REPORT` must be answered, or kernel readers block.
- **Socket race:** if a client connects, sends and closes immediately, the daemon's `hello` write
  hits EPIPE and the command is dropped. Clients must read the `hello` line first (see
  `daemon_command` in `vkb-mapper.py`).
- **`select`/`mode` order:** the daemon broadcasts the new `hello` *before* the caller's `result`,
  so a client waiting for `result` has already read past it. Ask for a fresh `hello` afterwards.
- **GUI state:** never replace the `mapping` object after a save. Input handlers hold references
  into it, and edits made after an autosave silently vanish.
- **`__pycache__`:** anything root runs from the source tree (e.g. the `install.sh` helpers)
  writes root-owned `__pycache__` there. `install.sh` exports `PYTHONDONTWRITEBYTECODE=1`; keep that.
- **`pkill -f <pattern>`** in a shell whose own command line contains the pattern kills that shell.
  Match on `/proc/<pid>/cmdline` of `pgrep -x python3` instead. Make the match specific to what you
  started (e.g. a private test directory in the arguments), so the user's own processes can never
  match.
- **Backgrounding from bash scripts:** `( cd dir && cmd & )` inside a script left `cmd` as a
  foreground child, and callers piping the script's output hung. Use
  `(cd dir && exec setsid -f cmd < /dev/null > log 2>&1)`.
- **Claude Code's Bash sandbox** hides `/dev` (so `install.sh`, and the `test-installer` sandbox,
  fail at the `/dev/uhid` check), blocks `git push` unless `github.com` is in `allowed_domains`, and
  makes `git status` list fake `/dev/null` placeholders (`.claude/settings.json`, `.mcp.json`, …).
  Check `git status` outside the sandbox and stage files by name. Its `TMPDIR` differs from an
  unsandboxed run's, and the scratch folder is cleared after some days: keep reusable test
  scripts in the skills, not there.
- **`rm -rf` on a computed path** (e.g. `$(mktemp -d)`) is blocked by a safety check in Claude Code.
  Use literal scratch paths for anything you need to delete afterwards.
- **Steam holds the device open.** Steam opens the real stick at startup, and hiding doesn't revoke
  an existing fd. After install, Steam must be restarted.
- **At most two sticks** (one per role). If the user owns a real T.16000M or TWCS too, games see two
  identical devices; `vkb-check.py` warns about this.
- **TWCS descriptor is approximate.** Its controls are documented (Z throttle, X/Y mini-stick, Rz
  rocker, Slider antenna, Rx/Ry toe brakes, 14 buttons, hat) but its exact report descriptor wasn't
  found; logical ranges are this project's choice.
- **Testing curses pickers through a pty:** in keypad mode xterm sends arrows as `ESC O B`, not
  `ESC [ B`; send the wrong one and the lone ESC reads as "cancel".
- **Python 3.11 compatibility:** don't reuse quotes inside f-string expressions (Debian 12 has
  3.11); the user's machine runs 3.14, so that bug wouldn't show locally.
- **Every Proton game needs `proton-setup.py <appid>`.** The service is game-agnostic, but a prefix
  left on Wine's SDL backend can expose the stick as a gamepad. Project Wingman reads joysticks
  through its bundled `SDL2.dll` and the UE4 JoystickPlugin (`IgnoreGameControllers`); it saw no
  stick input until its prefix was set up. When a new game "sees nothing", check the prefix's
  winebus `Enable SDL` value first (`vkb-check.py` prints it), then the log.
- **AC8 controller icons:** AC8 shows controller button icons even when DirectInput is working.
  That alone isn't a failure.
- **Large Proton logs:** a `+hid,+dinput` log can pass 1 GB; `vkb-check.py` streams it, but grep
  the log directly when you need one detail.
- **Layout/format changes ripple into the user's data.** Bump `API_VERSION` whenever the page
  relies on new server behaviour; keep a format change's old files loading (or backed up); and
  remember defaults never reach existing layouts.

## Domain knowledge this work requires

- **Linux input stack:** evdev, EVIOCGRAB, absinfo, joystick button code ranges, uinput; `/dev/uhid`
  and HID report descriptors (`with_buttons` patches Usage Max and Report Count in place).
- **udev and logind:** rule ordering, `uaccess`, `TAG-=`, `ATTRS{}`/`ENV{ID_PATH}` matching, ACL
  masks, `SYSTEMD_WANTS`; systemd units (`StartLimit*` lives in `[Unit]`).
- **Thrustmaster devices:** T.16000M and TWCS Throttle controls, ids and how games' HOTAS presets
  recognise them; the xpad Xbox 360 layout.
- **Wine/Proton input:** winebus backends (SDL / hidraw / evdev) and registry options,
  `PROTON_ENABLE_HIDRAW`, DirectInput device types, Wine `+hid,+dinput` trace format, Wine `.reg` file
  format.
- **Steam layout:** library folders (native, Flatpak and Snap roots), `compatdata/<appid>/pfx`,
  launch options, Steam Input.
- **GTK/WebKitGTK via PyGObject** (GTK 4 window, GTK 3 tray), StatusNotifierItem/dbusmenu,
  freedesktop `.desktop`/autostart/icon theme rules.
- **Local web security:** token auth, Host checks, CSP; SSE.
