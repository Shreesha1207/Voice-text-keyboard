#!/usr/bin/env python3
"""
Xvoice macOS preflight — find out exactly why the app is not working.

Run it in whichever situation matches your problem:

    # A) The installed app will not start
    /Applications/Xvoice.app/Contents/MacOS/xvoice --help   # see note below
    python3 mac/preflight.py

    # B) Checking a build machine / source checkout before building
    python3 mac/preflight.py

It never imports main.py, so it cannot be broken by the very bug it is
looking for. Every check is independent and prints PASS / WARN / FAIL with
the exact command to fix it.

Note on (A): the frozen app has no --help; to see why it dies, run the
executable inside the bundle directly and read stderr:

    /Applications/Xvoice.app/Contents/MacOS/xvoice

That is the single most useful thing you can do — a GUI double-click hides
the traceback, running it from Terminal shows it.
"""

import os
import platform
import plistlib
import shutil
import subprocess
import sys

APP_PATHS = [
    "/Applications/Xvoice.app",
    os.path.expanduser("~/Applications/Xvoice.app"),
    "dist/Xvoice.app",
]

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
)
if not sys.stdout.isatty():
    GREEN = YELLOW = RED = DIM = RESET = ""

results = []


def check(name, status, detail="", fix=""):
    """status: 'PASS' | 'WARN' | 'FAIL'"""
    colour = {"PASS": GREEN, "WARN": YELLOW, "FAIL": RED}[status]
    print(f"  [{colour}{status:4}{RESET}] {name}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")
    if fix:
        print(f"         {YELLOW}fix:{RESET} {fix}")
    results.append((name, status))


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def find_app():
    for p in APP_PATHS:
        if os.path.isdir(p):
            return p
    return None


# ═════════════════════════════════════════════════════════════════════════
def check_platform():
    section("1. Platform")

    if sys.platform != "darwin":
        check("Running on macOS", "FAIL",
              f"sys.platform is {sys.platform!r}, not 'darwin'.",
              "This script only means anything on a Mac.")
        return False

    mac_ver = platform.mac_ver()[0]
    major = int(mac_ver.split(".")[0]) if mac_ver else 0
    check("macOS version", "PASS" if major >= 11 else "FAIL",
          f"macOS {mac_ver}",
          "" if major >= 11 else "Xvoice requires macOS 11 (Big Sur) or newer.")

    arch = platform.machine()
    check("CPU architecture", "PASS", f"{arch} "
          f"({'Apple Silicon' if arch == 'arm64' else 'Intel'})")
    return True


