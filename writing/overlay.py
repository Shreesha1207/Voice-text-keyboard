import tkinter as tk
import threading
import queue
from writing.ui.language_menu import show_language_menu
from writing.ui.action_menu import show_action_menu
from writing.ui.preview_widget import show_preview_widget

BG_COLOR = "#2B1B17"
TEXT_COLOR = "#E8B89C"
HOVER_COLOR = "#3B2620"

class OverlayManager:
    def __init__(self, engine):
        self.engine = engine
        self.cmd_queue = queue.Queue()
        self.root = None
        self.btn_window = None
        
        # Start the Tkinter UI thread
        self.ui_thread = threading.Thread(target=self._ui_loop, daemon=True)
        self.ui_thread.start()

    def _ui_loop(self):
        import logging
        import ctypes
        import sys
        
        logging.info("Overlay UI thread starting...")
        
        # Ensure Tkinter uses the same coordinate system as pynput on high-DPI displays
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
                logging.info("SetProcessDpiAwareness succeeded.")
            except Exception as e:
                logging.warning(f"SetProcessDpiAwareness failed: {e}")
                
        try:
            self.root = tk.Tk()
            self.root.withdraw() # Hide the main root window completely
            logging.info("Tkinter root initialized and hidden.")
            
            # Poll the queue for commands from other threads
            def check_queue():
                try:
                    while True:
                        cmd, args = self.cmd_queue.get_nowait()
                        logging.info(f"UI thread received command: {cmd}")
                        if cmd == 'show_btn':
                            self._do_show_button(*args)
                        elif cmd == 'hide_btn':
                            self._do_hide_button()
                        elif cmd == 'left_click':
                            # Check if click is inside the button before hiding
                            if self.btn_window is not None:
                                bx = self.btn_window.winfo_rootx()
                                by = self.btn_window.winfo_rooty()
                                bw = self.btn_window.winfo_width()
                                bh = self.btn_window.winfo_height()
                                cx, cy = args[0], args[1]
                                if not (bx <= cx <= bx + bw and by <= cy <= by + bh):
                                    self._do_hide_button()
                        elif cmd == 'show_action':
                            # New: show the full writing action picker
                            self._do_hide_button()
                            x, y, selected_text, prev_clipboard = args
                            show_action_menu(
                                self.root, x, y,
                                lambda action, lang, st=selected_text, pc=prev_clipboard:
                                    self.engine.trigger_action(action, lang, st, pc)
                            )
                        elif cmd == 'show_lang':
                            # Legacy path kept for compatibility
                            self._do_hide_button()
                            show_language_menu(self.root, args[0], args[1], 
                                lambda lang: self.engine.trigger_action("translate", lang, args[2], args[3]))
                        elif cmd == 'show_toast':
                            self._do_show_toast(*args)
                        elif cmd == 'show_preview':
                            # VS Code-style preview: show result, wait for Accept/Dismiss
                            x, y, action, original_text, result_text, prev_clipboard = args
                            from writing import selection as sel
                            def _accept(rt=result_text, pc=prev_clipboard):
                                try:
                                    sel.replace_text(rt, pc)
                                    self._do_show_toast((f"✓ Applied", True, 1800))
                                except Exception as exc:
                                    import logging
                                    logging.error(f"Preview accept replace_text failed: {exc}")
                            def _dismiss():
                                pass  # original text stays untouched
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
                    logging.error(f"Error in check_queue: {e}")
                
                self.root.after(50, check_queue)
                
            logging.info("Scheduling check_queue and starting mainloop.")
            self.root.after(50, check_queue)
            self.root.mainloop()
            logging.info("Tkinter mainloop exited naturally.")
        except Exception as e:
            logging.error(f"UI thread crashed: {e}")
            print(f"UI thread crashed: {e}")


    def show_xvoice_button(self, x: int, y: int):
        self.cmd_queue.put(('show_btn', (x, y)))

    def on_left_click(self, x: int, y: int):
        self.cmd_queue.put(('left_click', (x, y)))

    def hide_all(self):
        self.cmd_queue.put(('hide_btn', ()))

    def _do_show_button(self, x: int, y: int):
        import logging
        logging.info(f"_do_show_button called with x={x}, y={y}")
        try:
            if self.btn_window is not None:
                logging.info("Destroying old btn_window")
                self.btn_window.destroy()
                
            self.btn_window = tk.Toplevel(self.root)
            self.btn_window.overrideredirect(True)
            self.btn_window.attributes('-topmost', True)
            self.btn_window.configure(bg=BG_COLOR)
            logging.info("btn_window created and configured")
            
            offset_x = x + 15
            offset_y = y - 30
            
            # Clamp to screen to prevent off-screen rendering
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            logging.info(f"Screen dimensions: {screen_w}x{screen_h}")
            
            # Estimate button size
            btn_w, btn_h = 100, 40
            if offset_x + btn_w > screen_w: offset_x = screen_w - btn_w
            if offset_y + btn_h > screen_h: offset_y = screen_h - btn_h
            if offset_x < 0: offset_x = 0
            if offset_y < 0: offset_y = 0
            
            geom = f"+{int(offset_x)}+{int(offset_y)}"
            logging.info(f"Setting window geometry to {geom}")
            self.btn_window.geometry(geom)
            
            # Force window to top
            self.btn_window.deiconify()
            self.btn_window.lift()
            self.btn_window.attributes('-topmost', True)
            self.btn_window.update_idletasks()
            
            btn = tk.Label(
                self.btn_window,
                text="🌐 Xvoice", 
                bg=BG_COLOR, 
                fg=TEXT_COLOR, 
                font=("Segoe UI", 10, "bold"),
                padx=10,
                pady=5,
                cursor="hand2"
            )
            btn.pack()
            
            def on_enter(e):
                btn.config(bg=HOVER_COLOR)
            def on_leave(e):
                btn.config(bg=BG_COLOR)
            def on_click(e):
                self.engine.on_xvoice_click(offset_x, offset_y)
                
            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.bind("<Button-1>", on_click)
            
            logging.info("_do_show_button completed successfully")
            
            # Auto hide
            self.btn_window.after(4000, self._do_hide_button)
        except Exception as e:
            logging.error(f"Error in _do_show_button: {e}")
    def _do_hide_button(self):
        if self.btn_window:
            self.btn_window.destroy()
            self.btn_window = None

    def _do_show_toast(self, message: str, success: bool, duration: int):
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes('-topmost', True)
        
        color = "#2e7d32" if success else "#c62828" # Green or Red
        if message == "Translating...":
            color = "#1565c0" # Blue for loading
            
        toast.configure(bg=color)
        
        lbl = tk.Label(toast, text=message, bg=color, fg="white", font=("Segoe UI", 10, "bold"), padx=15, pady=10)
        lbl.pack()
        
        # Position bottom right
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        
        # Wait for widget to compute size
        toast.update_idletasks()
        w = toast.winfo_width()
        h = toast.winfo_height()
        
        toast.geometry(f"+{screen_w - w - 20}+{screen_h - h - 60}")
        
        toast.after(duration, toast.destroy)

