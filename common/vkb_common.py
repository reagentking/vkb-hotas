"""Shared helpers for the vkb-hotas tools: config file, roles, source-device selection,
the mapping schema and small terminal pickers."""
import os
import re
import sys

CONFIG = "/etc/default/vkb-hotas"
VKB_VENDOR = 0x231D
UHID_PHYS = "vkb-hotas/uhid"  # phys of our own virtual devices, so discovery never offers them

# ---- Roles --------------------------------------------------------------------
# Each role turns one real stick into one emulated Thrustmaster device:
#   stick    -> T.16000M (required)          config: VKB_HOTAS_DEVICE
#   throttle -> TWCS Throttle (optional)     config: VKB_HOTAS_THROTTLE
# A role can be "exposed" (VKB_HOTAS_EXPOSE): its real stick is given to games
# untouched and its virtual device is removed.
ROLES = ("stick", "throttle")
ROLE_CONFIG = {"stick": "VKB_HOTAS_DEVICE", "throttle": "VKB_HOTAS_THROTTLE"}
EMULATED_IDS = {"stick": (0x044F, 0xB10A), "throttle": (0x044F, 0xB687)}
EMULATED_NAMES = {"stick": "Thrustmaster T.16000M", "throttle": "Thrustmaster TWCS Throttle"}
EMULATED = EMULATED_IDS["stick"]
ROLE_AXES = {"stick": ("X", "Y", "RZ", "SLIDER"),
             "throttle": ("X", "Y", "Z", "RX", "RY", "RZ", "SLIDER")}
ROLE_BUTTON_COUNTS = {"stick": (16, 32), "throttle": (14, 32)}  # first = faithful to the real device
# Defaults suit VKB Gladiator NXT EVO Omni Throttle bases: X/Y = stick, RZ = twist,
# Z = Omni Throttle lever, RX/RY = the SCG grip's mini-stick.
ROLE_DEFAULT_AXES = {
    "stick": {"X": "ABS_X", "Y": "ABS_Y", "RZ": "ABS_RZ", "SLIDER": "ABS_Z"},
    "throttle": {"Z": "ABS_Z", "X": "ABS_RX", "Y": "ABS_RY", "RZ": "ABS_RZ"},
}
EMU_AXES = ROLE_AXES["stick"]
DEFAULT_AXES = ROLE_DEFAULT_AXES["stick"]
HAT_DIRS = ("up", "down", "left", "right")


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


def write_config(updates, path=CONFIG):
    """Set KEY="value" lines in the EnvironmentFile, keeping every other line. Atomic."""
    for k, v in updates.items():
        if not re.fullmatch(r"[A-Z_]+", k) or not re.fullmatch(r"[A-Za-z0-9:@._/ -]*", v):
            raise ValueError(f"refusing to write {k}={v!r}")
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        lines = []
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z_]+)\s*=", line)
        if m and m[1] in updates and not line.lstrip().startswith("#"):
            lines[i] = f'{m[1]}="{updates[m[1]]}"'
            done.add(m[1])
    lines += [f'{k}="{v}"' for k, v in updates.items() if k not in done]
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def role_selectors(cfg=None):
    """{role: selector} from the environment (systemd passes the config) or the config file."""
    cfg = read_config() if cfg is None else cfg
    return {r: os.environ.get(ROLE_CONFIG[r], cfg.get(ROLE_CONFIG[r], "")).strip() for r in ROLES}


def exposed_roles(cfg=None):
    cfg = read_config() if cfg is None else cfg
    return {r for r in os.environ.get("VKB_HOTAS_EXPOSE", cfg.get("VKB_HOTAS_EXPOSE", "")).split() if r in ROLES}


# ---- Source devices and selectors ----------------------------------------------
# A selector names a real stick:
#   "231d:3200"                         VID:PID (any port)
#   "231d:3200@usb-0000:18:00.3-2.1"    VID:PID on one USB port (needed only when two
#                                       connected sticks report the same VID:PID)
#   "231d" / ""                         legacy: any product of that vendor / any VKB device
#   "/dev/input/eventN"                 legacy: one evdev node
SELECTOR = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}(@[A-Za-z0-9:._/-]{1,80})?$")


