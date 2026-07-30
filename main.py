import pyaudio
import wave
import sys
from pynput import keyboard as pk
import os
import subprocess
import array
import json
import time
import threading
import webbrowser
import socket
import tempfile
import secrets
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
import pystray
from PIL import Image, ImageDraw
from contextlib import contextmanager

if sys.platform == "win32":
    import winsound
else:
    winsound = None

# --- Configuration URLs ---
RAILWAY_URL          = "https://voicetotext-keyboard-production.up.railway.app/api"
FRONTEND_URL         = "https://xvoicekeyboard.com"           # Dictation dashboard
WRITING_DASHBOARD_URL = "https://xvoicekeyboard.com/writing/dashboard"  # Writing dashboard
LOCAL_PORT = 45678

__version__ = "1.2.0"

# --- Audio Settings ---
HOTKEY = 'f8'
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 480
# Temp dir used for per-recording unique files (see get_temp_files())
_TMPDIR = tempfile.gettempdir()

if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Xvoice")
else:  # Linux
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "Xvoice")
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

auth_success = False
# CSRF guard for the local login callback. The desktop generates this before
# opening the browser and only accepts a /auth POST that echoes it back. Without
# it, ANY web page open in the browser could POST a token to 127.0.0.1:45678/auth
# and silently bind this app to the attacker's account.
_expected_state: str | None = None
tray_icon = None
PREFERRED_LANGUAGE = "en"
IS_TRANSLATION_ENABLED = False
PLAN_PRODUCT = "dictation"   # updated after auth; 'dictation' | 'writing' | 'platform'
WRITING_ENABLED = False     # updated after auth; True if user has writing access (paid or trial)
_last_sync_time = 0.0

# ─────────────────────────────────────────────
#   Single-instance lock
#   We bind a loopback TCP socket for the lifetime
#   of the process.  A second launch finds the port
#   occupied and exits cleanly.
# ─────────────────────────────────────────────

INSTANCE_PORT = 45679          # arbitrary; distinct from LOCAL_PORT (45678)
_instance_lock: socket.socket | None = None

def _live_instance_running() -> bool:
    """Return True only if something is actively listening on INSTANCE_PORT."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1)
        probe.connect(("127.0.0.1", INSTANCE_PORT))
        probe.close()
        return True
    except OSError:
        return False

def acquire_instance_lock() -> bool:
    """Try to acquire the single-instance lock.

    Retries up to 3 times with a short delay so that a brief OS port-cleanup
    state after a crash/kill does NOT block the next launch.
    Only returns False when a genuinely live instance is detected.
    """
    global _instance_lock
    for attempt in range(3):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(("127.0.0.1", INSTANCE_PORT))
            sock.listen(5)
            _instance_lock = sock
            return True
        except OSError:
            sock.close()
            if _live_instance_running():
                return False        # real instance is up → we are the duplicate
            # Bind failed but nobody is listening → transient OS cleanup state
            # Wait and retry rather than incorrectly blocking startup
            time.sleep(1.5)

    # Last-chance check after retries
    return not _live_instance_running()  # start if nobody is actually there

def _focus_listener_thread():
    """Background thread (running instance only).
    Blocks on accept(); when a second launch connects it closes the
    connection immediately and fires a tray notification so the user
    knows the app is already alive in the system tray."""
    while True:
        try:
            conn, _ = _instance_lock.accept()
            conn.close()
            safe_notify(
                "Xvoice is already running — look for the mic icon in your system tray.",
                "Xvoice"
            )
        except Exception:
            break   # socket closed on exit — stop the thread cleanly

# ─────────────────────────────────────────────
#   Logging — writes to a file so you can always
#   find errors regardless of platform/bundling
# ─────────────────────────────────────────────

import logging
from logging.handlers import RotatingFileHandler

if sys.platform == "win32":
    LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    LOG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Logs", "Xvoice")
else:
    LOG_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "Xvoice", "logs")

# This runs at import. If it raises — a locked-down profile, a redirected
# LOCALAPPDATA, a read-only home — a windowed build dies here with no console and
# no dialog, so the user double-clicks Xvoice and simply nothing happens. Fall back
# to the temp directory instead; the app's job is dictation, not logging.
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    LOG_DIR = os.path.join(tempfile.gettempdir(), "Xvoice")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except OSError:
        LOG_DIR = tempfile.gettempdir()

LOG_FILE = os.path.join(LOG_DIR, "xvoice.log")

_handlers = []
try:
    _handlers.append(
        RotatingFileHandler(
            LOG_FILE, encoding="utf-8",
            maxBytes=5 * 1024 * 1024,   # 5 MB per file
            backupCount=2               # keep xvoice.log + 2 rotated backups
        )
    )
except OSError:
    pass   # unwritable path — carry on without a log file rather than not starting

# In a windowed build (console=False) PyInstaller sets sys.stdout to None, and
# StreamHandler then falls back to sys.stderr — also None. Every log call would
# fail internally and be silently swallowed. Only attach it if a console exists.
if sys.stdout is not None:
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    # INFO not DEBUG — suppresses internal HTTP noise. Set XVOICE_LOG_LEVEL=DEBUG to
    # diagnose a problem in the field without shipping a new build.
    level=os.getenv("XVOICE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("xvoice")
# Silence the noisy HTTP debug output from the requests/urllib3 libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logger.info(f"Xvoice v{__version__} starting. Platform: {sys.platform}")
logger.info(f"Log file: {LOG_FILE}")

# ─────────────────────────────────────────────
#   System Tray
# ─────────────────────────────────────────────

def _make_icon_image():
    """Draws a simple microphone icon as a 64×64 PIL image."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Purple background circle
    d.ellipse([0, 0, 63, 63], fill=(124, 58, 237, 255))
    # Microphone body
    d.rounded_rectangle([22, 10, 42, 38], radius=10, fill="white")
    # Mic stand arc
    d.arc([14, 24, 50, 52], start=0, end=180, fill="white", width=4)
    # Stand post
    d.line([32, 52, 32, 60], fill="white", width=4)
    d.line([24, 60, 40, 60], fill="white", width=4)
    return img

