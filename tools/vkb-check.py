#!/usr/bin/env python3
"""
Verify the vkb-hotas setup and analyse Proton logs.

  vkb-check.py               host checks + analysis of the newest Proton log
  vkb-check.py LOGFILE       analyse a specific log
  vkb-check.py --device V:P  check a stick other than the configured one (as the stick role)

Proton logs come from launching a game with
  PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%
and land in ~/steam-<appid>.log (or $PROTON_LOG_DIR).
"""
import argparse
import ctypes
import glob
import os
import re
import subprocess
import sys
import time

import json
import socket

# vkb_common lives in ../common in the repo and next to this file once installed
_here = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [_here, os.path.join(os.path.dirname(_here), "common")]
from vkb_common import (CONFIG, EMULATED_IDS, EMULATED_NAMES, ROLES, exposed_roles, load_mapping, mapping_path,
                        parse_match, role_selectors)

if sys.stdout.isatty():
    OK, BAD, WARN, INFO = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m", "\033[33mWARN\033[0m", "  "
else:
    OK, BAD, WARN, INFO = "OK", "FAIL", "WARN", "  "
EMU_IDS = set(EMULATED_IDS.values())


def can_open(path):
    try:
        os.close(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        return True
    except OSError:
        return False


def hid_devices(vid, pid):
    """sysfs HID device dirs for VID[:PID] (works even when the nodes are hidden from us)."""
    p = f"{pid:04X}" if pid is not None else "*"
    return sorted(glob.glob(f"/sys/bus/hid/devices/*:{vid:04X}:{p}.*"))


def nodes(hid):
    out = ["/dev/" + os.path.basename(s) for s in glob.glob(f"{hid}/hidraw/hidraw*")]
    out += ["/dev/input/" + os.path.basename(s) for s in glob.glob(f"{hid}/input/input*/event*")]
    return [n for n in out if os.path.exists(n)]


def hid_name(hid):
    try:
        m = re.search(r"^HID_NAME=(.*)$", open(f"{hid}/uevent").read(), re.M)
        return m.group(1).strip() if m else "?"
    except OSError:
        return "?"


def configured_roles(stick_override=None):
    """{role: (selector, exposed)} from the running daemon if reachable, else the config file."""
    hello = daemon_hello()
    if hello and "roles" in hello:
        roles = {r: (v["selector"], v["exposed"]) for r, v in hello["roles"].items()}
    else:
        sel, exp = role_selectors(), exposed_roles()
        roles = {r: (sel[r] if (sel[r] or r == "stick") else None, r in exp) for r in ROLES}
    if stick_override is not None:
        roles["stick"] = (stick_override, roles["stick"][1])
    return roles


def host_checks(stick_override):
    print("== host")
    st = subprocess.run(["systemctl", "is-active", "vkb-hotas.service"], capture_output=True, text=True).stdout.strip()
    print(f"  [{OK if st == 'active' else BAD}] vkb-hotas.service: {st or 'not installed'}")
    if st != "active":
        print(f"{INFO}  -> journalctl -u vkb-hotas -b   (bad VKB_HOTAS_ARGS in {CONFIG}? stick unplugged?)")
    rules = "/etc/udev/rules.d/72-vkb-hotas.rules"
    print(f"  [{OK if os.path.exists(rules) else BAD}] udev rule {'installed' if os.path.exists(rules) else 'missing (run sudo ./install.sh)'}")

    mapper_checks()
    roles = configured_roles(stick_override)
    hide_rule = "/run/udev/rules.d/72-vkb-hotas-hide.rules"
    hidden_any = False
    for r in ROLES:
        sel, exposed = roles[r]
        if sel is None:
            print(f"{INFO}{r}: not used (no {EMULATED_NAMES[r]})")
            continue
        vid, pid = parse_match(sel)
        real = [h for h in hid_devices(vid, pid) if not any(f":{v:04X}:{p:04X}." in h for v, p in EMU_IDS)]
        if "@" in sel:
            real = real[:1] if len(real) > 1 else real  # sysfs can't tell ports apart here; show one
        label = sel or "any VKB stick"
        if not real:
            print(f"  [{WARN}] {r}: real stick {label} not connected")
        for h in real:
            print(f"{INFO}{r}: {hid_name(h)} ({os.path.basename(h)}){'  EXPOSED to games (vkb-mapper --hide ' + r + ' to undo)' if exposed else ''}")
            for n in nodes(h):
                vis = can_open(n)
                if exposed:
                    print(f"  [{OK if vis else WARN}] real    {n:20} {'openable: games see the real stick (exposed)' if vis else 'not openable by you'}")
                else:
                    hidden_any = True
                    print(f"  [{BAD if vis else OK}] real    {n:20} {'OPENABLE by you: games will see it' if vis else 'hidden from your session'}")
        emu_id = EMULATED_IDS[r]
        virt = hid_devices(*emu_id)
        name = f"virtual {EMULATED_NAMES[r]} {emu_id[0]:04x}:{emu_id[1]:04x}"
        if exposed:
            print(f"  [{OK if not virt else WARN}] {name} {'removed while exposed' if not virt else 'present although exposed'}")
        elif not virt:
            print(f"  [{BAD if real else WARN}] {name} not present")
        for h in ([] if exposed else virt):
            for n in nodes(h):
                vis = can_open(n)
                print(f"  [{OK if vis else BAD}] virtual {n:20} {'openable by you' if vis else 'NOT openable (udev rule missing?)'}")
        if len(virt) > 1:
            print(f"  [{WARN}] {len(virt)} devices with {emu_id[0]:04x}:{emu_id[1]:04x}: a real {EMULATED_NAMES[r]} is plugged in too; games may pick either")
    if hidden_any:
        print(f"  [{OK if os.path.exists(hide_rule) else WARN}] runtime hiding rule {hide_rule} "
              f"{'present' if os.path.exists(hide_rule) else 'missing (permissions may come back on a session change)'}")
    sdl_probe(roles)


def daemon_hello():
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect("/run/vkb-hotas/ctl.sock")
            return json.loads(s.makefile().readline())
    except (OSError, ValueError):
        return None


def mapper_checks():
    path = mapping_path()
    if os.path.exists(path):
        try:
            m = load_mapping(path)
            print(f"  [{OK}] mapping file valid: {len(m['profiles'])} profile(s), active \"{m['active']}\"")
        except (OSError, ValueError) as e:
            print(f"  [{BAD}] mapping file {path}: {e} (the daemon keeps its previous mapping)")
    else:
        print(f"{INFO}no mapping file yet: built-in default profile (create one with vkb-mapper)")
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect("/run/vkb-hotas/ctl.sock")
            hello = json.loads(s.makefile().readline())
        note = f", daemon reports: {hello['error']}" if hello.get("error") else ""
        if "roles" not in hello:
            note += ", but the daemon is an older version (reinstall: sudo ./install.sh)"
        print(f"  [{OK if not note else WARN}] control socket reachable: daemon applying \"{hello.get('profile')}\"{note}")
    except PermissionError:
        print(f"  [{BAD}] control socket not yours: set VKB_HOTAS_USER in {CONFIG} (or sudo ./install.sh --user $USER)")
    except (OSError, ValueError):
        print(f"  [{WARN}] control socket unavailable: daemon not running, or an older version without vkb-mapper support")


def sdl_probe(roles):
    real_ids = [(r, *parse_match(sel), exposed) for r, (sel, exposed) in roles.items() if sel is not None]
    for lib in ("libSDL3.so.0", "libSDL3.so"):
        try:
            sdl = ctypes.CDLL(lib)
            break
        except OSError:
            sdl = None
    if sdl is None:
        print(f"  [{WARN}] libSDL3 not found, skipping SDL probe")
        return
    sdl.SDL_Init.restype = ctypes.c_bool
    sdl.SDL_GetJoysticks.restype = ctypes.POINTER(ctypes.c_uint32)
    sdl.SDL_GetJoystickNameForID.restype = ctypes.c_char_p
    sdl.SDL_IsGamepad.restype = ctypes.c_bool
    sdl.SDL_GetJoystickTypeForID.restype = ctypes.c_int
    sdl.SDL_GetJoystickVendorForID.restype = ctypes.c_uint16
    sdl.SDL_GetJoystickProductForID.restype = ctypes.c_uint16
    if not sdl.SDL_Init(0x200 | 0x2000):  # JOYSTICK | GAMEPAD
        return
    time.sleep(0.5)
    types = {0: "unknown", 1: "gamepad", 2: "wheel", 3: "arcade stick", 4: "flight stick",
             5: "dance pad", 6: "guitar", 7: "drum kit", 8: "arcade pad", 9: "throttle"}
    n = ctypes.c_int()
    ids = sdl.SDL_GetJoysticks(ctypes.byref(n))
    print(f"{INFO}SDL view (what native games and Steam see):")
    for i in range(n.value):
        j = ids[i]
        v, p = sdl.SDL_GetJoystickVendorForID(j), sdl.SDL_GetJoystickProductForID(j)
        real = next((x for x in real_ids if v == x[1] and (x[2] is None or p == x[2])), None)
        if not real and (v, p) not in EMU_IDS:
            continue
        gp = sdl.SDL_IsGamepad(j)
        good = not gp and (not real or real[3])
        note = ("  <- real stick, exposed on purpose" if real and real[3] else
                "  <- real stick still visible" if real else "")
        print(f"    [{OK if good else BAD}] {v:04x}:{p:04x} {sdl.SDL_GetJoystickNameForID(j).decode(errors='replace')!r} "
              f"type={types.get(sdl.SDL_GetJoystickTypeForID(j), '?')} gamepad={gp}{note}")
    sdl.SDL_Quit()


def analyse(path, roles):
    real_ids = [(r, *parse_match(sel), exposed) for r, (sel, exposed) in roles.items() if sel is not None]
    print(f"\n== Proton log {path}")
    head = open(path, errors="replace").read(4000)
    for key, rx in (("proton", r"Proton: (.*)"), ("appid", r"SteamGameId: (\d+)")):
        m = re.search(rx, head)
        print(f"{INFO}{key}: {m.group(1) if m else '?'}")
    m = re.search(r"Effective WINEDEBUG: (.*)", head)
    traced = bool(m and "+hid" in m.group(1) and "+dinput" in m.group(1))
    if not traced:
        print(f"  [{WARN}] WINEDEBUG lacks +hid,+dinput; launch with PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%")

    devs, enums, detected = {}, 0, set()
    acquired, reads, fmt = [], {}, {}
    sdl_off = False
    last_fmt_iface = None
    for line in open(path, errors="replace"):
        if "SDL devices disabled" in line:
            sdl_off = True
        m = re.search(r"bus_create_hid_device desc \{vid (\w+), pid (\w+).*is_gamepad (\d), is_hidraw (\d)", line)
        if m:
            devs[(int(m[1], 16), int(m[2], 16))] = (m[3] == "1", m[4] == "1")
        # Wine's log prefix is a THREAD id and games read on other threads, so key by device iface.
        m = re.match(r"\w{4}:trace:dinput:(\w+) (?:iface )?(\w+)?(.*)", line)
        if not m:
            continue
        fn, iface, rest = m[1], m[2], m[3]
        if fn == "dinput8_EnumDevices":
            enums += 1
        elif fn == "hid_joystick_device_try_open" and "device type" in rest:
            detected.add(rest.split()[-1])
        elif fn == "dinput_device_SetDataFormat" and "dwDataSize" in rest:
            fmt[last_fmt_iface] = int(re.search(r"dwDataSize (\d+)", rest)[1])
        elif fn == "dinput_device_SetDataFormat":
            last_fmt_iface = iface
        elif fn == "dinput_device_Acquire" and iface not in acquired:
            acquired.append(iface)
        elif fn in ("dinput_device_GetDeviceState", "dinput_device_GetDeviceData", "dinput_device_Poll"):
            reads[iface] = reads.get(iface, 0) + 1

    print(f"{INFO}winebus SDL backend: {'disabled (hidraw path)' if sdl_off else 'ENABLED (run ./tools/proton-setup.py)'}")
    if devs:
        for r, (sel, exposed) in roles.items():
            if sel is None or exposed:
                continue
            name = f"virtual {EMULATED_NAMES[r]}"
            emu = devs.get(EMULATED_IDS[r])
            if emu is None:
                print(f"  [{BAD}] {name} never reached Wine (service down when the game started?)")
            else:
                print(f"  [{BAD if emu[0] else OK}] Wine created {name}: "
                      f"{'GAMEPAD' if emu[0] else 'joystick'} via {'hidraw' if emu[1] else 'SDL/evdev'}")
        leaked = [(k, x) for k in devs for x in real_ids if k[0] == x[1] and (x[2] is None or k[1] == x[2])]
        for (v, p), x in leaked:
            if x[3]:
                print(f"{INFO}real {x[0]} stick {v:04x}:{p:04x} visible to the game (exposed on purpose)")
            else:
                print(f"  [{BAD}] real {x[0]} stick {v:04x}:{p:04x} was visible to the game (not hidden)")
        if not leaked:
            print(f"  [{OK}] real sticks not visible to the game")
    elif traced:
        print(f"  [{WARN}] no HID devices in log")

    if enums:
        print(f"{INFO}DirectInput: EnumDevices x{enums}, detected {sorted(detected) or '-'} "
              f"(0x..14 = joystick: 0x10214 standard, 0x10114 limited)")
    kinds = {256: "keyboard", 16: "mouse", 20: "mouse", 80: "joystick", 272: "joystick"}
    sticks = 0
    for iface in acquired:
        n = reads.get(iface, 0)
        kind = kinds.get(fmt.get(iface), "unknown")
        if kind in ("keyboard", "mouse"):
            print(f"{INFO}  device {iface}: {kind}, read {n} times (not the stick)")
            continue
        sticks += 1
        print(f"  [{OK if n else WARN}] device {iface}: {kind}, acquired, read {n} times"
              f"{'' if n else ' (never polled: menu-only session, or the game ignores it)'}")
    if traced and not sticks:
        print(f"  [{BAD}] the game never acquired a joystick (enumerated but rejected)")


def newest_log():
    dirs = {os.path.expanduser("~"), os.path.expanduser(os.environ.get("PROTON_LOG_DIR", "~"))}
    logs = [f for d in dirs for f in glob.glob(os.path.join(d, "steam-*.log"))]
    return max(logs, key=os.path.getmtime) if logs else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", help="Proton log to analyse (default: newest steam-*.log)")
    ap.add_argument("--device", default=None, help="real stick VID[:PID] for the stick role (default: from the daemon/config)")
    args = ap.parse_args()
    roles = configured_roles(args.device)
    if args.log:
        analyse(args.log, roles)
        return
    host_checks(args.device)
    log = newest_log()
    if log:
        analyse(log, roles)
    else:
        print("\nno steam-*.log found (launch a game with PROTON_LOG=1 WINEDEBUG=+hid,+dinput %command%)")


if __name__ == "__main__":
    main()
