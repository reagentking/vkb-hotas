#!/usr/bin/env python3
"""
fake-daemon: develop and test HOTAS Mapper without root or a stick.

Runs the daemon's real event loop (the Daemon class from ../service/vkb-hotas.py,
with its Mapper, ControlServer and MappingWatcher) against simulated VKB Gladiator
NXT EVO Omni Throttles: a right-hand one (231d:3200) and a left-hand one (231d:3201,
an assumed product id). There is no uhid, grabbing or udev hiding; device selection
and exposing are saved to DIR/config instead of /etc/default/vkb-hotas.

  dev/fake-daemon.py [--dir DIR] [--still] [--sticks 1|2] [--twin] [--throttle SEL] [--button-count 16|32]

Then, in another terminal, point the mapper at it:

  VKB_HOTAS_CTL=DIR/ctl.sock mapper/vkb-mapper.py --mapping DIR/mapping.json

Simulation commands on the control socket, which the real daemon doesn't accept:

  {"cmd": "fake_press", "source": 17}                      button 17 on the stick role's stick
  {"cmd": "fake_press", "source": "hat:left", "stick": 1}  hat on simulated stick 1 (the left one)
  {"cmd": "fake_plug", "stick": 1, "on": false}            unplug / plug a simulated stick
  dev/fake-daemon.py --press 17 [--stick 1] [--dir DIR]     one-shot client for fake_press
  dev/fake-daemon.py --unplug 1 / --plug 1 [--dir DIR]      one-shot client for fake_plug
"""
import argparse
import importlib.util
import json
import math
import os
import signal
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))
_spec = importlib.util.spec_from_file_location("vkb_hotas", os.path.join(ROOT, "service", "vkb-hotas.py"))
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)

from evdev import InputEvent  # noqa: E402
from evdev import ecodes as E  # noqa: E402

import vkb_common  # noqa: E402

DEFAULT_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vkb-hotas-dev")


class AbsInfo:
    def __init__(self, value, maximum, minimum=0):
        self.value, self.min, self.max = value, minimum, maximum


class FakeStick:
    """Same capabilities as the real VKBsim Gladiator EVO OT R: 6 axes, 1 hat, 79 buttons.
    Events are queued by inject() and read through a pipe, so the daemon can poll() it."""

    class _Info:
        def __init__(self, vendor, product):
            self.vendor, self.product = vendor, product

    def __init__(self, n, product, hand, buttons=79, port=None):
        self.path = f"/dev/input/sim{n}"
        self.name = f"VKB-Sim (C) Alex Oz 2023  VKBsim Gladiator EVO OT {hand}  (SIMULATED)"
        self.phys = f"{port or f'usb-sim-{n}'}/input0"
        self.info = self._Info(0x231D, product)
        self.keys = [0x120 + i for i in range(min(buttons, 16))] + [0x2C0 + i for i in range(max(0, buttons - 16))]
        self.fd, self._w = os.pipe()
        os.set_blocking(self.fd, False)
        self.queue = []
        self.plugged = True
        self.grabbed = False

    def capabilities(self, absinfo=False):
        return {E.EV_ABS: [(E.ABS_X, AbsInfo(2048, 4095)), (E.ABS_Y, AbsInfo(2048, 4095)),
                           (E.ABS_Z, AbsInfo(1571, 2047)), (E.ABS_RX, AbsInfo(2048, 4095)),
                           (E.ABS_RY, AbsInfo(2048, 4095)), (E.ABS_RZ, AbsInfo(1024, 2047)),
                           (E.ABS_HAT0X, AbsInfo(0, 1, -1)), (E.ABS_HAT0Y, AbsInfo(0, 1, -1))],
                E.EV_KEY: list(self.keys)}

    def inject(self, *events):
        self.queue += events
        os.write(self._w, b"x")

    def read(self):
        try:
            os.read(self.fd, 4096)
        except BlockingIOError:
            pass
        evs, self.queue = self.queue, []
        return evs

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False


