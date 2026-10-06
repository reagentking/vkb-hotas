# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

vkb-hotas makes unsupported flight sticks (built for a VKB Gladiator NXT EVO Omni Throttle R,
`231d:3200`, but any evdev joystick works) usable in Proton games that only accept HOTAS models they
recognise (Ace Combat 8, Star Wars: Squadrons). A root daemon grabs the real sticks, re-creates them
via `/dev/uhid` as Thrustmaster devices, and hides the real ones from the user session. Two
**roles**: *stick* → **T.16000M (`044f:b10a`)**, required; *throttle* → **TWCS Throttle
(`044f:b687`)**, optional (the user wants a left- and a right-hand Omni Throttle). Together they're
Thrustmaster's T.16000M FCS HOTAS set. A browser-based GUI (HOTAS Mapper) remaps buttons and axes
live, because AC8 has no rebinding for this stick.

**Goals:**
1. Games see only the Thrustmaster devices, recognised as joysticks (not gamepads).
2. Remapping without sudo and without restarting games.
3. Shareable: no machine-specific assumptions; standard library plus python-evdev only.

**Current status:** in Squadrons the game acquires and polls the stick. AC8 acquires and polls it,
but shows controller button icons, and in-flight behaviour hasn't been confirmed yet. If AC8
treats the T.16000M badly, the planned fallback is emulating a Logitech X56 instead.
Project Wingman (appid 895870) opens the stick and receives input once its prefix is set up with
`proton-setup.py 895870`; controls are bound in the game's own menu. The throttle role has only been
tested against simulated sticks: the user doesn't own the left-hand OT yet, and its product id
(`231d:3201` in the fake daemon) is a guess.

## Repository layout

| Path | Contents |
|---|---|
| `service/` | root daemon `vkb-hotas.py`, systemd unit, config template, udev rule |
| `mapper/` | HOTAS Mapper server `vkb-mapper.py`, page `vkb-mapper.html`, `.desktop` entry |
| `common/` | `vkb_common.py`, shared by service, mapper and tools |
| `tools/` | user-run helpers: `proton-setup.py`, `vkb-check.py`, `vkb-learn.py` |
| `dev/` | `fake-daemon.py` (GUI testing without root) |
| top level | `install.sh`, `uninstall.sh`, docs |

- **The install is flat:** `install.sh` copies the service, mapper and common files into
  `/usr/local/lib/vkb-hotas/` with no subdirectories. The systemd unit's `ExecStart` and the
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

# Roles at runtime (as the user, over the control socket; no sudo)
vkb-mapper --status | --expose stick|throttle|all | --hide stick|throttle|all

# Verify the whole chain: service, hiding, virtual device, SDL view, control socket, mapping file,
# plus analysis of the newest ~/steam-*.log
tools/vkb-check.py            # or: tools/vkb-check.py ~/steam-<appid>.log
journalctl -u vkb-hotas -b

# Proton prefix setup (as user, games closed). No ids on a terminal: curses checklist of all
# prefixes (checked = set up; unchecking restores). No ids without a terminal: 1222730, 2288340
tools/proton-setup.py [APPID...] | --list | --undo [APPID...]
```

A game launch that produces an analysable log uses these launch options:
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%`.

### Working with the user's installed system

- **sudo is the human's job.** `sudo ./install.sh` and other privileged steps need a password, so the
  user runs them in a real terminal; an inline `!` command box can't take one. Give them the exact
  command, then verify afterwards (the `verify-install` skill).
- **Restart the mapper after a reinstall.** The user must close HOTAS Mapper and reopen it once the
  old server has exited (about 45 s). The `API_VERSION` check guards the gap.
- **Editing the user's live files** (`~/.config/vkb-hotas/*.json`):
  - only with their go-ahead, and make a timestamped backup first;
  - check that HOTAS Mapper isn't running;
  - validate with the same normalizer the server uses.
  - If you write a new format while an older mapper is still installed, an old server could drop
    fields on save. Tell the user to reinstall before opening the mapper.

