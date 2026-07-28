# 🎙️ Voice-Text Keyboard

A lightweight background utility that lets you **dictate text anywhere** using a hotkey. Hold **F8**, speak, release — your words are instantly typed into whatever app is in focus.

Supports **English spoken in any accent** (Indian, American, Australian, British, French, German, Dutch, and more).

---

## ✨ Features

- 🔴 **Push-to-talk** — Hold `F8` to record, release to transcribe
- 🌍 **Accent-aware** — Works with all English accents out of the box
- 🔇 **Smart noise filtering** — WebRTC VAD removes silence and background noise before sending audio to the API
- 🔊 **Audio normalization** — FFmpeg lightly normalizes volume for optimal AI clarity
- 🤖 **Auto-types the result** — Transcribed text is typed directly into your active window
- 🔔 **Audio feedback** — Beeps signal recording start, stop, and success
- 🚀 **Runs on startup** — Automatically launches invisibly in the background on boot

---

## 📋 Requirements

- **Python 3.8+**
- **FFmpeg** — must be placed in the project root as `ffmpeg.exe` (or added to system PATH)
- **OpenAI API Key**

### Python Dependencies

```
pyaudio
pynput
webrtcvad-wheels
openai
python-dotenv
pyinstaller
```

---

## ⚙️ Setup

### 1. Clone / Download the project

```
git clone https://github.com/your-username/Voice-text-keyboard.git
cd Voice-text-keyboard
```

### 2. Add your OpenAI API Key

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-your-key-here
```

### 3. Run Setup

**Windows** — double-click **`setup.bat`** or run it from a terminal:

```bat
setup.bat
```

**macOS** — double-click **`setup_mac.command`** or run it from a terminal:

```bash
./setup_mac.command
```

**Linux** — run **`setup_mac_linux.sh`**:

```bash
./setup_mac_linux.sh
```

Each of these will:
1. Install the system prerequisites (ffmpeg, portaudio) and Python dependencies
2. Register the app to launch automatically at login
   (Windows Startup folder / macOS LaunchAgent)
3. Launch the app immediately in the background (no console window)

---

## 📦 Installing the packaged app

Prebuilt downloads are published on the [Releases page](../../releases) — no
Python required.

| Platform | Download | How to install |
|---|---|---|
| 🪟 Windows | `xvoice.exe` / `XVoiceSetup.exe` | Double-click to run |
| 🍎 macOS (Apple Silicon) | `Xvoice-arm64.dmg` | Open the DMG → drag **Xvoice** into **Applications** → run **First Run.command** |
| 🍎 macOS (Intel) | `Xvoice-x86_64.dmg` | Same as above |
| 🐧 Linux | `Xvoice-x86_64.AppImage` | `chmod +x Xvoice-x86_64.AppImage && ./Xvoice-x86_64.AppImage` |

Not sure which Mac you have?  → About This Mac. "Apple M1/M2/M3/M4" is
arm64, "Intel" is x86_64.

### macOS permissions

Xvoice is a menu-bar app (no Dock icon) and needs three permissions macOS
gates behind Privacy & Security. **First Run.command** in the DMG opens all
three panes for you:

| Permission | Why | Where |
|---|---|---|
| **Microphone** | To hear you | Prompted automatically on first `F8` |
| **Accessibility** | To type the transcribed text into other apps | Privacy & Security → Accessibility |
| **Input Monitoring** | To notice `F8` while another app is in front | Privacy & Security → Input Monitoring |

Xvoice only appears in the Accessibility / Input Monitoring lists **after**
it has been launched once. After you switch either one on, restart Xvoice
(menu-bar icon → *Refresh / Restart*) — macOS only hands a new permission to
a freshly started process.

> **"Xvoice is damaged and can't be opened"** — the build is not notarized
> yet, so macOS quarantines it. `First Run.command` clears that, or run:
> ```bash
> xattr -dr com.apple.quarantine /Applications/Xvoice.app
> ```

---

## 🏗️ Building the installers yourself

| Platform | Build the app | Build the installer |
|---|---|---|
| 🪟 Windows | `build_exe.bat` → `dist\xvoice.exe` | Compile `installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) → `Output\XVoiceSetup.exe` |
| 🍎 macOS | `./build_mac.sh` → `dist/Xvoice.app` | `./installer_mac.sh` → `dist/Xvoice-<version>-<arch>.dmg` |
| 🐧 Linux | `pyinstaller --noconfirm xvoice.spec` → `dist/xvoice` | See the `build-linux` job in `.github/workflows/build.yml` |

All three share the same `xvoice.spec`. Pushing a `v*` tag builds and
publishes every platform automatically via GitHub Actions.

**Optional macOS signing** (removes the Gatekeeper warning entirely):

