#!/usr/bin/env bash
#
#  Xvoice Desktop App Compiler — macOS
#  The mac counterpart of build_exe.bat.  Produces dist/Xvoice.app
#
#  Usage:
#      ./build_mac.sh
#
#  Optional environment variables:
#      XVOICE_SIGN_IDENTITY   "Developer ID Application: Name (TEAMID)"
#                             Defaults to "-" (ad-hoc), which is fine for
#                             personal use but shows a Gatekeeper warning.
#      XVOICE_FFMPEG          Path to a static mac ffmpeg binary to bundle.
#      XVOICE_SKIP_DEPS=1     Skip the brew / pip install steps.
#
set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

echo "=========================================="
echo "       Xvoice Desktop App Compiler"
echo "                (macOS)"
echo "=========================================="
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] build_mac.sh must be run on macOS."
    echo "        On Windows use build_exe.bat instead."
    exit 1
fi

# Prefer an activated virtualenv, else the system python3.
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERROR] python3 not found. Install it with: brew install python3"
    exit 1
fi
echo "Using Python: $($PY -c 'import sys; print(sys.executable)')  ($($PY -V 2>&1))"
echo "Architecture: $(uname -m)"
echo

# ─────────────────────────────────────────────────────────────────────────
#  [1/7] Dependencies
# ─────────────────────────────────────────────────────────────────────────
echo "[1/7] Installing dependencies..."
if [[ "${XVOICE_SKIP_DEPS:-0}" == "1" ]]; then
    echo "  XVOICE_SKIP_DEPS=1 — skipping."
else
    if ! command -v brew >/dev/null 2>&1; then
        echo "  Homebrew not found. Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
        [[ -f /usr/local/bin/brew   ]] && eval "$(/usr/local/bin/brew shellenv)"
    fi

    # portaudio provides the headers PyAudio compiles against.
    brew list portaudio >/dev/null 2>&1 || brew install portaudio

    # PyAudio needs to find portaudio.h / libportaudio wherever brew put it.
    BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /usr/local)"
    export LDFLAGS="-L${BREW_PREFIX}/lib ${LDFLAGS:-}"
    export CPPFLAGS="-I${BREW_PREFIX}/include ${CPPFLAGS:-}"

    "$PY" -m pip install --upgrade pip

    # Install from requirements.txt so the build never drifts from the app's
    # deps (this is what previously dropped PySide6 and broke the Writing
    # engine + glow on Windows — same trap applies here).
    "$PY" -m pip install -r requirements.txt
    if [[ $? -ne 0 ]]; then
        echo
        echo "[ERROR] Dependency install failed."
        echo "        If PyAudio is the culprit: brew install portaudio, then retry."
        exit 1
    fi
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [2/7] webrtcvad metadata (PyInstaller compatibility patch)
# ─────────────────────────────────────────────────────────────────────────
echo "[2/7] Fixing webrtcvad metadata (PyInstaller compatibility patch)..."
SITEPACKAGES="$("$PY" -c "import site, sys; dirs = site.getsitepackages() + [site.getusersitepackages()]; sp = next((d for d in dirs if 'site-packages' in d), dirs[-1]); print(sp)")"
DISTINFO="$SITEPACKAGES/webrtcvad-2.0.14.dist-info"
if [[ ! -d "$DISTINFO" ]]; then
    mkdir -p "$DISTINFO"
    printf 'Metadata-Version: 2.1\nName: webrtcvad\nVersion: 2.0.14\n' > "$DISTINFO/METADATA"
    printf 'pip\n' > "$DISTINFO/INSTALLER"
    echo "  Created webrtcvad stub metadata at $DISTINFO"
else
    echo "  Stub already exists, skipping."
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [3/7] App icon
# ─────────────────────────────────────────────────────────────────────────
echo "[3/7] Generating app icon (mac/xvoice.icns)..."
"$PY" mac/make_icns.py || echo "  [WARN] Icon generation failed — the app will use the generic icon."
echo

# ─────────────────────────────────────────────────────────────────────────
#  [4/7] ffmpeg (optional)
# ─────────────────────────────────────────────────────────────────────────
#  Windows ships ffmpeg.exe beside the app; we do the same on macOS.  It is
#  a nice-to-have: main.py's normalize_audio() returns False when ffmpeg is
#  missing and the raw recording gets transcribed instead.
echo "[4/7] Locating a static ffmpeg to bundle..."
FFMPEG_SRC=""
if [[ -n "${XVOICE_FFMPEG:-}" && -f "${XVOICE_FFMPEG}" ]]; then
    FFMPEG_SRC="$XVOICE_FFMPEG"
    echo "  Using XVOICE_FFMPEG: $FFMPEG_SRC"
