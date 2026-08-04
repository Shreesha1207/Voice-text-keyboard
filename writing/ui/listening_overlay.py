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

# ── Voice waveform ───────────────────────────────────────────────────────────
# A flowing wave carrying the last ~1s of speech, scrolling right to left.
WAVE_COLOR    = "#FFFFFF"   # plain white
ISLAND_BG     = "rgba(0, 0, 0, 235)"
WAVE_WIDTH    = 132
WAVE_HEIGHT   = 28
WAVE_MAX_H    = 24          # full height at peak volume
WAVE_MIN_H    = 3           # resting thickness — a slim line, never nothing
# How many samples the wave holds. At the ~25fps redraw this is roughly a second
# of speech on screen at once; fewer looks twitchy, many more looks like a smear.
WAVE_POINTS   = 34
# Fraction of the width over which the wave eases in at each end.
WAVE_EDGE_FRACTION = 0.14
# Depth of the per-sample wobble that gives the wave its texture.
WAVE_WOBBLE   = 0.30

# How fast the bars follow the microphone. Rising is quick so a syllable lands
# immediately; falling is slower, which is what stops the bars flickering and
# reads as "listening" rather than "strobing".
LEVEL_ATTACK  = 0.55
LEVEL_DECAY   = 0.14

# The bars sit in their own pill at the TOP of the screen, up by the camera,
# rather than inside the caption island at the bottom.
BARS_ISLAND_RADIUS = 16
BARS_ISLAND_TOP_MARGIN = 12


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
        """A flowing waveform that reacts to the voice.

        The five discrete bars this replaces were jumpy: every bar was driven from
        the same instantaneous level, so the whole row twitched together on each
        30ms audio chunk. Nothing carried over between frames, so it read as a
        level meter flickering rather than a voice being heard.

        This keeps a short rolling history of the level instead and draws a smooth
        curve through it, scrolling right to left. The shape you see is the last
        second or so of what you actually said, which is why it looks alive: the
        motion comes from speech travelling across the widget, not from a value
        being redrawn in place.

        Drawn as a symmetric envelope around the centre line — the top and bottom
        halves mirror each other — which is the shape a waveform is expected to
        have and what ChatGPT's and Gemini's voice modes both use.
        """

        def __init__(self):
            super().__init__(None)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedSize(WAVE_WIDTH, WAVE_HEIGHT)
            self._level = 0.0                            # smoothed 0–1
            self._history = [0.0] * WAVE_POINTS          # oldest → newest
            self._phase = 0.0

        def set_level(self, raw: float, phase: float):
            # Asymmetric smoothing: jump up so a syllable registers at once, ease
            # down so the tail of a word glides instead of snapping to zero.
            k = LEVEL_ATTACK if raw > self._level else LEVEL_DECAY
            self._level += (raw - self._level) * k
            self._phase = phase

            # Scroll the history one step. This is what makes the wave travel.
            self._history.pop(0)
            self._history.append(self._level)
            self.update()

        def _envelope(self):
            """Half-height of the wave at each sample point, in pixels."""
            n = len(self._history)
            base = WAVE_MIN_H / 2.0
            span = (WAVE_MAX_H - WAVE_MIN_H) / 2.0
            out = []
            for i, lvl in enumerate(self._history):
                # Taper only the outermost few points, so the wave eases into the
                # pill instead of being cut off. Tapering across the whole width
                # (a sine bow) collapsed every shape into the same lens and hid the
                # speech entirely — the middle must be free to follow the history.
                t = i / max(1, n - 1)
                edge = min(t, 1.0 - t) / WAVE_EDGE_FRACTION
                taper = min(1.0, edge)

                # Alternating per-sample wobble is what gives a waveform its
                # texture; without it a sustained note draws a flat-topped slab.
                # Scaled by level, so silence stays a clean straight line.
                wobble = 1.0 + WAVE_WOBBLE * math.sin(self._phase * 9.0 + i * 1.7)

                # Only the variable part tapers. The baseline is constant, so the
                # line never pinches to nothing at the ends.
                amp = base + span * lvl * wobble * taper
                out.append(max(base, min(WAVE_MAX_H / 2.0, amp)))
            return out

        def paintEvent(self, _):
            from PySide6.QtGui import QPainterPath

            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(WAVE_COLOR))

            amps = self._envelope()
            n = len(amps)
            mid = self.height() / 2.0
            step = self.width() / max(1, n - 1)

            # One closed path: along the top of the envelope, back along the
            # bottom. Filled rather than stroked, so the wave has body at volume
            # and still reads as a slim line in silence.
            path = QPainterPath()
            path.moveTo(0.0, mid - amps[0])
            for i in range(1, n):
                x = i * step
                y = mid - amps[i]
                # Quadratic through the midpoint of each pair keeps the curve
                # smooth without the overshoot a cubic spline gives on spiky data.
                px, py = (i - 1) * step, mid - amps[i - 1]
                path.quadTo((px + x) / 2.0, (py + y) / 2.0, x, y)
            for i in range(n - 1, -1, -1):
                x = i * step
                y = mid + amps[i]
                if i == n - 1:
                    path.lineTo(x, y)
                else:
                    nx, ny = (i + 1) * step, mid + amps[i + 1]
                    path.quadTo((nx + x) / 2.0, (ny + y) / 2.0, x, y)
            path.closeSubpath()
            p.drawPath(path)
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

            label = QLabel(ISLAND_TEXT)
            label.setStyleSheet(
                f"color: #F3E3D6; font-family: '{FONT_FAMILY}'; "
                f"font-size: 14px; font-weight: 600; background: transparent;"
            )
            row.addWidget(label)

    class BarsIslandWidget(QWidget):
        """The voice bars in a pill of their own, pinned to the top of the screen.

        Separate window rather than part of the caption island, because the two
        sit at opposite edges: this one lives up by the camera the way Siri and
        the Dynamic Island do, while the caption stays at the bottom.
        """

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
            self.pill.setObjectName("barsisland")
            self.pill.setStyleSheet(
                f"""
                #barsisland {{
                    background-color: {ISLAND_BG};
                    border-radius: {BARS_ISLAND_RADIUS}px;
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
            row.setContentsMargins(18, 8, 18, 8)
            row.setSpacing(0)

            self.bars = BarsWidget()
            row.addWidget(self.bars)

        def set_level(self, level: float, phase: float):
            self.bars.set_level(level, phase)

    return GlowWidget, IslandWidget, BarsIslandWidget, QTimer, QGuiApplication, Qt


class ListeningOverlay:
    """Manages the glow + island. Thread-safe show()/hide()."""

    def __init__(self):
        self._host = QtHost.instance()
        self._glow = None
        self._island = None
        self._bars_island = None
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
        GlowWidget, IslandWidget, BarsIsland, QTimer, QGuiApplication, Qt = _make_classes()
        self._Qt = Qt
        self._QGuiApplication = QGuiApplication

        self._glow = GlowWidget()
        self._island = IslandWidget()
        self._bars_island = BarsIsland()

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

            # Bars island: top-centre, up by the camera. Uses the full screen
            # geometry rather than availableGeometry so it sits at the true top
            # edge even when the taskbar is docked there.
            self._bars_island.adjustSize()
            bw = self._bars_island.width()
            self._bars_island.move(
                geo.x() + (geo.width() - bw) // 2,
                geo.y() + BARS_ISLAND_TOP_MARGIN,
            )
            self._bars_island.show()
            self._bars_island.raise_()

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
            if self._bars_island is not None:
                self._bars_island.hide()
        except Exception as e:
            logger.error(f"Listening overlay hide failed: {e}")

    def _tick(self):
        self._phase += PULSE_MS / 1000.0

        # The screen-edge glow keeps its slow independent breathe. Tying it to the
        # voice as well made the whole screen flicker while talking.
        intensity = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 2.2))
        if self._glow is not None:
            self._glow.set_intensity(intensity)

        # The bars island follows the microphone.
        if self._bars_island is not None:
            self._bars_island.set_level(self._level, self._phase)


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
