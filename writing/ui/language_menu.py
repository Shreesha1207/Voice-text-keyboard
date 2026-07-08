import tkinter as tk
import os
import sys
import json
from typing import Callable

BG_COLOR = "#2B1B17"
TEXT_COLOR = "#E8B89C"
HOVER_COLOR = "#3B2620"

# Default languages
LANGUAGES = [
    "English",
    "Spanish",
    "French",
    "German",
    "Japanese",
    "Hindi",
    "More..."
]

# Determine config file path to read/write recent language
if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Xvoice")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "Xvoice")
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

def load_recent_lang():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f).get('recent_lang')
        except Exception:
            pass
    return None

def save_recent_lang(lang):
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
        except Exception:
            pass
    data['recent_lang'] = lang
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def show_language_menu(root, x: int, y: int, on_select: Callable[[str], None]):
    menu_win = tk.Toplevel(root)
    menu_win.overrideredirect(True)
    menu_win.attributes('-topmost', True)
    menu_win.configure(bg=BG_COLOR, highlightbackground=TEXT_COLOR, highlightthickness=1)
    
    # Adjust position slightly down
    menu_win.geometry(f"+{x}+{y + 35}")

    # Reorder languages
    display_langs = list(LANGUAGES)
    recent = load_recent_lang()
    
    title = tk.Label(menu_win, text="Translate", bg=BG_COLOR, fg=TEXT_COLOR, font=("Segoe UI", 9, "bold"), pady=4)
    title.pack(fill="x")
    
    def make_handler(lang):
        return lambda e: select(lang)
        
    def select(lang):
        if lang != "More...":
            save_recent_lang(lang)
        menu_win.destroy()
        if lang != "More...":
            on_select(lang)

    # Render Recent
    if recent and recent in display_langs and recent != "More...":
        lbl = tk.Label(menu_win, text=f"Recent: {recent}", bg=BG_COLOR, fg=TEXT_COLOR, font=("Segoe UI", 9, "italic"), anchor="w", padx=10, pady=3, cursor="hand2")
        lbl.pack(fill="x")
        lbl.bind("<Enter>", lambda e, w=lbl: w.config(bg=HOVER_COLOR))
        lbl.bind("<Leave>", lambda e, w=lbl: w.config(bg=BG_COLOR))
        lbl.bind("<Button-1>", make_handler(recent))
        
        # Separator
        sep = tk.Frame(menu_win, bg=TEXT_COLOR, height=1)
        sep.pack(fill="x", padx=5, pady=2)

    # Render All
    for lang in display_langs:
        lbl = tk.Label(
            menu_win, 
            text=lang, 
            bg=BG_COLOR, 
            fg=TEXT_COLOR,
            font=("Segoe UI", 9),
            anchor="w",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        lbl.pack(fill="x")
        
        lbl.bind("<Enter>", lambda e, w=lbl: w.config(bg=HOVER_COLOR))
        lbl.bind("<Leave>", lambda e, w=lbl: w.config(bg=BG_COLOR))
        lbl.bind("<Button-1>", make_handler(lang))

    # Auto close if they click outside. 
    # Since capturing global clicks is handled by pynput in engine.py, 
    # we don't strictly need a focusout handler, but it's safe.
    # However, tkinter focusout without borders is tricky.
    
    # Auto close after 5 seconds of inactivity
    menu_win.after(5000, menu_win.destroy)