elif [[ -f "mac/ffmpeg" ]]; then
    FFMPEG_SRC="$ROOT/mac/ffmpeg"
    echo "  Using bundled mac/ffmpeg"
else
    ARCH="$(uname -m)"
    TMPD="$(mktemp -d)"
    if [[ "$ARCH" == "x86_64" ]]; then
        FF_URL="https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
    else
        FF_URL="https://www.osxexperts.net/ffmpeg711arm.zip"
    fi
    echo "  Downloading a static ffmpeg for $ARCH ..."
    if curl -fsSL --max-time 180 "$FF_URL" -o "$TMPD/ffmpeg.zip" \
       && unzip -qo "$TMPD/ffmpeg.zip" -d "$TMPD" \
       && FOUND="$(find "$TMPD" -type f -name ffmpeg -perm -u+x | head -1)" \
       && [[ -n "$FOUND" ]]; then
        mkdir -p mac
        cp "$FOUND" mac/ffmpeg
        chmod +x mac/ffmpeg
        FFMPEG_SRC="$ROOT/mac/ffmpeg"
        echo "  Saved to mac/ffmpeg"
    else
        echo "  [WARN] Could not fetch ffmpeg — building without it."
        echo "         Audio will be sent unnormalized (still works, slightly lower accuracy)."
        echo "         To add it later, drop a static binary at mac/ffmpeg and rebuild."
    fi
    rm -rf "$TMPD"
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [5/7] Compile
# ─────────────────────────────────────────────────────────────────────────
echo "[5/7] Compiling Xvoice.app..."
rm -rf build dist/Xvoice.app dist/xvoice
"$PY" -m PyInstaller --noconfirm xvoice.spec
if [[ ! -d "dist/Xvoice.app" ]]; then
    echo
    echo "[ERROR] Build failed: dist/Xvoice.app not found. Check the output above."
    exit 1
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [6/7] Post-process the bundle
# ─────────────────────────────────────────────────────────────────────────
echo "[6/7] Post-processing the bundle..."

# main.py looks for "ffmpeg.exe" beside sys.executable when frozen, on every
# platform — inside an .app that is Contents/MacOS.  Copying it here (rather
# than declaring it in the spec) guarantees the path regardless of which
# PyInstaller version laid out the bundle.
if [[ -n "$FFMPEG_SRC" ]]; then
    cp "$FFMPEG_SRC" "dist/Xvoice.app/Contents/MacOS/ffmpeg.exe"
    chmod +x "dist/Xvoice.app/Contents/MacOS/ffmpeg.exe"
    echo "  Bundled ffmpeg -> Contents/MacOS/ffmpeg.exe"
fi

# Strip any quarantine flag picked up from downloaded components.
xattr -cr "dist/Xvoice.app" 2>/dev/null || true

# Sign LAST — anything copied in above invalidates an earlier signature.
SIGN_ID="${XVOICE_SIGN_IDENTITY:--}"
if [[ "$SIGN_ID" == "-" ]]; then
    # Ad-hoc.  Deliberately WITHOUT --options runtime: the hardened runtime is
    # only meaningful together with a Developer ID (and is what notarization
    # requires), and pairing it with an ad-hoc signature just breaks dylib
    # loading for no benefit.
    echo "  Signing ad-hoc (no Developer ID)."
    echo "  Users will need to right-click -> Open on first launch,"
    echo "  or run 'First Run.command' from the DMG."
    codesign --force --deep --sign - "dist/Xvoice.app" \
      || echo "  [WARN] codesign failed — the app may be blocked by Gatekeeper."
else
    echo "  Signing with: $SIGN_ID"
    codesign --force --deep --timestamp \
             --options runtime \
             --entitlements mac/entitlements.plist \
             --sign "$SIGN_ID" "dist/Xvoice.app" \
      || echo "  [WARN] codesign failed — the app may be blocked by Gatekeeper."
fi

codesign --verify --deep --strict "dist/Xvoice.app" 2>&1 | head -5 || true
echo

# ─────────────────────────────────────────────────────────────────────────
#  [7/7] Done
# ─────────────────────────────────────────────────────────────────────────
echo "[7/7] Done!"
SIZE="$(du -sh dist/Xvoice.app | cut -f1)"
echo
echo "=========================================="
echo "  SUCCESS: dist/Xvoice.app ($SIZE)"
echo "=========================================="
echo
echo "Try it now:      open dist/Xvoice.app"
echo "Make a DMG:      ./installer_mac.sh"
echo
