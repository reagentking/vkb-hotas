#!/usr/bin/env python3
"""
vkb-hotas: re-present a flight stick (default: any VKB device, vendor 231d) to
Proton/Wine games as a Thrustmaster T.16000M (044f:b10a), using /dev/uhid.

Some games (e.g. Ace Combat 8, Star Wars Squadrons) only enable their
flight-stick code path for HOTAS models they recognise. Under Proton such a
stick is enumerated by DirectInput but the game never opens it. A uhid device
gives Wine a real hidraw node with a T.16000M HID descriptor, so the game sees
a stick it knows: X/Y, Rz (twist), Slider (throttle), one hat, 16 or 32 buttons.

While running, the real stick is:
  * EVIOCGRAB'd, so the desktop/Steam stop receiving its evdev events;
  * hidden from user sessions: a flag file makes the installed udev rule drop
    its uaccess tag, and its event/js/hidraw nodes are chmod 0600 (which zeroes
    the ACL mask), so newly started games only see the virtual stick.
On exit, permissions are restored with `udevadm trigger --action=change`.

Runs as root (uhid is root-only); normally started by udev via systemd.
Configuration: /etc/default/vkb-hotas (VKB_HOTAS_DEVICE, VKB_HOTAS_ARGS).
"""
import argparse
import errno
import glob
import json
import os
import pwd
import select
import signal
import socket
import struct
import subprocess
import sys
import time

import evdev
from evdev import ecodes as E

from vkb_common import (DEFAULT_AXES, EMU_AXES, HAT_DIRS, default_match, default_profile,
                        find_one, load_mapping, mapping_path)

# ---- uhid ABI (linux/uhid.h) -------------------------------------------------
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14
UHID_EVENT_SIZE = 4380  # sizeof(struct uhid_event) per linux/uhid.h
BUS_USB = 0x03

# ---- Emulated device: Thrustmaster T.16000M -----------------------------------
# Report (9 bytes): buttons[16] | hat:4 pad:4 | X u16 | Y u16 | Rz u8 | Slider u8
T16000M = dict(
    name="Thrustmaster T.16000M",
    vid=0x044F,
    pid=0xB10A,
    version=0x0100,
    rdesc=bytes([
        0x05, 0x01,        # Usage Page (Generic Desktop)
        0x09, 0x04,        # Usage (Joystick)
        0xA1, 0x01,        # Collection (Application)
        0x05, 0x09,        #   Usage Page (Button)
        0x19, 0x01,        #   Usage Minimum (1)
        0x29, 0x10,        #   Usage Maximum (16)       [patched by with_buttons]
        0x15, 0x00,        #   Logical Minimum (0)
        0x25, 0x01,        #   Logical Maximum (1)
        0x75, 0x01,        #   Report Size (1)
        0x95, 0x10,        #   Report Count (16)        [patched by with_buttons]
        0x81, 0x02,        #   Input (Data,Var,Abs)
        0x05, 0x01,        #   Usage Page (Generic Desktop)
        0x09, 0x39,        #   Usage (Hat switch)
        0x15, 0x00,        #   Logical Minimum (0)
        0x25, 0x07,        #   Logical Maximum (7)
        0x35, 0x00,        #   Physical Minimum (0)
        0x46, 0x3B, 0x01,  #   Physical Maximum (315)
        0x65, 0x14,        #   Unit (Eng Rot: Degrees)
        0x75, 0x04,        #   Report Size (4)
        0x95, 0x01,        #   Report Count (1)
        0x81, 0x42,        #   Input (Data,Var,Abs,Null)
        0x65, 0x00,        #   Unit (None)
        0x75, 0x04,        #   Report Size (4)
        0x95, 0x01,        #   Report Count (1)
        0x81, 0x01,        #   Input (Const) padding
        0x09, 0x30,        #   Usage (X)
        0x09, 0x31,        #   Usage (Y)
        0x15, 0x00,        #   Logical Minimum (0)
        0x26, 0xFF, 0x3F,  #   Logical Maximum (16383)
        0x75, 0x10,        #   Report Size (16)
        0x95, 0x02,        #   Report Count (2)
        0x81, 0x02,        #   Input (Data,Var,Abs)
        0x09, 0x35,        #   Usage (Rz)
        0x09, 0x36,        #   Usage (Slider)
        0x15, 0x00,        #   Logical Minimum (0)
        0x26, 0xFF, 0x00,  #   Logical Maximum (255)
        0x75, 0x08,        #   Report Size (8)
        0x95, 0x02,        #   Report Count (2)
        0x81, 0x02,        #   Input (Data,Var,Abs)
        0xC0,              # End Collection
    ]),
    axes={"X": 16383, "Y": 16383, "RZ": 255, "SLIDER": 255},
    buttons=16,
)



