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
    "Japanese", "Hindi", "More...",
]

# ── Config file for the most-recently-used language ──────────────────────────
if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Xvoice")
elif sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Xvoice")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "Xvoice")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

_MENU_TIMEOUT_MS = 5000


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
    btn.setMinimumWidth(180)
    return btn


def show_language_menu(
    x: int,
    y: int,
    on_select: Callable[[str], None],
) -> FramelessPopup:
    menu = FramelessPopup(radius=12)
    menu.body.addWidget(_title("Translate"))

    def select(lang: str) -> None:
        if lang != "More...":
            save_recent_lang(lang)
        menu.close()
        if lang != "More...":
            on_select(lang)

    recent = load_recent_lang()
    if recent and recent in LANGUAGES and recent != "More...":
        row = _row(f"Recent: {recent}")
        row.clicked.connect(lambda _=False, l=recent: select(l))
        menu.body.addWidget(row)
        menu.body.addWidget(_separator())

    for lang in LANGUAGES:
        row = _row(lang)
        row.clicked.connect(lambda _=False, l=lang: select(l))
        menu.body.addWidget(row)

    menu.show_at(x, y + 35)
    QTimer.singleShot(_MENU_TIMEOUT_MS, menu.close)
    return menu
