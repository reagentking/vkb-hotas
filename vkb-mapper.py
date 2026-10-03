#!/usr/bin/env python3
"""
vkb-mapper: graphical button/axis remapper for vkb-hotas.

Opens a local web UI in your browser. It shows your stick's live input, and
lets you choose which physical button drives each button / hat direction of the
emulated T.16000M, plus axis sources, inversion and deadzones. Changes are
saved to ~/.config/vkb-hotas/mapping.json and applied by the running daemon
within half a second, even while a game is running.

  vkb-mapper.py [--port N] [--no-browser]

No extra packages needed. The server listens on 127.0.0.1 only, requires a
per-session token, and exits shortly after the last browser tab closes.
"""
import argparse
import hmac
import json
import os
import secrets
import shlex
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
sys.path.insert(0, HERE)
from vkb_common import (DEFAULT_AXES, EMU_AXES, default_profile, load_mapping,  # noqa: E402
                        mapping_path, normalize_mapping, read_config)

CTL = os.environ.get("VKB_HOTAS_CTL", "/run/vkb-hotas/ctl.sock")
PAGE = os.path.join(HERE, "vkb-mapper.html")
STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vkb-mapper")
IDLE_EXIT = 45  # seconds without any open UI before the server quits


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


class State:
    token = secrets.token_urlsafe(24)
    path = mapping_path()
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
        elif path == "/api/events":
            self._events()
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self):
        path = self._guard()
        if path is None:
            return
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

    def do_POST(self):
        path = self._guard()
        if path is None:
            return
        if path != "/api/pulse":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self._body().get("button"))
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1)
                s.connect(CTL)
                # wait for the daemon's hello so it has accepted us before we send and hang up
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        raise OSError("daemon closed the connection")
                    buf += chunk
                s.sendall(json.dumps({"cmd": "pulse", "button": n}).encode() + b"\n")
                time.sleep(0.05)
            self._send(200, {"ok": True})
        except (OSError, ValueError, TypeError, AttributeError) as e:
            self._send(503, {"error": f"daemon not reachable: {e}"})

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


def existing_instance():
    """URL of an already running mapper, if any."""
    try:
        url = open(os.path.join(STATE_DIR, "url")).read().strip()
        u = urllib.parse.urlparse(url)
        tok = urllib.parse.parse_qs(u.query)["t"][0]
        req = urllib.request.Request(f"{u.scheme}://{u.netloc}/api/mapping", headers={"X-Token": tok})
        urllib.request.urlopen(req, timeout=1).read()
        return url
    except Exception:
        try:
            os.unlink(os.path.join(STATE_DIR, "url"))  # stale: left by a killed instance
        except OSError:
            pass
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: random)")
    ap.add_argument("--no-browser", action="store_true", help="just print the URL")
    ap.add_argument("--mapping", help="mapping file to edit (default: ~/.config/vkb-hotas/mapping.json, "
                                      "which is what the daemon reads)")
    args = ap.parse_args()
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
    fd = os.open(os.path.join(STATE_DIR, "url"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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
            os.unlink(os.path.join(STATE_DIR, "url"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