def with_buttons(spec, n):
    """Variant of the spec exposing n (16 or 32) buttons. 16 matches a real T.16000M."""
    if n == spec["buttons"]:
        return spec
    rd = bytearray(spec["rdesc"])
    rd[rd.index(bytes([0x29, 0x10])) + 1] = n
    rd[rd.index(bytes([0x95, 0x10])) + 1] = n
    return dict(spec, rdesc=bytes(rd), buttons=n)


# Default axis mapping (vkb_common.DEFAULT_AXES) suits VKB Gladiator NXT EVO
# bases: X/Y = stick, RZ = twist, Z = Omni Throttle lever. Verify yours with
# vkb-learn.py / vkb-mapper.py. Source axes the device lacks are held at neutral.

# hat (x,y) -> HID hat value, 8 = null (outside logical range)
HAT = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
       (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}


def log(*a):
    print("vkb-hotas:", *a, flush=True)


def find_real_device(spec):
    if spec.startswith("/dev/"):
        return evdev.InputDevice(spec) if os.path.exists(spec) else None
    return find_one(spec)


def sibling_nodes(dev):
    """event/js/hidraw /dev nodes belonging to the same HID device."""
    sys_ev = os.path.realpath(f"/sys/class/input/{os.path.basename(dev.path)}")
    # .../0003:231D:3200.XXXX/input/inputN/eventM -> hid dir is 3 up
    hid_dir = os.path.dirname(os.path.dirname(os.path.dirname(sys_ev)))
    nodes = []
    for pat in ("input/input*/event*", "input/input*/js*", "hidraw/hidraw*"):
        for s in glob.glob(os.path.join(hid_dir, pat)):
            n = "/dev/input/" + os.path.basename(s) if "input" in pat else "/dev/" + os.path.basename(s)
            if os.path.exists(n):
                nodes.append(n)
    return hid_dir, nodes


class Uhid:
    def __init__(self, spec, verbose=False):
        self.verbose = verbose
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_CLOEXEC)
        rd = spec["rdesc"]
        payload = struct.pack(
            "<128s64s64sHHIIII4096s",
            spec["name"].encode(), b"vkb-hotas/uhid", b"vkb-hotas-virtual",
            len(rd), BUS_USB, spec["vid"], spec["pid"], spec["version"], 0, rd)
        self._write(struct.pack("<I", UHID_CREATE2) + payload)

    def _write(self, buf):
        buf = buf.ljust(UHID_EVENT_SIZE, b"\0")
        os.write(self.fd, buf)

    def send(self, report):
        self._write(struct.pack("<IH", UHID_INPUT2, len(report)) + report)

    def handle(self, current_report):
        ev = os.read(self.fd, UHID_EVENT_SIZE)
        (etype,) = struct.unpack_from("<I", ev)
        if etype == UHID_GET_REPORT:
            rid, rnum, rtype = struct.unpack_from("<IBB", ev, 4)
            data = current_report if rtype == 2 else b""  # 2 = UHID_INPUT_REPORT
            err = 0 if data else errno.EIO
            self._write(struct.pack("<IIHH", UHID_GET_REPORT_REPLY, rid, err, len(data)) + data)
        elif etype == UHID_SET_REPORT:
            (rid,) = struct.unpack_from("<I", ev, 4)
            self._write(struct.pack("<IIH", UHID_SET_REPORT_REPLY, rid, 0))
        elif etype in (UHID_START, UHID_STOP) or (self.verbose and etype in (UHID_OPEN, UHID_CLOSE)):
            log({UHID_OPEN: "virtual stick opened", UHID_CLOSE: "virtual stick closed",
                 UHID_START: "uhid started", UHID_STOP: "uhid stopped"}[etype])
        return etype

    def destroy(self):
        try:
            self._write(struct.pack("<I", UHID_DESTROY))
        finally:
            os.close(self.fd)


