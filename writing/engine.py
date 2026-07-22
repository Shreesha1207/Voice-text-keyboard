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
        self.backend_url = "https://voicetotext-keyboard-production.up.railway.app/api"
        self.backend_client = BackendClient(self.backend_url, get_token_func)
        self.overlay_manager = OverlayManager(self)
        self.mouse_listener = None
        self._running = False
        self._last_right_click_time = 0
        self._cached_selected_text = None
        self._cached_prev_clipboard = None
        self._last_click_x = 100
        self._last_click_y = 100

        # User preferences — fetched from backend on start (and refreshed periodically)
        self._auto_replace   = False   # True → replace immediately, no preview
        self._show_preview   = True    # True → show VS Code-style preview widget
        self._default_language = "en"

    def start(self):
        if self._running:
            return
        self._running = True
        # Load preferences in background so startup isn't blocked
        threading.Thread(target=self._load_preferences, daemon=True).start()
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()

    def _load_preferences(self):
        """Fetch writing preferences from the backend and cache them."""
        prefs = self.backend_client.get_preferences()
        if prefs:
            self._auto_replace    = prefs.get("auto_replace",    self._auto_replace)
            self._show_preview    = prefs.get("show_preview",    self._show_preview)
            self._default_language = prefs.get("default_language", self._default_language)
            logger.info(
                f"Writing prefs loaded: auto_replace={self._auto_replace}, "
                f"show_preview={self._show_preview}, lang={self._default_language}"
            )

    def refresh_preferences(self):
        """Called externally (e.g. after user saves settings) to reload prefs."""
        threading.Thread(target=self._load_preferences, daemon=True).start()

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.overlay_manager.hide_all()

    def _on_click(self, x, y, button, pressed):
        if button == mouse.Button.right:
            if pressed:
                self._last_right_click_time = time.time()
                threading.Thread(
                    target=self._capture_selection_and_show,
                    args=(x, y),
                    daemon=True
                ).start()
            return

        if button == mouse.Button.left and pressed:
            if time.time() - self._last_right_click_time < 0.5:
                return
            self.overlay_manager.on_left_click(x, y)

    def _capture_selection_and_show(self, x, y):
        from writing import selection
        time.sleep(0.05)
        selected_text, prev_clipboard = selection.get_selected_text_and_restore()

        if selected_text and selected_text.strip():
            self._cached_selected_text = selected_text
            self._cached_prev_clipboard = prev_clipboard
            self.overlay_manager.show_xvoice_button(x, y)
        else:
            self._cached_selected_text = None
            self._cached_prev_clipboard = None

    def on_xvoice_click(self, x, y):
        """Called when the user clicks the floating Xvoice button."""
        selected_text = self._cached_selected_text
        prev_clipboard = self._cached_prev_clipboard

        if not selected_text or selected_text.strip() == "":
            self.overlay_manager.hide_all()
            return

        self._last_click_x = x
        self._last_click_y = y
        self._cached_selected_text = None
        self._cached_prev_clipboard = None
        self.overlay_manager.cmd_queue.put(('show_action', (x, y, selected_text, prev_clipboard)))

    def trigger_action(self, action: str, target_language: str | None,
                       selected_text: str, prev_clipboard: str):
        """Called by the action menu when the user picks an action."""
        action_label = action.replace("_", " ").title()
        if target_language:
            action_label += f" → {target_language}"

        click_x = self._last_click_x
        click_y = self._last_click_y
        auto_replace = self._auto_replace   # snapshot at time of action

        def on_success(result_text: str):
            if auto_replace:
                # User chose "Auto-replace" in settings → replace immediately
                self.overlay_manager.cmd_queue.put(
                    ("auto_replace", (result_text, prev_clipboard, action_label))
                )
            else:
                # User chose "Preview before replacing" → show Accept/Dismiss widget
                self.overlay_manager.cmd_queue.put((
                    "show_preview",
                    (click_x, click_y, action, selected_text, result_text, prev_clipboard),
                ))

        def on_error(msg: str):
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
