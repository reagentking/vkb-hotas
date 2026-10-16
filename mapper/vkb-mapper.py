#!/usr/bin/env python3
"""
vkb-mapper: graphical button/axis remapper for vkb-hotas.

Opens a local web UI in your browser. It shows your sticks' live input, and
lets you choose which physical button drives each button / hat direction of the
emulated T.16000M (and TWCS Throttle, with a second stick), plus axis sources,
inversion and deadzones, and which real stick feeds each one. Changes are saved
to ~/.config/vkb-hotas/mapping.json and applied by the running daemon within
half a second, even while a game is running.

  vkb-mapper.py [--port N] [--no-browser]
  vkb-mapper.py --expose stick|throttle|all   give the real stick(s) to games as they are
  vkb-mapper.py --xbox stick|throttle|all     make them feed a virtual Xbox 360 controller instead
  vkb-mapper.py --hide stick|throttle|all     back to the virtual Thrustmaster devices
  vkb-mapper.py --status                      show which stick feeds which role

No extra packages needed. The server listens on 127.0.0.1 only, requires a
per-session token, and exits shortly after the last browser tab closes.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
# vkb_common lives in ../common in the repo and next to this file once installed
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "common")]
from vkb_common import (DEFAULT_AXES, EMU_AXES, HAT_DIRS, MODES, ROLE_AXES, ROLES, XBOX_BUTTONS,  # noqa: E402
                        default_profile, load_mapping, mapping_path, normalize_mapping, read_config)

CTL = os.environ.get("VKB_HOTAS_CTL", "/run/vkb-hotas/ctl.sock")
PAGE = os.path.join(HERE, "vkb-mapper.html")
GAME_DEFAULTS = os.path.join(HERE, "game-defaults.json")
STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vkb-mapper")
IDLE_EXIT = 45  # seconds without any open UI before the server quits
API_VERSION = 16  # bump when the page needs server features; the page refuses to save anything on a mismatch


def initial_mapping():
    """First-run mapping that mirrors the daemon's VKB_HOTAS_ARGS defaults."""
    args = shlex.split(read_config().get("VKB_HOTAS_ARGS", ""))
    count, axes, invert = 16, dict(DEFAULT_AXES), []
    for i, a in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a == "--button-count" and nxt in ("16", "32"):
            count = int(nxt)
        elif a == "--invert" and nxt.upper() in EMU_AXES:
            invert.append(nxt.upper())
        elif a == "--axis" and "=" in nxt:
            k, v = nxt.split("=", 1)
            if k.upper() in EMU_AXES:
                axes[k.upper()] = v.upper()
    return normalize_mapping({"active": "Default", "profiles": {"Default": default_profile(count, axes, invert)}})


# ---- Stick layout (pictures + pin positions) ------------------------------------
# layout.json lives next to mapping.json. It describes the hardware, not a game, so
# it's shared by all profiles and the daemon never reads it. One section per role
# (the real stick behind the T.16000M, and the one behind the TWCS Throttle):
#   {"version": 7, "devices": {"stick": DEVICE, "throttle": DEVICE}}
# where DEVICE is
#   {"views": [{"id": "stick", "name": "Whole stick", "schematic": "evo-ot-side", "image": null | "x-1a2b3c4d.jpg"}],
#    "hats":  [{"id": "c1", "name": "Hat 2", "dirs": {"up": 11, "right": 12, "down": 13, "left": 14, "center": 15}}],
#    "pins":  {"draw:evo-ot-side": {"17": [0.42, 0.31], "hat": [0.5, 0.12], "c:c1": [0.7, 0.1]},
#              "photo:x-1a2b3c4d.jpg": {...}},
#    "names": {"1": "Trigger stage 1", "21": "Rapid-fire up"}}
# Pins are anchored to the *picture* (a drawing or a photo), not to the view showing it, so
# switching a view's drawing shows that drawing's own pins and never misplaces them. Pin keys:
# physical button numbers ("1".."128"), "hat" (the stick's real hat) or "c:<hat id>" (where a
# 5-way hat sits on that picture); coordinates are 0..1 fractions.
# hats are multi-way hats that report as separate buttons (VKB's 4-way hats with center push);
# they're hardware, defined once and placeable on any picture; each direction is learned by
# pressing it. Names are the user's labels for physical buttons, shown wherever a button appears.
# Drawings ending in "-l" are the mirrored (left-hand) versions of the EVO drawings.
# Version 1 kept pins and hats per view; version 2 was one DEVICE at the top level (the
# stick role's). normalize_layout converts both.
EVO = ("evo-ot-side", "evo-ot-straight", "evo-scg-head", "evo-scg-side", "evo-base-front")
SCHEMATICS = (*EVO, *(s + "-l" for s in EVO), "grip-front", "grip-back", "base")
CLUSTER_DIRS = ("up", "right", "down", "left", "center")