def parse_match(spec):
    """'231d:3200' -> (0x231d, 0x3200); '231d' -> (0x231d, None); '' -> VKB vendor, any product.
    A trailing '@port' is ignored here (see port_of)."""
    spec = (spec or "").split("@", 1)[0]
    if not spec:
        return VKB_VENDOR, None
    parts = spec.lower().replace("0x", "").split(":")
    vid = int(parts[0], 16)
    pid = int(parts[1], 16) if len(parts) > 1 and parts[1] else None
    return vid, pid


def port_of(dev):
    """USB port part of an evdev device's phys ('usb-0000:18:00.3-2.1/input0' -> 'usb-0000:18:00.3-2.1')."""
    phys = (getattr(dev, "phys", "") or "").strip()
    return phys.rsplit("/input", 1)[0] if "/input" in phys else phys


def vidpid(dev):
    return f"{dev.info.vendor:04x}:{dev.info.product:04x}"


def is_stick(dev):
    """A joystick-like evdev device: absolute axes plus joystick/gamepad buttons, not a touchpad
    and not one of our own virtual devices."""
    from evdev import ecodes as E
    if (getattr(dev, "phys", "") or "") == UHID_PHYS:
        return False
    caps = dev.capabilities()
    keys = set(caps.get(E.EV_KEY, []))
    if E.EV_ABS not in caps or E.BTN_TOUCH in keys or E.BTN_TOOL_FINGER in keys:
        return False
    return any(0x120 <= k <= 0x13F or k >= 0x2C0 for k in keys)


def list_sticks():
    """Open every joystick-like evdev device we can read, sorted by VID:PID and port."""
    import evdev
    out = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
        except OSError:
            continue
        if is_stick(d):
            out.append(d)
        else:
            d.close()
    return sorted(out, key=lambda d: (vidpid(d), port_of(d), d.path))


def selector_for(dev, others):
    """Shortest selector that picks dev out of others: VID:PID, plus @port if needed."""
    same = [o for o in others if o is not dev and vidpid(o) == vidpid(dev)]
    return f"{vidpid(dev)}@{port_of(dev)}" if same and port_of(dev) else vidpid(dev)


def matches(spec, dev):
    if spec.startswith("/dev/"):
        return os.path.realpath(spec) == os.path.realpath(dev.path)
    vid, pid = parse_match(spec)
    port = spec.split("@", 1)[1] if "@" in spec else None
    return (dev.info.vendor == vid and (pid is None or dev.info.product == pid)
            and (port is None or port_of(dev) == port))


def find_devices(spec="", sticks=None):
    """Sticks matching a selector. Closes the others when it opened them itself."""
    own = sticks is None
    sticks = list_sticks() if own else sticks
    found = [d for d in sticks if matches(spec, d)]
    if own:
        for d in sticks:
            if d not in found:
                d.close()
    return found


def find_one(spec=""):
    """Exactly one matching device, or raise SystemExit with a helpful message."""
    devs = find_devices(spec)
    if len(devs) == 1:
        return devs[0]
    if not devs:
        return None
    listing = "\n".join(f"  {selector_for(d, devs):40}  {d.name.strip()}" for d in devs)
    raise SystemExit(f"several matching sticks found; pick one with --device SELECTOR "
                     f"(or {ROLE_CONFIG['stick']} in {CONFIG}):\n{listing}")


def default_match():
    """--device default: $VKB_HOTAS_DEVICE, then the config file, then any VKB device."""
    return role_selectors()["stick"]


# ---- Mapping profiles ---------------------------------------------------------
# ~/.config/vkb-hotas/mapping.json, written by vkb-mapper.py, hot-reloaded by the daemon:
# {
#   "version": 2, "active": "Default",
#   "profiles": {"Default": {
#       "stick": {                                   # the emulated T.16000M
#           "button_count": 16 | 32,
#           "axes":    {"X": {"src": "ABS_X", "invert": false, "deadzone": 0}, ...},  # src null = neutral
#           "buttons": {"1": [1, 17], ...},          # emulated button -> sources
#           "hat":     {"up": ["hat:up"], "down": [...], "left": [...], "right": [...]},
#           "labels":  {"1": "Fire guns", "hat:up": "Camera"}},
#       "throttle": {...}}}                          # the emulated TWCS: axes X Y Z RX RY RZ SLIDER, 14 | 32 buttons
# }
# A source is a physical button number (1-based, in Linux/VKBDevCfg order) or a physical
# hat direction "hat:up|down|left|right" on the role's own stick, or the same prefixed with
# the other role ("throttle:5", "stick:hat:left") for a control on the other stick. Axis
# sources work the same way ("ABS_Z", "throttle:ABS_Z").
# Version 1 had a single stick profile at the top level; it becomes the "stick" section.
MAPPING_MAX_BYTES = 1 << 20


