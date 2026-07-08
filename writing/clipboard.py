import time
import sys
from pynput.keyboard import Controller, Key
import tkinter as tk

keyboard = Controller()

# Use a single hidden Tk instance for clipboard if needed when overlay isn't active
_tk_instance = None

def _get_tk():
    global _tk_instance
    if _tk_instance is None:
        _tk_instance = tk.Tk()
        _tk_instance.withdraw()
    return _tk_instance

def get_clipboard() -> str:
    """Gets the current text from the clipboard."""
    root = _get_tk()
    try:
        text = root.clipboard_get()
        return text
    except tk.TclError:
        return ""

def set_clipboard(text: str):
    """Sets the given text to the clipboard."""
    root = _get_tk()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    time.sleep(0.05) # ensure clipboard is updated

def copy_selection() -> str:
    """Simulates Ctrl+C and returns the copied text."""
    # Press Ctrl+C
    # Use cmd on mac, ctrl on windows/linux
    mod_key = Key.cmd if sys.platform == "darwin" else Key.ctrl
    with keyboard.pressed(mod_key):
        keyboard.press('c')
        keyboard.release('c')
    
    time.sleep(0.1) # Wait for OS to copy to clipboard
    return get_clipboard()

def paste_selection():
    """Simulates Ctrl+V to paste the clipboard content."""
    mod_key = Key.cmd if sys.platform == "darwin" else Key.ctrl
    with keyboard.pressed(mod_key):
        keyboard.press('v')
        keyboard.release('v')
    time.sleep(0.1)
