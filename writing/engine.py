import sys
import time
import threading
from pynput import mouse
from writing.overlay import OverlayManager
from writing.backend_client import BackendClient
from writing.actions.translate import perform_translation

class WritingEngine:
    def __init__(self, get_token_func):
        # We need the railway URL from main config, but we can hardcode for now
        # since it's a constant in main.py
        self.backend_url = "https://voicetotext-keyboard-production.up.railway.app/api"
        self.backend_client = BackendClient(self.backend_url, get_token_func)
        self.overlay_manager = OverlayManager(self)
        self.mouse_listener = None
        self._running = False
        self._last_click_time = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.start()

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.overlay_manager.hide_all()

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
            
        # Ignore clicks that happen very close together to avoid double triggers
        now = time.time()
        if now - self._last_click_time < 0.1:
            return
        self._last_click_time = now

        if button == mouse.Button.right:
            # Show the overlay button adjacent to the cursor
            self.overlay_manager.show_xvoice_button(x, y)
        elif button == mouse.Button.left:
            # If they left click somewhere else, we should hide the overlay
            # OverlayManager needs to handle if the click was ON the overlay or not.
            # We'll just pass the click coordinates.
            self.overlay_manager.on_left_click(x, y)

    def on_xvoice_click(self, x, y):
        # 1. Grab selection and backup clipboard before showing UI
        from writing import selection
        selected_text, prev_clipboard = selection.get_selected_text_and_restore()
        
        if not selected_text or selected_text.strip() == "":
            self.overlay_manager.hide_all()
            return
            
        self.overlay_manager.cmd_queue.put(('show_lang', (x, y, selected_text, prev_clipboard)))

    def trigger_translation(self, target_language: str, selected_text: str, prev_clipboard: str):
        # Called by the UI when a language is selected
        def on_success(text):
            self.overlay_manager.cmd_queue.put(('show_toast', (f"✓ Translated to {target_language}", True, 1000)))
            
        def on_error(msg):
            # No error toast on copy failure, but we still have error callback for backend issues
            # We will ignore errors for gracefully exiting, but log backend errors.
            pass
            
        def on_loading():
            self.overlay_manager.cmd_queue.put(('show_toast', ("🌐 Translating...", True, 3000)))

        perform_translation(
            self.backend_client,
            target_language,
            selected_text,
            prev_clipboard,
            on_success,
            on_error,
            on_loading
        )
