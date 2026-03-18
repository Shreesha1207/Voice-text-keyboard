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

Double-click **`setup.bat`** or run it from a terminal:

```bat
setup.bat
```

This will:
1. Install all Python dependencies via `pip`
2. Add the app to your **Windows Startup folder** so it launches automatically on boot
3. Launch the app immediately in the background (no console window)

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
├── main.py           # Main app logic
├── setup.bat         # One-click setup & launcher
├── requirements.txt  # Python dependencies
├── .env              # Your OpenAI API key (not committed)
├── ffmpeg.exe        # FFmpeg binary (not committed)
└── README.md         # This file
```

---

## 🔑 API Key Distribution

If sharing the `.exe` with others, each user must provide their own `OPENAI_API_KEY`. On first launch, the app reads the key from the `.env` file located in the same directory as `main.py` / the `.exe`.

---

## 📄 License

MIT — free to use and modify.