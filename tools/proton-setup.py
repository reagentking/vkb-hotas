#!/usr/bin/env python3
"""
Configure Proton prefixes of HOTAS games for vkb-hotas. Run as your user, with
the games (and anything else running under Wine) closed.

  proton-setup.py                    pick games from a checklist of installed games
  proton-setup.py APPID ...          configure these games
  proton-setup.py --undo [APPID ...] restore Wine's default input backend (no ids: checklist)
  proton-setup.py --list             list installed Steam games with Proton prefixes

The checklist shows every installed game with a Proton prefix. Games already set
up start checked: check a game to set it up, uncheck one to restore Wine's
default. Without a terminal, no app ids means Squadrons + Ace Combat 8.

For each prefix:
  * winebus "Enable SDL"=0, so Wine uses its hidraw backend. That passes the
    virtual T.16000M's HID descriptor through untouched and ignores SDL gamepad
    mappings Steam injects (a common cause of "stick detected as a controller").
  * Cached DirectInput instance entries for the real stick / T.16000M are
    dropped so the game enumerates fresh.
A timestamped backup is written next to each edited .reg file.
"""
import sys
import argparse
import curses
import glob
import os
import re
import shutil
import subprocess
import time

# vkb_common lives in ../common in the repo and next to this file once installed
_here = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [_here, os.path.join(os.path.dirname(_here), "common")]
from vkb_common import default_match, parse_match

DEFAULT_APPS = {"1222730": "STAR WARS: Squadrons", "2288340": "ACE COMBAT 8: WINGS OF THEVE"}
LAUNCH = "PROTON_ENABLE_HIDRAW=0x044F/0xB10A %command%"
LAUNCH_DEBUG = "PROTON_ENABLE_HIDRAW=0x044F/0xB10A PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%"
STEAM_ROOTS = [
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",           # Flatpak
    "~/snap/steam/common/.local/share/Steam",                  # Snap
]
WINEBUS = r"System\\ControlSet001\\Services\\winebus"


def libraries():
    libs = set()
    for r in map(os.path.expanduser, STEAM_ROOTS):
        vdf = os.path.join(r, "steamapps/libraryfolders.vdf")
        if os.path.exists(vdf):
            libs.add(os.path.realpath(r))
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, errors="replace").read()):
                libs.add(os.path.realpath(m.group(1)))
    return sorted(libs)


def app_name(lib, appid):
    try:
        m = re.search(r'"name"\s+"([^"]+)"', open(os.path.join(lib, f"steamapps/appmanifest_{appid}.acf")).read())
        return m.group(1) if m else appid
    except OSError:
        return DEFAULT_APPS.get(appid, appid)


def prefixes():
    """{appid: (name, pfx_dir)} for every Proton prefix in every library."""
    out = {}
    for lib in libraries():
        for reg in glob.glob(os.path.join(lib, "steamapps/compatdata/*/pfx/system.reg")):
            appid = reg.split(os.sep)[-3]
            name = app_name(lib, appid)
            if appid == "0" or re.match(r"(Proton|Steam Linux Runtime|Steamworks)", name):
                continue  # Steam's own tools, not games
            out[appid] = (name, os.path.dirname(reg))
    return out


def is_configured(pfx):
    """True if the prefix already has winebus "Enable SDL"=0."""
    try:
        text = open(os.path.join(pfx, "system.reg"), encoding="utf-8", errors="surrogateescape").read()
    except OSError:
        return False
    span = block_span(text, WINEBUS)
    return bool(span) and re.search(r'^"Enable SDL"=dword:0+$', text[span[0]:span[1]], re.M) is not None


