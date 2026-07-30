# 🎙️ Xvoice

Two tools in one lightweight background app:

- **Dictation** — hold a hotkey, speak, release. Your words are transcribed and typed into whatever app is focused.
- **Writing** — select any text on screen, click the floating **Xvoice** button, and rewrite, translate, shorten, summarise, or fix its grammar in place.

Dictation works in **English across accents** (Indian, American, Australian, British, French, German, Dutch, and more). Writing works on any text you can select.

> **Hosted service.** You sign in through your browser — you do **not** need your own OpenAI API key. Transcription and rewriting run on the Xvoice backend. Manage your account at **[xvoicekeyboard.com](https://xvoicekeyboard.com)**.

---

## ✨ Features

- 🔴 **Push-to-talk dictation** — hold `F8` (customisable on Pro) to record, release to transcribe
- ✍️ **In-place AI writing** — improve, translate, shorten, summarise, fix grammar, and more, on any selected text
- 🌍 **Accent-aware** — works with English accents out of the box
- 🔇 **Smart noise filtering** — WebRTC VAD removes silence and background noise before audio leaves your machine
- 🔊 **Audio normalization** — FFmpeg normalises volume for clearer transcription
- 🔔 **Audio feedback** — beeps signal recording start, stop, and success
- 🚀 **Runs on startup** — launches invisibly in the background at login
- 🔐 **Browser sign-in** — no API keys to manage; log out from all devices with one click

---

## 📦 Install (recommended)

Prebuilt downloads are on **[xvoicekeyboard.com/download](https://xvoicekeyboard.com/download)** and the [Releases page](../../releases) — no Python required.

| Platform | Download | How to install |
|---|---|---|
| 🪟 Windows | `XVoiceSetup.exe` | Double-click to run. If SmartScreen warns, **More info → Run anyway**. |
| 🍎 macOS (Apple Silicon) | **`Xvoice-arm64.pkg`** ← recommended | Double-click and click through — install, launch-at-login and first launch are all automatic |
| 🍎 macOS (Intel) | **`Xvoice-x86_64.pkg`** ← recommended | Same as above |
| 🍎 macOS (drag-install) | `Xvoice-<arch>.dmg` | Drag **Xvoice** into **Applications** → run **First Run.command** |
| 🐧 Linux | `Xvoice-x86_64.AppImage` | `chmod +x Xvoice-x86_64.AppImage && ./Xvoice-x86_64.AppImage` |

On first launch the app opens your browser to link your Xvoice account. After that it lives in the system tray / menu bar and starts with your computer.

Not sure which Mac you have? → About This Mac. "Apple M1/M2/M3/M4" is **arm64**, "Intel" is **x86_64**. They are not interchangeable — PyInstaller freezes per-architecture, and the wrong one will not open.

> ### ⚠️ Xvoice has no Dock icon on macOS
>
> It is a **menu-bar app** — the purple microphone at the **top-right of your screen**, next to the clock. That is the same role the system tray plays on Windows, and it is deliberate (`LSUIElement`).
>
> If you installed it and "nothing happened", it is almost certainly running up there. Confirm with `pgrep -fl Xvoice`.

**Something not working on macOS?** Run the diagnostic:

```bash
python3 mac/preflight.py
```

It checks architecture match, quarantine, signature, `Info.plist`, every Python dependency and the permission state, and prints the exact fix. See [`MACOS_NOTES.md`](MACOS_NOTES.md) for the full breakdown, including one **known open issue** with the Qt overlay on macOS.

### macOS permissions

Xvoice needs three permissions macOS gates behind Privacy & Security. **First Run.command** in the DMG opens all three panes for you:

| Permission | Why | Where |
|---|---|---|
| **Microphone** | To hear you | Prompted automatically on first `F8` |
| **Accessibility** | To type transcribed text into other apps | Privacy & Security → Accessibility |
| **Input Monitoring** | To notice `F8` while another app is in front | Privacy & Security → Input Monitoring |

Xvoice only appears in the Accessibility / Input Monitoring lists **after** it has been launched once. After switching either on, restart Xvoice (menu-bar icon → *Refresh / Restart*) — macOS only hands a new permission to a freshly started process.

> **"Xvoice is damaged and can't be opened"** — the build is not notarized yet, so macOS quarantines it. `First Run.command` clears that, or run:
> ```bash
> xattr -dr com.apple.quarantine /Applications/Xvoice.app
> ```

---

## 🗣️ Using Dictation

| Action | Result |
|---|---|
| **Hold `F8`** | Recording starts (high beep 🔔) |
| **Speak** | Talk naturally in English — any accent |
| **Release `F8`** | Recording stops (low beep 🔕), transcription begins |
| **Wait ~1–2 s** | Text is typed into your active window ✅ |

Works anywhere text input does — browsers, Word, Notepad, chat, email, editors. **Pro** users can change the push-to-talk key and pick a transcription language from the dashboard.

---

## ✍️ Using Writing

1. **Select** some text in any app.
2. **Click** the floating **Xvoice** button that appears (or right-click after selecting, then click it).
3. **Choose an action** — Improve, Professional, Shorten, Expand, Translate, Summarise, Fix grammar, and more.
4. The result is shown as a preview you can accept, or replaces the selection directly — your choice in settings.

Nothing is copied from your screen until you click the Xvoice button — a stray selection, or a right-click on its own, never touches your clipboard.

---

## 🔑 Account, trials & Pro

- Sign in once through the browser; the app stays linked until you log out.
- **Dictation** and **Writing** each have a free trial. Upgrade to **Pro** for either, or the **Platform** bundle for both, on the billing dashboard.
- **Log out from all devices** with one click — it invalidates every active session immediately.

You do **not** supply an OpenAI key. The desktop app sends audio and text to the Xvoice backend, which holds the key server-side. Your login is stored locally in a private, owner-only file:

| Platform | Config |
|---|---|
| Windows | `%LOCALAPPDATA%\Xvoice\config.json` |
| macOS | `~/Library/Application Support/Xvoice/config.json` |
| Linux | `~/.config/Xvoice/config.json` |

---

## 🩺 Logs & troubleshooting

If something misbehaves, the log file helps (the tray menu can open or export it):

| Platform | Log file |
|---|---|
| Windows | `%LOCALAPPDATA%\Xvoice\xvoice.log` |
| macOS | `~/Library/Logs/Xvoice/xvoice.log` |
| Linux | `~/.local/share/Xvoice/logs/xvoice.log` |

Set `XVOICE_LOG_LEVEL=DEBUG` for verbose logging without rebuilding. **Nothing you dictate or rewrite is written to these logs** — only lengths and status, never content.

---

## 🧠 How it works

```
Hold F8
   ↓
PyAudio captures mic input in 30 ms frames
   ↓
WebRTC VAD drops silence and background noise (only speech frames kept)
   ↓
FFmpeg normalises the volume (loudnorm)
   ↓
Audio is sent to the Xvoice backend, which transcribes it and returns text
   ↓
Text is typed at the cursor via pynput
```

The backend runs the transcription (OpenAI `gpt-4o-transcribe`) and the Writing rewrites (`gpt-4o`) with the API key held server-side — the desktop app never sees it. Writing input is treated strictly as data, never as instructions, on the server.

---

## 🏗️ Building the installers yourself

| Platform | Build the app | Build the installer |
|---|---|---|
| 🪟 Windows | `build_exe.bat` → `dist\xvoice.exe` | Compile `installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) → `Output\XVoiceSetup.exe` |
| 🍎 macOS | `./build_mac.sh` → `dist/Xvoice.app` | `./installer_mac_pkg.sh` → `.pkg` (wizard) or `./installer_mac.sh` → `.dmg` (drag) |
| 🐧 Linux | `pyinstaller --noconfirm xvoice.spec` → `dist/xvoice` | See the `build-linux` job in `.github/workflows/build.yml` |

All three share the same `xvoice.spec`; the version comes from `APP_VERSION` in that file (kept in step with `__version__` in `main.py` and `MyAppVersion` in `installer.iss`). Pushing a `v*` tag builds and publishes every platform via GitHub Actions.

**Optional macOS signing** (removes the Gatekeeper warning entirely):

```bash
export XVOICE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export XVOICE_NOTARY_PROFILE="xvoice-notary"   # see installer_mac.sh
./build_mac.sh && ./installer_mac.sh
```

---

## 🛠️ Running from source (developers)

**Desktop app**

```bash
# Windows
setup.bat
# macOS
./setup_mac.command
# Linux
./setup_mac_linux.sh
```

Each installs the system prerequisites (FFmpeg, PortAudio) and the Python dependencies in `requirements.txt`, registers the app to launch at login, and starts it in the background. Point it at your own backend by editing `RAILWAY_URL` / `FRONTEND_URL` at the top of `main.py`.

**Backend** (FastAPI, in `backend/`)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

It reads its configuration from the environment:

| Variable | Purpose |
|---|---|
| `JWT_SECRET_KEY` | **Required** — signs auth tokens; the app refuses to start without it |
| `OPENAI_API_KEY` | Server-side transcription and rewriting |
| `DATABASE_URL` | PostgreSQL |
| `REDIS_URL` | Transcription queue + rate limiting |
| `STRIPE_SECRET_KEY` | Billing portal |
| `LOVABLE_SYNC_SECRET` | Verifies the subscription-sync webhook |
| `EMAIL_WEBHOOK_SECRET` | Transactional email delivery |

The web dashboard (React) is managed separately and is not in this repository.

---

## 📁 Project structure

```
Voice-text-keyboard/
├── main.py                 # Desktop app: tray, hotkey, recording, auth handshake
├── writing/                # Writing engine (selection capture, Qt overlay, actions)
├── backend/                # FastAPI service (deployed to Railway)
│   ├── main.py             #   app, CORS, startup
│   ├── routers/            #   auth, stats, billing, transcribe, writing, achievements
│   ├── rate_limit.py       #   Redis-backed rate limiting
│   ├── audit.py            #   security / billing audit trail
│   ├── worker.py           #   background transcription workers
│   └── queue_manager.py
├── requirements.txt        # Desktop Python dependencies
├── xvoice.spec             # Shared PyInstaller spec (Windows / macOS / Linux)
│
├── setup.bat               # Windows: run-from-source setup & launcher
├── build_exe.bat           # Windows: build dist\xvoice.exe
├── installer.iss           # Windows: Inno Setup script -> XVoiceSetup.exe
├── ffmpeg.exe              # Windows FFmpeg binary (fetched for builds)
│
├── setup_mac.command       # macOS: run-from-source setup & launcher
├── build_mac.sh            # macOS: build dist/Xvoice.app
├── installer_mac.sh        # macOS: build the .dmg (drag-install)
├── installer_mac_pkg.sh    # macOS: build the .pkg (wizard + auto-setup)
├── MACOS_NOTES.md          # macOS gotchas, signing, and the open Qt issue
├── mac/                    # macOS packaging assets (preflight, entitlements, agent…)
│
├── setup_mac_linux.sh      # Linux (and bare-bones macOS) source setup
└── .github/workflows/      # CI: builds installers on v* tags
```

### Windows ↔ macOS equivalents

| Windows | macOS | Purpose |
|---|---|---|
| `setup.bat` | `setup_mac.command` | Install deps, register at login, launch from source |
| `build_exe.bat` | `build_mac.sh` | Freeze with PyInstaller |
| `installer.iss` (Inno Setup) | `installer_mac_pkg.sh` (`.pkg`) | Wizard-style installer |
| — | `installer_mac.sh` (`.dmg`) | Drag-to-Applications alternative |
| `xvoice.exe` | `Xvoice.app` | The application |
| `XVoiceSetup.exe` | `Xvoice-<ver>-<arch>.pkg` | What users download |
| Authenticode signing | Developer ID + **notarization** | Stops the "unknown publisher" warning |
| `HKCU\...\Run` value | `~/Library/LaunchAgents/com.xvoicekeyboard.xvoice.plist` | Start at login |
| System tray icon | Menu-bar icon (`LSUIElement`) | Background UI |
| `%LOCALAPPDATA%\Xvoice` | `~/Library/Application Support/Xvoice` | Token / config storage |
| `%LOCALAPPDATA%\Xvoice` | `~/Library/Logs/Xvoice` | Logs |

---

## 📄 License

MIT — see [LICENSE](LICENSE). Redistributed FFmpeg binaries are covered by their own LGPL/GPL terms.
