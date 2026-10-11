"""The CHECKPOINTS page: every picture and video model in your ComfyUI - checkpoints and diffusion models, each
marked as such - what it is made for (read from the file), tags, notes, link, cover, size; Fetch from Civitai, delete,
use it on the Image page. And the Market for new ones."""

import os
import threading

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import ckpts, comfy, icons, loras, system, theme as T
from .config import cfg
from .widgets import (BLUR_ROLE, BlurTextDelegate, Card, ChipBox, Combo, ImageDrop, Scroll, Segmented, ToggleRow,
                      blur_pixmap, blur_widget, button, chip, field, hrow, icon_button, label, nice_name, quiet,
                      set_combo)

_FOLDER = {"ckpt": "checkpoints", "unet": "unet"}


class CheckpointsPage(QWidget):
    _fetched = Signal(str, object)
    _fetch_done = Signal(int, int)
    _noted = Signal()
    title = "Checkpoints"
    subtitle = "Your picture and video models - made for, tags, notes, favourites"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link = app.link
        self.cur = None
        self._revealed = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        lib = QWidget()
        self.stack.addWidget(lib)
        self.switches = [Segmented([("lib", "Library"), ("market", "Market", "Browse and download from Civitai")],
                                   self._switch, "lib", expand=False) for _ in range(2)]
        h = QHBoxLayout(lib)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.typef = Combo()
        for k, t in (("", "Type: all"), ("ckpt", "Checkpoints"), ("unet", "Diffusion models")):
            self.typef.addItem(t, k)
        self.typef.currentIndexChanged.connect(lambda _=0: self.fill())
        self.filter = Combo()
        self.filter.addItem("Made for: all", "all")
        for k in loras.MADE_FOR:
            self.filter.addItem(loras.FAMILY_NAMES.get(k, k), k)
        self.filter.currentIndexChanged.connect(lambda _=0: self.fill())
        self.tagf = Combo()
        self.tagf.currentIndexChanged.connect(lambda _=0: self.fill())
        self._fill_tag_filter()
        self.sort = Combo()
        for k, t in (("name", "Sort: name"), ("big", "Sort: biggest first"), ("small", "Sort: smallest first"),
                     ("new", "Sort: newest first")):
            self.sort.addItem(t, k)
        self.sort.currentIndexChanged.connect(lambda _=0: self.fill())
        self.sort.setMinimumWidth(200)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, notes…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.textChanged.connect(lambda _t: self.fill())
        self.fav = ToggleRow("Favourites", None, False, lambda _v: self.fill())
        self.fav.setFixedWidth(128)
        self.count = label("", "Faint")
        lv.addWidget(hrow(self.switches[0], self.search, self.count,
                          icon_button("folder", self._open_folder, "Open ComfyUI's checkpoints folder", size=16),
                          icon_button("refresh", self.link.refresh_models, "Look for new model files", size=16),
                          icon_button("download", self._fetch_all, "Fetch all: covers and what they are made for, "
                                                                    "for every model with a Civitai link", size=16),
                          spacing=10))
        lv.addWidget(hrow(self.typef, self.filter, self.tagf, self.sort, None, self.fav, spacing=10))
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setIconSize(QSize(150, 150))
        self.grid.setGridSize(QSize(176, 214))
        self.grid.setWordWrap(True)
        self.grid.setSpacing(4)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setStyleSheet("QListWidget::item { padding: 6px; border-radius: 12px; color: #D4D4D8; }"
                                "QListWidget::item:selected { background: #222226; }")
        self.grid.setItemDelegate(BlurTextDelegate(self.grid))
        self.grid.currentItemChanged.connect(lambda cur, _p: cur and self.show(cur.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.grid, 1)
        h.addWidget(left, 1)

        side = QWidget()
        side.setObjectName("Panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(400)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        self.det = Scroll((18, 18, 16, 18), 12)
        sv.addWidget(self.det)
        self.name = label("Pick a model", "H2", wrap=True)
        self.file = label("", "Faint", wrap=True, sel=True)
        self.star = button("", self._toggle_fav, "Ghost", "star", "Favourite (shown first)", checkable=True)
        self.eye = icon_button("eye", self._reveal_toggle, "Show the name and cover of this NSFW model", size=16)
        self.eye.setCheckable(True)
        self.det.add(hrow(self.name, None, self.eye, self.star))
        self.badges = hrow(spacing=6)
        self.fam_badge = label("", "AccentBadge")
        self.type_badge = label("", "Badge")
        self.size_badge = label("", "Badge")
        for w in (self.fam_badge, self.type_badge, self.size_badge):
            self.badges.layout().addWidget(w)
        self.badges.layout().addStretch(1)
        self.det.add(self.file, self.badges)
        self.prev = ImageDrop("Drop a cover picture here, or click to choose one", 210)
        self.prev.changed.connect(self._set_preview)
        self.det.add(self.prev)
        c = Card("Made for & tags")
        self.made = Combo()
        self.made.currentIndexChanged.connect(self._made_changed)
        self.tagbox = ChipBox(5)
        self.tag_new = QLineEdit()
        self.tag_new.setPlaceholderText("Own tag…")
        self.tag_new.setFixedWidth(130)
        self.tag_new.returnPressed.connect(self._add_own_tag)
        self.tag_sugg = label("", "Faint", wrap=True)
        c.add(field("Made for", self.made, "Read from the file. Set it here if it is wrong.", label_w=64),
              self.tagbox, hrow(self.tag_new, None, spacing=6), self.tag_sugg)
        self.det.add(c)
        c = Card("Where it is from")
        self.url = QLineEdit()
        self.url.setPlaceholderText("Link to the page you downloaded it from")
        self.url.editingFinished.connect(lambda: self._save("url", self.url.text().strip()))
        self.url.textChanged.connect(lambda _t: self._fetch_state())
        c.add(hrow(self.url, icon_button("copy", self._copy_url, "Copy the link", size=16),
                   icon_button("external", self._open_url, "Open the page in your browser", size=16), spacing=4))
        self.det.add(c)
        c = Card("Notes")
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Good settings, what it does well…")
        self.note.setFixedHeight(90)
        self._nt = QTimer(self, singleShot=True, interval=500, timeout=lambda: self._save("note", self.note.toPlainText()))
        self.note.textChanged.connect(lambda: self._nt.start())
        c.add(self.note)
        self.det.add(c)
        self.use_btn = button("Use on Image", self._use, "Accent", "image")
        self.use_lbl = label("", "Faint", wrap=True)
        self.det.add(hrow(self.use_btn, None), self.use_lbl)
        self.fetch_btn = button("Fetch", self._fetch, "Ghost", "download",
                                "The first picture as the cover and what it was made for, from its Civitai link")
        self.del_btn = button("Delete…", self._delete, "Ghost", "trash", "Delete the model file from the drive (the "
                                                                           "way Settings, Deleting says) and from this "
                                                                           "list", icon_color="#FF8A8A")
        self.det.add(hrow(self.fetch_btn, button("Show the file", self._reveal, "Ghost", "folder"), None, self.del_btn,
                          spacing=6))
        self.det.end()
        h.addWidget(side)
        self.market = None
        self._icons = {}
        self._dirty = True
        self._fetching = False
        self.link.models.connect(self._models_changed)
        self._fetched.connect(self._apply_fetch)
        self._fetch_done.connect(self._fetch_finished)
        self._noted.connect(self._after_market)
        self._enable(False)

    # ---- library / market
    def _switch(self, key):
        for s in self.switches:
            s.set(key)
        if key == "market" and self.market is None:
            from .market import MarketView
            self.market = MarketView(self.app, self.switches[1], "Checkpoint", "models/checkpoints",
                                     ("checkpoints", "unet"), self._from_market, what="checkpoint")
            self.stack.addWidget(self.market)
        self.stack.setCurrentWidget(self.market if key == "market" else self.stack.widget(0))
        if key == "lib" and self._dirty:
            self.fill()

    def _from_market(self, name, model, version, host):
        """A model downloaded in the Market (in a thread): a file without its own VAE and text encoder goes to
        diffusion_models, then its link, cover and what it is made for are noted."""
        from . import civitai
        ins = self.link.install()
        src = "ckpt"
        if ins:
            p = os.path.join(comfy.sub_dir(ins, "models/checkpoints"), name)
            if not ckpts.is_full_checkpoint(comfy.safetensors_header(p)):
                dest_dir = comfy.sub_dir(ins, "models/diffusion_models")
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                    if not os.path.exists(os.path.join(dest_dir, name)):
                        os.replace(p, os.path.join(dest_dir, name))
                        src = "unet"
                except OSError:
                    pass
        k = ckpts.key(src, name)
        fields = {"url": civitai.page_link(host, model["id"], version.get("id"))}
        fam = civitai.made_for(version.get("baseModel"))
        if fam:
            fields.update(made_for=fam, made_for_src="fetch")
        if model.get("nsfw"):
            fields["tags"] = ["NSFW"]
        try:
            cover = civitai.save_cover(version, name)
        except Exception:
            cover = None
        if cover:
            fields["preview"] = cover
        ckpts.set_note(k, **fields)
        self._noted.emit()

    def _after_market(self):
        self._icons.clear()
        self._fill_tag_filter()
        self.link.refresh_models()
        self._dirty = True

    # ---- listing
    def _models_changed(self, _lists):
        self._icons.clear()
        if self.isVisible() and self.stack.currentIndex() == 0:
            self.fill()
        else:
            self._dirty = True

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty or not self.grid.count():
            self.fill()

    def _keys(self):
        out = [ckpts.key("ckpt", n) for n in self.link.lists.get("checkpoints") or []]
        out += [ckpts.key("unet", n) for n in self.link.lists.get("unet") or []]
        return out

    def _kind(self, k):
        src, name = ckpts.split(k)
        return self.link.kind_of(name, src)

    def _path(self, k):
        src, name = ckpts.split(k)
        return comfy.model_path(self.link.install(), _FOLDER[src], name)

    def _size(self, k):
        p = self._path(k)
        try:
            return os.path.getsize(p) if p else 0
        except OSError:
            return 0

    def _mtime(self, k):
        p = self._path(k)
        try:
            return os.path.getmtime(p) if p else 0
        except OSError:
            return 0

    def _hidden(self, k):
        return bool(cfg.get("lora_blur_nsfw", False)) and ckpts.is_nsfw(k) and k != self._revealed

    def _fill_tag_filter(self):
        cur = self.tagf.currentData() if self.tagf.count() else ""
        self.tagf.blockSignals(True)
        self.tagf.clear()
        self.tagf.addItem("All tags", "")
        for t in ckpts.all_tags():
            self.tagf.addItem(t, t)
        self.tagf.addItem("No tags yet", "-")
        set_combo(self.tagf, cur or "")
        self.tagf.blockSignals(False)

    def _icon(self, k):
        nt = ckpts.notes(k)
        prev = nt.get("preview") if nt.get("preview") and os.path.isfile(nt["preview"]) else None
        src = ckpts.split(k)[0]
        fam = ckpts.made_for(k, self._kind(k))
        key = (k, prev, fam, bool(nt.get("favorite")), tuple(ckpts.tags(k)), self._hidden(k))
        if key in self._icons:
            return self._icons[key]
        s = 300
        out = QPixmap(s, s)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, s, s), 22, 22)
        p.setClipPath(path)
        p.fillRect(out.rect(), QColor(T.SURFACE2))
        pm = QPixmap(prev) if prev else QPixmap()
        if not pm.isNull():
            pm = pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            if key[-1]:
                pm = blur_pixmap(pm, 14)
            p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
        else:
            p.drawPixmap(s // 2 - 36, s // 2 - 36, icons.pixmap("chip", 72, "#3A3A40", dpr=1.0))
        p.setClipping(False)
        f = p.font()
        f.setPixelSize(19)
        f.setBold(True)
        p.setFont(f)
        x = 14
        for txt, bg, fg in ((loras.FAMILY_NAMES.get(fam, fam).upper(), T.accent(), T.on_accent()),
                            ("CKPT" if src == "ckpt" else "DIFFUSION", QColor("#3B6FD9") if src == "unet"
                             else QColor(0, 0, 0, 170), QColor("#FFFFFF"))):
            tw = p.fontMetrics().horizontalAdvance(txt) + 22
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(x, 14, tw, 34), 10, 10)
            p.setPen(fg)
            p.drawText(QRectF(x, 14, tw, 34), Qt.AlignmentFlag.AlignCenter, txt)
            x += tw + 6
        if nt.get("favorite"):
            p.drawPixmap(s - 52, 14 if x < s - 60 else 56, icons.pixmap("starf", 36, "#F5B041", dpr=1.0))
        x, y = 14, s - 48
        for t in ckpts.tags(k)[:4]:
            txt = t.upper() if len(t) <= 10 else t[:9].upper() + "…"
            tw = p.fontMetrics().horizontalAdvance(txt) + 22
            if x + tw > s - 10:
                break
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#C0392B") if t.lower() == "nsfw" else QColor(0, 0, 0, 170))
            p.drawRoundedRect(QRectF(x, y, tw, 34), 10, 10)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(x, y, tw, 34), Qt.AlignmentFlag.AlignCenter, txt)
            x += tw + 6
        p.end()
        out.setDevicePixelRatio(2.0)
        self._icons[key] = QIcon(out)
        return self._icons[key]

    def fill(self):
        self._dirty = False
        q = self.search.text().strip().lower()
        typ, fam, tag, fav = self.typef.currentData(), self.filter.currentData(), self.tagf.currentData(), \
            self.fav.isChecked()
        how = self.sort.currentData()
        rows = []
        keys = self._keys()
        for k in keys:
            src, name = ckpts.split(k)
            nt = ckpts.notes(k)
            if typ and src != typ:
                continue
            if fam != "all" and ckpts.made_for(k, self._kind(k)) != fam:
                continue
            tg = [t.lower() for t in ckpts.tags(k)]
            if tag == "-" and tg or tag and tag != "-" and tag.lower() not in tg:
                continue
            if fav and not nt.get("favorite"):
                continue
            if q and q not in name.lower() and q not in nt.get("note", "").lower():
                continue
            sk = {"big": -self._size(k), "small": self._size(k), "new": -self._mtime(k)}.get(how, 0)
            rows.append((not nt.get("favorite"), sk, name.lower(), k))
        keep = self.cur
        self.grid.blockSignals(True)
        self.grid.clear()
        for _f, _s, _n, k in sorted(rows):
            src, name = ckpts.split(k)
            it = QListWidgetItem(self._icon(k), nice_name(name))
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setData(BLUR_ROLE, self._hidden(k))
            it.setToolTip("" if self._hidden(k) else f"{name}\n{ckpts.TYPE_NAMES[src]}  ·  "
                                                     f"{loras.FAMILY_NAMES.get(ckpts.made_for(k, self._kind(k)), '?')}"
                                                     f"  ·  {self._size(k) / 2 ** 30:.1f} GB")
            self.grid.addItem(it)
            if k == keep:
                self.grid.setCurrentItem(it)
        self.grid.blockSignals(False)
        self.count.setText(f"{len(rows)} of {len(keys)}")
        if not keys:
            self.name.setText("No models found")
            self.file.setText("Put model files into ComfyUI's models/checkpoints or models/diffusion_models folder, "
                              "then press ↻.")

    # ---- one model
    def show(self, k):
        if not k:
            return
        self.cur = k
        src, name = ckpts.split(k)
        nt = ckpts.notes(k)
        kind = self._kind(k)
        if self._revealed != k:
            self._revealed = None
        hidden = self._hidden(k)
        self.name.setText(nice_name(name))
        self.file.setText(name)
        for w in (self.name, self.file, self.prev):
            blur_widget(w, hidden, 12)
        self.eye.setVisible(bool(cfg.get("lora_blur_nsfw", False)) and ckpts.is_nsfw(k))
        quiet(self.eye, self.eye.setChecked, not hidden)
        fam = ckpts.made_for(k, kind)
        self.fam_badge.setText(loras.FAMILY_NAMES.get(fam, fam))
        self.type_badge.setText(ckpts.TYPE_NAMES[src])
        sz = self._size(k)
        self.size_badge.setText(f"{sz / 2 ** 30:.1f} GB" if sz >= 2 ** 30 else f"{sz / 2 ** 20:.0f} MB")
        self.made.blockSignals(True)
        self.made.clear()
        det = ckpts.detected(src, name, kind)
        self.made.addItem(f"As read from the file ({loras.FAMILY_NAMES.get(det, det)})", "")
        for f in loras.MADE_FOR:
            self.made.addItem(loras.FAMILY_NAMES.get(f, f), f)
        set_combo(self.made, nt.get("made_for", ""))
        self.made.blockSignals(False)
        self._show_tags(k)
        quiet(self.star, self.star.setChecked, bool(nt.get("favorite")))
        self.star.setIcon(icons.icon("starf" if nt.get("favorite") else "star",
                                     "#F5B041" if nt.get("favorite") else "#A1A1AA"))
        self.url.setText(nt.get("url", ""))
        quiet(self.note, self.note.setPlainText, nt.get("note", ""))
        prev = nt.get("preview") if nt.get("preview") and os.path.isfile(nt["preview"]) else ""
        self.prev.set_path(prev, emit=False)
        usable = any(n == name and s == src for n, s, _k in self.link.picture_models())
        self.use_btn.setVisible(usable)
        self.use_lbl.setVisible(not usable)
        self.use_lbl.setText("A video model: pick it on the Video page." if fam == "wan" else
                             "Better Comfy can't make pictures with this kind of model (yet).")
        self._enable(True)
        self._fetch_state()

    def _show_tags(self, k):
        self.tagbox.clear()
        have = {t.lower() for t in ckpts.tags(k)}
        for t in ckpts.all_tags():
            c = chip(t, None, True, "Press to set or take off this tag")
            c.setChecked(t.lower() in have)
            c.clicked.connect(lambda on, t=t: self._tag(t, on))
            self.tagbox.add(c)
        sug = ckpts.suggest_tags(k, self._kind(k))
        self.tag_sugg.setText(("Looks like: " + ", ".join(sug) + ". Press the tag to set it.") if sug else "")
        self.tag_sugg.setVisible(bool(sug))

    def _tag(self, t, on):
        if not self.cur:
            return
        cur = ckpts.tags(self.cur)
        ckpts.set_tags(self.cur, cur + [t] if on else [x for x in cur if x.lower() != t.lower()])
        self._fill_tag_filter()
        self.show(self.cur)
        self._refresh_icon()

    def _add_own_tag(self):
        t = self.tag_new.text().strip()
        if t and self.cur:
            self.tag_new.clear()
            self._tag(t, True)

    def _made_changed(self, _i):
        if self.cur:
            ckpts.set_note(self.cur, made_for=self.made.currentData() or None, made_for_src=None)
            fam = ckpts.made_for(self.cur, self._kind(self.cur))
            self.fam_badge.setText(loras.FAMILY_NAMES.get(fam, fam))
            self._refresh_icon()

    def _reveal_toggle(self):
        if self.cur:
            self._revealed = self.cur if self.eye.isChecked() else None
            self.show(self.cur)
            it = self.grid.currentItem()
            if it:
                it.setData(BLUR_ROLE, self._hidden(self.cur))
            self._refresh_icon()

    def _enable(self, on):
        for w in (self.star, self.prev, self.note, self.url, self.made, self.tag_new, self.tagbox, self.del_btn,
                  self.use_btn):
            w.setEnabled(on)

    def _save(self, key, value):
        if self.cur:
            ckpts.set_note(self.cur, **{key: value})

    def _toggle_fav(self):
        if self.cur:
            ckpts.set_note(self.cur, favorite=not ckpts.notes(self.cur).get("favorite"))
            self.show(self.cur)
            self._refresh_icon()

    def _set_preview(self, path):
        if self.cur:
            ckpts.set_note(self.cur, preview=path or None)
            self._refresh_icon()

    def _refresh_icon(self):
        it = self.grid.currentItem()
        if it and self.cur:
            it.setIcon(self._icon(self.cur))

    def _use(self):
        if not self.cur:
            return
        src, name = ckpts.split(self.cur)
        ip = self.app.pages["image"]
        i = ip.ckpt.findData(f"{src}|{name}")
        if i < 0:
            self.app.toast("It is not in the Image page's list yet - press ↻ there.", "warn")
            return
        ip.ckpt.setCurrentIndex(i)
        self.app.go("image")

    def _open_url(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        u = self.url.text().strip()
        if u and not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        if u:
            QDesktopServices.openUrl(QUrl(u))

    def _copy_url(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.url.text().strip())
        self.app.toast("Link copied.", "ok")

    def _open_folder(self):
        ins = self.link.install()
        if ins:
            system.open_folder(comfy.sub_dir(ins, "models/checkpoints"))

    def _reveal(self):
        p = self._path(self.cur) if self.cur else None
        if p:
            system.reveal(p)

    # ---- fetch (Civitai): cover, made for
    def _fetch_state(self):
        from . import civitai
        ok = bool(self.cur) and civitai.parse(self.url.text()) is not None and not self._fetching
        self.fetch_btn.setEnabled(ok)

    def _fetch(self):
        if self.cur:
            self._save("url", self.url.text().strip())
            self._run_fetch([self.cur])

    def _fetch_all(self):
        from . import civitai
        keys = [k for k in self._keys() if civitai.parse(ckpts.notes(k).get("url", ""))]
        if not keys:
            self.app.toast("No model has a Civitai link yet ('Where it is from').", "warn")
            return
        self._run_fetch(keys)

    def _run_fetch(self, keys):
        from . import civitai
        if self._fetching:
            return
        self._fetching = True
        self._fetch_state()
        self.app.toast(f"Fetching {len(keys)} model{'s' if len(keys) != 1 else ''} from Civitai… (a link without a "
                       "version: the file is checked first, which takes a while for big ones)", "info")
        jobs = [(k, ckpts.notes(k).get("url", ""), self._path(k)) for k in keys]

        def work():
            ok = bad = 0
            for k, url, path in jobs:
                try:
                    self._fetched.emit(k, civitai.fetch(ckpts.split(k)[1], url, path))
                    ok += 1
                except Exception as ex:                 # noqa: BLE001 - shown to the user
                    self._fetched.emit(k, ex)
                    bad += 1
            self._fetch_done.emit(ok, bad)
        threading.Thread(target=work, daemon=True).start()

    def _apply_fetch(self, k, res):
        from . import civitai
        if isinstance(res, Exception):
            if self.cur == k:
                self.app.toast(f"{nice_name(ckpts.split(k)[1])}: {res}", "warn")
            return
        nt = ckpts.notes(k)
        fields = {}
        own_cover = nt.get("preview") and not os.path.normcase(nt["preview"]).startswith(os.path.normcase(civitai.COVERS))
        if res["cover"] and not own_cover:
            fields["preview"] = res["cover"]
        if res["made_for"] and (not nt.get("made_for") or nt.get("made_for_src") == "fetch"):
            src, name = ckpts.split(k)
            det = ckpts.detected(src, name, self._kind(k))
            fits = ("illustrious", "pony", "sdxl") if det in ("illustrious", "pony", "sdxl") else (det,)
            if res.get("exact") or det == "other" or res["made_for"] in fits:
                fields.update(made_for=res["made_for"], made_for_src="fetch")
        if fields:
            ckpts.set_note(k, **fields)
        if k == self.cur:
            self.show(k)
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == k:
                it.setIcon(self._icon(k))

    def _fetch_finished(self, ok, bad):
        self._fetching = False
        self._fetch_state()
        if ok + bad > 1 or not bad:
            self.app.toast(f"Fetched {ok}" + (f", {bad} didn't work (no link, gone, or offline)" if bad else "") + ".",
                           "ok" if not bad else "warn")

    # ---- delete
    def _delete(self):
        from PySide6.QtWidgets import QMessageBox
        from . import civitai, shred
        k = self.cur
        p = self._path(k) if k else None
        if not p:
            return
        mode = cfg.get("delete_mode", "recycle")
        how = {"recycle": "to the Recycle Bin", "shred": "shredded (can't be brought back)",
               "eraser": "erased with Eraser (can't be brought back)"}.get(mode, "deleted")
        name = ckpts.split(k)[1]
        if QMessageBox.question(self, "Delete model", f"Delete {nice_name(name)} ({os.path.getsize(p) / 2 ** 30:.1f} GB) "
                                                      f"from the drive? It goes {how}.") != QMessageBox.StandardButton.Yes:
            return
        files = [p]
        cov = ckpts.notes(k).get("preview") or ""
        if cov and os.path.normcase(cov).startswith(os.path.normcase(civitai.COVERS)):
            files.append(cov)
        left = shred.delete(files, mode)
        if p in left:
            QMessageBox.warning(self, "Delete model", "The file could not be deleted (in use by ComfyUI?).")
            return
        ckpts.forget(k)
        self.cur = None
        self._icons.clear()
        self.link.refresh_models()
        self.app.toast(f"{nice_name(name)} deleted.", "ok")
