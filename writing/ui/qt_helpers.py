"""
Shared PySide6 helpers for the Xvoice Writing overlay UI.

Provides the brand palette and a polished frameless-popup base class used by the
floating button, action menu, language menu, preview widget and toasts.

Design goals:
  • Frameless, always-on-top, rounded-corner panels with a soft drop shadow.
  • Never steal keyboard focus from the app the user is writing in — the overlay
    sits on top of another window whose text we are about to replace, so
    activating our own window would break the selection/replace flow.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QGuiApplication

# ── Brand palette ────────────────────────────────────────────────────────────
BG_COLOR     = "#2B1B17"   # deep espresso
TEXT_COLOR   = "#E8B89C"   # warm sand
HOVER_COLOR  = "#3B2620"   # slightly lifted row hover
DIM_COLOR    = "#6B4A3A"   # muted secondary text
BORDER_RGBA  = "rgba(232, 184, 156, 0.22)"   # hairline border from TEXT_COLOR

FONT_FAMILY  = "Segoe UI"

# Margin (px) reserved around the container so the drop shadow has room to draw.
_SHADOW_MARGIN = 14

# Keeps every live popup referenced so Qt/Python GC can't collect a window that
# has no explicit owner (unparented top-level widgets otherwise vanish).
_LIVE_POPUPS: set = set()


def screen_geometry_at(x: int, y: int):
    """Return the QRect of the screen containing point (x, y) (primary as fallback)."""
    screen = QGuiApplication.screenAt(QPoint(int(x), int(y))) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


class FramelessPopup(QWidget):
    """A rounded, shadowed, top-most popup that does not steal focus.

    Subclasses/creators add their content to ``self.body`` (a QVBoxLayout that
    lives inside the rounded container).
    """

    def __init__(self, radius: int = 12, bg: str = BG_COLOR,
                 border_rgba: str = BORDER_RGBA):
        super().__init__(None)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                       # keep off the taskbar
            | Qt.NoDropShadowWindowHint     # we draw our own soft shadow
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.NoFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN
        )

        self.container = QFrame(self)
        self.container.setObjectName("popupContainer")
        self.container.setStyleSheet(
            f"""
            #popupContainer {{
                background-color: {bg};
                border-radius: {radius}px;
                border: 1px solid {border_rgba};
            }}
            """
        )
        outer.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setColor(QColor(0, 0, 0, 170))
        shadow.setOffset(0, 5)
        self.container.setGraphicsEffect(shadow)

        # Content layout for creators to populate.
        self.body = QVBoxLayout(self.container)
        self.body.setContentsMargins(6, 6, 6, 6)
        self.body.setSpacing(0)

        # Prevent premature garbage collection of this unparented top-level widget.
        _LIVE_POPUPS.add(self)
        self.destroyed.connect(lambda *_: _LIVE_POPUPS.discard(self))

    # ── Positioning ──────────────────────────────────────────────────────────
    def show_at(self, x: int, y: int, *, clamp: bool = True):
        """Move so the *visible container* sits at (x, y) and show without activating."""
        self.adjustSize()
        px, py = int(x) - _SHADOW_MARGIN, int(y) - _SHADOW_MARGIN

        if clamp:
            rect = screen_geometry_at(x, y)
            w, h = self.width(), self.height()
            if px + w > rect.right():
                px = rect.right() - w
            if py + h > rect.bottom():
                py = rect.bottom() - h
            px = max(px, rect.left() - _SHADOW_MARGIN)
            py = max(py, rect.top() - _SHADOW_MARGIN)

        self.move(px, py)
        self.show()
        self.raise_()

    def contains_global(self, x: int, y: int) -> bool:
        """True if global point (x, y) falls inside the visible container."""
        top_left = self.container.mapToGlobal(QPoint(0, 0))
        return (
            top_left.x() <= x <= top_left.x() + self.container.width()
            and top_left.y() <= y <= top_left.y() + self.container.height()
        )
