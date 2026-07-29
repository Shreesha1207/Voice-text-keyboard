"""
clipboard.py — Uses pyperclip for reliable cross-thread clipboard access.
No Tkinter here. Tkinter is NOT thread-safe and must only be touched from
the UI thread in overlay.py.
"""
import time
import sys
import logging
from pynput.keyboard import Controller, Key
import pyperclip

logger = logging.getLogger(__name__)

keyboard = Controller()


def check_available() -> bool:
    """Verify the clipboard actually works, and say so loudly if it doesn't.

    On Linux pyperclip needs xclip or xsel; without one, copy() raises and the
    handlers below swallow it — so the entire Writing feature silently did nothing
    with no error and no log line. Call this once at startup.
    """
    try:
        pyperclip.copy(pyperclip.paste() or "")
        return True
    except Exception as e:
        logger.error(
            "Clipboard unavailable (%s). The Writing engine cannot read selected "
            "text. On Linux install xclip or xsel: sudo apt install xclip", e
        )
        return False

def get_clipboard() -> str:
    """Gets the current text from the clipboard."""
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""

def set_clipboard(text: str):
    """Sets the given text to the clipboard."""
    try:
        pyperclip.copy(text or "")
        time.sleep(0.05)  # ensure clipboard is updated
    except Exception:
        pass

def copy_selection() -> str:
    """Simulates Ctrl+C and returns the copied text."""
    mod_key = Key.cmd if sys.platform == "darwin" else Key.ctrl
    keyboard.press(mod_key)
    keyboard.press('c')
    keyboard.release('c')
    keyboard.release(mod_key)
    time.sleep(0.15)  # Wait for OS to copy to clipboard
    return get_clipboard()

def paste_selection():
    """Simulates Ctrl+V to paste the clipboard content."""
    mod_key = Key.cmd if sys.platform == "darwin" else Key.ctrl
    keyboard.press(mod_key)
    keyboard.press('v')
    keyboard.release('v')
    keyboard.release(mod_key)
    time.sleep(0.1)
