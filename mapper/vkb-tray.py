#!/usr/bin/env python3
"""
vkb-tray: a system tray icon for vkb-hotas. Its menu switches what games see of each stick
(virtual Thrustmaster device / virtual Xbox controller / the real stick), the same switch as in
HOTAS Mapper and `vkb-mapper --hide/--xbox/--expose`, without root or a terminal; it also opens
HOTAS Mapper.

It talks to the daemon's control socket (owned by VKB_HOTAS_USER) and follows changes made
elsewhere. Needs PyGObject with GTK 3 and libayatana-appindicator (StatusNotifierItem: KDE
Plasma, most other desktops; GNOME needs the AppIndicator extension). It's a separate program
from HOTAS Mapper's window because that one uses GTK 4, and one process can't load both.
"""
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "common")]
from vkb_common import EMULATED_NAMES, ROLES  # noqa: E402

CTL = os.environ.get("VKB_HOTAS_CTL", "/run/vkb-hotas/ctl.sock")
LABEL = {"stick": "Stick", "throttle": "Throttle stick"}
SHORT = {"stick": "T.16000M", "throttle": "TWCS"}


def on_signal(GLib, sig, cb):
    """GLibUnix.signal_add where PyGObject has it (GLib.unix_signal_add is deprecated there)."""
    try:
        import gi
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        return GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, cb)
    except (ImportError, ValueError, AttributeError):
        return GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, cb)


def single_instance():
    """Hold a lock for the session; a second tray for the same socket exits quietly."""
    d = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    path = os.path.join(d, f"vkb-tray-{hashlib.sha256(CTL.encode()).hexdigest()[:12]}.lock")
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return f


class Daemon:
    """One long-lived control connection: asks for no live state ("quiet") and reports each hello
    to on_hello(hello or None, problem) from a background thread; reconnects every 2 s."""

    def __init__(self, on_hello):
        self.on_hello = on_hello
        self.sock = None
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(CTL)
                s.sendall(b'{"cmd":"quiet"}\n')
                with self.lock:
                    self.sock = s
                buf = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        if m.get("t") == "hello":
                            self.on_hello(m, None)
                        elif m.get("t") == "result" and not m.get("ok"):
                            self.on_hello(None, f"Not changed: {m.get('error')}")
                problem = "The vkb-hotas service stopped"
            except PermissionError:
                problem = "No access to the vkb-hotas service (VKB_HOTAS_USER in /etc/default/vkb-hotas must be you)"
            except OSError:
                problem = "The vkb-hotas service isn't running (it starts when a stick is plugged in)"
            with self.lock:
                self.sock = None
            self.on_hello(None, problem)
            time.sleep(2)

    def mode(self, role, mode):
        with self.lock:
            if self.sock is None:
                return False
            try:
                self.sock.sendall(json.dumps({"cmd": "mode", "role": role, "mode": mode}).encode() + b"\n")
                return True
            except OSError:
                return False


def main():
    autostart = "--autostart" in sys.argv[1:]  # from /etc/xdg/autostart: quiet exit for other users
    if autostart and os.path.exists(CTL) and not os.access(CTL, os.R_OK | os.W_OK):
        return 0  # the control socket belongs to another user (VKB_HOTAS_USER)
    lock = single_instance()
    if lock is None:
        print("vkb-tray is already running", flush=True)
        return 0
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AI
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as e:
        if autostart:
            return 0
        sys.exit(f"vkb-tray needs PyGObject with GTK 3 and libayatana-appindicator ({e})")

    ind = AI.Indicator.new("vkb-hotas-tray", "vkb-hotas", AI.IndicatorCategory.HARDWARE)
    if os.path.isdir(os.path.join(HERE, "icons")):  # running from the repo: icons not installed in the theme
        ind.set_icon_theme_path(os.path.join(HERE, "icons"))
    ind.set_attention_icon_full("vkb-hotas-attention", "A real stick is given to games")
    ind.set_title("HOTAS")
    ind.set_status(AI.IndicatorStatus.ACTIVE)
    menu = Gtk.Menu()
    status = Gtk.MenuItem(label="Connecting to vkb-hotas…")
    status.set_sensitive(False)
    menu.append(status)
    sections = {}  # role -> {"header": item, "sep": item, "items": {mode: RadioMenuItem}}
    updating = [False]

    def pick(item, role, mode):
        if updating[0] or not item.get_active():
            return
        if not daemon.mode(role, mode):
            status.set_label("Not changed: the vkb-hotas service isn't reachable")

    for role in ROLES:
        sep = Gtk.SeparatorMenuItem()
        header = Gtk.MenuItem(label=LABEL[role])
        header.set_sensitive(False)
        items, group = {}, None
        menu.append(sep)
        menu.append(header)
        for mode, text in (("virtual", f"Virtual {SHORT[role]}"), ("xbox", "Xbox pad"), ("real", "Real stick")):
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, text)
            group = it
            it.connect("toggled", pick, role, mode)
            items[mode] = it
            menu.append(it)
        sections[role] = {"header": header, "sep": sep, "items": items}
    menu.append(Gtk.SeparatorMenuItem())
    opener = Gtk.MenuItem(label="Open HOTAS Mapper")
    opener.connect("activate", lambda *_: subprocess.Popen(
        [sys.executable, os.path.join(HERE, "vkb-mapper.py")], start_new_session=True))
    menu.append(opener)
    quitter = Gtk.MenuItem(label="Quit tray icon")
    quitter.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quitter)
    menu.show_all()
    ind.set_menu(menu)

    def show(hello, problem):
        updating[0] = True
        try:
            if hello is None:
                if problem and problem.startswith("Not changed"):
                    status.set_label(problem)
                    return False
                status.set_label(problem or "vkb-hotas isn't reachable")
                ind.set_title("HOTAS: " + (problem or "service not reachable"))
                ind.set_status(AI.IndicatorStatus.ACTIVE)
                for sec in sections.values():
                    for it in sec["items"].values():
                        it.set_sensitive(False)
                return False
            parts, real = [], False
            for role, sec in sections.items():
                info = (hello.get("roles") or {}).get(role) or {}
                used = info.get("selector") is not None
                mode = info.get("mode") or ("real" if info.get("exposed") else "virtual")
                for w in (sec["header"], sec["sep"], *sec["items"].values()):
                    w.set_visible(used)
                if not used:
                    continue
                dev = (info.get("device") or {}).get("name", "").replace("VKB-Sim (C) Alex Oz 2023", "").strip()
                sec["header"].set_label(f"{LABEL[role]}: {dev or 'not connected'}")
                for m, it in sec["items"].items():
                    it.set_sensitive(m != "xbox" or "xbox" in hello)  # an older daemon has no Xbox mode
                    it.set_active(m == mode)
                real = real or mode == "real"
                parts.append(f"{LABEL[role]}: " + {"real": "REAL stick", "xbox": "Xbox pad",
                                                   "virtual": EMULATED_NAMES[role]}[mode])
            status.set_label("Games see:")
            ind.set_title("HOTAS: games see " + " · ".join(parts))
            ind.set_status(AI.IndicatorStatus.ATTENTION if real else AI.IndicatorStatus.ACTIVE)
        finally:
            updating[0] = False
        return False

    daemon = Daemon(lambda h, p: GLib.idle_add(show, h, p))
    on_signal(GLib, 15, lambda: Gtk.main_quit() or False)  # SIGTERM
    on_signal(GLib, 2, lambda: Gtk.main_quit() or False)   # SIGINT
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
