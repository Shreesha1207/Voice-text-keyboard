"""
Xvoice Writing action picker (PySide6).

show_action_menu(x, y, on_action) shows the floating action menu and returns the
widget. on_action(action_key, target_language) fires when an item is chosen;
target_language is None for everything except translate.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer

from writing.ui.qt_helpers import (
    FramelessPopup, BG_COLOR, TEXT_COLOR, HOVER_COLOR, FONT_FAMILY,
)

# (display_label, action_key, has_language_sub_menu)
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

_MENU_TIMEOUT_MS = 6000

_TITLE_QSS = (
    f"color: {TEXT_COLOR}; font-family: '{FONT_FAMILY}'; "
    f"font-size: 12px; font-weight: 600; padding: 6px 12px 4px 12px;"
)
_ROW_QSS = f"""
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


def _title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_TITLE_QSS)
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {TEXT_COLOR}; border: none; margin: 2px 6px;")
    return line


def _row(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(_ROW_QSS)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setMinimumWidth(210)
    return btn


def show_action_menu(
    x: int,
    y: int,
    on_action: Callable[[str, Optional[str]], None],
) -> FramelessPopup:
    menu = FramelessPopup(radius=12)
    menu.body.addWidget(_title("✦ Xvoice Writing"))
    menu.body.addWidget(_separator())

    def open_language_sub():
        menu.close()
        sub = FramelessPopup(radius=12)
        sub.body.addWidget(_title("🌐  Translate to…"))
        sub.body.addWidget(_separator())
        for lang in LANGUAGES:
            row = _row(lang)
            row.clicked.connect(
                lambda _=False, l=lang: (sub.close(), on_action("translate", l))
            )
            sub.body.addWidget(row)
        sub.show_at(x, y + 35)
        QTimer.singleShot(_MENU_TIMEOUT_MS, sub.close)

    for label, action_key, needs_lang in ACTIONS:
        row = _row(label + ("   ▶" if needs_lang else ""))
        if needs_lang:
            row.clicked.connect(lambda _=False: open_language_sub())
        else:
            row.clicked.connect(
                lambda _=False, ak=action_key: (menu.close(), on_action(ak, None))
            )
        menu.body.addWidget(row)

    menu.show_at(x, y + 35)
    QTimer.singleShot(_MENU_TIMEOUT_MS, menu.close)
    return menu