class Mapper:
    """Physical stick state -> emulated T.16000M report, driven by a mapping profile."""

    def __init__(self, dev, spec_base):
        self.spec_base = spec_base
        self.absinfo = {c: ai for c, ai in dev.capabilities(absinfo=True).get(E.EV_ABS, [])
                        if not E.ABS_HAT0X <= c <= E.ABS_HAT3Y}
        # Physical button numbers follow Linux joystick order, which matches the
        # HID button numbers VKBDevCfg shows (for the first 80 or so buttons).
        self.btn_num = {code: i + 1 for i, code in enumerate(sorted(dev.capabilities().get(E.EV_KEY, [])))}
        self.raw = {c: ai.value for c, ai in self.absinfo.items()}
        self.pressed = set()
        self.hat = [0, 0]
        self.pulses = {}
        self.apply(default_profile())

    def apply(self, profile):
        self.profile = profile
        self.spec = with_buttons(self.spec_base, profile["button_count"])
        self.axes = {}
        for dst, cfg in profile["axes"].items():
            code = E.ecodes.get(cfg["src"]) if cfg["src"] else None
            if cfg["src"] and code not in self.absinfo:
                log(f"source axis {cfg['src']} not on this device; emulated {dst} held at neutral")
                code = None
            self.axes[dst] = (code, cfg["invert"], cfg["deadzone"] / 100)
        self.buttons = {int(k): v for k, v in profile["buttons"].items() if int(k) <= self.spec["buttons"]}
        self.hatmap = profile["hat"]

    def feed(self, ev):
        """Update physical state; returns True if anything changed."""
        if ev.type == E.EV_ABS:
            if ev.code in self.raw:
                self.raw[ev.code] = ev.value
            elif ev.code == E.ABS_HAT0X:
                self.hat[0] = ev.value
            elif ev.code == E.ABS_HAT0Y:
                self.hat[1] = ev.value
            else:
                return False
            return True
        if ev.type == E.EV_KEY and ev.code in self.btn_num:
            n = self.btn_num[ev.code]
            (self.pressed.add if ev.value else self.pressed.discard)(n)
            return True
        return False

    def _active(self, src):
        if isinstance(src, int):
            return src in self.pressed
        d = src[4:]
        return ((d == "up" and self.hat[1] < 0) or (d == "down" and self.hat[1] > 0)
                or (d == "left" and self.hat[0] < 0) or (d == "right" and self.hat[0] > 0))

    def _axis(self, dst):
        code, invert, dz = self.axes[dst]
        top = self.spec["axes"][dst]
        if code is None:
            return top // 2
        ai = self.absinfo[code]
        f = (self.raw[code] - ai.min) / max(1, ai.max - ai.min)
        if invert:
            f = 1.0 - f
        if dz:
            c = f * 2 - 1
            m = abs(c)
            c = 0.0 if m < dz else (m - dz) / (1 - dz) * (1 if c > 0 else -1)
            f = (c + 1) / 2
        return max(0, min(top, round(f * top)))

    def output(self, now):
        for n in [n for n, t in self.pulses.items() if t <= now]:
            del self.pulses[n]
        mask = 0
        for n in self.pulses:  # test presses from the mapper GUI, even on unassigned slots
            mask |= 1 << (n - 1)
        for n, srcs in self.buttons.items():
            if any(self._active(s) for s in srcs):
                mask |= 1 << (n - 1)
        on = {d: any(self._active(s) for s in self.hatmap.get(d, [])) for d in HAT_DIRS}
        hat = HAT[(on["right"] - on["left"], on["down"] - on["up"])]
        return mask, hat, {a: self._axis(a) for a in EMU_AXES}

    def report(self, now):
        mask, hat, a = self.output(now)
        fmt = "<HBHHBB" if self.spec["buttons"] == 16 else "<IBHHBB"
        return struct.pack(fmt, mask, hat & 0x0F, a["X"], a["Y"], a["RZ"], a["SLIDER"])

    def pulse(self, n, now, length=0.35):
        if 1 <= n <= self.spec["buttons"]:
            self.pulses[n] = now + length

    def state_msg(self, now):
        mask, hat, a = self.output(now)
        return {"t": "state",
                "raw": {"b": sorted(self.pressed), "h": self.hat,
                        "a": {E.ABS[c]: v for c, v in self.raw.items()}},
                "out": {"b": mask, "h": hat, "a": a}}


