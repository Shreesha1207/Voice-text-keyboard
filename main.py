import pyaudio
import wave
import sys
from pynput import keyboard as pk
import os
import subprocess
import webrtcvad
import json
import time
import threading
import webbrowser
import socket
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
RAILWAY_URL = "https://voicetotext-keyboard-production.up.railway.app/api"
FRONTEND_URL = "https://happy-tiny-glance.lovable.app"
LOCAL_PORT = 45678

# --- Audio Settings ---
HOTKEY = 'f8'
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 480
RAW_FILE = "temp_raw.wav"
NORM_FILE = "temp_norm.wav"

if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Xvoice")
else:  # Linux
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "Xvoice")
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

auth_success = False
tray_icon = None

# ─────────────────────────────────────────────
#   Single-instance lock
#   We bind a loopback TCP socket for the lifetime
#   of the process.  A second launch finds the port
#   occupied and exits cleanly.
# ─────────────────────────────────────────────

INSTANCE_PORT = 45679          # arbitrary; distinct from LOCAL_PORT (45678)
_instance_lock: socket.socket | None = None

def acquire_instance_lock() -> bool:
    """Try to acquire the single-instance lock.
    Returns True if this is the only instance, False otherwise."""
    global _instance_lock
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", INSTANCE_PORT))
        sock.listen(5)
        _instance_lock = sock          # keep alive for the process lifetime
        return True
    except OSError:
        return False

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

if sys.platform == "win32":
    LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    LOG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Logs", "Xvoice")
else:
    LOG_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "Xvoice", "logs")

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "xvoice.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("xvoice")
logger.info(f"Xvoice starting. Platform: {sys.platform}")
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

def _logout(icon, item):
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    safe_notify("Logged out", "Xvoice")

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

def start_tray():
    global tray_icon

    image = _make_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", _open_dashboard, default=True),
        pystray.MenuItem("Download Page",  _open_download),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Log Out",        _logout),
        pystray.MenuItem("Quit Xvoice",    _quit_app),
    )

    try:
        tray_icon = pystray.Icon("xvoice", image, "Xvoice - Press F8 to dictate", menu)
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

def save_token(access_token):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'access_token': access_token}, f)

# ─────────────────────────────────────────────
#   Magic Auth (one-time browser login)
# ─────────────────────────────────────────────

class AuthHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/auth':
            length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            token = data.get('token')
            if token:
                save_token(token)
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"success"}')
                global auth_success
                auth_success = True
            else:
                self.send_response(400)
                self.end_headers()

    def log_message(self, format, *args):
        pass          # silence server logs

# ─────────────────────────────────────────────
#   Internet connectivity helper
# ─────────────────────────────────────────────

def has_internet(timeout: float = 3.0) -> bool:
    """Return True if we can reach at least one of several well-known hosts.

    Tries multiple targets so that corporate firewalls blocking a specific
    IP (e.g. 8.8.8.8) don't cause a false negative.
    Uses per-socket timeout — does NOT touch socket.setdefaulttimeout()
    so other sockets in the process are unaffected.
    """
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
    safe_notify("No internet", "Xvoice will connect as soon as the network is available.")
    while not has_internet():
        time.sleep(poll_interval)
    logger.info("Internet connection restored.")

def require_auth():
    global auth_success

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
                return True
        except Exception:
            pass

    # No valid token — trigger web login
    # (internet is guaranteed at this point)
    webbrowser.open(f"{FRONTEND_URL}/connect-desktop")

    server = HTTPServer(('127.0.0.1', LOCAL_PORT), AuthHandler)
    server.timeout = 1
    while not auth_success:
        server.handle_request()

    if tray_icon:
        safe_notify("Connected!", "Xvoice is ready. Press F8 to dictate.")
    return True

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
    if s.lower().startswith('f') and s[1:].isdigit():
        return getattr(pk.Key, s.lower())
    return pk.KeyCode.from_char(s)

KEY_OBJ = _to_key(HOTKEY)

def on_press(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        hotkey_pressed = True

def on_release(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        hotkey_pressed = False

try:
    listener = pk.Listener(on_press=on_press, on_release=on_release)
    listener.start()
except Exception as e:
    logger.warning(f"Keyboard listener failed to start: {e}")
    listener = None

def wait_hotkey(_):
    while not hotkey_pressed:
        time.sleep(0.01)

def is_pressed(_):
    return hotkey_pressed

def write_text(text):
    pk.Controller().type(text)

vad = webrtcvad.Vad(1)

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

def record_audio(output_filename):
    with suppress_stderr():
        audio = pyaudio.PyAudio()
        
    wait_hotkey(HOTKEY)
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

    frames = []
    while is_pressed(HOTKEY):
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            if vad.is_speech(data, RATE):
                frames.append(data)
        except IOError:
            pass

    if winsound:
        winsound.Beep(800, 100)
    stream.stop_stream()
    stream.close()
    audio.terminate()

    if not frames:
        return False

    with wave.open(output_filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
    return True

def normalize_audio(input_file, output_file):
    try:
        ffmpeg = os.path.join(os.getcwd(), "ffmpeg.exe") if sys.platform == "win32" else "ffmpeg"
        subprocess.run(
            [ffmpeg, "-y", "-i", input_file,
             "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", "-ar", "16000", output_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return True
    except Exception:
        return False

def transcribe_audio(audio_file):
    token = load_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with open(audio_file, "rb") as f:
            response = requests.post(
                f"{RAILWAY_URL}/transcribe",
                headers=headers,
                files={"file": ("audio.wav", f, "audio/wav")},
                timeout=30
            )

        if response.status_code == 200:
            text = response.json().get("text", "").strip()
            if text:
                write_text(text + " ")
                if winsound:
                    winsound.Beep(1200, 50)
        elif response.status_code == 403:
            safe_notify("Trial Expired", "Upgrade on the dashboard to continue.")
        elif response.status_code == 401:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            safe_notify("Session expired", "Restart Xvoice to log in again.")
        elif response.status_code == 429:
            safe_notify("Server busy", "Try again in a moment.")
    except Exception as e:
        safe_notify("Connection error", str(e)[:80])

# ─────────────────────────────────────────────
#   Main
# ─────────────────────────────────────────────

def voice_loop():
    """Runs the F8 recording loop in a background thread."""
    require_auth()
    while True:
        has_speech = record_audio(RAW_FILE)
        if not has_speech:
            continue
        if os.path.exists(RAW_FILE):
            ok = normalize_audio(RAW_FILE, NORM_FILE)
            transcribe_audio(NORM_FILE if ok else RAW_FILE)
            for f in (RAW_FILE, NORM_FILE):
                if os.path.exists(f):
                    os.remove(f)

if __name__ == "__main__":
    # ── Single-instance guard ──────────────────
    if not acquire_instance_lock():
        # Ping the running instance so it flashes a tray notification,
        # then exit silently — no dialog, no "quit first" friction.
        logger.info("Another instance is running; sending focus ping and exiting.")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", INSTANCE_PORT))
            s.close()
        except Exception:
            pass   # running instance may be starting up; safe to ignore
        sys.exit(0)
    # ──────────────────────────────────────────

    # Start the focus-ping listener so future duplicate launches are
    # handled gracefully (daemon=True → dies with the main process).
    threading.Thread(target=_focus_listener_thread, daemon=True).start()

    setup_startup()
    t = threading.Thread(target=voice_loop, daemon=True)
    t.start()
    start_tray()   # blocks here — keeps app alive via tray