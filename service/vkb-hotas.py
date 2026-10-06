#!/usr/bin/env python3
"""
vkb-hotas: re-present real flight sticks to Proton/Wine games as Thrustmaster
devices, using /dev/uhid.

Two roles, each turning one real stick into one emulated device:
  stick    -> Thrustmaster T.16000M (044f:b10a)       VKB_HOTAS_DEVICE   (required)
  throttle -> Thrustmaster TWCS Throttle (044f:b687)  VKB_HOTAS_THROTTLE (optional)
Together they look like Thrustmaster's T.16000M FCS HOTAS set, which games with
HOTAS presets recognise. A role's buttons and axes can come from either stick.

Some games (e.g. Ace Combat 8, Star Wars Squadrons) only enable their
flight-stick code path for HOTAS models they recognise. Under Proton such a
stick is enumerated by DirectInput but the game never opens it. A uhid device
gives Wine a real hidraw node with a Thrustmaster HID descriptor, so the game
sees a device it knows.

While a role is active, its real stick is:
  * EVIOCGRAB'd, so the desktop/Steam stop receiving its evdev events;
  * hidden from user sessions: a runtime udev rule (/run/udev/rules.d) drops
    the uaccess tag of its event/js/hidraw nodes and they are chmod 0600
    (which zeroes the ACL mask), so newly started games only see the virtual one.
A role can be "exposed" instead (VKB_HOTAS_EXPOSE, `vkb-mapper --expose`): its
real stick is released to games untouched and its virtual device removed.
Sticks are re-attached automatically when unplugged and plugged back in.

Runs as root (uhid is root-only); normally started by udev via systemd.
Configuration: /etc/default/vkb-hotas.
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

# vkb_common lives in ../common in the repo and next to this file once installed
_here = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [_here, os.path.join(os.path.dirname(_here), "common")]
from vkb_common import (CONFIG, EMULATED_IDS, EMULATED_NAMES, HAT_DIRS, ROLE_AXES, ROLE_CONFIG,  # noqa: E402
                        ROLE_DEFAULT_AXES, ROLES, SELECTOR, UHID_PHYS, default_profile,
                        default_role_profile, exposed_roles, list_sticks, load_mapping, mapping_path,
                        matches, port_of, role_selectors, selector_for, split_source, vidpid,
                        write_config)

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

HAT_ITEMS = [
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
]


def button_items(n):
    items = [
        0x05, 0x09,        #   Usage Page (Button)
        0x19, 0x01,        #   Usage Minimum (1)
        0x29, n,           #   Usage Maximum (n)
        0x15, 0x00,        #   Logical Minimum (0)
        0x25, 0x01,        #   Logical Maximum (1)
        0x75, 0x01,        #   Report Size (1)
        0x95, n,           #   Report Count (n)
        0x81, 0x02,        #   Input (Data,Var,Abs)
    ]
    if n % 8:
        items += [0x75, 0x01, 0x95, 8 - n % 8, 0x81, 0x01]  # pad to a whole byte
    return items


# ---- Emulated device: Thrustmaster T.16000M -----------------------------------
# Report (9 or 11 bytes): buttons[16|32] | hat:4 pad:4 | X u16 | Y u16 | Rz u8 | Slider u8
def t16000m_rdesc(n):
    return bytes([
        0x05, 0x01,        # Usage Page (Generic Desktop)
        0x09, 0x04,        # Usage (Joystick)
        0xA1, 0x01,        # Collection (Application)
        *button_items(n),
        *HAT_ITEMS,
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
    ])


def t16000m_pack(n, mask, hat, a):
    fmt = "<HBHHBB" if n == 16 else "<IBHHBB"
    return struct.pack(fmt, mask, hat & 0x0F, a["X"], a["Y"], a["RZ"], a["SLIDER"])


# ---- Emulated device: Thrustmaster TWCS Throttle ------------------------------
# Controls of the real TWCS: throttle (Z), mini-stick (X/Y), rocker (Rz), antenna
# (Slider), toe brakes of the optional TFRP pedals (Rx/Ry), 14 buttons, an 8-way hat.
# Report (17 or 19 bytes): buttons[14|32] (+pad) | hat:4 pad:4 | X Y Z Rx Ry Rz Slider, u16 each
TWCS_AXES = ("X", "Y", "Z", "RX", "RY", "RZ", "SLIDER")


def twcs_rdesc(n):
    return bytes([
        0x05, 0x01,        # Usage Page (Generic Desktop)
        0x09, 0x04,        # Usage (Joystick)
        0xA1, 0x01,        # Collection (Application)
        *button_items(n),
        *HAT_ITEMS,
        0x09, 0x30,        #   Usage (X)       mini-stick X
        0x09, 0x31,        #   Usage (Y)       mini-stick Y
        0x09, 0x32,        #   Usage (Z)       throttle
        0x09, 0x33,        #   Usage (Rx)      right toe brake
        0x09, 0x34,        #   Usage (Ry)      left toe brake
        0x09, 0x35,        #   Usage (Rz)      rocker / rudder
        0x09, 0x36,        #   Usage (Slider)  antenna
        0x15, 0x00,        #   Logical Minimum (0)
        0x26, 0xFF, 0x3F,  #   Logical Maximum (16383)
        0x75, 0x10,        #   Report Size (16)
        0x95, 0x07,        #   Report Count (7)
        0x81, 0x02,        #   Input (Data,Var,Abs)
        0xC0,              # End Collection
    ])


def twcs_pack(n, mask, hat, a):
    return (mask.to_bytes((n + 7) // 8, "little") + bytes([hat & 0x0F])
            + struct.pack("<7H", *(a[x] for x in TWCS_AXES)))


def make_spec(role, n):
    """Everything needed to create and feed the emulated device of a role with n buttons."""
    vid, pid = EMULATED_IDS[role]
    if role == "stick":
        return dict(role=role, name=EMULATED_NAMES[role], vid=vid, pid=pid, version=0x0100, buttons=n,
                    axes={"X": 16383, "Y": 16383, "RZ": 255, "SLIDER": 255}, rdesc=t16000m_rdesc(n),
                    pack=lambda mask, hat, a: t16000m_pack(n, mask, hat, a))
    return dict(role=role, name=EMULATED_NAMES[role], vid=vid, pid=pid, version=0x0100, buttons=n,
                axes={x: 16383 for x in TWCS_AXES}, rdesc=twcs_rdesc(n),
                pack=lambda mask, hat, a: twcs_pack(n, mask, hat, a))


# hat (x,y) -> HID hat value, 8 = null (outside logical range)
HAT = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
       (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}


def log(*a):
    print("vkb-hotas:", *a, flush=True)


class Uhid:
    def __init__(self, spec, verbose=False):
        self.verbose = verbose
        self.spec = spec
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_CLOEXEC)
        rd = spec["rdesc"]
        payload = struct.pack(
            "<128s64s64sHHIIII4096s",
            spec["name"].encode(), UHID_PHYS.encode(), b"vkb-hotas-virtual",
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
            log(f"{self.spec['name']}:", {UHID_OPEN: "opened", UHID_CLOSE: "closed",
                                          UHID_START: "uhid started", UHID_STOP: "uhid stopped"}[etype])
        return etype

    def destroy(self):
        try:
            self._write(struct.pack("<I", UHID_DESTROY))
        finally:
            os.close(self.fd)


class PhysState:
    """Live state of one real stick."""

    def __init__(self, dev):
        self.absinfo = {c: ai for c, ai in dev.capabilities(absinfo=True).get(E.EV_ABS, [])
                        if not E.ABS_HAT0X <= c <= E.ABS_HAT3Y}
        # Physical button numbers follow Linux joystick order, which matches the
        # HID button numbers VKBDevCfg shows (for the first 80 or so buttons).
        self.btn_num = {code: i + 1 for i, code in enumerate(sorted(dev.capabilities().get(E.EV_KEY, [])))}
        self.raw = {c: ai.value for c, ai in self.absinfo.items()}
        self.pressed = set()
        self.hat = [0, 0]
        self.name = dev.name.strip()
        self.id = vidpid(dev)

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

    def active(self, src):
        if isinstance(src, int):
            return src in self.pressed
        d = src[4:]
        return ((d == "up" and self.hat[1] < 0) or (d == "down" and self.hat[1] > 0)
                or (d == "left" and self.hat[0] < 0) or (d == "right" and self.hat[0] > 0))

    def fraction(self, name):
        code = E.ecodes.get(name)
        ai = self.absinfo.get(code)
        if ai is None:
            return None
        return (self.raw[code] - ai.min) / max(1, ai.max - ai.min)

    def info(self):
        return {"name": self.name, "id": self.id, "buttons": len(self.btn_num),
                "axes": [{"name": E.ABS[c], "min": ai.min, "max": ai.max} for c, ai in self.absinfo.items()]}

    def raw_msg(self):
        return {"b": sorted(self.pressed), "h": self.hat, "a": {E.ABS[c]: v for c, v in self.raw.items()}}


class Mapper:
    """Real sticks' state -> one emulated device's report, driven by that role's profile section.
    Sources can be on any role's stick (cross-mapping); an absent stick contributes nothing."""

    def __init__(self, role):
        self.role = role
        self.pulses = {}
        self.apply(default_role_profile(role))

    def apply(self, profile):
        self.profile = profile
        self.spec = make_spec(self.role, profile["button_count"])
        self.axes = {dst: (*split_source(self.role, cfg["src"]), cfg["invert"], cfg["deadzone"] / 100)
                     if cfg["src"] else (None, None, False, 0) for dst, cfg in profile["axes"].items()}
        self.buttons = {int(k): [split_source(self.role, s) for s in v]
                        for k, v in profile["buttons"].items() if int(k) <= self.spec["buttons"]}
        self.hatmap = {d: [split_source(self.role, s) for s in v] for d, v in profile["hat"].items()}

    @staticmethod
    def _active(phys, src):
        role, local = src
        p = phys.get(role)
        return p is not None and p.active(local)

    def _axis(self, phys, dst):
        role, name, invert, dz = self.axes.get(dst, (None, None, False, 0))
        top = self.spec["axes"][dst]
        p = phys.get(role) if role else None
        f = p.fraction(name) if p else None
        if f is None:
            return top // 2
        if invert:
            f = 1.0 - f
        if dz:
            c = f * 2 - 1
            m = abs(c)
            c = 0.0 if m < dz else (m - dz) / (1 - dz) * (1 if c > 0 else -1)
            f = (c + 1) / 2
        return max(0, min(top, round(f * top)))

    def output(self, now, phys):
        """phys: {role: PhysState or None}. Returns (button mask, HID hat, {axis: value})."""
        for n in [n for n, t in self.pulses.items() if t <= now]:
            del self.pulses[n]
        mask = 0
        for n in self.pulses:  # test presses from the mapper GUI, even on unassigned slots
            mask |= 1 << (n - 1)
        for n, srcs in self.buttons.items():
            if any(self._active(phys, s) for s in srcs):
                mask |= 1 << (n - 1)
        on = {d: any(self._active(phys, s) for s in self.hatmap.get(d, [])) for d in HAT_DIRS}
        hat = HAT[(on["right"] - on["left"], on["down"] - on["up"])]
        return mask, hat, {a: self._axis(phys, a) for a in ROLE_AXES[self.role]}

    def report(self, now, phys):
        mask, hat, a = self.output(now, phys)
        return self.spec["pack"](mask, hat, a)

    def pulse(self, n, now, length=0.35):
        if 1 <= n <= self.spec["buttons"]:
            self.pulses[n] = now + length


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


