import tkinter as tk
import threading
import queue
from writing.ui.language_menu import show_language_menu

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
        self.root = tk.Tk()
        self.root.withdraw() # Hide the main root window completely
        
        # Poll the queue for commands from other threads
        def check_queue():
            try:
                while True:
                    cmd, args = self.cmd_queue.get_nowait()
                    if cmd == 'show_btn':
                        self._do_show_button(*args)
                    elif cmd == 'hide_btn':
                        self._do_hide_button()
                    elif cmd == 'show_lang':
                        self._do_hide_button()
                        show_language_menu(self.root, args[0], args[1], self.engine.trigger_translation)
                    elif cmd == 'show_toast':
                        self._do_show_toast(*args)
            except queue.Empty:
                pass
            self.root.after(50, check_queue)
            
        self.root.after(50, check_queue)
        self.root.mainloop()

    def show_xvoice_button(self, x: int, y: int):
        self.cmd_queue.put(('show_btn', (x, y)))

    def on_left_click(self, x: int, y: int):
        self.cmd_queue.put(('hide_btn', ()))

    def hide_all(self):
        self.cmd_queue.put(('hide_btn', ()))

    def _do_show_button(self, x: int, y: int):
        if self.btn_window:
            self.btn_window.destroy()
            
        self.btn_window = tk.Toplevel(self.root)
        self.btn_window.overrideredirect(True)
        self.btn_window.attributes('-topmost', True)
        self.btn_window.configure(bg=BG_COLOR)
        
        offset_x = x + 15
        offset_y = y - 30
        self.btn_window.geometry(f"+{offset_x}+{offset_y}")
        
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
            self.cmd_queue.put(('show_lang', (offset_x, offset_y)))

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind("<Button-1>", on_click)
        
        # Auto hide
        self.btn_window.after(4000, self._do_hide_button)

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

