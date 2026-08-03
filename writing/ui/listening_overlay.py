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
GLOW_COLOR = "#CCCCCC"
# ─────────────────────────────────────────────────────────────────────────────

ISLAND_TEXT   = "Xvoice is listening"
GLOW_BAND_PX  = 46      # thickness of the edge glow band
GLOW_MAX_A    = 130     # peak alpha of the glow (0–255)
PULSE_MS      = 40      # animation tick (~25 fps)
FONT_FAMILY   = "Segoe UI"

# ── Voice bars ───────────────────────────────────────────────────────────────
# The island shows live bars that move with the voice instead of a static dot.
BAR_COLOR     = "#FFFFFF"   # plain white
ISLAND_BG     = "rgba(0, 0, 0, 235)"
BAR_COUNT     = 5
BAR_WIDTH     = 5
BAR_GAP       = 5
BAR_MAX_H     = 26
BAR_MIN_H     = 10          # resting height — must stay clearly a line, not a dot
                            # (at BAR_WIDTH the rounded ends meet and it reads as a
                            #  circle, so keep this comfortably above BAR_WIDTH)

# How fast the bars follow the microphone. Rising is quick so a syllable lands
# immediately; falling is slower, which is what stops the bars flickering and
# reads as "listening" rather than "strobing".
LEVEL_ATTACK  = 0.55
LEVEL_DECAY   = 0.14


def _make_classes():
    """Build the QWidget subclasses lazily (needs QtWidgets imported)."""
    from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsDropShadowEffect
    from PySide6.QtCore import Qt, QTimer, QRectF
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

    class BarsWidget(QWidget):
        """Row of vertical bars whose heights follow the microphone level.

        Each bar carries its own phase offset, so at a steady volume they ripple
        rather than moving as one block — that is what makes it read as reacting
        to a voice instead of a level meter. Silence settles to short flat lines.
        """

        def __init__(self):
            super().__init__(None)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedSize(
                BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP,
                BAR_MAX_H,
            )
            self._level = 0.0     # smoothed 0–1
            self._phase = 0.0

        def set_level(self, raw: float, phase: float):
            # Asymmetric smoothing: jump up, ease down.
            k = LEVEL_ATTACK if raw > self._level else LEVEL_DECAY
            self._level += (raw - self._level) * k
            self._phase = phase
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(BAR_COLOR))

            mid = self.height() / 2.0
            for i in range(BAR_COUNT):
                # Offset each bar around the row so the movement travels across it.
                wobble = 0.65 + 0.35 * math.sin(self._phase * 6.0 + i * 1.1)
                h = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * self._level * wobble
                h = max(BAR_MIN_H, min(BAR_MAX_H, h))
                x = i * (BAR_WIDTH + BAR_GAP)
                p.drawRoundedRect(
                    QRectF(x, mid - h / 2.0, BAR_WIDTH, h),
                    BAR_WIDTH / 2.0, BAR_WIDTH / 2.0,
                )
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
                    background-color: {ISLAND_BG};
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

            # The static dot and "Xvoice is listening" caption are replaced by bars
            # that move with the voice: it says the same thing, and also shows the
            # microphone is genuinely picking you up.
            self.bars = BarsWidget()
            row.addWidget(self.bars)

            label = QLabel(ISLAND_TEXT)
            label.setStyleSheet(
                f"color: #F3E3D6; font-family: '{FONT_FAMILY}'; "
                f"font-size: 14px; font-weight: 600; background: transparent;"
            )
            row.addWidget(label)

        def set_level(self, level: float, phase: float):
            self.bars.set_level(level, phase)

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
        # Written by the audio thread, read by the Qt thread. A bare float
        # assignment is atomic in CPython, and the worst case of a torn read here
        # would be one frame drawn at the previous level — deliberately NOT a lock,
        # because this is touched once per 30ms audio chunk inside the recording
        # loop and nothing about the recording may ever wait on the UI.
        self._level = 0.0

    # ── Public, thread-safe ──────────────────────────────────────────────────
    def show(self):
        self._host.run_on_ui(self._show_on_ui)

    def hide(self):
        self._host.run_on_ui(self._hide_on_ui)

    def set_level(self, level: float):
        """Report current microphone loudness, 0–1. Called from the audio thread."""
        self._level = level

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
            geo = screen.geometry()             # full screen — glow hugs true edges
            avail = screen.availableGeometry()  # excludes taskbar — island sits above it

            self._glow.setGeometry(geo)
            self._glow.show()
            self._glow.raise_()

            self._island.adjustSize()
            iw, ih = self._island.width(), self._island.height()
            self._island.move(
                avail.x() + (avail.width() - iw) // 2,
                avail.y() + avail.height() - ih - 24,   # bottom-center, above the taskbar
            )
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
            # Reset, or the next recording opens with the bars still at the volume
            # of the last word of the previous one.
            self._level = 0.0
            if self._timer is not None:
                self._timer.stop()
            if self._glow is not None:
                self._glow.hide()
            if self._island is not None:
                self._island.hide()
        except Exception as e:
            logger.error(f"Listening overlay hide failed: {e}")

    def _tick(self):
        self._phase += PULSE_MS / 1000.0

        # The screen-edge glow keeps its slow independent breathe. Tying it to the
        # voice as well made the whole screen flicker while talking.
        intensity = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 2.2))
        if self._glow is not None:
            self._glow.set_intensity(intensity)

        # The island bars follow the microphone.
        if self._island is not None:
            self._island.set_level(self._level, self._phase)


# Module-level singleton + convenience wrappers used by main.py.
_overlay: "ListeningOverlay | None" = None


def _get() -> ListeningOverlay:
    global _overlay
    if _overlay is None:
        _overlay = ListeningOverlay()
    return _overlay


def prewarm():
    """Build the Qt host and the overlay widgets before they are first needed.

    Everything here is lazy: the first show_listening() had to start the
    QApplication thread, import the Qt widget stack, construct both windows and
    run the first paint of a 46-layer glow. In a frozen build that is a visible
    lag — so the glow trailed the microphone badly on the very first dictation
    and was near-instant on every one afterwards.

    Doing it once at startup makes the timing consistent, which is the point: the
    glow is meant to track the microphone, not the state of Qt's import cache.
    Nothing is shown here — the widgets are built hidden.
    """
    try:
        ov = _get()
        ov._host.run_on_ui(ov._ensure_built)
    except Exception as e:
        logger.error(f"Listening overlay prewarm failed: {e}")


def set_level(level: float):
    """Report microphone loudness (0–1) so the island bars react to the voice."""
    try:
        _get().set_level(level)
    except Exception:
        pass


def show_listening():
    _get().show()


def hide_listening():
    _get().hide()