def _checklist_curses(scr, title, rows, checked):
    for setup in (lambda: curses.curs_set(0), curses.use_default_colors):
        try:
            setup()  # cosmetic; some terminals don't support it
        except curses.error:
            pass
    cur, top, sel = 0, 0, set(checked)
    help_ = "Up/Down move  Space toggle  a all/none  Enter apply  q cancel"
    while True:
        h, w = scr.getmaxyx()
        scr.erase()
        avail = max(1, h - 4)
        top = min(max(top, cur - avail + 1), cur)
        scr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
        for i, (key, label, note) in enumerate(rows[top:top + avail], top):
            line = f" [{'x' if key in sel else ' '}] {label}"
            if note:
                line = line.ljust(max(len(line) + 2, w - len(note) - 3)) + note
            scr.addnstr(2 + i - top, 0, line, w - 1, curses.A_REVERSE if i == cur else 0)
        scr.addnstr(h - 1, 0, help_, w - 1, curses.A_DIM)
        scr.refresh()
        k = scr.getch()
        if k in (curses.KEY_UP, ord("k")):
            cur = max(0, cur - 1)
        elif k in (curses.KEY_DOWN, ord("j")):
            cur = min(len(rows) - 1, cur + 1)
        elif k in (curses.KEY_PPAGE,):
            cur = max(0, cur - avail)
        elif k in (curses.KEY_NPAGE,):
            cur = min(len(rows) - 1, cur + avail)
        elif k == ord(" "):
            sel ^= {rows[cur][0]}
        elif k == ord("a"):
            sel = set() if len(sel) == len(rows) else {r[0] for r in rows}
        elif k in (10, 13, curses.KEY_ENTER):
            return sel
        elif k in (27, ord("q")):
            return None


def _checklist_text(title, rows, checked):
    print(title)
    for n, (key, label, note) in enumerate(rows, 1):
        print(f"  {n:>3}. [{'x' if key in checked else ' '}] {label}  {note}")
    ans = input("Numbers to have checked (e.g. 1 4 7), 'all', 'none', Enter to keep as shown, q to cancel: ").strip().lower()
    if ans == "q":
        return None
    if not ans:
        return set(checked)
    if ans == "all":
        return {r[0] for r in rows}
    if ans == "none":
        return set()
    picked = set()
    for tok in ans.replace(",", " ").split():
        if not tok.isdigit() or not 1 <= int(tok) <= len(rows):
            sys.exit(f"not a number from the list: {tok}")
        picked.add(rows[int(tok) - 1][0])
    return picked


def checklist(title, rows, checked):
    """rows: [(key, label, note)]. Returns the set of checked keys, or None if cancelled."""
    try:
        curses.setupterm()
        full_screen = curses.tigetstr("cup") is not None  # e.g. TERM=dumb can't position the cursor
    except curses.error:
        full_screen = False
    if full_screen:
        try:
            return curses.wrapper(_checklist_curses, title, rows, checked)
        except curses.error:
            pass
    return _checklist_text(title, rows, checked)


def wine_running(pfx):
    if subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True).returncode == 0:
        return True
    needle = b"WINEPREFIX=" + pfx.encode()
    for env in glob.glob("/proc/[0-9]*/environ"):
        try:
            if needle in open(env, "rb").read():
                return True
        except OSError:
            pass
    return False


def block_span(text, key):
    m = re.compile(r"^\[" + re.escape(key) + r"\][^\n]*\n", re.M | re.I).search(text)
    if not m:
        return None
    nxt = re.search(r"^\[", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start() if nxt else len(text))


def set_value(text, key, name, value):
    """Set (or with value=None, delete) "name" inside [key] of a Wine .reg file."""
    span = block_span(text, key)
    line = re.compile(r'^"' + re.escape(name) + r'"=[^\n]*\n', re.M)
    if span is None:
        return text if value is None else text + f'\n[{key}] {int(time.time())}\n"{name}"={value}\n'
    start, end = span
    block = text[start:end]
    if line.search(block):
        block = line.sub("" if value is None else f'"{name}"={value}\n', block)
    elif value is not None:
        lines = block.split("\n")
        lines.insert(1 if lines and lines[0].startswith("#time=") else 0, f'"{name}"={value}')
        block = "\n".join(lines)
    return text[:start] + block + text[end:]


def drop_dinput_cache(text, vidpids):
    alt = "|".join(f"{v:04X}&PID_{p:04X}" if p is not None else f"{v:04X}&PID_[0-9A-F]{{4}}" for v, p in vidpids)
    pat = re.compile(r"^\[System\\\\CurrentControlSet\\\\Control\\\\MediaProperties\\\\PrivateProperties"
                     r"\\\\DirectInput\\\\VID_(?:" + alt + r")\\\\[^\n]*\n(?:(?!\[)[^\n]*\n)*", re.M | re.I)
    return pat.sub("", text)


