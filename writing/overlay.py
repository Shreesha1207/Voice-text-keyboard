import tkinter as tk
import threading
import queue
import logging
from writing.ui.language_menu import show_language_menu
from writing.ui.action_menu import show_action_menu
from writing.ui.preview_widget import show_preview_widget

logger = logging.getLogger(__name__)

BG_COLOR    = "#2B1B17"
TEXT_COLOR  = "#E8B89C"
HOVER_COLOR = "#3B2620"

class OverlayManager:
    def __init__(self, engine):
        self.engine = engine
        self.cmd_queue = queue.Queue()
        self.root = None
        self.btn_window = None

        self.ui_thread = threading.Thread(target=self._ui_loop, daemon=True)
        self.ui_thread.start()

    def _ui_loop(self):
        import ctypes
        import sys

        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        try:
            self.root = tk.Tk()
            self.root.withdraw()

            def check_queue():
                try:
                    while True:
                        cmd, args = self.cmd_queue.get_nowait()
                        if cmd == 'show_btn':
                            self._do_show_button(*args)
                        elif cmd == 'hide_btn':
                            self._do_hide_button()
                        elif cmd == 'left_click':
                            if self.btn_window is not None:
                                bx = self.btn_window.winfo_rootx()
                                by = self.btn_window.winfo_rooty()
                                bw = self.btn_window.winfo_width()
                                bh = self.btn_window.winfo_height()
                                cx, cy = args[0], args[1]
                                if not (bx <= cx <= bx + bw and by <= cy <= by + bh):
                                    self._do_hide_button()
                        elif cmd == 'show_action':
                            self._do_hide_button()
                            x, y, selected_text, prev_clipboard = args
                            show_action_menu(
                                self.root, x, y,
                                lambda action, lang, st=selected_text, pc=prev_clipboard:
                                    self.engine.trigger_action(action, lang, st, pc)
                            )
                        elif cmd == 'show_lang':
                            self._do_hide_button()
                            show_language_menu(self.root, args[0], args[1],
                                lambda lang: self.engine.trigger_action("translate", lang, args[2], args[3]))
                        elif cmd == 'show_toast':
                            self._do_show_toast(*args)
                        elif cmd == 'auto_replace':
                            # User has auto_replace enabled — apply result immediately
                            result_text, prev_clipboard, action_label = args
                            from writing import selection as sel
                            try:
                                sel.replace_text(result_text, prev_clipboard)
                                self._do_show_toast(f"✓ {action_label}", True, 1800)
                            except Exception as exc:
                                logger.error(f"Auto-replace failed: {exc}")
                                self._do_show_toast("✗ Replace failed", False, 2000)
                        elif cmd == 'show_preview':
                            x, y, action, original_text, result_text, prev_clipboard = args
                            from writing import selection as sel
                            def _accept(rt=result_text, pc=prev_clipboard):
                                try:
                                    sel.replace_text(rt, pc)
                                    self._do_show_toast("✓ Applied", True, 1800)
                                except Exception as exc:
                                    logger.error(f"Preview accept failed: {exc}")
                            def _dismiss():
                                pass
                            show_preview_widget(
                                self.root, x, y,
                                action=action,
                                original_text=original_text,
                                result_text=result_text,
                                on_accept=_accept,
                                on_dismiss=_dismiss,
                            )
                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"check_queue error: {e}")

                self.root.after(50, check_queue)

            self.root.after(50, check_queue)
            self.root.mainloop()
        except Exception as e:
            logger.error(f"Overlay UI thread crashed: {e}")

    def show_xvoice_button(self, x: int, y: int):
        self.cmd_queue.put(('show_btn', (x, y)))

    def on_left_click(self, x: int, y: int):
        self.cmd_queue.put(('left_click', (x, y)))

    def hide_all(self):
        self.cmd_queue.put(('hide_btn', ()))

    def _do_show_button(self, x: int, y: int):
        try:
            if self.btn_window is not None:
                self.btn_window.destroy()

            self.btn_window = tk.Toplevel(self.root)
            self.btn_window.overrideredirect(True)
            self.btn_window.attributes('-topmost', True)
            self.btn_window.configure(bg=BG_COLOR)

            offset_x = x + 15
            offset_y = y - 30

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            btn_w, btn_h = 100, 40
            if offset_x + btn_w > screen_w: offset_x = screen_w - btn_w
            if offset_y + btn_h > screen_h: offset_y = screen_h - btn_h
            if offset_x < 0: offset_x = 0
            if offset_y < 0: offset_y = 0

            self.btn_window.geometry(f"+{int(offset_x)}+{int(offset_y)}")
            self.btn_window.deiconify()
            self.btn_window.lift()
            self.btn_window.attributes('-topmost', True)
            self.btn_window.update_idletasks()

            btn = tk.Label(
                self.btn_window,
                text="✦ Xvoice",
                bg=BG_COLOR,
                fg=TEXT_COLOR,
                font=("Segoe UI", 10, "bold"),
                padx=10,
                pady=5,
                cursor="hand2"
            )
            btn.pack()

            btn.bind("<Enter>",    lambda e: btn.config(bg=HOVER_COLOR))
            btn.bind("<Leave>",    lambda e: btn.config(bg=BG_COLOR))
            btn.bind("<Button-1>", lambda e: self.engine.on_xvoice_click(offset_x, offset_y))

            self.btn_window.after(4000, self._do_hide_button)
        except Exception as e:
            logger.error(f"Error showing Xvoice button: {e}")

    def _do_hide_button(self):
        if self.btn_window:
            self.btn_window.destroy()
            self.btn_window = None

    def _do_show_toast(self, message: str, success: bool, duration: int):
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes('-topmost', True)

        color = "#2e7d32" if success else "#c62828"
        if "⏳" in message:
            color = "#1565c0"
        toast.configure(bg=color)

        lbl = tk.Label(
            toast, text=message,
            bg=color, fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=15, pady=10
        )
        lbl.pack()

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        toast.update_idletasks()
        w = toast.winfo_width()
        h = toast.winfo_height()
        toast.geometry(f"+{screen_w - w - 20}+{screen_h - h - 60}")

        toast.after(duration, toast.destroy)
