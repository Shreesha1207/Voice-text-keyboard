"""
Xvoice Writing action picker (PySide6).

show_action_menu(x, y, on_action) shows the floating action menu and returns the
widget. on_action(action_key, target_language) fires when an item is chosen;
target_language is passed automatically for translate based on user settings.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QPoint

from writing.ui.qt_helpers import (
    FramelessPopup, BG_COLOR, TEXT_COLOR, HOVER_COLOR, FONT_FAMILY,
)
from writing.ui.language_menu import show_language_menu

ACTIONS = [
    ("✨  Improve writing",   "improve"),
    ("💼  Professional tone", "professional"),
    ("📋  Make it shorter",   "shorten"),
    ("🌐  Translate  ▸",      "translate"),
    ("📝  Fix grammar",       "fix_grammar"),
    ("📊  Summarize",         "summarise"),
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
    default_lang: Optional[str] = None,
) -> FramelessPopup:
    menu = FramelessPopup(radius=12)
    menu.body.addWidget(_title("✦ Xvoice Writing"))
    menu.body.addWidget(_separator())

    sub_menu: Optional[FramelessPopup] = None

    def close_all():
        nonlocal sub_menu
        if sub_menu is not None:
            try:
                sub_menu.close()
            except Exception:
                pass
            sub_menu = None
        try:
            menu.close()
        except Exception:
            pass

    menu.destroyed.connect(lambda: sub_menu.close() if sub_menu else None)

    for label, action_key in ACTIONS:
        row = _row(label)

        if action_key == "translate":
            def open_translate_submenu(_=False, btn=row):
                nonlocal sub_menu
                if sub_menu is not None:
                    try:
                        sub_menu.close()
                    except Exception:
                        pass
                    sub_menu = None
                    return

                global_pos = btn.mapToGlobal(QPoint(btn.width() + 10, -5))
                flyout_x = global_pos.x()
                flyout_y = global_pos.y()

                def on_lang_select(lang: str):
                    close_all()
                    on_action("translate", lang)

                sub_menu = show_language_menu(
                    flyout_x,
                    flyout_y,
                    on_lang_select,
                    custom_lang=default_lang,
                )

            row.clicked.connect(open_translate_submenu)
        else:
            def make_action_callback(ak=action_key):
                def callback(_=False):
                    close_all()
                    on_action(ak, None)
                return callback

            row.clicked.connect(make_action_callback())

        menu.body.addWidget(row)

    menu.show_at(x, y + 35)
    QTimer.singleShot(_MENU_TIMEOUT_MS, close_all)
    return menu
