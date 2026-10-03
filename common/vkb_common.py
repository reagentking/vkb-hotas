"""Shared helpers for the vkb-hotas tools: config file and source-device matching."""
import os
import re

CONFIG = "/etc/default/vkb-hotas"
VKB_VENDOR = 0x231D
EMULATED = (0x044F, 0xB10A)  # Thrustmaster T.16000M


def read_config(path=CONFIG):
    """Parse the systemd EnvironmentFile (KEY="value" lines) into a dict."""
    cfg = {}
    try:
        for line in open(path):
            m = re.match(r'\s*([A-Z_]+)\s*=\s*"?([^"#]*?)"?\s*$', line)
            if m and not line.lstrip().startswith("#"):
                cfg[m[1]] = m[2]
    except OSError:
        pass
    return cfg


def parse_match(spec):
    """'231d:3200' -> (0x231d, 0x3200); '231d' -> (0x231d, None); '' -> VKB vendor, any product."""
    if not spec:
        return VKB_VENDOR, None
    parts = spec.lower().replace("0x", "").split(":")
    vid = int(parts[0], 16)
    pid = int(parts[1], 16) if len(parts) > 1 and parts[1] else None
    return vid, pid


def default_match():
    """--device default: $VKB_HOTAS_DEVICE, then the config file, then any VKB device."""
    return os.environ.get("VKB_HOTAS_DEVICE") or read_config().get("VKB_HOTAS_DEVICE", "")


def find_devices(spec=""):
    """Open evdev devices matching VID[:PID] that have absolute axes (i.e. sticks)."""
    import evdev
    from evdev import ecodes as E
    vid, pid = parse_match(spec)
    found = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
        except OSError:
            continue
        if (d.info.vendor == vid and (pid is None or d.info.product == pid)
                and (d.info.vendor, d.info.product) != EMULATED
                and E.EV_ABS in d.capabilities()):
            found.append(d)
        else:
            d.close()
    return found


def find_one(spec=""):
    """Exactly one matching device, or raise SystemExit with a helpful message."""
    devs = find_devices(spec)
    if len(devs) == 1:
        return devs[0]
    if not devs:
        return None
    listing = "\n".join(f"  {d.info.vendor:04x}:{d.info.product:04x}  {d.name.strip()}  ({d.path})" for d in devs)
    raise SystemExit(f"several matching sticks found; pick one with --device VID:PID "
                     f"(or VKB_HOTAS_DEVICE in {CONFIG}):\n{listing}")


# ---- Mapping profiles ---------------------------------------------------------
# ~/.config/vkb-hotas/mapping.json, written by vkb-mapper.py, hot-reloaded by the daemon:
# {
#   "version": 1, "active": "Default",
#   "profiles": {"Default": {
#       "button_count": 16 | 32,
#       "axes":    {"X": {"src": "ABS_X", "invert": false, "deadzone": 0}, ...},  # src null = neutral
#       "buttons": {"1": [1, 17], ...},          # emulated button -> sources
#       "hat":     {"up": ["hat:up"], "down": [...], "left": [...], "right": [...]},
#       "labels":  {"1": "Fire guns", "hat:up": "Camera"}}}
# }
# A source is a physical joystick button number (1-based, in Linux/VKBDevCfg order)
# or a physical hat direction "hat:up|down|left|right".

EMU_AXES = ("X", "Y", "RZ", "SLIDER")
HAT_DIRS = ("up", "down", "left", "right")
DEFAULT_AXES = {"X": "ABS_X", "Y": "ABS_Y", "RZ": "ABS_RZ", "SLIDER": "ABS_Z"}
MAPPING_MAX_BYTES = 1 << 20


def mapping_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".config", "vkb-hotas", "mapping.json")


def default_profile(button_count=16, axes=None, invert=()):
    axes = axes or DEFAULT_AXES
    return {
        "button_count": button_count,
        "axes": {a: {"src": axes.get(a), "invert": a in invert, "deadzone": 0} for a in EMU_AXES},
        "buttons": {str(i): [i] for i in range(1, button_count + 1)},
        "hat": {d: [f"hat:{d}"] for d in HAT_DIRS},
        "labels": {},
    }


