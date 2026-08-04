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
import random
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
# A single thin line that displaces organically, not an equaliser and not a sine.
#
# The shape comes from layered value noise rather than trigonometry. Sine waves
# are the reason the previous version read as artificial: they are perfectly
# periodic, so the eye finds the repeat within a second or two no matter how the
# amplitude is modulated. Noise has no period, and three octaves drifting at
# unrelated speeds never realign — every frame genuinely differs from the last.
WAVE_COLOR    = "#FFFFFF"   # the crisp core line
GLOW_BLUE     = "#4DA3FF"   # halo around it
ISLAND_BG     = "rgba(0, 0, 0, 235)"

WAVE_WIDTH    = 148
WAVE_HEIGHT   = 34
WAVE_LINE_PX  = 2.4         # thin, with rounded caps and joins
WAVE_POINTS   = 56          # sample count along the line; higher = smoother curve

# Peak displacement from the centre line, in pixels, at full volume.
#
# Generous relative to the widget height because the three noise octaves rarely
# peak together — their sum usually lands well inside its theoretical range, so a
# value that looks right on paper draws an almost flat line in practice.
WAVE_MAX_AMP  = 26.0

# Shape of the centre-weighting. The middle moves most and the motion falls away
# towards both ends, so the eye is drawn to the centre and the line settles
# calmly into the pill instead of being cut off.
WAVE_FOCUS    = 1.7

# Glow: concentric strokes of increasing width and decreasing alpha.
GLOW_LAYERS   = 4
GLOW_SPREAD   = 2.6         # px added per layer
GLOW_ALPHA    = 46          # alpha of the innermost halo layer

# Per-state amplitude and flow speed. `amp` is a fraction of WAVE_MAX_AMP applied
# when there is no microphone signal; `flow` scales how fast the noise drifts.
WAVE_STATES = {
    #            idle amp   flow   follows mic
    "idle":       (0.07,    0.40,  False),   # awake, waiting — barely breathing
    "listening":  (0.10,    1.00,  True),    # follows the voice
    "processing": (0.26,    0.55,  False),   # thinking — calm, still moving
    "speaking":   (0.70,    1.50,  False),   # energetic but smooth
}
WAVE_DEFAULT_STATE = "listening"

# How fast the line follows the microphone. Rising is quick so a syllable lands
# immediately; falling is slower, which is what stops it flickering and reads as
# "listening" rather than "strobing".
LEVEL_ATTACK  = 0.55
LEVEL_DECAY   = 0.14

# Amplitude changes are eased too, so switching state morphs rather than jumps.
STATE_MORPH   = 0.08

# The waveform sits in its own pill at the TOP of the screen, up by the camera,
# rather than inside the caption island at the bottom.
BARS_ISLAND_RADIUS = 16
BARS_ISLAND_TOP_MARGIN = 12


class _ValueNoise:
    """Smooth 1-D value noise — the organic alternative to a sine.

    A table of random values, read with a smoothstep between neighbours. Sampling
    it at a moving offset gives a wandering curve with no period and no corners,
    which is exactly what a sine cannot do however it is modulated.
    """

    __slots__ = ("_t", "_n")

    def __init__(self, size: int = 512, seed: int = 0x5EED):
        rng = random.Random(seed)
        self._t = [rng.uniform(-1.0, 1.0) for _ in range(size)]
        self._n = size

    def at(self, x: float) -> float:
        i = int(x)
        f = x - i
        a = self._t[i % self._n]
        b = self._t[(i + 1) % self._n]
        f = f * f * (3.0 - 2.0 * f)          # smoothstep: C1-continuous, no kinks
        return a + (b - a) * f