```bash
export XVOICE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export XVOICE_NOTARY_PROFILE="xvoice-notary"   # see installer_mac.sh
./build_mac.sh && ./installer_mac.sh
```

---

## 🎮 Usage

Once running:

| Action | Result |
|---|---|
| **Hold `F8`** | Starts recording (you'll hear a high beep 🔔) |
| **Speak** | Talk naturally in English — any accent |
| **Release `F8`** | Stops recording (low beep 🔕), transcription begins |
| **Wait ~1–2 sec** | Text is auto-typed into your active window ✅ |

> The app runs invisibly in the background. You can use it in any app — browsers, Word, Notepad, chat apps, etc.

---

## 🛠️ Configuration

All settings are at the top of `main.py`:

| Constant | Default | Description |
|---|---|---|
| `HOTKEY` | `'f8'` | Push-to-talk key |
| `RATE` | `16000` | Audio sample rate (Hz) — required by WebRTC VAD |
| `CHUNK` | `480` | Audio frame size (30ms at 16kHz) |
| `RAW_FILE` | `temp_raw.wav` | Temp file for raw recorded audio |
| `NORM_FILE` | `temp_norm.wav` | Temp file for normalized audio |

To change the hotkey to e.g. `F9`, edit:
```python
HOTKEY = 'f9'
```

---

## 🧠 How It Works

```
Hold F8
   ↓
PyAudio captures mic input in 30ms chunks
   ↓
WebRTC VAD filters out silence and noise (only speech frames kept)
   ↓
FFmpeg normalizes audio volume (loudnorm filter)
   ↓
Audio sent to OpenAI gpt-4o-transcribe (Whisper API)
   with accent-aware prompt for best accuracy
   ↓
Transcribed text auto-typed at cursor via pynput
```

### Accent Support

The transcription API call includes a `prompt` that informs the model the speaker may have an Indian, American, Australian, British, French, German, Dutch, or other English accent. Combined with `webrtcvad.Vad(0)` (least aggressive VAD mode) to avoid clipping speech patterns with different cadence, this gives the best accuracy across accents.

---

## 📁 Project Structure

```
Voice-text-keyboard/
├── main.py                 # Main app logic
├── writing/                # Writing engine (Qt overlay, actions, backend client)
├── backend/                # API service
├── requirements.txt        # Python dependencies
├── xvoice.spec             # Shared PyInstaller spec (Windows / macOS / Linux)
│
├── setup.bat               # Windows: run-from-source setup & launcher
├── build_exe.bat           # Windows: build dist\xvoice.exe
├── installer.iss           # Windows: Inno Setup script -> XVoiceSetup.exe
├── ffmpeg.exe              # Windows FFmpeg binary
│
├── setup_mac.command       # macOS: run-from-source setup & launcher
├── build_mac.sh            # macOS: build dist/Xvoice.app
├── installer_mac.sh        # macOS: build dist/Xvoice-<ver>-<arch>.dmg
├── mac/
│   ├── make_icns.py                     # Generates the app icon
│   ├── entitlements.plist               # Hardened-runtime entitlements
│   ├── com.xvoicekeyboard.xvoice.plist  # LaunchAgent template (login start)
│   ├── first-run.command                # Ships in the DMG: permissions + launch
│   └── dmg-readme.txt                   # Ships in the DMG as "Read Me.txt"
│
├── setup_mac_linux.sh      # Linux (and bare-bones macOS) source setup
├── .env                    # Your API key (not committed)
└── README.md               # This file
```

### Windows ↔ macOS equivalents

| Windows | macOS | Purpose |
|---|---|---|
| `setup.bat` | `setup_mac.command` | Install deps, register at login, launch from source |
| `build_exe.bat` | `build_mac.sh` | Freeze with PyInstaller |
| `installer.iss` (Inno Setup) | `installer_mac.sh` (DMG) | Wrap the build into a distributable installer |
| `xvoice.exe` | `Xvoice.app` | The application |
| `XVoiceSetup.exe` | `Xvoice-<ver>-<arch>.dmg` | What users download |
| `HKCU\...\Run` registry value | `~/Library/LaunchAgents/com.xvoicekeyboard.xvoice.plist` | Start at login |
| System tray icon | Menu-bar icon (`LSUIElement`) | Background UI |
| `%LOCALAPPDATA%\Xvoice` | `~/Library/Application Support/Xvoice` | Token / config storage |
| `%LOCALAPPDATA%\Xvoice` | `~/Library/Logs/Xvoice` | Logs |

---

## 🔑 API Key Distribution

If sharing the `.exe` with others, each user must provide their own `OPENAI_API_KEY`. On first launch, the app reads the key from the `.env` file located in the same directory as `main.py` / the `.exe`.

---

## 📄 License

MIT — free to use and modify.