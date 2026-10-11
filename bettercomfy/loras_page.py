"""The LORAS page: every LoRA in your ComfyUI - what it is for (read from the file), trigger words, notes, favourite
strength, preview pictures - and one click to put it on the Image or Video page."""

import os
import threading

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import comfy, icons, loras, system, theme as T, workflows as W
from .config import cfg
from .widgets import (BLUR_ROLE, BlurTextDelegate, Card, ChipBox, Combo, ImageDrop, Scroll, Segmented, Slider, ToggleRow,
                      blur_pixmap,
                      blur_widget, button, chip, field, hrow, icon_button, label, nice_name, quiet, set_combo)


class LorasPage(QWidget):
    _fetched = Signal(str, object)              # LoRA name, result dict or the error
    _fetch_done = Signal(int, int)              # how many worked, how many did not
    _noted = Signal()                           # a Market download got its notes
    title = "LoRAs"
    subtitle = "Your LoRA library - trigger words, notes, favourites"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link = app.link
        self.cur = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        lib = QWidget()
        self.stack.addWidget(lib)
        # Library | Market - the same switch on both (the Market has its own toolbar)
        self.switches = [Segmented([("lib", "Library"), ("market", "Market", "Browse and download from Civitai")],
                                   self._switch, "lib", expand=False) for _ in range(2)]
        h = QHBoxLayout(lib)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.filter = Combo()
        self.filter.addItem("Made for: all", "all")
        for k in loras.MADE_FOR:
            self.filter.addItem(loras.FAMILY_NAMES.get(k, k), k)
        self.filter.addItem("SDXL, can't be told", "")
        self.filter.currentIndexChanged.connect(lambda _=0: self.fill())
        self.tagf = Combo()
        self.tagf.currentIndexChanged.connect(lambda _=0: self.fill())
        self.sort = Combo()
        for k, t in (("name", "Sort: name"), ("big", "Sort: biggest first"), ("small", "Sort: smallest first"),
                     ("new", "Sort: newest first")):
            self.sort.addItem(t, k)
        self.sort.currentIndexChanged.connect(lambda _=0: self.fill())
        self._fill_tag_filter()
        self.fav = ToggleRow("Favourites", None, False, lambda _v: self.fill())
        self.fav.setFixedWidth(128)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, trigger words, notes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.fill())
        self.count = label("", "Faint")
        lv.addWidget(hrow(self.switches[0], self.filter, self.tagf, self.sort, self.search, self.fav, self.count,
                          icon_button("folder", self._open_folder, "Open ComfyUI's loras folder", size=16),
                          icon_button("refresh", self.link.refresh_models, "Look for new LoRA files", size=16),
                          icon_button("download", self._fetch_all, "Fetch all: trigger words and covers for every LoRA "
                                                                    "with a Civitai link", size=16),
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
        self.eye = icon_button("eye", self._reveal_toggle, "Show the name and preview of this NSFW LoRA", size=16)
        self.eye.setCheckable(True)
        self._revealed = None
        self.det.add(hrow(self.name, None, self.eye, self.star))
        self.pair_lbl = label("", "Faint", wrap=True)
        self.pair_lbl.hide()
        self.det.add(self.file, self.badges, self.pair_lbl)
        self.prev = ImageDrop("Drop a preview picture here, or click to choose one", 210)
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
        c.add(field("Made for", self.made, "Read from the file. Set it here if it is wrong: the LoRA lists on the "
                                         "Image page go by it.", label_w=64),
              self.tagbox, hrow(self.tag_new, None, spacing=6), self.tag_sugg)
        self.det.add(c)
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
        self.fetch_btn = button("Fetch", self._fetch, "Ghost", "download",
                                "Trigger words, the first picture as the cover and what it was made for, from its "
                                "Civitai link")
        self.del_btn = button("Delete…", self._delete, "Ghost", "trash", "Delete the LoRA file from the drive (the way "
                                                                           "Settings, Deleting says) and from this list",
                              icon_color="#FF8A8A")
        self.det.add(hrow(self.fetch_btn, button("Show the file", self._reveal, "Ghost", "folder"), None, self.del_btn,
                          spacing=6))
        self.det.end()
        h.addWidget(side)
        self.market = None              # made the first time it is opened
        self._icons = {}
        self._dirty = True
        self.link.models.connect(self._models_changed)
        self._fetched.connect(self._apply_fetch)
        self._noted.connect(self._after_market)
        self._fetch_done.connect(self._fetch_finished)
        self._fetching = False
        self.url.textChanged.connect(lambda _t: self._fetch_state())
        self._enable(False)

    def _switch(self, key):
        for s in self.switches:
            s.set(key)
        if key == "market" and self.market is None:
            from .market import MarketView
            self.market = MarketView(self.app, self.switches[1], "LORA", "models/loras", "loras", self._from_market)
            self.stack.addWidget(self.market)
        self.stack.setCurrentWidget(self.market if key == "market" else self.stack.widget(0))
        if key == "lib" and self._dirty:
            self.fill()

    def _from_market(self, name, model, version, host):
        """A LoRA downloaded in the Market: its link, trigger words, cover and what it is made for (runs in a
        thread: the cover is fetched)."""
        from . import civitai
        words = []
        for w in version.get("trainedWords") or []:
            for part in str(w).split(","):
                part = part.strip()
                if part and part not in words:
                    words.append(part)
        fields = {"url": civitai.page_link(host, model["id"], version.get("id")), "triggers": ", ".join(words)}
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
        loras.set_note(name, **fields)
        self._noted.emit()

    def _after_market(self):
        self._icons.clear()
        self._fill_tag_filter()
        if self.stack.currentIndex() == 0 and self.isVisible():
            self.fill()
        else:
            self._dirty = True

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

    def _hidden(self, n):
        """An NSFW LoRA while names and previews of those are blurred (Settings, Privacy)."""
        return bool(cfg.get("lora_blur_nsfw", False)) and loras.is_nsfw(n) and n != self._revealed

    def _fill_tag_filter(self):
        cur = self.tagf.currentData() if self.tagf.count() else ""
        quiet(self.tagf, self.tagf.clear)
        self.tagf.blockSignals(True)
        self.tagf.addItem("All tags", "")
        for t in loras.all_tags():
            self.tagf.addItem(t, t)
        self.tagf.addItem("No tags yet", "-")
        set_combo(self.tagf, cur or "")
        self.tagf.blockSignals(False)

    def _icon(self, n):
        prev = loras.preview_for(self.link.install(), n)
        half = self._half(n)[0]
        key = (n, prev, half, bool(loras.notes(n).get("favorite")), tuple(loras.lora_tags(n)), self._hidden(n))
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
                if key[-1]:
                    pm = blur_pixmap(pm, 14)
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
            tags_ = loras.lora_tags(n)
            if tags_:
                # the LoRA's tags as small labels along the bottom (like HIGH / LOW at the top)
                f = p.font()
                f.setPixelSize(19)
                f.setBold(True)
                p.setFont(f)
                x, y = 14, s - 48
                for t in tags_[:4]:
                    txt = t.upper() if len(t) <= 10 else t[:9].upper() + "…"
                    tw = p.fontMetrics().horizontalAdvance(txt) + 22
                    if x + tw > s - 10:
                        break
                    nsfw = t.lower() == "nsfw"
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#C0392B") if nsfw else QColor(0, 0, 0, 170))
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
        fam = self.filter.currentData()
        tag = self.tagf.currentData()
        fav = self.fav.isChecked()
        rows = []
        for n in self._names():
            nt = loras.notes(n)
            f = loras.made_for(n, self._info(n))
            if fam != "all" and f != fam:
                continue
            if tag == "-" and loras.lora_tags(n):
                continue
            if tag and tag != "-" and tag.lower() not in (t.lower() for t in loras.lora_tags(n)):
                continue
            if fav and not nt.get("favorite"):
                continue
            if q and q not in n.lower() and q not in nt.get("triggers", "").lower() and q not in nt.get("note", "").lower():
                continue
            info = self._info(n)
            how = self.sort.currentData()
            key = {"big": -int(info.get("size") or 0), "small": int(info.get("size") or 0),
                   "new": -self._mtime(n)}.get(how, 0)
            rows.append((not nt.get("favorite"), key, n.lower(), n, f))
        keep = self.cur
        self.grid.blockSignals(True)
        self.grid.clear()
        for _, _, _, n, f in sorted(rows):
            it = QListWidgetItem(self._icon(n), nice_name(n))
            it.setData(Qt.ItemDataRole.UserRole, n)
            it.setData(BLUR_ROLE, self._hidden(n))
            half, part = self._half(n)
            tip = "" if self._hidden(n) else (f"{n}\n{loras.made_for_label(n, self._info(n))}  ·  "
                                               f"{self._info(n).get('size', 0) / 2 ** 20:.0f} MB")
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
        if self._revealed != n:
            self._revealed = None
        self.name.setText(nice_name(n))
        self.file.setText(n)
        hidden = self._hidden(n)
        for w in (self.name, self.file, self.prev):
            blur_widget(w, hidden, 12)
        self.eye.setVisible(bool(cfg.get("lora_blur_nsfw", False)) and loras.is_nsfw(n))
        quiet(self.eye, self.eye.setChecked, not hidden)
        self.fam_badge.setText(loras.made_for_label(n, info))
        self.made.blockSignals(True)
        self.made.clear()
        det = info.get("base_family", info.get("family", "other"))
        self.made.addItem(f"As read from the file ({'SDXL, unclear' if det == '' else loras.FAMILY_NAMES.get(det, det)})",
                          "")
        for k in loras.MADE_FOR:
            self.made.addItem(loras.FAMILY_NAMES.get(k, k), k)
        set_combo(self.made, nt.get("made_for", ""))
        self.made.blockSignals(False)
        self._show_tags(n, info)
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
        self._fetch_state()
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

    def _show_tags(self, n, info):
        self.tagbox.clear()
        have = {t.lower() for t in loras.lora_tags(n)}
        for t in loras.all_tags():
            c = chip(t, None, True, "Press to set or take off this tag")
            c.setChecked(t.lower() in have)
            c.clicked.connect(lambda on, t=t: self._tag(t, on))
            self.tagbox.add(c)
        sug = loras.suggest_tags(n, info)
        self.tag_sugg.setText(("Looks like: " + ", ".join(sug) + ". Press the tag to set it.") if sug else "")
        self.tag_sugg.setVisible(bool(sug))

    def _tag(self, t, on):
        if not self.cur:
            return
        cur = loras.lora_tags(self.cur)
        new = cur + [t] if on else [x for x in cur if x.lower() != t.lower()]
        loras.set_tags(self.cur, new)
        part = self._half(self.cur)[1]
        if part:
            loras.set_tags(part, new)                  # the High and Low half share their tags
        self._after_tags()

    def _add_own_tag(self):
        t = self.tag_new.text().strip()
        if not t or not self.cur:
            return
        self.tag_new.clear()
        self._tag(t, True)

    def _after_tags(self):
        self._fill_tag_filter()
        self.show(self.cur)
        it = self.grid.currentItem()
        if it:
            it.setData(BLUR_ROLE, self._hidden(self.cur))
        self._refresh_icon()

    def _made_changed(self, _i):
        if not self.cur:
            return
        v = self.made.currentData() or None
        loras.set_note(self.cur, made_for=v, made_for_src=None)     # set by hand: Fetch leaves it alone
        part = self._half(self.cur)[1]
        if part:
            loras.set_note(part, made_for=v, made_for_src=None)
        self.fam_badge.setText(loras.made_for_label(self.cur, self._info(self.cur)))

    def _reveal_toggle(self):
        if not self.cur:
            return
        self._revealed = self.cur if self.eye.isChecked() else None
        self.show(self.cur)
        it = self.grid.currentItem()
        if it:
            it.setData(BLUR_ROLE, self._hidden(self.cur))
        self._refresh_icon()

    # ---- fetch (Civitai): trigger words, cover, made for
    def _fetch_state(self):
        from . import civitai
        ok = bool(self.cur) and civitai.parse(self.url.text()) is not None and not self._fetching
        self.fetch_btn.setEnabled(ok)
        self.fetch_btn.setToolTip("Trigger words, the first picture as the cover and what it was made for, from its "
                                  "Civitai link" if ok or self._fetching else
                                  "Put the LoRA's Civitai page into 'Where it is from' first")

    def _fetch(self):
        if not self.cur:
            return
        self._save("url", self.url.text().strip())
        self._run_fetch([self.cur])

    def _fetch_all(self):
        from . import civitai
        names = [n for n in self._names() if civitai.parse(loras.notes(n).get("url", ""))]
        if not names:
            self.app.toast("No LoRA has a Civitai link yet ('Where it is from').", "warn")
            return
        self._run_fetch(names)

    def _run_fetch(self, names):
        from . import civitai
        if self._fetching:
            return
        self._fetching = True
        self._fetch_state()
        self.app.toast(f"Fetching {len(names)} LoRA{'s' if len(names) != 1 else ''} from Civitai…", "info")
        ins = self.link.install()
        jobs = [(n, loras.notes(n).get("url", ""), comfy.model_path(ins, "loras", n) if ins else None) for n in names]

        def work():
            ok = bad = 0
            for n, url, path in jobs:
                try:
                    self._fetched.emit(n, civitai.fetch(n, url, path))
                    ok += 1
                except Exception as ex:                 # noqa: BLE001 - shown to the user
                    self._fetched.emit(n, ex)
                    bad += 1
            self._fetch_done.emit(ok, bad)
        threading.Thread(target=work, daemon=True).start()

    def _apply_fetch(self, n, res):
        from . import civitai
        if isinstance(res, Exception):
            if len(self._names()) and self.cur == n:
                self.app.toast(f"{nice_name(n)}: {res}", "warn")
            return
        nt = loras.notes(n)
        words = loras.split_words(", ".join(loras.split_words(nt.get("triggers", "")) + res["triggers"]))
        fields = {"triggers": ", ".join(words)}
        own_cover = nt.get("preview") and not os.path.normcase(nt["preview"]).startswith(
            os.path.normcase(civitai.COVERS))
        if res["cover"] and not own_cover:
            fields["preview"] = res["cover"]           # a cover you set yourself stays
        if res["made_for"] and (not nt.get("made_for") or nt.get("made_for_src") == "fetch"):
            # Civitai's base model when it is certainly this file's version (in the link, or the same checksum);
            # a guess only when it fits what the file itself is. One you set by hand always stays.
            fam = self._info(n).get("family", "other")
            fits = {"sdxl": ("illustrious", "pony", "sdxl")}.get(fam, (fam,))
            if res.get("exact") or fam == "other" or res["made_for"] in fits:
                fields["made_for"] = res["made_for"]
                fields["made_for_src"] = "fetch"
        loras.set_note(n, **fields)
        part = self._half(n)[1]
        if part:
            loras.set_note(part, triggers=fields["triggers"])
        if n == self.cur:
            self.show(n)
        self._icons = {k: v for k, v in self._icons.items() if k[0] != n}
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == n:
                it.setIcon(self._icon(n))

    def _fetch_finished(self, ok, bad):
        self._fetching = False
        self._fetch_state()
        if ok + bad > 1 or not bad:
            self.app.toast(f"Fetched {ok}" + (f", {bad} didn't work (no link, gone, or offline)" if bad else "") + ".",
                           "ok" if not bad else "warn")

    def _enable(self, on):
        for w in (self.star, self.prev, self.trig, self.note, self.strength, self.add_img, self.add_vid, self.url,
                  self.url_open, self.url_copy, self.made, self.tag_new, self.tagbox, self.del_btn):
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

    def _mtime(self, n):
        p = comfy.model_path(self.link.install(), "loras", n)
        try:
            return os.path.getmtime(p) if p else 0
        except OSError:
            return 0

    def _delete(self):
        """The LoRA file away (Recycle Bin, shredded or Eraser, as Settings say), with its notes and fetched cover."""
        from PySide6.QtWidgets import QMessageBox
        from . import civitai, shred
        from .config import cfg as _cfg
        n = self.cur
        p = comfy.model_path(self.link.install(), "loras", n) if n else None
        if not p:
            return
        mode = _cfg.get("delete_mode", "recycle")
        how = {"recycle": "to the Recycle Bin", "shred": "shredded (can't be brought back)",
               "eraser": "erased with Eraser (can't be brought back)"}.get(mode, "deleted")
        part = self._half(n)[1]
        extra = f"\n\nIts other half ({nice_name(part)}) stays - delete it on its own." if part else ""
        if QMessageBox.question(self, "Delete LoRA", f"Delete {nice_name(n)} ({os.path.getsize(p) / 2 ** 20:.0f} MB) "
                                                     f"from the drive? It goes {how}.{extra}") != \
                QMessageBox.StandardButton.Yes:
            return
        files = [p]
        cov = loras.notes(n).get("preview") or ""
        if cov and os.path.normcase(cov).startswith(os.path.normcase(civitai.COVERS)):
            files.append(cov)
        left = shred.delete(files, mode)
        if p in left:
            QMessageBox.warning(self, "Delete LoRA", "The file could not be deleted (in use by ComfyUI?).")
            return
        loras.forget(n)
        self.cur = None
        self._icons = {k: v for k, v in self._icons.items() if k[0] != n}
        self.link.refresh_models()
        self.app.toast(f"{nice_name(n)} deleted.", "ok")

    def _reveal(self):
        p = comfy.model_path(self.link.install(), "loras", self.cur) if self.cur else None
        if p:
            system.reveal(p)