# Three octaves, each with its own spatial frequency, drift speed and table.
# The speeds are deliberately not simple ratios of one another, so the octaves
# never realign and the combined shape does not visibly loop.
_OCTAVES = (
    # (noise, spatial freq, drift speed, weight)
    (_ValueNoise(seed=0xA11CE), 0.85, 0.37, 0.58),
    (_ValueNoise(seed=0xB0B),   1.90, 0.61, 0.28),
    (_ValueNoise(seed=0xC0FFEE), 4.10, 0.94, 0.14),
)


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

    class WaveformWidget(QWidget):
        """A single organic line that reacts to the voice.

        Deliberately NOT a sine. A sine is perfectly periodic, so however its
        amplitude is modulated the eye finds the repeat within a second or two —
        that is what made the previous version read as mechanical. The shape here
        comes from three octaves of value noise drifting at unrelated speeds, so
        it never realigns and no two frames are the same.

        Displacement is weighted towards the centre, so the middle moves most and
        the motion falls away smoothly towards both ends. Stroked as one thin
        polyline with round caps and joins, under a soft blue halo — no fill, no
        bars, nothing that reads as an equaliser.
        """

        def __init__(self):
            super().__init__(None)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedSize(WAVE_WIDTH, WAVE_HEIGHT)
            self._level = 0.0        # smoothed mic level, 0-1
            self._amp = 0.0          # eased amplitude actually drawn
            self._time = 0.0         # noise cursor; advances, never wraps visibly
            self._state = WAVE_DEFAULT_STATE

        # ── public ───────────────────────────────────────────────────────────
        def set_state(self, state: str):
            if state in WAVE_STATES:
                self._state = state

        def set_level(self, raw: float, phase: float):
            idle_amp, flow, follows_mic = WAVE_STATES[self._state]

            # Asymmetric smoothing: jump up so a syllable registers at once, ease
            # down so the tail of a word glides instead of snapping to zero.
            k = LEVEL_ATTACK if raw > self._level else LEVEL_DECAY
            self._level += (raw - self._level) * k

            target = max(idle_amp, self._level) if follows_mic else idle_amp
            # Ease towards the target so a state change morphs instead of jumping.
            self._amp += (target - self._amp) * STATE_MORPH

            # Louder speech also flows faster, which is what makes it feel like it
            # is responding rather than merely scaling.
            self._time += (PULSE_MS / 1000.0) * flow * (1.0 + 1.2 * self._amp)
            self.update()

        # ── shape ────────────────────────────────────────────────────────────
        def _points(self):
            """Vertical offset from the centre line at each sample, in pixels."""
            n = WAVE_POINTS
            mid = self.height() / 2.0
            step = self.width() / (n - 1)
            out = []
            for i in range(n):
                t = i / (n - 1)

                # Centre focus: most movement in the middle, easing to stillness
                # at both ends. sin() is fine here — it shapes the envelope, it
                # does not generate the motion, so it cannot introduce a period.
                focus = math.sin(math.pi * t) ** WAVE_FOCUS

                d = 0.0
                for noise, freq, speed, weight in _OCTAVES:
                    d += weight * noise.at(t * freq * n * 0.25 + self._time * speed)

                out.append((i * step, mid + d * WAVE_MAX_AMP * self._amp * focus))
            return out

        def _path(self):
            from PySide6.QtGui import QPainterPath
            pts = self._points()
            path = QPainterPath()
            path.moveTo(*pts[0])
            # Quadratic through the midpoint of each pair: C1-continuous, so there
            # are no visible corners, and no overshoot on a sharp sample.
            for i in range(1, len(pts)):
                px, py = pts[i - 1]
                x, y = pts[i]
                path.quadTo((px + x) / 2.0, (py + y) / 2.0, x, y)
            return path

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(Qt.NoBrush)
            path = self._path()

            # Blue halo: the same path stroked progressively wider and fainter.
            # Cheaper than a blur effect and it tracks the line exactly.
            glow = QColor(GLOW_BLUE)
            for layer in range(GLOW_LAYERS, 0, -1):
                pen = QPen(QColor(glow.red(), glow.green(), glow.blue(),
                                  int(GLOW_ALPHA / layer)))
                pen.setWidthF(WAVE_LINE_PX + layer * GLOW_SPREAD)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.drawPath(path)

            # Crisp core on top.
            pen = QPen(QColor(WAVE_COLOR))
            pen.setWidthF(WAVE_LINE_PX)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
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

            self.wave = WaveformWidget()
            row.addWidget(self.wave)

        def set_level(self, level: float, phase: float):
            self.wave.set_level(level, phase)

        def set_state(self, state: str):
            self.wave.set_state(state)

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
        self._state = WAVE_DEFAULT_STATE
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

    def set_state(self, state: str):
        """idle | listening | processing | speaking. Safe from any thread."""
        self._state = state
        self._host.run_on_ui(self._apply_state)

    def _apply_state(self):
        if self._bars_island is not None:
            self._bars_island.set_state(self._state)

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
            self._bars_island.set_state(self._state)
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


def set_state(state: str):
    """Switch the waveform between idle / listening / processing / speaking."""
    try:
        _get().set_state(state)
    except Exception:
        pass


def show_listening():
    _get().set_state("listening")
    _get().show()


def hide_listening():
    _get().hide()