### Project skills (`.claude/skills/`)

| Skill | Use it for |
|---|---|
| `verify-install` | "I ran the install, check if it's working": installed vs repo, stale mapper, health checks, read-only validation of the user's files |
| `test-mapper-ui` | Testing HOTAS Mapper in the browser against `dev/fake-daemon.py`, isolated from the user's own mapper |
| `ship-change` | "Update docs, commit and push": docs-vs-diff, `API_VERSION` rule, prechecks, commit, push |
| `test-installer` | `install.sh` / `uninstall.sh` changed or files moved: run both in a sandbox root without sudo, and check every installed file against its source |
| `proton-log-analysis` | Whether a game accepts the stick: launch options, `vkb-check.py` log analysis, Wine trace pitfalls |

### Testing without root

**GUI, control protocol and the whole event loop:** `dev/fake-daemon.py` runs the daemon's real
`Daemon` class (imported from `service/vkb-hotas.py`) with a `FakeSource` of simulated Omni Throttles
(right `231d:3200`, left `231d:3201`), a `FakeUhid` and no hider. Only uhid, grabbing, udev and the
real config file are swapped out (selection/expose persist to `DIR/config`), so device selection,
swapping, exposing, cross-mapping and replugging are all testable without root.

```bash
dev/fake-daemon.py [--button-count 32] [--still] [--sticks 1|2] [--twin] [--throttle 231d:3201]
VKB_HOTAS_CTL=$XDG_RUNTIME_DIR/vkb-hotas-dev/ctl.sock mapper/vkb-mapper.py --mapping $XDG_RUNTIME_DIR/vkb-hotas-dev/mapping.json
dev/fake-daemon.py --press 17 [--stick 1]   # simulate a press (or hat:left etc.) on simulated stick N
dev/fake-daemon.py --unplug 1 / --plug 1    # simulate unplugging / replugging stick N
```

- **Extra commands:** `fake_press` and `fake_plug` on the socket; the real daemon doesn't accept them.
- `--twin` gives both sticks the same VID:PID, to test `@port` selectors.
- **Socket path limit:** unix socket paths max out at 107 bytes, so keep `--dir` short.

**Mapper logic in isolation:** load the hyphenated daemon file with importlib. A `Mapper` is one
role's output; it reads every role's `PhysState`, which is how cross-mapping works. `FakeStick` in
`dev/fake-daemon.py` is a ready-made fake device.

```python
import importlib.util
sys.path.insert(0, "common"); s = importlib.util.spec_from_file_location("v", "service/vkb-hotas.py"); v = importlib.util.module_from_spec(s); s.loader.exec_module(v)
m = v.Mapper("stick"); m.apply(vkb_common.normalize_profile({...})["stick"])
ps = v.PhysState(FakeStick(0, 0x3200, "R")); ps.feed(evdev.InputEvent(0, 0, E.EV_KEY, 0x2c0, 1))
m.output(time.monotonic(), {"stick": ps, "throttle": None})   # -> (mask, hat, axes)
```

**Socket tests from Claude Code** need `dangerouslyDisableSandbox`: the Bash sandbox blocks unix
sockets and `$XDG_RUNTIME_DIR`. The `test-mapper-ui` scripts already assume that.

**Root-only paths** (uhid, hiding, udev) can only be tested on the installed service. Live checks:
- send `{"cmd":"pulse","role":"stick","button":N}` to `/run/vkb-hotas/ctl.sock` (read the `hello`
  line first) and watch `EV_KEY` on the virtual evdev node;
- write `~/.config/vkb-hotas/mapping.json` and watch the profile in the socket's `hello`. Delete
  the file afterwards to restore the built-in default.

When killing test processes, select them via `/proc/<pid>/cmdline` (see Gotchas), not `pkill -f`.

## Architecture

