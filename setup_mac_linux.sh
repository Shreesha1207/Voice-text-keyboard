#!/bin/bash
echo "=========================================="
echo "  Voice-to-Text Keyboard Setup (Mac/Linux)"
echo "=========================================="
echo

echo "[1/3] Installing system prerequisites..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    # Mac OSX — auto-install Homebrew if missing
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add Homebrew to PATH for Apple Silicon Macs
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi
    # Auto-install portaudio (required to build PyAudio) and python3 (bundles pip)
    brew install ffmpeg portaudio python3
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y ffmpeg portaudio19-dev python3-dev
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm ffmpeg portaudio
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y ffmpeg portaudio-devel python3-devel
    else
        echo "Could not detect package manager. Please install ffmpeg and portaudio manually."
    fi
else
    echo "Unsupported OS"
    exit 1
fi

echo
echo "[2/3] Installing Python dependencies..."

# Determine pip command — use python3 -m pip as it works on Mac
# even when pip/pip3 are not in PATH
if [[ "$OSTYPE" == "darwin"* ]] && [[ -f "/opt/homebrew/bin/python3" ]]; then
    # Apple Silicon Mac: use Homebrew's python3 explicitly (brew may not be in PATH yet)
    PIP_CMD="/opt/homebrew/bin/python3 -m pip"
elif [[ "$OSTYPE" == "darwin"* ]] && [[ -f "/usr/local/bin/python3" ]]; then
    # Intel Mac: Homebrew installs to /usr/local
    PIP_CMD="/usr/local/bin/python3 -m pip"
elif command -v python3 &> /dev/null; then
    PIP_CMD="python3 -m pip"
elif command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
elif command -v pip &> /dev/null; then
    PIP_CMD="pip"
else
    echo "[ERROR] No pip or python3 found. Please install Python 3 first."
    exit 1
fi

# Install PyAudio separately first — it requires portaudio headers to build the wheel
echo "Installing PyAudio (requires portaudio to be installed first)..."
$PIP_CMD install pyaudio
if [ $? -ne 0 ]; then
    echo
    echo "[ERROR] Failed to install PyAudio. Please ensure portaudio is installed and try again."
    exit 1
fi

# Install the rest of the dependencies
$PIP_CMD install -r requirements.txt
if [ $? -ne 0 ]; then
    echo
    echo "[ERROR] Failed to install dependencies. Make sure Python 3 is installed."
    exit 1
fi

echo
echo "[3/3] Triggering Microphone Permissions..."
echo "If prompted, please click 'Allow' so the AI can hear you."
echo "Waiting for microphone access..."

# Run a quick python snippet just to trigger the Apple/OS microphone permission prompt.
# TCC (privacy framework) will pause this script until the user clicks Allow/Deny.
if command -v python3 &> /dev/null; then
    PY_BIN="python3"
else
    PY_BIN="python"
fi

$PY_BIN -c "
import pyaudio
import sys
p = pyaudio.PyAudio()
try:
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
    stream.close()
except Exception as e:
    pass
finally:
    p.terminate()
"

echo
echo "Permissions processed. Launching the script now in the background..."
# Run python invisibly in background (restoring the discreet nature)
nohup $PY_BIN main.py >/dev/null 2>&1 &

echo
echo "=========================================="
echo "  SUCCESS!"
echo "=========================================="
echo "The system is now running invisibly in the background."
echo "You can use the F8 hotkey anywhere to talk."
echo
echo "You can manually configure this script to run on startup based on your OS."
echo
