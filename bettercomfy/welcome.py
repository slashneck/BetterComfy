"""The first start: finds ComfyUI (or lets you pick its folder) and says what Better Comfy needs."""
import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import comfy, icons, theme as T
from .config import APP_NAME, cfg, resource
from .widgets import Card, ToggleRow, button, hrow, label


class Welcome(QDialog):
    _found = Signal(list)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(APP_NAME)
        self.setModal(True)
        self.resize(640, 10)
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 28, 30, 24)
        v.setSpacing(14)
        logo = QLabel()
        pm = QPixmap(resource("assets", "icon.png")).scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(2.0)
        logo.setPixmap(pm)
        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(16)
        hh.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Welcome to Better Comfy", "H1"))
        col.addWidget(label("Pictures and videos with ComfyUI, without wiring nodes.", "Muted"))
        hh.addLayout(col, 1)
        v.addWidget(head)

        self.card = Card("ComfyUI", margins=(18, 14, 18, 16))
        self.status = label("Looking for ComfyUI on this PC…", "H3", wrap=True)
        self.detail = label("", "Faint", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pick_btn = button("Choose my ComfyUI folder…", self._pick, "Accent", "folder")
        self.search_btn = button("Search all drives", self._search, "Ghost", "search")
        self.card.add(self.status, self.detail, hrow(self.pick_btn, self.search_btn, None, spacing=6))
        v.addWidget(self.card)
        self.help = label("No ComfyUI yet? The easiest way is Pinokio: install it, open Discover and install ComfyUI "
                          "with one click. The portable ComfyUI and ComfyUI Desktop work too. Better Comfy then finds it "
                          "by itself, or you pick its folder here (the one with main.py, or the folder around it).",
                          "Muted", wrap=True)
        v.addWidget(self.help)
        self.updates = ToggleRow("Look for new versions", "Asks GitHub once in a while. That is the only thing "
                                 "Better Comfy does online. Everything you make stays on this PC.",
                                 bool(cfg.get("check_updates")), None)
        v.addWidget(self.updates)
        self.start_btn = button("Start", self._done, "Accent", "arrow")
        self.start_btn.setMinimumWidth(130)
        v.addWidget(hrow(None, self.start_btn))
        self._found.connect(self._show)
        QTimer.singleShot(50, lambda: self._search(deep=False))

    def _search(self, deep=True):
        self.status.setText("Looking for ComfyUI on this PC…" if not deep else "Searching all drives…")
        self.detail.setText("")
        self.search_btn.setEnabled(False)

        def work():
            self._found.emit(comfy.find_installs(cfg.get("comfy_folder") or None, deep=deep))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, found):
        self.search_btn.setEnabled(True)
        if found:
            ins = found[0]
            kind = {"pinokio": "Pinokio", "portable": "the portable ComfyUI", "desktop": "ComfyUI Desktop"}.get(
                ins["kind"], "a ComfyUI folder")
            self.status.setText(f"✓  Found ComfyUI {ins.get('version', '')} from {kind}")
            self.status.setStyleSheet(f"color: {T.GOOD};")
            extra = f"\n{len(found) - 1} more found - pick another one in Settings." if len(found) > 1 else ""
            self.detail.setText(ins["path"] + extra)
            self.pick_btn.setText("Use another folder…")
            self.pick_btn.setObjectName("Ghost")
            self.pick_btn.setIcon(icons.icon("folder", "#A1A1AA"))
            self.pick_btn.style().unpolish(self.pick_btn)
            self.pick_btn.style().polish(self.pick_btn)
            self.search_btn.hide()
            self.help.hide()
        else:
            self.status.setText("No ComfyUI found yet")
            self.status.setStyleSheet(f"color: {T.WARN};")
            self.detail.setText("Pick the folder you installed it to, or search all drives.")
        self.adjustSize()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Your ComfyUI folder (the one with main.py, or the folder around it)")
        if not d:
            return
        it = comfy.install_from_pick(d) or comfy.install_from_pick(os.path.join(d, "ComfyUI_windows_portable"))
        if it is None:
            self.status.setText("There is no ComfyUI in that folder")
            self.status.setStyleSheet(f"color: {T.BAD};")
            self.detail.setText(d + "\nPick the folder that has main.py in it (or the one around it).")
            return
        cfg.set("comfy_folder", it["path"])
        self._show([it])

    def _done(self):
        cfg.set("check_updates", self.updates.isChecked())
        cfg.set("welcomed", True)
        self.accept()

    def reject(self):
        self._done()
