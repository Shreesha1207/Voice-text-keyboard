==========================================
  Xvoice for macOS — Install Instructions
==========================================

Hold F8 anywhere, speak, let go — your words get typed into
whatever app you are in.


------------------------------------------
1. INSTALL
------------------------------------------

Drag  Xvoice.app  onto the  Applications  folder in this window.


------------------------------------------
2. FIRST RUN
------------------------------------------

Double-click  "First Run.command"  in this window.

It clears the download flag macOS puts on the app, opens the
permission settings, and starts Xvoice for you.

If macOS says "First Run.command cannot be opened because it is
from an unidentified developer":

    Right-click it  ->  Open  ->  Open

(You only have to do that once.)

Prefer to do it by hand instead? Open Terminal and run:

    xattr -dr com.apple.quarantine /Applications/Xvoice.app
    open /Applications/Xvoice.app


------------------------------------------
3. PERMISSIONS
------------------------------------------

macOS will ask for these. Xvoice cannot work without them:

  Microphone
      So Xvoice can hear you.
      Asked automatically the first time you press F8.

  Accessibility
      So Xvoice can type the transcribed text into other apps.
      System Settings -> Privacy & Security -> Accessibility
      -> turn ON Xvoice

  Input Monitoring
      So Xvoice notices the F8 key while another app is in front.
      System Settings -> Privacy & Security -> Input Monitoring
      -> turn ON Xvoice

Xvoice only shows up in those two lists AFTER you have launched it
once, so launch it first, then go and switch it on.

After granting Accessibility or Input Monitoring, quit and reopen
Xvoice (menu-bar icon -> Refresh / Restart). macOS only hands the
new permission to a freshly started process.


------------------------------------------
4. USING IT
------------------------------------------

  Hold F8      start recording  (you will hear a beep)
  Speak        talk normally, any English accent
  Release F8   recording stops, transcription starts
  ~1-2 sec     the text is typed at your cursor

Xvoice has no Dock icon on purpose — it lives in the menu bar as a
purple microphone at the top-right of your screen. Click it for the
dashboard, logs, translation toggle, restart and quit.

On first launch your browser opens once so you can link your
Xvoice account.


------------------------------------------
5. TROUBLESHOOTING
------------------------------------------

"Xvoice is damaged and can't be opened"
    The quarantine flag is still set. Run "First Run.command",
    or in Terminal:
        xattr -dr com.apple.quarantine /Applications/Xvoice.app

F8 does nothing
    Input Monitoring is off, or Xvoice needs a restart after you
    turned it on. Also check that no other app has claimed F8 —
    System Settings -> Keyboard -> Keyboard Shortcuts.

Nothing gets typed, but the logs show a transcription
    Accessibility is off. Turn it on, then restart Xvoice.

No menu-bar icon
    Your menu bar may be full. Hold Cmd and drag other icons to
    make room, or check the logs to confirm Xvoice is running.

Logs
    ~/Library/Logs/Xvoice/
    Also reachable from the menu-bar icon -> Help -> View Logs.


------------------------------------------
6. UNINSTALL
------------------------------------------

    launchctl unload ~/Library/LaunchAgents/com.xvoicekeyboard.xvoice.plist
    rm -f ~/Library/LaunchAgents/com.xvoicekeyboard.xvoice.plist
    rm -rf /Applications/Xvoice.app
    rm -rf ~/Library/Application\ Support/Xvoice
    rm -rf ~/Library/Logs/Xvoice

Then remove Xvoice from System Settings -> Privacy & Security ->
Accessibility / Input Monitoring / Microphone.


Support: https://xvoicekeyboard.com