def _open_dashboard(icon, item):
    webbrowser.open(FRONTEND_URL)

def _open_download(icon, item):
    webbrowser.open(f"{FRONTEND_URL}/download")

def _view_logs(icon, item):
    if sys.platform == "win32":
        os.startfile(LOG_FILE)
    elif sys.platform == "darwin":
        subprocess.run(["open", LOG_FILE])
    else:
        subprocess.run(["xdg-open", LOG_FILE])

def _export_logs(icon, item):
    import shutil
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        dest = os.path.join(desktop, "xvoice_logs.txt")
        shutil.copy(LOG_FILE, dest)
        safe_notify(f"Logs exported to Desktop", "Xvoice")
    except Exception as e:
        logger.error(f"Failed to export logs: {e}")
        safe_notify("Failed to export logs", "Xvoice")

def _report_issue(icon, item):
    webbrowser.open("mailto:support@xvoice.com")

def _toggle_translation(icon, item):
    global IS_TRANSLATION_ENABLED
    new_state = not IS_TRANSLATION_ENABLED
    token = load_token()
    if token:
        try:
            r = requests.patch(
                f"{RAILWAY_URL}/auth/translation",
                headers={"Authorization": f"Bearer {token}"},
                json={"enabled": new_state},
                timeout=5
            )
            if r.status_code == 200:
                IS_TRANSLATION_ENABLED = new_state
                safe_notify(f"Translation {'Enabled' if new_state else 'Disabled'}", "Xvoice")
        except Exception as e:
            logger.error(f"Failed to toggle translation: {e}")
            safe_notify("Could not reach server.", "Update Failed")

def _logout(icon, item):
    # Invalidate all tokens server-side (logs out browser dashboard too)
    token = load_token()
    if token:
        try:
            requests.post(
                f"{RAILWAY_URL}/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
        except Exception:
            pass  # proceed even if network is down
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    safe_notify("Logged out from all devices", "Xvoice")

def _restart_app(icon, item):
    """Close the current instance and relaunch the app."""
    icon.stop()
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        subprocess.Popen([sys.executable])
    else:
        # Running as plain Python script
        subprocess.Popen([sys.executable, os.path.abspath(__file__)])
    os._exit(0)

def _quit_app(icon, item):
    icon.stop()
    os._exit(0)

def safe_notify(msg, title="Xvoice"):
    logger.info(f"Notification: [{title}] {msg}")
    if sys.platform.startswith("linux"):
        try:
            subprocess.run(["notify-send", title, msg], timeout=3)
        except Exception:
            pass
        return
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                timeout=3
            )
        except Exception:
            pass
        return
    if tray_icon is not None:
        try:
            tray_icon.notify(msg, title)
        except Exception:
            pass

# Language logic removed to rely entirely on webapp sync.

def start_tray():
    global tray_icon

    image = _make_icon_image()
    help_menu = pystray.Menu(
        pystray.MenuItem("View Logs", _view_logs),
        pystray.MenuItem("Export Logs", _export_logs),
        pystray.MenuItem("Report Issue", _report_issue)
    )

    # NOTE: default=True is intentionally omitted — on Windows it causes
    # pystray to render a duplicate clickable label at the very top of the
    # context menu (the extra "Xvoice" button the user sees).  Double-click
    # on the tray icon still works without it.
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", _open_dashboard),
        pystray.MenuItem(
            "Open Writing Dashboard",
            lambda icon, item: webbrowser.open(WRITING_DASHBOARD_URL),
            visible=lambda item: PLAN_PRODUCT in ("writing", "platform"),
        ),
        pystray.MenuItem("Download Page",  _open_download),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Enable Translation", _toggle_translation, checked=lambda item: IS_TRANSLATION_ENABLED),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Help", help_menu),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Refresh / Restart", _restart_app),
        pystray.MenuItem("Log Out",        _logout),
        pystray.MenuItem("Quit Xvoice",    _quit_app),
    )

    try:
        tray_icon = pystray.Icon("xvoice", image, f"Xvoice - Press {HOTKEY.upper()} to dictate", menu)
        tray_icon.run()
    except KeyboardInterrupt:
        os._exit(0)
    except Exception as e:
        logger.warning(f"System tray unavailable ({e}). Falling back to headless mode.")
        tray_icon = None

    # Headless fallback
    os_name = "macOS" if sys.platform == "darwin" else "Linux"
    logger.info(f"{os_name} detected: running in headless mode (no system tray).")
    print(f"{os_name} detected: running in headless mode. Press Ctrl+C to quit.")

    import signal
    def _handle_signal(sig, frame):
        os._exit(0)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while True:
        time.sleep(1)
