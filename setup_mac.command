#!/usr/bin/env bash
#
#  Voice-to-Text Keyboard Setup — macOS
#  The mac counterpart of setup.bat.  Runs Xvoice straight from this source
#  checkout: installs the dependencies, registers it to start at login, and
#  launches it in the background.
#
#  Double-click this file in Finder, or run:  ./setup_mac.command
#
#  Want the packaged app instead of running from source?  Download
#  Xvoice.dmg from the releases page, or build it here with:
#      ./build_mac.sh && ./installer_mac.sh
#
set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

LABEL="com.xvoicekeyboard.xvoice"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$ROOT/mac/com.xvoicekeyboard.xvoice.plist"

echo "=========================================="
echo "   Voice-to-Text Keyboard Setup (macOS)"
echo "=========================================="
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] This script is for macOS. On Linux use setup_mac_linux.sh."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────
#  [1/5] System prerequisites
# ─────────────────────────────────────────────────────────────────────────
echo "[1/5] Installing system prerequisites..."
if ! command -v brew >/dev/null 2>&1; then
    echo "      Homebrew not found. Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -f /usr/local/bin/brew   ]] && eval "$(/usr/local/bin/brew shellenv)"
fi

# ffmpeg normalizes the recording; portaudio is what PyAudio builds against.
for pkg in ffmpeg portaudio python3; do
    brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done

BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /usr/local)"
export LDFLAGS="-L${BREW_PREFIX}/lib ${LDFLAGS:-}"
export CPPFLAGS="-I${BREW_PREFIX}/include ${CPPFLAGS:-}"
echo

# ─────────────────────────────────────────────────────────────────────────
#  [2/5] Python dependencies
# ─────────────────────────────────────────────────────────────────────────
echo "[2/5] Installing Python dependencies..."

# Pick a python3 the same way setup_mac_linux.sh does — brew's python may not
# be on PATH yet in this shell.
if [[ -x "${BREW_PREFIX}/bin/python3" ]]; then
    PY="${BREW_PREFIX}/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "[ERROR] No python3 found. Install it with: brew install python3"
    exit 1
fi
echo "      Using $PY ($("$PY" -V 2>&1))"

# PyAudio first and on its own — it compiles against the portaudio headers and
# a failure here is worth reporting clearly rather than burying in a batch.
"$PY" -m pip install --upgrade pip
"$PY" -m pip install pyaudio
if [[ $? -ne 0 ]]; then
    echo
    echo "[ERROR] Failed to install PyAudio."
    echo "        Make sure portaudio is installed (brew install portaudio)"
    echo "        and try again."
    exit 1
fi

"$PY" -m pip install -r requirements.txt
if [[ $? -ne 0 ]]; then
    echo
    echo "[ERROR] Failed to install dependencies."
    exit 1
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [3/5] Permissions
# ─────────────────────────────────────────────────────────────────────────
echo "[3/5] Triggering microphone permission..."
echo "      If prompted, click 'Allow' so Xvoice can hear you."
# Opening an input stream is what makes TCC show the prompt; the call blocks
# until the user answers.
"$PY" - <<'PYEOF' 2>/dev/null
import pyaudio
p = pyaudio.PyAudio()
try:
    s = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
               input=True, frames_per_buffer=1024)
    s.close()
except Exception:
    pass
finally:
    p.terminate()
PYEOF
echo

echo "      Xvoice also needs two permissions macOS cannot prompt for:"
echo
echo "        * Accessibility     — to type text into other apps"
echo "        * Input Monitoring  — to notice F8 while another app is in front"
echo
echo "      When running from source these are granted to your TERMINAL app"
echo "      (Terminal / iTerm), not to Xvoice — that is expected."
echo
read -r -p "      Open those settings panes now? [Y/n] " REPLY
if [[ ! "$REPLY" =~ ^[Nn] ]]; then
    for pane in Privacy_Accessibility Privacy_ListenEvent; do
        open "x-apple.systempreferences:com.apple.preference.security?$pane"
        sleep 1
    done
    read -n 1 -s -r -p "      Press any key when you are done..."
    echo
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [4/5] Launch at login
# ─────────────────────────────────────────────────────────────────────────
#  The LaunchAgent below is the macOS equivalent of the Startup-folder VBScript
#  that setup.bat writes on Windows.
echo "[4/5] Configuring to run hidden at login..."
if [[ -f "$TEMPLATE" ]]; then
    mkdir -p "$(dirname "$AGENT")"
    ARGS=$(printf '        <string>%s</string>\n        <string>%s</string>' \
                  "$PY" "$ROOT/main.py")
    "$PY" - "$TEMPLATE" "$AGENT" "$ARGS" "$ROOT" <<'PYEOF'
import sys
tmpl, out, args, workdir = sys.argv[1:5]
with open(tmpl) as f:
    text = f.read()
text = text.replace('__PROGRAM_ARGUMENTS__', args)
text = text.replace('__WORKING_DIRECTORY__', workdir)
with open(out, 'w') as f:
    f.write(text)
PYEOF
    launchctl unload "$AGENT" 2>/dev/null || true
    launchctl load  "$AGENT" 2>/dev/null || true
    echo "      Installed $AGENT"
else
    echo "      [WARN] $TEMPLATE missing — skipping launch-at-login."
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [5/5] Launch
# ─────────────────────────────────────────────────────────────────────────
echo "[5/5] Launching in the background..."
# Kill any instance already running so we do not fight the single-instance
# socket lock in main.py.
pkill -f "main.py" 2>/dev/null || true
sleep 1
nohup "$PY" "$ROOT/main.py" >/dev/null 2>&1 &
sleep 2
echo

echo "=========================================="
echo "                SUCCESS!"
echo "=========================================="
echo "Xvoice is running in the background."
echo "Hold F8 anywhere to dictate."
echo
echo "It will also start automatically every time you log in."
echo
echo "Turn that off with:"
echo "  launchctl unload $AGENT && rm $AGENT"
echo
echo "Logs: ~/Library/Logs/Xvoice/"
echo
