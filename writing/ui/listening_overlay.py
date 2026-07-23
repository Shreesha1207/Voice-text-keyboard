"""
"Xvoice is listening" overlay (PySide6).

Displayed while the desktop is actively recording dictation. Two pieces:
  • A soft, pulsing glow hugging the edges of the screen (Antigravity-style).
  • A small floating "island" pill that reads "Xvoice is listening".

The window is click-through (Qt.WindowTransparentForInput) so it never gets in
the way of whatever the user is dictating into. All widget work runs on the
shared QtHost thread; show()/hide() are safe to call from any thread.
"""
from __future__ import annotations

import math
import logging

from writing.ui.qt_host import QtHost

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  ▶▶ CHANGE THIS to set the glow / island accent color (any hex). ◀◀
#     Default: brand warm sand, matching the rest of the Xvoice overlay.
GLOW_COLOR = "#E8B89C"
# ─────────────────────────────────────────────────────────────────────────────

ISLAND_TEXT   = "Xvoice is listening"
GLOW_BAND_PX  = 46      # thickness of the edge glow band
GLOW_MAX_A    = 130     # peak alpha of the glow (0–255)
PULSE_MS      = 40      # animation tick (~25 fps)
FONT_FAMILY   = "Segoe UI"


def _make_classes():
    """Build the QWidget subclasses lazily (needs QtWidgets imported)."""
    from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsDropShadowEffect
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPainter, QColor, QPen, QGuiApplication

    class GlowWidget(QWidget):
        def __init__(self):
            super().__init__(None)
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
                | Qt.WindowTransparentForInput      # fully click-through
                | Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._intensity = 1.0

        def set_intensity(self, value: float):
            self._intensity = value
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            base = QColor(GLOW_COLOR)
            rect = self.rect()
            layers = GLOW_BAND_PX
            for i in range(layers):
                frac = 1.0 - (i / layers)             # brightest at the very edge
                alpha = int(GLOW_MAX_A * (frac ** 1.6) * self._intensity)
                if alpha <= 0:
                    continue
                pen = QPen(QColor(base.red(), base.green(), base.blue(), alpha))
                pen.setWidth(2)
                p.setPen(pen)
                r = rect.adjusted(i, i, -i - 1, -i - 1)
                p.drawRoundedRect(r, 20, 20)
            p.end()

    class IslandWidget(QWidget):
        def __init__(self):
            super().__init__(None)
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
                | Qt.WindowTransparentForInput
                | Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

            outer = QHBoxLayout(self)
            outer.setContentsMargins(16, 12, 16, 12)   # room for the shadow

            from PySide6.QtWidgets import QFrame
            self.pill = QFrame(self)
            self.pill.setObjectName("island")
            self.pill.setStyleSheet(
                f"""
                #island {{
                    background-color: rgba(26, 17, 14, 235);
                    border-radius: 18px;
                    border: 1px solid {GLOW_COLOR};
                }}
                """
            )
            outer.addWidget(self.pill)

            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(28)
            shadow.setColor(QColor(GLOW_COLOR))
            shadow.setOffset(0, 0)
            self.pill.setGraphicsEffect(shadow)

            row = QHBoxLayout(self.pill)
            row.setContentsMargins(16, 8, 18, 8)
            row.setSpacing(10)

            self.dot = QLabel()
            self.dot.setFixedSize(12, 12)
            self._style_dot(1.0)
            row.addWidget(self.dot)

            label = QLabel(ISLAND_TEXT)
            label.setStyleSheet(
                f"color: #F3E3D6; font-family: '{FONT_FAMILY}'; "
                f"font-size: 14px; font-weight: 600; background: transparent;"
            )
            row.addWidget(label)

        def _style_dot(self, intensity: float):
            a = int(120 + 135 * intensity)
            self.dot.setStyleSheet(
                f"background-color: {GLOW_COLOR}; border-radius: 6px; "
                f"border: 1px solid rgba(255,255,255,{a});"
            )

        def set_intensity(self, value: float):
            self._style_dot(value)

    return GlowWidget, IslandWidget, QTimer, QGuiApplication, Qt


class ListeningOverlay:
    """Manages the glow + island. Thread-safe show()/hide()."""

    def __init__(self):
        self._host = QtHost.instance()
        self._glow = None
        self._island = None
        self._timer = None
        self._phase = 0.0
        self._visible = False

    # ── Public, thread-safe ──────────────────────────────────────────────────
    def show(self):
        self._host.run_on_ui(self._show_on_ui)

    def hide(self):
        self._host.run_on_ui(self._hide_on_ui)

    # ── Runs on the Qt thread ────────────────────────────────────────────────
    def _ensure_built(self):
        if self._glow is not None:
            return
        GlowWidget, IslandWidget, QTimer, QGuiApplication, Qt = _make_classes()
        self._Qt = Qt
        self._QGuiApplication = QGuiApplication

        self._glow = GlowWidget()
        self._island = IslandWidget()

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

    def _show_on_ui(self):
        try:
            self._ensure_built()
            screen = self._QGuiApplication.primaryScreen()
            geo = screen.geometry()

            self._glow.setGeometry(geo)
            self._glow.show()
            self._glow.raise_()

            self._island.adjustSize()
            iw = self._island.width()
            self._island.move(geo.x() + (geo.width() - iw) // 2, geo.y() + 18)
            self._island.show()
            self._island.raise_()

            self._phase = 0.0
            self._timer.start(PULSE_MS)
            self._visible = True
        except Exception as e:
            logger.error(f"Listening overlay show failed: {e}")

    def _hide_on_ui(self):
        try:
            self._visible = False
            if self._timer is not None:
                self._timer.stop()
            if self._glow is not None:
                self._glow.hide()
            if self._island is not None:
                self._island.hide()
        except Exception as e:
            logger.error(f"Listening overlay hide failed: {e}")

    def _tick(self):
        # Smooth breathe between ~0.55 and 1.0
        self._phase += PULSE_MS / 1000.0
        intensity = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 2.2))
        if self._glow is not None:
            self._glow.set_intensity(intensity)
        if self._island is not None:
            self._island.set_intensity(intensity)


# Module-level singleton + convenience wrappers used by main.py.
_overlay: "ListeningOverlay | None" = None


def _get() -> ListeningOverlay:
    global _overlay
    if _overlay is None:
        _overlay = ListeningOverlay()
    return _overlay


def show_listening():
    _get().show()


def hide_listening():
    _get().hide()