def mapping_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".config", "vkb-hotas", "mapping.json")


def default_role_profile(role, button_count=None, axes=None, invert=()):
    count = button_count or ROLE_BUTTON_COUNTS[role][0]
    axes = ROLE_DEFAULT_AXES[role] if axes is None else axes
    return {
        "button_count": count,
        "axes": {a: {"src": axes.get(a), "invert": a in invert, "deadzone": 0} for a in ROLE_AXES[role]},
        "buttons": {str(i): [i] for i in range(1, count + 1)},
        "hat": {d: [f"hat:{d}"] for d in HAT_DIRS},
        "labels": {},
    }


def default_profile(button_count=None, axes=None, invert=()):
    """A whole profile; the arguments shape the stick section (they come from VKB_HOTAS_ARGS)."""
    return {"stick": default_role_profile("stick", button_count, axes, invert),
            "throttle": default_role_profile("throttle")}


def split_source(role, s):
    """Normalized source -> (role of the physical stick, local source)."""
    if isinstance(s, str):
        head, _, rest = s.partition(":")
        if head in ROLES:
            return head, (int(rest) if rest.isdigit() else rest)
    return role, s


def _source(role, s, where):
    src_role, local = split_source(role, s)
    if isinstance(local, bool):
        raise ValueError(f"{where}: bad source {s!r}")
    if not ((isinstance(local, int) and 1 <= local <= 128)
            or (isinstance(local, str) and local.startswith("hat:") and local[4:] in HAT_DIRS)):
        raise ValueError(f"{where}: bad source {s!r} (button 1-128 or hat:up/down/left/right, "
                         f"optionally prefixed with stick: or throttle:)")
    return local if src_role == role else f"{src_role}:{local}"


def _sources(role, v, where):
    if not isinstance(v, list):
        raise ValueError(f"{where}: expected a list of sources")
    out = []
    for s in v:
        s = _source(role, s, where)
        if s not in out:
            out.append(s)
    return out


def _axis_source(role, src, where):
    if src is None:
        return None
    if isinstance(src, str):
        head, _, rest = src.partition(":")
        local, src_role = (rest, head) if head in ROLES else (src, role)
        if re.fullmatch(r"ABS_[A-Z0-9_]+", local):
            return local if src_role == role else f"{src_role}:{local}"
    raise ValueError(f"{where}: bad value {src!r}")


def normalize_role_profile(role, p):
    """Validate one role's section, fill defaults, return a clean copy (raises ValueError)."""
    if p is None:
        return default_role_profile(role)
    if not isinstance(p, dict):
        raise ValueError("must be an object")
    counts = ROLE_BUTTON_COUNTS[role]
    count = p.get("button_count", counts[0])
    if count not in counts:
        raise ValueError(f"button_count must be {' or '.join(map(str, counts))}")
    out = default_role_profile(role, count)
    for a, cfg in (p.get("axes") or {}).items():
        if a not in ROLE_AXES[role] or not isinstance(cfg, dict):
            raise ValueError(f"axes: unknown axis {a!r}")
        dz = cfg.get("deadzone", 0)
        if not isinstance(dz, (int, float)) or isinstance(dz, bool) or not 0 <= dz <= 50:
            raise ValueError(f"axes.{a}.deadzone must be 0-50 (%)")
        out["axes"][a] = {"src": _axis_source(role, cfg.get("src"), f"axes.{a}.src"),
                          "invert": bool(cfg.get("invert", False)), "deadzone": dz}
    if "buttons" in p:
        if not isinstance(p["buttons"], dict):
            raise ValueError("buttons must be an object")
        out["buttons"] = {}
        for k, v in p["buttons"].items():
            if not (k.isdigit() and 1 <= int(k) <= count):
                raise ValueError(f"buttons: {k!r} is not an emulated button 1-{count}")
            out["buttons"][str(int(k))] = _sources(role, v, f"buttons.{k}")
    if "hat" in p:
        if not isinstance(p["hat"], dict):
            raise ValueError("hat must be an object")
        out["hat"] = {d: _sources(role, p["hat"].get(d, []), f"hat.{d}") for d in HAT_DIRS}
    labels = p.get("labels") or {}
    if not isinstance(labels, dict):
        raise ValueError("labels must be an object")
    out["labels"] = {str(k)[:16]: str(v)[:48] for k, v in labels.items() if str(v).strip()}
    return out


