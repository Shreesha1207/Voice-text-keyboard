import tkinter as tk
from typing import Callable

BG_COLOR    = "#2B1B17"
TEXT_COLOR  = "#E8B89C"
HOVER_COLOR = "#3B2620"
DIM_COLOR   = "#6B4A3A"

# All supported writing actions.
# Each entry: (display_label, action_key, has_language_sub_menu)
ACTIONS = [
    ("✨  Improve writing",   "improve",       False),
    ("📝  Fix grammar",       "fix_grammar",   False),
    ("📋  Make shorter",      "shorten",       False),
    ("📣  Make longer",       "expand",        False),
    ("💼  Professional tone", "professional",  False),
    ("😊  Casual tone",       "casual",        False),
    ("🔥  More persuasive",   "persuasive",    False),
    ("📋  Summarise",         "summarise",     False),
    ("🔄  Rephrase",          "rephrase",      False),
    ("🌐  Translate…",        "translate",     True),   # opens language sub-menu
]

LANGUAGES = [
    "English", "Spanish", "French", "German",
    "Hindi",   "Arabic",  "Chinese", "Japanese",
    "Portuguese", "Korean",
]


def show_action_menu(
    root: tk.Tk,
    x: int,
    y: int,
    on_action: Callable[[str, str | None], None],   # (action_key, language_or_None)
) -> None:
    """
    Show the Xvoice Writing action picker at (x, y).
    Calls on_action(action_key, target_language) when an item is chosen.
    'target_language' is None for all actions except translate.
    """
    menu_win = tk.Toplevel(root)
    menu_win.overrideredirect(True)
    menu_win.attributes("-topmost", True)
    menu_win.configure(bg=BG_COLOR, highlightbackground=TEXT_COLOR, highlightthickness=1)
    menu_win.geometry(f"+{x}+{y + 35}")

    title = tk.Label(
        menu_win, text="✦ Xvoice Writing",
        bg=BG_COLOR, fg=TEXT_COLOR,
        font=("Segoe UI", 9, "bold"), pady=5, padx=10,
    )
    title.pack(fill="x")

    sep = tk.Frame(menu_win, bg=TEXT_COLOR, height=1)
    sep.pack(fill="x", padx=5, pady=1)

    # ── Language sub-menu helper ──────────────────────────────────────────────
    def show_language_sub(parent_win: tk.Toplevel) -> None:
        parent_win.destroy()
        lang_win = tk.Toplevel(root)
        lang_win.overrideredirect(True)
        lang_win.attributes("-topmost", True)
        lang_win.configure(bg=BG_COLOR, highlightbackground=TEXT_COLOR, highlightthickness=1)
        lang_win.geometry(f"+{x}+{y + 35}")

        tk.Label(
            lang_win, text="🌐  Translate to…",
            bg=BG_COLOR, fg=TEXT_COLOR,
            font=("Segoe UI", 9, "bold"), pady=5, padx=10,
        ).pack(fill="x")
        tk.Frame(lang_win, bg=TEXT_COLOR, height=1).pack(fill="x", padx=5, pady=1)

        def pick_lang(lang: str) -> None:
            lang_win.destroy()
            on_action("translate", lang)

        for lang in LANGUAGES:
            lbl = tk.Label(
                lang_win, text=lang,
                bg=BG_COLOR, fg=TEXT_COLOR,
                font=("Segoe UI", 9), anchor="w", padx=12, pady=3, cursor="hand2",
            )
            lbl.pack(fill="x")
            lbl.bind("<Enter>",    lambda e, w=lbl: w.config(bg=HOVER_COLOR))
            lbl.bind("<Leave>",    lambda e, w=lbl: w.config(bg=BG_COLOR))
            lbl.bind("<Button-1>", lambda e, l=lang: pick_lang(l))

        lang_win.after(6000, lambda: lang_win.destroy() if lang_win.winfo_exists() else None)

    # ── Render action rows ────────────────────────────────────────────────────
    for label, action_key, needs_lang in ACTIONS:
        row_text = label + ("  ▶" if needs_lang else "")
        lbl = tk.Label(
            menu_win, text=row_text,
            bg=BG_COLOR, fg=TEXT_COLOR,
            font=("Segoe UI", 9), anchor="w", padx=12, pady=3, cursor="hand2",
        )
        lbl.pack(fill="x")
        lbl.bind("<Enter>", lambda e, w=lbl: w.config(bg=HOVER_COLOR))
        lbl.bind("<Leave>", lambda e, w=lbl: w.config(bg=BG_COLOR))

        if needs_lang:
            lbl.bind("<Button-1>", lambda e, mw=menu_win: show_language_sub(mw))
        else:
            def _make_handler(ak: str):
                def _handler(e):
                    menu_win.destroy()
                    on_action(ak, None)
                return _handler
            lbl.bind("<Button-1>", _make_handler(action_key))

    menu_win.after(6000, lambda: menu_win.destroy() if menu_win.winfo_exists() else None)
