"""
Standalone translate-language picker (PySide6).

show_language_menu(x, y, on_select) shows the menu and returns the widget.
Remembers the most recently used language in the per-user config file.
"""
from __future__ import annotations

import os
import sys
import json
from typing import Callable, Optional

from PySide6.QtWidgets import QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer

from writing.ui.qt_helpers import (
    FramelessPopup, TEXT_COLOR, HOVER_COLOR, FONT_FAMILY,
)

LANGUAGES = [
    "English", "Spanish", "French", "German",
    "Japanese", "Hindi",
]

# ── Config file for the most-recently-used language ──────────────────────────
if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Xvoice")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "Xvoice")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

_MENU_TIMEOUT_MS = 6000


def load_recent_lang() -> Optional[str]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f).get("recent_lang")
        except Exception:
            pass
    return None


def save_recent_lang(lang: str) -> None:
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    data["recent_lang"] = lang
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _title(text: str, *, italic: bool = False) -> QLabel:
    style = (
        f"color: {TEXT_COLOR}; font-family: '{FONT_FAMILY}'; font-size: 12px; "
        f"font-weight: 600; padding: 6px 12px 4px 12px;"
    )
    if italic:
        style = (
            f"color: {TEXT_COLOR}; font-family: '{FONT_FAMILY}'; font-size: 12px; "
            f"font-style: italic; padding: 5px 12px;"
        )
    lbl = QLabel(text)
    lbl.setStyleSheet(style)
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {TEXT_COLOR}; border: none; margin: 2px 6px;")
    return line


def _row(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent;
            color: {TEXT_COLOR};
            border: none;
            border-radius: 6px;
            text-align: left;
            padding: 7px 14px;
            font-family: '{FONT_FAMILY}';
            font-size: 13px;
        }}
        QPushButton:hover {{ background: {HOVER_COLOR}; }}
        """
    )
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setMinimumWidth(190)
    return btn


def show_language_menu(
    x: int,
    y: int,
    on_select: Callable[[str], None],
    custom_lang: Optional[str] = None,
) -> FramelessPopup:
    menu = FramelessPopup(radius=12)
    menu.body.addWidget(_title("🌐  Translate to..."))

    def select(lang: str) -> None:
        if "More languages" not in lang:
            save_recent_lang(lang)
            menu.close()
            on_select(lang)
        else:
            menu.close()
            import webbrowser
            try:
                from main import FRONTEND_URL
                webbrowser.open(f"{FRONTEND_URL}/writing/settings")
            except Exception:
                webbrowser.open("https://xvoicekeyboard.com/writing/settings")

    recent = load_recent_lang()
    if recent:
        row_text = f"Recent: {recent}  ✓"
        row = _row(row_text)
        row.clicked.connect(lambda _=False, l=recent: select(l))
        menu.body.addWidget(row)
        menu.body.addWidget(_separator())

    # Build the combined list of languages to show.
    #
    # custom_lang is whatever was saved as the writing default on the website. It
    # accepts a comma-separated list, so several extra languages can be added
    # there without a schema change: "Kannada, Tamil, Telugu" adds all three.
    # A single value still behaves exactly as before.
    langs_to_show = list(LANGUAGES)
    extras = [c.strip() for c in (custom_lang or "").split(",") if c.strip()]
    for extra in extras:
        if extra.lower() not in [l.lower() for l in langs_to_show]:
            langs_to_show.append(extra)

    active_lang = recent or (extras[0] if extras else None)
    for lang in langs_to_show:
        is_active = bool(active_lang and lang.lower() == active_lang.lower())
        label_text = f"{lang}  ✓" if is_active else lang
        row = _row(label_text)
        row.clicked.connect(lambda _=False, l=lang: select(l))
        menu.body.addWidget(row)

    menu.body.addWidget(_separator())
    more_row = _row("⚙  More languages...")
    more_row.clicked.connect(lambda _=False: select("More languages..."))
    menu.body.addWidget(more_row)

    menu.show_at(x, y)
    QTimer.singleShot(_MENU_TIMEOUT_MS, menu.close)
    return menu
