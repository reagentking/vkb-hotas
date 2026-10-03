#!/usr/bin/env python3
"""
fake-daemon: develop and test HOTAS Mapper without root or a stick.

Runs the daemon's real Mapper, ControlServer and MappingWatcher classes (imported
from ../vkb-hotas.py) against a simulated VKB Gladiator NXT EVO OT R, but
without uhid, evdev grabbing or udev hiding. Axes sweep continuously; buttons
and hat directions are pressed on demand.

  dev/fake-daemon.py [--dir DIR] [--still] [--buttons N] [--button-count 16|32]

Then, in another terminal, point the mapper at it:

  VKB_HOTAS_CTL=DIR/ctl.sock ./vkb-mapper.py --mapping DIR/mapping.json

Simulated presses (held for --hold seconds) via the control socket, which the
real daemon doesn't accept:

  {"cmd": "fake_press", "source": 17}            physical button 17
  {"cmd": "fake_press", "source": "hat:left"}    physical hat direction
  dev/fake-daemon.py --press 17 [--dir DIR]       same, as a one-shot client

Keep the main loop's message handling (hello / mapping / state / pulse) in step
with main() in vkb-hotas.py.
"""
import argparse
import importlib.util
import json
import math
import os
import select
import signal
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("vkb_hotas", os.path.join(ROOT, "vkb-hotas.py"))
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)

from evdev import InputEvent  # noqa: E402
from evdev import ecodes as E  # noqa: E402

DEFAULT_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vkb-hotas-dev")


class AbsInfo:
    def __init__(self, value, maximum, minimum=0):
        self.value, self.min, self.max = value, minimum, maximum


class FakeStick:
    """Same capabilities as the real VKBsim Gladiator EVO OT R: 6 axes, 1 hat, 79 buttons."""
    name = "VKB-Sim (C) Alex Oz 2023  VKBsim Gladiator EVO OT R  (SIMULATED)"

    class info:
        vendor, product = 0x231D, 0x3200

    def __init__(self, buttons=79):
        self.keys = [0x120 + i for i in range(min(buttons, 16))] + [0x2C0 + i for i in range(max(0, buttons - 16))]

    def capabilities(self, absinfo=False):
        return {E.EV_ABS: [(E.ABS_X, AbsInfo(2048, 4095)), (E.ABS_Y, AbsInfo(2048, 4095)),
                           (E.ABS_Z, AbsInfo(1571, 2047)), (E.ABS_RX, AbsInfo(2048, 4095)),
                           (E.ABS_RY, AbsInfo(2048, 4095)), (E.ABS_RZ, AbsInfo(1024, 2047)),
                           (E.ABS_HAT0X, AbsInfo(0, 1, -1)), (E.ABS_HAT0Y, AbsInfo(0, 1, -1))],
                E.EV_KEY: list(self.keys)}


def ev(etype, code, value):
    return InputEvent(0, 0, etype, code, value)


