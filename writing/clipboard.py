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

# Window classes of the Windows console hosts. Ctrl+C means "copy" in an editor
# but SIGINT in a console: sent to a terminal with no active selection it kills
# whatever command is running. Terminals also tend to eat the selection on
# right-click (conhost QuickEdit copies and clears it), so by the time the Xvoice
# button is clicked there is often nothing selected left to copy — and the
# keystroke lands as an interrupt instead.
_CONSOLE_WINDOW_CLASSES = {
    "ConsoleWindowClass",              # cmd.exe / PowerShell via conhost
    "CASCADIA_HOSTING_WINDOW_CLASS",   # Windows Terminal
    "PseudoConsoleWindow",
    "mintty",                          # Git Bash / MSYS2
}


def _foreground_is_console() -> bool:
    """True if the window about to receive our keystroke is a Windows console."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value in _CONSOLE_WINDOW_CLASSES
    except Exception:
        return False


def copy_selection() -> str:
    """Simulates a copy shortcut and returns the copied text.

    Uses Ctrl+Shift+C when the focused window is a console. That is the copy
    shortcut in Windows Terminal, and in older consoles it is simply ignored — so
    the worst case is that nothing is copied and the caller dismisses cleanly,
    rather than interrupting the user's running command.
    """
    if sys.platform == "darwin":
        mod_key, shift = Key.cmd, None
    elif _foreground_is_console():
        mod_key, shift = Key.ctrl, Key.shift
    else:
        mod_key, shift = Key.ctrl, None

    keyboard.press(mod_key)
    if shift:
        keyboard.press(shift)
    keyboard.press('c')
    keyboard.release('c')
    if shift:
        keyboard.release(shift)
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