def default_device_layout(role):
    l = "-l" if role == "throttle" else ""
    return {"views": [
        {"id": "stick", "name": "Whole stick", "schematic": "evo-ot-side" + l, "image": None},
        {"id": "head", "name": "Grip head", "schematic": "evo-scg-head" + l, "image": None},
        {"id": "side", "name": "Triggers", "schematic": "evo-scg-side" + l, "image": None},
        {"id": "base", "name": "Base front", "schematic": "evo-base-front" + l, "image": None}],
        "hats": [], "pins": {}, "names": {}}


DEFAULT_LAYOUT = {"version": 7, "devices": {r: default_device_layout(r) for r in ROLES}}
IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}
IMAGE_MAX = 10 << 20
IMAGE_NAME = re.compile(r"^[a-z0-9_-]{1,24}-[0-9a-f]{8}\.(png|jpg|webp)$")
VIEW_ID = re.compile(r"^[a-z0-9_-]{1,24}$")


def image_kind(data):
    for magic, ext in IMAGE_TYPES.items():
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def picture_key(view):
    return f"photo:{view['image']}" if view["image"] else f"draw:{view['schematic']}"


def _xy(xy, what):
    if not (isinstance(xy, list) and len(xy) == 2
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1 for c in xy)):
        raise ValueError(f"{what}: coordinates must be two numbers 0..1")
    return [round(xy[0], 4), round(xy[1], 4)]


def _migrate_v1(lay, views):
    """v1 kept pins/clusters per view id: re-anchor them to each view's current picture."""
    by_id = {v["id"]: picture_key(v) for v in views}
    pins, hats = {}, []
    for vid, vp in (lay.get("pins") or {}).items():
        if vid in by_id and isinstance(vp, dict):
            pins.setdefault(by_id[vid], {}).update(vp)
    for vid, cl in (lay.get("clusters") or {}).items():
        for c in cl if isinstance(cl, list) else []:
            if isinstance(c, dict):
                hats.append({k: c.get(k) for k in ("id", "name", "dirs")})
                if vid in by_id and c.get("xy") is not None:
                    pins.setdefault(by_id[vid], {})[f"c:{c.get('id')}"] = c["xy"]
    return pins, hats


# The whole-stick drawing (evo-ot-side) changed twice; pins on its grip move with it, pins on the
# base (below OT_BASE_TOP) never do:
#  - before version 4 the bracket and grip were drawn on the left-hand side, from a left-hand grip
#    drawing. Since version 6 that assembly is mirrored as a whole (right-hand OT, right-hand grip),
#    so grip pins just mirror: x -> 1 - x, on the "-l" drawing too.
#  - versions 4 and 5 mirrored the assembly but kept the grip's details unmirrored (inner
#    scale(-1 1)), leaving the red button on the wrong side (the user reported it); converting from
#    them flips each pin within the grip again (_flip_ot_grip_pin).
OT_GRIP_ORIGIN, OT_GRIP_ANGLE, OT_BASE_TOP = (135, 238), math.radians(38), 330  # drawing units (400x500)


def _mirror_ot_grip_pin(fx, fy, mirrored):
    return [round(1 - fx, 4), fy] if fy * 500 < OT_BASE_TOP else [fx, fy]


def _flip_ot_grip_pin(fx, fy, mirrored):
    """p -> M(T(S(T^-1(M(p))))) for a pin on the grip (M = whole-picture mirror, S = grip-local
    mirror, T = the grip's placement in the original drawing)."""
    x, y = fx * 400, fy * 500
    if y >= OT_BASE_TOP:
        return [fx, fy]
    if mirrored:
        x = 400 - x
    x = 400 - x  # undo the drawing's outer mirror
    ox, oy = OT_GRIP_ORIGIN
    c, s = math.cos(OT_GRIP_ANGLE), math.sin(OT_GRIP_ANGLE)
    dx, dy = x - ox, y - oy
    gx, gy = c * dx + s * dy, -s * dx + c * dy  # into the grip's own coordinates
    gx = -gx
    x, y = ox + c * gx - s * gy, oy + s * gx + c * gy
    x = 400 - x
    if mirrored:
        x = 400 - x
    return [round(min(1, max(0, x / 400)), 4), round(min(1, max(0, y / 500)), 4)]


