import pyaudio
import wave
import sys
from pynput import keyboard as pk
import os
import subprocess
import webrtcvad
if sys.platform == "win32":
    import winsound
else:
    winsound = None
from openai import OpenAI
from dotenv import load_dotenv
import time

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# --- NEW STARTUP LOGIC ---
def setup_startup():
    """Ensures the app runs when the computer starts."""
    # sys.executable points to the .exe or .app binary when compiled
    app_path = os.path.realpath(sys.executable)
    
    if getattr(sys, 'frozen', False):
        exec_args_mac = f'<string>{os.path.realpath(sys.executable)}</string>'
        exec_cmd = os.path.realpath(sys.executable)
    else:
        exec_args_mac = f'<string>{sys.executable}</string>\n            <string>{os.path.realpath(__file__)}</string>'
        exec_cmd = f'"{sys.executable}" "{os.path.realpath(__file__)}"'
        
    if sys.platform == "win32":
        import winreg
        key = winreg.HKEY_CURRENT_USER
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(key, key_path, 0, winreg.KEY_ALL_ACCESS) as reg_key:
                winreg.SetValueEx(reg_key, "AIVoiceTranscriber", 0, winreg.REG_SZ, exec_cmd)
        except Exception as e:
            print(f"Startup failed: {e}")

    elif sys.platform == "darwin":  # macOS
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.ai.transcriber.plist")
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
            <key>Label</key><string>com.ai.transcriber</string>
            <key>ProgramArguments</key><array>
                {exec_args_mac}
            </array>
            <key>RunAtLoad</key><true/>
        </dict></plist>"""
        try:
            with open(plist_path, "w") as f:
                f.write(plist_content)
        except Exception as e:
            print(f"Startup setup failed: {e}")

    elif sys.platform.startswith("linux"):
        autostart_dir = os.path.expanduser("~/.config/autostart")
        os.makedirs(autostart_dir, exist_ok=True)
        desktop_file_path = os.path.join(autostart_dir, "ai-transcriber.desktop")
        desktop_content = f"""[Desktop Entry]
Type=Application
Exec={exec_cmd}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=AIVoiceTranscriber
Comment=AI Voice Transcriber
"""
        try:
            with open(desktop_file_path, "w") as f:
                f.write(desktop_content)
        except Exception as e:
            print(f"Startup setup failed: {e}")

# --- Configuration ---
HOTKEY = 'f8'
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000  # Webrtcvad requires 16000Hz
# Webrtcvad requires frames to be exactly 10ms, 20ms, or 30ms.
# 30ms at 16000Hz is exactly 480 frames.
CHUNK = 480
RAW_FILE = "temp_raw.wav"
NORM_FILE = "temp_norm.wav"

# --- Keyboard helper wrappers using pynput ---
# listener keeps track of whether the hotkey is currently pressed
hotkey_pressed = False

# convert HOTKEY string like 'f8' or single character to a pynput key

def _to_key(hotkey_str):
    if hotkey_str.lower().startswith('f') and hotkey_str[1:].isdigit():
        return getattr(pk.Key, hotkey_str.lower())
    # fallback: use the first character
    return pk.KeyCode.from_char(hotkey_str)

KEY_OBJ = _to_key(HOTKEY)


def on_press(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        hotkey_pressed = True


def on_release(key):
    global hotkey_pressed
    if key == KEY_OBJ:
        hotkey_pressed = False

# start a background listener
listener = pk.Listener(on_press=on_press, on_release=on_release)
listener.start()


def wait_hotkey(hotkey):
    """Block until the specified hotkey is pressed."""
    # we already have a listener updating hotkey_pressed
    while not hotkey_pressed:
        time.sleep(0.01)


def is_pressed(hotkey):
    """Return True if the hotkey is currently held down."""
    return hotkey_pressed


def write_text(text):
    """Type text using pynput's controller."""
    controller = pk.Controller()
    controller.type(text)

# Initialize VAD (Voice Activity Detection)
# Aggressiveness mode from 0 to 3 (3 is the most aggressive in filtering out non-speech)
# Setting to 1 to prevent clipping the ends of words
vad = webrtcvad.Vad(1)

# Initialize OpenAI Client (Requires OPENAI_API_KEY environment variable)
try:
    client = OpenAI()
except Exception as e:
    print(f"[Error] Failed to initialize OpenAI client: {e}")
    print("Please make sure you have set the OPENAI_API_KEY environment variable.")
    exit(1)