def edit(path, fn, label):
    text = open(path, encoding="utf-8", errors="surrogateescape").read()
    new = fn(text)
    if new == text:
        print(f"    {os.path.basename(path)}: nothing to change")
        return
    bak = f"{path}.vkb-hotas.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(new)
    print(f"    {os.path.basename(path)}: {label} (backup: {os.path.basename(bak)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("appids", nargs="*", help="Steam app ids (default: a checklist of installed games; without a terminal: %s)"
                    % ", ".join(DEFAULT_APPS))
    ap.add_argument("--undo", action="store_true", help="remove the setting again (Wine default backend)")
    ap.add_argument("--list", action="store_true", help="list Proton prefixes and exit")
    args = ap.parse_args()

    pfxs = prefixes()
    if args.list:
        for appid, (name, pfx) in sorted(pfxs.items(), key=lambda kv: kv[1][0].lower()):
            print(f"  {appid:>10}  {'set up    ' if is_configured(pfx) else 'not set up'}  {name}")
        return

    vidpids = [parse_match(default_match()), (0x044F, 0xB10A)]
    setup, undo = [], []
    if args.appids:
        (undo if args.undo else setup).extend(args.appids)
    elif not (sys.stdin.isatty() and sys.stdout.isatty()):
        if args.undo:
            sys.exit("--undo needs app ids when not run in a terminal")
        setup.extend(DEFAULT_APPS)
    else:
        games = sorted(pfxs.items(), key=lambda kv: kv[1][0].lower())
        done = {a for a, (_, pfx) in games if is_configured(pfx)}
        if args.undo:
            games = [g for g in games if g[0] in done]
            if not games:
                print("No game prefixes are set up; nothing to restore.")
                return
            title, start = "Restore Wine's default input backend for:", set()
        else:
            if not games:
                print("No Proton prefixes found. Install a game and launch it once.")
                return
            title, start = "Set up these games for vkb-hotas (checked = set up):", done
        rows = [(a, name, f"{a}  {'set up' if a in done else 'not set up'}") for a, (name, _) in games]
        sel = checklist(title, rows, start)
        if sel is None:
            print("Cancelled; nothing changed.")
            return
        if args.undo:
            undo = sorted(sel, key=[g[0] for g in games].index)
        else:
            order = [g[0] for g in games]
            setup = [a for a in order if a in sel and a not in done]
            undo = [a for a in order if a in done and a not in sel]
        if not setup and not undo:
            print("No changes selected.")
            return
        for label, ids in (("Set up", setup), ("Restore default", undo)):
            if ids:
                print(f"{label}: " + ", ".join(pfxs[a][0] for a in ids))
        if input("Apply? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            print("Cancelled; nothing changed.")
            return

    for appid, restore in [(a, False) for a in setup] + [(a, True) for a in undo]:
        if appid not in pfxs:
            print(f"[{DEFAULT_APPS.get(appid, appid)}] no Proton prefix found (install and launch the game once)")
            continue
        name, pfx = pfxs[appid]
        print(f"[{name}] {pfx}")
        if wine_running(pfx):
            print("    Wine is running; close the game (and other Proton apps) and retry")
            continue
        if restore:
            edit(os.path.join(pfx, "system.reg"), lambda t: set_value(t, WINEBUS, "Enable SDL", None),
                 "SDL backend restored")
        else:
            edit(os.path.join(pfx, "system.reg"), lambda t: set_value(t, WINEBUS, "Enable SDL", "dword:00000000"),
                 "SDL backend disabled")
            edit(os.path.join(pfx, "user.reg"), lambda t: drop_dinput_cache(t, vidpids),
                 "stale DirectInput entries dropped")

    if setup:
        print(f"\nSteam launch options for each game (Properties > General), Steam Input off:\n  {LAUNCH}")
        print(f"For a diagnosable run (then check with ./tools/vkb-check.py):\n  {LAUNCH_DEBUG}")


if __name__ == "__main__":
    main()