class ControlServer:
    """Unix socket for vkb-mapper.py: streams live state (JSON lines) and takes
    small commands. Owned by the configured user, mode 0600."""
    PATH = "/run/vkb-hotas/ctl.sock"
    MAX_OUT = 256 * 1024

    def __init__(self, uid):
        os.makedirs(os.path.dirname(self.PATH), exist_ok=True)
        try:
            os.unlink(self.PATH)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC)
        old = os.umask(0o177)
        try:
            self.sock.bind(self.PATH)
        finally:
            os.umask(old)
        if uid is not None:
            os.chown(self.PATH, uid, -1)
        self.sock.listen(4)
        self.clients = {}  # fd -> [socket, inbuf, outbuf]

    def fds(self):
        return [self.sock.fileno(), *self.clients]

    def accept(self):
        try:
            conn, _ = self.sock.accept()
        except BlockingIOError:
            return None
        conn.setblocking(False)
        self.clients[conn.fileno()] = [conn, b"", b""]
        return conn.fileno()

    def drop(self, fd):
        c = self.clients.pop(fd, None)
        if c:
            c[0].close()

    def send(self, msg, fd=None):
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        for f in ([fd] if fd is not None else list(self.clients)):
            c = self.clients.get(f)
            if not c:
                continue
            c[2] += line
            if len(c[2]) > self.MAX_OUT:
                self.drop(f)  # client stopped reading
                continue
            self._flush(f)

    def _flush(self, fd):
        c = self.clients[fd]
        try:
            n = c[0].send(c[2])
            c[2] = c[2][n:]
        except BlockingIOError:
            pass
        except OSError:
            self.drop(fd)

    def read(self, fd):
        """Commands (dicts) received from a client."""
        c = self.clients.get(fd)
        if not c:
            return []
        try:
            data = c[0].recv(4096)
        except BlockingIOError:
            return []
        except OSError:
            data = b""
        if not data:
            self.drop(fd)
            return []
        c[1] += data
        if len(c[1]) > 16384:
            self.drop(fd)
            return []
        *lines, c[1] = c[1].split(b"\n")
        cmds = []
        for line in lines:
            try:
                m = json.loads(line)
                if isinstance(m, dict):
                    cmds.append(m)
            except ValueError:
                pass
        return cmds

    def close(self):
        for fd in list(self.clients):
            self.drop(fd)
        self.sock.close()
        try:
            os.unlink(self.PATH)
        except OSError:
            pass


class MappingWatcher:
    """Polls the user's mapping.json and returns the active profile when it changes."""

    def __init__(self, path, uid):
        self.path, self.uid, self.sig = path, uid, False
        self.error = None
        self.name = None

    def poll(self):
        """None = unchanged; ('file', name, profile) | ('default', None, None) | ('error', msg, None)."""
        try:
            st = os.lstat(self.path)
            sig = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            sig = None
        if sig == self.sig:
            return None
        self.sig = sig
        if sig is None:
            self.error, self.name = None, None
            return ("default", None, None)
        try:
            m = load_mapping(self.path, self.uid)
        except (OSError, ValueError) as e:
            self.error = str(e)
            return ("error", self.error, None)
        self.error, self.name = None, m["active"]
        return ("file", m["active"], m["profiles"][m["active"]])


FLAG = "/run/vkb-hotas/active"


def node_syspaths(hid_dir):
    return (glob.glob(os.path.join(hid_dir, "input/input*/event*"))
            + glob.glob(os.path.join(hid_dir, "input/input*/js*"))
            + glob.glob(os.path.join(hid_dir, "hidraw/hidraw*")))


