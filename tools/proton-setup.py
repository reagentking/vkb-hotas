#!/usr/bin/env python3
"""
Configure Proton prefixes of HOTAS games for vkb-hotas. Run as your user, with
the games (and anything else running under Wine) closed.

  proton-setup.py [APPID ...]        configure (default: Squadrons + Ace Combat 8)
  proton-setup.py --undo [APPID ...] restore Wine's default input backend
  proton-setup.py --list             list installed Steam games with Proton prefixes

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
    ap.add_argument("appids", nargs="*", help="Steam app ids (default: %s)" % ", ".join(DEFAULT_APPS))
    ap.add_argument("--undo", action="store_true", help="remove the setting again (Wine default backend)")
    ap.add_argument("--list", action="store_true", help="list Proton prefixes and exit")
    args = ap.parse_args()

    pfxs = prefixes()
    if args.list:
        for appid, (name, pfx) in sorted(pfxs.items(), key=lambda kv: kv[1][0].lower()):
            print(f"  {appid:>10}  {name}")
        return

    vidpids = [parse_match(default_match()), (0x044F, 0xB10A)]
    for appid in args.appids or list(DEFAULT_APPS):
        if appid not in pfxs:
            print(f"[{DEFAULT_APPS.get(appid, appid)}] no Proton prefix found (install and launch the game once)")
            continue
        name, pfx = pfxs[appid]
        print(f"[{name}] {pfx}")
        if wine_running(pfx):
            print("    Wine is running; close the game (and other Proton apps) and retry")
            continue
        if args.undo:
            edit(os.path.join(pfx, "system.reg"), lambda t: set_value(t, WINEBUS, "Enable SDL", None),
                 "SDL backend restored")
        else:
            edit(os.path.join(pfx, "system.reg"), lambda t: set_value(t, WINEBUS, "Enable SDL", "dword:00000000"),
                 "SDL backend disabled")
            edit(os.path.join(pfx, "user.reg"), lambda t: drop_dinput_cache(t, vidpids),
                 "stale DirectInput entries dropped")

    if not args.undo:
        print(f"\nSteam launch options for each game (Properties > General), Steam Input off:\n  {LAUNCH}")
        print(f"For a diagnosable run (then check with ./vkb-check.py):\n  {LAUNCH_DEBUG}")


if __name__ == "__main__":
    main()
