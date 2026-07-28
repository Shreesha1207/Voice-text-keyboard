# macOS — what works, what needs a decision

Notes from packaging Xvoice for macOS. Read this before debugging a Mac
build; most "it downloaded but wouldn't start" reports are one of the five
things below.

---

## 1. There is no Dock icon, and that is intentional

`xvoice.spec` sets `LSUIElement: True`. That is the macOS equivalent of a
Windows tray-only app: **no Dock icon, no window, no app switcher entry.**

Xvoice appears as a purple microphone in the **menu bar** — the strip at the
very top-right of the screen, next to the clock, Wi-Fi and battery. That is
the same role the Windows system tray plays.

> If you double-clicked Xvoice and "nothing happened", check the menu bar
> before assuming it crashed. It very likely started.

Two things make this worse than on Windows:

- macOS **hides menu-bar icons when the bar runs out of room**, especially on
  laptops with a notch. If the bar is crowded, the icon is silently dropped.
  Bartender/Ice or removing another icon will reveal it.
- There is no "show hidden icons" chevron like the one in the Windows tray.

To confirm it is running regardless of the menu bar:

```bash
pgrep -fl Xvoice
tail -f ~/Library/Logs/Xvoice/*.log
```

---

## 2. Inno Setup is not an antivirus workaround — and neither is any Mac format

Worth correcting, because it changes what you need to buy:

Inno Setup is an **installer builder**. It does not stop Windows flagging
your binary. What stops SmartScreen showing "unknown publisher" is an
**Authenticode code-signing certificate** plus accumulated download
reputation. An unsigned Inno installer gets warned about exactly like an
unsigned bare `.exe`.

macOS is the same story with different names. No packaging format makes
Gatekeeper trust you. What does:

| | Windows | macOS |
|---|---|---|
| Stops the scary dialog | Authenticode cert | **Developer ID cert + notarization** |
| Cost | ~$100–400/yr (CA) | **$99/yr** (Apple Developer Program) |
| Wrapper format | `.exe` installer | `.pkg` or `.dmg` |

Notarization is the step people miss: you upload the signed build to Apple,
their automated scanner checks it, and you **staple** the resulting ticket to
the file. Without it, macOS 10.15+ blocks the app on first launch even if it
*is* signed.

Both `installer_mac.sh` and `installer_mac_pkg.sh` do this automatically when
you set the environment variables — see §6.

### `.dmg` vs `.pkg`

Since you asked specifically for the Inno Setup analogue: **that is `.pkg`,
not `.dmg`.**

| | `.dmg` | `.pkg` |
|---|---|---|
| What it is | A disk image — a folder in a wrapper | A real installer with a wizard |
| User action | Drag the app to Applications | Click Continue → Install |
| Can run install logic | **No** | **Yes** (pre/postinstall scripts) |
| Closest Windows equivalent | A zip | An Inno Setup `Setup.exe` |

The repo now builds both:

- **`./installer_mac.sh`** → `Xvoice-1.1.0-<arch>.dmg`
  Drag-to-Applications, plus a `First Run.command` the user must double-click
  to clear quarantine and set up login-start.
- **`./installer_mac_pkg.sh`** → `Xvoice-1.1.0-<arch>.pkg`
  A proper wizard. `mac/pkg-scripts/postinstall` runs as root afterwards and
  does the quarantine clear, the LaunchAgent install and the first launch
  **automatically** — no manual step at all.

**Recommendation: ship the `.pkg`.** It removes the single most confusing
part of the DMG flow. Keep the DMG as the "advanced users" download.

---

## 3. Architecture must match — there is no fat binary here

PyInstaller freezes for the architecture it runs on, and there are no
`universal2` wheels for PyAudio or PySide6. So an Apple Silicon build **will
not run on Intel** and vice versa; the app just refuses to open, with no
useful error.

CI therefore builds two of everything:

| Runner | Arch | Artifact |
|---|---|---|
| `macos-14` | `arm64` | Apple Silicon (M1–M4) |
| `macos-13` | `x86_64` | Intel |

The `.pkg` pins this with `hostArchitectures` in its distribution XML, so
installing the wrong one fails loudly instead of installing a broken app.
The `.dmg` has no such guard — another point for the `.pkg`.

---

## 4. Three permissions, and a restart requirement people miss

| Permission | Without it | Prompted? |
|---|---|---|
| **Microphone** | Cannot record | Yes, automatically |
| **Accessibility** | Records fine, types nothing | **No — manual** |
| **Input Monitoring** | F8 ignored unless Xvoice is focused | **No — manual** |

Two traps:

1. Xvoice only appears in the Accessibility / Input Monitoring lists **after
   it has been launched once.** A user who looks first sees an empty list.