# ---- Real devices and hiding ----------------------------------------------------

class DeviceSource:
    """Real evdev sticks. dev/fake-daemon.py swaps in simulated ones."""

    def scan(self):
        return list_sticks()

    def release(self, dev):
        try:
            dev.close()
        except OSError:
            pass

    @staticmethod
    def nodes(dev):
        """{/dev node: sysfs path} of the event/js/hidraw nodes of the same HID device."""
        sys_ev = os.path.realpath(f"/sys/class/input/{os.path.basename(dev.path)}")
        # .../0003:231D:3200.XXXX/input/inputN/eventM -> the HID device dir is 3 up
        hid_dir = os.path.dirname(os.path.dirname(os.path.dirname(sys_ev)))
        out = {}
        for pat in ("input/input*/event*", "input/input*/js*", "hidraw/hidraw*"):
            for s in glob.glob(os.path.join(hid_dir, pat)):
                n = ("/dev/input/" if pat.startswith("input") else "/dev/") + os.path.basename(s)
                if os.path.exists(n):
                    out[n] = s
        return out


class Hider:
    """Hides real sticks from user sessions with a runtime udev rule (dropping uaccess, mode 0600)
    for exactly their nodes, plus chmod 0600 now (which zeroes the already-applied ACL's mask).
    Living in /run/udev/rules.d, it follows live device switches without reinstalling, and
    sorts between 70-uaccess and 73-seat-late like the installed rule."""
    RULES = "/run/udev/rules.d/72-vkb-hotas-hide.rules"
    OLD_FLAG = "/run/vkb-hotas/active"  # used by versions before roles

    def __init__(self):
        self.hidden = {}  # /dev node -> sysfs path

    def apply(self, nodes):
        if nodes == self.hidden:
            return
        changed = {**self.hidden, **nodes}
        if nodes:
            os.makedirs(os.path.dirname(self.RULES), exist_ok=True)
            lines = ["# vkb-hotas: real sticks currently replaced by virtual ones (written by the daemon)"]
            for n in sorted(nodes):
                k = os.path.basename(n)
                sub = "hidraw" if k.startswith("hidraw") else "input"
                lines.append(f'SUBSYSTEM=="{sub}", KERNEL=="{k}", TAG-="uaccess", MODE="0600"')
            tmp = self.RULES + ".tmp"
            with open(tmp, "w") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, self.RULES)
        else:
            self._remove_rules()
        subprocess.run(["udevadm", "control", "--reload"], check=False)
        for s in changed.values():
            subprocess.run(["udevadm", "trigger", "--action=change", s], check=False)
        subprocess.run(["udevadm", "settle", "-t", "5"], check=False)
        for n in nodes:
            try:
                os.chmod(n, 0o600)  # with POSIX ACLs this zeroes the mask -> uaccess entry inert
            except OSError as e:
                log("could not hide", n, e)
        for n in sorted(nodes.keys() - self.hidden.keys()):
            log("hidden real node", n)
        if self.hidden.keys() - nodes.keys():
            log("restored permissions of", ", ".join(sorted(self.hidden.keys() - nodes.keys())))
        self.hidden = dict(nodes)

    def _remove_rules(self):
        for f in (self.RULES, self.OLD_FLAG):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

    @classmethod
    def cleanup(cls):
        """After a crash: drop the runtime rule and re-apply normal permissions everywhere."""
        if not any(os.path.exists(f) for f in (cls.RULES, cls.OLD_FLAG)):
            return
        cls()._remove_rules()
        subprocess.run(["udevadm", "control", "--reload"], check=False)
        subprocess.run(["udevadm", "trigger", "--action=change",
                        "--subsystem-match=input", "--subsystem-match=hidraw"], check=False)
        log("cleaned up the hiding rule")


