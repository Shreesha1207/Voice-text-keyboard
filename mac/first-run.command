#!/usr/bin/env bash
#
#  Xvoice — First Run helper (ships inside Xvoice.dmg)
#
#  macOS has no post-install script step the way an Inno Setup [Run] section
#  does, so this stands in for it.  Double-click it after dragging Xvoice into
#  Applications and it will:
#
#    1. Clear the quarantine flag so Gatekeeper stops blocking the app
#    2. Open the three Privacy panes Xvoice needs
#    3. Optionally start Xvoice automatically at login
#    4. Launch Xvoice
#
set -uo pipefail

APP="/Applications/Xvoice.app"
LABEL="com.xvoicekeyboard.xvoice"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

clear
cat <<'BANNER'
==========================================
              Xvoice Setup
==========================================

BANNER

# ── Locate the app ───────────────────────────────────────────────────────
if [[ ! -d "$APP" ]]; then
    HERE="$(cd "$(dirname "$0")" && pwd)"
    if [[ -d "$HERE/Xvoice.app" ]]; then
        echo "Xvoice has not been installed yet."
        echo
        echo "Please drag Xvoice.app onto the Applications folder in this"
        echo "window first, then run this script again."
        echo
        read -n 1 -s -r -p "Press any key to close..."
        exit 1
    fi
    echo "[ERROR] Could not find $APP"
    echo "        Install Xvoice into your Applications folder and retry."
    echo
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

# ── [1/4] Quarantine ─────────────────────────────────────────────────────
echo "[1/4] Clearing the download quarantine flag..."
# Anything downloaded from the internet is tagged com.apple.quarantine.  For
# an app that is not notarized, that tag is what produces the
# "Xvoice is damaged and can't be opened" dialog.  Removing it is the same
# thing macOS does for you when you right-click -> Open.
if xattr -dr com.apple.quarantine "$APP" 2>/dev/null; then
    echo "      Done."
else
    echo "      Nothing to clear (or already cleared)."
fi
echo

# ── [2/4] Permissions ────────────────────────────────────────────────────
echo "[2/4] Opening the Privacy & Security settings Xvoice needs."
echo
echo "      Please switch ON  Xvoice  in each pane that opens:"
echo
echo "        * Microphone        — to hear you"
echo "        * Accessibility     — to type the transcribed text for you"
echo "        * Input Monitoring  — to notice the F8 key in other apps"
echo
read -n 1 -s -r -p "      Press any key to open the settings panes..."
echo
for pane in Privacy_Microphone Privacy_Accessibility Privacy_ListenEvent; do
    open "x-apple.systempreferences:com.apple.preference.security?$pane"
    sleep 1
done
echo
echo "      (Xvoice appears in Accessibility and Input Monitoring only after"
echo "       its first launch — if a list is empty, finish this script, then"
echo "       press F8 once and check again.)"
echo
read -n 1 -s -r -p "      Press any key when you are done granting access..."
echo
echo

# ── [3/4] Launch at login ────────────────────────────────────────────────
echo "[3/4] Start Xvoice automatically when you log in?"
read -r -p "      [Y/n] " REPLY
echo
if [[ ! "$REPLY" =~ ^[Nn] ]]; then
    mkdir -p "$(dirname "$AGENT")"
    cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP/Contents/MacOS/xvoice</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>/dev/null</string>
    <key>StandardErrorPath</key>
    <string>/dev/null</string>
</dict>
</plist>
PLIST
    launchctl unload "$AGENT" 2>/dev/null || true
    launchctl load  "$AGENT" 2>/dev/null || true
    echo "      Enabled. (Turn it off later with:"
    echo "       launchctl unload ~/Library/LaunchAgents/$LABEL.plist"
    echo "       rm ~/Library/LaunchAgents/$LABEL.plist)"
else
    echo "      Skipped."
fi
echo

# ── [4/4] Launch ─────────────────────────────────────────────────────────
echo "[4/4] Launching Xvoice..."
open "$APP"
sleep 2
echo

cat <<'DONE'
==========================================
                 SUCCESS!
==========================================

Xvoice is running in your menu bar (look for the purple
microphone at the top-right of the screen).

  * Hold F8 anywhere, speak, release  ->  your words get typed
  * Your browser opens once so you can link your account
  * Click the menu-bar icon for the dashboard, logs and Quit

Logs live in:  ~/Library/Logs/Xvoice/

DONE
read -n 1 -s -r -p "Press any key to close this window..."
echo