class FakeSource:
    def __init__(self, sticks):
        self.sticks = sticks

    def scan(self):
        return [s for s in self.sticks if s.plugged]

    def release(self, dev):
        pass  # simulated sticks stay "open"

    @staticmethod
    def nodes(dev):
        return {}


class FakeUhid:
    def __init__(self, spec):
        self.spec = spec
        self.fd, self._w = os.pipe()  # never readable: no kernel on the other end
        self.reports = 0
        self.last = None

    def send(self, report):
        self.reports += 1
        self.last = report

    def handle(self, current):
        pass

    def destroy(self):
        os.close(self.fd)
        os.close(self._w)


def ev(etype, code, value):
    return InputEvent(0, 0, etype, code, value)


def client(sock_path, cmd):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(2)
        s.connect(sock_path)
        s.makefile("rb").readline()  # wait for hello (avoids the accept/EPIPE race)
        s.sendall(json.dumps(cmd).encode() + b"\n")
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"where ctl.sock, mapping.json and config live (default: {DEFAULT_DIR})")
    ap.add_argument("--still", action="store_true", help="don't sweep the axes")
    ap.add_argument("--sticks", type=int, choices=(1, 2), default=2, help="simulated sticks: right OT, plus a left OT")
    ap.add_argument("--twin", action="store_true", help="give both sticks the same VID:PID (tests @port selectors)")
    ap.add_argument("--throttle", default=None, help="initial throttle selector, e.g. 231d:3201 (default: none)")
    ap.add_argument("--buttons", type=int, default=79, help="physical button count to simulate")
    ap.add_argument("--button-count", type=int, choices=(16, 32), default=16,
                    help="T.16000M buttons in the no-mapping-file default profile (like VKB_HOTAS_ARGS)")
    ap.add_argument("--hold", type=float, default=0.6, help="seconds a simulated press is held")
    ap.add_argument("--press", metavar="SOURCE", help="client mode: press a button number or hat:<dir> on a running fake daemon")
    ap.add_argument("--stick", type=int, default=None, help="with --press: simulated stick index (default: the stick role's)")
    ap.add_argument("--unplug", type=int, metavar="N", help="client mode: unplug simulated stick N")
    ap.add_argument("--plug", type=int, metavar="N", help="client mode: plug simulated stick N back in")
    args = ap.parse_args()
    sock_path = os.path.join(args.dir, "ctl.sock")
    if len(sock_path.encode()) > 107:
        sys.exit(f"--dir is too long for a unix socket path ({len(sock_path.encode())} > 107 bytes); pick a shorter one")

    if args.press:
        client(sock_path, {"cmd": "fake_press", "source": int(args.press) if args.press.isdigit() else args.press,
                           "stick": args.stick})
        return
    if args.unplug is not None or args.plug is not None:
        client(sock_path, {"cmd": "fake_plug", "stick": args.unplug if args.unplug is not None else args.plug,
                           "on": args.plug is not None})
        return

    os.makedirs(args.dir, exist_ok=True)
    sticks = [FakeStick(0, 0x3200, "R", args.buttons)]
    if args.sticks == 2:
        sticks.append(FakeStick(1, 0x3200 if args.twin else 0x3201, "L", args.buttons))
    config = os.path.join(args.dir, "config")
    cfg = vkb_common.read_config(config)
    selectors = {"stick": cfg.get("VKB_HOTAS_DEVICE") or "231d:3200",
                 "throttle": cfg.get("VKB_HOTAS_THROTTLE", args.throttle or "") or ""}
    if args.twin and "@" not in selectors["stick"]:
        selectors["stick"] = "231d:3200@usb-sim-0"

    v.ControlServer.PATH = sock_path
    ctl = v.ControlServer(os.getuid())
    daemon = v.Daemon(selectors, vkb_common.exposed_roles(cfg), ctl=ctl,
                      watcher=v.MappingWatcher(os.path.join(args.dir, "mapping.json"), os.getuid()),
                      source=FakeSource(sticks), make_uhid=FakeUhid, hider=None,
                      persist=lambda updates: vkb_common.write_config(updates, config),
                      fallback=v.default_profile(args.button_count),
                      logger=lambda *a: print(*a, flush=True))
    held, t0 = {}, time.monotonic()

    def stick_for(idx):
        if idx is None:
            role = daemon.roles["stick"]
            return role.dev
        return sticks[idx] if 0 <= idx < len(sticks) else None

    def fake_press(cmd):
        s, source, now = stick_for(cmd.get("stick")), cmd.get("source"), time.monotonic()
        if s is None or not s.plugged:
            print(f"fake_press: no such plugged stick {cmd.get('stick')!r}", flush=True)
            return
        order = sorted(s.keys)
        if isinstance(source, int) and 1 <= source <= len(order):
            s.inject(ev(E.EV_KEY, order[source - 1], 1))
            held[(id(s), "key", order[source - 1])] = (s, now + args.hold)
        elif isinstance(source, str) and source[4:] in v.HAT_DIRS:
            d = source[4:]
            code = E.ABS_HAT0X if d in ("left", "right") else E.ABS_HAT0Y
            s.inject(ev(E.EV_ABS, code, -1 if d in ("left", "up") else 1))
            held[(id(s), "hat", code)] = (s, now + args.hold)
        else:
            print(f"fake_press: unknown source {source!r}", flush=True)
            return
        print(f"pressed {source} on {s.path}", flush=True)

    def fake_plug(cmd):
        s = stick_for(cmd.get("stick"))
        if s is None:
            return
        s.plugged = bool(cmd.get("on"))
        if not s.plugged:  # what a real unplug looks like to the daemon: POLLHUP on the fd
            for role in daemon.roles.values():
                if role.dev is s:
                    daemon.detach(role, "disconnected")
        print(f"{'plugged' if s.plugged else 'unplugged'} {s.path}", flush=True)

    real_command = daemon.command

    def command(cmd, fd):
        if cmd.get("cmd") == "fake_press":
            fake_press(cmd)
        elif cmd.get("cmd") == "fake_plug":
            fake_plug(cmd)
        else:
            real_command(cmd, fd)
    daemon.command = command

    def tick(now):
        if not args.still:
            t = now - t0
            if sticks[0].plugged:
                sticks[0].inject(ev(E.EV_ABS, E.ABS_X, int(2048 + 1500 * math.sin(t * 1.3))),
                                 ev(E.EV_ABS, E.ABS_Y, int(2048 + 1200 * math.sin(t * 0.9 + 1))),
                                 ev(E.EV_ABS, E.ABS_RZ, int(1024 + 600 * math.sin(t * 0.6))),
                                 ev(E.EV_ABS, E.ABS_Z, int(1024 + 1000 * math.sin(t * 0.25))))
            if len(sticks) > 1 and sticks[1].plugged:
                sticks[1].inject(ev(E.EV_ABS, E.ABS_Z, int(1024 + 1000 * math.sin(t * 0.2 + 2))),
                                 ev(E.EV_ABS, E.ABS_RX, int(2048 + 1500 * math.sin(t * 0.7))))
        for key, (s, until) in list(held.items()):
            if now >= until:
                _, kind, code = key
                s.inject(ev(E.EV_KEY, code, 0) if kind == "key" else ev(E.EV_ABS, code, 0))
                del held[key]
    daemon.tick = tick
    daemon.SCAN_EVERY = 0.5

    print(f"fake daemon on {sock_path}\n  mapper: VKB_HOTAS_CTL={sock_path} {ROOT}/mapper/vkb-mapper.py "
          f"--mapping {daemon.watcher.path}", flush=True)
    daemon.scan()

    def on_term(*_):
        daemon.stop = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    daemon.run()


if __name__ == "__main__":
    main()
