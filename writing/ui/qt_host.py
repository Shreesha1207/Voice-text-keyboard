"""
Shared Qt event-loop host for all Xvoice desktop overlays.

Qt allows only one QApplication per process, and it must be driven from a single
thread. The application's main thread is held by the system-tray loop, so this
host runs the QApplication inside one dedicated daemon thread and lets any other
thread marshal work onto it via ``run_on_ui(fn)``.

Both the always-on "listening" overlay (dictation) and the conditional Writing
overlay share this one host.
"""
from __future__ import annotations

import queue
import logging
import threading

logger = logging.getLogger(__name__)


class QtHost:
    _instance: "QtHost | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._app = None
        self._pump = None
        self._calls: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # ── Singleton ────────────────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "QtHost":
        with cls._lock:
            if cls._instance is None:
                cls._instance = QtHost()
            return cls._instance

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the Qt event loop in a daemon thread. Idempotent."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="XvoiceQtHost", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
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

            # Drain queued callables on the Qt thread ~every 20 ms.
            self._pump = QTimer()
            self._pump.timeout.connect(self._drain)
            self._pump.start(20)

            self._ready.set()
            self._app.exec()
        except Exception as e:
            logger.error(f"QtHost thread crashed: {e}")
            self._ready.set()

    def _drain(self) -> None:
        try:
            while True:
                fn = self._calls.get_nowait()
                try:
                    fn()
                except Exception as e:
                    logger.error(f"QtHost UI callable failed: {e}")
        except queue.Empty:
            pass

    # ── Public API ───────────────────────────────────────────────────────────
    def run_on_ui(self, fn) -> None:
        """Schedule ``fn()`` to run on the Qt thread. Safe to call from any thread."""
        self.start()
        self._calls.put(fn)

    @property
    def app(self):
        return self._app