# ─────────────────────────────────────────────
#   Token helpers
# ─────────────────────────────────────────────

def load_token():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f).get('access_token')
        except Exception:
            pass
    return None

def load_refresh_token():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f).get('refresh_token')
        except Exception:
            pass
    return None

def save_token(access_token, refresh_token=None):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Merge into the existing config instead of overwriting it, so we never wipe
    # keys owned by the Writing side (e.g. recent_lang) — dictation and writing
    # share config.json but must not clobber each other's local state.
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
        except Exception:
            data = {}
    data['access_token'] = access_token
    if refresh_token:
        data['refresh_token'] = refresh_token
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f)
    # These are credentials — the refresh token stays valid for 30 days. Default file
    # permissions leave them readable by every other account on the machine.
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass

# ─────────────────────────────────────────────
#   Magic Auth (one-time browser login)
# ─────────────────────────────────────────────

# Only these origins may talk to the local login server. The connect-desktop page
# is served from the production domain; nothing else has any business here.
_ALLOWED_LOGIN_ORIGINS = {
    "https://xvoicekeyboard.com",
    "https://www.xvoicekeyboard.com",
}


class AuthHandler(BaseHTTPRequestHandler):
    def _send_cors_origin(self):
        """Echo the request Origin only if it is allow-listed.

        A wildcard here let any site read our responses; combined with the missing
        state check it was the whole vulnerability. We now reflect only known-good
        origins, and fall back to the production domain for same-origin/no-Origin
        callers (e.g. the desktop itself).
        """
        origin = self.headers.get('Origin')
        allowed = origin if origin in _ALLOWED_LOGIN_ORIGINS else "https://xvoicekeyboard.com"
        self.send_header('Access-Control-Allow-Origin', allowed)

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_origin()
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/auth':
            length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            token = data.get('token')
            refresh = data.get('refresh_token')   # optional; sent by some frontend versions
            state = data.get('state', '')

            # CSRF check: the callback must echo the random state we minted before
            # opening the browser. A malicious page cannot guess it, so it cannot
            # complete the login even though it can reach this port.
            if not _expected_state or not hmac.compare_digest(str(state), _expected_state):
                logger.warning("Rejected /auth callback: state mismatch (possible CSRF).")
                self.send_response(403)
                self._send_cors_origin()
                self.end_headers()
                self.wfile.write(b'{"status":"forbidden"}')
                return

            if token:
                save_token(token, refresh)
                self.send_response(200)
                self._send_cors_origin()
                self.end_headers()
                self.wfile.write(b'{"status":"success"}')
                global auth_success
                auth_success = True
            else:
                self.send_response(400)
                self._send_cors_origin()
                self.end_headers()

        elif self.path == '/logout':
            # The web app posts here when the user logs out in the browser, but no
            # such route existed — the handler fell through without sending any
            # response at all. Logout still propagated eventually (the server bumps
            # token_version, so the next API call 401s), but not immediately.
            logger.info("Logout ping received from browser; clearing local token.")
            try:
                if os.path.exists(CONFIG_FILE):
                    os.remove(CONFIG_FILE)
            except OSError as e:
                logger.warning(f"Could not remove config on logout ping: {e}")
            self.send_response(200)
            self._send_cors_origin()
            self.end_headers()
            self.wfile.write(b'{"status":"logged_out"}')

        else:
            # Always answer something. Falling through left the caller hanging.
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass          # silence server logs

# ─────────────────────────────────────────────
#   Internet connectivity helper
# ─────────────────────────────────────────────
#   Silent token refresh
# ─────────────────────────────────────────────

need_reauth = False   # set by transcribe_audio; read by voice_loop

