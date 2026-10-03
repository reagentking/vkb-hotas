# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

vkb-hotas makes an unsupported flight stick (built for a VKB Gladiator NXT EVO Omni Throttle R,
`231d:3200`, but any evdev joystick works) usable in Proton games that only accept HOTAS models they
recognise (Ace Combat 8, Star Wars: Squadrons). A root daemon grabs the real stick, re-creates it via
`/dev/uhid` as a **Thrustmaster T.16000M (`044f:b10a`)**, and hides the real device from the user
session. A browser-based GUI (HOTAS Mapper) remaps buttons and axes live, because AC8 has no
rebinding for this stick.

**Goals:**
1. Games see exactly one stick, which they recognise as a T.16000M joystick (not a gamepad).
2. Remapping without sudo and without restarting games.
3. Shareable: no machine-specific assumptions; standard library plus python-evdev only.

**Current status:** in Squadrons the game acquires and polls the stick. AC8 acquires and polls it,
but shows controller button icons, and in-flight behaviour hasn't been confirmed yet. If AC8
treats the T.16000M badly, the planned fallback is emulating a Logitech X56 instead.

## Repository layout

| Path | Contents |
|---|---|
| `service/` | root daemon `vkb-hotas.py`, systemd unit, config template, udev rule template |
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
t=$(mktemp --suffix=.rules); sed -e s/@VID@/231d/g -e s/@PID@/3200/g service/72-vkb-hotas.rules.in > $t; udevadm verify $t; rm $t
systemd-analyze verify service/vkb-hotas.service

# Install / reinstall after any change (copies to /usr/local/lib/vkb-hotas, restarts the service)
sudo ./install.sh [--device VID:PID] [--user NAME]     # --list shows connected sticks
sudo ./uninstall.sh

# Verify the whole chain: service, hiding, virtual device, SDL view, control socket, mapping file,
# plus analysis of the newest ~/steam-*.log
tools/vkb-check.py            # or: tools/vkb-check.py ~/steam-<appid>.log
journalctl -u vkb-hotas -b

# Proton prefix setup (as user, games closed). Default app ids: 1222730 (Squadrons), 2288340 (AC8)
tools/proton-setup.py [APPID...] | --list | --undo
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

**GUI and control protocol:** `dev/fake-daemon.py` runs the daemon's real `Mapper`, `ControlServer`
and `MappingWatcher` (imported from `service/vkb-hotas.py`) against a simulated Gladiator EVO OT R. It has no
uhid, grab or udev, so it needs no root and no stick.

```bash
dev/fake-daemon.py [--button-count 32] [--still]          # socket + mapping in $XDG_RUNTIME_DIR/vkb-hotas-dev/
VKB_HOTAS_CTL=$XDG_RUNTIME_DIR/vkb-hotas-dev/ctl.sock mapper/vkb-mapper.py --mapping $XDG_RUNTIME_DIR/vkb-hotas-dev/mapping.json
dev/fake-daemon.py --press 17          # simulate physical button 17 (or hat:left etc.) being pressed
```

- **Extra command:** the fake daemon also accepts `{"cmd":"fake_press","source":...}`; the real
  daemon doesn't.
- **Keep it in step:** its main-loop message handling (hello / mapping / state / pulse) is a copy of
  `main()` in `service/vkb-hotas.py`. Update both together.
- **Socket path limit:** unix socket paths max out at 107 bytes, so keep `--dir` short.

**Mapper logic in isolation:** load the hyphenated daemon file with importlib and feed it events.
`FakeStick` in `dev/fake-daemon.py` is a ready-made fake device.

```python
import importlib.util
sys.path.insert(0, "common"); s = importlib.util.spec_from_file_location("v", "service/vkb-hotas.py"); v = importlib.util.module_from_spec(s); s.loader.exec_module(v)
m = v.Mapper(FakeStick(), v.T16000M); m.apply(vkb_common.normalize_profile({...}))
m.feed(evdev.InputEvent(0, 0, E.EV_KEY, 0x2c0, 1)); m.output(time.monotonic())  # -> (mask, hat, axes)
```

**Root-only paths** (uhid, hiding, udev) can only be tested on the installed service. Live checks:
- send `{"cmd":"pulse","button":N}` to `/run/vkb-hotas/ctl.sock` (read the `hello` line first) and
  watch `EV_KEY` on the virtual evdev node;
- write `~/.config/vkb-hotas/mapping.json` and watch the profile in the socket's `hello`. Delete
  the file afterwards to restore the built-in default.

When killing test processes, select them via `/proc/<pid>/cmdline` (see Gotchas), not `pkill -f`.

## Architecture

```
real stick ─evdev (EVIOCGRAB)─▶ vkb-hotas.py (root) ─/dev/uhid─▶ virtual T.16000M (hidraw+evdev) ─▶ Wine winebus (hidraw) ─▶ DirectInput ─▶ game
                                   ▲            │
          ~/.config/vkb-hotas/mapping.json      └─ /run/vkb-hotas/ctl.sock (JSON lines) ◀─▶ vkb-mapper.py (user, 127.0.0.1 HTTP+SSE) ◀─▶ vkb-mapper.html
```

**Daemon (`service/vkb-hotas.py`).** A single-threaded `poll()` loop over the evdev fd, the uhid fd and the
control-socket fds. The poll set is rebuilt every iteration, because the uhid device is destroyed
and re-created whenever `button_count` changes (16 ↔ 32 changes the HID descriptor).

- **`Mapper`** holds the physical state (raw axes, pressed button numbers, hat) and computes the
  9- or 11-byte T.16000M report from the active profile.
