---
name: verify-install
description: Verify a vkb-hotas (re)install on the user's machine. Compares the installed copy in /usr/local/lib/vkb-hotas with the repo, finds a stale HOTAS Mapper process still running old code, runs the health checks, and validates the user's mapping/layout files read-only with the installed code. Use this whenever the user says they ran the install or reinstall ("I ran the install, check if it's working", "installed it", "did the update take?"), after any change that needs `sudo ./install.sh`, or when something in HOTAS Mapper behaves as if it's an old version.
---

# Verify a vkb-hotas install

The user runs `sudo ./install.sh` themselves, because it needs their password. Your job afterwards
is to confirm three things. Each one has failed at least once before:

1. **The installed files are the repo's files.** If they differ, the install didn't happen or used
   an older checkout.
2. **No stale HOTAS Mapper is running.** The server re-reads the page from disk on every request.
   So a mapper started *before* the install serves the new page with old server code. The page's
   `API_VERSION` check disables the stick map in that case, but the user still needs to restart it.
3. **The user's data loads with the installed code.** Formats change between versions, and the
   installed normalizer must accept, or convert, what's on disk.

## Steps

1. Run the read-only checker (it changes nothing):
   ```bash
   .claude/skills/verify-install/scripts/verify_install.sh
   ```
2. Read the report:
   - `DIFFERS` on code files: the installed copy isn't the repo's current code. Either the install
     didn't happen, or the repo changed since; check `git log`/`git status` for changes made after
     the install time. A README-only difference is harmless if the docs changed after the install.
   - Repo paths are mapped to the flat install (`service/vkb-hotas.py` →
     `/usr/local/lib/vkb-hotas/vkb-hotas.py`, and so on), so the layout itself never causes
     `DIFFERS`.
   - `API_VERSION repo != installed`: not reinstalled yet.
   - A mapper marked `STALE`: tell the user to close the HOTAS Mapper tab, wait about a minute
     (it exits 45 s after its last tab closes), and reopen it.
   - Any `FAIL` in the health section: see `journalctl -u vkb-hotas -b`. A common cause is a bad
     `VKB_HOTAS_ARGS` in `/etc/default/vkb-hotas`; inline `# comments` there break it.
   - A file marked `INVALID`: don't "fix" it silently. Explain, and offer a fix with a backup.
3. If you need to confirm the server's live API (rarely), start the installed mapper with
   `--no-browser` on a spare port, query `/api/layout` with its token, and stop it straight away.
   - Its single-instance file is keyed by mapping path, so it's the *same* key as the user's
     mapper.
   - A long-lived extra instance would intercept their next launch. Don't leave one running.

## Rules

- **Stay read-only.** Never write `~/.config/vkb-hotas/*` as part of a check.
- **Don't run sudo.** If something needs root, give the user the exact command to run in a real
  terminal; the inline `!` box can't take a password.
- If a running mapper started after the install, it's current. Say so, and don't ask them to
  restart.

## Reporting

Lead with the verdict ("Yes, it's working" / "Not yet: …"). Then cover:
- **Install:** time and version, and whether all files match.
- **Running mapper:** current, stale, or none.
- **Health:** service, real stick hidden, virtual stick present, SDL sees a flight stick.
- **The user's data:** what's in it (views, pins per picture, hats, names), so they can see their
  work survived.
- **Next steps:** usually what to do in the mapper next, or to restart it.

Mention uncommitted repo changes only if the user has been asking you to commit.