def try_silent_refresh() -> bool:
    """Use the stored refresh token to silently get a new access token.
    Returns True if successful (new access token saved), False otherwise."""
    refresh = load_refresh_token()
    if not refresh:
        return False
    try:
        r = requests.post(
            f"{RAILWAY_URL}/auth/refresh",
            json={"refresh_token": refresh},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            save_token(data['access_token'], data.get('refresh_token', refresh))
            logger.info("Access token silently refreshed.")
            return True
    except Exception as e:
        logger.warning(f"Silent refresh failed: {e}")
    return False

# ─────────────────────────────────────────────

def has_internet(timeout: float = 3.0) -> bool:
    """Return True if we can reach at least one of several well-known hosts.

    Tries multiple targets so that corporate firewalls blocking a specific
    IP (e.g. 8.8.8.8) don't cause a false negative.
    Uses per-socket timeout — does NOT touch socket.setdefaulttimeout()
    so other sockets in the process are unaffected.
    """
    # Try our own API over HTTPS first. Port 53 is blocked outbound on plenty of
    # corporate and campus networks, which produced a false "no internet" and left
    # the app stuck in its wait loop on exactly the machines it needed to work on.
    # Reaching the backend is also the thing we actually care about.
    try:
        requests.head(f"{RAILWAY_URL.rsplit('/api', 1)[0]}/health", timeout=timeout)
        return True
    except Exception:
        pass

    targets = [
        ("8.8.8.8",       53),   # Google DNS
        ("1.1.1.1",       53),   # Cloudflare DNS
        ("208.67.222.222", 53),  # OpenDNS
    ]
    for host, port in targets:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)          # per-socket, not global
            sock.connect((host, port))
            sock.close()
            return True
        except OSError:
            continue
    return False

def wait_for_internet(poll_interval: float = 5.0) -> None:
    """Block until an internet connection is detected.
    Logs a message only the first time we enter the wait."""
    if has_internet():
        return
    logger.info("No internet connection detected. Waiting…")
    safe_notify("Xvoice will connect as soon as the network is available.", "No internet")
    while not has_internet():
        time.sleep(poll_interval)
    logger.info("Internet connection restored.")

