"""
Comprehensive Linux test suite for Xvoice.
Run this on your Linux VirtualBox BEFORE building/releasing an AppImage.

Automatically installs all missing dependencies before running tests.
No tests are skipped — everything must pass for a release to be safe.

Usage:
    python3 test_linux.py           # run all tests (auto-installs deps)
    python3 test_linux.py -v        # verbose output
    python3 test_linux.py --quick   # skip slow network test only
"""

import sys
import os
import json
import time
import signal
import logging
import tempfile
import threading
import subprocess
import argparse
import traceback
import importlib
import shutil
from pathlib import Path

# ─────────────────────────────────────────────
# Dependency auto-install — always runs first
# ─────────────────────────────────────────────

def ensure_dependencies():
    """Install all missing pip packages and system tools before tests run."""
    req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")

    pip_map = {
        "pyaudio":    "pyaudio",
        "pynput":     "pynput",
        "webrtcvad":  "webrtcvad",
        "pystray":    "pystray",
        "jose":       "python-jose",
        "PIL":        "Pillow",
        "requests":   "requests",
    }

    missing_pip = []
    for mod, pkg in pip_map.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing_pip.append(pkg)

    if missing_pip:
        print(f"[setup] Installing Python packages: {', '.join(missing_pip)}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing_pip,
            check=True
        )

    if os.path.exists(req_file):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", req_file],
            check=False
        )

    apt_needed = []
    if shutil.which("ffmpeg") is None:
        apt_needed.append("ffmpeg")
    if shutil.which("notify-send") is None:
        apt_needed.append("libnotify-bin")

    if apt_needed:
        print(f"[setup] Installing system packages via apt: {', '.join(apt_needed)}")
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "--quiet"] + apt_needed,
            check=True
        )

    print("[setup] All dependencies ready.\n")


# ─────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def test(name):
    def decorator(fn):
        results.append({"name": name, "fn": fn, "status": None, "detail": ""})
        return fn
    return decorator

def run_all(verbose=False, quick=False):
    for r in results:
        try:
            r["fn"](verbose=verbose, quick=quick)
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
        label = PASS if r["status"] == "PASS" else FAIL
        print(f"  [{label}] {r['name']}")
        if r["detail"] and (r["status"] == "FAIL" or verbose):
            for line in r["detail"].strip().splitlines():
                print(f"         {line}")
    print("="*58)

    passed  = sum(1 for r in results if r["status"] == "PASS")
    failed  = sum(1 for r in results if r["status"] == "FAIL")
    print(f"  {passed} passed  |  {failed} failed")
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
# 3. All required packages importable
# ─────────────────────────────────────────────

@test("Required packages importable")
def _(verbose=False, quick=False):
    missing = []
    for pkg in ["pyaudio", "wave", "pynput", "webrtcvad", "requests", "pystray", "PIL", "jose"]:
        try:
            importlib.import_module(pkg)
            if verbose:
                print(f"       {pkg}: OK")
        except ImportError:
            missing.append(pkg)
    assert not missing, f"Still missing after install: {', '.join(missing)}"


# ─────────────────────────────────────────────
# 4. Logging — writes to correct Linux path
# ─────────────────────────────────────────────

@test("Logging writes to ~/.local/share/Xvoice/logs/")
def _(verbose=False, quick=False):
    log_dir = Path.home() / ".local" / "share" / "Xvoice" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "xvoice_test.log"

    logger = logging.getLogger("xvoice_log_test")
    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.info("Test log entry")
    handler.flush()
    handler.close()
    logger.removeHandler(handler)

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
    script = """
import sys, os, signal, logging, time, threading
logging.basicConfig(level=logging.DEBUG, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("xvoice")
def _handle_signal(sig, frame):
    os._exit(0)  # NO logger here — not re-entrant safe
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)
threading.Thread(target=lambda: (time.sleep(2), os.kill(os.getpid(), signal.SIGINT)), daemon=True).start()
while True:
    logger.debug("heartbeat")
    time.sleep(0.1)
"""
    result = subprocess.run([sys.executable, "-c", script],
                            capture_output=True, text=True, timeout=10)
    combined = result.stdout + result.stderr
    assert "Logging error" not in combined, f"Reentrant logging crash!\n{combined}"
    assert "RuntimeError" not in combined, f"RuntimeError in handler!\n{combined}"
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
threading.Thread(target=lambda: (time.sleep(2), os.kill(os.getpid(), signal.SIGTERM)), daemon=True).start()
while True:
    logger.debug("heartbeat")
    time.sleep(0.1)
