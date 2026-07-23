"""
VS Code-style inline preview widget for the Xvoice Writing Engine (PySide6).

Shows the rewritten text in a floating panel beside the cursor with
Accept / Dismiss buttons. Accept replaces the original selection; Dismiss (or a
12-second timeout) closes without changing anything.

show_preview_widget(x, y, action, original_text, result_text, on_accept, on_dismiss)
returns the widget.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QLabel, QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut

from writing.ui.qt_helpers import FramelessPopup, FONT_FAMILY

# ── VS Code-ish palette ──────────────────────────────────────────────────────
BG_MAIN    = "#1E1E1E"
BG_HEADER  = "#252526"
BG_BTN_ACC = "#0E7A0D"
BG_BTN_DIS = "#5A1A1A"
TEXT_COLOR = "#D4D4D4"
ACCENT     = "#569CD6"
BORDER     = "#3C3C3C"

MAX_PREVIEW_CHARS = 400
PREVIEW_WIDTH     = 430
_TIMEOUT_MS       = 12_000


def show_preview_widget(
    x: int,
    y: int,
    action: str,
    original_text: str,
    result_text: str,
    on_accept: Callable[[], None],
    on_dismiss: Callable[[], None],
) -> FramelessPopup:
    win = FramelessPopup(radius=10, bg=BG_MAIN, border_rgba=BORDER)
    win.container.setFixedWidth(PREVIEW_WIDTH)
    win.body.setContentsMargins(0, 0, 0, 0)

    done = {"flag": False}

    def _accept():
        if done["flag"]:
            return
        done["flag"] = True
        win.close()
        on_accept()

    def _dismiss():
        if done["flag"]:
            return
        done["flag"] = True
        win.close()
        on_dismiss()

    # ── Header ────────────────────────────────────────────────────────────────
    header = QFrame()
    header.setStyleSheet(f"background-color: {BG_HEADER}; border-top-left-radius: 10px; border-top-right-radius: 10px;")
    hl = QHBoxLayout(header)
    hl.setContentsMargins(12, 7, 8, 7)

    action_label = action.replace("_", " ").title()
    title = QLabel(f"✦ Xvoice  ·  {action_label}")
    title.setStyleSheet(
        f"color: {ACCENT}; font-family: '{FONT_FAMILY}'; font-size: 12px; font-weight: 600;"
    )
    hl.addWidget(title)
    hl.addStretch(1)

    close_btn = QPushButton("×")
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setFocusPolicy(Qt.NoFocus)
    close_btn.setFixedSize(24, 24)
    close_btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent; color: {TEXT_COLOR};
            border: none; border-radius: 12px; font-size: 16px;
        }}
        QPushButton:hover {{ background: #3A3A3A; color: white; }}
        """
    )
    close_btn.clicked.connect(lambda _=False: _dismiss())
    hl.addWidget(close_btn)
    win.body.addWidget(header)

    win.body.addWidget(_hline())

    # ── Result text ───────────────────────────────────────────────────────────
    preview_text = result_text
    truncated = len(preview_text) > MAX_PREVIEW_CHARS
    if truncated:
        preview_text = preview_text[:MAX_PREVIEW_CHARS] + "…"

    text_wrap = QWidget()
    tw = QVBoxLayout(text_wrap)
    tw.setContentsMargins(12, 10, 12, 8)
    tw.setSpacing(4)

    txt = QTextEdit()
    txt.setPlainText(preview_text)
    txt.setReadOnly(True)
    txt.setFrameShape(QFrame.NoFrame)
    txt.setFocusPolicy(Qt.NoFocus)
    txt.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    txt.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    txt.setStyleSheet(
        f"QTextEdit {{ background: {BG_MAIN}; color: {TEXT_COLOR}; "
        f"font-family: '{FONT_FAMILY}'; font-size: 13px; border: none; }}"
    )
    lines = preview_text.count("\n") + 3
    txt.setFixedHeight(min(max(lines, 3), 9) * 20)
    tw.addWidget(txt)

    if truncated:
        note = QLabel(f"({len(result_text)} chars total — truncated for preview)")
        note.setStyleSheet(f"color: #6A6A6A; font-family: '{FONT_FAMILY}'; font-size: 11px;")
        tw.addWidget(note)

    win.body.addWidget(text_wrap)
    win.body.addWidget(_hline())

    # ── Action buttons ────────────────────────────────────────────────────────
    btn_row = QWidget()
    br = QHBoxLayout(btn_row)
    br.setContentsMargins(10, 8, 12, 10)
    br.setSpacing(8)

    acc = QPushButton("✓  Accept")
    acc.setCursor(Qt.PointingHandCursor)
    acc.setFocusPolicy(Qt.NoFocus)
    acc.setStyleSheet(
        f"""
        QPushButton {{
            background: {BG_BTN_ACC}; color: white; border: none; border-radius: 6px;
            font-family: '{FONT_FAMILY}'; font-size: 12px; font-weight: 600; padding: 6px 16px;
        }}
        QPushButton:hover {{ background: #12A011; }}
        """
    )
    acc.clicked.connect(lambda _=False: _accept())
    br.addWidget(acc)

    dis = QPushButton("✗  Dismiss")
    dis.setCursor(Qt.PointingHandCursor)
    dis.setFocusPolicy(Qt.NoFocus)
    dis.setStyleSheet(
        f"""
        QPushButton {{
            background: {BG_BTN_DIS}; color: #CCCCCC; border: none; border-radius: 6px;
            font-family: '{FONT_FAMILY}'; font-size: 12px; padding: 6px 16px;
        }}
        QPushButton:hover {{ background: #7A2222; color: white; }}
        """
    )
    dis.clicked.connect(lambda _=False: _dismiss())
    br.addWidget(dis)
    br.addStretch(1)

    hint = QLabel("Enter = accept  ·  Esc = dismiss")
    hint.setStyleSheet(f"color: #555555; font-family: '{FONT_FAMILY}'; font-size: 11px;")
    br.addWidget(hint)

    win.body.addWidget(btn_row)

    # ── Keyboard shortcuts (work even without activating the window) ───────────
    QShortcut(QKeySequence(Qt.Key_Return), win, activated=_accept)
    QShortcut(QKeySequence(Qt.Key_Enter), win, activated=_accept)
    QShortcut(QKeySequence(Qt.Key_Escape), win, activated=_dismiss)

    win.show_at(x + 12, y + 12)
    QTimer.singleShot(_TIMEOUT_MS, _dismiss)
    return win


def _hline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {BORDER}; border: none;")
    return line
