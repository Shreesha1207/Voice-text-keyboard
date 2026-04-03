import pyaudio
import wave
import sys
from pynput import keyboard as pk
import os
import subprocess
import webrtcvad
import json
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

if sys.platform == "win32":
    import winsound
else:
    winsound = None

# --- Configuration URLs ---
RAILWAY_URL = "https://voicetotext-keyboard-production.up.railway.app/api"
# Change this to your live lovable.app URL before compiling the final .exe!
FRONTEND_URL = "http://localhost:8080" 
LOCAL_PORT = 45678

# --- Audio Settings ---
HOTKEY = 'f8'
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 480
RAW_FILE = "temp_raw.wav"
NORM_FILE = "temp_norm.wav"

CONFIG_DIR = os.path.join(os.getenv('LOCALAPPDATA', os.path.expanduser('~')), 'Xvoice')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

auth_success = False

def load_token():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return data.get('access_token')
        except:
            pass
    return None

def save_token(access_token):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'access_token': access_token}, f)

class AuthHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/auth':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            token = data.get('token')
            if token:
                save_token(token)
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"success"}')
                
                global auth_success
                auth_success = True
            else:
                self.send_response(400)
                self.end_headers()
                
    def log_message(self, format, *args):
        pass

def require_auth():
    global auth_success
    token = load_token()
    
    if token:
        # Check if token is still valid
        try:
            r = requests.get(f"{RAILWAY_URL}/auth/validate", headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200 and r.json().get('allowed'):
                return True
        except:
            pass
            
    print("\n[Auth] No valid session found. Opening browser to authenticate...")
    webbrowser.open(f"{FRONTEND_URL}/connect-desktop")
    
    print(f"[Auth] Waiting for Lovable desktop connection callback...")
    server = HTTPServer(('127.0.0.1', LOCAL_PORT), AuthHandler)
    server.timeout = 1 # Non-blocking loop
    
    while not auth_success:
        server.handle_request()
        
    print("[Auth] Successfully connected to Xvoice account!\n")
    return True

# --- NEW STARTUP LOGIC ---
def setup_startup():
    """Ensures the app runs when the computer starts."""
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
            pass

hotkey_pressed = False

def _to_key(hotkey_str):
    if hotkey_str.lower().startswith('f') and hotkey_str[1:].isdigit():
        return getattr(pk.Key, hotkey_str.lower())
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

listener = pk.Listener(on_press=on_press, on_release=on_release)
listener.start()

def wait_hotkey(hotkey):
    while not hotkey_pressed:
        time.sleep(0.01)

def is_pressed(hotkey):
    return hotkey_pressed

def write_text(text):
    controller = pk.Controller()
    controller.type(text)

vad = webrtcvad.Vad(1)

def record_audio(output_filename):
    audio = pyaudio.PyAudio()
    wait_hotkey(HOTKEY)

    if winsound: winsound.Beep(1000, 100)
    
    try:
        stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    except:
        audio.terminate()
        while is_pressed(HOTKEY): time.sleep(0.1)
        return False
    
    frames = []

    while is_pressed(HOTKEY):
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            if vad.is_speech(data, RATE):
                frames.append(data)
        except IOError:
            pass

    if winsound: winsound.Beep(800, 100)

    stream.stop_stream()
    stream.close()
    audio.terminate()
    
    if not frames:
        return False

    with wave.open(output_filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        
    return True

def normalize_audio(input_file, output_file):
    try:
        ffmpeg_path = os.path.join(os.getcwd(), "ffmpeg.exe") if sys.platform == "win32" else "ffmpeg"
        cmd = [ffmpeg_path, "-y", "-i", input_file, "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", "-ar", "16000", output_file]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except:
        return False

def transcribe_audio(audio_file):
    token = load_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with open(audio_file, "rb") as f:
            files = {"file": ("audio.wav", f, "audio/wav")}
            response = requests.post(f"{RAILWAY_URL}/transcribe", headers=headers, files=files)
            
        if response.status_code == 200:
            data = response.json()
            text = data.get("text", "")
            if text:
                write_text(text + " ")
                if winsound: winsound.Beep(1200, 50)
        elif response.status_code == 403:
            print("[Error] Trial expired. Please upgrade in the dashboard to continue.")
            if winsound: winsound.Beep(400, 500)
        elif response.status_code == 401:
            print("[Error] Session expired. Please restart the app to log in again.")
            os.remove(CONFIG_FILE) # Force relogin on next boot
        elif response.status_code == 429:
            print("[Error] Server overloaded right now. Try again.")
        else:
            print(f"[STT Error] {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"[STT] API connection failed: {e}")

def main():
    if not require_auth():
        return
        
    print(f"\n[Init] Xvoice running perfectly. Press '{HOTKEY}' anywhere to dictate!")
    
    try:
        while True:
            has_speech = record_audio(RAW_FILE)
            if not has_speech: continue
                
            if os.path.exists(RAW_FILE):
                norm_success = normalize_audio(RAW_FILE, NORM_FILE)
                file_to_transcribe = NORM_FILE if norm_success else RAW_FILE
                transcribe_audio(file_to_transcribe)
                
                if os.path.exists(RAW_FILE): os.remove(RAW_FILE)
                if os.path.exists(NORM_FILE): os.remove(NORM_FILE)

    except KeyboardInterrupt:
        print("\n[Exit] Exiting program...")
        
if __name__ == "__main__":
    setup_startup()
    main()