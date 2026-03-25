#!/bin/bash
echo "=========================================="
echo "  Voice-to-Text Keyboard Setup (Mac/Linux)"
echo "=========================================="
echo

echo "[1/3] Installing system prerequisites..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    # Mac OSX
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Please install Homebrew first."
        exit 1
    fi
    brew install ffmpeg portaudio
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y ffmpeg portaudio19-dev python3-pyaudio
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm ffmpeg portaudio
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y ffmpeg portaudio-devel
    else
        echo "Could not detect package manager. Please install ffmpeg and portaudio manually."
    fi
else
    echo "Unsupported OS"
    exit 1
fi

echo
echo "[2/3] Installing Python dependencies..."
# Use pip3 if available, otherwise pip
if command -v pip3 &> /dev/null; then
    pip3 install -r requirements.txt
else
    pip install -r requirements.txt
fi

if [ $? -ne 0 ]; then
    echo
    echo "[ERROR] Failed to install dependencies. Make sure Python 3 is installed."
    exit 1
fi

echo
echo "[3/3] Launching the script now in the background..."
# Run python in background
if command -v python3 &> /dev/null; then
    nohup python3 main.py >/dev/null 2>&1 &
else
    nohup python main.py >/dev/null 2>&1 &
fi

echo
echo "=========================================="
echo "  SUCCESS!"
echo "=========================================="
echo "The script is now running invisibly in the background."
echo "You can use the F8 hotkey anywhere."
echo
echo "You can manually configure this script to run on startup based on your OS."
echo
