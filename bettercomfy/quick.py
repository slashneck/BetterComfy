"""Small windows of the app: quick search (Ctrl+K), the keyboard shortcuts (F1) and the connection log."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import ckpts, charlib, icons, loras, netlog, theme as T
from .config import cfg
from .widgets import Card, ToggleRow, button, hrow, label, nice_name

_ROW = Qt.ItemDataRole.UserRole


class QuickSearch(QDialog):
    """Type a few letters: any tab, setting, model, LoRA or character; Enter goes there."""

    def __init__(self, app):
        super().__init__(app, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.app = app
        self.setObjectName("Quick")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#Quick {{ background: {T.SURFACE}; border: 1px solid {T.BORDER_HI}; border-radius: 12px; }}")
        self.setFixedWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Go to a tab, setting, model, LoRA or character…")
        self.edit.textChanged.connect(self._fill)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.setFixedHeight(340)
        self.list.itemClicked.connect(lambda _it: self._go())
        v.addWidget(self.edit)
        v.addWidget(self.list)
        self.rows = self._collect()
        self._fill("")
        g = app.geometry()
        self.adjustSize()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)
        self.edit.setFocus()

    def showEvent(self, e):
        super().showEvent(e)
        self.activateWindow()
        self.edit.setFocus()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.reject()               # clicked somewhere else

    def _collect(self):
        a = self.app
        rows = []
        from .app import GROUPS, BOTTOM
        for key, text, ic in [it for _g, items in GROUPS for it in items] + BOTTOM:
            rows.append((text, "Tab", ic, lambda k=key: a.go(k)))
        settings = a.pages["settings"]
        for c in settings.findChildren(Card):
            title = getattr(c, "title", None)
            if title is not None:
                name = title.text().capitalize()
                rows.append((name, "Settings", "settings", lambda c=c: self._to_setting(c)))
                for row in c.findChildren(ToggleRow):
                    rows.append((row.text.text(), f"Settings, {name}", "settings", lambda c=row: self._to_setting(c)))
        hide = cfg.get("lora_blur_nsfw", False)
        for name, src, _kind in a.link.picture_models():
            if not (hide and ckpts.is_nsfw(ckpts.key(src, name))):
                rows.append((nice_name(name), "Model: use on Image", "chip", lambda n=name, s=src: a.pages["image"].use_model(n, s)))
        for name in a.link.lists.get("loras") or []:
            if not (hide and loras.is_nsfw(name)):
                rows.append((nice_name(name), "LoRA", "lora", lambda n=name: self._to_lora(n)))
        for c in charlib.all_characters():
            rows.append((c["name"], "Character: use on Image", "person", lambda i=c["id"]: self._to_char(i)))
        return rows

    def _to_setting(self, w):
        self.app.go("settings")
        area = self.app.pages["settings"].findChild(QScrollArea)
        if area is not None:
            QTimer.singleShot(320, lambda: area.ensureWidgetVisible(w, 0, 80))

    def _to_lora(self, name):
        self.app.go("loras")
        self.app.pages["loras"].show_name(name)

    def _to_char(self, cid):
        self.app.pages["image"].pick_character(cid)
        self.app.go("image")

    def _gallery(self, text):
        self.app.go("gallery")
        self.app.pages["gallery"].search.setText(text)

    def _fill(self, text):
        words = text.lower().split()
        self.list.clear()
        shown = 0
        for name, where, ic, fn in self.rows:
            hay = f"{name} {where}".lower()
            if all(w in hay for w in words):
                it = QListWidgetItem(icons.icon(ic, "#8A8A93", 16), f"{name}     {where}")
                it.setData(_ROW, fn)
                self.list.addItem(it)
                shown += 1
                if shown >= 60:
                    break
        if text.strip():
            it = QListWidgetItem(icons.icon("gallery", "#8A8A93", 16), f"Search the gallery for \"{text.strip()}\"")
            it.setData(_ROW, lambda t=text.strip(): self._gallery(t))
            self.list.addItem(it)
        self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == e.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._go()
            return True
        if obj is self.edit and e.type() == e.Type.KeyPress and e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            step = 1 if e.key() == Qt.Key.Key_Down else -1
            n = self.list.count()
            if n:
                self.list.setCurrentRow((self.list.currentRow() + step) % n)
            return True
        return super().eventFilter(obj, e)

    def _go(self):
        it = self.list.currentItem()
        if it is not None:
            fn = it.data(_ROW)
            self.accept()
            fn()


SHORTCUTS = (
    ("Everywhere", (("Ctrl+K", "Quick search"), ("F1", "These shortcuts"), ("Ctrl+1 to Ctrl+9", "Go to a tab"),
                    ("Panic key", "Hides the app and locks the vault. Set it in Settings, Privacy"))),
    ("Prompt", (("Ctrl+Enter", "Generate"), ("Ctrl+Up / Down", "Weight the word under the cursor"),
                ("{a|b}", "One of them is picked for each picture"))),
    ("Gallery", (("F", "Favourite"), ("X", "Mark for deletion"), ("Delete", "Delete"), ("Enter", "View big"))),
    ("Big view", (("Left / Right, A / D", "The one before / after"), ("Mouse wheel", "Zoom"), ("0", "Fit to the window"),
                  ("+ / -", "Zoom in / out"), ("F", "Favourite"), ("X", "Mark for deletion"), ("E", "Edit"),
                  ("S", "Slideshow"), ("Space", "Play / pause"), (", / .", "One frame back / on"), ("M", "Sound on / off"),
                  ("Delete", "Delete"), ("Esc", "Close"))),
)


class Shortcuts(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        v.addWidget(label("Keyboard shortcuts", "H2"))
        row = QHBoxLayout()
        row.setSpacing(28)
        for i in range(2):
            col = QVBoxLayout()
            col.setSpacing(14)
            for title, items in SHORTCUTS[i * 2:i * 2 + 2]:
                col.addWidget(label(title.upper(), "CardTitle"))
                g = QGridLayout()
                g.setHorizontalSpacing(16)
                g.setVerticalSpacing(6)
                for r, (keys, what) in enumerate(items):
                    k = label(keys, "Mono")
                    g.addWidget(k, r, 0)
                    g.addWidget(label(what, "Muted"), r, 1)
                g.setColumnStretch(1, 1)
                col.addLayout(g)
            col.addStretch(1)
            row.addLayout(col, 1)
        v.addLayout(row)
        v.addWidget(hrow(None, button("Close", self.accept, "Ghost")))
        self.resize(720, 10)


class ConnectionLog(QDialog):
    """Everything Better Comfy connected to since it opened."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Connection log")
        self.resize(720, 480)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(label("Connection log", "H2"))
        self.summary = label("", "Muted", wrap=True)
        v.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(["What", "Address", "Times", "Last"])
        self.tree.setColumnWidth(0, 170)
        self.tree.setColumnWidth(1, 300)
        self.tree.setColumnWidth(2, 60)
        v.addWidget(self.tree, 1)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T.BORDER};")
        v.addWidget(line)
        v.addWidget(label("Everything Better Comfy itself connected to since it opened. This list is kept in memory and "
                          "is gone when the app closes. ComfyUI is a separate program, so what it or its add-ons do is "
                          "not shown here.", "Faint", wrap=True))
        v.addWidget(hrow(None, button("Close", self.accept, "Ghost")))
        self._fill()
        self.timer = QTimer(self, interval=1500, timeout=self._fill)
        self.timer.start()

    def _fill(self):
        rows = netlog.rows()
        online = [r for r in rows if not r["local"]]
        self.summary.setText("Only this PC so far: nothing went online." if not online else
                             f"{len(online)} address{'es' if len(online) != 1 else ''} online, the rest on this PC.")
        self.tree.clear()
        for r in rows:
            it = QTreeWidgetItem([netlog.what(r), f"{r['host']}:{r['port']}", str(r["times"]),
                                  time.strftime("%H:%M:%S", time.localtime(r["last"]))])
            self.tree.addTopLevelItem(it)