- **`MappingWatcher`** polls `mapping.json` every 0.5 s (inode, mtime, size). As root it refuses
  symlinks, non-regular files and files not owned by `VKB_HOTAS_USER`. When the file is missing, it
  falls back to a profile built from the CLI args in `VKB_HOTAS_ARGS`.
- **`ControlServer`** pushes `hello` / `state` (≤30 Hz, only when something changed) / `mapping`
  messages, and accepts `{"cmd":"pulse","button":N}` and `{"cmd":"hello"}`.

**Shared model (`common/vkb_common.py`).** Owns config parsing (`/etc/default/vkb-hotas`), device matching
(`VKB_HOTAS_DEVICE` as VID[:PID], defaulting to any VKB vendor `231d`), and the mapping schema.
`normalize_profile` / `normalize_mapping` are the single validator used by the daemon, the GUI
server and the checker. Change the schema there and nowhere else.

- **Profile schema:** `button_count` (16|32); `axes{X,Y,RZ,SLIDER: {src, invert, deadzone%}}`;
  `buttons{"N": [sources]}`; `hat{up,down,left,right: [sources]}`; `labels`.
- **Sources:** a physical button number (1-based), or `"hat:<dir>"`.

**Physical button numbering** is the index into the sorted list of evdev `EV_KEY` codes (+1). That
matches HID / VKBDevCfg numbering (Linux maps HID buttons 1–16 to `0x120–0x12f`, then `0x2c0+`).
Keep every tool consistent with this.

**Hiding the real stick takes two mechanisms; both are needed.**
- The daemon creates `/run/vkb-hotas/active`. The generated `72-vkb-hotas.rules` then applies
  `TAG-="uaccess", MODE="0600"`, so logind won't re-add the ACL on login or session switch.
- The daemon also `chmod 0600`s the nodes. With POSIX ACLs that zeroes the mask, which neutralises
  the ACL that is already applied.
- On exit it removes the flag and runs `udevadm trigger --action=change`.
- The rule file must sort after `70-uaccess` and before `73-seat-late`. It is generated by
  `install.sh` from `service/72-vkb-hotas.rules.in` (`@VID@` / `@PID@`).

**GUI (`mapper/vkb-mapper.py` + `.html`).** The server is stdlib `ThreadingHTTPServer`. It enforces three
guards, and keeps them:
- binds `127.0.0.1` only,
- requires a per-session token (`X-Token` header or `?t=`),
- checks the `Host` header (anti-DNS-rebinding).

- `/api/events` is SSE relaying the daemon socket. It coalesces state lines, and reconnects when
  the daemon restarts.
- `PUT /api/mapping` validates via `vkb_common` and writes atomically.
- `GET|PUT /api/layout`, `POST /api/image?view=ID` (raw body), and `GET /api/image/<name>` (token in
  the query, so `<img>` works) back the stick map.
- The server exits 45 s after its last SSE client disconnects.
- **Single-instance file:** `$XDG_RUNTIME_DIR/vkb-mapper/url-<hash of mapping path + socket>`. It's
  keyed so test instances never hand over to the user's real mapper. A test instance once did
  exactly that before the key existed.
- **`API_VERSION` handshake:** the page is re-read from disk on every request, so after a reinstall
  a still-running old server serves the new page. On a mismatch the page disables the stick map,
  because an old server would drop fields it doesn't know when saving. Bump `API_VERSION` (and the
  page's check) whenever the page relies on new server behaviour. History: 2 = clusters and `evo-*`
  drawings; 3 = `evo-scg-side` (an older server would silently swap unknown drawings on save);
  4 = physical button `names`; 5 = layout format 2 (picture-anchored pins, global `hats`).
- The page is one self-contained file (CSP forbids external resources). Physical inputs are amber
  and emulated/game outputs are cyan.
- **Stick map:** layout and photos are owned by the mapper server alone; the daemon never reads them.
  They live in `layout.json` + `images/` next to the mapping file (so `--mapping` relocates them for
  tests).
  - The layout is kept out of `mapping.json` deliberately: `normalize_mapping` drops unknown keys,
    and the layout describes hardware, not a game profile.
  - **Format version 2:** `views`, `hats` (global), `pins` keyed by **picture**, and `names`.
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

**Proton side.** `tools/proton-setup.py` sets winebus `"Enable SDL"=dword:0` in each prefix's
`system.reg`, so Wine uses its hidraw backend for everything. That passes the T.16000M HID
descriptor through untouched, and ignores SDL gamepad mappings that Steam injects. It also drops
cached `DirectInput\VID_…` entries from `user.reg`.

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
- **`rm -rf` on a computed path** (e.g. `$(mktemp -d)`) is blocked by a safety check in Claude Code.
  Use literal scratch paths for anything you need to delete afterwards.
- **Steam holds the device open.** Steam opens the real stick at startup, and hiding doesn't revoke
  an existing fd. After install, Steam must be restarted.
- **One source stick per install.** If the user owns a real T.16000M too, games see two identical
  devices; `vkb-check.py` warns about this.
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
- **Wine/Proton input:** winebus backends (SDL / hidraw / evdev) and registry options,
  `PROTON_ENABLE_HIDRAW`, DirectInput device types, Wine `+hid,+dinput` trace format, Wine `.reg` file
  format.
- **Steam layout:** library folders (native, Flatpak and Snap roots), `compatdata/<appid>/pfx`,
  launch options, Steam Input.
- **Local web security:** token auth, Host checks, CSP; SSE.
