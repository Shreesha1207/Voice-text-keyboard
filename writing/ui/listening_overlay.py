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

# ── Voice activity indicator ─────────────────────────────────────────────────
# A row of lines, each driven by its OWN frequency band. That independence is
# the whole point: an earlier version drove every bar from one overall volume,
# so the row rose and fell as a block and read as mechanical no matter how much
# per-bar wobble was added on top. With real bands a vowel lights the low lines
# and an "s" lights the high ones, and the row moves the way a voice sounds.
WAVE_COLOR    = "#FFFFFF"   # the lines themselves
GLOW_BLUE     = "#4DA3FF"   # halo around them
ISLAND_BG     = "rgba(0, 0, 0, 235)"

BAR_WIDTH     = 3.0         # thin, with rounded caps
BAR_GAP       = 5.0
BAR_MAX_H     = 26.0        # full height at peak level
BAR_MIN_H     = 3.0         # resting height — a short line, never a dot
WAVE_HEIGHT   = 32

# Bands are mirrored around the centre: the middle line is the lowest band,
# fanning out to the highest at both ends. Speech carries most of its energy low
# down, so this puts the strongest response in the middle — the natural focus —
# while every line still reports a genuine, independent frequency.
BAR_BAND_ORDER = (5, 4, 3, 2, 1, 0, 1, 2, 3, 4, 5)
BAR_COUNT      = len(BAR_BAND_ORDER)

# Glow: concentric strokes of increasing width and decreasing alpha.
GLOW_LAYERS   = 4
GLOW_SPREAD   = 2.2         # px added per layer
GLOW_ALPHA    = 44          # alpha of the innermost halo layer

# Per-state idle height and animation speed, used when there is no live audio.
WAVE_STATES = {
    #             idle amp   flow   follows mic
    "idle":       (0.26,     0.85,  False),   # awake, waiting
    "listening":  (0.16,     1.25,  True),    # follows the voice
    "processing": (0.52,     1.30,  False),   # thinking — a travelling ripple
    "speaking":   (0.85,     1.90,  False),   # energetic
}

# ── Motion ───────────────────────────────────────────────────────────────────
# A travelling sweep runs across the row at all times, its phase offset by line
# position so it moves ALONG the row rather than pulsing every line at once.
#
# It is present even while following the microphone, deliberately: band levels
# alone leave the row frozen whenever someone holds a steady vowel, which reads
# as the indicator having died mid-word. The band still sets the ceiling; the
# sweep only modulates within it.
# Crucially, the sweep is applied when DRAWING, after the level smoothing —
# not folded into the smoothed value. Smoothing exists to stop band readings
# flickering, and it has a long decay; running the animation through it damped
# the motion almost to nothing (measured at 0.06px/frame — visually frozen).
# Smoothing belongs on the data, never on the animation.
SWEEP_SPEED      = 6.5    # radians per second of flow-adjusted time
SWEEP_PER_LINE   = 0.62   # radian offset between neighbouring lines
NOISE_SPEED      = 5.0    # multiplier on the per-line noise drift
LIVE_SHIMMER     = 0.40   # sweep depth while following a live band, 0-1
IDLE_SHIMMER     = 0.85   # sweep depth with no live audio — carries all motion
WAVE_DEFAULT_STATE = "listening"

# How fast a line follows its band. Rising is quick so a syllable lands at once;
# falling is slower, which is what stops the row flickering.
LEVEL_ATTACK  = 0.62
LEVEL_DECAY   = 0.16

