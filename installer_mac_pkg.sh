#!/usr/bin/env bash
#
#  Xvoice macOS .pkg builder — the closest true analogue of installer.iss.
#
#  A .dmg is only a folder in a wrapper: the user drags, and nothing else
#  happens.  A .pkg is a real installer with a wizard, exactly like the one
#  Inno Setup produces — it copies Xvoice into /Applications and then runs
#  mac/pkg-scripts/postinstall, which clears quarantine, registers the
#  launch-at-login LaunchAgent and starts the app.  No "First Run.command"
#  step for the user.
#
#  Usage:
#      ./build_mac.sh && ./installer_mac_pkg.sh
#
#  Optional environment variables:
#      XVOICE_INSTALLER_IDENTITY  "Developer ID Installer: Name (TEAMID)"
#                                 NOTE: a .pkg is signed with a Developer ID
#                                 *Installer* cert — a different cert from the
#                                 *Application* one used on the .app itself.
#      XVOICE_NOTARY_PROFILE      notarytool keychain profile; when set the
#                                 .pkg is submitted and stapled.
#
set -uo pipefail

cd "$(dirname "$0")"

APP="dist/Xvoice.app"
IDENTIFIER="com.xvoicekeyboard.xvoice"

echo "=========================================="
echo "      Xvoice macOS .pkg Installer Builder"
echo "=========================================="
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] installer_mac_pkg.sh must be run on macOS (needs pkgbuild)."
    exit 1
fi
if [[ ! -d "$APP" ]]; then
    echo "[ERROR] $APP not found. Run ./build_mac.sh first."
    exit 1
fi
if [[ ! -f "mac/pkg-scripts/postinstall" ]]; then
    echo "[ERROR] mac/pkg-scripts/postinstall missing."
    exit 1
fi

VERSION="$(sed -n "s/^APP_VERSION *= *'\([^']*\)'.*/\1/p" xvoice.spec | head -1)"
VERSION="${VERSION:-1.1.0}"
ARCH="$(uname -m)"
PKG="dist/Xvoice-${VERSION}-${ARCH}.pkg"

echo "Version:      $VERSION"
echo "Architecture: $ARCH"
echo "Output:       $PKG"
echo

STAGE="$(mktemp -d)"
SCRIPTS="$(mktemp -d)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$SCRIPTS" "$BUILD"' EXIT

# ─────────────────────────────────────────────────────────────────────────
#  [1/4] Stage the payload
# ─────────────────────────────────────────────────────────────────────────
echo "[1/4] Staging payload..."
# ditto preserves the bundle's symlinks and code signature; cp -r does not.
ditto "$APP" "$STAGE/Xvoice.app" || {
    echo "[ERROR] Failed to stage $APP"; exit 1;
}

# pkgbuild refuses a scripts dir whose contents are not executable.
cp mac/pkg-scripts/postinstall "$SCRIPTS/postinstall"
chmod +x "$SCRIPTS/postinstall"
echo "  Payload staged."
echo

# ─────────────────────────────────────────────────────────────────────────
#  [2/4] Component package
# ─────────────────────────────────────────────────────────────────────────
echo "[2/4] Building the component package..."
pkgbuild \
    --root "$STAGE" \
    --install-location /Applications \
    --scripts "$SCRIPTS" \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    "$BUILD/component.pkg"
if [[ ! -f "$BUILD/component.pkg" ]]; then
    echo "[ERROR] pkgbuild failed."
    exit 1
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [3/4] Product archive (the part with the wizard UI)
# ─────────────────────────────────────────────────────────────────────────
echo "[3/4] Building the installer wizard..."

# Resources shown in the wizard panes. productbuild wants .txt/.rtf/.html.
mkdir -p "$BUILD/resources"
cp mac/dmg-readme.txt "$BUILD/resources/readme.txt"
cat > "$BUILD/resources/welcome.txt" <<'WELCOME'
Xvoice — dictate anywhere on your Mac.

Hold F8, speak, let go, and your words are typed into whatever app
you are using.

This installer will:

  * Install Xvoice into your Applications folder
  * Set it to start automatically when you log in
  * Launch it for you when the install finishes

Xvoice lives in your MENU BAR (the purple microphone at the top-right
of your screen), not in the Dock. That is deliberate — it is a
background utility, so it has no Dock icon and no window.

After installing you will be asked to grant Microphone, Accessibility
and Input Monitoring access. Xvoice cannot type for you without them.
WELCOME

# hostArchitectures pins the package to the arch this build was frozen for,
# so an Intel .pkg cannot be installed on Apple Silicon and vice versa —
# PyInstaller output is per-arch and a mismatch would install a broken app.
cat > "$BUILD/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Xvoice ${VERSION}</title>
    <organization>com.xvoicekeyboard</organization>
    <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
    <options customize="never" require-scripts="true" hostArchitectures="${ARCH}"/>
    <welcome file="welcome.txt"/>
    <readme file="readme.txt"/>
    <volume-check>
        <allowed-os-versions><os-version min="11.0"/></allowed-os-versions>
    </volume-check>
    <pkg-ref id="${IDENTIFIER}"/>
    <choices-outline>
        <line choice="default"><line choice="${IDENTIFIER}"/></line>
    </choices-outline>
    <choice id="default"/>
    <choice id="${IDENTIFIER}" visible="false">
        <pkg-ref id="${IDENTIFIER}"/>
    </choice>
    <pkg-ref id="${IDENTIFIER}" version="${VERSION}" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
XML

rm -f "$PKG"
mkdir -p dist

SIGN_ARGS=()
if [[ -n "${XVOICE_INSTALLER_IDENTITY:-}" ]]; then
    SIGN_ARGS=(--sign "$XVOICE_INSTALLER_IDENTITY")
    echo "  Signing with: $XVOICE_INSTALLER_IDENTITY"
else
    echo "  No XVOICE_INSTALLER_IDENTITY set — shipping unsigned."
    echo "  Users will need to right-click -> Open on the .pkg once."
fi

productbuild \
    --distribution "$BUILD/distribution.xml" \
    --package-path "$BUILD" \
    --resources "$BUILD/resources" \
    "${SIGN_ARGS[@]+"${SIGN_ARGS[@]}"}" \
    "$PKG"

if [[ ! -f "$PKG" ]]; then
    echo "[ERROR] productbuild failed."
    exit 1
fi
echo

# ─────────────────────────────────────────────────────────────────────────
#  [4/4] Notarize (optional)
# ─────────────────────────────────────────────────────────────────────────
echo "[4/4] Notarization..."
if [[ -n "${XVOICE_NOTARY_PROFILE:-}" ]]; then
    echo "  Submitting to Apple (profile: $XVOICE_NOTARY_PROFILE)..."
    if xcrun notarytool submit "$PKG" \
            --keychain-profile "$XVOICE_NOTARY_PROFILE" --wait; then
        xcrun stapler staple "$PKG" && echo "  Stapled."
    else
        echo "  [WARN] Notarization failed — the .pkg still installs but"
        echo "         shows a Gatekeeper warning on other Macs."
    fi
else
    echo "  Skipped (set XVOICE_NOTARY_PROFILE to enable)."
fi
echo

SIZE="$(du -sh "$PKG" | cut -f1)"
echo "=========================================="
echo "  SUCCESS: $PKG ($SIZE)"
echo "=========================================="
echo
echo "Test it:              open \"$PKG\""
echo "Inspect the payload:  pkgutil --payload-files \"$PKG\" | head"
echo "Postinstall log:      /tmp/xvoice-postinstall.log"
echo
