---
name: test-installer
description: Test vkb-hotas's install.sh and uninstall.sh without root by running them against a throwaway sandbox root (system paths redirected, systemctl/udevadm/modprobe stubbed), then checking every installed file against its repo source, the rendered udev rule, the config, the launcher and a clean uninstall. Use this whenever install.sh, uninstall.sh, the systemd unit, the udev rule template or the repository's file layout changes (moving or renaming files), and before asking the user to run sudo ./install.sh after such a change.
---

# Test the installer without root

The user runs `sudo ./install.sh` on their real machine. If an installer change is wrong (a
stale source path after files moved, the wrong file copied, something uninstall forgets), that's
where it fails, mid-install, on their system. Check it first in a sandbox.

## Run

```bash
.claude/skills/test-installer/scripts/sandbox_install.sh            # default: --device 231d:3200 --throttle 231d:3201@usb-... --user $USER
.claude/skills/test-installer/scripts/sandbox_install.sh --keep ... # keep the sandbox for inspection
```

**Run it outside Claude Code's Bash sandbox** (`dangerouslyDisableSandbox: true`). The sandbox
hides `/dev`, so `install.sh` stops at its `/dev/uhid missing` check and the run fails with
nothing wrong in the repo. The script itself writes only to a temp dir and the repo.

What the script does:
- Copies `install.sh` and `uninstall.sh` with `/usr/local`, `/etc/`, `/run/vkb-hotas` and
  `/run/udev/` rewritten into a temp root, and the root check disabled. The copies stay in the repo directory (deleted
  afterwards), so the installer's `$here` still resolves the real sources.
- Pre-creates the standard directories a real system already has (`/etc/modules-load.d`,
  `/etc/udev/rules.d`, …). The installer writes into some of them without `install -D`.
- Puts stub `systemctl` / `udevadm` / `modprobe` commands first on `PATH`, which only log their
  calls.

It then checks:
- **Install:** exits 0, and lists the installed tree.
- **Files:** every installed file is byte-identical to its repo source (matched by name). This
  catches a wrong or stale file copied, even when the install "succeeds".
- **Generated files:** the (now static) udev rule passes `udevadm verify`; the config has exactly
  one `VKB_HOTAS_DEVICE`, `VKB_HOTAS_THROTTLE` and `VKB_HOTAS_USER` line each, written by
  `write_config` (selectors like `231d:3200@usb-0000:18:00.3-2.1` are awkward for `sed`, so the
  installer doesn't use it for them).
- **Runs:** the `vkb-mapper` symlink runs, and the installed daemon imports from the flat layout.
- **Uninstall:** removes everything except `/etc/default/vkb-hotas`, which is deliberately kept.

`RESULT: OK` (exit 0) or `RESULT: FAIL` with the failing lines.

## When it fails

- `error: /dev/uhid missing` straight away: you ran it inside the Bash sandbox. Re-run it outside.
- `cannot stat .../<file>` during install: `install.sh` points at a path that moved. Update its
  `$here/...` source paths.
- `MISMATCH`: the installer copies a different file than the repo source, or the source changed
  between steps. Read the `install -D...` line for that file.
- `LEFT BEHIND` after uninstall: add the path to `uninstall.sh`'s `rm -f` list.
- A new file isn't listed at all: the installer doesn't install it yet. That's fine if it's
  dev-only, otherwise add an `install -D...` line, and an `rm` in `uninstall.sh`.

## What it can't tell you

- **Real system behaviour:** the stubs don't start services or apply udev rules. Real behaviour
  (the service starting, the stick hidden) needs the user's actual `sudo ./install.sh`, then the
  `verify-install` skill.
- **Device autodetect and the picker:** it passes `--device` by default and has no terminal, because
  detection reads real input devices and the picker needs a TTY. Test `pick_one` itself through a
  pty instead (see CLAUDE.md's gotcha about arrow-key sequences).

The sandbox removes itself unless `--keep` was given. To delete a kept one, use its literal path,
since `rm -rf` on a computed path is blocked by a safety check.