```
real stick ─────┐                                     ┌─▶ virtual T.16000M      ─┐
                ├─evdev (EVIOCGRAB)─▶ vkb-hotas.py ───┤                           ├─(hidraw)─▶ Wine winebus ─▶ DirectInput ─▶ game
throttle stick ─┘                       (root)        └─▶ virtual TWCS Throttle ─┘
                                   ▲            │
          ~/.config/vkb-hotas/mapping.json      └─ /run/vkb-hotas/ctl.sock (JSON lines) ◀─▶ vkb-mapper.py (user, 127.0.0.1 HTTP+SSE) ◀─▶ vkb-mapper.html
```

**Daemon (`service/vkb-hotas.py`).** `main()` only parses arguments and builds a **`Daemon`**, whose
single-threaded `poll()` loop serves both roles. It takes its collaborators as arguments
(`source`, `make_uhid`, `hider`, `persist`), which is how `dev/fake-daemon.py` reuses the same loop.
The poll set is rebuilt every iteration, because devices come and go: sticks attach/detach, and a
virtual device is destroyed and re-created when its `button_count` changes the HID descriptor.

- **`Role`** (one per role) holds the selector, `exposed`, the attached evdev device and its
  `PhysState`, its hidden nodes, its `Mapper` and its `Uhid`.
- **`PhysState`**: one real stick's raw axes, pressed button numbers and hat.
- **`Mapper`**: one role's output. Computes that emulated device's report from the role's profile
  section and *every* role's `PhysState` (cross-mapping). `make_spec(role, n)` gives the HID
  descriptor and packer: T.16000M 9/11 bytes; TWCS 17/19 bytes (buttons padded to a byte, hat, 7 u16
  axes X Y Z Rx Ry Rz Slider). The T.16000M descriptor is byte-identical to the pre-roles version.
- **Scanning:** every second, `scan()` lists joysticks (`list_sticks`: EV_ABS plus joystick/gamepad
  buttons, no touchpads, not our own `phys == vkb-hotas/uhid`), attaches configured sticks that
  appeared and publishes the list in `hello.devices`. An unplugged stick is detected by
  POLLHUP/ENODEV and detached, but its **virtual device stays** (neutral), so running games keep it;
  the stick re-attaches when it's back. The daemon only exits at start, if no configured stick shows
  up within `--wait`.
- **Selectors:** `VID:PID`, or `VID:PID@<port>` (the evdev `phys` minus `/inputN`) when two
  connected sticks share a VID:PID; `selector_for()` adds the port only then. A vague legacy
  selector (`""` = any VKB, vendor only) never takes a stick another role names exactly.
