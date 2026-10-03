---
name: test-mapper-ui
description: Test HOTAS Mapper (mapper/vkb-mapper.py and mapper/vkb-mapper.html) end to end without root, a real stick or the user's data, using dev/fake-daemon.py plus an isolated mapper instance driven in the browser pane. Use this whenever you change the mapper page or server (stick map, pins, 5-way hats, zoom, button names, assign/learn, profiles, layout format), when the user reports a mapper bug you need to reproduce, or before telling the user a mapper feature works.
---

# Test HOTAS Mapper against a simulated stick

`dev/fake-daemon.py` runs the daemon's real `Mapper`, `ControlServer` and `MappingWatcher`
classes with a simulated Gladiator. It has no uhid, grab or udev, so it needs no root.
`scripts/test_stack.sh` pairs it with a mapper instance that has its own socket, mapping file and
port, so tests can't touch `~/.config/vkb-hotas` or the user's running mapper.

## Start, drive, stop

```bash
S=.claude/skills/test-mapper-ui/scripts/test_stack.sh
$S start [--button-count 32] [--layout ~/.config/vkb-hotas/layout.json] [--sweep]   # prints the URL
$S press 17          # simulated physical press (held 0.6 s); also hat:up|down|left|right
$S url               # reprint the URL
$S stop              # always, when done
```

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
- **State:** `layout`, `mapping`, `prof()`, `curView()`, `picKey(v)`, `curPins()`.
- **Actions:** `assign(kind, key, src, add)`, `renderMap()`, `saveLayout(true)`.
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

- **Mapping:** Assign (and Shift+Assign) moves a source off other slots; labels persist; Test
  pulses light the output.
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
  - a version mismatch shows the restart message (start a mapper from before the
    `API_VERSION` bump);
  - an old-format layout converts on load;
  - the server rejects bad input (curl the API with the token).

## Cleanup

Run `$S stop`. It kills only processes carrying the private test directory, and deletes the test
data and `__pycache__`. Never use `pkill -f` here: it can match your own shell.
