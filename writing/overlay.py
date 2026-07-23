"""
Xvoice Writing overlay manager (PySide6).

Owns a QApplication running inside a dedicated daemon thread (the app's main
thread is held by the system-tray loop, exactly as with the previous Tk
implementation). A QTimer drains ``self.cmd_queue`` every 50 ms and dispatches
UI commands on the Qt thread, so callers on other threads (the pynput mouse
listener, the writing action worker threads) keep using the same thread-safe
``cmd_queue.put(...)`` API — no changes needed outside this package.
"""
import queue
import logging
import threading

from writing.ui.qt_helpers import (
    FramelessPopup, screen_geometry_at,
    BG_COLOR, TEXT_COLOR, HOVER_COLOR, FONT_FAMILY,
)

logger = logging.getLogger(__name__)


class OverlayManager:
    def __init__(self, engine):
        self.engine = engine
        self.cmd_queue = queue.Queue()
        self._app = None
        self._timer = None
        self.btn = None            # floating "✦ Xvoice" button popup
        self._btn_pos = (0, 0)     # clamped top-left of the button, for the click callback

        self.ui_thread = threading.Thread(target=self._ui_loop, daemon=True)
        self.ui_thread.start()

    # ── Qt event loop (runs in ui_thread) ────────────────────────────────────
    def _ui_loop(self):
        import sys
        import ctypes

        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QTimer

            self._app = QApplication.instance() or QApplication(sys.argv)
            self._app.setQuitOnLastWindowClosed(False)

            self._timer = QTimer()
            self._timer.timeout.connect(self._check_queue)
            self._timer.start(50)

            self._app.exec()
        except Exception as e:
            logger.error(f"Overlay UI thread crashed: {e}")

    def _check_queue(self):
        try:
            while True:
                cmd, args = self.cmd_queue.get_nowait()
                try:
                    self._dispatch(cmd, args)
                except Exception as e:
                    logger.error(f"Overlay command '{cmd}' failed: {e}")
        except queue.Empty:
            pass

    def _dispatch(self, cmd, args):
        if cmd == "show_btn":
            self._do_show_button(*args)
        elif cmd == "hide_btn":
            self._do_hide_button()
        elif cmd == "left_click":
            if self.btn is not None and not self.btn.contains_global(args[0], args[1]):
                self._do_hide_button()
        elif cmd == "show_action":
            self._do_hide_button()
            x, y, selected_text, prev_clipboard = args
            from writing.ui.action_menu import show_action_menu
            show_action_menu(
                x, y,
                lambda action, lang, st=selected_text, pc=prev_clipboard:
                    self.engine.trigger_action(action, lang, st, pc),
            )
        elif cmd == "show_lang":
            self._do_hide_button()
            x, y, selected_text, prev_clipboard = args
            from writing.ui.language_menu import show_language_menu
            show_language_menu(
                x, y,
                lambda lang: self.engine.trigger_action("translate", lang, selected_text, prev_clipboard),
            )
        elif cmd == "show_toast":
            self._do_show_toast(*args)
        elif cmd == "auto_replace":
            result_text, prev_clipboard, action_label = args
            from writing import selection as sel
            try:
                sel.replace_text(result_text, prev_clipboard)
                self._do_show_toast(f"✓ {action_label}", True, 1800)
            except Exception as exc:
                logger.error(f"Auto-replace failed: {exc}")
                self._do_show_toast("✗ Replace failed", False, 2000)
        elif cmd == "show_preview":
            x, y, action, original_text, result_text, prev_clipboard = args
            from writing import selection as sel
            from writing.ui.preview_widget import show_preview_widget

            def _accept(rt=result_text, pc=prev_clipboard):
                try:
                    sel.replace_text(rt, pc)
                    self._do_show_toast("✓ Applied", True, 1800)
                except Exception as exc:
                    logger.error(f"Preview accept failed: {exc}")

            show_preview_widget(
                x, y,
                action=action,
                original_text=original_text,
                result_text=result_text,
                on_accept=_accept,
                on_dismiss=lambda: None,
            )

    # ── Public API (called from other threads) ───────────────────────────────
    def show_xvoice_button(self, x: int, y: int):
        self.cmd_queue.put(("show_btn", (x, y)))

    def on_left_click(self, x: int, y: int):
        self.cmd_queue.put(("left_click", (x, y)))

    def hide_all(self):
        self.cmd_queue.put(("hide_btn", ()))

    # ── Floating "✦ Xvoice" button ───────────────────────────────────────────
    def _do_show_button(self, x: int, y: int):
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtCore import Qt, QTimer

        self._do_hide_button()

        btn_w, btn_h = 112, 44
        offset_x, offset_y = x + 15, y - 30
        rect = screen_geometry_at(x, y)
        if offset_x + btn_w > rect.right():
            offset_x = rect.right() - btn_w
        if offset_y + btn_h > rect.bottom():
            offset_y = rect.bottom() - btn_h
        offset_x = max(offset_x, rect.left())
        offset_y = max(offset_y, rect.top())
        self._btn_pos = (offset_x, offset_y)

        popup = FramelessPopup(radius=18)
        popup.body.setContentsMargins(0, 0, 0, 0)

        button = QPushButton("✦ Xvoice")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(
            f"""
            QPushButton {{
                background: {BG_COLOR}; color: {TEXT_COLOR};
                border: none; border-radius: 16px;
                font-family: '{FONT_FAMILY}'; font-size: 13px; font-weight: 600;
                padding: 8px 18px;
            }}
            QPushButton:hover {{ background: {HOVER_COLOR}; }}
            """
        )
        ox, oy = offset_x, offset_y
        button.clicked.connect(lambda _=False: self.engine.on_xvoice_click(ox, oy))
        popup.body.addWidget(button)

        popup.show_at(offset_x, offset_y, clamp=False)
        self.btn = popup
        QTimer.singleShot(4000, self._do_hide_button)

    def _do_hide_button(self):
        if self.btn is not None:
            try:
                self.btn.close()
            except Exception:
                pass
            self.btn = None

    # ── Toast notifications (bottom-right) ────────────────────────────────────
    def _do_show_toast(self, message: str, success: bool, duration: int):
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import Qt, QTimer

        if "⏳" in message:
            color = "#1565C0"
        elif success:
            color = "#2E7D32"
        else:
            color = "#C62828"

        toast = FramelessPopup(radius=10, bg=color, border_rgba="rgba(255,255,255,0.18)")
        toast.body.setContentsMargins(0, 0, 0, 0)

        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            f"color: white; font-family: '{FONT_FAMILY}'; font-size: 13px; "
            f"font-weight: 600; padding: 10px 16px;"
        )
        toast.body.addWidget(label)
        toast.adjustSize()

        # Position at the bottom-right of the screen under the cursor.
        from PySide6.QtGui import QCursor
        cur = QCursor.pos()
        area = screen_geometry_at(cur.x(), cur.y())
        w, h = toast.width(), toast.height()
        toast.show_at(area.right() - w + 14, area.bottom() - h - 26, clamp=False)
        QTimer.singleShot(duration, toast.close)
