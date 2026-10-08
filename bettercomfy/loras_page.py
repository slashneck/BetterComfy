"""The LORAS page: every LoRA in your ComfyUI - what it is for (read from the file), trigger words, notes, favourite
strength, preview pictures - and one click to put it on the Image or Video page."""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from . import comfy, icons, loras, system, theme as T, workflows as W
from .widgets import (Card, ChipBox, ImageDrop, Scroll, Segmented, Slider, ToggleRow, button, chip, field, hrow,
                      icon_button, label, nice_name, quiet)


class LorasPage(QWidget):
    title = "LoRAs"
    subtitle = "Your LoRA library - trigger words, notes, favourites"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link = app.link
        self.cur = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.filter = Segmented([("all", "All"), ("sdxl", "SDXL"), ("wan", "WAN"), ("sd15", "SD 1.5"),
                                 ("flux", "Flux"), ("qwen", "Qwen"), ("other", "Other")], lambda _v: self.fill(), "all",
                                expand=False, height=34)
        self.fav = ToggleRow("Favourites", None, False, lambda _v: self.fill())
        self.fav.setFixedWidth(128)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, trigger words, notes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.fill())
        self.count = label("", "Faint")
        lv.addWidget(hrow(self.filter, self.search, self.fav, self.count,
                          icon_button("folder", self._open_folder, "Open ComfyUI's loras folder", size=16),
                          icon_button("refresh", self.link.refresh_models, "Look for new LoRA files", size=16),
                          spacing=10))
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
        self.name = label("Pick a LoRA", "H2", wrap=True)
        self.file = label("", "Faint", wrap=True, sel=True)
        self.badges = hrow(spacing=6)
        self.fam_badge = label("", "AccentBadge")
        self.info_badge = label("", "Badge")
        self.badges.layout().addWidget(self.fam_badge)
        self.half_badge = label("")
        self.half_badge.hide()
        self.badges.layout().addWidget(self.half_badge)
        self.badges.layout().addWidget(self.info_badge)
        self.badges.layout().addStretch(1)
        self.star = button("", self._toggle_fav, "Ghost", "star", "Favourite (shown first everywhere)", checkable=True)
        self.det.add(hrow(self.name, None, self.star))
        self.pair_lbl = label("", "Faint", wrap=True)
        self.pair_lbl.hide()
        self.det.add(self.file, self.badges, self.pair_lbl)
        self.prev = ImageDrop("Drop a preview picture here, or click to choose one", 210)
        self.prev.changed.connect(self._set_preview)
        self.det.add(self.prev)
        c = Card("Trigger words")
        self.trig = QLineEdit()
        self.trig.setPlaceholderText("Words that switch it on, separated by commas")
        self.trig.editingFinished.connect(lambda: self._save("triggers", self.trig.text().strip()))
        self.sugg_lbl = label("Found inside the file - press to add:", "Faint")
        self.sugg = ChipBox(5)
        c.add(self.trig, self.sugg_lbl, self.sugg)
        self.det.add(c)
        c = Card("Where it is from")
        self.url = QLineEdit()
        self.url.setPlaceholderText("Link to the page you downloaded it from")
        self.url.editingFinished.connect(lambda: self._save("url", self.url.text().strip()))
        self.url_open = icon_button("external", self._open_url, "Open the page in your browser", size=16)
        self.url_copy = icon_button("copy", self._copy_url, "Copy the link", size=16)
        c.add(hrow(self.url, self.url_copy, self.url_open, spacing=4))
        self.det.add(c)
        c = Card("Notes & strength")
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("What it does, good settings, where it is from…")
        self.note.setFixedHeight(90)
        self.note.textChanged.connect(lambda: self._nt.start())
        self._nt = QTimer(self, singleShot=True, interval=500, timeout=lambda: self._save("note", self.note.toPlainText()))
        self.strength = Slider(-2.0, 4.0, 0.05, 2, 1.0, lambda v: self._save("strength", round(v, 2)), soft_hi=2.0)
        c.add(self.note, field("Strength", self.strength, "The strength it gets when you add it.", label_w=64))
        self.det.add(c)
        self.add_img = button("Add to Image", lambda: self._add("image"), "Accent", "image")
        self.add_vid = button("Add to Video", lambda: self._add("video"), None, "video")
        self.det.add(hrow(self.add_img, self.add_vid, spacing=8))
        self.det.add(button("Show the file", self._reveal, "Ghost", "folder"))
        self.det.end()
        h.addWidget(side)
        self._icons = {}
        self._dirty = True
        self.link.models.connect(self._models_changed)
        self._enable(False)

    def _models_changed(self, _lists):
        if self.isVisible():
            self.fill()
        else:
            self._dirty = True

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty or not self.grid.count():
            self.fill()

    def _names(self):
        return list(self.link.lists.get("loras") or [])

    def _info(self, n):
        p = comfy.model_path(self.link.install(), "loras", n)
        return loras.file_info(p) if p else {"family": "other"}

    def _half(self, n):
        """('high' | 'low' | None, partner) - only for WAN LoRAs (or ones that clearly come as a pair)."""
        half, part = W.lora_pair(n, self._names())
        if part is None and self._info(n).get("family") != "wan":
            return None, None
        return half, part

    def _icon(self, n):
        prev = loras.preview_for(self.link.install(), n)
        half = self._half(n)[0]
        key = (n, prev, half, bool(loras.notes(n).get("favorite")))
        if key not in self._icons:
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
                p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
            else:
                p.drawPixmap(s // 2 - 36, s // 2 - 36, icons.pixmap("lora", 72, "#3A3A40", dpr=1.0))
            p.setClipping(False)
            if half:
                f = p.font()
                f.setPixelSize(24)
                f.setBold(True)
                p.setFont(f)
                txt = "HIGH" if half == "high" else "LOW"
                tw = p.fontMetrics().horizontalAdvance(txt) + 28
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(T.accent() if half == "high" else QColor("#3B6FD9"))
                p.drawRoundedRect(QRectF(14, 14, tw, 40), 12, 12)
                p.setPen(T.on_accent() if half == "high" else QColor("#FFFFFF"))
                p.drawText(QRectF(14, 14, tw, 40), Qt.AlignmentFlag.AlignCenter, txt)
            if loras.notes(n).get("favorite"):
                p.drawPixmap(s - 52, 14, icons.pixmap("starf", 36, "#F5B041", dpr=1.0))
            p.end()
            out.setDevicePixelRatio(2.0)
            self._icons[key] = QIcon(out)
        return self._icons[key]

    def fill(self):
        self._dirty = False
        q = self.search.text().strip().lower()
        fam = self.filter.value()
        fav = self.fav.isChecked()
        rows = []
        for n in self._names():
            nt = loras.notes(n)
            f = self._info(n).get("family", "other")
            if fam != "all" and f != fam:
                continue
            if fav and not nt.get("favorite"):
                continue
            if q and q not in n.lower() and q not in nt.get("triggers", "").lower() and q not in nt.get("note", "").lower():
                continue
            rows.append((not nt.get("favorite"), n.lower(), n, f))
        keep = self.cur
        self.grid.blockSignals(True)
        self.grid.clear()
        for _, _, n, f in sorted(rows):
            it = QListWidgetItem(self._icon(n), nice_name(n))
            it.setData(Qt.ItemDataRole.UserRole, n)
            half, part = self._half(n)
            tip = f"{n}\n{loras.FAMILY_NAMES.get(f, f)}"
            if half:
                tip += f"  ·  {half.capitalize()} noise" + (f"\nPair: {part}" if part else "  (its other half is missing)")
            it.setToolTip(tip)
            self.grid.addItem(it)
            if n == keep:
                self.grid.setCurrentItem(it)
        self.grid.blockSignals(False)
        self.count.setText(f"{len(rows)} of {len(self._names())}")
        if not self._names():
            self.name.setText("No LoRAs found")
            self.file.setText("Put .safetensors LoRA files into ComfyUI's models/loras folder, then press ↻.")

    def show(self, n):
        self.cur = n
        nt = loras.notes(n)
        info = self._info(n)
        self.name.setText(nice_name(n))
        self.file.setText(n)
        self.fam_badge.setText(loras.FAMILY_NAMES.get(info.get("family"), "Other"))
        half, part = self._half(n)
        self.half_badge.setVisible(bool(half))
        if half:
            self.half_badge.setText("HIGH NOISE" if half == "high" else "LOW NOISE")
            self.half_badge.setStyleSheet(
                f"background: {T.accent().name() if half == 'high' else '#3B6FD9'}; color: "
                f"{T.on_accent().name() if half == 'high' else '#FFFFFF'}; border-radius: 6px; padding: 2px 7px; "
                f"font-size: 11px; font-weight: 700;")
            self.pair_lbl.setText((f"Pair: {nice_name(part)}  -  trigger words and link are shared with it."
                                   if part else "Its other half was not found in the loras folder."))
            # one half set up before the other: take over what the partner already has
            if part:
                pn = loras.notes(part)
                fill = {k: pn[k] for k in ("triggers", "url") if pn.get(k) and not nt.get(k)}
                if fill:
                    loras.set_note(n, **fill)
                    nt = loras.notes(n)
        self.pair_lbl.setVisible(bool(half))
        bits = []
        if info.get("size"):
            bits.append(f"{info['size'] / 2 ** 20:.0f} MB")
        if info.get("rank"):
            bits.append(f"rank {info['rank']}")
        if info.get("base"):
            bits.append(str(info["base"])[:24])
        self.info_badge.setText("  ·  ".join(bits))
        self.info_badge.setVisible(bool(bits))
        quiet(self.star, self.star.setChecked, bool(nt.get("favorite")))
        self.star.setIcon(icons.icon("starf" if nt.get("favorite") else "star", "#F5B041" if nt.get("favorite")
                                     else "#A1A1AA"))
        self.trig.setText(nt.get("triggers", ""))
        self.url.setText(nt.get("url", ""))
        quiet(self.note, self.note.setPlainText, nt.get("note", ""))
        self.strength.set(float(nt.get("strength") or 1.0))
        self.prev.set_path(loras.preview_for(self.link.install(), n) or "", emit=False)
        self.sugg.clear()
        have = [w.lower() for w in loras.split_words(nt.get("triggers", ""))]
        sug = [s for s in info.get("suggest") or [] if s.lower() not in have][:14]
        for s in sug:
            self.sugg.add(chip(s, lambda s=s: self._add_word(s), False, "Add as a trigger word"))
        self.sugg_lbl.setVisible(bool(sug))
        is_wan = info.get("family") == "wan"
        self.add_img.setEnabled(not is_wan)
        self.add_vid.setEnabled(is_wan or info.get("family") == "other")
        self._enable(True)

    def _enable(self, on):
        for w in (self.star, self.prev, self.trig, self.note, self.strength, self.add_img, self.add_vid, self.url,
                  self.url_open, self.url_copy):
            w.setEnabled(on and (w not in (self.add_img, self.add_vid) or w.isEnabled()))

    def _save(self, key, value):
        if not self.cur:
            return
        loras.set_note(self.cur, **{key: value})
        if key in ("triggers", "url"):
            part = self._half(self.cur)[1]
            if part:
                loras.set_note(part, **{key: value})       # the High and Low half share them
        if key == "triggers":
            self.show(self.cur)

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

    def _add_word(self, w):
        words = loras.split_words(self.trig.text()) + [w]
        self.trig.setText(", ".join(words))
        self._save("triggers", self.trig.text())

    def _toggle_fav(self):
        if not self.cur:
            return
        on = not loras.notes(self.cur).get("favorite")
        loras.set_note(self.cur, favorite=on)
        self.show(self.cur)
        self._refresh_icon()

    def _set_preview(self, path):
        if self.cur:
            loras.set_note(self.cur, preview=path or None)
            self._refresh_icon()

    def _refresh_icon(self):
        it = self.grid.currentItem()
        if it and self.cur:
            it.setIcon(self._icon(self.cur))

    def _add(self, where):
        if self.cur:
            self.app.add_lora(where, self.cur)

    def _open_folder(self):
        ins = self.link.install()
        if ins:
            system.open_folder(comfy.sub_dir(ins, "models/loras"))

    def _reveal(self):
        p = comfy.model_path(self.link.install(), "loras", self.cur) if self.cur else None
        if p:
            system.reveal(p)