# Version 7 moved the grip's thumb-side bump (mini-stick) to the other side of the grip on the
# whole-stick drawing (the user's correction); pins on it move along: a mirror within the grip about
# local x = 1. Applied to version-6 coordinates (grip = the original assembly mirrored as a whole).
OT_BUMP, OT_BUMP_RADIUS = (-47, -128), 40  # grip coordinates, before the move


def _move_ot_bump_pin(fx, fy, mirrored):
    x, y = fx * 400, fy * 500
    if mirrored:
        x = 400 - x
    x = 400 - x  # undo the outer mirror: now in the original drawing's coordinates
    ox, oy = OT_GRIP_ORIGIN
    c, s = math.cos(OT_GRIP_ANGLE), math.sin(OT_GRIP_ANGLE)
    dx, dy = x - ox, y - oy
    gx, gy = c * dx + s * dy, -s * dx + c * dy
    if math.hypot(gx - OT_BUMP[0], gy - OT_BUMP[1]) > OT_BUMP_RADIUS:
        return [fx, fy]
    gx = 2 - gx
    x, y = ox + c * gx - s * gy, oy + s * gx + c * gy
    x = 400 - x
    if mirrored:
        x = 400 - x
    return [round(min(1, max(0, x / 400)), 4), round(min(1, max(0, y / 500)), 4)]


def _migrate_ot_drawing(dev, move):
    for key, mirrored in (("draw:evo-ot-side", False), ("draw:evo-ot-side-l", True)):
        for k, (fx, fy) in list(dev["pins"].get(key, {}).items()):
            dev["pins"][key][k] = move(fx, fy, mirrored)
    return dev


# Before layout version 5 the SCG grip head drawing was a left-hand grip (red button bottom-right);
# version 5 mirrors it, so its pins (and those of its "-l" mirror) are mirrored too.
def _migrate_scg_head(dev):
    for key in ("draw:evo-scg-head", "draw:evo-scg-head-l"):
        for k, (fx, fy) in list(dev["pins"].get(key, {}).items()):
            dev["pins"][key][k] = [round(1 - fx, 4), fy]
    return dev


def normalize_layout(lay):
    """Validate a whole layout (version 7: one section per role); older versions are converted:
    versions 1/2 become the stick's section, whole-stick grip pins move with the grip (before 6)
    and with its thumb-side bump (before 7), and SCG grip head pins are mirrored (before 5)."""
    if not isinstance(lay, dict):
        raise ValueError("layout must be an object")
    old = lay.get("version", 1)
    if old < 3 and "devices" not in lay:
        lay = {"version": 3, "devices": {"stick": normalize_device_layout(lay, old < 2),
                                         "throttle": default_device_layout("throttle")}}
    devs = lay.get("devices")
    if not isinstance(devs, dict) or not set(devs) <= set(ROLES):
        raise ValueError("layout 'devices' must be an object with stick / throttle sections")
    out = {}
    for r in ROLES:
        try:
            out[r] = normalize_device_layout(devs[r]) if r in devs else default_device_layout(r)
        except ValueError as e:
            raise ValueError(f"{r}: {e}") from None
    names = [v["image"] for d in out.values() for v in d["views"] if v["image"]]
    if len(names) != len(set(names)):
        raise ValueError("a photo can belong to one view only")
    if old < 6:
        move = _mirror_ot_grip_pin if old < 4 else _flip_ot_grip_pin
        out = {r: _migrate_ot_drawing(d, move) for r, d in out.items()}
    if old < 5:
        out = {r: _migrate_scg_head(d) for r, d in out.items()}
    if old < 7:
        out = {r: _migrate_ot_drawing(d, _move_ot_bump_pin) for r, d in out.items()}
    return {"version": 7, "devices": out}


def layout_images(lay):
    return {v["image"] for d in lay["devices"].values() for v in d["views"] if v["image"]}