def press_client(sock_path, source):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(2)
        s.connect(sock_path)
        s.makefile("rb").readline()  # wait for hello (avoids the accept/EPIPE race)
        s.sendall(json.dumps({"cmd": "fake_press", "source": source}).encode() + b"\n")
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"where ctl.sock and mapping.json live (default: {DEFAULT_DIR})")
    ap.add_argument("--still", action="store_true", help="don't sweep the axes")
    ap.add_argument("--buttons", type=int, default=79, help="physical button count to simulate")
    ap.add_argument("--button-count", type=int, choices=(16, 32), default=16,
                    help="emulated buttons in the no-mapping-file default profile (like VKB_HOTAS_ARGS)")
    ap.add_argument("--hold", type=float, default=0.6, help="seconds a simulated press is held")
    ap.add_argument("--press", metavar="SOURCE", help="client mode: press a button number or hat:<dir> on a running fake daemon")
    args = ap.parse_args()
    sock_path = os.path.join(args.dir, "ctl.sock")
    if len(sock_path.encode()) > 107:
        sys.exit(f"--dir is too long for a unix socket path ({len(sock_path.encode())} > 107 bytes); pick a shorter one")

    if args.press:
        press_client(sock_path, int(args.press) if args.press.isdigit() else args.press)
        return

    os.makedirs(args.dir, exist_ok=True)
    dev = FakeStick(args.buttons)
    mapper = v.Mapper(dev, v.T16000M)
    fallback = v.default_profile(args.button_count)
    mapper.apply(fallback)
    v.ControlServer.PATH = sock_path
    ctl = v.ControlServer(os.getuid())
    watcher = v.MappingWatcher(os.path.join(args.dir, "mapping.json"), os.getuid())
    profile_name = "built-in default"
    key_for = {n: code for code, n in mapper.btn_num.items()}

    def hello():
        return {"t": "hello",
                "device": {"name": dev.name, "id": "231d:3200", "buttons": len(mapper.btn_num),
                           "axes": [{"name": E.ABS[c], "min": a.min, "max": a.max} for c, a in mapper.absinfo.items()]},
                "emulated": {"name": v.T16000M["name"], "id": "044f:b10a", "buttons": mapper.spec["buttons"],
                             "axes": {a: mapper.spec["axes"][a] for a in v.EMU_AXES}},
                "profile": profile_name, "mapping": watcher.path, "error": watcher.error}

    def fake_press(source, now):
        if isinstance(source, int) and source in key_for:
            mapper.feed(ev(E.EV_KEY, key_for[source], 1))
            held[("key", key_for[source])] = now + args.hold
        elif isinstance(source, str) and source[4:] in v.HAT_DIRS:
            d = source[4:]
            code = E.ABS_HAT0X if d in ("left", "right") else E.ABS_HAT0Y
            mapper.feed(ev(E.EV_ABS, code, -1 if d in ("left", "up") else 1))
            held[("hat", code)] = now + args.hold
        else:
            print(f"fake_press: unknown source {source!r}", flush=True)
            return
        print(f"pressed {source}", flush=True)

    print(f"fake daemon on {sock_path}\n  mapper: VKB_HOTAS_CTL={sock_path} {ROOT}/vkb-mapper.py "
          f"--mapping {watcher.path}", flush=True)
    t0, last_sent, next_check, held = time.monotonic(), 0.0, 0.0, {}

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        while True:
            now = time.monotonic()
            if now >= next_check:
                next_check = now + 0.5
                change = watcher.poll()
                if change:
                    kind, name, prof = change
                    if kind == "error":
                        print(f"mapping rejected: {name}", flush=True)
                        ctl.send({"t": "mapping", "ok": False, "error": name, "profile": profile_name})
                    else:
                        mapper.apply(prof if kind == "file" else fallback)
                        profile_name = name if kind == "file" else "built-in default"
                        print(f"applied profile: {profile_name}", flush=True)
                        ctl.send({"t": "mapping", "ok": True, "error": None, "profile": profile_name})
                        ctl.send(hello())
            if not args.still:
                t = now - t0
                mapper.feed(ev(E.EV_ABS, E.ABS_X, int(2048 + 1500 * math.sin(t * 1.3))))
                mapper.feed(ev(E.EV_ABS, E.ABS_Y, int(2048 + 1200 * math.sin(t * 0.9 + 1))))
                mapper.feed(ev(E.EV_ABS, E.ABS_RZ, int(1024 + 600 * math.sin(t * 0.6))))
                mapper.feed(ev(E.EV_ABS, E.ABS_Z, int(1024 + 1000 * math.sin(t * 0.25))))
            for (kind, code), until in list(held.items()):
                if now >= until:
                    mapper.feed(ev(E.EV_KEY, code, 0) if kind == "key" else ev(E.EV_ABS, code, 0))
                    del held[(kind, code)]

            for fd in select.select(ctl.fds(), [], [], 0.03)[0]:
                if fd == ctl.sock.fileno():
                    new = ctl.accept()
                    if new is not None:
                        ctl.send(hello(), new)
                    continue
                for cmd in ctl.read(fd):
                    if cmd.get("cmd") == "pulse" and isinstance(cmd.get("button"), int):
                        mapper.pulse(cmd["button"], time.monotonic())
                        print(f"pulse B{cmd['button']}", flush=True)
                    elif cmd.get("cmd") == "fake_press":
                        fake_press(cmd.get("source"), time.monotonic())
                    elif cmd.get("cmd") == "hello":
                        ctl.send(hello(), fd)
            if ctl.clients and now - last_sent > 0.033:
                ctl.send(mapper.state_msg(now))
                last_sent = now
    except KeyboardInterrupt:
        pass
    finally:
        ctl.close()


if __name__ == "__main__":
    main()
