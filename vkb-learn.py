#!/usr/bin/env python3
"""
Record a stick's axes to work out the --axis mapping: which axes spring back
to centre (stick, twist, thumb stick) and which hold position (throttle lever).

  vkb-learn.py [SECONDS] [--device VID[:PID]|/dev/input/eventN]

Move every control through its full travel, then let go, before time runs out.
Note: while vkb-hotas is running the real stick is hidden from you; stop it
first (sudo systemctl stop vkb-hotas) or run this as root.
"""
import argparse
import select
import sys
import time

import evdev
from evdev import ecodes as E

from vkb_common import default_match, find_one


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seconds", nargs="?", type=float, default=60)
    ap.add_argument("--device", default=default_match(), help="VID[:PID] or /dev/input/eventN (default: from config, else any VKB)")
    args = ap.parse_args()

    dev = evdev.InputDevice(args.device) if args.device.startswith("/dev/") else find_one(args.device)
    if dev is None:
        sys.exit("no matching stick readable by you. Is vkb-hotas hiding it? "
                 "Try: sudo systemctl stop vkb-hotas, or run with sudo.")
    ai = {c: a for c, a in dev.capabilities(absinfo=True)[E.EV_ABS] if not E.ABS_HAT0X <= c <= E.ABS_HAT3Y}
    start = {c: a.value for c, a in ai.items()}
    cur, lo, hi = dict(start), dict(start), dict(start)
    buttons, hat = set(), set()
    end = time.time() + args.seconds
    print(f"recording {dev.name.strip()} ({dev.info.vendor:04x}:{dev.info.product:04x}) for {args.seconds:.0f}s: "
          "move every control through full travel, then let go", flush=True)
    while time.time() < end:
        if not select.select([dev.fd], [], [], 0.2)[0]:
            continue
        for ev in dev.read():
            if ev.type == E.EV_ABS and ev.code in ai:
                cur[ev.code] = ev.value
                lo[ev.code] = min(lo[ev.code], ev.value)
                hi[ev.code] = max(hi[ev.code], ev.value)
            elif ev.type == E.EV_ABS and ev.value:
                hat.add((E.ABS[ev.code], ev.value))
            elif ev.type == E.EV_KEY and ev.value:
                buttons.add(ev.code)

    print(f"\n{'axis':10} {'travel%':>7} {'final-off-centre%':>17}  verdict")
    for c, a in ai.items():
        span = max(1, a.max - a.min)
        travel = 100 * (hi[c] - lo[c]) / span
        off = 100 * abs(cur[c] - (a.max + a.min) / 2) / (span / 2)
        verdict = ("not moved" if travel < 5 else
                   "HOLDS POSITION: throttle-like (try --axis SLIDER=%s)" % E.ABS[c] if off > 15 else
                   "springs back to centre (stick/twist/thumb stick)")
        print(f"{E.ABS[c]:10} {travel:7.0f} {off:17.0f}  {verdict}")
    order = {code: i + 1 for i, code in enumerate(sorted(dev.capabilities()[E.EV_KEY]))}
    pressed = sorted(order[b] for b in buttons)
    print("buttons pressed (joystick button #):", pressed or "-")
    if pressed and max(pressed) > 16:
        print("  some are above 16: add --button-count 32 to VKB_HOTAS_ARGS")
    print("hat directions seen:", sorted(hat) or "-")


if __name__ == "__main__":
    main()
