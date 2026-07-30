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

        # User preferences — fetched from backend on start (and refreshed periodically)
        self._auto_replace   = False   # True → replace immediately, no preview
        self._show_preview   = True    # True → show VS Code-style preview widget
        self._default_language = "en"
        self._prefs_initialized = False

    def start(self):
        if self._running:
            return
        self._running = True
        # Surface a missing clipboard backend once, at startup, instead of failing
        # silently on every selection.
        from writing import clipboard
        clipboard.check_available()
        # Load preferences in background and keep syncing so webapp changes reflect instantly
        threading.Thread(target=self._preference_polling_loop, daemon=True).start()
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()

    def _load_preferences(self):
        """Fetch writing preferences from the backend and cache them."""
        prefs = self.backend_client.get_preferences()
        if prefs:
            new_auto = prefs.get("auto_replace", self._auto_replace)
            new_preview = prefs.get("show_preview", self._show_preview)
            new_lang = prefs.get("default_language", self._default_language)

            changed = (
                not self._prefs_initialized
                or new_auto != self._auto_replace
                or new_preview != self._show_preview
                or new_lang != self._default_language
            )

            self._auto_replace = new_auto
            self._show_preview = new_preview
            self._default_language = new_lang

            if changed:
                self._prefs_initialized = True
                logger.info(
                    f"Writing prefs loaded: auto_replace={self._auto_replace}, "
                    f"show_preview={self._show_preview}, lang={self._default_language}"
                )

    PREFERENCE_POLL_SECONDS = 3600

    def _preference_polling_loop(self):
        while self._running:
            self._load_preferences()
            time.sleep(self.PREFERENCE_POLL_SECONDS)

    def refresh_preferences(self):
        """Called externally (e.g. after user saves settings) to reload prefs."""
        threading.Thread(target=self._load_preferences, daemon=True).start()

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.overlay_manager.hide_all()

    def _on_click(self, x, y, button, pressed):
        if button == mouse.Button.left:
            if pressed:
                # Detect a click on the Xvoice button via the GLOBAL hook, so it
                # works even while a native context menu is grabbing the mouse
                # (in which case Qt never receives the click on our overlay).
                if self.overlay_manager.button_hit(x, y):
                    self.on_xvoice_click(x, y)
                    return
            else:
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
            self.overlay_manager.show_xvoice_button(x, y)

    def on_xvoice_click(self, x, y):
        """Called when the user clicks the floating Xvoice button.

        This — and only this — is where the selection is copied. The copy runs in a
        background thread because it sends real keystrokes (Ctrl+C) with short
        sleeps, which must not block the mouse-listener callback."""
        self._last_click_x = x
        self._last_click_y = y

        def _capture_and_open():
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