2. macOS only hands a newly granted permission to a **freshly started
   process.** After toggling either switch you must quit and reopen Xvoice
   (menu bar → *Refresh / Restart*). Not doing this is why "I granted it and
   it still doesn't work" happens.

---

## 5. ⚠️ Open issue: Qt and the menu bar both want the main thread

**This is the one thing packaging cannot fix, and it needs your decision.**

`main.py`'s entrypoint does:

```python
QtHost.instance().start()   # -> QApplication built inside a daemon thread
...
start_tray()                # -> pystray owns the main thread, blocks here
```

`writing/ui/qt_host.py` even documents the intent:

> *"The application's main thread is held by the system-tray loop, so this
> host runs the QApplication inside one dedicated daemon thread."*

That is fine on Windows. **On macOS it is illegal.** Cocoa requires
`NSApplication` and every AppKit object to live on the main thread — an OS
rule enforced by AppKit, not a Qt style preference. And `pystray`'s Darwin
backend is itself built on `NSStatusBar` + an `NSApplication` run loop, so it
*also* requires the main thread.

Two libraries, one main thread. On macOS they cannot both have it.

**Likely symptom:** Qt fails when it first touches Cocoa. Best case the
listening glow and Writing overlays never appear and the log shows
`QtHost thread crashed`; worst case Cocoa raises an uncatchable
`NSInternalInconsistencyException` and the whole process dies at launch —
which looks exactly like "it downloaded but didn't start".

I have **not** confirmed which of the two happens, because it cannot be
reproduced off Apple hardware (see §7). Run this on the Mac to find out:

```bash
python3 mac/preflight.py          # section 6 probes it directly
```

### The fix, when you want it

There is no trivial swap — moving Qt to the main thread just breaks pystray
instead. The correct structure on macOS is **one event loop, Qt's**:

- Run `QApplication` on the **main thread**.
- Replace the menu-bar item with Qt's own `QSystemTrayIcon` **on darwin only**,
  so Qt owns the single Cocoa run loop.
- Keep `pystray` unchanged on Windows and Linux.

That is a change to `main.py` and `start_tray()` — i.e. the app code you
asked me not to touch, so **I have left it alone.** Say the word and I will
implement it behind a `sys.platform == "darwin"` branch so Windows behaviour
is untouched.

---

## 6. Signing and notarization

```bash
# One-time: store an app-specific password in the keychain
xcrun notarytool store-credentials xvoice-notary \
  --apple-id you@example.com --team-id TEAMID --password <app-specific-password>

# Every build
export XVOICE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export XVOICE_INSTALLER_IDENTITY="Developer ID Installer: Your Name (TEAMID)"
export XVOICE_NOTARY_PROFILE="xvoice-notary"

./build_mac.sh
./installer_mac_pkg.sh     # or ./installer_mac.sh for the DMG
```

Note the **two different certificates**: `.app` bundles are signed with a
*Developer ID Application* cert, `.pkg` files with a *Developer ID
Installer* cert. Using the wrong one is a common and confusing failure.

Without these the build is ad-hoc signed. It still works, but every user has
to clear quarantine manually once.

---

## 7. What I could and could not verify

Being explicit, since a wrong claim here costs you a release cycle.

**Verified by execution:**

- `build_mac.sh` and `installer_mac.sh` run start-to-finish against a stubbed
  macOS toolchain (`uname`/`brew`/`codesign`/`ditto`/`hdiutil`/`create-dmg`),
  including the `create-dmg` → `hdiutil` fallback and the DMG filename CI
  globs for.
- `xvoice.spec` executed under all three platform branches with stubbed
  PyInstaller classes. darwin builds Analysis→PYZ→EXE→COLLECT→BUNDLE;
  **win32/linux graphs are byte-identical to before my changes.**
- Every plist parses via `plistlib`; `make_icns.py` produced a real `.icns`.
- `mac/preflight.py` exercised section by section without crashing.

**Verified by source inspection, not execution:**

- The main-thread conflict in §5.
- `pyperclip` was imported at module scope by `writing/clipboard.py:9` but
  missing from `requirements.txt` — nothing pulls it in transitively, so a
  clean build killed the Writing engine on import. **Now fixed.**
- The pyobjc hidden imports `pynput`/`pystray` need on darwin. **Now added.**

**Not verified, and cannot be from here:**

- An actual end-to-end build, launch, or F8 round-trip.

> macOS is **not** a Linux wrapper — that premise does not hold. It is
> Darwin: a Mach/BSD kernel, Mach-O binaries instead of ELF, and a GUI stack
> (Cocoa, Quartz, TCC) with no Linux counterpart. PyInstaller cannot
> cross-compile; it only freezes for the OS it is running on. Nothing in this
> Linux container can produce or execute an `.app`.
>
> The honest path is: run `mac/preflight.py` on a Mac, or push a `v*` tag and
> let the `macos-14` / `macos-13` CI runners build it. Those are real Macs.