def normalize_profile(p):
    """Validate a whole profile ({stick, throttle}); a version-1 profile becomes the stick section."""
    if not isinstance(p, dict):
        raise ValueError("profile must be an object")
    if not set(p) & set(ROLES):
        p = {"stick": p}  # version 1
    out = {}
    for role in ROLES:
        try:
            out[role] = normalize_role_profile(role, p.get(role))
        except ValueError as e:
            raise ValueError(f"{role}: {e}") from None
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
    return {"version": 2, "active": active, "profiles": profiles}


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


# ---- Terminal pickers ------------------------------------------------------------
# Used by install.sh (pick a stick per role) and proton-setup.py (pick games). Full-screen
# curses when the terminal can position the cursor, else a numbered list: under TERM=dumb
# curses still starts, and typed numbers were read as keys.

def _full_screen():
    try:
        import curses
        curses.setupterm()
        return curses.tigetstr("cup") is not None
    except Exception:
        return False


def _curses_list(scr, title, rows, sel, multi, cur):
    import curses
    for setup in (lambda: curses.curs_set(0), curses.use_default_colors):
        try:
            setup()  # cosmetic; some terminals don't support it
        except curses.error:
            pass
    top = 0
    help_ = ("Up/Down move  Space toggle  a all/none  Enter apply  q cancel" if multi
             else "Up/Down move  Enter choose  q cancel")
    while True:
        h, w = scr.getmaxyx()
        scr.erase()
        avail = max(1, h - 4)
        top = min(max(top, cur - avail + 1), cur)
        scr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
        for i, (key, label, note) in enumerate(rows[top:top + avail], top):
            mark = ("[x]" if key in sel else "[ ]") if multi else ("(*)" if key in sel else "( )")
            line = f" {mark} {label}"
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
        elif k == curses.KEY_PPAGE:
            cur = max(0, cur - avail)
        elif k == curses.KEY_NPAGE:
            cur = min(len(rows) - 1, cur + avail)
        elif k == ord(" ") and multi:
            sel ^= {rows[cur][0]}
        elif k == ord("a") and multi:
            sel = set() if len(sel) == len(rows) else {r[0] for r in rows}
        elif k in (10, 13, curses.KEY_ENTER):
            return sel if multi else rows[cur][0]
        elif k in (27, ord("q")):
            return None


def _ask(prompt):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        return "q"
    return line.strip().lower()


def checklist(title, rows, checked):
    """rows: [(key, label, note)]. Returns the set of checked keys, or None if cancelled."""
    if _full_screen():
        import curses
        try:
            return curses.wrapper(_curses_list, title, rows, set(checked), True, 0)
        except curses.error:
            pass
    print(title)
    for n, (key, label, note) in enumerate(rows, 1):
        print(f"  {n:>3}. [{'x' if key in checked else ' '}] {label}  {note}")
    ans = _ask("Numbers to have checked (e.g. 1 4 7), 'all', 'none', Enter to keep as shown, q to cancel: ")
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


def pick_one(title, rows, current=None):
    """rows: [(key, label, note)]. Returns the chosen key, or None if cancelled."""
    keys = [r[0] for r in rows]
    start = keys.index(current) if current in keys else 0
    if _full_screen():
        import curses
        try:
            return curses.wrapper(_curses_list, title, rows, {keys[start]}, False, start)
        except curses.error:
            pass
    print(title)
    for n, (key, label, note) in enumerate(rows, 1):
        print(f"  {n:>3}. {'*' if n - 1 == start else ' '} {label}  {note}")
    ans = _ask(f"Number to choose (Enter = {start + 1}, q to cancel): ")
    if ans == "q":
        return None
    if not ans:
        return keys[start]
    if not ans.isdigit() or not 1 <= int(ans) <= len(rows):
        sys.exit(f"not a number from the list: {ans}")
    return keys[int(ans) - 1]