- **Commands** (control socket): `hello`; `pulse {role, button}`; `select {role, device|null}`
  (validated against connected sticks; picking the other role's stick swaps them; the stick role
  can't be none); `expose {role|"all", on}`. `select`/`expose` reply `{"t":"result","ok","error"}`
  to the caller only, *after* broadcasting the new `hello`, and persist to `/etc/default/vkb-hotas`
  through `write_config` (atomic, keeps other lines, refuses odd characters).
- **Exposing** a role: ungrab, unhide, destroy its virtual device; its events are still read (live
  view, cross-mapped sources). Persisted as `VKB_HOTAS_EXPOSE`.
- **Messages:** `hello {roles{r: selector, exposed, device|null, emulated{name,id,buttons,axes,
  active}}, devices[], profile, mapping, error}`; `state {roles{r: raw|null, out}}` (≤30 Hz, only
  when something changed; `out` is computed even while exposed); `mapping`; `result`.
- **`MappingWatcher`** polls `mapping.json` every 0.5 s (inode, mtime, size). As root it refuses
  symlinks, non-regular files and files not owned by `VKB_HOTAS_USER`. When the file is missing, it
  falls back to a profile built from the CLI args in `VKB_HOTAS_ARGS` (stick section only).

**User decisions behind the roles** (asked, not guessed):
- Two Omni Throttles become a T.16000M + TWCS (not two T.16000Ms, not one merged device).
- Sticks are chosen in the installer *and* live in HOTAS Mapper's Devices panel; the root daemon
  accepting a "select" from the socket owner was accepted as the cost.
- Cross-mapping is allowed: any source on either stick can feed either device.
- Exposing removes the virtual device and persists across restarts. It started CLI only
  (`vkb-mapper --expose/--hide`); the user later asked for it in the GUI too: a VIRTUAL/REAL badge
  per tab and a "Games see: Virtual | Real stick" switch (stick panel and Devices dialog).

**Shared model (`common/vkb_common.py`).** Owns config reading/writing (`/etc/default/vkb-hotas`:
`VKB_HOTAS_DEVICE`, `VKB_HOTAS_THROTTLE`, `VKB_HOTAS_EXPOSE`, `VKB_HOTAS_USER`, `VKB_HOTAS_ARGS`),
roles and their emulated ids, axes and button counts, selectors and stick discovery, the mapping
schema, and the terminal pickers (`checklist`, `pick_one`, used by `proton-setup.py` and
`install.sh`). `normalize_profile` / `normalize_mapping` are the single validator used by the
daemon, the GUI server and the checker. Change the schema there and nowhere else.

- **Mapping version 2:** a profile is `{stick: SECTION, throttle: SECTION}`. A section is
  `button_count` (stick 16|32, throttle 14|32); `axes{...: {src, invert, deadzone%}}` (stick X Y RZ
  SLIDER; throttle X Y Z RX RY RZ SLIDER); `buttons{"N": [sources]}`; `hat{up,down,left,right:
  [sources]}`; `labels`. Version 1 (one stick profile at the top level) becomes the stick section.
- **Sources** are relative to the section's role: a physical button number (1-based) or
  `"hat:<dir>"` on the role's own stick, or the same prefixed with the other role (`"throttle:5"`,
  `"stick:hat:up"`). Axis sources likewise (`"ABS_Z"`, `"throttle:ABS_Z"`). The normalizer strips a
  prefix naming the section's own role and drops duplicates. `split_source()` resolves them.

**Physical button numbering** is the index into the sorted list of evdev `EV_KEY` codes (+1). That
matches HID / VKBDevCfg numbering (Linux maps HID buttons 1–16 to `0x120–0x12f`, then `0x2c0+`).
Keep every tool consistent with this.

**Hiding a real stick takes two mechanisms; both are needed.**
- The `Hider` writes `/run/udev/rules.d/72-vkb-hotas-hide.rules` with one
  `KERNEL=="eventN|jsN|hidrawN", TAG-="uaccess", MODE="0600"` line per node of the sticks it
  replaces, reloads udev and retriggers them, so logind won't re-add the ACL on login or session
  switch. It's a runtime file (not generated at install) so that a stick chosen later in HOTAS
  Mapper is hidden too. It sorts between `70-uaccess` and `73-seat-late` like the installed rule.
- The daemon also `chmod 0600`s the nodes. With POSIX ACLs that zeroes the mask, which neutralises
  the ACL that is already applied.
- Detaching, exposing or exiting rewrites/removes the file and retriggers. Kernel names get reused
  after an unplug, so a stale file must never survive: `--cleanup` (run at startup and as
  `ExecStopPost`, i.e. even after a crash) deletes it, plus the pre-roles flag
  `/run/vkb-hotas/active`, and retriggers input/hidraw.
- The installed `service/72-vkb-hotas.rules` is now static: it starts the service on any joystick
  `add` (`ENV{ID_INPUT_JOYSTICK}=="1"`; the daemon exits if no configured stick is there) and
  grants `uaccess` on both virtual devices' hidraw nodes.

**GUI (`mapper/vkb-mapper.py` + `.html`).** The server is stdlib `ThreadingHTTPServer`. It enforces three
guards, and keeps them:
- binds `127.0.0.1` only,
- requires a per-session token (`X-Token` header or `?t=`),
- checks the `Host` header (anti-DNS-rebinding).

- `/api/events` is SSE relaying the daemon socket. It coalesces state lines, and reconnects when
  the daemon restarts.
