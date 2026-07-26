from typing import Optional, Tuple
from writing import clipboard
import time

def get_selected_text_and_restore() -> Tuple[Optional[str], Optional[str]]:
    """
    Attempts to get the selected text by simulating a copy operation.
    Saves and restores the previous clipboard content so it isn't lost.
    """
    prev_clipboard = clipboard.get_clipboard()
    
    # Clear clipboard to detect if copy succeeded
    clipboard.set_clipboard("")
    
    # Simulate copy
    copied_text = clipboard.copy_selection()
    
    # Immediately restore the previous clipboard so system clipboard isn't polluted while user views preview
    if prev_clipboard is not None:
        clipboard.set_clipboard(prev_clipboard)
    
    # If text is empty, nothing was selected
    if not copied_text or copied_text.strip() == "":
        return None, prev_clipboard
        
    return copied_text, prev_clipboard

def replace_text(new_text: str, prev_clipboard_content: str):
    """
    Replaces the currently selected text with new_text.
    After the paste lands, the clipboard is restored or cleared so the AI-generated text does not linger.
    """
    # 1. Put the new text in the clipboard
    clipboard.set_clipboard(new_text)
    
    # 2. Paste the text over the selection
    clipboard.paste_selection()
    
    # 3. Give the OS a moment to process the paste, then restore the old clipboard
    time.sleep(0.15)
    if prev_clipboard_content is not None:
        clipboard.set_clipboard(prev_clipboard_content)
    else:
        clipboard.set_clipboard("")
