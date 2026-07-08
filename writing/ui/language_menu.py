import tkinter as tk
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

# Keep track of recent for this session
_recent_language = "Spanish" 

def show_language_menu(root, x: int, y: int, on_select: Callable[[str], None]):
    menu_win = tk.Toplevel(root)
    menu_win.overrideredirect(True)
    menu_win.attributes('-topmost', True)
    menu_win.configure(bg=BG_COLOR, highlightbackground=TEXT_COLOR, highlightthickness=1)
    
    # Adjust position slightly down
    menu_win.geometry(f"+{x}+{y + 35}")

    # Reorder languages so recent is first (unless it's English, or just put it at top)
    display_langs = list(LANGUAGES)
    if _recent_language in display_langs and _recent_language != "More...":
        display_langs.remove(_recent_language)
        display_langs.insert(0, _recent_language)
        
    title = tk.Label(menu_win, text="Translate", bg=BG_COLOR, fg=TEXT_COLOR, font=("Segoe UI", 9, "bold"), pady=4)
    title.pack(fill="x")
    
    def make_handler(lang):
        return lambda e: select(lang)
        
    def select(lang):
        global _recent_language
        if lang != "More...":
            _recent_language = lang
        menu_win.destroy()
        if lang != "More...":
            on_select(lang)

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
        
        def on_enter(e, widget=lbl):
            widget.config(bg=HOVER_COLOR)
        def on_leave(e, widget=lbl):
            widget.config(bg=BG_COLOR)
            
        lbl.bind("<Enter>", on_enter)
        lbl.bind("<Leave>", on_leave)
        lbl.bind("<Button-1>", make_handler(lang))

    # Auto close if they click outside. 
    # Since capturing global clicks is handled by pynput in engine.py, 
    # we don't strictly need a focusout handler, but it's safe.
    # However, tkinter focusout without borders is tricky.
    
    # Auto close after 5 seconds of inactivity
    menu_win.after(5000, menu_win.destroy)
