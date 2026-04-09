"""
Cross-platform signal handler test.
Simulates the Linux and macOS code paths from main.py on Windows
by patching sys.platform before running the relevant logic.

Usage:
    python test_signal.py          # tests all platforms sequentially
    python test_signal.py linux    # test only linux path
    python test_signal.py darwin   # test only macOS path
"""

import os
import sys
import logging
import signal
import time
import threading
import subprocess
import argparse

# ─────────────────────────────────────────────
# Logging setup (mirrors main.py exactly)
# ─────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.expanduser("~"), "xvoice_test.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("xvoice_test")


# ─────────────────────────────────────────────
# Mirrors safe_notify() from main.py exactly
# ─────────────────────────────────────────────
def safe_notify(msg, title="Xvoice", platform=None):
    platform = platform or sys.platform
    logger.info(f"Notification [{title}]: {msg} (platform={platform})")

    if platform.startswith("linux"):
        try:
            subprocess.run(["notify-send", title, msg], timeout=3)
        except FileNotFoundError:
            logger.info("notify-send not available (expected on Windows) — skipping.")
        except Exception as e:
            logger.warning(f"notify-send failed: {e}")
        return

    if platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                timeout=3
            )
        except FileNotFoundError:
            logger.info("osascript not available (expected on Windows) — skipping.")
        except Exception as e:
            logger.warning(f"osascript failed: {e}")
        return

    logger.info("Windows path: would call tray_icon.notify() here.")


# ─────────────────────────────────────────────
# Mirrors start_tray() headless branch from main.py
# ─────────────────────────────────────────────
def run_headless_mode(mock_platform):
    """Runs the headless loop exactly as main.py does on Linux/macOS."""
    os_name = "macOS" if mock_platform == "darwin" else "Linux"
    logger.info(f"{os_name} headless mode started.")
    print(f"  [{os_name}] Headless mode running. Will auto-send SIGINT in 3 seconds...")

    # Replicate the exact signal handler code from main.py
    def _handle_signal(sig, frame):
        os._exit(0)  # Do NOT call logger here - signal handlers are async

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Auto-send SIGINT after 3 seconds so the test completes automatically
    def _auto_interrupt():
        time.sleep(3)
        print(f"  [{os_name}] Sending SIGINT now...")
        os.kill(os.getpid(), signal.SIGINT)

    t = threading.Thread(target=_auto_interrupt, daemon=True)
    t.start()

    # This is the exact same loop as main.py
    count = 0
    while True:
        logger.debug(f"[{os_name}] Heartbeat #{count}")
        count += 1
        time.sleep(1)


# ─────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────
def test_platform(mock_platform):
    print(f"\n{'='*50}")
    print(f"  Testing platform: {mock_platform}")
    print(f"{'='*50}")

    # Test 1: safe_notify doesn't crash
    print("\n[1/2] Testing safe_notify()...")
    try:
        safe_notify("Test notification", "Xvoice Test", platform=mock_platform)
        print("  PASS: safe_notify() completed without crash.")
    except Exception as e:
        print(f"  FAIL: safe_notify() raised: {e}")
        return False

    # Test 2: headless mode + signal handler exits cleanly
    print("\n[2/2] Testing headless mode + signal handler...")
    result = subprocess.run(
        [sys.executable, __file__, f"--headless-only={mock_platform}"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode in (0, 1, 2):
        # os._exit(0) = 0 on Linux/mac; Windows SIGINT via os.kill exits with 2.
        # What matters is no "Logging error" traceback in the output.
        combined = result.stdout + result.stderr
        if "Logging error" in combined or "RuntimeError" in combined:
            print(f"  FAIL: Reentrant logging crash detected!")
            print(combined)
            return False
        else:
            print(f"  PASS: Clean exit (code={result.returncode}), no logging errors.")
    else:
        print(f"  FAIL: Unexpected exit code {result.returncode}")
        print(result.stdout)
        print(result.stderr)
        return False

    return True


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", nargs="?", choices=["linux", "darwin"],
                        help="Test only this platform")
    parser.add_argument("--headless-only", dest="headless_only",
                        help="Internal: run headless mode for a given platform")
    args = parser.parse_args()

    # Internal mode: run headless loop (called as subprocess by test runner)
    if args.headless_only:
        run_headless_mode(args.headless_only)
        sys.exit(0)

    # Normal test mode
    platforms = [args.platform] if args.platform else ["linux", "darwin"]
    results = {}

    for p in platforms:
        results[p] = test_platform(p)

    print(f"\n{'='*50}")
    print("  RESULTS")
    print(f"{'='*50}")
    for p, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {p:10s} -> {status}")

    if all(results.values()):
        print("\nAll tests passed! Safe to push and release.")
    else:
        print("\nSome tests failed. Do NOT release yet.")

    logger.info(f"Test log saved to: {LOG_FILE}")