def check_app_bundle():
    section("2. The installed app")
    app = find_app()
    if not app:
        check("Xvoice.app found", "WARN",
              "Not in /Applications, ~/Applications or ./dist.",
              "Install it, or run this from the repo after ./build_mac.sh")
        return None
    check("Xvoice.app found", "PASS", app)

    # ── Executable present and runnable ──
    exe = os.path.join(app, "Contents", "MacOS", "xvoice")
    if not os.path.isfile(exe):
        check("Bundle executable", "FAIL", f"Missing {exe}",
              "The build is corrupt — rebuild with ./build_mac.sh")
        return app
    check("Bundle executable", "PASS", exe)

    # ── Architecture match: the #1 cause of "downloaded but won't open" ──
    rc, out = run(["lipo", "-archs", exe])
    if rc == 0:
        archs = out.split()
        here = platform.machine()
        if here in archs or "universal" in out:
            check("Architecture matches this Mac", "PASS",
                  f"binary: {' '.join(archs)} / machine: {here}")
        else:
            check("Architecture matches this Mac", "FAIL",
                  f"binary is {' '.join(archs)} but this Mac is {here}.",
                  f"Download the {here} build — an arch mismatch means the "
                  f"app silently refuses to launch.")

    # ── Quarantine: the #2 cause ──
    rc, out = run(["xattr", "-l", app])
    if "com.apple.quarantine" in out:
        check("Gatekeeper quarantine", "FAIL",
              "com.apple.quarantine is set — macOS will refuse to open this "
              "app, usually with 'Xvoice is damaged and can't be opened'.",
              f"xattr -dr com.apple.quarantine {app}")
    else:
        check("Gatekeeper quarantine", "PASS", "not quarantined")

    # ── Code signature ──
    rc, out = run(["codesign", "--verify", "--deep", "--strict", app])
    if rc == 0:
        check("Code signature", "PASS", "valid")
    else:
        check("Code signature", "WARN",
              out.strip()[:200] or "signature invalid",
              "Ad-hoc/unsigned builds still run once quarantine is cleared. "
              "For a warning-free install you need a Developer ID + notarization.")

    # ── Info.plist sanity ──
    plist_path = os.path.join(app, "Contents", "Info.plist")
    try:
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        missing = [k for k in ("CFBundleIdentifier", "NSMicrophoneUsageDescription")
                   if k not in info]
        if missing:
            check("Info.plist keys", "FAIL", f"missing: {', '.join(missing)}",
                  "Without NSMicrophoneUsageDescription macOS kills the app "
                  "the instant it opens the microphone.")
        else:
            check("Info.plist keys", "PASS",
                  f"id={info.get('CFBundleIdentifier')} "
                  f"v{info.get('CFBundleShortVersionString')} "
                  f"LSUIElement={info.get('LSUIElement')}")
        if info.get("LSUIElement"):
            check("Menu-bar-only mode", "PASS",
                  "LSUIElement=true — Xvoice deliberately has NO Dock icon. "
                  "Look for the purple microphone in the menu bar (top-right).")
    except Exception as e:
        check("Info.plist readable", "FAIL", str(e))

    # ── ffmpeg ──
    ff = os.path.join(app, "Contents", "MacOS", "ffmpeg.exe")
    if os.path.isfile(ff):
        check("Bundled ffmpeg", "PASS", "audio will be volume-normalized")
    else:
        check("Bundled ffmpeg", "WARN",
              "not bundled — main.py falls back to the raw recording.",
              "Optional. Drop a static binary at mac/ffmpeg and rebuild.")
    return app


def check_python_deps():
    section("3. Python dependencies (source runs / build machines)")
    mods = {
        "pyaudio": "microphone capture",
        "pynput": "F8 hotkey + typing",
        "pystray": "menu-bar icon",
        "PIL": "icon drawing",
        "requests": "backend calls",
        "pyperclip": "clipboard paste (Writing engine imports this at module scope)",
        "PySide6": "listening glow + Writing overlays",
        "objc": "pyobjc bridge — pynput/pystray need it on macOS",
        "Quartz": "pyobjc: key event tap",
        "AppKit": "pyobjc: menu-bar item",
    }
    for mod, why in mods.items():
        try:
            __import__(mod)
            check(f"import {mod}", "PASS", why)
        except Exception as e:
            check(f"import {mod}", "FAIL", f"{why} — {type(e).__name__}: {e}",
                  f"python3 -m pip install -r requirements.txt")


def check_binaries():
    section("4. System tools")
    for tool, why, needed in [
        ("ffmpeg", "audio normalization (optional)", False),
        ("codesign", "signing the bundle", True),
        ("hdiutil", "building the DMG", True),
        ("pkgbuild", "building the PKG", True),
        ("iconutil", "generating the app icon", True),
        ("create-dmg", "prettier DMG layout (optional)", False),
    ]:
        path = shutil.which(tool)
        if path:
            check(tool, "PASS", f"{why} — {path}")
        else:
            check(tool, "FAIL" if needed else "WARN", f"{why} — not found",
                  "xcode-select --install" if needed else f"brew install {tool}")


