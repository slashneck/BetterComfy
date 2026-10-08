"""The GALLERY: everything made, searchable, with its settings - reuse, animate, open, delete."""
import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QVBoxLayout,
                               QWidget, QAbstractItemView)

from . import icons, theme as T
from .components import _Loader
from .widgets import FileList, Player, Segmented, button, hrow, label, nice_name


class GalleryPage(QWidget):
    title = "Gallery"
    subtitle = "Everything you made"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.history = app.history
        self.cur = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.filter = Segmented([("all", "All"), ("image", "Images"), ("video", "Videos")], lambda _v: self.fill(),
                                "all", expand=False, height=34)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search prompts, models, seeds…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._st.start())
        self._st = QTimer(self, singleShot=True, interval=200, timeout=self.fill)
        self.count = label("", "Faint")
        self.size = Segmented([("s", "S"), ("m", "M"), ("l", "L")], self._size, "m", expand=False, height=34)
        lv.addWidget(hrow(self.filter, self.search, self.count, self.size, spacing=10))
        self.grid = FileList()
        self.grid.file_of = lambda eid: (self.history.get(eid) or {}).get("file")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setSpacing(6)
        self.grid.currentItemChanged.connect(lambda cur, _p: cur and self.show(cur.data(Qt.ItemDataRole.UserRole)))
        self.grid.itemDoubleClicked.connect(lambda it: self._view())
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.setStyleSheet("QListWidget::item { padding: 3px; border-radius: 12px; }"
                                "QListWidget::item:selected { background: #222226; }")
        lv.addWidget(self.grid, 1)
        self.empty = label("Nothing here yet - everything you generate shows up here, with its settings.", "Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self.empty, 1)
        h.addWidget(left, 1)

        # details
        side = QWidget()
        side.setObjectName("Panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(380)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 16, 16, 16)
        sv.setSpacing(10)
        self.player = Player()
        self.player.setMinimumSize(300, 300)
        self.player.click_pauses = False
        self.player.setToolTip("Click: view big (zoom, ← →)")
        self.player.clicked.connect(self._view)
        self._shown = []
        self.player.message("Pick something", "Its settings show here.")
        sv.addWidget(self.player, 1)
        self.name = label("", "H3", sel=True)
        self.meta = label("", "Faint", wrap=True, sel=True)
        sv.addWidget(self.name)
        sv.addWidget(self.meta)
        self.prompt = QPlainTextEdit()
        self.prompt.setReadOnly(True)
        self.prompt.setMaximumHeight(120)
        sv.addWidget(self.prompt)
        self.b = {}
        acts = [("reuse", "Reuse settings", "refresh"), ("animate", "Animate", "video"), ("extend", "Extend", "arrow"),
                ("upscale", "Upscale", "scale"), ("inpaint", "Edit", "edit"), ("compare", "Comparison", "gallery"),
                ("copyprompt", "Copy prompt", "copy"), ("folder", "Show in folder", "folder"),
                ("delete", "Delete", "trash")]
        for k, t, ic in acts:
            self.b[k] = button(t, lambda k=k: self._act(k), "Ghost" if k != "reuse" else None, ic)
        sv.addWidget(hrow(self.b["reuse"], self.b["animate"], self.b["extend"], self.b["upscale"], self.b["inpaint"],
                          None, spacing=4))
        sv.addWidget(hrow(self.b["compare"], self.b["copyprompt"], self.b["folder"], None, self.b["delete"], spacing=4))
        self.multi = label("", "Faint")
        self.multi_join = button("Join into one video", lambda: self.app.join_entries(self._selected()), None, "film")
        self.multi_anim = button("Animate all", lambda: self.app.animate_entries(self._selected()), None, "video")
        self.multi_row = hrow(self.multi, None, self.multi_anim, self.multi_join, spacing=6)
        sv.addWidget(self.multi_row)
        self.grid.itemSelectionChanged.connect(self._sel_changed)
        h.addWidget(side)
        self.loader = _Loader()
        self.loader.done.connect(self._loaded)
        self._icons = {}
        self.thumb = 168
        self.history.added.connect(self._changed)
        self.history.removed.connect(self._changed)
        self._dirty = True
        self._enable()

    def _changed(self, *_):
        if self.isVisible():
            self.fill()
        else:
            self._dirty = True

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.fill()

    def _size(self, k):
        self.thumb = {"s": 120, "m": 168, "l": 240}[k]
        self._icons.clear()
        self.fill()

    def _icon(self, e):
        key = (e["id"], self.thumb)
        if key not in self._icons:
            s = self.thumb * 2
            pm = QPixmap(e.get("thumb") or e["file"])
            out = QPixmap(s, s)
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, s, s), 20, 20)
            p.setClipPath(path)
            p.fillRect(out.rect(), QColor(T.SURFACE2))
            if not pm.isNull():
                pm = pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
            if e.get("kind") == "video":
                p.setClipping(False)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 150))
                p.drawRoundedRect(QRectF(14, s - 50, 92, 36), 12, 12)
                p.drawPixmap(QPointF(22, s - 44), icons.pixmap("play", 24, "#FFFFFF"))
                f = p.font()
                f.setPixelSize(22)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(QRectF(50, s - 50, 56, 36), Qt.AlignmentFlag.AlignVCenter, f"{e.get('seconds', '')}s")
            p.end()
            out.setDevicePixelRatio(2.0)
            self._icons[key] = QIcon(out)
        return self._icons[key]

    def fill(self):
        self._dirty = False
        kind = self.filter.value()
        q = self.search.text().strip().lower()
        items = self.history.recent(None if kind == "all" else kind)
        if q:
            items = [e for e in items if q in (e.get("prompt") or "").lower() or q in (e.get("model") or "").lower()
                     or q in str(e.get("seed")) or q in os.path.basename(e["file"]).lower()]
        keep = self.cur["id"] if self.cur else None
        self._shown = items[:3000]
        self.grid.blockSignals(True)
        self.grid.clear()
        self.grid.setIconSize(QSize(self.thumb, self.thumb))
        self.grid.setGridSize(QSize(self.thumb + 14, self.thumb + 14))
        for e in items[:3000]:
            it = QListWidgetItem(self._icon(e), "")
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setToolTip((e.get("prompt") or "")[:300])
            self.grid.addItem(it)
            if e["id"] == keep:
                self.grid.setCurrentItem(it)
        self.grid.blockSignals(False)
        self.count.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")
        self.grid.setVisible(bool(items))
        self.empty.setVisible(not items)
        if self.cur and not self.history.get(self.cur["id"]):
            self.cur = None
            self.player.message("Pick something", "Its settings show here.")
            self.name.setText("")
            self.meta.setText("")
            self.prompt.setPlainText("")
        self._enable()

    def show(self, eid):
        e = self.history.get(eid)
        if e is None:
            return
        self.cur = e
        self.name.setText(nice_name(e["file"]))
        p = e.get("params") or {}
        bits = [f"{e.get('w')} × {e.get('h')}", f"seed {e.get('seed')}", nice_name(e.get("model") or "")]
        if e.get("kind") == "video":
            bits[1:1] = [f"{e.get('seconds')} s · {int(e.get('fps', 16))} fps · {e.get('loop', '')}"]
        else:
            bits.append(p.get("preset", ""))
        if e.get("loras"):
            bits.append("LoRAs: " + ", ".join(nice_name(x) for x in e["loras"] if x))
        bits.append(e.get("created", ""))
        if e.get("took"):
            bits.append(f"made in {e['took']:.0f}s")
        self.meta.setText("  ·  ".join(b for b in bits if b))
        self.prompt.setPlainText(e.get("final_prompt") or e.get("prompt") or "")
        if e["kind"] == "video":
            self.player.show_image(e.get("thumb") or "", "Loading…", fade=False)
            self.loader.load(e["id"], e["file"])
        else:
            self.player.show_image(e["file"])
        self._enable()

    def _loaded(self, key, frames, fps):
        if self.cur and self.cur["id"] == key and frames:
            self.player.play(frames, fps, f"{fps:.0f} fps")

    def _selected(self):
        sel = [self.history.get(i.data(Qt.ItemDataRole.UserRole)) for i in self.grid.selectedItems()]
        return [x for x in sel if x]

    def _sel_changed(self):
        sel = self._selected()
        n = len(sel)
        vids = len([e for e in sel if e["kind"] == "video"])
        pics = n - vids
        self.multi_row.setVisible(n > 1)
        self.multi.setText(f"{n} selected")
        self.multi_join.setVisible(vids > 1)
        self.multi_anim.setVisible(pics > 0)
        self.multi_anim.setText(f"Animate {pics}" if pics > 1 else "Animate")

    def _enable(self):
        on = self.cur is not None
        for k, b in self.b.items():
            b.setEnabled(on)
        if on:
            img = self.cur["kind"] == "image"
            self.b["animate"].setVisible(img)
            self.b["upscale"].setVisible(img)
            self.b["inpaint"].setVisible(img)
            self.b["extend"].setVisible(not img)
            self.b["compare"].setVisible(bool(self.cur.get("group")))
        if hasattr(self, "multi_row"):
            self._sel_changed()

    def _act(self, k):
        if not self.cur:
            return
        if k == "copyprompt":
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.cur.get("prompt") or "")
            self.app.toast("Prompt copied.", "ok")
            return
        if k == "delete":
            sel = [self.history.get(it.data(Qt.ItemDataRole.UserRole)) for it in self.grid.selectedItems()]
            sel = [e for e in sel if e]
            if len(sel) > 1:
                self.app.delete_entries(sel)
                return
        self.app.result_action(k, self.cur, self)

    def _view(self):
        if self.cur:
            ids = [e["id"] for e in self._shown]
            i = ids.index(self.cur["id"]) if self.cur["id"] in ids else 0
            self.app.view(self._shown or [self.cur], i, on_move=self._follow)

    def _follow(self, e):
        """The viewer moved on: the gallery follows (selection + details)."""
        for i in range(self.grid.count()):
            if self.grid.item(i).data(Qt.ItemDataRole.UserRole) == e["id"]:
                self.grid.setCurrentRow(i)
                self.grid.scrollToItem(self.grid.item(i))
                break

    def _open(self, e):
        if e:
            os.startfile(e["file"])

    def _menu(self, pos):
        it = self.grid.itemAt(pos)
        if it is None:
            return
        e = self.history.get(it.data(Qt.ItemDataRole.UserRole))
        if e is None:
            return
        m = QMenu(self)
        m.addAction(icons.icon("external", "#A1A1AA", 16), "Open").triggered.connect(lambda: self._open(e))
        m.addAction(icons.icon("refresh", "#A1A1AA", 16), "Reuse settings").triggered.connect(
            lambda: self.app.result_action("reuse", e, self))
        if e["kind"] == "image":
            m.addAction(icons.icon("video", "#A1A1AA", 16), "Animate").triggered.connect(
                lambda: self.app.result_action("animate", e, self))
            m.addAction(icons.icon("copy", "#A1A1AA", 16), "Copy picture").triggered.connect(
                lambda: self.app.result_action("copy", e, self))
        else:
            m.addAction(icons.icon("arrow", "#A1A1AA", 16), "Extend from last frame").triggered.connect(
                lambda: self.app.result_action("extend", e, self))
        m.addAction(icons.icon("folder", "#A1A1AA", 16), "Show in folder").triggered.connect(
            lambda: self.app.result_action("folder", e, self))
        if e["kind"] == "image":
            m.addAction(icons.icon("scale", "#A1A1AA", 16), "Upscale…").triggered.connect(
                lambda: self.app.result_action("upscale", e, self))
            m.addAction(icons.icon("edit", "#A1A1AA", 16), "Edit a part…").triggered.connect(
                lambda: self.app.result_action("inpaint", e, self))
        if e.get("group"):
            m.addAction(icons.icon("gallery", "#A1A1AA", 16), "Show the comparison").triggered.connect(
                lambda: self.app.open_compare(e["group"]))
        m.addSeparator()
        sel = self._selected() or [e]
        if len([x for x in sel if x["kind"] == "video"]) > 1:
            m.addAction(icons.icon("film", "#A1A1AA", 16), "Join into one video").triggered.connect(
                lambda: self.app.join_entries(sel))
        if len(sel) > 1 and any(x["kind"] == "image" for x in sel):
            m.addAction(icons.icon("video", "#A1A1AA", 16), "Animate all").triggered.connect(
                lambda: self.app.animate_entries(sel))
        m.addAction(icons.icon("trash", "#FF8A8A", 16), f"Delete{f' {len(sel)} items' if len(sel) > 1 else ''}"
                    ).triggered.connect(lambda: self.app.delete_entries(sel))
        m.exec(self.grid.mapToGlobal(pos))