def require_auth():
    global auth_success, PREFERRED_LANGUAGE, IS_TRANSLATION_ENABLED, PLAN_PRODUCT, WRITING_ENABLED

    # ── Wait for a live network before touching any endpoint ──
    wait_for_internet()

    token = load_token()

    if token:
        try:
            r = requests.get(
                f"{RAILWAY_URL}/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
            if r.status_code == 200 and r.json().get('allowed'):
                received_key = r.json().get('custom_hotkey', 'f8')
                set_dynamic_hotkey(received_key)
                PREFERRED_LANGUAGE = r.json().get('preferred_language', 'en')
                IS_TRANSLATION_ENABLED = r.json().get('is_translation_enabled', False)
                PLAN_PRODUCT = r.json().get('plan_product', 'dictation')
                WRITING_ENABLED = r.json().get('writing_enabled', False)
                _sync_timezone()
                return True
        except Exception:
            pass

        # Access token rejected — try silent refresh before falling back to browser
        logger.info("Access token invalid at startup; trying silent refresh.")
        if try_silent_refresh():
            refreshed = load_token()
            try:
                r = requests.get(
                    f"{RAILWAY_URL}/auth/validate",
                    headers={"Authorization": f"Bearer {refreshed}"},
                    timeout=5
                )
                if r.status_code == 200 and r.json().get('allowed'):
                    received_key = r.json().get('custom_hotkey', 'f8')
                    set_dynamic_hotkey(received_key)
                    PREFERRED_LANGUAGE = r.json().get('preferred_language', 'en')
                    IS_TRANSLATION_ENABLED = r.json().get('is_translation_enabled', False)
                    PLAN_PRODUCT = r.json().get('plan_product', 'dictation')
                    WRITING_ENABLED = r.json().get('writing_enabled', False)
                    safe_notify(f"Xvoice is ready. Press {HOTKEY.upper()} to dictate.", "Session renewed")
                    _sync_timezone()
                    return True
            except Exception:
                pass

    # Must do full browser login — tell the user so they know why F8 is quiet
    logger.info("Opening browser for re-authentication.")
    safe_notify(
        "Sign in required — check your browser to reconnect.",
        "Xvoice"
    )
    # Mint a fresh CSRF state for this login attempt and pass it to the browser.
    # The connect-desktop page echoes it back in the /auth POST; do_POST rejects any
    # callback that does not match.
    global _expected_state
    _expected_state = secrets.token_urlsafe(24)
    webbrowser.open(f"{FRONTEND_URL}/connect-desktop?state={_expected_state}")

    server = HTTPServer(('127.0.0.1', LOCAL_PORT), AuthHandler)
    server.timeout = 1
    last_reminder = time.time()
    while not auth_success:
        server.handle_request()
        # Remind every 30 s so the user doesn't wonder why nothing works
        if time.time() - last_reminder > 30:
            safe_notify(
                "Still waiting — please sign in via your browser.",
                "Xvoice"
            )
            last_reminder = time.time()

    # Now that we are logged in, fetch the hotkey
    token = load_token()
    if token:
        try:
            r = requests.get(
                f"{RAILWAY_URL}/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
            if r.status_code == 200:
                set_dynamic_hotkey(r.json().get('custom_hotkey', 'f8'))
                PREFERRED_LANGUAGE = r.json().get('preferred_language', 'en')
                IS_TRANSLATION_ENABLED = r.json().get('is_translation_enabled', False)
                PLAN_PRODUCT = r.json().get('plan_product', 'dictation')
                WRITING_ENABLED = r.json().get('writing_enabled', False)
        except Exception:
            pass

    safe_notify(f"Xvoice is ready. Press {HOTKEY.upper()} to dictate.", "Connected!")
    _sync_timezone()
    return True

def _sync_timezone():
    """Send the system's IANA timezone to the backend so stats use the correct local time."""
    token = load_token()
    if not token:
        return
    try:
        # Python 3.9+ has zoneinfo; detect system IANA tz name
        import datetime as _dt
        local_tz = _dt.datetime.now().astimezone().tzinfo
        tz_name = str(local_tz)
        # On Windows, astimezone().tzinfo gives an offset, not IANA name.
        # Try tzlocal for a proper IANA name.
        try:
            from tzlocal import get_localzone
            tz_name = str(get_localzone())
        except ImportError:
            # Fallback: use time.timezone offset to guess (less precise)
            import time as _time
            offset_hours = -_time.timezone // 3600
            # Map common Indian offset
            tz_map = {
                5: "Asia/Kolkata", 0: "UTC", 1: "Europe/London",
                -5: "America/New_York", -8: "America/Los_Angeles",
                8: "Asia/Shanghai", 9: "Asia/Tokyo",
            }
            tz_name = tz_map.get(offset_hours, "UTC")

        requests.patch(
            f"{RAILWAY_URL}/auth/timezone",
            headers={"Authorization": f"Bearer {token}"},
            json={"timezone": tz_name},
            timeout=5
        )
        logger.info(f"Timezone synced: {tz_name}")
    except Exception as e:
        logger.debug(f"Timezone sync skipped: {e}")

# ─────────────────────────────────────────────
#   Startup registration
# ─────────────────────────────────────────────

def setup_startup():
    if getattr(sys, 'frozen', False):
        exec_cmd = os.path.realpath(sys.executable)
    else:
        exec_cmd = f'"{sys.executable}" "{os.path.realpath(__file__)}"'

    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_ALL_ACCESS
            ) as key:
                winreg.SetValueEx(key, "Xvoice", 0, winreg.REG_SZ, exec_cmd)
        except Exception:
            pass

# ─────────────────────────────────────────────
#   Hotkey listener
# ─────────────────────────────────────────────

hotkey_pressed = False

def _to_key(s):
    s = s.lower().replace(" ", "_")
    
    # Map frontend human-readable strings to pynput Key attributes
    mapping = {
        "right_alt": "alt_r", "left_alt": "alt_l",
        "right_ctrl": "ctrl_r", "left_ctrl": "ctrl_l",
        "right_shift": "shift_r", "left_shift": "shift_l",
    }
    s = mapping.get(s, s)

    if hasattr(pk.Key, s):
        return getattr(pk.Key, s)
    return pk.KeyCode.from_char(s)

KEY_OBJ = _to_key(HOTKEY)

def set_dynamic_hotkey(new_key):
    global HOTKEY, KEY_OBJ, tray_icon
    if new_key:
        HOTKEY = new_key.lower()
        KEY_OBJ = _to_key(HOTKEY)
        logger.info(f"System hotkey successfully updated to: {HOTKEY.upper()}")
        if tray_icon:
            tray_icon.title = f"Xvoice - Press {HOTKEY.upper()} to dictate"

def on_press(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        if not hotkey_pressed:
            # DEBUG, not INFO: at INFO this writes a timestamped record of every
            # dictation to disk, which is more retained activity data than needed.
            logger.debug(f"Hotkey {HOTKEY.upper()} pressed")
        hotkey_pressed = True

def on_release(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        hotkey_pressed = False

# ─────────────────────────────────────────────
#   Self-healing keyboard listener
#   Restarts automatically if pynput ever dies
#   (e.g. after screen-lock, session switch, or
#   a driver hiccup on startup)
# ─────────────────────────────────────────────

def _listener_watchdog():
    """Keeps the pynput listener alive. If it ever stops for any reason
    (crash, OS session event, etc.) it restarts after a short delay."""
    while True:
        try:
            logger.info("Starting keyboard listener…")
            with pk.Listener(on_press=on_press, on_release=on_release) as lst:
                lst.join()          # blocks until the listener thread exits
        except Exception as e:
            logger.error(f"Keyboard listener crashed: {e}")
        logger.warning("Keyboard listener stopped — restarting in 2 s…")
        global hotkey_pressed
        hotkey_pressed = False          # clear stale press state on restart
        time.sleep(2)

threading.Thread(target=_listener_watchdog, daemon=True).start()

def wait_hotkey(_):
    while not hotkey_pressed:
        time.sleep(0.01)

def is_pressed(_):
    return hotkey_pressed

def _paste_text(text):
    """Insert text via the clipboard and Ctrl+V (Cmd+V on macOS).

    Atomic from the target application's point of view: the whole string arrives in
    one operation, so there is no per-character race to lose.

    Raises on failure so the caller can fall back to keystroke typing.
    """
    import pyperclip

    try:
        prev = pyperclip.paste() or ""
    except Exception:
        prev = ""

    # Put our text on the clipboard and CONFIRM it landed before pasting. If the
    # copy silently failed, pasting would insert whatever was on the clipboard
    # before — dumping unrelated content into the user's document, which is far
    # worse than a dropped character. Retry briefly; some clipboard backends are
    # slow to make a write visible.
    placed = False
    for _ in range(5):
        pyperclip.copy(text)
        time.sleep(0.03)
        try:
            if pyperclip.paste() == text:
                placed = True
                break
        except Exception:
            pass
    if not placed:
        raise RuntimeError("clipboard did not accept the text")

    ctrl = pk.Controller()
    mod = pk.Key.cmd if sys.platform == "darwin" else pk.Key.ctrl
    ctrl.press(mod); ctrl.press('v'); ctrl.release('v'); ctrl.release(mod)

    # Give the target app time to consume the paste before we put the old clipboard
    # back. Restoring too early can make the app paste the PREVIOUS contents.
    time.sleep(0.25)
    try:
        pyperclip.copy(prev)
    except Exception:
        pass


def write_text(text):
    """Insert transcribed text into the focused application.

    Clipboard paste is the primary path. pynput's type() sends one synthetic
    keystroke per character, which races the target application's input queue —
    busy apps, Electron apps, browsers and remote sessions silently drop characters,
    producing exactly the "missing a letter in the middle of a word" symptom. A
    paste is a single atomic operation and cannot be partially applied.

    Note this was already the intent — there was a paste fallback here — but it only
    triggered when type() raised an *exception*. Dropped characters raise nothing:
    typing "succeeds" while quietly losing text, so the fallback never fired for the
    one failure mode it was meant to cover.

    Set XVOICE_INSERT_METHOD=type to force keystroke typing (useful in apps where
    Ctrl+V is bound to something else, e.g. some terminals).
    """
    method = os.getenv("XVOICE_INSERT_METHOD", "paste").strip().lower()

    if method == "type":
        pk.Controller().type(text)
        return

    try:
        _paste_text(text)
    except Exception as e:
        logger.warning(f"Clipboard paste failed ({e}); falling back to keystroke typing.")
        try:
            pk.Controller().type(text)
        except Exception as e2:
            logger.error(f"Keystroke typing fallback also failed: {e2}")

# No voice-activity detection. The recording is captured whole and sent as-is.
#
# It previously ran every 30 ms frame through webrtcvad and made keep/drop decisions.
# That was the wrong tool for the job: quiet consonants get classified as
# non-speech, so letters were deleted before upload, and because dropped frames were
# removed rather than silenced the audio was spliced mid-word. The transcription
# model is trained on real-world continuous audio — it handles silence and background
# noise far better than frame-level gating ever did, and it cannot recover audio we
# threw away.
#
# The only thing still worth checking locally is whether the user actually said
# anything, so a mis-tap of the hotkey doesn't cost a network round-trip. That needs
# nothing more than a peak-amplitude reading.

# 16-bit samples run to ±32767. A real utterance peaks in the thousands even when
# softly spoken; room tone and mic self-noise sit far below this.
SILENCE_PEAK_THRESHOLD = 500


def _peak_amplitude(pcm: bytes) -> int:
    """Largest absolute 16-bit sample in a little-endian PCM buffer.

    Uses array rather than audioop: audioop is deprecated from 3.11 and removed in
    3.13, and this app runs on whatever Python the user happens to have.
    """
    try:
        samples = array.array('h')
        # frombytes needs a whole number of samples
        samples.frombytes(pcm[: len(pcm) - (len(pcm) % samples.itemsize)])
        if sys.byteorder == 'big':
            samples.byteswap()
        if not samples:
            return 0
        return max(max(samples), -min(samples))
    except Exception:
        # Never let a measurement problem discard a real recording.
        return SILENCE_PEAK_THRESHOLD + 1

# ─────────────────────────────────────────────
#   Audio pipeline
# ─────────────────────────────────────────────

@contextmanager
def suppress_stderr():
    """Context manager to suppress C-level stderr (used to silence ALSA/JACK warnings)."""
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stderr = os.dup(sys.stderr.fileno())
        os.dup2(null_fd, sys.stderr.fileno())
    except Exception:
        yield
        return

    try:
        yield
    finally:
        try:
            os.dup2(saved_stderr, sys.stderr.fileno())
            os.close(saved_stderr)
            os.close(null_fd)
        except Exception:
            pass

# ─────────────────────────────────────────────
#   "Listening" overlay (screen-edge glow + island)
#   Shown while actively recording dictation.
# ─────────────────────────────────────────────
def _show_listening():
    try:
        from writing.ui.listening_overlay import show_listening
        show_listening()
    except Exception as e:
        logger.error(f"show_listening failed: {e}")

def _hide_listening():
    try:
        from writing.ui.listening_overlay import hide_listening
        hide_listening()
    except Exception as e:
        logger.error(f"hide_listening failed: {e}")

def record_audio(output_filename):
    with suppress_stderr():
        audio = pyaudio.PyAudio()

    wait_hotkey(HOTKEY)
    _show_listening()          # glow on — recording starts now
    if winsound:
        winsound.Beep(1000, 100)

    try:
        with suppress_stderr():
            stream = audio.open(
                format=FORMAT, channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=CHUNK
            )
    except Exception:
        audio.terminate()
        while is_pressed(HOTKEY):
            time.sleep(0.1)
        return False

    # Capture the whole recording, unmodified. No per-frame decisions at all — just
    # read the microphone for as long as the key is held.
    frames = []
    while is_pressed(HOTKEY):
        try:
            frames.append(stream.read(CHUNK, exception_on_overflow=False))
        except IOError:
            pass


    if winsound:
        winsound.Beep(800, 100)
    stream.stop_stream()
    stream.close()
    # Read the sample size before terminating: get_sample_size() was being called
    # on an already-released PyAudio instance below.
    sample_width = audio.get_sample_size(FORMAT)
    audio.terminate()

    if not frames:
        return False

    audio_bytes = b''.join(frames)

    # Only guard: was anything actually said? This skips a pointless upload after a
    # mis-tap of the hotkey. It looks at the whole recording, so nothing is trimmed
    # or altered — it either all goes or none of it does.
    peak = _peak_amplitude(audio_bytes)
    if peak < SILENCE_PEAK_THRESHOLD:
        logger.info(f"Recording is silent (peak {peak} < {SILENCE_PEAK_THRESHOLD}); skipping upload.")
        return False

    logger.debug(
        f"Captured {len(frames)} frames "
        f"({len(frames) * CHUNK / RATE:.2f}s, peak {peak}) — sending unmodified"
    )

    with wave.open(output_filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(sample_width)
        wf.setframerate(RATE)
        wf.writeframes(audio_bytes)
    return True

def normalize_audio(input_file, output_file):
    try:
        # Use the bundle's own directory for ffmpeg when frozen, else PATH.
        # The binary is only called ffmpeg.exe on Windows — this branch used that
        # name unconditionally, so frozen macOS and Linux builds looked for a file
        # that could never exist. normalize_audio then returned False and raw,
        # un-normalized audio was sent, quietly degrading accuracy on exactly the
        # platforms the .dmg and .AppImage target.
        exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        if getattr(sys, 'frozen', False):
            bundled = os.path.join(os.path.dirname(sys.executable), exe_name)
            ffmpeg = bundled if os.path.isfile(bundled) else "ffmpeg"
        elif sys.platform == "win32":
            local = os.path.join(os.path.dirname(os.path.abspath(__file__)), exe_name)
            ffmpeg = local if os.path.isfile(local) else "ffmpeg"
        else:
            ffmpeg = "ffmpeg"
        subprocess.run(
            [ffmpeg, "-y", "-i", input_file,
             "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", "-ar", "16000", output_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return True
    except Exception:
        return False

def transcribe_audio(audio_path):
    global need_reauth
    token = load_token()
    logger.info(f"Transcribing {os.path.basename(audio_path)}...")
    try:
        with open(audio_path, 'rb') as f:
            r = requests.post(
                f"{RAILWAY_URL}/transcribe",
                headers={"Authorization": f"Bearer {token}"},
                files={'file': f},
                data={'language': PREFERRED_LANGUAGE},
                timeout=30
            )

        if r.status_code == 200:
            text = r.json().get('text', '').strip()
            if text:
                write_text(text + " ")
                if winsound:
                    winsound.Beep(1200, 50)
            else:
                logger.info("Transcription returned empty text.")
        elif r.status_code == 403:
            safe_notify("Upgrade on the dashboard to continue.", "Trial Expired")
        elif r.status_code == 401:
            logger.warning("401 received — attempting silent token refresh.")
            if try_silent_refresh():
                # Got a fresh token; next F8 press will use it automatically
                safe_notify("Xvoice reconnected automatically.", "Session renewed")
            else:
                # Refresh token missing or expired — need full re-login
                if os.path.exists(CONFIG_FILE):
                    os.remove(CONFIG_FILE)
                need_reauth = True
                safe_notify("Xvoice will reconnect — check your browser.", "Session expired")
        elif r.status_code == 429:
            safe_notify("Try again in a moment.", "Server busy")
        else:
            # 5xx and anything else. Without this the user holds the hotkey, speaks,
            # and absolutely nothing happens — no text, no beep, no message.
            logger.error(f"Transcription failed: HTTP {r.status_code} — {r.text[:200]}")
            safe_notify("Transcription failed — please try again.", "Xvoice")
    except Exception as e:
        logger.error(f"Transcription request failed: {e}")
        safe_notify(str(e)[:80], "Connection error")

def get_temp_files() -> tuple[str, str]:
    """Return two unique writable temp file paths for a single recording.
    Using unique names per recording avoids file-lock races from:
      - Windows Defender scanning a previous file
      - A crash leaving a handle open
      - ffmpeg still flushing while a new recording starts
    """
    raw  = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir=_TMPDIR)
    norm = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir=_TMPDIR)
    raw.close()
    norm.close()
    return raw.name, norm.name

# ─────────────────────────────────────────────
#   Main
# ─────────────────────────────────────────────

_writing_engine = None

def _background_sync():
    """Silently fetches the latest preferences from the server."""
    global PREFERRED_LANGUAGE, IS_TRANSLATION_ENABLED, PLAN_PRODUCT, WRITING_ENABLED
    token = load_token()
    if not token:
        return
    try:
        r = requests.get(
            f"{RAILWAY_URL}/auth/validate",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3
        )
        if r.status_code == 200 and r.json().get('allowed'):
            PREFERRED_LANGUAGE = r.json().get('preferred_language', PREFERRED_LANGUAGE)
            IS_TRANSLATION_ENABLED = r.json().get('is_translation_enabled', IS_TRANSLATION_ENABLED)
            PLAN_PRODUCT = r.json().get('plan_product', PLAN_PRODUCT)
            WRITING_ENABLED = r.json().get('writing_enabled', WRITING_ENABLED)
    except Exception:
        pass

def _maybe_start_writing_engine():
    """Start the Writing Engine once, AFTER require_auth() has populated the
    entitlement globals (WRITING_ENABLED / PLAN_PRODUCT).

    This must not run before auth completes: require_auth() runs inside
    voice_loop's thread and does network calls, so the __main__ thread would
    otherwise read WRITING_ENABLED while it's still the default False and never
    start writing. Idempotent — the guard makes repeat calls safe.
    """
    global _writing_engine
    if _writing_engine is not None:
        return
    if WRITING_ENABLED or PLAN_PRODUCT in ("writing", "platform"):
        try:
            from writing.engine import WritingEngine
            _writing_engine = WritingEngine(load_token)
            _writing_engine.start()
            logger.info(f"Writing Engine started (plan: {PLAN_PRODUCT}, writing_enabled: {WRITING_ENABLED}).")
        except Exception as e:
            logger.error(f"Writing Engine failed to start (dictation continues): {e}")

def voice_loop():
    """Runs the F8 recording loop in a background thread.
    An outer watchdog catches any crash, logs it, and restarts automatically
    so the thread never dies silently and leaves the app a zombie."""
    global need_reauth, auth_success
    while True:                          # ── watchdog: restart on any crash ──
        try:
            require_auth()
            _maybe_start_writing_engine()   # after auth → WRITING_ENABLED is known
            while True:
                if need_reauth:
                    need_reauth = False
                    auth_success = False    # reset so login server loop runs
                    logger.info("Re-authenticating after session expiry.")
                    require_auth()
                    _maybe_start_writing_engine()   # entitlement may now be active
                
                # Periodically sync dictation settings from the webapp (every 10s)
                # We do this check non-blockingly right before recording if it's been a while
                global _last_sync_time
                if time.time() - _last_sync_time > 10:
                    _last_sync_time = time.time()
                    threading.Thread(target=_background_sync, daemon=True).start()

                raw_file, norm_file = get_temp_files()   # unique per recording

                try:
                    has_speech = record_audio(raw_file)
                finally:
                    _hide_listening()    # glow off the moment recording ends
                if not has_speech:
                    # No audio captured; clean up and wait for next press
                    for f in (raw_file, norm_file):
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                    continue

                if os.path.exists(raw_file):
                    ok = normalize_audio(raw_file, norm_file)
                    transcribe_audio(norm_file if ok else raw_file)

                for f in (raw_file, norm_file):
                    try:
                        if os.path.exists(f):
                            os.remove(f)
                    except Exception:
                        pass   # file may be locked by AV scan; not fatal

        except Exception as e:
            logger.error(f"voice_loop crashed: {e}. Restarting in 5 s…")
            safe_notify("An error occurred — recovering automatically.", "Xvoice restarting")
            time.sleep(5)
            # Reset flags so require_auth() runs fresh on restart
            auth_success = False
            need_reauth  = False

if __name__ == "__main__":
    
    # ── Single-instance guard ──────────────────
    if not acquire_instance_lock():
        logger.info("Another instance is running; sending focus ping.")
        # 1. Ping the running instance → triggers its tray notification
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", INSTANCE_PORT))
            s.close()
        except Exception:
            pass
        # 2. Open the dashboard so the user has clear, visible feedback
        #    (they see the browser → know the app is already running)
        webbrowser.open(FRONTEND_URL)
        sys.exit(0)
    # ──────────────────────────────────────────

    # Start the focus-ping listener so future duplicate launches are
    # handled gracefully (daemon=True → dies with the main process).
    threading.Thread(target=_focus_listener_thread, daemon=True).start()

    setup_startup()

    # Start the shared Qt UI host (powers the "listening" glow + writing overlays).
    try:
        from writing.ui.qt_host import QtHost
        QtHost.instance().start()
    except Exception as e:
        logger.error(f"Failed to start Qt UI host: {e}")

    # Start voice dictation loop
    t = threading.Thread(target=voice_loop, daemon=True)
    t.start()

    # NOTE: the Writing Engine is started from inside voice_loop via
    # _maybe_start_writing_engine(), AFTER require_auth() has populated
    # WRITING_ENABLED / PLAN_PRODUCT. Starting it here would race that background
    # auth and read the flags before they are set (they'd still be False), which
    # is exactly why writing never started. Do not re-add a start call here.

    start_tray()   # blocks here — keeps app alive via tray