"""The MARKET: Civitai (civitai.red or civitai.com) inside the app - browse, search, download straight into
ComfyUI's folder with the link, trigger words, cover and what it is made for filled in. Online only while it is open
and you browse; the covers you see stay in memory and are never written to the drive."""
import collections
import html
import queue
import re
import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QProgressBar, QVBoxLayout, QWidget)

from . import civitai as C, comfy, icons, loras, theme as T
from .config import cfg
from .widgets import (BLUR_ROLE, BlurTextDelegate, ChipBox, Combo, Scroll, ToggleRow, blur_pixmap, blur_widget,
                      button, chip, hrow, icon_button, label, set_combo)

FAVS = "market_favs"


class _Thumbs(QObject):
    """Covers, fetched by a few threads (started the first time one is wanted) and kept in memory only."""
    ready = Signal(str)

    def __init__(self, keep=500):
        super().__init__()
        self.q = queue.Queue()
        self.cache = collections.OrderedDict()
        self.pending = set()
        self.keep = keep
        self._started = False
        self._got = {}
        self._lock = threading.Lock()
        self._arrived = _Arrived()
        self._arrived.got.connect(self._store)

    def get(self, url):
        if not url:
            return None
        if url in self.cache:
            self.cache.move_to_end(url)
            return self.cache[url]
        if url not in self.pending:
            self.pending.add(url)
            self.q.put(url)
            if not self._started:
                self._started = True
                for _ in range(4):
                    threading.Thread(target=self._work, daemon=True).start()
        return None

    def drop_waiting(self):
        """A new search: covers not fetched yet are not wanted any more."""
        try:
            while True:
                self.pending.discard(self.q.get_nowait())
        except queue.Empty:
            pass

    def _work(self):
        while True:
            url = self.q.get()
            img = QImage()
            try:
                data = C.thumb(url)
                if data:
                    img.loadFromData(data)
            except Exception:
                pass
            self._arrived.got.emit(url, img)

    def _store(self, url, img):
        self.pending.discard(url)
        self.cache[url] = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        while len(self.cache) > self.keep:
            self.cache.popitem(last=False)
        self.ready.emit(url)


class _Arrived(QObject):
    got = Signal(str, QImage)