def normalize_device_layout(lay, v1=False):
    """One role's section (or a whole version 1/2 layout; v1=True converts version 1)."""
    if not isinstance(lay, dict) or not isinstance(lay.get("views"), list) or not lay["views"]:
        raise ValueError("layout needs a non-empty 'views' list")
    if len(lay["views"]) > 8:
        raise ValueError("at most 8 views")
    views, ids = [], set()
    for v in lay["views"]:
        if not isinstance(v, dict) or not VIEW_ID.match(str(v.get("id", ""))) or v["id"] in ids:
            raise ValueError("each view needs a unique id (a-z, 0-9, _ -)")
        name = str(v.get("name") or v["id"]).strip()[:30]
        sch = v.get("schematic") if v.get("schematic") in SCHEMATICS else SCHEMATICS[0]
        img = v.get("image")
        if img is not None and not (isinstance(img, str) and IMAGE_NAME.match(img)):
            raise ValueError(f"view {v['id']}: bad image name")
        ids.add(v["id"])
        views.append({"id": v["id"], "name": name, "schematic": sch, "image": img})

    if v1 or "clusters" in lay:
        raw_pins, raw_hats = _migrate_v1(lay, views)
    else:
        raw_pins, raw_hats = lay.get("pins") or {}, lay.get("hats") or []

    if not isinstance(raw_hats, list) or len(raw_hats) > 32:
        raise ValueError("hats must be a list of at most 32")
    hats, hat_ids, hat_buttons = [], set(), set()
    for c in raw_hats:
        if not isinstance(c, dict) or not VIEW_ID.match(str(c.get("id", ""))) or c["id"] in hat_ids:
            raise ValueError("each 5-way hat needs a unique id")
        dirs = c.get("dirs")
        if not isinstance(dirs, dict) or len(dirs) < 2 or not set(dirs) <= set(CLUSTER_DIRS):
            raise ValueError(f"hat {c['id']}: needs at least 2 of {', '.join(CLUSTER_DIRS)}")
        if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 128 for n in dirs.values()):
            raise ValueError(f"hat {c['id']}: directions must be button numbers 1-128")
        hat_ids.add(c["id"])
        hat_buttons.update(dirs.values())
        hats.append({"id": c["id"], "name": str(c.get("name") or "Hat").strip()[:24],
                     "dirs": {d: dirs[d] for d in CLUSTER_DIRS if d in dirs}})

    if not isinstance(raw_pins, dict):
        raise ValueError("pins must be an object")
    photos = {v["image"] for v in views if v["image"]}
    pins = {}
    for pk, vp in raw_pins.items():
        kind, _, ref = str(pk).partition(":")
        valid = (kind == "draw" and ref in SCHEMATICS) or (kind == "photo" and IMAGE_NAME.match(ref))
        if not valid or not isinstance(vp, dict):
            raise ValueError(f"pins: {pk!r} is not a picture key (draw:<drawing> or photo:<file>)")
        if kind == "photo" and ref not in photos:
            continue  # photo removed (its file is deleted too)
        clean = {}
        for key, xy in vp.items():
            if key.startswith("c:"):
                if key[2:] not in hat_ids:
                    continue  # position of a deleted hat
            elif key == "hat":
                pass
            elif key.isdigit() and 1 <= int(key) <= 128:
                if int(key) in hat_buttons:
                    continue  # a button is drawn as part of its hat, never as its own pin
            else:
                raise ValueError(f"pin {key!r}: must be a button number, 'hat' or 'c:<hat id>'")
            clean[key] = _xy(xy, f"pin {key!r}")
        if clean:
            pins[pk] = clean

    names = {}
    raw_names = lay.get("names") or {}
    if not isinstance(raw_names, dict):
        raise ValueError("names must be an object")
    for key, name in raw_names.items():
        if not (str(key).isdigit() and 1 <= int(key) <= 128):
            raise ValueError(f"names: {key!r} is not a button number 1-128")
        name = str(name).strip()[:32]
        if name:
            names[str(int(key))] = name
    return {"views": views, "hats": hats, "pins": pins, "names": names}


def backup_layout(old_version):
    """Keep the file as it was before a format conversion (once per old version)."""
    bak = f"{State.layout_path()}.bak-v{old_version}"
    if not os.path.exists(bak):
        with State.lock:
            shutil.copy2(State.layout_path(), bak)