def check_permissions():
    section("5. Privacy permissions (TCC)")
    print(f"  {DIM}macOS gives no API to read these reliably, so this is a "
          f"guided check.{RESET}\n")

    # Microphone can be probed for real.
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        try:
            s = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                       input=True, frames_per_buffer=1024)
            s.close()
            check("Microphone", "PASS", "an input stream opened successfully")
        except Exception as e:
            check("Microphone", "FAIL", str(e)[:150],
                  "System Settings -> Privacy & Security -> Microphone")
        finally:
            p.terminate()
    except Exception as e:
        check("Microphone", "WARN", f"could not test: {e}")

    check("Accessibility", "WARN",
          "Required to TYPE the transcribed text into other apps.",
          "System Settings -> Privacy & Security -> Accessibility "
          "-> enable Xvoice")
    check("Input Monitoring", "WARN",
          "Required to notice F8 while another app is in front.",
          "System Settings -> Privacy & Security -> Input Monitoring "
          "-> enable Xvoice")
    print(f"\n  {YELLOW}Both only list Xvoice AFTER its first launch, and "
          f"macOS only\n  applies a newly granted permission to a freshly "
          f"STARTED process —\n  always quit and reopen Xvoice after toggling "
          f"one.{RESET}")


def check_threading_model():
    section("6. Qt / menu-bar main-thread conflict")
    print(f"  {DIM}This is the check that source-level review flagged as the "
          f"most likely\n  reason a built app starts and then does nothing "
          f"visible.{RESET}\n")

    repo_qt_host = os.path.join("writing", "ui", "qt_host.py")
    if not os.path.isfile(repo_qt_host):
        check("qt_host.py present", "WARN",
              "Run this from the repo root to include this check.")
        return

    src = open(repo_qt_host).read()
    runs_on_thread = "threading.Thread" in src and "QApplication" in src
    if runs_on_thread:
        check("QApplication thread", "FAIL",
              "writing/ui/qt_host.py builds QApplication inside a daemon "
              "thread. On macOS, Cocoa requires NSApplication and every "
              "AppKit/Qt object to live on the MAIN thread — this is an OS "
              "rule, not a Qt preference.",
              "See MACOS_NOTES.md — needs a main-thread swap in main.py "
              "(pystray to a thread, Qt on main).")
    else:
        check("QApplication thread", "PASS", "not obviously off-main-thread")

    # Empirically demonstrate it, safely, in a subprocess.
    probe = r'''
import threading, sys
ok = {}
def work():
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from PySide6.QtWidgets import QWidget
        w = QWidget()          # this is what actually touches Cocoa
        ok["r"] = "created QApplication+QWidget off-main-thread"
    except BaseException as e:
        ok["r"] = f"{type(e).__name__}: {e}"
t = threading.Thread(target=work); t.start(); t.join(20)
print(ok.get("r", "thread hung or crashed the process"))
'''
    rc, out = run([sys.executable, "-c", probe])
    out = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    if rc != 0:
        check("Live off-main-thread Qt probe", "FAIL",
              f"the probe process died (rc={rc}): {out}",
              "Confirms Qt cannot run off the main thread here.")
    else:
        check("Live off-main-thread Qt probe", "WARN", out,
              "Compare against the same probe on the main thread.")


def main():
    print("=" * 62)
    print("  Xvoice macOS preflight")
    print("=" * 62)

    if not check_platform():
        return 1
    check_app_bundle()
    check_python_deps()
    check_binaries()
    check_permissions()
    check_threading_model()

    section("Summary")
    fails = [n for n, s in results if s == "FAIL"]
    warns = [n for n, s in results if s == "WARN"]
    print(f"  {GREEN}{len([1 for _, s in results if s == 'PASS'])} passed"
          f"{RESET}, {YELLOW}{len(warns)} warnings{RESET}, "
          f"{RED}{len(fails)} failures{RESET}")
    if fails:
        print(f"\n  {RED}Blocking issues:{RESET}")
        for f in fails:
            print(f"    - {f}")
    print(f"\n  {DIM}Logs, once the app has run at least once:"
          f"\n    ~/Library/Logs/Xvoice/{RESET}")
    print(f"  {DIM}To see a launch crash, run the binary from Terminal:"
          f"\n    /Applications/Xvoice.app/Contents/MacOS/xvoice{RESET}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
