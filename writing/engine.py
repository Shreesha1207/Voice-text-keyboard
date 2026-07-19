import sys
import time
import threading
import logging
from pynput import mouse
from writing.overlay import OverlayManager
from writing.backend_client import BackendClient
from writing.actions.perform import perform_action

class WritingEngine:
    def __init__(self, get_token_func):
        self.backend_url = "https://voicetotext-keyboard-production.up.railway.app/api"
        self.backend_client = BackendClient(self.backend_url, get_token_func)
        self.overlay_manager = OverlayManager(self)
        self.mouse_listener = None
        self._running = False
        self._last_right_click_time = 0
        # Cache the selection captured at right-click time
        self._cached_selected_text = None
        self._cached_prev_clipboard = None

    def start(self):
        if self._running:
            return
        self._running = True
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()
        logging.info("WritingEngine mouse listener started.")

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.overlay_manager.hide_all()

    def _on_click(self, x, y, button, pressed):
        if button == mouse.Button.right:
            if pressed:
                self._last_right_click_time = time.time()
                logging.info(f"Right click PRESS at {x}, {y}")
                # Grab selection NOW, while the original app still has focus
                threading.Thread(
                    target=self._capture_selection_and_show,
                    args=(x, y),
                    daemon=True
                ).start()
            return

        if button == mouse.Button.left and pressed:
            # Ignore left-clicks within 0.5s of a right-click
            if time.time() - self._last_right_click_time < 0.5:
                logging.info("Ignoring left-click too soon after right-click")
                return
            self.overlay_manager.on_left_click(x, y)

    def _capture_selection_and_show(self, x, y):
        """
        Called in a background thread immediately on right-click.
        Captures the selected text before the Xvoice button appears.
        """
        from writing import selection
        time.sleep(0.05)
        logging.info("Capturing selected text at right-click time...")
        selected_text, prev_clipboard = selection.get_selected_text_and_restore()

        if selected_text and selected_text.strip():
            logging.info(f"Captured text: '{selected_text[:60]}...'")
            self._cached_selected_text = selected_text
            self._cached_prev_clipboard = prev_clipboard
            self.overlay_manager.show_xvoice_button(x, y)
        else:
            logging.info("No text selected at right-click, not showing button.")
            self._cached_selected_text = None
            self._cached_prev_clipboard = None

    def on_xvoice_click(self, x, y):
        """Called when the user clicks the floating Xvoice button."""
        selected_text = self._cached_selected_text
        prev_clipboard = self._cached_prev_clipboard

        if not selected_text or selected_text.strip() == "":
            logging.info("on_xvoice_click: no cached text, hiding overlay.")
            self.overlay_manager.hide_all()
            return

        logging.info(f"on_xvoice_click: showing action menu for '{selected_text[:40]}...'")
        # Clear cache now — action menu will use the values we pass directly
        self._cached_selected_text = None
        self._cached_prev_clipboard = None
        # Show the action picker (replaces old language-only menu)
        self.overlay_manager.cmd_queue.put(('show_action', (x, y, selected_text, prev_clipboard)))

    def trigger_action(self, action: str, target_language: str | None,
                       selected_text: str, prev_clipboard: str):
        """Called by the UI when the user selects an action (and optional language)."""
        action_label = f"{action}" + (f" → {target_language}" if target_language else "")

        def on_success(result_text: str):
            self.overlay_manager.cmd_queue.put(
                ('show_toast', (f"✓ {action_label.title()}", True, 2000))
            )

        def on_error(msg: str):
            logging.error(f"Action '{action}' error: {msg}")
            self.overlay_manager.cmd_queue.put(
                ('show_toast', (f"✗ Failed: {msg[:40]}", False, 2500))
            )

        def on_loading():
            self.overlay_manager.cmd_queue.put(
                ('show_toast', (f"⏳ {action_label.title()}…", True, 6000))
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
