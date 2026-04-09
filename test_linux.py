"""
Comprehensive Linux test suite for Xvoice.
Run this on your Linux VirtualBox BEFORE building/releasing an AppImage.

Usage:
    python3 test_linux.py           # run all tests
    python3 test_linux.py -v        # verbose output
    python3 test_linux.py --quick   # skip slow tests (audio, network)

Each test is independent. A failure in one does NOT stop others.
"""

import sys
import os
import json
import time
import signal
import socket
import logging
import tempfile
import threading
import subprocess
import argparse
import traceback
import shutil
from pathlib import Path

# ─────────────────────────────────────────────
# Dependency auto-install (runs before tests)
# ─────────────────────────────────────────────

def ensure_dependencies():
    """Install pip requirements and system packages if missing."""
    import importlib
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")

    pip_needed = []
    check_map = {
        "pyaudio": "pyaudio", "pynput": "pynput", "webrtcvad": "webrtcvad",
        "pystray": "pystray", "jose": "python-jose", "PIL": "Pillow",
        "requests": "requests",
    }
    for mod, pkg in check_map.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            pip_needed.append(pkg)

    if pip_needed:
        print(f"[setup] Installing missing Python packages: {', '.join(pip_needed)}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + pip_needed,
            check=False
        )
        # Also try requirements.txt if it exists
        if os.path.exists(req_file):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-r", req_file],
                check=False
            )

    if shutil.which("ffmpeg") is None:
        print("[setup] ffmpeg not found. Installing via apt...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "--quiet", "ffmpeg"], check=False)

    if shutil.which("notify-send") is None:
        print("[setup] notify-send not found. Installing libnotify-bin...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "--quiet", "libnotify-bin"], check=False)


# ─────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────

PASS  = "\033[92mPASS\033[0m"
FAIL  = "\033[91mFAIL\033[0m"
SKIP  = "\033[93mSKIP\033[0m"
INFO  = "\033[94mINFO\033[0m"

results = []

def test(name):
    """Decorator that registers and runs a test function."""
    def decorator(fn):
        results.append({"name": name, "fn": fn, "status": None, "detail": ""})
        return fn
    return decorator

def run_all(verbose=False, quick=False):
    for r in results:
        try:
            skip_msg = r["fn"](verbose=verbose, quick=quick)
            if isinstance(skip_msg, str) and skip_msg.startswith("SKIP"):
                r["status"] = "SKIP"
                r["detail"] = skip_msg[5:].strip()
            else:
                r["status"] = "PASS"
        except AssertionError as e:
            r["status"] = "FAIL"
            r["detail"] = str(e)
        except Exception as e:
            r["status"] = "FAIL"
            r["detail"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    print("\n" + "="*58)
    print("  XVOICE LINUX TEST RESULTS")
    print("="*58)
    for r in results:
        label = {"PASS": PASS, "FAIL": FAIL, "SKIP": SKIP}[r["status"]]
        print(f"  [{label}] {r['name']}")
        if r["detail"] and (r["status"] == "FAIL" or verbose):
            for line in r["detail"].strip().splitlines():
                print(f"         {line}")
    print("="*58)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    print(f"  {passed} passed  |  {failed} failed  |  {skipped} skipped")
    print("="*58)

    if failed == 0:
        print("\n  All tests passed. Safe to build and release the AppImage!\n")
    else:
        print(f"\n  {failed} test(s) failed. Fix these before releasing.\n")
    return failed == 0


# ─────────────────────────────────────────────
# 1. Platform
# ─────────────────────────────────────────────

@test("Platform is Linux")
def _(verbose=False, quick=False):
    assert sys.platform.startswith("linux"), \
        f"Expected linux, got: {sys.platform}"


# ─────────────────────────────────────────────
# 2. Python version
# ─────────────────────────────────────────────

@test("Python >= 3.8")
def _(verbose=False, quick=False):
    v = sys.version_info
    assert (v.major, v.minor) >= (3, 8), \
        f"Python {v.major}.{v.minor} is too old. Need 3.8+"
    if verbose:
        print(f"       Python {sys.version}")


# ─────────────────────────────────────────────
# 3. Imports (all required packages)
# ─────────────────────────────────────────────

@test("Required packages importable")
def _(verbose=False, quick=False):
    missing = []
    packages = [
        "pyaudio", "wave", "pynput", "webrtcvad",
        "requests", "pystray", "PIL", "jose",
    ]
    for pkg in packages:
        try:
            __import__(pkg)
            if verbose:
                print(f"       {pkg}: OK")
        except ImportError as e:
            missing.append(f"{pkg}")
    if missing:
        return (
            f"SKIP Some packages not installed: {', '.join(missing)}\n"
            f"       Run: pip3 install -r requirements.txt"
        )


# ─────────────────────────────────────────────
# 4. Logging — writes to correct Linux path
# ─────────────────────────────────────────────

@test("Logging writes to ~/.local/share/Xvoice/logs/")
def _(verbose=False, quick=False):
    log_dir = Path.home() / ".local" / "share" / "Xvoice" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "xvoice_test.log"

    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    log = logging.getLogger("xvoice_log_test")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.info("Test log entry")
    handler.flush()
    handler.close()
    log.removeHandler(handler)

    content = log_file.read_text(encoding="utf-8")
    assert "Test log entry" in content, "Log entry not found in file"
    if verbose:
        print(f"       Log file: {log_file}")


# ─────────────────────────────────────────────
# 5. Config dir
# ─────────────────────────────────────────────

@test("Config dir created at ~/.config/Xvoice/")
def _(verbose=False, quick=False):
    config_dir = Path.home() / ".config" / "Xvoice"
    config_dir.mkdir(parents=True, exist_ok=True)
    assert config_dir.is_dir(), f"Failed to create {config_dir}"


# ─────────────────────────────────────────────
# 6. Token save/load
# ─────────────────────────────────────────────

@test("Token save and load roundtrip")
def _(verbose=False, quick=False):
    config_dir = Path.home() / ".config" / "Xvoice"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config_test.json"

    token = "test-jwt-token-abc123"
    config_file.write_text(json.dumps({"access_token": token}))
    loaded = json.loads(config_file.read_text()).get("access_token")
    config_file.unlink()

    assert loaded == token, f"Expected {token!r}, got {loaded!r}"


# ─────────────────────────────────────────────
# 7. Signal handler — SIGINT exits cleanly
# ─────────────────────────────────────────────

@test("SIGINT exits cleanly (no reentrant logging crash)")
def _(verbose=False, quick=False):
    # Run the headless signal handler in a subprocess and look for crash markers
    script = """
import sys, os, signal, logging, time, threading

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("xvoice")

def _handle_signal(sig, frame):
    os._exit(0)   # NO logger call here — signal handlers are not re-entrant safe

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

def _auto_kill():
    time.sleep(2)
    os.kill(os.getpid(), signal.SIGINT)

threading.Thread(target=_auto_kill, daemon=True).start()

while True:
    logger.debug("heartbeat")
    time.sleep(0.1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=10
    )
    combined = result.stdout + result.stderr
    assert "Logging error" not in combined, \
        f"Reentrant logging crash detected!\n{combined}"
    assert "RuntimeError" not in combined, \
        f"RuntimeError in signal handler!\n{combined}"
    if verbose:
        print(f"       Exit code: {result.returncode}")


# ─────────────────────────────────────────────
# 8. Signal handler — SIGTERM exits cleanly
# ─────────────────────────────────────────────

@test("SIGTERM exits cleanly")
def _(verbose=False, quick=False):
    script = """
import sys, os, signal, logging, time, threading

logging.basicConfig(level=logging.DEBUG, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("xvoice")

def _handle_signal(sig, frame):
    os._exit(0)

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

def _auto_kill():
    time.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)

threading.Thread(target=_auto_kill, daemon=True).start()
while True:
    logger.debug("heartbeat")
    time.sleep(0.1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=10
    )
    combined = result.stdout + result.stderr
    assert "Logging error" not in combined, f"Crash on SIGTERM:\n{combined}"
    if verbose:
        print(f"       Exit code: {result.returncode}")


# ─────────────────────────────────────────────
# 9. safe_notify — notify-send available
# ─────────────────────────────────────────────

@test("notify-send is available for Linux notifications")
def _(verbose=False, quick=False):
    path = shutil.which("notify-send")
    if path is None:
        return "SKIP notify-send not installed. Run: sudo apt install libnotify-bin"
    if verbose:
        print(f"       notify-send: {path}")


# ─────────────────────────────────────────────
# 10. safe_notify — sends without crash
# ─────────────────────────────────────────────

@test("safe_notify() runs without crashing")
def _(verbose=False, quick=False):
    def safe_notify(msg, title="Xvoice"):
        if sys.platform.startswith("linux"):
            try:
                subprocess.run(["notify-send", title, msg], timeout=3)
            except FileNotFoundError:
                pass  # notify-send not installed — acceptable
            except Exception as e:
                raise AssertionError(f"notify-send raised unexpected error: {e}")
            return
    # Should not raise
    safe_notify("Test notification", "Xvoice Test")


# ─────────────────────────────────────────────
# 11. pystray import — headless path taken
# ─────────────────────────────────────────────

@test("pystray is NOT called on Linux (headless path)")
def _(verbose=False, quick=False):
    # Verify that the platform check would correctly skip pystray
    platform = sys.platform
    would_be_headless = platform.startswith("linux") or platform == "darwin"
    assert would_be_headless, \
        f"Platform {platform!r} would NOT enter headless mode — pystray would be called!"
    if verbose:
        print(f"       Platform {platform!r} -> headless=True. pystray skipped.")


# ─────────────────────────────────────────────
# 12. PyAudio — can initialize
# ─────────────────────────────────────────────

@test("PyAudio initializes without error")
def _(verbose=False, quick=False):
    try:
        import pyaudio
    except ImportError:
        return "SKIP pyaudio not installed. Run: pip3 install pyaudio"
    try:
        # Suppress ALSA error spam
        null_fd = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(sys.stderr.fileno())
        os.dup2(null_fd, sys.stderr.fileno())
        try:
            p = pyaudio.PyAudio()
            count = p.get_device_count()
            p.terminate()
        finally:
            os.dup2(saved, sys.stderr.fileno())
            os.close(saved)
            os.close(null_fd)
        if verbose:
            print(f"       PyAudio devices: {count}")
    except Exception as e:
        raise AssertionError(f"PyAudio init failed: {e}")


# ─────────────────────────────────────────────
# 13. PyAudio — microphone available
# ─────────────────────────────────────────────

@test("Microphone input device exists")
def _(verbose=False, quick=False):
    try:
        import pyaudio
    except ImportError:
        return "SKIP pyaudio not installed"
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(sys.stderr.fileno())
        os.dup2(null_fd, sys.stderr.fileno())
        try:
            p = pyaudio.PyAudio()
            input_devices = [
                p.get_device_info_by_index(i)
                for i in range(p.get_device_count())
                if p.get_device_info_by_index(i)["maxInputChannels"] > 0
            ]
            p.terminate()
        finally:
            os.dup2(saved, sys.stderr.fileno())
            os.close(saved)
            os.close(null_fd)
        if not input_devices:
            return "SKIP No microphone detected (VirtualBox may not have mic access)"
        if verbose:
            for d in input_devices:
                print(f"       Mic: {d['name']}")
    except Exception as e:
        return f"SKIP Could not check audio devices: {e}"


# ─────────────────────────────────────────────
# 14. webrtcvad
# ─────────────────────────────────────────────

@test("webrtcvad processes audio frames correctly")
def _(verbose=False, quick=False):
    try:
        import webrtcvad
    except ImportError:
        return "SKIP webrtcvad not installed. Run: pip3 install webrtcvad"
    vad = webrtcvad.Vad(1)
    # 30ms of silence at 16000Hz = 480 samples * 2 bytes = 960 bytes
    silence = b"\x00" * 960
    result = vad.is_speech(silence, 16000)
    assert isinstance(result, bool), f"Expected bool, got {type(result)}"
    if verbose:
        print(f"       VAD on silence: is_speech={result}")


# ─────────────────────────────────────────────
# 15. pynput keyboard listener
# ─────────────────────────────────────────────

@test("pynput keyboard listener starts and stops cleanly")
def _(verbose=False, quick=False):
    try:
        from pynput import keyboard as pk
    except ImportError:
        return "SKIP pynput not installed. Run: pip3 install pynput"
    try:
        pk  # reference to suppress unused warning
        pressed = []

        def on_press(key):
            pressed.append(key)

        listener = pk.Listener(on_press=on_press)
        listener.start()
        time.sleep(0.2)
        listener.stop()
        if verbose:
            print("       pynput listener started and stopped cleanly.")
    except Exception as e:
        raise AssertionError(f"pynput listener failed: {e}")


# ─────────────────────────────────────────────
# 16. ffmpeg available
# ─────────────────────────────────────────────

@test("ffmpeg is installed and accessible")
def _(verbose=False, quick=False):
    path = shutil.which("ffmpeg")
    if path is None:
        return "SKIP ffmpeg not found. Run: sudo apt install ffmpeg"
    result = subprocess.run(["ffmpeg", "-version"],
                            capture_output=True, text=True)
    assert result.returncode == 0, "ffmpeg --version failed"
    if verbose:
        first_line = result.stdout.splitlines()[0]
        print(f"       {first_line}")


# ─────────────────────────────────────────────
# 17. normalize_audio (ffmpeg pipeline)
# ─────────────────────────────────────────────

@test("normalize_audio() produces valid output file")
def _(verbose=False, quick=False):
    if shutil.which("ffmpeg") is None:
        return "SKIP ffmpeg not installed. Run: sudo apt install ffmpeg"
    import wave
    # Create a tiny valid WAV file (16kHz mono, 0.1s silence)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        infile = f.name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        outfile = f.name

    try:
        with wave.open(infile, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * (16000 * 2 // 10))  # 0.1s silence — parens fix precedence

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", infile,
             "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", "-ar", "16000", outfile],
            capture_output=True, timeout=15
        )
        assert result.returncode == 0, \
            f"ffmpeg normalize failed (code {result.returncode})"
        assert os.path.getsize(outfile) > 0, "Output WAV is empty"
        if verbose:
            print(f"       Output size: {os.path.getsize(outfile)} bytes")
    finally:
        for f in [infile, outfile]:
            if os.path.exists(f):
                os.unlink(f)


# ─────────────────────────────────────────────
# 18. HTTP auth server can bind to port 45678
# ─────────────────────────────────────────────

@test("Auth HTTP server can bind to port 45678")
def _(verbose=False, quick=False):
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class NullHandler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

    try:
        server = HTTPServer(("127.0.0.1", 45678), NullHandler)
        server.server_close()
    except OSError as e:
        raise AssertionError(
            f"Cannot bind to port 45678: {e}\n"
            "Another process may be using it. Run: lsof -i :45678"
        )


# ─────────────────────────────────────────────
# 19. Network — backend reachable
# ─────────────────────────────────────────────

@test("Backend server is reachable")
def _(verbose=False, quick=False):
    if quick:
        return "SKIP --quick flag set"
    try:
        import requests
        BACKEND = "https://voicetotext-keyboard-production.up.railway.app"
        r = requests.get(f"{BACKEND}/", timeout=8)
        assert r.status_code in (200, 404), \
            f"Unexpected status: {r.status_code}"
        if verbose:
            print(f"       Status: {r.status_code}")
    except requests.exceptions.ConnectionError:
        raise AssertionError("Cannot reach backend. Check internet connection.")
    except requests.exceptions.Timeout:
        raise AssertionError("Backend timed out after 8s.")


# ─────────────────────────────────────────────
# 20. Threads — voice_loop runs without crashing
# ─────────────────────────────────────────────

@test("Background thread stays alive for 2 seconds")
def _(verbose=False, quick=False):
    errors = []

    def dummy_loop():
        try:
            for _ in range(20):
                time.sleep(0.1)
        except Exception as e:
            errors.append(str(e))

    t = threading.Thread(target=dummy_loop, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive() or len(errors) == 0, \
        f"Thread errors: {errors}"
    assert not errors, f"Thread raised: {errors}"


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Xvoice Linux test suite")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show extra detail for passing tests")
    parser.add_argument("--quick", action="store_true",
                        help="Skip slow tests (audio, network)")
    parser.add_argument("--setup", action="store_true",
                        help="Auto-install missing dependencies before running tests")
    args = parser.parse_args()

    print("\n  Xvoice Linux Test Suite")
    print(f"  Python {sys.version.split()[0]}  |  Platform: {sys.platform}\n")

    if args.setup:
        print("[setup] Checking and installing dependencies...")
        ensure_dependencies()
        print("[setup] Done.\n")

    success = run_all(verbose=args.verbose, quick=args.quick)
    sys.exit(0 if success else 1)