def retrigger(hid_dir):
    for sub in node_syspaths(hid_dir):
        subprocess.run(["udevadm", "trigger", "--action=change", sub], check=False)
    subprocess.run(["udevadm", "settle", "-t", "5"], check=False)


def hide(hid_dir, nodes):
    # Flag makes 72-vkb-hotas.rules drop the uaccess tag so logind won't re-add
    # the ACL on login/session switch; chmod handles the already-applied ACL.
    os.makedirs(os.path.dirname(FLAG), exist_ok=True)
    open(FLAG, "w").close()
    retrigger(hid_dir)
    for n in nodes:
        try:
            os.chmod(n, 0o600)  # with POSIX ACLs this zeroes the mask -> uaccess entry inert
            log("hidden real node", n)
        except OSError as e:
            log("could not hide", n, e)


def unhide(hid_dir):
    try:
        os.unlink(FLAG)
    except FileNotFoundError:
        pass
    retrigger(hid_dir)
    log("restored real device permissions")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=default_match(),
                    help="real stick as VID:PID, VID (any product) or /dev/input/eventN "
                         "(default: $VKB_HOTAS_DEVICE, else the only VKB device present)")
    ap.add_argument("--user", default=os.environ.get("VKB_HOTAS_USER", ""),
                    help="desktop user who owns the control socket and ~/.config/vkb-hotas/mapping.json "
                         "(default: $VKB_HOTAS_USER)")
    ap.add_argument("--mapping", help="mapping file (default: ~USER/.config/vkb-hotas/mapping.json)")
    ap.add_argument("--axis", action="append", default=[], metavar="DST=ABS_SRC",
                    help="default-profile axis source, e.g. SLIDER=ABS_RX (used when there is no mapping file)")
    ap.add_argument("--invert", action="append", default=[], metavar="DST", help="default-profile axis inversion")
    ap.add_argument("--buttons", help="default-profile source buttons (codes/names) for emulated buttons 1..N")
    ap.add_argument("--button-count", type=int, choices=(16, 32), default=16,
                    help="default-profile buttons: 16 = faithful T.16000M, 32 = also pass buttons 17-32")
    ap.add_argument("--no-hide", action="store_true", help="don't hide the real stick from games")
    ap.add_argument("--no-grab", action="store_true", help="don't EVIOCGRAB the real stick")
    ap.add_argument("--verbose", action="store_true", help="log every open/close of the virtual stick")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the stick to appear")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("vkb-hotas: must run as root (/dev/uhid is root-only)")

    deadline = time.time() + args.wait
    dev = find_real_device(args.device)
    while dev is None and time.time() < deadline:
        time.sleep(0.5)
        dev = find_real_device(args.device)
    if dev is None:
        log(f"no stick matching {args.device or 'vendor 231d (VKB)'} connected; nothing to do")
        return 0

    uid, home = None, None
    if args.user:
        try:
            pw = pwd.getpwnam(args.user)
            uid, home = pw.pw_uid, pw.pw_dir
        except KeyError:
            log(f"user {args.user!r} not found; control socket will be root-only")
    path = args.mapping or (mapping_path(home) if home else None)

    mapper = Mapper(dev, T16000M)
    axis_map = dict(DEFAULT_AXES)
    for a in args.axis:
        k, v = a.split("=", 1)
        axis_map[k.upper()] = v.upper()
    fallback = default_profile(args.button_count, axis_map, [i.upper() for i in args.invert])
    if args.buttons:
        codes = [int(b, 0) if b[0].isdigit() else E.ecodes[b] for b in args.buttons.split(",")]
        fallback["buttons"] = {str(i + 1): [mapper.btn_num[c]] for i, c in enumerate(codes[:args.button_count])
                               if c in mapper.btn_num}
    mapper.apply(fallback)
    watcher = MappingWatcher(path, uid) if path else None
    profile_name = "built-in default"

    hid_dir, nodes = sibling_nodes(dev)
    log(f"real: {dev.name.strip()} {dev.info.vendor:04x}:{dev.info.product:04x} at {dev.path}; emulating "
        f"{T16000M['name']} {T16000M['vid']:04x}:{T16000M['pid']:04x}; mapping file: {path or 'none'}")

    if not args.no_grab:
        dev.grab()
    if not args.no_hide:
        hide(hid_dir, nodes)

    ctl = ControlServer(uid)
    uhid = Uhid(mapper.spec, args.verbose)
    stop = False

    def on_sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, on_sig)
    signal.signal(signal.SIGINT, on_sig)

    def hello():
        info = {"name": dev.name.strip(), "id": f"{dev.info.vendor:04x}:{dev.info.product:04x}",
                "buttons": len(mapper.btn_num),
                "axes": [{"name": E.ABS[c], "min": ai.min, "max": ai.max} for c, ai in mapper.absinfo.items()]}
        emu = {"name": T16000M["name"], "id": f"{T16000M['vid']:04x}:{T16000M['pid']:04x}",
               "buttons": mapper.spec["buttons"], "axes": {a: mapper.spec["axes"][a] for a in EMU_AXES}}
        return {"t": "hello", "device": info, "emulated": emu, "profile": profile_name,
                "mapping": path, "error": watcher.error if watcher else None}

    last, last_sent, dirty, next_check = None, 0.0, True, 0.0
    try:
        while not stop:
            now = time.monotonic()
            if watcher and now >= next_check:
                next_check = now + 0.5
                change = watcher.poll()
                if change:
                    kind, name, prof = change
                    if kind == "error":
                        log(f"mapping not applied: {name}")
                        ctl.send({"t": "mapping", "ok": False, "error": name, "profile": profile_name})
                    else:
                        prof = prof if kind == "file" else fallback
                        old_count = mapper.spec["buttons"]
                        mapper.apply(prof)
                        profile_name = name if kind == "file" else "built-in default"
                        if mapper.spec["buttons"] != old_count:
                            uhid.destroy()  # descriptor changes: recreate the virtual device
                            uhid = Uhid(mapper.spec, args.verbose)
                            last = None
                        log(f"applied profile: {profile_name}")
                        ctl.send({"t": "mapping", "ok": True, "error": None, "profile": profile_name})
                        ctl.send(hello())
                        dirty = True

            timeout = 500
            if dirty and ctl.clients:
                timeout = max(0, int(33 - (now - last_sent) * 1000))
            if mapper.pulses:
                timeout = min(timeout, 30)
            poller = select.poll()
            poller.register(dev.fd, select.POLLIN)
            poller.register(uhid.fd, select.POLLIN)
            for fd in ctl.fds():
                poller.register(fd, select.POLLIN)

            for fd, mask in poller.poll(timeout):
                if fd == uhid.fd:
                    uhid.handle(last or mapper.report(now))
                elif fd == dev.fd:
                    if mask & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                        raise OSError(errno.ENODEV, "device gone")
                    try:
                        events = list(dev.read())
                    except BlockingIOError:
                        continue
                    for ev in events:
                        if ev.type != E.EV_SYN and mapper.feed(ev):
                            dirty = True
                elif fd == ctl.sock.fileno():
                    new = ctl.accept()
                    if new is not None:
                        ctl.send(hello(), new)
                        dirty = True
                else:
                    for cmd in ctl.read(fd):
                        if cmd.get("cmd") == "pulse" and isinstance(cmd.get("button"), int):
                            mapper.pulse(cmd["button"], time.monotonic())
                        elif cmd.get("cmd") == "hello":
                            ctl.send(hello(), fd)

            now = time.monotonic()
            r = mapper.report(now)
            if r != last:
                uhid.send(r)
                last = r
                dirty = True
            if dirty and ctl.clients and now - last_sent >= 0.033:
                ctl.send(mapper.state_msg(now))
                last_sent, dirty = now, False
            elif not ctl.clients:
                dirty = False
    except OSError as e:
        if e.errno != errno.ENODEV:
            raise
        log("real stick disconnected")
    finally:
        ctl.close()
        uhid.destroy()
        if not args.no_hide:
            if os.path.exists(hid_dir):
                unhide(hid_dir)
            elif os.path.exists(FLAG):
                os.unlink(FLAG)
        try:
            dev.ungrab()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
