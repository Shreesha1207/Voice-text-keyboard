from typing import Optional, Tuple
from writing import clipboard
import time

def get_selected_text_and_restore() -> Tuple[Optional[str], Optional[str]]:
    """
    Attempts to get the selected text by simulating a copy operation.
    It saves the previous clipboard, copies the selection, and then we return the text
    and the previous clipboard content so it can be restored later.
    """
    prev_clipboard = clipboard.get_clipboard()
    
    # We clear the clipboard first so we can detect if the copy actually did anything 
    # (e.g. if no text is selected)
    clipboard.set_clipboard("")
    
    # Simulate copy
    copied_text = clipboard.copy_selection()
    
    # If the text is empty, nothing was selected
    if not copied_text or copied_text.strip() == "":
        clipboard.set_clipboard(prev_clipboard)
        return None, prev_clipboard
        
    return copied_text, prev_clipboard

def replace_text(new_text: str, prev_clipboard_content: str):
    """
    Replaces the currently selected text with new_text.
    """
    # 1. Put the new text in the clipboard
    clipboard.set_clipboard(new_text)
    
    # 2. Paste the text over the selection
    clipboard.paste_selection()
    
    # 3. Give the OS a moment to process the paste, then restore the old clipboard
    time.sleep(0.15)
    if prev_clipboard_content is not None:
        clipboard.set_clipboard(prev_clipboard_content)
