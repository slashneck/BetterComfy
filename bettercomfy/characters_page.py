"""The CHARACTERS tab: make a character from pictures of it (and, if trained, its LoRA), keep them as a library, and
pick one on the Image page. The optional Character reference add-on is managed here too."""
import threading

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget)

from . import charlib, icons, theme as T
from .widgets import (Card, Combo, ImageDrop, Scroll, Slider, blur_pixmap, button, field, hrow, label, nice_name, quiet,
                      set_combo)

ADDON_NOTE = ("Add-ons are programs that run inside ComfyUI with full access to your PC, and ComfyUI doesn't check them. "
              "This one, ComfyUI_IPAdapter_plus, is open source (GPL-3.0) and widely used, and Better Comfy only fetches "
              "one fixed version, checked against a checksum. Still: you install it on your own behalf. It can be "
              "taken out again here at any time.")


def tile(path, s=300, placeholder="person", blur=False):
    """A rounded square of a picture (or a placeholder icon)."""
    out = QPixmap(s, s)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, s, s), s * 0.07, s * 0.07)
    p.setClipPath(clip)
    p.fillRect(out.rect(), T.col(T.SURFACE2))
    pm = QPixmap(path) if path else QPixmap()
    if not pm.isNull():
        pm = pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        if blur:
            pm = blur_pixmap(pm, 14)
        p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
    else:
        p.drawPixmap(s // 2 - s // 8, s // 2 - s // 8, icons.pixmap(placeholder, s // 4, "#3A3A40", dpr=1.0))
    p.end()
    out.setDevicePixelRatio(2.0)
    return QIcon(out)


class AddonDialog(QDialog):
    """Downloads the Character reference add-on and its model, with progress."""
    _prog = Signal(object, object)
    _end = Signal(object)

    def __init__(self, parent, install):
        super().__init__(parent)
        self.install = install
        self.setWindowTitle("Character reference")
        self.setModal(True)
        self.resize(560, 10)
        self.ev = threading.Event()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(10)
        v.addWidget(label("Character reference", "H2"))
        v.addWidget(label("Lets a character's pictures guide every new picture, whatever the scene. Optional.", "Muted",
                          wrap=True))
        for what, size in charlib.addon_todo(install):
            v.addWidget(label(f"{what}  ·  {size / 2 ** 20:.0f} MB" if size >= 2 ** 20 else f"{what}  ·  under 1 MB", None))
        note = label(ADDON_NOTE, None, wrap=True)
        note.setObjectName("InfoBox")
        note.setStyleSheet(f"#InfoBox {{ background: {T.rgba(T.WARN, 22)}; border: 1px solid {T.rgba(T.WARN, 70)}; "
                           "border-radius: 10px; padding: 10px; }")
        v.addWidget(note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.bar)
        v.addWidget(self.status)
        self.go_btn = button("Download and install", self._go, "Accent", "download")
        self.close_btn = button("Close", self.reject, "Ghost")
        v.addWidget(hrow(None, self.close_btn, self.go_btn, spacing=6))
        self._prog.connect(self._progress)
        self._end.connect(self._finished)

    def _go(self):
        self.go_btn.setEnabled(False)
        self.bar.show()
        self.status.setText("Downloading…")

        def work():
            try:
                charlib.addon_install(self.install, lambda d, t: self._prog.emit(d, t), self.ev)
                res = None
            except BaseException as ex:         # noqa: B036 - stopped too
                res = ex
            try:
                self._end.emit(res)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, done, total):
        self.bar.setValue(int(done * 1000 / total) if total else 0)
        self.status.setText(f"{done / 2 ** 20:.0f} of {total / 2 ** 20:.0f} MB")

    def _finished(self, res):
        if res is None:
            self.accept()
        else:
            self.status.setText(f"That didn't work: {res}")
            self.go_btn.setEnabled(True)

    def reject(self):
        self.ev.set()
        super().reject()


