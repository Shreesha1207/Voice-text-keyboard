"""
Generic writing action handler.
Replaces the translate-only perform_translation function.
All 10 actions go through the same /api/text/transform endpoint;
only the 'action' field and optional 'target_language' differ.
"""
import threading
from typing import Callable, Any
from writing.backend_client import BackendClient
from writing import selection


def perform_action(
    backend_client: BackendClient,
    action: str,
    target_language: str | None,
    selected_text: str,
    prev_clipboard: str,
    on_success: Callable[[str], Any],
    on_error: Callable[[str], Any],
    on_loading: Callable[[], Any],
) -> None:
    """
    Execute any writing action in a background thread so the UI stays responsive.
    
    action          — one of: translate, improve, shorten, expand, professional,
                              casual, persuasive, summarise, rephrase, fix_grammar
    target_language — required when action == 'translate'; ignored otherwise
    """
    def _run():
        on_loading()

        response = backend_client.transform_text(
            action=action,
            text=selected_text,
            target_language=target_language or "English",
        )

        if response.get("success"):
            result_text = response.get("result", "")
            if result_text:
                try:
                    selection.replace_text(result_text, prev_clipboard)
                    on_success(result_text)
                except Exception as e:
                    on_error(f"Failed to replace text: {e}")
            else:
                on_error("Action returned empty result.")
        else:
            # Surface quota errors clearly
            error_msg = response.get("error", "Unknown error.")
            if "quota" in error_msg.lower():
                on_error("Monthly writing limit reached. Upgrade to Writing Pro.")
            elif "trial" in error_msg.lower():
                on_error("Trial expired. Please upgrade on the dashboard.")
            else:
                on_error(error_msg)

    threading.Thread(target=_run, daemon=True).start()