- `PUT /api/mapping` validates via `vkb_common` and writes atomically.
- `GET|PUT /api/layout`, `POST /api/image?view=ID&role=R` (raw body), and `GET /api/image/<name>`
  (token in the query, so `<img>` works) back the stick map.
- `POST /api/pulse {role, button}`, `POST /api/select {role, device}` and
  `POST /api/expose {role, on}` relay to the daemon
  (`daemon_command`, which reads `hello` first). `--status` / `--expose` / `--hide` are the same
  relay from the command line.
- **The page is role-tabbed:** the header's role tabs switch both panels (that role's real stick on
  the left, its emulated device on the right); the Devices dialog and the dropdown on a tab's real-stick name (`openDevMenu`) list
  `hello.devices` per role; both go through `selectDevice()`.
  In the page, `role` is the current tab, `prof()` its section, `layout` its layout section,
  `srcSplit`/`srcJoin` convert sources, and Learn/Assign watch *both* sticks' raw state. Assigning
  takes the physical control off every slot of *both* devices.
- The server exits 45 s after its last SSE client disconnects.
- **Single-instance file:** `$XDG_RUNTIME_DIR/vkb-mapper/url-<hash of mapping path + socket>`. It's
  keyed so test instances never hand over to the user's real mapper. A test instance once did
  exactly that before the key existed.
- **`API_VERSION` handshake:** the page is re-read from disk on every request, so after a reinstall
  a still-running old server serves the new page. On a mismatch the page disables the stick map,
  because an old server would drop fields it doesn't know when saving. Bump `API_VERSION` (and the
  page's check) whenever the page relies on new server behaviour. History: 2 = clusters and `evo-*`
  drawings; 3 = `evo-scg-side` (an older server would silently swap unknown drawings on save);
  4 = physical button `names`; 5 = layout format 2 (picture-anchored pins, global `hats`);
  6 = roles (mapping v2 with stick/throttle sections and cross sources, layout v3 per role, `-l`
  drawings, `/api/select`). Since 6 a mismatch makes the page read-only (`readOnly`: no mapping or
  layout saves at all), not just the stick map, because an old server would drop the throttle data.
  7 = `/api/expose` (the "Games see" switch).
- The page is one self-contained file (CSP forbids external resources). Physical inputs are amber
  and emulated/game outputs are cyan.