class CharactersPage(QWidget):
    title = "Characters"
    subtitle = "Your characters: pick one on the Image page and it appears in any scene"

    def __init__(self, app):
        super().__init__()
        self.app, self.link = app, app.link
        self.cur = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        lv.addWidget(hrow(button("New character", self._new, "Accent", "plus"), None))
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setIconSize(QSize(150, 150))
        self.grid.setGridSize(QSize(176, 196))
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setStyleSheet("QListWidget::item { padding: 6px; border-radius: 12px; color: #D4D4D8; }"
                                "QListWidget::item:selected { background: #222226; }")
        self.grid.currentItemChanged.connect(lambda cur, _p: cur and self.open_char(cur.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.grid, 1)
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.setSpacing(8)
        ev.addStretch(1)
        art = label()
        art.setPixmap(icons.pixmap("person", 56, "#3A3A40"))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = label("No characters yet", "H2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = label("Press New character and give it a few pictures: different poses and angles work best.", "Muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(art)
        ev.addWidget(title)
        ev.addWidget(text)
        ev.addStretch(1)
        lv.addWidget(self.empty, 1)

        # the optional add-on, with what it is and that it's on your own behalf
        self.addon_lbl = label("", None)
        self.addon_btn = button("Install…", self._addon, "Ghost", "download")
        self.addon_rm = button("Take it out…", self._addon_remove, "Ghost", "trash", icon_color="#FF8A8A")
        addon = Card()
        addon.add(hrow(self.addon_lbl, None, self.addon_btn, self.addon_rm, spacing=6),
                  label("Lets a character's pictures guide a new picture, without training a LoRA. ComfyUI doesn't check "
                        "add-ons: this one is widely used and open source, but you install it on your own behalf.", "Faint",
                        wrap=True))
        lv.addWidget(addon)
        h.addWidget(left, 1)

        side = QWidget()
        side.setObjectName("Panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(400)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        self.det = Scroll((18, 18, 16, 18), 12)
        sv.addWidget(self.det)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Its name")
        self.name.editingFinished.connect(self._rename)
        self.det.add(self.name)
        c = Card("Its pictures")
        self.refs = QListWidget()
        self.refs.setViewMode(QListWidget.ViewMode.IconMode)
        self.refs.setMovement(QListWidget.Movement.Static)
        self.refs.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.refs.setIconSize(QSize(80, 80))
        self.refs.setGridSize(QSize(90, 90))
        self.refs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.refs.customContextMenuRequested.connect(self._ref_menu)
        self.drop = ImageDrop("Drop pictures here, or click to choose", 84)
        self.drop.changed.connect(lambda path: path and self._add_refs([path]))
        self.drop.many.connect(self._add_refs)
        c.add(self.refs, self.drop, label(f"The first {charlib.MAX_REFS} go along with each picture. Right click one to "
                                          "take it out.", "Faint", wrap=True))
        self.det.add(c)
        c = Card("Trained LoRA")
        self.lora = Combo()
        self.lora.currentIndexChanged.connect(self._lora_picked)
        self.trigger = QLineEdit()
        self.trigger.setPlaceholderText("The word that brings it in")
        self.trigger.editingFinished.connect(lambda: self._save(trigger=self.trigger.text().strip()))
        c.add(label("Holds a character best. Without one, its pictures guide the result (needs Character reference).",
                    "Faint", wrap=True),
              field("LoRA", self.lora, label_w=64), field("Trigger", self.trigger, label_w=64),
              hrow(button("Train one from these pictures", self._train, "Ghost", "chip"), None))
        self.det.add(c)
        c = Card("How close by default")
        self.strength = Slider(0.0, 1.0, 0.01, 2, 0.8, lambda v: self._save(strength=round(v, 2)))
        c.add(self.strength, label("Low: free, loosely like it. High: as close as it gets.", "Faint", wrap=True))
        self.det.add(c)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Notes: good words for it, what works…")
        self.note.setFixedHeight(70)
        self.note.textChanged.connect(lambda: self._save(note=self.note.toPlainText()))
        self.det.add(self.note)
        self.use_btn = button("Use on Image", self._use, "Accent", "image")
        self.del_btn = button("Delete…", self._delete, "Ghost", "trash", icon_color="#FF8A8A")
        self.det.add(hrow(self.use_btn, None, self.del_btn, spacing=6))
        self.det.end()
        self.side = side
        h.addWidget(side)
        self.link.models.connect(lambda _l: self.isVisible() and self._fill_loras())

    # ---- list
    def showEvent(self, e):
        super().showEvent(e)
        self.fill()
        self._addon_state()

    def fill(self):
        keep = self.cur
        self.grid.blockSignals(True)
        self.grid.clear()
        chars = charlib.all_characters()
        for c in chars:
            it = QListWidgetItem(tile(charlib.cover(c)), c["name"])
            it.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.grid.addItem(it)
            if c["id"] == keep:
                self.grid.setCurrentItem(it)
        self.grid.blockSignals(False)
        self.grid.setVisible(bool(chars))
        self.empty.setVisible(not chars)
        self.side.setVisible(bool(chars))
        if chars:
            if not charlib.get(keep or ""):
                self.grid.setCurrentRow(0)
            else:
                self.open_char(keep)

    def open_char(self, cid):
        c = charlib.get(cid)
        if not c:
            return
        self.cur = cid
        quiet(self.name, self.name.setText, c["name"])
        quiet(self.trigger, self.trigger.setText, c.get("trigger", ""))
        quiet(self.note, self.note.setPlainText, c.get("note", ""))
        self.strength.set(float(c.get("strength", 0.8)))
        self.refs.clear()
        for r in c.get("refs") or []:
            it = QListWidgetItem(tile(r, 160), "")
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.refs.addItem(it)
        rows = min(3, (self.refs.count() + 2) // 3)
        self.refs.setFixedHeight(rows * 92 + 4)
        self.refs.setVisible(rows > 0)
        self._fill_loras()

    def _fill_loras(self):
        c = charlib.get(self.cur or "")
        self.lora.blockSignals(True)
        self.lora.clear()
        self.lora.addItem("None", "")
        for n in self.link.lists.get("loras") or []:
            self.lora.addItem(nice_name(n), n)
        set_combo(self.lora, (c or {}).get("lora", ""))
        self.lora.blockSignals(False)

    # ---- editing
    def _new(self):
        name, ok = QInputDialog.getText(self, "New character", "Its name:")
        if not ok or not name.strip():
            return
        c = charlib.add(name)
        self.cur = c["id"]
        self.fill()
        files, _f = QFileDialog.getOpenFileNames(self, "Pictures of it", "", "Pictures (*.png *.jpg *.jpeg *.webp)")
        if files:
            self._add_refs(files)

    def _add_refs(self, paths):
        if not self.cur:
            return
        n = charlib.add_refs(self.cur, paths)
        self.drop.set_path("", emit=False)
        self.fill()
        if n < len(paths):
            self.app.toast(f"{len(paths) - n} couldn't be read.", "warn")

    def _ref_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        it = self.refs.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        m.addAction(icons.icon("trash", "#FF8A8A", 16), "Take it out").triggered.connect(
            lambda: (charlib.remove_ref(self.cur, it.data(Qt.ItemDataRole.UserRole)), self.fill()))
        m.exec(self.refs.mapToGlobal(pos))

    def _rename(self):
        if self.cur and self.name.text().strip():
            charlib.update(self.cur, name=self.name.text().strip()[:60])
            it = self.grid.currentItem()
            if it:
                it.setText(self.name.text().strip()[:60])

    def _save(self, **fields):
        if self.cur:
            charlib.update(self.cur, **fields)

    def _lora_picked(self, _i):
        if not self.cur:
            return
        name = self.lora.currentData() or ""
        self._save(lora=name)
        if name and not self.trigger.text().strip():
            from . import loras
            words = loras.split_words(loras.notes(name).get("triggers", ""))
            if words:
                self.trigger.setText(words[0])
                self._save(trigger=words[0])

    def _train(self):
        if self.cur:
            self.app.go("train")
            self.app.pages["train"].from_character(self.cur)

    def _use(self):
        if self.cur:
            self.app.pages["image"].pick_character(self.cur)
            self.app.go("image")

    def _delete(self):
        c = charlib.get(self.cur or "")
        if c and QMessageBox.question(self, "Delete", f"Delete {c['name']}? Its copies of the pictures go; a LoRA trained "
                                                      "for it stays in the LoRA library.") == QMessageBox.StandardButton.Yes:
            charlib.remove(c["id"])
            self.cur = None
            self.fill()

    # ---- the add-on
    def _addon_state(self):
        ins = self.link.install()
        on = charlib.addon_ready(ins)
        anything = any(charlib.addon_state(ins).values()) and (charlib.addon_state(ins)["node"] or
                                                               charlib.addon_state(ins)["model"])
        self.addon_lbl.setText("Character reference: installed" if on else "Character reference: optional add-on")
        self.addon_btn.setToolTip("" if ins else "ComfyUI wasn't found")
        self.addon_btn.setEnabled(bool(ins))
        self.addon_btn.setVisible(not on)
        self.addon_rm.setVisible(anything)

    def _addon(self):
        ins = self.link.install()
        if ins and AddonDialog(self, ins).exec():
            self._restart_comfy()
            self.app.toast("Installed. ComfyUI loads it the next time it starts.", "ok")
        self._addon_state()

    def _addon_remove(self):
        ins = self.link.install()
        if not ins:
            return
        if self.app.queue.running():
            self.app.toast("Not while something is being made.", "warn")
            return
        if QMessageBox.question(self, "Character reference", "Take the add-on and the model it brought out of ComfyUI? "
                                                             "Characters stay; ones without a LoRA can't be used until "
                                                             "it is back.") != QMessageBox.StandardButton.Yes:
            return
        self.link.stop()
        charlib.addon_remove(ins)
        self.link.refresh_models()
        self._addon_state()
        self.app.toast("Taken out.", "ok")

    def _restart_comfy(self):
        if self.link.running() and not self.app.queue.running():
            self.link.stop()