# ---------------------

def record_audio(output_filename):
    """ Record audio from microphone, apply VAD to filter noise, and save. """
    audio = pyaudio.PyAudio()
    
    print(f"\n[Ready] Press and hold '{HOTKEY}' to talk...")
    wait_hotkey(HOTKEY)

    if winsound:
        winsound.Beep(1000, 100)  # High pitch beep to signal start
    print(f"[Recording] Listening... Release '{HOTKEY}' when done.")
    
    stream = audio.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
    
    frames = []

    # We will only append frames that contain actual human speech, ignoring breathing/static
    while is_pressed(HOTKEY):
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            
            # Check if this frame contains speech
            is_speech = vad.is_speech(data, RATE)
            if is_speech:
                frames.append(data)
                
        except IOError as e:
            pass

    print("[Finished] Recording stopped.")
    if winsound:
        winsound.Beep(800, 100)  # Lower pitch beep to signal stop

    stream.stop_stream()
    stream.close()
    audio.terminate()
    
    # If no speech was detected at all, don't bother saving
    if not frames:
        return False

    # Save to file
    with wave.open(output_filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        
    return True

def normalize_audio(input_file, output_file):
    """ Use ffmpeg to slightly amplify and normalize the volume without destroying frequency data. """
    print("[Audio] Slightly normalizing volume using FFmpeg for optimal AI clarity...")
    try:
        # We only use a light normalization to ensure it is loud enough, but no aggressive 
        # noise gating or EQ cutting. Neural network models like Whisper need raw audio
        # data to properly differentiate between human speech and background artifacts.
        if sys.platform == "win32":
            ffmpeg_path = os.path.join(os.getcwd(), "ffmpeg.exe")
        else:
            ffmpeg_path = "ffmpeg"
        
        cmd = [
            ffmpeg_path,
            "-y",
            "-i", input_file,
            "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
            "-ar", "16000",
            output_file
        ]
        
        # Run ffmpeg, suppressing output unless there's an error
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True

    except FileNotFoundError:
        print("[Error] FFmpeg not found. Please ensure ffmpeg is installed and added to PATH.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[Error] FFmpeg failed to normalize audio: {e}")
        return False

def transcribe_audio(audio_file):
    """ Feed the normalized audio into OpenAI's Whisper model (via API). """
    print("[STT] Feeding audio to OpenAI Whisper API...")
    try:
        with open(audio_file, "rb") as audio:
            # Using whisper-1 as the underlying speech-to-text model because it is natively 
            # trained to handle noisy environments drastically better than aggressive gating.
            # Strictly enforce English output to prevent hallucinations into other languages.
            transcription = client.audio.transcriptions.create(
                model="gpt-4o-transcribe", 
                file=audio,
                language="en"
            )
            
        final_text = transcription.text.strip()
        if final_text:
            print("\n" + "="*40)
            print("Transcription Result:")
            print("-" * 40)
            print(final_text)
            print("="*40 + "\n")
            
            # Auto-type it!
            write_text(final_text + " ")
            if winsound:
                winsound.Beep(1200, 50)  # Quick success beep
        else:
            print("[STT] No coherent text found.")
            
    except Exception as e:
        print(f"[STT] OpenAI API could not parse the audio: {e}")

def main():
    print("[Init] Ready. Please ensure OPENAI_API_KEY is set in your environment.")
    print(f"[Init] Press '{HOTKEY}' to start recording...")
    
    try:
        while True:
            # 1. Capture Audio using pyaudio + WebRTC VAD
            has_speech = record_audio(RAW_FILE)
            
            if not has_speech:
                continue
                
            # 2. Normalize voice levels with ffmpeg
            if os.path.exists(RAW_FILE):
                norm_success = normalize_audio(RAW_FILE, NORM_FILE)
                
                # 3. Feed to OpenAI API
                file_to_transcribe = NORM_FILE if norm_success else RAW_FILE
                transcribe_audio(file_to_transcribe)
                
                # Cleanup temp files
                if os.path.exists(RAW_FILE):
                    os.remove(RAW_FILE)
                if os.path.exists(NORM_FILE):
                    os.remove(NORM_FILE)

    except KeyboardInterrupt:
        print("\n[Exit] Exiting program...")
    except Exception as e:
        print(f"\n[Error] An unexpected error occurred: {e}")

if __name__ == "__main__":
    setup_startup()   # enable auto start
    main()