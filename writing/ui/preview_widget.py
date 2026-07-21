"""
VS Code-style inline preview widget for Xvoice Writing Engine.

Shows the rewritten text in a floating panel beside the cursor with
Accept / Dismiss buttons. Accepts on click replaces the original selection;
Dismiss closes without changing anything.
"""
import tkinter as tk
from typing import Callable

BG_MAIN    = "#1E1E1E"   # dark editor background
BG_HEADER  = "#252526"   # VS Code title bar tone
BG_BTN_ACC = "#0E7A0D"   # green accept
BG_BTN_DIS = "#5A1A1A"   # muted red dismiss
TEXT_COLOR = "#D4D4D4"   # VS Code default text
ACCENT     = "#569CD6"   # VS Code blue
BORDER     = "#3C3C3C"
FONT_BODY  = ("Segoe UI", 9)
FONT_HEAD  = ("Segoe UI", 9, "bold")
FONT_LABEL = ("Segoe UI", 8)

MAX_PREVIEW_CHARS = 400   # chars shown before truncation
PREVIEW_WIDTH     = 420   # px


def show_preview_widget(
    root: tk.Tk,
    x: int,
    y: int,
    action: str,
    original_text: str,
    result_text: str,
    on_accept: Callable[[], None],
    on_dismiss: Callable[[], None],
) -> None:
    """
    Display a floating preview panel at (x, y) with the rewrite result.
    on_accept is called when the user clicks Accept.
    on_dismiss is called when the user clicks Dismiss (or the widget times out).
    """
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=BORDER)

    # ── Position: appear just below and to the right of the click point ──────
    win.geometry(f"+{x + 12}+{y + 12}")

    # ── Outer frame (acts as 1-px border) ────────────────────────────────────
    outer = tk.Frame(win, bg=BORDER, padx=1, pady=1)
    outer.pack(fill="both", expand=True)

    inner = tk.Frame(outer, bg=BG_MAIN, width=PREVIEW_WIDTH)
    inner.pack(fill="both", expand=True)
    inner.pack_propagate(False)

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(inner, bg=BG_HEADER, pady=5, padx=10)
    header.pack(fill="x")

    action_label = action.replace("_", " ").title()
    tk.Label(
        header,
        text=f"✦ Xvoice  ·  {action_label}",
        bg=BG_HEADER, fg=ACCENT,
        font=FONT_HEAD, anchor="w",
    ).pack(side="left")

    # Close × button
    close_btn = tk.Label(
        header, text="×", bg=BG_HEADER, fg=TEXT_COLOR,
        font=("Segoe UI", 12), cursor="hand2", padx=6,
    )
    close_btn.pack(side="right")
    close_btn.bind("<Button-1>", lambda e: _dismiss())

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(inner, bg=BORDER, height=1).pack(fill="x")

    # ── Result text ───────────────────────────────────────────────────────────
    preview_text = result_text
    truncated = False
    if len(preview_text) > MAX_PREVIEW_CHARS:
        preview_text = preview_text[:MAX_PREVIEW_CHARS] + "…"
        truncated = True

    text_frame = tk.Frame(inner, bg=BG_MAIN, padx=12, pady=8)
    text_frame.pack(fill="x")

    # Use a Text widget so it wraps correctly
    txt = tk.Text(
        text_frame,
        bg=BG_MAIN, fg=TEXT_COLOR,
        font=FONT_BODY,
        wrap="word",
        relief="flat",
        borderwidth=0,
        padx=0, pady=0,
        height=min(8, preview_text.count("\n") + 3),
        width=52,
        cursor="arrow",
        state="normal",
    )
    txt.insert("1.0", preview_text)
    txt.config(state="disabled")
    txt.pack(fill="x")

    if truncated:
        tk.Label(
            text_frame,
            text=f"({len(result_text)} chars total — truncated for preview)",
            bg=BG_MAIN, fg="#6A6A6A",
            font=FONT_LABEL, anchor="w",
        ).pack(fill="x", pady=(2, 0))

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(inner, bg=BORDER, height=1).pack(fill="x")

    # ── Action buttons ────────────────────────────────────────────────────────
    btn_row = tk.Frame(inner, bg=BG_MAIN, padx=10, pady=8)
    btn_row.pack(fill="x")

    def _accept():
        win.destroy()
        on_accept()

    def _dismiss():
        win.destroy()
        on_dismiss()

    acc_btn = tk.Button(
        btn_row,
        text="✓  Accept",
        bg=BG_BTN_ACC, fg="white",
        activebackground="#12A011", activeforeground="white",
        font=FONT_HEAD, relief="flat", padx=14, pady=4,
        cursor="hand2", bd=0,
        command=_accept,
    )
    acc_btn.pack(side="left", padx=(0, 8))

    dis_btn = tk.Button(
        btn_row,
        text="✗  Dismiss",
        bg=BG_BTN_DIS, fg="#CCCCCC",
        activebackground="#7A2222", activeforeground="white",
        font=FONT_BODY, relief="flat", padx=14, pady=4,
        cursor="hand2", bd=0,
        command=_dismiss,
    )
    dis_btn.pack(side="left")

    # Keyboard shortcut hint
    tk.Label(
        btn_row,
        text="Enter = accept  ·  Esc = dismiss",
        bg=BG_MAIN, fg="#555555",
        font=FONT_LABEL,
    ).pack(side="right")

    # ── Keyboard bindings (if the widget gets focus) ──────────────────────────
    win.bind("<Return>", lambda e: _accept())
    win.bind("<Escape>", lambda e: _dismiss())
    win.focus_set()

    # ── Auto-dismiss after 12 seconds ─────────────────────────────────────────
    win.after(12_000, lambda: _dismiss() if win.winfo_exists() else None)