- **Stick map:** layout and photos are owned by the mapper server alone; the daemon never reads them.
  They live in `layout.json` + `images/` next to the mapping file (so `--mapping` relocates them for
  tests).
  - The layout is kept out of `mapping.json` deliberately: `normalize_mapping` drops unknown keys,
    and the layout describes hardware, not a game profile.
  - **Format version 3:** `{"version": 3, "devices": {"stick": DEVICE, "throttle": DEVICE}}`, one
    section per role's real stick. A version 1/2 file becomes the stick's section
    (`normalize_device_layout(lay, v1=...)`; sections inside v3 must never be treated as v1, which
    once wiped pins on a second pass). Photos are unique across sections, and image cleanup uses
    all of them.
  - **A DEVICE section** (the old format 2): `views`, `hats` (global to that stick), `pins` keyed
    by **picture**, and `names`.
    - A picture key is `draw:<schematic>` or `photo:<file>` (`picture_key()` on the server,
      `picKey()` in the page).
    - Pin keys are a button number, `"hat"`, or `c:<hat id>` for a 5-way hat's position; 0..1
      coordinates.
    - Pins used to be per view, and changing a view's drawing left them stranded on the wrong
      picture (the user's bug report). Anchoring them to the picture fixes it.
    - Version 1 (pins and `clusters` per view id) is converted by `_migrate_v1`, which attaches
      each view's pins to that view's current picture.
    - `normalize_layout` also drops pins of removed photos and deleted hats, and button pins that
      belong to a hat.
  - `hats` are multi-way hats that report as separate buttons (VKB 4-way + center push). They're
    defined once and placed per picture: Place pins plus any of a hat's buttons places the whole
    hat. (In page code they're still called clusters: `clusterOf`, `renderClusters`, `.cluster`.)
    - Directions are learned by pressing. The user chose this over assuming numbering, which
      varies by firmware.
    - Hats are visual only. The user chose to keep mapping per-button, with no "send hat to game
      hat" shortcut.
  - A button belongs to a hat or has its own pin, never both.
  - `names` holds the user's names for physical buttons (keys 1–128, at most 32 characters).
    - They describe hardware, so they live in the layout (shared across profiles), not in the
      per-profile `labels`, which say what a button does in-game.
    - The user chose this over a second per-card note.
    - They're shown on card chips (a second line), with a 5-way hat badge from `clusterOf`, and in
      tile and pin tooltips and toasts (`describeSrc`).
  - Image names are server-generated (`IMAGE_NAME` regex). Uploads are checked by magic bytes and
    capped at 10 MB, and saving a layout deletes images no view references.
  - **Drawings:** the drawn schematics are inline SVGs in the page (viewBox 400×500).
    - `evo-*` are original line drawings of the Gladiator NXT EVO Omni Throttle, made from product
      photos: whole stick with the OTA bracket and grip tilted 38°, SCG grip head, SCG grip side
      (rapid-fire paddle above the dual-stage trigger, mini-stick), base front.
    - `evo-*-l` are the same drawings mirrored (CSS `scaleX(-1)`), the throttle role's defaults
      for a left-hand OT. They are separate picture keys, so their pins are separate too.
    - `grip-front` / `grip-back` / `base` are generic outlines for other sticks.
    - Pins are always placed by the user, never pre-filled, because button numbering differs per
      grip and firmware.
  - **Zoom/pan:** a CSS transform on `#canvas` (origin 0 0, clamped so the picture covers its
    box). Pins and clusters counter-scale via `--inv`, so they keep their screen size.
    - Click fractions come from the transformed `getBoundingClientRect()`, so placement and
      dragging need no zoom math.
    - A drag of more than 4 px on empty stage pans, and the click that follows it is suppressed.
    - Zoom state is per view, for the session only.
  - **Defaults only apply to new layouts:** `DEFAULT_LAYOUT` is used only when no `layout.json`
    exists. Existing layouts keep their views and drawings.

**Proton side.** Launch options name both virtual devices:
`PROTON_ENABLE_HIDRAW=0x044F/0xB10A,0x044F/0xB687` (Proton matches the list by substring, so the
separator doesn't matter). `tools/proton-setup.py` sets winebus `"Enable SDL"=dword:0` in each prefix's
`system.reg`, so Wine uses its hidraw backend for everything. That passes the T.16000M HID
descriptor through untouched, and ignores SDL gamepad mappings that Steam injects. It also drops
cached `DirectInput\VID_…` entries (both configured sticks, both emulated ids) from `user.reg`. "Set up" status = that value present
(`is_configured`). The checklist is desired-state, by the user's choice: it starts with set-up games
checked, then applies the difference after a confirmation. It falls back to a numbered list when the
terminal lacks cursor addressing (`tigetstr("cup")`): under `TERM=dumb` curses still starts, and
typed numbers were read as checklist keys. The user chose prefix-only: launch options are printed,
never written into Steam's `localconfig.vdf`.

## Gotchas (all hit during development)

- **Wine log prefixes are thread IDs, not process IDs.** Games read DirectInput from different
  threads than the one that acquired the device, so `vkb-check.py` keys read counts by device iface.
  DirectInput type `0x10114` ("limited joystick") is expected: the T.16000M has no Z axis.
- **systemd EnvironmentFile doesn't allow inline comments.** `VKB_HOTAS_ARGS="--button-count 32"  # x`
  passes `32#` to the daemon. Keep example lines commented out on their own lines.
- **uhid ABI:** `sizeof(struct uhid_event)` is **4380**. `UHID_INPUT_REPORT` is `rtype == 2`.
  Every `UHID_GET_REPORT` / `UHID_SET_REPORT` must be answered, or kernel readers block.
- **Socket race:** if a client connects, sends and closes immediately, the daemon's `hello` write
  hits EPIPE and the command is dropped. Clients must read the `hello` line first (see
  `do_POST` in `vkb-mapper.py`).
- **GUI state:** never replace the `mapping` object after a save. Input handlers hold references
  into it, and edits made after an autosave silently vanish.
- **`__pycache__`:** anything root runs from the source tree (e.g. the `install.sh` autodetect)
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
  Check `git status` outside the sandbox and stage files by name.
- **`rm -rf` on a computed path** (e.g. `$(mktemp -d)`) is blocked by a safety check in Claude Code.
  Use literal scratch paths for anything you need to delete afterwards.
- **Steam holds the device open.** Steam opens the real stick at startup, and hiding doesn't revoke
  an existing fd. After install, Steam must be restarted.
- **At most two sticks** (one per role). If the user owns a real T.16000M or TWCS too, games see two
  identical devices; `vkb-check.py` warns about this.
- **`select`/`expose` order:** the daemon broadcasts the new `hello` *before* the caller's `result`,
  so a client waiting for `result` has already read past it. Ask for a fresh `hello` afterwards.
- **TWCS descriptor is approximate.** Its controls are documented (Z throttle, X/Y mini-stick, Rz
  rocker, Slider antenna, Rx/Ry toe brakes, 14 buttons, hat) but its exact report descriptor wasn't
  found; logical ranges are this project's choice.
- **Testing curses pickers through a pty:** in keypad mode xterm sends arrows as `ESC O B`, not
  `ESC [ B`; send the wrong one and the lone ESC reads as "cancel".
- **Python 3.11 compatibility:** don't reuse quotes inside f-string expressions (Debian 12 has
  3.11); the user's machine runs 3.14, so that bug wouldn't show locally.
- **Every Proton game needs `proton-setup.py <appid>`.** The service is game-agnostic, but a prefix
  left on Wine's SDL backend can expose the stick as a gamepad. Project Wingman reads joysticks
  through its bundled `SDL2.dll` and the UE4 JoystickPlugin, which has `IgnoreGameControllers`;
  the user saw no stick input before its prefix was configured, and input arrived after (no log
  exists from before, so the gamepad path is the likely cause, not a proven one). When a new game "sees nothing",
  check the prefix's winebus `Enable SDL` value first (`vkb-check.py` prints it), then the log.
- **AC8 controller icons:** AC8 shows controller button icons even when DirectInput is working.
  That alone isn't a failure.
- **Layout/format changes ripple into the user's data.**
  - Bump `API_VERSION` whenever the page relies on new server behaviour.
  - Make `normalize_layout` convert older formats, rather than reject them.
  - Defaults (`DEFAULT_LAYOUT`) never reach existing users. Tell them how to get a new view or
    drawing.

## Domain knowledge this work requires

- **Linux input stack:** evdev, EVIOCGRAB, absinfo, joystick button code ranges; `/dev/uhid` and HID
  report descriptors (`with_buttons` patches Usage Max and Report Count in place).
- **udev and logind:** rule ordering, `uaccess`, `TEST==`, `TAG-=`, ACL masks, `SYSTEMD_WANTS`; systemd
  units (`StartLimit*` lives in `[Unit]`).
- **Thrustmaster devices:** T.16000M and TWCS Throttle controls, ids and how games' HOTAS presets
  recognise them.
- **Wine/Proton input:** winebus backends (SDL / hidraw / evdev) and registry options,
  `PROTON_ENABLE_HIDRAW`, DirectInput device types, Wine `+hid,+dinput` trace format, Wine `.reg` file
  format.
- **Steam layout:** library folders (native, Flatpak and Snap roots), `compatdata/<appid>/pfx`,
  launch options, Steam Input.
- **Local web security:** token auth, Host checks, CSP; SSE.