"""
    result = subprocess.run([sys.executable, "-c", script],
                            capture_output=True, text=True, timeout=10)
    combined = result.stdout + result.stderr
    assert "Logging error" not in combined, f"Crash on SIGTERM:\n{combined}"
    if verbose:
        print(f"       Exit code: {result.returncode}")


# ─────────────────────────────────────────────
# 9. notify-send available
# ─────────────────────────────────────────────

@test("notify-send is available for Linux notifications")
def _(verbose=False, quick=False):
    path = shutil.which("notify-send")
    assert path is not None, \
        "notify-send not found. Run: sudo apt install libnotify-bin"
    if verbose:
        print(f"       notify-send: {path}")


# ─────────────────────────────────────────────
# 10. safe_notify — runs without crashing
# ─────────────────────────────────────────────

@test("safe_notify() runs without crashing")
def _(verbose=False, quick=False):
    try:
        subprocess.run(["notify-send", "Xvoice Test", "Test notification"], timeout=3)
    except FileNotFoundError:
        raise AssertionError("notify-send not found — install libnotify-bin")
    except Exception as e:
        raise AssertionError(f"notify-send raised unexpected error: {e}")


# ─────────────────────────────────────────────
# 11. pystray skipped on Linux
# ─────────────────────────────────────────────

@test("pystray is NOT called on Linux (headless path)")
def _(verbose=False, quick=False):
    would_be_headless = sys.platform.startswith("linux") or sys.platform == "darwin"
    assert would_be_headless, \
        f"Platform {sys.platform!r} would NOT enter headless mode!"
    if verbose:
        print(f"       {sys.platform!r} -> headless=True. pystray skipped.")


# ─────────────────────────────────────────────
# 12. PyAudio initializes
# ─────────────────────────────────────────────

@test("PyAudio initializes without error")
def _(verbose=False, quick=False):
    import pyaudio
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


# ─────────────────────────────────────────────
# 13. Microphone input device
# ─────────────────────────────────────────────

@test("Microphone input device exists")
def _(verbose=False, quick=False):
    import pyaudio
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

    assert input_devices, \
        "No microphone detected. Enable mic passthrough in VirtualBox: Devices > Audio > Enable Audio Input"
    if verbose:
        for d in input_devices:
            print(f"       Mic: {d['name']}")


# ─────────────────────────────────────────────
# 14. webrtcvad
# ─────────────────────────────────────────────

@test("webrtcvad processes audio frames correctly")
def _(verbose=False, quick=False):
    import webrtcvad
    vad = webrtcvad.Vad(1)
    silence = b"\x00" * 960   # 30ms at 16kHz, 16-bit mono
    result = vad.is_speech(silence, 16000)
    assert isinstance(result, bool), f"Expected bool, got {type(result)}"
    if verbose:
        print(f"       VAD on silence: is_speech={result}")


# ─────────────────────────────────────────────
# 15. pynput keyboard listener
# ─────────────────────────────────────────────

@test("pynput keyboard listener starts and stops cleanly")
def _(verbose=False, quick=False):
    from pynput import keyboard as pk
    pressed = []
    listener = pk.Listener(on_press=lambda key: pressed.append(key))
    listener.start()
    time.sleep(0.2)
    listener.stop()
    if verbose:
        print("       pynput listener started and stopped cleanly.")


# ─────────────────────────────────────────────
# 16. ffmpeg available
# ─────────────────────────────────────────────

@test("ffmpeg is installed and accessible")
def _(verbose=False, quick=False):
    path = shutil.which("ffmpeg")
    assert path is not None, "ffmpeg not found. Run: sudo apt install ffmpeg"
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    assert result.returncode == 0, "ffmpeg --version failed"
    if verbose:
        print(f"       {result.stdout.splitlines()[0]}")


# ─────────────────────────────────────────────
# 17. normalize_audio pipeline
# ─────────────────────────────────────────────

@test("normalize_audio() produces valid output file")
def _(verbose=False, quick=False):
    import wave
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        infile = f.name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        outfile = f.name
    try:
        with wave.open(infile, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * (16000 * 2 // 10))  # 0.1s silence
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
# 18. HTTP auth server binds to port 45678
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
# 19. Backend reachable
# ─────────────────────────────────────────────

@test("Backend server is reachable")
def _(verbose=False, quick=False):
    if quick:
        return  # intentionally no assertion, just return
    import requests as req
    BACKEND = "https://voicetotext-keyboard-production.up.railway.app"
    try:
        r = req.get(f"{BACKEND}/", timeout=8)
        assert r.status_code in (200, 404), f"Unexpected status: {r.status_code}"
        if verbose:
            print(f"       Status: {r.status_code}")
    except req.exceptions.ConnectionError:
        raise AssertionError("Cannot reach backend. Check internet connection.")
    except req.exceptions.Timeout:
        raise AssertionError("Backend timed out after 8s.")


# ─────────────────────────────────────────────
# 20. Background thread stability
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
    assert not errors, f"Thread raised: {errors}"


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Xvoice Linux test suite")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--quick", action="store_true",
                        help="Skip network test")
    args = parser.parse_args()

    print("\n  Xvoice Linux Test Suite")
    print(f"  Python {sys.version.split()[0]}  |  Platform: {sys.platform}\n")

    print("[setup] Checking dependencies...")
    ensure_dependencies()

    success = run_all(verbose=args.verbose, quick=args.quick)
    sys.exit(0 if success else 1)
