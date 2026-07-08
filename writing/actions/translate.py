import threading
from typing import Callable, Any
from writing.backend_client import BackendClient
from writing import selection

def perform_translation(
    backend_client: BackendClient, 
    target_language: str, 
    on_success: Callable[[str], Any],
    on_error: Callable[[str], Any],
    on_loading: Callable[[], Any]
):
    """
    Executes the translation flow in a background thread to avoid blocking the UI.
    """
    def _run():
        on_loading()
        
        # 1. Grab selection and backup clipboard
        selected_text, prev_clipboard = selection.get_selected_text_and_restore()
        
        if not selected_text:
            on_error("No text selected to translate.")
            return

        # 2. Call Backend
        response = backend_client.transform_text(
            action="translate",
            text=selected_text,
            target_language=target_language
        )
        
        # 3. Handle response
        if response.get("success"):
            translated_text = response.get("result", "")
            if translated_text:
                # 4. Paste over original selection
                try:
                    selection.replace_text(translated_text, prev_clipboard)
                    on_success(translated_text)
                except Exception as e:
                    on_error(f"Failed to replace text: {e}")
            else:
                on_error("Translation returned empty result.")
        else:
            on_error(response.get("error", "Unknown error during translation."))

    # Run in thread so the overlay/menu UI doesn't freeze
    threading.Thread(target=_run, daemon=True).start()