class Role:
    def __init__(self, name, selector, exposed):
        self.name = name
        # the stick role is always configured ("" = any VKB stick); the throttle is optional
        self.selector = selector if (selector or name == "stick") else None
        self.exposed = exposed
        self.dev = None       # open evdev device while attached
        self.phys = None      # PhysState while attached
        self.nodes = {}
        self.mapper = Mapper(name)
        self.uhid = None      # virtual device while active
        self.last = None      # last report sent
        self.warned = None


class Daemon:
    """The event loop: attaches real sticks to roles, feeds the virtual devices, serves the
    control socket and follows the mapping file. Shared with dev/fake-daemon.py, which passes
    simulated devices, a fake uhid and a no-op hider."""
    SCAN_EVERY = 1.0

    def __init__(self, selectors, exposed, *, ctl, watcher=None, source=None, make_uhid=None,
                 hider=None, persist=None, fallback=None, grab=True, logger=log):
        self.roles = {r: Role(r, selectors.get(r), r in exposed) for r in ROLES}
        self.ctl, self.watcher = ctl, watcher
        self.source = source or DeviceSource()
        self.make_uhid = make_uhid or Uhid
        self.hider = hider
        self.persist = persist          # callable(dict) writing the config file, or None
        self.fallback = fallback or default_profile()
        self.grab = grab
        self.log = logger
        self.profile_name = "built-in default"
        self.devices = []               # connected sticks offered for selection
        self.stop = False
        self.dirty = True
        self.tick = None                # optional callable(now), for the fake daemon
        self.extra_fds = {}             # fd -> callable(), for the fake daemon
        self._apply(self.fallback)

    # -- devices --
    def phys(self):
        return {r: role.phys for r, role in self.roles.items()}

    def attached(self):
        return [role for role in self.roles.values() if role.dev is not None]

    def scan(self):
        devs = self.source.scan()
        busy = {role.dev.path for role in self.attached()}
        listing = [{"selector": selector_for(d, devs), "name": d.name.strip(), "id": vidpid(d),
                    "port": port_of(d), "path": d.path,
                    "role": next((r.name for r in self.attached() if r.dev.path == d.path), None)}
                   for d in devs]
        keep = set()
        for role in self.roles.values():
            if role.dev is not None or role.selector is None:
                continue
            others = [o.selector for o in self.roles.values() if o is not role and o.selector]  # skips ""
            cands = [d for d in devs if d.path not in busy and matches(role.selector, d)]
            # a vague selector ("" / vendor only) never takes a stick another role names exactly
            specific = [d for d in cands if not any(":" in s and matches(s, d) for s in others)]
            cands = specific or cands
            if len(cands) > 1 and role.warned != role.selector:
                self.log(f"{role.name}: {len(cands)} sticks match {role.selector!r}; using the first "
                         f"(add @port to tell them apart)")
                role.warned = role.selector
            if cands:
                self.attach(role, cands[0])
                busy.add(cands[0].path)
                keep.add(id(cands[0]))
                for item in listing:
                    if item["path"] == cands[0].path:
                        item["role"] = role.name
        for d in devs:
            if id(d) not in keep:
                self.source.release(d)
        if listing != self.devices:
            self.devices = listing
            self.send_hello()

    def attach(self, role, dev):
        role.dev, role.phys, role.last = dev, PhysState(dev), None
        role.nodes = self.source.nodes(dev)
        self.log(f"{role.name}: {role.phys.name} {role.phys.id} at {dev.path}"
                 + (" (exposed to games)" if role.exposed else
                    f" -> {role.mapper.spec['name']} {role.mapper.spec['vid']:04x}:{role.mapper.spec['pid']:04x}"))
        if not role.exposed:
            self._grab(role, True)
            self._ensure_uhid(role)
        if role.name == "stick" and self.profile_name == "built-in default":
            self._apply(self.fallback)  # --buttons needs the stick's button numbering
        self._update_hiding()
        self.send_hello()

    def detach(self, role, why):
        if role.dev is None:
            return
        self.log(f"{role.name}: {role.phys.name} {why}")
        self._grab(role, False)
        self.source.release(role.dev)
        role.dev = role.phys = None
        role.nodes = {}
        role.last = None
        self._update_hiding()
        self.send_hello()

    def _grab(self, role, on):
        if not self.grab or role.dev is None:
            return
        try:
            role.dev.grab() if on else role.dev.ungrab()
        except OSError as e:
            if on:
                self.log(f"{role.name}: could not grab {role.dev.path}: {e}")

    def _ensure_uhid(self, role):
        if role.uhid is None and not role.exposed and role.selector is not None:
            role.uhid = self.make_uhid(role.mapper.spec)
            role.last = None

    def _drop_uhid(self, role):
        if role.uhid is not None:
            role.uhid.destroy()
            role.uhid = None

    def _update_hiding(self):
        if self.hider is None:
            return
        nodes = {}
        for role in self.attached():
            if not role.exposed:
                nodes.update(role.nodes)
        self.hider.apply(nodes)

    # -- mapping --
    def _apply(self, profile):
        for r, role in self.roles.items():
            old = role.mapper.spec["buttons"]
            role.mapper.apply(profile[r])
            if role.uhid is not None and role.mapper.spec["buttons"] != old:
                self._drop_uhid(role)  # descriptor changes: recreate the virtual device
                self._ensure_uhid(role)

    def poll_mapping(self):
        change = self.watcher.poll() if self.watcher else None
        if not change:
            return
        kind, name, prof = change
        if kind == "error":
            self.log(f"mapping not applied: {name}")
            self.ctl.send({"t": "mapping", "ok": False, "error": name, "profile": self.profile_name})
            return
        self.profile_name = name if kind == "file" else "built-in default"
        self._apply(prof if kind == "file" else self.fallback)
        self.log(f"applied profile: {self.profile_name}")
        self.ctl.send({"t": "mapping", "ok": True, "error": None, "profile": self.profile_name})
        self.send_hello()

    # -- commands --
    def select(self, role_name, selector):
        """Point a role at another stick (or none, for the throttle). Picking the stick the other
        role uses swaps them. Persisted to the config file."""
        if role_name not in self.roles:
            raise ValueError(f"unknown role {role_name!r}")
        if selector is not None and not (isinstance(selector, str) and SELECTOR.match(selector)):
            raise ValueError("device must be a selector like 231d:3200 or 231d:3200@usb-...")
        if selector is None and role_name == "stick":
            raise ValueError("the stick role needs a device")
        role = self.roles[role_name]
        other = next(o for o in self.roles.values() if o is not role)
        if selector is not None and not any(d["selector"] == selector for d in self.devices):
            raise ValueError(f"{selector} is not connected")
        new = {role.name: selector, other.name: other.selector}
        if selector is not None and other.dev is not None and matches(selector, other.dev):
            new[other.name] = role.selector  # swap
            if new[other.name] is None and other.name == "stick":
                raise ValueError("that stick is the stick role's; give the stick role another device first")
        for r in self.roles.values():
            if new[r.name] != r.selector:
                self.detach(r, "released (device selection changed)")
                r.selector, r.warned = new[r.name], None
                if r.selector is None:
                    self._drop_uhid(r)
        self._persist({ROLE_CONFIG[r]: self.roles[r].selector or "" for r in ROLES})
        self.scan()
        self.send_hello()

    def expose(self, role_name, on):
        """Give a role's real stick to games (no grab, not hidden, virtual device removed) or take it back."""
        targets = list(self.roles.values()) if role_name == "all" else [self.roles.get(role_name)]
        if None in targets:
            raise ValueError(f"unknown role {role_name!r}")
        for role in targets:
            if role.exposed == on:
                continue
            role.exposed = on
            if on:
                self._grab(role, False)
                self._drop_uhid(role)
            else:
                self._grab(role, True)
                if role.dev is not None:
                    self._ensure_uhid(role)
            self.log(f"{role.name}: " + ("real stick exposed to games" if on else "real stick hidden and emulated again"))
        self._update_hiding()
        self._persist({"VKB_HOTAS_EXPOSE": " ".join(r for r in ROLES if self.roles[r].exposed)})
        self.send_hello()

    def _persist(self, updates):
        if self.persist:
            try:
                self.persist(updates)
            except (OSError, ValueError) as e:
                self.log(f"could not save {', '.join(updates)} to the config file: {e}")

    def command(self, cmd, fd):
        c = cmd.get("cmd")
        if c == "hello":
            self.ctl.send(self.hello(), fd)
            return
        if c == "pulse" and isinstance(cmd.get("button"), int):
            role = self.roles.get(cmd.get("role", "stick"))
            if role:
                role.mapper.pulse(cmd["button"], time.monotonic())
            return
        try:
            if c == "select":
                self.select(cmd.get("role"), cmd.get("device"))
            elif c == "expose":
                self.expose(cmd.get("role"), bool(cmd.get("on")))
            else:
                return
            self.ctl.send({"t": "result", "cmd": c, "ok": True, "error": None}, fd)
        except ValueError as e:
            self.ctl.send({"t": "result", "cmd": c, "ok": False, "error": str(e)}, fd)

    # -- messages --
    def hello(self):
        roles = {}
        for r, role in self.roles.items():
            spec = role.mapper.spec
            roles[r] = {"selector": role.selector, "exposed": role.exposed,
                        "device": role.phys.info() if role.phys else None,
                        "emulated": {"name": spec["name"], "id": f"{spec['vid']:04x}:{spec['pid']:04x}",
                                     "buttons": spec["buttons"], "axes": dict(spec["axes"]),
                                     "active": role.uhid is not None}}
        return {"t": "hello", "roles": roles, "devices": self.devices, "profile": self.profile_name,
                "mapping": self.watcher.path if self.watcher else None,
                "error": self.watcher.error if self.watcher else None}

    def send_hello(self):
        if self.ctl.clients:
            self.ctl.send(self.hello())
        self.dirty = True

    def state_msg(self, now):
        phys, roles = self.phys(), {}
        for r, role in self.roles.items():
            mask, hat, a = role.mapper.output(now, phys)
            roles[r] = {"raw": role.phys.raw_msg() if role.phys else None, "out": {"b": mask, "h": hat, "a": a}}
        return {"t": "state", "roles": roles}

    # -- loop --
    def wait_for_sticks(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.scan()
            if self.attached() or time.monotonic() >= deadline:
                return bool(self.attached())
            time.sleep(0.5)

    def run(self):
        last_sent, next_check, next_scan = 0.0, 0.0, time.monotonic() + self.SCAN_EVERY
        try:
            while not self.stop:
                now = time.monotonic()
                if now >= next_check:
                    next_check = now + 0.5
                    self.poll_mapping()
                if now >= next_scan:
                    next_scan = now + self.SCAN_EVERY
                    self.scan()
                if self.tick:
                    self.tick(now)

                timeout = 200
                if self.dirty and self.ctl.clients:
                    timeout = max(0, int(33 - (now - last_sent) * 1000))
                if any(role.mapper.pulses for role in self.roles.values()):
                    timeout = min(timeout, 30)
                poller, owner = select.poll(), {}
                for role in self.roles.values():
                    if role.dev is not None:
                        poller.register(role.dev.fd, select.POLLIN)
                        owner[role.dev.fd] = ("dev", role)
                    if role.uhid is not None:
                        poller.register(role.uhid.fd, select.POLLIN)
                        owner[role.uhid.fd] = ("uhid", role)
                for fd in [*self.ctl.fds(), *self.extra_fds]:
                    poller.register(fd, select.POLLIN)

                for fd, mask in poller.poll(timeout):
                    kind, role = owner.get(fd, (None, None))
                    if kind == "uhid":
                        role.uhid.handle(role.last or role.mapper.report(now, self.phys()))
                    elif kind == "dev":
                        if role.dev is None or role.dev.fd != fd:
                            continue  # detached earlier in this batch
                        if mask & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                            self.detach(role, "disconnected")
                            continue
                        try:
                            events = list(role.dev.read())
                        except BlockingIOError:
                            continue
                        except OSError as e:
                            if e.errno != errno.ENODEV:
                                raise
                            self.detach(role, "disconnected")
                            continue
                        for ev in events:
                            if ev.type != E.EV_SYN and role.phys.feed(ev):
                                self.dirty = True
                    elif fd in self.extra_fds:
                        self.extra_fds[fd]()
                    elif fd == self.ctl.sock.fileno():
                        new = self.ctl.accept()
                        if new is not None:
                            self.ctl.send(self.hello(), new)
                            self.dirty = True
                    else:
                        for cmd in self.ctl.read(fd):
                            self.command(cmd, fd)

                now = time.monotonic()
                phys = self.phys()
                for role in self.roles.values():
                    if role.uhid is None:
                        continue
                    r = role.mapper.report(now, phys)
                    if r != role.last:
                        role.uhid.send(r)
                        role.last = r
                        self.dirty = True
                if self.dirty and self.ctl.clients and now - last_sent >= 0.033:
                    self.ctl.send(self.state_msg(now))
                    last_sent, self.dirty = now, False
                elif not self.ctl.clients:
                    self.dirty = False
        finally:
            self.shutdown()

    def shutdown(self):
        self.ctl.close()
        for role in self.roles.values():
            self._drop_uhid(role)
            self._grab(role, False)
        if self.hider:
            self.hider.apply({})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = role_selectors()
    ap.add_argument("--device", default=sel["stick"],
                    help="real stick for the T.16000M: VID:PID[@port], VID (any product) or /dev/input/eventN "
                         "(default: $VKB_HOTAS_DEVICE, else the only VKB device present)")
    ap.add_argument("--throttle", default=sel["throttle"],
                    help="real stick for the TWCS Throttle, as VID:PID[@port] (default: $VKB_HOTAS_THROTTLE; empty = none)")
    ap.add_argument("--user", default=os.environ.get("VKB_HOTAS_USER", ""),
                    help="desktop user who owns the control socket and ~/.config/vkb-hotas/mapping.json "
                         "(default: $VKB_HOTAS_USER)")
    ap.add_argument("--mapping", help="mapping file (default: ~USER/.config/vkb-hotas/mapping.json)")
    ap.add_argument("--axis", action="append", default=[], metavar="DST=ABS_SRC",
                    help="default-profile T.16000M axis source, e.g. SLIDER=ABS_RX (used when there is no mapping file)")
    ap.add_argument("--invert", action="append", default=[], metavar="DST", help="default-profile axis inversion")
    ap.add_argument("--buttons", help="default-profile source buttons (codes/names) for T.16000M buttons 1..N")
    ap.add_argument("--button-count", type=int, choices=(16, 32), default=16,
                    help="default-profile T.16000M buttons: 16 = faithful, 32 = also pass buttons 17-32")
    ap.add_argument("--no-hide", action="store_true", help="don't hide the real sticks from games")
    ap.add_argument("--no-grab", action="store_true", help="don't EVIOCGRAB the real sticks")
    ap.add_argument("--verbose", action="store_true", help="log every open/close of the virtual devices")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for a stick to appear")
    ap.add_argument("--cleanup", action="store_true",
                    help="remove a hiding rule left behind by a crash, then exit (systemd ExecStopPost)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("vkb-hotas: must run as root (/dev/uhid is root-only)")
    if args.cleanup:
        Hider.cleanup()
        return 0

    uid, home = None, None
    if args.user:
        try:
            pw = pwd.getpwnam(args.user)
            uid, home = pw.pw_uid, pw.pw_dir
        except KeyError:
            log(f"user {args.user!r} not found; control socket will be root-only")
    path = args.mapping or (mapping_path(home) if home else None)

    axis_map = dict(ROLE_DEFAULT_AXES["stick"])
    for a in args.axis:
        k, v = a.split("=", 1)
        axis_map[k.upper()] = v.upper()
    fallback = default_profile(args.button_count, axis_map, [i.upper() for i in args.invert])
    Hider.cleanup()  # a previous instance may have crashed
    ctl = ControlServer(uid)
    daemon = Daemon({"stick": args.device, "throttle": args.throttle}, exposed_roles(), ctl=ctl,
                    watcher=MappingWatcher(path, uid) if path else None, fallback=fallback,
                    hider=None if args.no_hide else Hider(), grab=not args.no_grab,
                    make_uhid=lambda spec: Uhid(spec, args.verbose),
                    persist=lambda updates: write_config(updates, CONFIG))
    if args.buttons:
        codes = [int(b, 0) if b[0].isdigit() else E.ecodes[b] for b in args.buttons.split(",")]
        orig_attach = daemon.attach

        def attach(role, dev):  # map --buttons codes to this stick's physical button numbers
            if role.name == "stick":
                nums = PhysState(dev).btn_num
                fallback["stick"]["buttons"] = {str(i + 1): [nums[c]] for i, c in enumerate(codes[:args.button_count])
                                                if c in nums}
            orig_attach(role, dev)
        daemon.attach = attach

    log(f"emulating {', '.join(EMULATED_NAMES[r] for r in ROLES if daemon.roles[r].selector is not None)}; "
        f"mapping file: {path or 'none'}")
    if not daemon.wait_for_sticks(args.wait):
        wanted = ", ".join(f"{r}={s.selector or 'any VKB stick'}" for r, s in daemon.roles.items() if s.selector is not None)
        log(f"no configured stick connected ({wanted}); nothing to do")
        daemon.shutdown()
        return 0

    def on_sig(*_):
        daemon.stop = True
    signal.signal(signal.SIGTERM, on_sig)
    signal.signal(signal.SIGINT, on_sig)
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