# Amplitude changes are eased, so switching state morphs rather than jumps.
STATE_MORPH   = 0.10

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
    from PySide6.QtCore import Qt, QTimer, QPointF
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
        """A row of lines, each following its own frequency band.

        Every line is driven by real data — the Goertzel energy of one band of
        the live microphone signal — so they move independently. That is the
        difference from the first attempt, where all five bars shared a single
        volume figure and therefore always moved together.

        Drawn as thin rounded strokes under a soft blue halo. When there is no
        live audio (idle, processing, speaking) the heights come from smooth
        noise instead, so the row keeps breathing rather than freezing.
        """

        def __init__(self):
            super().__init__(None)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedSize(
                int(BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP),
                WAVE_HEIGHT,
            )
            self._levels = [0.0] * BAR_COUNT   # smoothed band data, per line
            self._live = False                 # is a microphone feeding us?
            self._amp = 0.0                    # eased idle amplitude
            self._time = 0.0
            self._state = WAVE_DEFAULT_STATE

        # ── public ───────────────────────────────────────────────────────────
        def set_state(self, state: str):
            if state in WAVE_STATES:
                self._state = state

        def set_levels(self, bands, phase: float = 0.0):
            """`bands` is one 0-1 value per entry in audio_analysis.BAND_CENTRES."""
            idle_amp, flow, follows_mic = WAVE_STATES[self._state]
            self._time += (PULSE_MS / 1000.0) * flow
            self._amp += (idle_amp - self._amp) * STATE_MORPH

            self._live = bool(follows_mic and bands)
            for i, band_index in enumerate(BAR_BAND_ORDER):
                # Smooth only the DATA here. The animation is applied at draw
                # time, so this long decay cannot damp it.
                target = bands[band_index % len(bands)] if self._live else self._amp
                if self._live:
                    # Never fully still between words.
                    target = max(target, self._amp)
                k = LEVEL_ATTACK if target > self._levels[i] else LEVEL_DECAY
                self._levels[i] += (target - self._levels[i]) * k
            self.update()

        # kept so existing callers that only have an overall level still work
        def set_level(self, raw: float, phase: float = 0.0):
            self.set_levels([raw] * len(_OCTAVES), phase)

        # ── shape ────────────────────────────────────────────────────────────
        def _bars(self):
            """(x, height) for each line, in pixels.

            The travelling sweep is applied here rather than to the stored level,
            so it runs at full strength regardless of how heavily the band
            readings are smoothed.
            """
            shimmer = LIVE_SHIMMER if self._live else IDLE_SHIMMER
            out = []
            for i, lvl in enumerate(self._levels):
                # Phase offset per line is what makes the motion travel ALONG the
                # row instead of every line breathing in unison.
                sweep = 0.5 + 0.5 * math.sin(
                    self._time * SWEEP_SPEED - i * SWEEP_PER_LINE
                )
                # Per-line noise, so it is not a clean marching sine.
                noise, _freq, speed, _weight = _OCTAVES[i % len(_OCTAVES)]
                wander = abs(noise.at(i * 1.7 + self._time * speed * NOISE_SPEED))

                anim = (1.0 - shimmer) + shimmer * (0.65 * sweep + 0.35 * wander)
                h = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * max(0.0, min(1.0, lvl)) * anim
                out.append((i * (BAR_WIDTH + BAR_GAP) + BAR_WIDTH / 2.0,
                            max(BAR_MIN_H, min(BAR_MAX_H, h))))
            return out

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(Qt.NoBrush)
            mid = self.height() / 2.0
            bars = self._bars()

            def stroke(color, width):
                pen = QPen(color)
                pen.setWidthF(width)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                for x, h in bars:
                    p.drawLine(QPointF(x, mid - h / 2.0), QPointF(x, mid + h / 2.0))

            glow = QColor(GLOW_BLUE)
            for layer in range(GLOW_LAYERS, 0, -1):
                stroke(QColor(glow.red(), glow.green(), glow.blue(),
                              int(GLOW_ALPHA / layer)),
                       BAR_WIDTH + layer * GLOW_SPREAD)

            stroke(QColor(WAVE_COLOR), BAR_WIDTH)
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

        def set_levels(self, bands, phase: float):
            self.wave.set_levels(bands, phase)

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
        self._bands = None

    # ── Public, thread-safe ──────────────────────────────────────────────────
    def show(self):
        self._host.run_on_ui(self._show_on_ui)

    def hide(self):
        self._host.run_on_ui(self._hide_on_ui)

    def set_levels(self, bands):
        """Report per-band microphone energy. Called from the audio thread.

        A whole-list assignment, deliberately not a lock: this runs once per
        30ms audio chunk inside the recording loop and nothing about the
        recording may ever wait on the UI. Rebinding a name is atomic in
        CPython, so the Qt thread either sees the old list or the new one,
        never a half-written one.
        """
        self._bands = bands

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
            self._bands = None
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

        # Each line follows its own frequency band.
        if self._bars_island is not None:
            self._bars_island.set_levels(self._bands, self._phase)


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


def set_levels(bands):
    """Report per-band microphone energy so each line reacts to its own band."""
    try:
        _get().set_levels(bands)
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
