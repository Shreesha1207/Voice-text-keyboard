"""Split a chunk of microphone audio into frequency bands.

This is what makes the visualiser look alive rather than mechanical. Driving
every bar from one overall volume — which is what the first attempt did — makes
the whole row rise and fall as a block, and no amount of per-bar wobble hides
that, because the bars carry no independent information. Splitting the signal
into bands gives each bar its own data: a vowel lights the low bands, an "s" or
a "t" lights the high ones, and the row moves the way a voice actually sounds.

Uses the Goertzel algorithm rather than an FFT. Goertzel evaluates a single
frequency for one multiply and two adds per sample, so measuring a handful of
bands costs a fraction of a full transform — and, more to the point, needs no
numpy. Adding numpy to a frozen desktop app for a decoration would be tens of
megabytes for something a hundred lines of arithmetic does well enough.
"""
from __future__ import annotations

import array
import math
import sys

# Band edges in Hz, spaced roughly logarithmically across the range that carries
# speech. Below ~90Hz is mostly rumble; the top band is sibilance, which is worth
# having because it is visually distinct — an "s" lights it and nothing else.
BAND_EDGES = (
    (90.0, 200.0),
    (200.0, 420.0),
    (420.0, 850.0),
    (850.0, 1600.0),
    (1600.0, 2600.0),
    (2600.0, 3900.0),
)

# Probes per band.
#
# A single Goertzel is a very narrow filter — its bin is (rate/decimate)/samples
# wide, about 33Hz here — so probing one frequency per band measures a sliver of
# it and misses everything else. A 700Hz vowel formant read as near-silence at a
# 600Hz probe. Several probes spread across each band sample it properly, and at
# 0.07ms per probe-set the cost is nil next to the 30ms chunk it analyses.
PROBES_PER_BAND = 4


def _probe_frequencies():
    out = []
    for lo, hi in BAND_EDGES:
        ratio = (hi / lo) ** (1.0 / PROBES_PER_BAND)
        # Geometric spacing, offset half a step so probes sit inside the band
        # rather than on its edges where neighbouring bands would double-count.
        out.append(tuple(lo * ratio ** (k + 0.5) for k in range(PROBES_PER_BAND)))
    return tuple(out)


BAND_PROBES = _probe_frequencies()
BAND_CENTRES = tuple(math.sqrt(lo * hi) for lo, hi in BAND_EDGES)

# Take every Nth sample. Speech detail above ~4kHz does not matter here, and
# this cuts the work per chunk proportionally. No anti-alias filter: for a
# visualiser the cosmetic cost is nil and a filter would cost more than it saves.
DECIMATE = 2

# Divisor mapping raw band magnitude onto 0-1. Lower bands carry far more energy
# in ordinary speech, so a single divisor would leave the high bars permanently
# flat and the low ones permanently pinned.
BAND_REFERENCE = (2600.0, 2100.0, 1500.0, 1000.0, 700.0, 520.0)


def _samples(pcm: bytes) -> array.array:
    a = array.array("h")
    # frombytes needs a whole number of samples
    a.frombytes(pcm[: len(pcm) - (len(pcm) % a.itemsize)])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def band_levels(pcm: bytes, rate: int) -> list[float]:
    """Return one 0-1 level per band in BAND_CENTRES.

    Never raises: this runs inside the recording loop and nothing about the
    recording may depend on it. A bad chunk simply reports silence.
    """
    try:
        s = _samples(pcm)[::DECIMATE]
        n = len(s)
        if n < 8:
            return [0.0] * len(BAND_CENTRES)

        eff_rate = rate / DECIMATE
        out = []
        for probes, ref in zip(BAND_PROBES, BAND_REFERENCE):
            total = 0.0
            for freq in probes:
                # Goertzel: a two-pole resonator tuned to `freq`, run over the
                # chunk. What remains in the state variables afterwards is the
                # energy the signal carried at that frequency.
                omega = 2.0 * math.pi * freq / eff_rate
                coeff = 2.0 * math.cos(omega)
                s1 = s2 = 0.0
                for x in s:
                    s0 = x + coeff * s1 - s2
                    s2 = s1
                    s1 = s0
                power = s1 * s1 + s2 * s2 - coeff * s1 * s2
                total += math.sqrt(power if power > 0.0 else 0.0) / n
            out.append(max(0.0, min(1.0, total / ref)))
        return out
    except Exception:
        return [0.0] * len(BAND_CENTRES)