# Game defaults: what each emulated control does by default in a game (display only).
#   shipped: game-defaults.json next to the page, {"version": 1, "games": {id: SET}}
#   user:    game-defaults/<id>.json next to the mapping file, one SET each, ids "u-..." so they
#            never collide with shipped ones. Export/import use the same single-set file:
#   SET = {"version": 1, "id": ..., "name": ..., "source": ..., "verified": bool,
#          "stick": {"buttons": {"N": text}, "hat": {dir: text}, "axes": {AXIS: text}}, "throttle": {...}}
REF_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
USER_REF_ID = re.compile(r"^u-[a-z0-9][a-z0-9-]{0,37}$")
REF_MAX_BYTES = 64 << 10


def normalize_reference(g, strict=False):
    """Clean one SET. strict (user files and uploads): reject unknown controls instead of dropping them."""
    if not isinstance(g, dict):
        raise ValueError("a game defaults set must be an object")
    name = str(g.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("a game defaults set needs a name")
    out = {"name": name, "source": str(g.get("source") or "").strip()[:400], "verified": bool(g.get("verified"))}
    valid = {"buttons": lambda k: k.isdigit() and 1 <= int(k) <= 32, "hat": lambda k: k in HAT_DIRS}
    for role in ROLES:
        sec = g.get(role) if isinstance(g.get(role), dict) else {}
        out[role] = {}
        for part in ("buttons", "hat", "axes"):
            ok = valid.get(part) or (lambda k, r=role: k in ROLE_AXES[r])
            items = sec.get(part) or {}
            if not isinstance(items, dict):
                raise ValueError(f"{role}.{part} must be an object")
            clean = {}
            for k, v in items.items():
                k, v = str(k), str(v).strip()[:60]
                if not ok(k):
                    if strict:
                        raise ValueError(f"{role}.{part}: {k!r} is not a control of the emulated device")
                    continue
                if v:
                    clean[str(int(k)) if part == "buttons" else k] = v
            out[role][part] = clean
    return out


def user_ref_dir():
    return os.path.join(os.path.dirname(State.path), "game-defaults")


def load_game_defaults():
    """All game default sets: shipped plus the user's own, each with "origin"."""
    out = {}
    raw = json.load(open(GAME_DEFAULTS))
    for gid, g in (raw.get("games") or {}).items():
        if REF_ID.match(str(gid)) and not USER_REF_ID.match(str(gid)):
            try:
                out[gid] = {**normalize_reference(g), "origin": "shipped"}
            except ValueError:
                pass
    d = user_ref_dir()
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        gid = f[:-5]
        if not (f.endswith(".json") and USER_REF_ID.match(gid)):
            continue
        try:
            with open(os.path.join(d, f)) as fh:
                out[gid] = {**normalize_reference(json.loads(fh.read(REF_MAX_BYTES))), "origin": "user"}
        except (OSError, ValueError):
            pass  # a broken file just doesn't show up
    return out


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


class State:
    token = secrets.token_urlsafe(24)
    path = mapping_path()

    @classmethod
    def layout_path(cls):
        return os.path.join(os.path.dirname(cls.path), "layout.json")

    @classmethod
    def image_dir(cls):
        return os.path.join(os.path.dirname(cls.path), "images")
    clients = 0
    last_seen = time.monotonic()
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "vkb-mapper"

    def log_message(self, *a):
        pass

    # -- helpers --
    def _host_ok(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, query):
        tok = self.headers.get("X-Token") or query.get("t", [""])[0]
        return hmac.compare_digest(tok.encode(), State.token.encode())

    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if not self._host_ok():
            self._send(403, {"error": "bad host"})
            return None
        if not self._token_ok(q):
            self._send(403, {"error": "bad token"})
            return None
        return url.path

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"null")

    # -- routes --
    def do_GET(self):
        path = self._guard()
        if path is None:
            return
        if path == "/":
            html = open(PAGE, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(html)
        elif path == "/api/mapping":
            exists = os.path.exists(State.path)
            try:
                m = load_mapping(State.path) if exists else initial_mapping()
                err = None
            except (OSError, ValueError) as e:
                m, err = initial_mapping(), str(e)
            self._send(200, {"mapping": m, "path": State.path, "exists": exists, "error": err})
        elif path == "/api/layout":
            try:
                raw = json.load(open(State.layout_path()))
                lay = normalize_layout(raw)
                err = None
                if isinstance(raw, dict) and raw.get("version", 1) < lay["version"]:
                    backup_layout(raw.get("version", 1))
            except FileNotFoundError:
                lay, err = DEFAULT_LAYOUT, None
            except (OSError, ValueError) as e:
                lay, err = DEFAULT_LAYOUT, str(e)
            self._send(200, {"layout": lay, "error": err, "api": API_VERSION})
        elif path == "/api/references":
            try:
                self._send(200, {"references": load_game_defaults(), "error": None})
            except (OSError, ValueError) as e:
                self._send(200, {"references": {}, "error": str(e)})
        elif path.startswith("/api/image/"):
            name = path.rsplit("/", 1)[1]
            if not IMAGE_NAME.match(name):
                return self._send(404, {"error": "not found"})
            try:
                data = open(os.path.join(State.image_dir(), name), "rb").read()
            except OSError:
                return self._send(404, {"error": "not found"})
            ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[name.rsplit(".", 1)[1]]
            self._send(200, data, ctype)
        elif path == "/api/events":
            self._events()
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self):
        path = self._guard()
        if path is None:
            return
        if path == "/api/layout":
            return self._put_layout()
        if path.startswith("/api/references/"):
            return self._put_reference(path.rsplit("/", 1)[1])
        if path != "/api/mapping":
            return self._send(404, {"error": "not found"})
        try:
            m = normalize_mapping(self._body())
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        os.makedirs(os.path.dirname(State.path), exist_ok=True)
        with State.lock:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(State.path), prefix=".mapping.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(m, f, indent=2)
            os.replace(tmp, State.path)
        self._send(200, {"mapping": m})

    def do_DELETE(self):
        path = self._guard()
        if path is None:
            return
        gid = path.rsplit("/", 1)[1] if path.startswith("/api/references/") else ""
        if not USER_REF_ID.match(gid):
            return self._send(400, {"error": "only your own game defaults (u-...) can be deleted"})
        try:
            os.unlink(os.path.join(user_ref_dir(), gid + ".json"))
        except FileNotFoundError:
            return self._send(404, {"error": "not found"})
        self._send(200, {"ok": True})

    def _put_reference(self, gid):
        if not USER_REF_ID.match(gid):
            return self._send(400, {"error": "user game defaults need an id like u-my-game"})
        try:
            if int(self.headers.get("Content-Length") or 0) > REF_MAX_BYTES:
                raise ValueError("game defaults file too large")
            ref = normalize_reference(self._body(), strict=True)
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        with State.lock:
            write_json(os.path.join(user_ref_dir(), gid + ".json"), {"version": 1, "id": gid, **ref})
        self._send(200, {"reference": {**ref, "origin": "user"}})

    def do_POST(self):
        path = self._guard()
        if path is None:
            return
        if path == "/api/image":
            return self._upload_image()
        try:
            body = self._body()
            if not isinstance(body, dict):
                raise TypeError("expected an object")
            role = body.get("role", "stick")
            if path == "/api/pulse" and role == "xbox":
                b = body.get("button")
                if b not in XBOX_BUTTONS:
                    return self._send(400, {"error": "unknown Xbox button"})
                daemon_command({"cmd": "pulse", "role": "xbox", "button": b})
                return self._send(200, {"ok": True})
            if role not in ROLES:
                return self._send(400, {"error": "unknown role"})
            if path == "/api/mode":
                mode = body.get("mode")
                if mode not in MODES:
                    return self._send(400, {"error": "unknown mode"})
                r = daemon_command({"cmd": "mode", "role": role, "mode": mode}, want_result=True)
                return self._send(200 if r["ok"] else 400, {"ok": r["ok"], "error": r.get("error")})
            if path == "/api/pulse":
                daemon_command({"cmd": "pulse", "role": role, "button": int(body.get("button"))})
                return self._send(200, {"ok": True})
            if path == "/api/expose":
                r = daemon_command({"cmd": "expose", "role": role, "on": bool(body.get("on"))}, want_result=True)
                return self._send(200 if r["ok"] else 400, {"ok": r["ok"], "error": r.get("error")})
            if path == "/api/select":
                dev = body.get("device")
                if dev is not None and not isinstance(dev, str):
                    return self._send(400, {"error": "device must be a selector or null"})
                r = daemon_command({"cmd": "select", "role": role, "device": dev}, want_result=True)
                return self._send(200 if r["ok"] else 400, {"ok": r["ok"], "error": r.get("error")})
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        except OSError as e:
            return self._send(503, {"error": f"daemon not reachable: {e}"})
        self._send(404, {"error": "not found"})

    def _put_layout(self):
        try:
            lay = normalize_layout(self._body())
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        with State.lock:
            write_json(State.layout_path(), lay)
            # remove pictures no view references any more
            used = layout_images(lay)
            for f in os.listdir(State.image_dir()) if os.path.isdir(State.image_dir()) else []:
                if IMAGE_NAME.match(f) and f not in used:
                    os.unlink(os.path.join(State.image_dir(), f))
        self._send(200, {"layout": lay})

    def _upload_image(self):
        """Raw image body; ?view=<id>&role=<role>. Returns the stored file name (the client then saves the layout)."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        view = q.get("view", [""])[0]
        role = q.get("role", ["stick"])[0]
        if role not in ROLES:
            return self._send(400, {"error": "unknown role"})
        n = int(self.headers.get("Content-Length") or 0)
        if not VIEW_ID.match(view):
            return self._send(400, {"error": "bad view id"})
        if not 0 < n <= IMAGE_MAX:
            return self._send(413, {"error": f"images must be under {IMAGE_MAX >> 20} MB"})
        data = self.rfile.read(n)
        ext = image_kind(data)
        if not ext:
            return self._send(415, {"error": "only PNG, JPEG or WebP images"})
        name = f"{'t-' if role == 'throttle' else ''}{view}"[:24] + f"-{secrets.token_hex(4)}.{ext}"
        os.makedirs(State.image_dir(), exist_ok=True)
        with open(os.path.join(State.image_dir(), name), "wb") as f:
            f.write(data)
        self._send(200, {"image": name})

    def _events(self):
        """Server-sent events relaying the daemon's JSON lines; reconnects if the daemon restarts."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with State.lock:
            State.clients += 1
        try:
            while True:
                try:
                    with socket.socket(socket.AF_UNIX) as s:
                        s.connect(CTL)
                        s.settimeout(15)
                        buf = b""
                        while True:
                            try:
                                data = s.recv(65536)
                            except socket.timeout:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                continue
                            if not data:
                                break
                            buf += data
                            *lines, buf = buf.split(b"\n")
                            # coalesce: forward only the newest state line per chunk
                            states = [l for l in lines if l.startswith(b'{"t":"state"')]
                            for line in lines:
                                if line and (not line.startswith(b'{"t":"state"') or line is states[-1]):
                                    self.wfile.write(b"data: " + line + b"\n\n")
                            self.wfile.flush()
                except (FileNotFoundError, ConnectionRefusedError, PermissionError) as e:
                    msg = {"t": "offline", "reason": type(e).__name__}
                    self.wfile.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(2)
                self.wfile.write(b'data: {"t":"offline","reason":"daemon stopped"}\n\n')
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with State.lock:
                State.clients -= 1
                State.last_seen = time.monotonic()


