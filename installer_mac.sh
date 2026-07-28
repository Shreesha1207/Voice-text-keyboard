#!/usr/bin/env bash
#
#  Xvoice macOS installer builder — the mac counterpart of installer.iss.
#
#  Inno Setup wraps xvoice.exe into XVoiceSetup.exe; on macOS the equivalent
#  deliverable is a disk image the user opens and drags into Applications.
#  This produces dist/Xvoice-<version>-<arch>.dmg containing:
#
#      Xvoice.app          the app itself
#      Applications        symlink, so the drag target is right there
#      First Run.command   clears quarantine, opens the permission panes,
#                          optionally enables launch-at-login
#      Read Me.txt         plain-text install + permissions instructions
#
#  Usage:
#      ./build_mac.sh && ./installer_mac.sh
#
#  Optional environment variables:
#      XVOICE_SIGN_IDENTITY   "Developer ID Application: Name (TEAMID)"
#      XVOICE_NOTARY_PROFILE  notarytool keychain profile name; when set the
#                             DMG is submitted for notarization and stapled.
#
set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

APP="dist/Xvoice.app"
VOLNAME="Xvoice"

echo "=========================================="
echo "        Xvoice macOS Installer Builder"
echo "=========================================="
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] installer_mac.sh must be run on macOS (needs hdiutil)."
    exit 1
fi

if [[ ! -d "$APP" ]]; then
    echo "[ERROR] $APP not found."
    echo "        Run ./build_mac.sh first."
    exit 1
fi

# Version comes from xvoice.spec so the app bundle and the DMG never disagree.
VERSION="$(sed -n "s/^APP_VERSION *= *'\([^']*\)'.*/\1/p" xvoice.spec | head -1)"
VERSION="${VERSION:-1.1.0}"
ARCH="$(uname -m)"
DMG="dist/Xvoice-${VERSION}-${ARCH}.dmg"

echo "Version:      $VERSION"
echo "Architecture: $ARCH"
echo "Output:       $DMG"
echo

# ─────────────────────────────────────────────────────────────────────────
#  [1/4] Stage the DMG contents
# ─────────────────────────────────────────────────────────────────────────
echo "[1/4] Staging DMG contents..."
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# ditto is the only copy that reliably preserves the bundle's symlinks,
# extended attributes and code signature — cp -r silently breaks all three.
for helper in mac/first-run.command mac/dmg-readme.txt; do
    if [[ ! -f "$helper" ]]; then
        echo "[ERROR] Missing $helper — the DMG would ship without install"
        echo "        instructions. Restore it and retry."
        exit 1
    fi
done

ditto "$APP" "$STAGE/Xvoice.app" || cp -Rp "$APP" "$STAGE/Xvoice.app"
ln -s /Applications "$STAGE/Applications"
cp "mac/first-run.command" "$STAGE/First Run.command"
chmod +x "$STAGE/First Run.command"
cp "mac/dmg-readme.txt" "$STAGE/Read Me.txt"

if [[ ! -d "$STAGE/Xvoice.app/Contents/MacOS" ]]; then
    echo "[ERROR] Staging the app bundle failed."
    exit 1
fi
echo "  Staged at $STAGE"
echo

# ─────────────────────────────────────────────────────────────────────────
#  [2/4] Build the disk image
# ─────────────────────────────────────────────────────────────────────────
echo "[2/4] Building the disk image..."
rm -f "$DMG"
mkdir -p dist

if command -v create-dmg >/dev/null 2>&1; then
    # Pretty window with icon positions, mirroring the Inno Setup wizard's
    # "here is your app, here is where it goes" layout.
    create-dmg \
        --volname "$VOLNAME" \
        --window-pos 200 120 \
        --window-size 660 460 \
        --icon-size 128 \
        --icon "Xvoice.app"        160 180 \
        --icon "Applications"      500 180 \
        --icon "First Run.command" 160 340 \
        --icon "Read Me.txt"       500 340 \
        --hide-extension "Xvoice.app" \
        --no-internet-enable \
        "$DMG" "$STAGE" \
    || true
fi

if [[ ! -f "$DMG" ]]; then
    echo "  create-dmg unavailable or failed — falling back to hdiutil."
    hdiutil create \
        -volname "$VOLNAME" \
        -srcfolder "$STAGE" \
        -ov -format UDZO \
        "$DMG"
fi

if [[ ! -f "$DMG" ]]; then
    echo "[ERROR] DMG creation failed."
    exit 1
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [3/4] Sign
# ─────────────────────────────────────────────────────────────────────────
echo "[3/4] Signing the disk image..."
SIGN_ID="${XVOICE_SIGN_IDENTITY:--}"
if [[ "$SIGN_ID" != "-" ]]; then
    codesign --force --sign "$SIGN_ID" "$DMG" \
      && echo "  Signed with $SIGN_ID" \
      || echo "  [WARN] DMG signing failed."
else
    echo "  No XVOICE_SIGN_IDENTITY set — shipping unsigned."
    echo "  Users must run 'First Run.command' (or right-click -> Open) once."
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [4/4] Notarize (optional)
# ─────────────────────────────────────────────────────────────────────────
echo "[4/4] Notarization..."
if [[ -n "${XVOICE_NOTARY_PROFILE:-}" ]]; then
    echo "  Submitting to Apple (profile: $XVOICE_NOTARY_PROFILE)..."
    if xcrun notarytool submit "$DMG" \
            --keychain-profile "$XVOICE_NOTARY_PROFILE" \
            --wait; then
        xcrun stapler staple "$DMG" && echo "  Stapled."
    else
        echo "  [WARN] Notarization failed — the DMG is still usable but will"
        echo "         show a Gatekeeper warning on other Macs."
    fi
else
    echo "  Skipped (set XVOICE_NOTARY_PROFILE to enable)."
    echo
    echo "  To set one up once:"
    echo "    xcrun notarytool store-credentials xvoice-notary \\"
    echo "      --apple-id you@example.com --team-id TEAMID --password <app-specific-password>"
fi
echo

SIZE="$(du -sh "$DMG" | cut -f1)"
echo "=========================================="
echo "  SUCCESS: $DMG ($SIZE)"
echo "=========================================="
echo
echo "Test it:  open \"$DMG\""
echo
