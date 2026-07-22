"""
Xvoice Writing — standalone entry point.
This is the executable for the Writing product only.
It does NOT include voice dictation.
"""
import sys
import os
import json
import time
import socket
import threading
import webbrowser
import logging
import logging.handlers

import pystray
from PIL import Image, ImageDraw

from writing.engine import WritingEngine

# ─── Config ───────────────────────────────────────────────────────────────────

RAILWAY_URL  = "https://voicetotext-keyboard-production.up.railway.app/api"
FRONTEND_URL = "https://happy-tiny-glance.lovable.app/writing/dashboard"
LOCAL_PORT   = 45680   # distinct from Dictation's 45678

if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "XvoiceWriting")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "XvoiceWriting")

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

INSTANCE_PORT = 45681   # distinct from Dictation's 45679

# ─── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(CONFIG_DIR, exist_ok=True)
log_path = os.path.join(CONFIG_DIR, "xvoice_writing.log")
handler  = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[handler, logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─── Token storage ────────────────────────────────────────────────────────────

def load_token() -> str | None:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f).get("token")
    except Exception:
        return None


def save_token(token: str) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"token": token}, f)


def clear_token() -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({}, f)
    except Exception:
        pass

# ─── Single-instance guard ────────────────────────────────────────────────────

_instance_lock: socket.socket | None = None


def _live_instance_running() -> bool:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1)
        probe.connect(("127.0.0.1", INSTANCE_PORT))
        probe.close()
        return True
    except OSError:
        return False


def acquire_instance_lock() -> bool:
    global _instance_lock
    for _ in range(3):
        try:
            _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _instance_lock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _instance_lock.bind(("127.0.0.1", INSTANCE_PORT))
            _instance_lock.listen(1)
            return True
        except OSError:
            if _live_instance_running():
                return False
            time.sleep(0.5)
    return False


def _focus_listener_thread():
    while True:
        try:
            conn, _ = _instance_lock.accept()
            conn.close()
        except Exception:
            break

# ─── Auth (OAuth via browser — same flow as Dictation) ───────────────────────

from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

auth_success = False


class _AuthHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global auth_success
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        token = params.get("token", [None])[0]
        if token:
            save_token(token)
            auth_success = True
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Xvoice Writing: authenticated! You can close this tab.</h2></body></html>")
        else:
            self.send_response(400)
            self.end_headers()


def require_auth():
    global auth_success
    token = load_token()
    if token:
        try:
            r = requests.get(
                f"{RAILWAY_URL}/writing/validate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            if r.status_code == 200 and r.json().get("allowed"):
                auth_success = True
                return
        except Exception:
            pass

    # Token missing / invalid → open browser login
    server = HTTPServer(("127.0.0.1", LOCAL_PORT), _AuthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webbrowser.open(f"{FRONTEND_URL}?callback=http://localhost:{LOCAL_PORT}")

    deadline = time.time() + 300  # 5-minute timeout
    while not auth_success and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()

# ─── System tray ──────────────────────────────────────────────────────────────

_writing_engine: WritingEngine | None = None
tray_icon = None


def _make_tray_icon() -> pystray.Icon:
    # Simple "W" icon in brand colour
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill="#7C3AED")   # purple
    d.text((18, 16), "W", fill="white")

    def open_dashboard(icon, item):
        webbrowser.open(FRONTEND_URL)

    def logout(icon, item):
        clear_token()
        icon.notify("Xvoice Writing", "Logged out. Restart to sign in again.")

    def quit_app(icon, item):
        if _writing_engine:
            _writing_engine.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
        pystray.MenuItem("Logout",         logout),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit",           quit_app),
    )
    return pystray.Icon("XvoiceWriting", img, "Xvoice Writing", menu)


def start_tray():
    global tray_icon
    tray_icon = _make_tray_icon()
    tray_icon.run()   # blocks

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not acquire_instance_lock():
        # Already running — focus it and open dashboard
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", INSTANCE_PORT))
            s.close()
        except Exception:
            pass
        webbrowser.open(FRONTEND_URL)
        sys.exit(0)

    threading.Thread(target=_focus_listener_thread, daemon=True).start()

    # Auth check
    require_auth()
    if not auth_success:
        logger.error("Authentication failed or timed out.")
        sys.exit(1)

    # Start the Writing Engine
    _writing_engine = WritingEngine(load_token)
    _writing_engine.start()

    start_tray()   # blocks — keeps app alive via tray
