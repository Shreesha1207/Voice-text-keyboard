import time
import threading
import logging
from pynput import mouse
from writing.overlay import OverlayManager
from writing.backend_client import BackendClient
from writing.actions.perform import perform_action

logger = logging.getLogger(__name__)


class WritingEngine:
    def __init__(self, get_token_func):
        # Single source of truth — this used to be a second hardcoded copy of the
        # backend URL, so changing environments meant editing two files.
        try:
            from main import RAILWAY_URL as _BACKEND_URL
        except Exception:
            _BACKEND_URL = "https://voicetotext-keyboard-production.up.railway.app/api"
        self.backend_url = _BACKEND_URL
        self.backend_client = BackendClient(self.backend_url, get_token_func)
        self.overlay_manager = OverlayManager(self)
        self.mouse_listener = None
        self._running = False
        self._last_click_x = 100
        self._last_click_y = 100

        # User preferences — read once at start-up, then updated only when the
        # server pushes a change (see apply_preferences). Never polled.
        self._auto_replace   = False   # True → replace immediately, no preview
        self._show_preview   = True    # True → show VS Code-style preview widget
        self._default_language = "en"
        self._prefs_initialized = False

        # Click-gating state for the floating Xvoice button. A click is a press
        # AND a release on the button — see _on_click.
        self._press_was_on_button = False
        self._click_lock = threading.Lock()
        self._click_in_flight = False
        self._last_xvoice_click = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        # Surface a missing clipboard backend once, at startup, instead of failing
        # silently on every selection.
        from writing import clipboard
        clipboard.check_available()
        # One read at start-up. Nothing after this asks the server for
        # preferences — changes arrive by push (see apply_preferences).
        threading.Thread(target=self._load_preferences, daemon=True).start()
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()

    def _load_preferences(self):
        """Fetch writing preferences from the backend and cache them."""
        try:
            prefs = self.backend_client.get_preferences()
        except Exception as e:
            # Runs as a bare thread target, so anything escaping here surfaces as
            # an unhandled-exception traceback and kills the refresh silently.
            # Preferences simply keep their previous values.
            logger.error(f"Could not refresh writing preferences: {e}")
            return
        if prefs:
            self._auto_replace = prefs.get("auto_replace", self._auto_replace)
            self._show_preview = prefs.get("show_preview", self._show_preview)
            self._default_language = prefs.get("default_language", self._default_language)
            logger.info(
                f"Writing prefs loaded: auto_replace={self._auto_replace}, "
                f"show_preview={self._show_preview}, lang={self._default_language}"
            )

    def apply_preferences(self, prefs: dict):
        """Apply preferences pushed down the live channel from the server.

        This is how a change made in writing settings on the website reaches a
        running app. There is no polling and no fetch-on-use: the values arrive
        the moment Save is pressed, which is also why the language picked there
        now shows up in the translate menu straight away instead of only after a
        restart.
        """
        if not prefs:
            return
        self._auto_replace     = prefs.get("auto_replace", self._auto_replace)
        self._show_preview     = prefs.get("show_preview", self._show_preview)
        self._default_language = prefs.get("default_language", self._default_language)
        logger.info(
            f"Writing prefs updated from server: auto_replace={self._auto_replace}, "
            f"show_preview={self._show_preview}, lang={self._default_language}"
        )

    def refresh_preferences(self):
        """One-off re-read. Used on start-up and after the live channel
        reconnects, to pick up anything saved while the app was offline."""
        threading.Thread(target=self._load_preferences, daemon=True).start()

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.overlay_manager.hide_all()

    def _on_click(self, x, y, button, pressed):
        if button == mouse.Button.left:
            if pressed:
                # Only REMEMBER whether the press landed on the button. Acting here
                # was a real bug: a press is also how a drag-selection starts, and
                # the button sits 15px right / 30px above the cursor for several
                # seconds after any right-click — exactly where you press next. So
                # starting a new selection near it fired Ctrl+C with no click at
                # all. In a terminal that Ctrl+C is SIGINT, which kills whatever
                # command is running.
                #
                # The global hook is still what detects the click (Qt never sees it
                # when a native context menu has grabbed the mouse), just on the
                # release instead.
                self._press_was_on_button = self.overlay_manager.button_hit(x, y)
                if self._press_was_on_button:
                    return
            else:
                was_on_button = self._press_was_on_button
                self._press_was_on_button = False
                # A click is press AND release on the button. A drag that merely
                # began or ended there is not a click and stays completely inert.
                if was_on_button and self.overlay_manager.button_hit(x, y):
                    self.on_xvoice_click(x, y)
                    return
                # Releasing a left click — including after a drag — deliberately
                # does NOT offer the button. Selecting text is not a request to do
                # anything; the user asks for Xvoice by right-clicking. This keeps
                # the app completely inert while you are just selecting or editing.
                self.overlay_manager.on_left_click(x, y)
            return

        # Right-click is the single entry point: it shows the button and nothing
        # else. No clipboard access, no synthetic Ctrl+C — the app's own context
        # menu opens exactly as normal alongside it.
        #
        # Deliberately not conditional on having seen a drag: text is just as often
        # selected by double-click, triple-click, Ctrl+A or Shift+arrows, none of
        # which produce one. Requiring a drag meant the button never appeared for
        # those. If nothing is in fact selected, clicking the button copies nothing
        # and dismisses cleanly (see on_xvoice_click).
        if button == mouse.Button.right and pressed:
            # A fresh right-click starts a new gesture, so clear the dedupe window.
            # Without this, offering the button again very soon after a previous
            # use could swallow the next genuine click.
            self._last_xvoice_click = 0.0
            self._press_was_on_button = False
            self.overlay_manager.show_xvoice_button(x, y)

    # Window in which a repeat on_xvoice_click is treated as the same physical
    # click arriving down a second path, rather than a new invocation.
    CLICK_DEDUPE_SECONDS = 0.7

    def on_xvoice_click(self, x, y):
        """Called when the user clicks the floating Xvoice button.

        This — and only this — is where the selection is copied. The copy runs in a
        background thread because it sends real keystrokes (Ctrl+C) with short
        sleeps, which must not block the mouse-listener callback."""
        # One physical click reaches here TWICE: once from the global mouse hook
        # and once from Qt's own clicked signal on the QPushButton. That sent two
        # Ctrl+C's per click, with two clipboard save/restore cycles racing each
        # other. Whichever arrives first wins; the second is dropped.
        #
        # Deduping on the in-flight flag alone is not enough — it only covers the
        # window where the copy thread is still running, and the two callbacks can
        # straddle it. A short time window makes it deterministic regardless of how
        # fast the copy completes. The button is hidden on the first click, so a
        # second genuine invocation needs another right-click and cannot land here
        # this quickly.
        now = time.time()
        with self._click_lock:
            if self._click_in_flight or (now - self._last_xvoice_click) < self.CLICK_DEDUPE_SECONDS:
                return
            self._last_xvoice_click = now
            self._click_in_flight = True

        self._last_click_x = x
        self._last_click_y = y

        # Take the button off screen right away so it cannot be hit again while
        # the copy is in progress.
        self.overlay_manager.hide_all()

        # Deliberately no preference fetch here. Preferences are pushed by the
        # server when they change, so asking again on every click would be the
        # polling this replaced, just triggered by the mouse instead of a timer.

        def _capture_and_open():
            try:
                from writing import selection
                selected_text, prev_clipboard = selection.get_selected_text_and_restore()
                if not selected_text or not selected_text.strip():
                    # Nothing was actually selected (e.g. the button appeared after a
                    # drag that selected no text). Just dismiss.
                    self.overlay_manager.hide_all()
                    return
                self.overlay_manager.cmd_queue.put(
                    ('show_action', (self._last_click_x, self._last_click_y,
                                     selected_text, prev_clipboard))
                )
            finally:
                with self._click_lock:
                    self._click_in_flight = False

        threading.Thread(target=_capture_and_open, daemon=True).start()

    def trigger_action(self, action: str, target_language: str | None,
                       selected_text: str, prev_clipboard: str):
        """Called by the action menu when the user picks an action."""
        if action == "translate" and not target_language:
            # Position the language sub-menu right beside the action menu as a flyout
            click_x = self._last_click_x + 215
            click_y = self._last_click_y + 85
            self.overlay_manager.cmd_queue.put(('show_lang', (click_x, click_y, selected_text, prev_clipboard)))
            return

        action_label = action.replace("_", " ").title()
        if target_language:
            action_label += f" → {target_language}"

        click_x = self._last_click_x
        click_y = self._last_click_y
        is_summary = action.lower() in ("summarise", "summarize", "summary")
        should_auto_replace = (self._auto_replace and not self._show_preview and not is_summary)

        def on_success(result_text: str):
            if should_auto_replace:
                # User chose "Auto-replace" with preview disabled, and action is not summary → replace immediately
                self.overlay_manager.cmd_queue.put(
                    ("auto_replace", (result_text, prev_clipboard, action_label))
                )
            else:
                # Preview mode is ON, or auto_replace is disabled, or action is Summary → show Accept/Dismiss widget
                self.overlay_manager.cmd_queue.put((
                    "show_preview",
                    (click_x, click_y, action, selected_text, result_text, prev_clipboard),
                ))

        def on_error(msg: str):
            # Release the cached selection — no need to hold the user's text in
            # memory once the action has finished.
            self._cached_selected_text = None
            self._cached_prev_clipboard = None
            logger.error(f"Writing action '{action}' failed: {msg}")
            self.overlay_manager.cmd_queue.put(
                ("show_toast", (f"✗ {msg[:60]}", False, 2500))
            )

        def on_loading():
            self.overlay_manager.cmd_queue.put(
                ("show_toast", (f"⏳ {action_label}…", True, 8000))
            )

        perform_action(
            self.backend_client,
            action=action,
            target_language=target_language,
            selected_text=selected_text,
            prev_clipboard=prev_clipboard,
            on_success=on_success,
            on_error=on_error,
            on_loading=on_loading,
        )
