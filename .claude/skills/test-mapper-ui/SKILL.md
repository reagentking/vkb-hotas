---
name: test-mapper-ui
description: Test HOTAS Mapper (mapper/vkb-mapper.py and mapper/vkb-mapper.html) end to end without root, a real stick or the user's data, using dev/fake-daemon.py plus an isolated mapper instance driven in the browser pane. Use this whenever you change the mapper page or server (stick map, pins, 5-way hats, zoom, button names, assign/learn, profiles, layout format), when the user reports a mapper bug you need to reproduce, or before telling the user a mapper feature works.
---

# Test HOTAS Mapper against a simulated stick

`dev/fake-daemon.py` runs the daemon's real `Daemon` event loop with two simulated Omni Throttles
(stick 0 = right `231d:3200`, stick 1 = left `231d:3201`). It has no uhid, grab or udev, so it needs
no root; device selection and exposing persist to the test dir's `config`.
`scripts/test_stack.sh` pairs it with a mapper instance that has its own socket, mapping file and
port, so tests can't touch `~/.config/vkb-hotas` or the user's running mapper.

## Start, drive, stop

```bash
S=.claude/skills/test-mapper-ui/scripts/test_stack.sh
$S start [--button-count 32] [--layout ~/.config/vkb-hotas/layout.json] [--sweep] [--throttle 231d:3201] [--twin]
$S press 17          # simulated press (held 0.6 s) on the stick role's stick; also hat:up|down|left|right
$S press 17 1        # ... on simulated stick 1 (the left one): cross-mapping, throttle role
$S unplug 1 / plug 1 # unplug / replug a simulated stick
$S url               # reprint the URL
$S stop              # always, when done
VKB_HOTAS_CTL=$XDG_RUNTIME_DIR/vkb-hotas-dev/ctl.sock mapper/vkb-mapper.py --expose throttle   # CLI against the fake
```

- **Run these outside Claude Code's Bash sandbox** (`dangerouslyDisableSandbox: true`): it blocks
  unix sockets and `$XDG_RUNTIME_DIR`.

- **`--layout`** copies a file into the test dir. Use a copy of the user's real layout to test
  format migrations on realistic data; the original is never touched.
- **`--sweep`** makes the axes move continuously. Leave it off (the default) for stable
  screenshots, and for axis "Learn", which would otherwise grab whichever axis moves first.
- **`start` refuses** to continue if the mapper handed over to another instance instead of
  starting an isolated one.

## Browser pane

- **Port:** the pane is allowed to open `127.0.0.1:8765`; other ports were refused. If the pane is
  closed, reopen it with `preview_start` and the URL.
- **Viewport:** use `resize_window` 1600×1000 for the two-column layout, and reset it to
  `desktop` when finished.
- **Screenshots:** they come back at half scale. Take one before any coordinate click, and
  convert page CSS pixels to the screenshot frame (divide by 2).
- **Don't scroll with `scrollIntoView` or `scrollTo` before a screenshot:** it produced blank or
  shifted captures. Scroll with the real wheel (`computer` scroll) instead.
- **A script that reloads the page loses its return value.** Reload in one call and inspect in
  the next.
- **Console errors from earlier sessions** ("ERR_CONNECTION_REFUSED" from old EventSources)
  aren't from your change. Judge by a fresh load.

## Driving the page

The page's globals are reachable from `javascript_tool`. That's faster and more reliable than
clicking for setup:
- **State:** `role` (current tab), `mapping`, `prof()` (that role's section), `profR(r)`,
  `layoutAll` / `layout` (that role's section), `hello`, `live`, `curView()`, `picKey(v)`,
  `curPins()`, `readOnly`.
- **Actions:** `setRole(r)`, `assign(kind, key, src, add)` (src may be `"throttle:7"`),
  `renderMap()`, `saveLayout(true)`, `renderDevices()`.
- **Dialogs:** answer them by setting `#mInput` and clicking `#mOk` or `#mCancel`.
- **Placement and drags:** use a real mouse click at least once, to prove the coordinate math.
  Synthetic `MouseEvent('click', {clientX, clientY})` on `#canvas` is fine after that.

**Check results on disk too.** Read `$XDG_RUNTIME_DIR/vkb-hotas-dev/layout.json` or
`mapping.json`, because the page's in-memory state can disagree with what was saved. A real bug
(edits silently lost after an autosave) was only visible this way.

**Live output:** connect to `$XDG_RUNTIME_DIR/vkb-hotas-dev/ctl.sock` and read `state` lines. For
example, press a button and check `out.b` has the mapped bit set, or send
`{"cmd":"pulse","button":N}` after reading the `hello` line.

## What to cover (pick what your change touches)

- **Roles and devices:** both tabs render; Devices selects, swaps and clears the throttle (check
  `DIR/config`); unplug/replug updates the tab and banner; the "Games see" switch (panel and Devices) and `--expose`
  flip the VIRTUAL/REAL tab badge and banner, and persist to `DIR/config`; switching tabs repaints live values.
- **Mapping:** Assign (and Shift+Assign) moves a source off other slots *on both devices*; a press
  on the other stick saves a prefixed source (`throttle:7`) and shows a `THR`/`STK` chip tag;
  cross axes in the dropdown; labels persist; Test pulses light the right device's output.
- **Stick map:**
  - pins land where clicked (also when zoomed);
  - each picture shows only its own pins;
  - switching a view's drawing and back restores its pins;
  - drag and × work;
  - hats: wizard, reject already-used buttons, "No center push", place the same hat on a second
    drawing, × only removes it from one drawing, delete-setup prompt.
- **Names and badges:** names follow a button when it's remapped; profiles share them; hat badges
  show on chips.
- **Robustness:**
  - reload persistence;
  - a version mismatch makes the page read-only with the restart message: copy an older
    `mapper/vkb-mapper.py` (`git show <rev>:...`) plus its `vkb_common.py` into a temp dir with
    the *new* page, start it on 8765, and confirm `mapping.json` is untouched after edits;
  - an old-format layout converts on load;
  - the server rejects bad input (curl the API with the token).

## Cleanup

Run `$S stop`. It kills only processes carrying the private test directory, and deletes the test
data and `__pycache__`. Never use `pkill -f` here: it can match your own shell.
