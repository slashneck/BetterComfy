"""Looks for updates in the background while Better Comfy runs (see updater.py for the work itself)."""
import threading
import time
import urllib.error

from PySide6.QtCore import QObject, QTimer, Signal

from .config import cfg
from .updater import CHECK_EVERY, can_self_update, check, download, start_install


class UpdateService(QObject):
    """Asks GitHub for a new version every few hours, downloads it in the background and installs it when you say so
    (or when Better Comfy closes) - never in the middle of a generation."""
    changed = Signal()
    _done = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.state = "idle"            # idle | checking | latest | available | downloading | ready | error
        self.info, self.staged, self.error, self.progress = None, None, "", 0.0
        self._busy = False
        self._last = 0.0
        self._done.connect(self._finish)
        self.timer = QTimer(self, interval=10 * 60 * 1000, timeout=self._tick)

    def begin(self):
        self.timer.start()
        QTimer.singleShot(45 * 1000, self._tick)

    def _tick(self):
        if cfg.get("check_updates") and not self.staged and time.time() - self._last >= CHECK_EVERY:
            self.check()

    def check(self, fetch=True):
        if self._busy or self.staged:
            return
        self._busy = True
        self._last = time.time()
        self._set("checking")

        def work():
            try:
                info = check()
                if info is None:
                    self._done.emit("latest", None)
                    return
                if not fetch or not can_self_update():
                    self._done.emit("available", info)
                    return
                self.info = info
                self._set_async("downloading")
                staged = download(info, self._prog)
                self._done.emit("ready", (info, staged))
            except Exception as ex:
                msg = "GitHub couldn't be reached" if isinstance(ex, (urllib.error.URLError, OSError)) and \
                    not isinstance(ex, RuntimeError) else str(ex)
                self._done.emit("error", msg)
        threading.Thread(target=work, daemon=True).start()

    def _prog(self, f):
        if f - self.progress >= 0.02 or f >= 1:
            self.progress = f
            self._set_async("downloading")

    def _set_async(self, st):
        self._done.emit("_" + st, None)

    def _finish(self, st, data):
        if st.startswith("_"):
            self.state = st[1:]
            self.changed.emit()
            return
        self._busy = False
        if st == "ready":
            self.info, self.staged = data
        elif st == "available":
            self.info = data
        elif st == "error":
            self.error = data
        self._set(st)

    def _set(self, st):
        self.state = st
        if st != "error":
            self.error = ""
        self.changed.emit()

    def install(self, restart=True):
        if self.staged:
            start_install(self.staged, restart)
            return True
        return False