class MarketView(QWidget):
    """kind: Civitai's type ("LORA" or "Checkpoint"); folder: where downloads go in ComfyUI (models/loras …);
    list_key: the link's model list that tells what is there already; on_downloaded(name, model, version, host)
    fills in the notes after a download."""
    _page = Signal(int, object, object)
    _dl_prog = Signal(int, int, int)
    _dl_done = Signal(int, object)
    _detail = Signal(int, object)

    def __init__(self, app, switch, kind="LORA", folder="models/loras", list_key="loras", on_downloaded=None,
                 what="LoRA"):
        super().__init__()
        self.app, self.link = app, app.link
        self.kind, self.folder, self.list_key, self.on_downloaded, self.what = kind, folder, list_key, on_downloaded, what
        self.models = {}            # id -> model (as Civitai sent it)
        self.cursor = None
        self.more = True
        self.loading = False
        self.gen = 0
        self.cur = None
        self.revealed = None
        self.dls = {}               # version id -> {"cancel": Event, "done": int, "total": int, "model": id}
        self.thumbs = _Thumbs()
        self.thumbs.ready.connect(self._thumb_ready)
        self._icons = {}
        self._started = False

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.site = Combo()
        for s in C.SITES:
            self.site.addItem(s, s)
        set_combo(self.site, cfg.get("market_site", "civitai.red"))
        self.site.currentIndexChanged.connect(self._site_changed)
        self.made = Combo()
        self.made.addItem("Made for: all", "")
        for k in C.MARKET_BASES:
            self.made.addItem(loras.FAMILY_NAMES.get(k, k), k)
        self.made.currentIndexChanged.connect(lambda _=0: self.search())
        self.sort = Combo()
        for i, (_s, _p, t) in enumerate(C.SORTS):
            self.sort.addItem(t, i)
        set_combo(self.sort, cfg.get("market_sort", 0))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        self.query = QLineEdit()
        self.query.setPlaceholderText(f"Search Civitai {what}s…")
        self.query.setClearButtonEnabled(True)
        self._qt = QTimer(self, singleShot=True, interval=700, timeout=self.search)
        self.query.textChanged.connect(lambda _t: self._qt.start())
        self.query.returnPressed.connect(self.search)
        self.nsfw = ToggleRow("NSFW", None, bool(cfg.get("market_nsfw", False)), self._nsfw_changed)
        self.nsfw.setFixedWidth(100)
        self.favs = ToggleRow("Favourites", None, False, lambda _v: self.search())
        self.favs.setFixedWidth(128)
        self.count = label("", "Faint")
        self.key_btn = icon_button("lock", self._key, "", size=16)
        self._key_tip()
        self.query.setMinimumWidth(260)
        self.site.setMinimumWidth(140)
        self.made.setMinimumWidth(170)
        self.sort.setMinimumWidth(240)
        lv.addWidget(hrow(switch, self.query, self.count, self.key_btn, spacing=10))
        lv.addWidget(hrow(self.site, self.made, self.sort, None, self.nsfw, self.favs, spacing=10))
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
        self.grid.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.status = label("", "Muted", wrap=True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self.status)
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
        self.name = label(f"Pick a {what}", "H2", wrap=True)
        self.by = label("", "Faint", wrap=True)
        self.eye = icon_button("eye", self._reveal, "Show this NSFW one", size=16)
        self.eye.setCheckable(True)
        self.star = button("", self._toggle_fav, "Ghost", "star", "Keep it in your Market favourites", checkable=True)
        self.det.add(hrow(self.name, None, self.eye, self.star))
        self.det.add(self.by)
        self.cover = QLabel()
        self.cover.setFixedHeight(300)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet(f"background: {T.SURFACE2}; border-radius: 14px;")
        self.det.add(self.cover)
        self.badges = hrow(spacing=6)
        self.base_badge = label("", "AccentBadge")
        self.nsfw_badge = label("NSFW", "Badge")
        self.nsfw_badge.setStyleSheet("background: #C0392B; color: white;")
        self.badges.layout().addWidget(self.base_badge)
        self.badges.layout().addWidget(self.nsfw_badge)
        self.badges.layout().addStretch(1)
        self.stats = label("", "Faint")
        self.det.add(self.badges, self.stats)
        self.version = Combo()
        self.version.currentIndexChanged.connect(lambda _=0: self._version_changed())
        self.file = label("", "Faint", wrap=True)
        self.have = label("", "Muted", wrap=True)
        self.det.add(self.version, self.file, self.have)
        self.dl_btn = button("Download", self._download, "Accent", "download")
        self.stop_btn = button("Stop", self._stop_dl, "Ghost", "stop")
        self.open_btn = button("Open page", self._open_page, "Ghost", "external")
        self.det.add(hrow(self.dl_btn, self.stop_btn, None, self.open_btn, spacing=6))
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar_lbl = label("", "Faint")
        self.det.add(self.bar, self.bar_lbl)
        self.trig_lbl = label("Trigger words", "Faint")
        self.trig = ChipBox(5)
        self.det.add(self.trig_lbl, self.trig)
        self.tags = label("", "Faint", wrap=True)
        self.desc = label("", "Muted", wrap=True)
        self.desc.setTextFormat(Qt.TextFormat.PlainText)
        self.det.add(self.tags, self.desc)
        self.det.end()
        h.addWidget(side)
        self._page.connect(self._got_page)
        self._dl_prog.connect(self._progress)
        self._dl_done.connect(self._finished)
        self._detail.connect(self._got_detail)
        self._clear_details()

    # ---- listing
    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:                  # nothing goes online before the Market is opened
            self._started = True
            self.search()

    def host(self):
        return self.site.currentData() or "civitai.red"

    def _site_changed(self, _i):
        cfg.set("market_site", self.host())
        self.search()

    def _sort_changed(self, _i):
        cfg.set("market_sort", self.sort.currentData() or 0)
        self.search()

    def _nsfw_changed(self, on):
        cfg.set("market_nsfw", bool(on))
        self._icons.clear()
        self.search()

    def search(self):
        if not self._started:
            return
        self._qt.stop()
        self.gen += 1
        self.models = {}
        self.cursor, self.more, self.loading = None, True, False
        self.thumbs.drop_waiting()
        self.grid.clear()
        self._clear_details()
        if self.favs.isChecked():
            self.more = False
            favs = [f for f in cfg.get(FAVS) or [] if f.get("type", "LORA") == self.kind]
            for f in favs:
                if f.get("nsfw") and not self.nsfw.isChecked():
                    continue
                self.models[f["id"]] = dict(f, _snapshot=True)
                self._add_item(self.models[f["id"]])
            self._set_count()
            self.status.setText("" if favs else "No favourites yet - the star on a model keeps it here.")
            self.status.setVisible(not favs)
            return
        self.status.setText("Looking…")
        self.status.show()
        self._load_more()

    def _load_more(self):
        if self.loading or not self.more:
            return
        self.loading = True
        gen, host, made, sort, q, cur = (self.gen, self.host(), self.made.currentData(), self.sort.currentData() or 0,
                                         self.query.text(), self.cursor)
        nsfw = self.nsfw.isChecked()

        def work():
            try:
                res = C.search(host, self.kind, q, made, sort, cur, nsfw=nsfw)
            except Exception as ex:
                res = ex
            self._page.emit(gen, res, host)
        threading.Thread(target=work, daemon=True).start()

    def _got_page(self, gen, res, host):
        if gen != self.gen:
            return
        self.loading = False
        if isinstance(res, Exception):
            self.more = False
            if not self.grid.count():
                self.status.setText(str(res) if isinstance(res, C.FetchError) else "Civitai can't be reached.")
                self.status.show()
            return
        items, cursor = res
        self.cursor, self.more = cursor, bool(cursor) and bool(items)
        nsfw = self.nsfw.isChecked()
        for m in items:
            if m.get("id") in self.models or not C.shows(m, nsfw):
                continue
            m["_host"] = host
            self.models[m["id"]] = m
            self._add_item(m)
        self._set_count()
        self.status.setVisible(not self.grid.count())
        if not self.grid.count():
            self.status.setText("Nothing found." if not self.more else "Looking…")
        QTimer.singleShot(0, self._scrolled)          # the page is not full yet: the next part right away

    def _set_count(self):
        self.count.setText(f"{self.grid.count()}" + ("+" if self.more else ""))

    def _scrolled(self, *_):
        sb = self.grid.verticalScrollBar()
        if self.more and not self.loading and sb.value() >= sb.maximum() - 400:
            self._load_more()

    # ---- the tiles
    def _cover_of(self, m, version=None):
        if m.get("_snapshot"):
            return m.get("cover"), int(m.get("cover_lvl") or 0)
        v = version or (m.get("modelVersions") or [{}])[0]
        return C.first_media(v, sfw=not self.nsfw.isChecked())

    def _hidden(self, m):
        url, lvl = self._cover_of(m)
        return bool(cfg.get("lora_blur_nsfw", False)) and (bool(m.get("nsfw")) or lvl >= C.MATURE) and \
            self.revealed != m.get("id")

    def _base(self, m):
        if m.get("_snapshot"):
            return m.get("base") or ""
        return ((m.get("modelVersions") or [{}])[0].get("baseModel")) or ""

    def _icon(self, m):
        url, _lvl = self._cover_of(m)
        pm = self.thumbs.get(url) if url else None
        fav = self._is_fav(m["id"])
        key = (m["id"], url, pm is not None, self._hidden(m), fav)
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
        if pm is not None and not pm.isNull():
            pm = pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            if key[3]:
                pm = blur_pixmap(pm, 14)
            p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
        else:
            p.drawPixmap(s // 2 - 36, s // 2 - 36, icons.pixmap("lora" if self.kind == "LORA" else "layers", 72,
                                                                 "#3A3A40", dpr=1.0))
        p.setClipping(False)
        f = p.font()
        f.setPixelSize(19)
        f.setBold(True)
        p.setFont(f)
        x = 14
        fam = C.made_for(self._base(m))
        pills = [(loras.FAMILY_NAMES.get(fam, self._base(m) or "?").upper()[:12], T.accent(), T.on_accent())]
        if m.get("nsfw"):
            pills.append(("NSFW", QColor("#C0392B"), QColor("#FFFFFF")))
        for txt, bg, fg in pills:
            tw = p.fontMetrics().horizontalAdvance(txt) + 22
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(x, 14, tw, 34), 10, 10)
            p.setPen(fg)
            p.drawText(QRectF(x, 14, tw, 34), Qt.AlignmentFlag.AlignCenter, txt)
            x += tw + 6
        if fav:
            p.drawPixmap(s - 52, 14, icons.pixmap("starf", 36, "#F5B041", dpr=1.0))
        p.end()
        out.setDevicePixelRatio(2.0)
        if len(self._icons) > 800:
            self._icons.clear()
        self._icons[key] = QIcon(out)
        return self._icons[key]

    def _add_item(self, m):
        it = QListWidgetItem(self._icon(m), m.get("name") or "?")
        it.setData(Qt.ItemDataRole.UserRole, m["id"])
        it.setData(BLUR_ROLE, self._hidden(m))
        st = m.get("stats") or {}
        it.setToolTip("" if self._hidden(m) else f"{m.get('name')}\n{self._base(m)}  ·  "
                                                 f"{_num(st.get('downloadCount'))} downloads")
        self.grid.addItem(it)

    def _refresh_item(self, mid):
        m = self.models.get(mid)
        if not m:
            return
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == mid:
                it.setIcon(self._icon(m))
                it.setData(BLUR_ROLE, self._hidden(m))

    def _thumb_ready(self, url):
        for mid, m in list(self.models.items()):
            if self._cover_of(m)[0] == url:
                self._refresh_item(mid)
        if self.cur and self.cur in self.models:
            v = self._ver()
            if v is not None and self._cover_of(self.models[self.cur], v)[0] == url:
                self._show_cover()

    # ---- details
    def _clear_details(self):
        self.cur = None
        self.name.setText(f"Pick a {self.what}")
        for w in (self.by, self.stats, self.file, self.have, self.tags, self.desc, self.bar_lbl):
            w.setText("")
        self.cover.clear()
        for w in (self.badges, self.version, self.dl_btn, self.stop_btn, self.open_btn, self.bar, self.trig_lbl,
                  self.trig, self.star, self.eye):
            w.hide()

    def show(self, mid):
        m = self.models.get(mid)
        if not m:
            return
        self.cur = mid
        if self.revealed != mid:
            self.revealed = None
        if m.get("_snapshot"):
            self._fill_details(m, loading=True)
            host = m.get("host") or self.host()

            def work():
                try:
                    full = C.model(host, mid)
                    full["_host"] = host
                except Exception as ex:
                    full = ex
                self._detail.emit(mid, full)
            threading.Thread(target=work, daemon=True).start()
            return
        self._fill_details(m)

    def _got_detail(self, mid, full):
        if isinstance(full, Exception):
            if self.cur == mid:
                self.desc.setText(str(full))
            return
        self.models[mid] = full
        self._refresh_item(mid)
        if self.cur == mid:
            self._fill_details(full)

    def _fill_details(self, m, loading=False):
        hidden = self._hidden(m)
        self.name.setText(m.get("name") or "?")
        blur_widget(self.name, hidden, 12)
        self.eye.setVisible(bool(cfg.get("lora_blur_nsfw", False)) and (bool(m.get("nsfw")) or
                                                                         self._cover_of(m)[1] >= C.MATURE))
        self.eye.setChecked(self.revealed == m["id"])
        self.star.show()
        self.star.setChecked(self._is_fav(m["id"]))
        self.star.setIcon(icons.icon("starf" if self._is_fav(m["id"]) else "star",
                                     "#F5B041" if self._is_fav(m["id"]) else "#A1A1AA", 16))
        self.badges.show()
        self.nsfw_badge.setVisible(bool(m.get("nsfw")))
        self.open_btn.show()
        if loading:
            self.by.setText("")
            self.base_badge.setText(m.get("base") or "")
            self.stats.setText("")
            self.desc.setText("Loading…")
            self.version.hide()
            self._show_cover()
            return
        self.by.setText("by " + ((m.get("creator") or {}).get("username") or "?"))
        st = m.get("stats") or {}
        self.stats.setText(f"{_num(st.get('downloadCount'))} downloads  ·  {_num(st.get('thumbsUpCount'))} likes")
        tags_ = [str(t) for t in m.get("tags") or []][:12]
        self.tags.setText(("Tags: " + ", ".join(tags_)) if tags_ else "")
        self.desc.setText(_plain(m.get("description"))[:900])
        self.version.blockSignals(True)
        self.version.clear()
        for v in m.get("modelVersions") or []:
            self.version.addItem(f"{v.get('name') or '?'}  ·  {v.get('baseModel') or '?'}", v.get("id"))
        self.version.blockSignals(False)
        self.version.setVisible(self.version.count() > 0)
        self._version_changed()

    def _ver(self):
        m = self.models.get(self.cur)
        if not m or m.get("_snapshot"):
            return None
        vid = self.version.currentData()
        for v in m.get("modelVersions") or []:
            if v.get("id") == vid:
                return v
        return None

    def _show_cover(self):
        m = self.models.get(self.cur)
        if not m:
            return
        url, _lvl = self._cover_of(m, self._ver())
        pm = self.thumbs.get(url) if url else None
        if pm is None or pm.isNull():
            self.cover.setPixmap(icons.pixmap("lora" if self.kind == "LORA" else "layers", 64, "#3A3A40"))
            return
        dpr = self.devicePixelRatioF()
        pm = pm.scaled(int(364 * dpr), int(300 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        if self._hidden(m):
            pm = blur_pixmap(pm, 14)
        pm.setDevicePixelRatio(dpr)
        self.cover.setPixmap(pm)

    def _version_changed(self):
        v = self._ver()
        m = self.models.get(self.cur)
        self._show_cover()
        if v is None or m is None:
            return
        self.base_badge.setText(v.get("baseModel") or "?")
        words = []
        for w in v.get("trainedWords") or []:
            for part in str(w).split(","):
                part = part.strip()
                if part and part not in words:
                    words.append(part)
        while self.trig.flow.count():
            it = self.trig.flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for w in words[:30]:
            c = chip(w, lambda _=False, w=w: self._copy(w), checkable=False, tip="Copy")
            self.trig.add(c)
        self.trig_lbl.setVisible(bool(words))
        self.trig.setVisible(bool(words))
        f = C.model_file(v)
        if f:
            self.file.setText(f"{f.get('name')}  ·  {float(f.get('sizeKB') or 0) / 1024:.0f} MB")
        else:
            self.file.setText("No .safetensors file in this version (other formats can run code, so they are "
                              "not offered).")
        self._dl_state()

    def _have(self, f):
        if not f:
            return False
        base = (C.safe_name(f.get("name")) or "").lower()
        return any(n.replace("\\", "/").rsplit("/", 1)[-1].lower() == base
                   for n in self.link.lists.get(self.list_key) or [])

    def _dl_state(self):
        v = self._ver()
        if v is None:
            for w in (self.dl_btn, self.stop_btn, self.bar):
                w.hide()
            self.bar_lbl.setText("")
            self.have.setText("")
            return
        f = C.model_file(v)
        d = self.dls.get(v["id"])
        have = self._have(f)
        self.have.setText("You have this one." if have else "")
        self.dl_btn.setVisible(d is None)
        self.dl_btn.setEnabled(bool(f) and not have and self.link.install() is not None)
        self.stop_btn.setVisible(d is not None)
        self.bar.setVisible(d is not None)
        if d is not None:
            self._progress(v["id"], d["done"], d["total"])
        else:
            self.bar_lbl.setText("")

    # ---- downloading
    def _download(self):
        v, m = self._ver(), self.models.get(self.cur)
        ins = self.link.install()
        if v is None or m is None or not ins or v["id"] in self.dls:
            return
        dest = comfy.sub_dir(ins, self.folder)
        host = m.get("_host") or self.host()
        key = C.api_key()
        ev = threading.Event()
        self.dls[v["id"]] = {"cancel": ev, "done": 0, "total": int(float((C.model_file(v) or {}).get("sizeKB") or 0) * 1024),
                             "model": m["id"]}

        def work():
            try:
                path = C.download(host, v, dest, key, lambda d, t: self._dl_prog.emit(v["id"], d, t), ev)
                res = {"path": path, "model": m, "version": v, "host": host}
            except BaseException as ex:          # noqa: B036 - Cancelled too
                res = ex
            self._dl_done.emit(v["id"], res)
        threading.Thread(target=work, daemon=True).start()
        self._dl_state()

    def _stop_dl(self):
        v = self._ver()
        if v is not None and v["id"] in self.dls:
            self.dls[v["id"]]["cancel"].set()

    def _progress(self, vid, done, total):
        d = self.dls.get(vid)
        if d is not None:
            d["done"], d["total"] = done, total or d["total"]
        v = self._ver()
        if v is None or v["id"] != vid or d is None:
            return
        t = d["total"] or 0
        self.bar.setValue(int(done * 1000 / t) if t else 0)
        self.bar_lbl.setText(f"{done / 2 ** 20:.0f} of {t / 2 ** 20:.0f} MB" if t else f"{done / 2 ** 20:.0f} MB")

    def _finished(self, vid, res):
        self.dls.pop(vid, None)
        if isinstance(res, C.Cancelled):
            self.app.toast("Download stopped.", "info")
        elif isinstance(res, C.NeedKey):
            self.app.toast(str(res), "warn")
        elif isinstance(res, Exception):
            self.app.toast(str(res) if isinstance(res, C.FetchError) else f"The download failed: {res}", "warn")
        else:
            import os
            name = os.path.basename(res["path"])
            if self.on_downloaded:
                threading.Thread(target=self._after, args=(name, res), daemon=True).start()
            self.app.toast(f"{name} is in your {self.what}s now.", "ok")
            self.link.refresh_models()
            QTimer.singleShot(1500, self._dl_state)
        self._dl_state()

    def _after(self, name, res):
        try:
            self.on_downloaded(name, res["model"], res["version"], res["host"])
        except Exception:
            pass

    # ---- small things
    def _open_page(self):
        m = self.models.get(self.cur)
        if m:
            v = self._ver()
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(C.page_link(m.get("_host") or m.get("host") or self.host(), m["id"],
                                                      v and v.get("id"))))

    def _copy(self, w):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(w)
        self.app.toast(f"Copied: {w}", "ok")

    def _reveal(self):
        self.revealed = self.cur if self.eye.isChecked() else None
        if self.cur:
            self._refresh_item(self.cur)
            self._fill_details(self.models[self.cur])

    def _is_fav(self, mid):
        return any(f.get("id") == mid for f in cfg.get(FAVS) or [])

    def _toggle_fav(self):
        m = self.models.get(self.cur)
        if not m:
            return
        favs = [f for f in cfg.get(FAVS) or [] if f.get("id") != m["id"]]
        if self.star.isChecked():
            if m.get("_snapshot"):
                snap = {k: v for k, v in m.items() if k != "_snapshot"}
            else:
                v = (m.get("modelVersions") or [{}])[0]
                url, lvl = C.first_media(v)
                snap = {"id": m["id"], "host": m.get("_host") or self.host(), "type": m.get("type") or self.kind,
                        "name": m.get("name"), "nsfw": bool(m.get("nsfw")), "base": v.get("baseModel") or "",
                        "cover": url, "cover_lvl": lvl}
            favs.insert(0, snap)
        cfg.set(FAVS, favs)
        self._refresh_item(m["id"])
        self.star.setIcon(icons.icon("starf" if self.star.isChecked() else "star",
                                     "#F5B041" if self.star.isChecked() else "#A1A1AA", 16))

    def _key_tip(self):
        self.key_btn.setToolTip("Civitai API key: " + ("added" if C.api_key() else "not added - some creators only "
                                                       "allow downloads when logged in") + ". Set it up in Settings.")

    def _key(self):
        self.app.go("settings")
        st = self.app.pages.get("settings")
        if st is not None and hasattr(st, "show_civitai"):
            st.show_civitai()


def _num(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _plain(text):
    """Civitai's description is HTML: shown as plain text here (nothing in it is loaded)."""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", text or "")
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()
