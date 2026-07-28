#!/usr/bin/env python3
"""Generate mac/xvoice.icns — the app / Dock / Finder icon for Xvoice.app.

The artwork is the same purple microphone drawn by ``_make_icon_image()`` in
main.py (and by the AppImage step in .github/workflows/build.yml), just
rendered at the sizes macOS expects.  Nothing here imports main.py, so the
app logic stays untouched.

Usage:
    python3 mac/make_icns.py            # writes mac/xvoice.icns
    python3 mac/make_icns.py out.icns

Requires Pillow.  ``iconutil`` (built into macOS) is used to pack the
.iconset; on a non-macOS machine the script falls back to writing a
multi-resolution .icns via Pillow so the file can still be produced.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

# macOS iconset contract: (filename, pixel size)
ICONSET = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

PURPLE = (124, 58, 237, 255)


def draw_icon(size: int) -> Image.Image:
    """Draw the Xvoice microphone at ``size``x``size`` pixels.

    Geometry is expressed as fractions of a 64px reference so every size is
    the same picture rather than a blurry upscale.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64.0

    def px(*vals):
        return [v * s for v in vals]

    # Purple background circle
    d.ellipse(px(0, 0, 63, 63), fill=PURPLE)
    # Microphone body
    d.rounded_rectangle(px(22, 10, 42, 38), radius=10 * s, fill="white")
    # Mic stand arc
    d.arc(px(14, 24, 50, 52), start=0, end=180, fill="white",
          width=max(1, round(4 * s)))
    # Stand post + base
    d.line(px(32, 52, 32, 60), fill="white", width=max(1, round(4 * s)))
    d.line(px(24, 60, 40, 60), fill="white", width=max(1, round(4 * s)))
    return img


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "xvoice.icns")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="xvoice-icon-")
    iconset = os.path.join(tmp, "xvoice.iconset")
    os.makedirs(iconset, exist_ok=True)
    try:
        for name, size in ICONSET:
            draw_icon(size).save(os.path.join(iconset, name), format="PNG")

        iconutil = shutil.which("iconutil")
        if iconutil:
            subprocess.run(
                [iconutil, "-c", "icns", iconset, "-o", out],
                check=True,
            )
        else:
            # Not on macOS — Pillow can still emit a valid multi-size .icns
            print("iconutil not found; writing .icns with Pillow instead.")
            draw_icon(1024).save(
                out,
                format="ICNS",
                sizes=[(s, s) for s in (16, 32, 64, 128, 256, 512, 1024)],
            )

        print(f"Wrote {out}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
