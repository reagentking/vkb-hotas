"""HOTAS Mapper in its own window: the same page and server as the browser version, shown in a
GTK4 + WebKitGTK 6.0 window (PyGObject). Imported by vkb-mapper.py; when the bindings are missing
it falls back to opening the page in the browser.

- One window per mapping file: a second launch brings it to the front (GApplication uniqueness).
- The window owns the server: closing it stops the server at once (no 45 s idle wait). If a
  browser-mode server is already running for this mapping file, the window just shows it.
- Links to other sites open in the browser; file inputs (Photo..., Import) get GTK's file dialog,
  and downloads (Export) ask where to save.
"""
import json
import os
import signal
import threading
import webbrowser

APP_ID = "io.github.reagentking.VkbHotasMapper"
TITLE = "HOTAS Mapper"


def load():
    """The GTK/WebKit modules, or None (with the reason) when they can't be used here."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return None, "no graphical session"
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gio, GLib, Gtk, WebKit
    except (ImportError, ValueError) as e:
        return None, f"WebKitGTK 6.0 / PyGObject not available ({e})"
    return (Gio, GLib, Gtk, WebKit), None


def run(mods, *, app_id, existing_url, start_server, state_file, log):
    """Show the window; returns when it's closed. start_server() -> (server, url, cleanup)."""
    Gio, GLib, Gtk, WebKit = mods
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    st = {"url": None, "srv": None, "cleanup": None, "win": None}

    def on_startup(_app):  # only in the first instance; a second launch just activates this one
        st["url"] = existing_url
        if not st["url"]:
            st["srv"], st["url"], st["cleanup"] = start_server()
            threading.Thread(target=st["srv"].serve_forever, daemon=True).start()

    def on_shutdown(_app):
        if st["srv"]:
            st["srv"].shutdown()
        if st["cleanup"]:
            st["cleanup"]()

    def size():
        try:
            w = json.load(open(state_file))
            return max(640, int(w["width"])), max(480, int(w["height"]))
        except (OSError, ValueError, KeyError, TypeError):
            return 1440, 900

    def save_size(w):
        try:
            with open(state_file, "w") as f:
                json.dump({"width": w.get_width(), "height": w.get_height()}, f)
        except OSError:
            pass

    def external(uri):
        host = st["url"].split("/?", 1)[0]
        return not (uri.startswith(host + "/") or uri == host or uri.startswith(("blob:", "data:", "about:")))

    def on_policy(view, decision, kind):
        if kind in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if external(uri):
                webbrowser.open(uri)
                decision.ignore()
                return True
        return False

    def on_download(session, download):
        def decide(dl, suggested):
            dlg = Gtk.FileDialog(title="Save", initial_name=suggested or "download")

            def done(d, res):
                try:
                    f = d.save_finish(res)
                except GLib.Error:  # cancelled
                    dl.cancel()
                    return
                dl.set_allow_overwrite(True)
                dl.set_destination(f.get_path())
            dlg.save(st["win"], None, done)
            return True  # destination is set asynchronously
        download.connect("decide-destination", decide)
        download.connect("finished", lambda dl: log(f"saved {dl.get_destination() or '(cancelled)'}"))

    def on_activate(_app):
        if st["win"] is not None:
            st["win"].present()
            return
        win = st["win"] = Gtk.ApplicationWindow(application=app, title=TITLE)
        win.set_icon_name("input-gaming")
        win.set_default_size(*size())
        view = WebKit.WebView()
        view.get_settings().set_enable_developer_extras(False)
        view.connect("decide-policy", on_policy)
        view.get_network_session().connect("download-started", on_download)
        view.connect("load-changed", lambda v, ev: ev == WebKit.LoadEvent.FINISHED and log("window: page loaded"))
        win.set_child(view)
        win.connect("close-request", lambda w: save_size(w) or False)
        view.load_uri(st["url"])
        win.present()

    app.connect("startup", on_startup)
    app.connect("activate", on_activate)
    app.connect("shutdown", on_shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: app.quit() or False)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: app.quit() or False)
    try:
        app.register(None)  # runs on_startup if this is the first instance
        remote = app.get_is_remote()
    except Exception as e:  # GLib.Error: e.g. no session bus
        log(f"no session bus ({e}); one window per launch")
        remote = False
    rc = app.run([])  # a second instance activates the first one (raising its window) and returns
    if remote:
        log("HOTAS Mapper is already open: brought its window to the front")
    return rc