def daemon_command(cmd, want_result=False, timeout=5):
    """Send one command to the daemon. With want_result, wait for its {"t":"result"} reply."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(CTL)
        f = s.makefile("rb")
        # wait for the daemon's hello so it has accepted us before we send and hang up
        hello = json.loads(f.readline() or b"null")
        if not isinstance(hello, dict):
            raise OSError("daemon closed the connection")
        s.sendall(json.dumps(cmd).encode() + b"\n")
        if not want_result:
            time.sleep(0.05)
            return hello
        for line in f:
            m = json.loads(line)
            if m.get("t") == "result":
                return m
        raise OSError("daemon closed the connection")


def daemon_hello():
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect(CTL)
        return json.loads(s.makefile("rb").readline())


def print_status():
    h = daemon_hello()
    if "roles" not in h:
        sys.exit("the running daemon is an older version; reinstall vkb-hotas (sudo ./install.sh)")
    for r in ROLES:
        info = h["roles"][r]
        dev = info["device"]
        src = f"{dev['name']} ({dev['id']})" if dev else ("not connected" if info["selector"] is not None else "none")
        if info["selector"] is None:
            state = "not used"
        elif info["exposed"]:
            state = "EXPOSED: games see the real stick"
        elif info.get("mode") == "xbox":
            pad = h.get("xbox") or {}
            state = f"feeds the virtual {pad.get('name', 'Xbox controller')}" + ("" if pad.get("active") else " (waiting for the stick)")
        else:
            state = f"games see a {info['emulated']['name']}" if info["emulated"]["active"] else "waiting for the stick"
        print(f"{r:9} {src}\n          {state}")
    others = [d for d in h["devices"] if not d["role"]]
    if others:
        print("other sticks: " + ", ".join(f"{d['name']} ({d['selector']})" for d in others))


def cli_mode(role, mode):
    try:
        r = daemon_command({"cmd": "mode", "role": role, "mode": mode}, want_result=True)
    except PermissionError:
        sys.exit("no access to the vkb-hotas daemon: VKB_HOTAS_USER in /etc/default/vkb-hotas must be you")
    except OSError as e:
        sys.exit(f"vkb-hotas daemon not reachable ({e}); is the service running?")
    if not r["ok"]:
        sys.exit(f"vkb-hotas: {r['error']}")
    print_status()
    print("Restart a running game to pick up the change.")


def url_file():
    """Single-instance file, keyed by mapping file + daemon socket so test instances
    (--mapping / VKB_HOTAS_CTL) never hand over to the real one."""
    key = hashlib.sha256(f"{State.path}\0{CTL}".encode()).hexdigest()[:12]
    return os.path.join(STATE_DIR, f"url-{key}")


def existing_instance():
    """URL of an already running mapper, if any."""
    try:
        url = open(url_file()).read().strip()
        u = urllib.parse.urlparse(url)
        tok = urllib.parse.parse_qs(u.query)["t"][0]
        req = urllib.request.Request(f"{u.scheme}://{u.netloc}/api/mapping", headers={"X-Token": tok})
        urllib.request.urlopen(req, timeout=1).read()
        return url
    except Exception:
        try:
            os.unlink(url_file())  # stale: left by a killed instance
        except OSError:
            pass
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: random)")
    ap.add_argument("--no-browser", action="store_true", help="just print the URL")
    ap.add_argument("--mapping", help="mapping file to edit (default: ~/.config/vkb-hotas/mapping.json, "
                                      "which is what the daemon reads)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--expose", choices=(*ROLES, "all"), metavar="ROLE",
                   help="give the real stick of a role (stick, throttle or all) to games untouched; "
                        "its virtual device is removed. Remembered across restarts")
    g.add_argument("--xbox", choices=(*ROLES, "all"), metavar="ROLE",
                   help="hide the real stick and make it feed a virtual Xbox 360 controller (shared by all "
                        "sticks in this mode), for games that support pads but not flight sticks. Remembered")
    g.add_argument("--hide", choices=(*ROLES, "all"), metavar="ROLE",
                   help="back to normal: hide the real stick and emulate its Thrustmaster device")
    g.add_argument("--status", action="store_true", help="show which stick feeds which role, and exit")
    args = ap.parse_args()
    if args.status or args.expose or args.hide or args.xbox:
        try:
            if args.status:
                print_status()
            else:
                cli_mode(args.expose or args.xbox or args.hide,
                         "real" if args.expose else "xbox" if args.xbox else "virtual")
        except PermissionError:
            sys.exit("no access to the vkb-hotas daemon: VKB_HOTAS_USER in /etc/default/vkb-hotas must be you")
        except (OSError, ValueError) as e:
            sys.exit(f"vkb-hotas daemon not reachable ({e}); is the service running?")
        return
    if args.mapping:
        State.path = os.path.abspath(args.mapping)

    url = existing_instance()
    if url:
        print(f"vkb-mapper already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={State.token}"
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    fd = os.open(url_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, url.encode())
    os.close(fd)
    print(f"vkb-mapper: {url}\n(mapping file: {State.path}; Ctrl+C to quit)", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()

    def reaper():
        started = time.monotonic()
        while True:
            time.sleep(5)
            with State.lock:
                idle = State.clients == 0 and time.monotonic() - max(State.last_seen, started) > IDLE_EXIT
            if idle:
                print("vkb-mapper: no open windows, exiting", flush=True)
                srv.shutdown()
                return
    threading.Thread(target=reaper, daemon=True).start()

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(url_file())
        except OSError:
            pass


if __name__ == "__main__":
    main()