def _sources(v, where):
    if not isinstance(v, list):
        raise ValueError(f"{where}: expected a list of sources")
    out = []
    for s in v:
        if isinstance(s, bool):
            raise ValueError(f"{where}: bad source {s!r}")
        if isinstance(s, int) and 1 <= s <= 128:
            out.append(s)
        elif isinstance(s, str) and s.startswith("hat:") and s[4:] in HAT_DIRS:
            out.append(s)
        else:
            raise ValueError(f"{where}: bad source {s!r} (button 1-128 or hat:up/down/left/right)")
    return out


def normalize_profile(p):
    """Validate a profile dict, fill defaults, return a clean copy (raises ValueError)."""
    if not isinstance(p, dict):
        raise ValueError("profile must be an object")
    count = p.get("button_count", 16)
    if count not in (16, 32):
        raise ValueError("button_count must be 16 or 32")
    out = default_profile(count)
    for a, cfg in (p.get("axes") or {}).items():
        if a not in EMU_AXES or not isinstance(cfg, dict):
            raise ValueError(f"axes: unknown axis {a!r}")
        src = cfg.get("src")
        if src is not None and not (isinstance(src, str) and re.fullmatch(r"ABS_[A-Z0-9_]+", src)):
            raise ValueError(f"axes.{a}.src: bad value {src!r}")
        dz = cfg.get("deadzone", 0)
        if not isinstance(dz, (int, float)) or not 0 <= dz <= 50:
            raise ValueError(f"axes.{a}.deadzone must be 0-50 (%)")
        out["axes"][a] = {"src": src, "invert": bool(cfg.get("invert", False)), "deadzone": dz}
    if "buttons" in p:
        if not isinstance(p["buttons"], dict):
            raise ValueError("buttons must be an object")
        out["buttons"] = {}
        for k, v in p["buttons"].items():
            if not (k.isdigit() and 1 <= int(k) <= count):
                raise ValueError(f"buttons: {k!r} is not an emulated button 1-{count}")
            out["buttons"][k] = _sources(v, f"buttons.{k}")
    if "hat" in p:
        if not isinstance(p["hat"], dict):
            raise ValueError("hat must be an object")
        out["hat"] = {d: _sources(p["hat"].get(d, []), f"hat.{d}") for d in HAT_DIRS}
    labels = p.get("labels") or {}
    if not isinstance(labels, dict):
        raise ValueError("labels must be an object")
    out["labels"] = {str(k)[:16]: str(v)[:48] for k, v in labels.items() if str(v).strip()}
    return out


def normalize_mapping(m):
    if not isinstance(m, dict) or not isinstance(m.get("profiles"), dict) or not m["profiles"]:
        raise ValueError("mapping needs a non-empty 'profiles' object")
    if len(m["profiles"]) > 64:
        raise ValueError("too many profiles")
    profiles = {}
    for name, p in m["profiles"].items():
        if not isinstance(name, str) or not 0 < len(name) <= 40:
            raise ValueError("profile names must be 1-40 characters")
        try:
            profiles[name] = normalize_profile(p)
        except ValueError as e:
            raise ValueError(f"profile {name!r}: {e}") from None
    active = m.get("active")
    if active not in profiles:
        active = next(iter(profiles))
    return {"version": 1, "active": active, "profiles": profiles}


def load_mapping(path, owner_uid=None):
    """Read + validate a mapping file. As root, refuse symlinks and files not owned by owner_uid/root."""
    import json
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("mapping path is not a regular file")
        if owner_uid is not None and st.st_uid not in (0, owner_uid):
            raise ValueError("mapping file not owned by the configured user")
        if st.st_size > MAPPING_MAX_BYTES:
            raise ValueError("mapping file too large")
        data = os.read(fd, MAPPING_MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        m = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON at line {e.lineno} column {e.colno}") from None
    return normalize_mapping(m)
