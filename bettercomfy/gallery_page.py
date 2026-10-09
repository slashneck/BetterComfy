"""The GALLERY: everything made, sorted your way - favourites, marked for deletion, collections (manual or smart),
sorting and filters, quick culling with F and X, bulk actions. Files never move: collections are just labels."""
import os
import threading
from collections import deque

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem,
                               QMenu, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from . import icons, theme as T
from .vault import vault
from .components import _Loader
from .widgets import (ChipBox, Combo, FileList, Player, Segmented, anims_on, button, chip, hrow, icon_button, label,
                      nice_name, set_combo)

SORTS = [("new", "Newest first"), ("old", "Oldest first"), ("model", "Checkpoint"), ("lora", "LoRA"),
         ("preset", "Preset"), ("type", "Type"), ("size", "Size")]
LIBRARY = [("all", "All", "gallery"), ("fav", "Favourites", "starf"), ("marked", "Marked for deletion", "trash"),
           ("image", "Images", "image"), ("video", "Videos", "video")]


def _matches(e, f):
    """Does an entry fit a filter {kind, q, model, lora, fav, marked}?"""
    if f.get("kind") and e.get("kind") != f["kind"]:
        return False
    if f.get("fav") and not e.get("fav"):
        return False
    if f.get("marked") and not e.get("marked"):
        return False
    if f.get("model") and (e.get("model") or "") != f["model"]:
        return False
    if f.get("lora") and f["lora"] not in (e.get("loras") or []):
        return False
    q = (f.get("q") or "").strip().lower()
    if q and not (q in (e.get("prompt") or "").lower() or q in (e.get("model") or "").lower()
                  or q in str(e.get("seed")) or q in os.path.basename(e.get("file", "")).lower()
                  or any(q in (x or "").lower() for x in e.get("loras") or [])):
        return False
    return True


class Nav(QListWidget):
    """The library column: fixed places, then your collections (drop pictures onto one to add them)."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setFixedWidth(214)
        self.setSpacing(1)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setStyleSheet(f"QListWidget {{ background: transparent; }} QListWidget::item {{ padding: 6px 8px; "
                           f"border-radius: 8px; color: {T.TEXT2}; }} QListWidget::item:selected {{ background: "
                           f"{T.SURFACE3}; color: {T.TEXT}; }}")

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.source() is self.page.grid else e.ignore()

    def dragMoveEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        sel = self.page._selected()
        vault_sel = bool(sel) and all(x.get("vault") for x in sel)
        if isinstance(key, str) and key.startswith("vcol:"):
            ok = vault_sel
        else:
            ok = isinstance(key, str) and key.startswith("col:") and not vault_sel and \
                not (self.page.history.collection(key[4:]) or {}).get("smart")
        e.acceptProposedAction() if ok else e.ignore()

    def dropEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        key = it.data(Qt.ItemDataRole.UserRole) if it else ""
        if key.startswith("vcol:"):
            from .vault import vault as _v
            c = _v.collection(key[5:]) if _v.is_open() else None
            sel = [x for x in self.page._selected() if x.get("vault")]
            if c and sel:
                self.page._vcol_set(sel, c, True)
        elif key.startswith("col:"):
            ids = [x["id"] for x in self.page._selected()]
            self.page.history.set_in_collection(ids, key[4:], True)
            c = self.page.history.collection(key[4:])
            self.page.app.toast(f"Added {len(ids)} to {c['name']}.", "ok")
        e.acceptProposedAction()


class GalleryPage(QWidget):
    _vthumb_ready = Signal(str, int, object)       # vault entry id, lock generation, decrypted thumbnail bytes
    title = "Gallery"
    subtitle = "Everything you made"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.history = app.history
        self.cur = None
        self.place = "all"
        self.flt = {}                   # model / lora filters on top of the place
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # ---- library column
        navw = QWidget()
        navw.setObjectName("PanelR")
        navw.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nv = QVBoxLayout(navw)
        nv.setContentsMargins(12, 16, 10, 12)
        nv.setSpacing(6)
        self.nav = Nav(self)
        self.nav.currentItemChanged.connect(lambda cur, _p: cur and self._go(cur.data(Qt.ItemDataRole.UserRole)))
        self.nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self._nav_menu)
        nv.addWidget(self.nav, 1)
        nv.addWidget(button("New collection", self._new_collection, "Ghost", "plus"))
        h.addWidget(navw)

        # ---- grid
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(20, 16, 16, 12)
        lv.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search prompts, models, LoRAs, seeds…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._st.start())
        self._st = QTimer(self, singleShot=True, interval=200, timeout=self.fill)
        self.filter_btn = button("Filter", self._filter_menu, "Ghost", "tag")
        self.sort = Combo(10)
        for k, t in SORTS:
            self.sort.addItem(t, k)
        self.sort.setFixedWidth(150)
        set_combo(self.sort, self.app_cfg("gallery_sort", "new"))
        self.sort.currentIndexChanged.connect(lambda _i: (self._save_sort(), self.fill()))
        self.size = Segmented([("s", "S"), ("m", "M"), ("l", "L")], self._size, "m", expand=False, height=34)
        lv.addWidget(hrow(self.search, self.filter_btn, self.sort, self.size, spacing=8))
        self.chips = hrow(spacing=6)
        self.chip_lay = self.chips.layout()
        self.save_smart = button("Save as smart collection", self._save_smart, "Ghost", "plus")
        lv.addWidget(self.chips)
        self.banner = QWidget()
        self.banner.setObjectName("MarkBanner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.banner.setStyleSheet(f"#MarkBanner {{ background: {T.rgba(T.BAD, 22)}; border: 1px solid "
                                  f"{T.rgba(T.BAD, 70)}; border-radius: 12px; }}")
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(14, 8, 10, 8)
        self.banner_text = label("", None)
        bh.addWidget(self.banner_text, 1)
        bh.addWidget(button("Unmark all", self._unmark_all, "Ghost"))
        self.del_marked = button("Delete all marked", self._delete_marked, "Danger", "trash")
        bh.addWidget(self.del_marked)
        lv.addWidget(self.banner)
        # the vault: a bar while it is open, a lock panel while it is locked
        self.vbar = QWidget()
        self.vbar.setObjectName("VaultBar")
        self.vbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.vbar.setStyleSheet(f"#VaultBar {{ background: {T.rgba(T.accent(), 22)}; border: 1px solid "
                                f"{T.rgba(T.accent(), 70)}; border-radius: 12px; }}")
        vb = QHBoxLayout(self.vbar)
        vb.setContentsMargins(14, 8, 10, 8)
        self.vbar_text = label("", None)
        vb.addWidget(self.vbar_text, 1)
        vb.addWidget(button("Lock the vault", lambda: self.app.lock_vault(), "Accent", "lock"))
        lv.addWidget(self.vbar)
        self.lockpanel = QWidget()
        lp = QVBoxLayout(self.lockpanel)
        lp.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("lock", 56, T.accent().name()))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lp.addWidget(ic)
        self.lock_title = label("The vault is locked", "H2")
        self.lock_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lock_text = label("", "Muted", wrap=True)
        self.lock_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lock_text.setFixedWidth(440)
        self.lock_btn = button("Unlock", lambda: self.app.ensure_vault(), "Accent", "unlock")
        self.lock_btn.setMinimumWidth(160)
        lp.addWidget(self.lock_title)
        lp.addWidget(hrow(None, self.lock_text, None))
        lp.addWidget(hrow(None, self.lock_btn, None))
        lp.addStretch(2)
        lv.addWidget(self.lockpanel, 1)
        self.grid = FileList()
        self.grid.file_of = lambda eid: (self.history.get(eid) or {}).get("file")       # (vault: nothing)
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
        self.empty = label("", "Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        lv.addWidget(self.empty, 1)
        self.count = label("", "Faint")
        self.hint = label("F favourite  ·  X mark for deletion  ·  Del delete  ·  Enter view big", "Faint")
        self.hint.setStyleSheet("color:#4A4A52; font-size:11px;")
        lv.addWidget(hrow(self.count, None, self.hint))
        h.addWidget(left, 1)

        # ---- details
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
        self._rows = {}
        self.player.message("Pick something", "Its settings show here.")
        sv.addWidget(self.player, 1)
        self.fav_btn = icon_button("star", lambda: self._flag("fav"), "Favourite (F)", size=17)
        self.mark_btn = icon_button("trash", lambda: self._flag("marked"), "Mark for deletion (X)", size=17)
        self.col_btn = icon_button("layers", self._col_menu, "Collections", size=17)
        self.name = label("", "H3", sel=True)
        self.name.setMinimumWidth(10)
        sv.addWidget(hrow(self.name, None, self.fav_btn, self.mark_btn, self.col_btn, spacing=2))
        self.meta = label("", "Faint", wrap=True, sel=True)
        sv.addWidget(self.meta)
        self.prompt = QPlainTextEdit()
        self.prompt.setReadOnly(True)
        self.prompt.setMaximumHeight(110)
        sv.addWidget(self.prompt)
        self.b = {}
        acts = [("reuse", "Reuse", "refresh"), ("animate", "Animate", "video"), ("extend", "Extend", "arrow"),
                ("upscale", "Upscale", "scale"), ("inpaint", "Edit", "edit"), ("compare", "Comparison", "gallery"),
                ("copyprompt", "Copy prompt", "copy"), ("folder", "Show in folder", "folder"),
                ("move_vault", "To vault", "lock"), ("take_out", "Take out", "unlock"), ("delete", "Delete", "trash")]
        for k, t, ic in acts:
            self.b[k] = button(t, lambda k=k: self._act(k), "Ghost" if k != "reuse" else None, ic)
        acts_box = ChipBox(4)           # wraps: never cut off, whatever is shown for the picked item
        for k in ("reuse", "animate", "extend", "upscale", "inpaint", "compare", "copyprompt", "folder", "move_vault",
                  "take_out", "delete"):
            acts_box.add(self.b[k])
        sv.addWidget(acts_box)
        self.multi = label("", "Faint")
        self.multi_join = button("Join", lambda: self.app.join_entries(self._selected()), "Ghost", "film")
        self.multi_anim = button("Animate", lambda: self.app.animate_entries(self._selected()), "Ghost", "video")
        self.multi_row = hrow(self.multi, None, self.multi_anim, self.multi_join, spacing=4)
        sv.addWidget(self.multi_row)
        self.grid.itemSelectionChanged.connect(self._sel_changed)
        h.addWidget(side)

        self.loader = _Loader()
        self.loader.done.connect(self._loaded)
        self._icons = {}
        self.thumb = 168
        for k, fn in (("F", lambda: self._flag("fav")), ("X", lambda: self._flag("marked")),
                      ("Delete", lambda: self._act("delete")), ("Return", self._view)):
            sc = QShortcut(QKeySequence(k), self.grid, activated=fn)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.history.added.connect(self._changed)
        self.history.removed.connect(self._changed)
        self.history.updated.connect(self._updated)
        self.history.changed.connect(self._fill_nav)
        self._dirty = True
        self._fill_nav()
        self._enable()

    # ------------------------------------------------------------------ helpers
    def app_cfg(self, k, d):
        from .config import cfg
        return cfg.get(k, d) or d

    def _save_sort(self):
        from .config import cfg
        cfg.set("gallery_sort", self.sort.currentData())

    def _changed(self, *_):
        if self.isVisible():
            self.fill()
        else:
            self._dirty = True
        self._fill_nav()

    def _updated(self, eid):
        e = self.history.get(eid)
        if e is None:
            return
        if self.isVisible():
            # it may leave the current place (unfavourited in Favourites ...): a full refill keeps it right
            if not self._in_place(e):
                self.fill()
            else:
                for i in range(self.grid.count()):
                    it = self.grid.item(i)
                    if it.data(Qt.ItemDataRole.UserRole) == eid:
                        it.setIcon(self._icon(e))
                        break
            if self.cur and self.cur["id"] == eid:
                self._flags_ui()
        else:
            self._dirty = True
        self._nav_counts_later()

    def _nav_counts_later(self):
        if not hasattr(self, "_nc"):
            self._nc = QTimer(self, singleShot=True, interval=150, timeout=self._fill_nav)
        self._nc.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.fill()

    def _size(self, k):
        self.thumb = {"s": 120, "m": 168, "l": 240}[k]
        self._icons.clear()
        self.fill()

    # ------------------------------------------------------------------ library column
    def _fill_nav(self):
        cur = self.place
        items = self.history.items
        counts = {"all": len(items), "fav": sum(1 for e in items if e.get("fav")),
                  "marked": sum(1 for e in items if e.get("marked")),
                  "image": sum(1 for e in items if e.get("kind") == "image"),
                  "video": sum(1 for e in items if e.get("kind") == "video")}
        self.nav.blockSignals(True)
        self.nav.clear()

        def head(text):
            it = QListWidgetItem(text.upper())
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(QColor(T.TEXT3))
            f = it.font()
            f.setPixelSize(11)
            f.setBold(True)
            it.setFont(f)
            it.setSizeHint(QSize(0, 30))
            self.nav.addItem(it)
        head("Library")
        for key, text, ic in LIBRARY:
            col = "#F5B041" if key == "fav" else ("#FF8A8A" if key == "marked" else "#A1A1AA")
            it = QListWidgetItem(icons.icon(ic, col, 16), f"{text}   {counts[key]}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)
            if key == cur:
                self.nav.setCurrentItem(it)
        head("Private")
        vt = (f"Vault   {len(vault.entries)}" if vault.is_open() else "Vault   locked") if vault.exists() else "Vault"
        it = QListWidgetItem(icons.icon("lock", T.accent().name(), 16), vt)
        it.setData(Qt.ItemDataRole.UserRole, "vault")
        it.setToolTip("Encrypted pictures and videos, only shown here after you unlock it")
        self.nav.addItem(it)
        if cur == "vault":
            self.nav.setCurrentItem(it)
        if vault.is_open():
            for c in vault.collections:
                n = sum(1 for e in vault.entries if c["id"] in (e.get("cols") or []))
                it = QListWidgetItem(icons.icon("layers", T.accent().name(), 15), f"   {c['name']}   {n}")
                it.setData(Qt.ItemDataRole.UserRole, "vcol:" + c["id"])
                it.setToolTip("A vault collection: drop vault pictures here to add them")
                self.nav.addItem(it)
                if cur == "vcol:" + c["id"]:
                    self.nav.setCurrentItem(it)
        head("Collections")
        if not self.history.collections:
            it = QListWidgetItem("None yet")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(QColor(T.TEXT3))
            self.nav.addItem(it)
        for c in self.history.collections:
            n = sum(1 for e in items if self._in_col(e, c))
            it = QListWidgetItem(icons.icon("sparkle" if c.get("smart") else "layers", "#A1A1AA", 16),
                                 f"{c['name']}   {n}")
            it.setData(Qt.ItemDataRole.UserRole, "col:" + c["id"])
            it.setToolTip("Smart collection: everything that fits its filter" if c.get("smart")
                          else "Drop pictures here to add them")
            self.nav.addItem(it)
            if "col:" + c["id"] == cur:
                self.nav.setCurrentItem(it)
        self.nav.blockSignals(False)

    def _in_col(self, e, c):
        if c.get("smart"):
            return _matches(e, c["smart"])
        return c["id"] in (e.get("cols") or [])

    def _get(self, eid):
        return self.history.get(eid) or (vault.get(eid) if vault.is_open() else None)

    def vault_changed(self):
        """Opened, locked or changed: thumbnails decrypted for it go when it locks. Its collections slide out from
        under the Vault entry when it opens, and back in when it locks."""
        was_open, now_open = getattr(self, "_nav_open", False), vault.is_open()
        self._nav_open = now_open
        if was_open and not now_open and anims_on() and self.isVisible():
            items = self._vcol_items()
            if items:
                # pictures and thumbnails go at once; only the collection entries shrink away afterwards
                self._vault_changed(nav=False)
                self._slide(items, opening=False, then=self._fill_nav)
                return
        self._vault_changed()
        if now_open and not was_open and anims_on() and self.isVisible():
            items = self._vcol_items()
            if items:
                self._slide(items, opening=True)

    def _vcol_items(self):
        return [self.nav.item(i) for i in range(self.nav.count())
                if str(self.nav.item(i).data(Qt.ItemDataRole.UserRole) or "").startswith("vcol:")]

    def _slide(self, items, opening, then=None):
        """The vault's collections grow out of (or shrink back into) the Vault entry, one after the other."""
        full = max(24, self.nav.visualItemRect(items[0]).height() or 32)
        col = QColor(T.TEXT2)
        n = len(items)
        lag = 0.35 / max(1, n)

        def step(v):
            for k, it in enumerate(items):
                t = min(1.0, max(0.0, (v - k * lag) / (1.0 - lag * (n - 1)) if n > 1 else v))
                t = t if opening else 1.0 - t
                try:
                    it.setSizeHint(QSize(0, int(full * t)))
                    c = QColor(col)
                    c.setAlphaF(t)
                    it.setForeground(c)
                except RuntimeError:
                    return
            self.nav.doItemsLayout()
        anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=240 + 60 * min(n, 6),
                                 easingCurve=QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(step)
        step(0.0)

        def done():
            for it in items:
                try:
                    it.setSizeHint(QSize())             # back to its natural height
                    it.setData(Qt.ItemDataRole.ForegroundRole, None)
                except RuntimeError:
                    pass
            self.nav.doItemsLayout()
            if then:
                then()
        anim.finished.connect(done)
        anim.start()
        self._slide_anim = anim

    def _vault_changed(self, nav=True):
        if not vault.is_open():
            if hasattr(self, "_vthumbs"):
                self._vgen += 1
                self._vthumbs, self._vdata = {}, {}
                self._vqueue.clear()
            if self.cur and self.cur.get("vault"):
                self.cur = None
            if self.place.startswith("vcol:"):
                self.place = "vault"            # locked: its collections are hidden, the lock panel shows
        if nav:
            self._fill_nav()
        if self.isVisible():
            self.fill()
        else:
            self._dirty = True

    def _in_place(self, e):
        p = self.place
        if p == "vault":
            return bool(e.get("vault"))
        if p.startswith("vcol:"):
            return bool(e.get("vault")) and p[5:] in (e.get("cols") or [])
        if p in ("fav", "marked"):
            return bool(e.get(p))
        if p in ("image", "video"):
            return e.get("kind") == p
        if p.startswith("col:"):
            c = self.history.collection(p[4:])
            return bool(c) and self._in_col(e, c)
        return True

    def _go(self, key):
        if not key:
            return
        self.place = key
        self.fill()

    def _new_collection(self, ids=None):
        name, ok = QInputDialog.getText(self, "New collection", "Name:")
        if not ok or not name.strip():
            return None
        c = self.history.add_collection(name)
        if ids:
            self.history.set_in_collection(ids, c["id"], True)
        self.place = "col:" + c["id"]
        self._fill_nav()
        self.fill()
        return c

    def _nav_menu(self, pos):
        it = self.nav.itemAt(pos)
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        if key == "vault" and vault.is_open():
            m = QMenu(self)
            m.addAction(icons.icon("plus", "#A1A1AA", 16), "New vault collection…").triggered.connect(
                lambda: self._new_vcol())
            m.exec(self.nav.mapToGlobal(pos))
            return
        if key and key.startswith("vcol:") and vault.is_open():
            c = vault.collection(key[5:])
            if c:
                m = QMenu(self)
                m.addAction("Rename…").triggered.connect(lambda: self._rename_vcol(c))
                m.addAction(icons.icon("trash", "#FF8A8A", 16), "Delete the collection (keeps its pictures)"
                            ).triggered.connect(lambda: self._del_vcol(c))
                m.addSeparator()
                m.addAction(icons.icon("plus", "#A1A1AA", 16), "New vault collection…").triggered.connect(
                    lambda: self._new_vcol())
                m.exec(self.nav.mapToGlobal(pos))
            return
        if not key or not key.startswith("col:"):
            return
        c = self.history.collection(key[4:])
        m = QMenu(self)
        m.addAction("Rename…").triggered.connect(lambda: self._rename(c))
        m.addAction(icons.icon("trash", "#FF8A8A", 16), "Delete the collection (keeps its pictures)").triggered.connect(
            lambda: self._del_collection(c))
        m.exec(self.nav.mapToGlobal(pos))

    # ---- vault collections
    def _new_vcol(self, ids=None):
        name, ok = QInputDialog.getText(self, "New vault collection", "Name:")
        if not ok or not name.strip() or not vault.is_open():
            return None
        c = vault.add_collection(name)
        if ids:
            vault.set_in_collection(ids, c["id"], True)
        self.place = "vcol:" + c["id"]
        self._fill_nav()
        self.fill()
        return c

    def _rename_vcol(self, c):
        name, ok = QInputDialog.getText(self, "Rename collection", "Name:", text=c["name"])
        if ok and name.strip():
            vault.rename_collection(c["id"], name)
            self._fill_nav()

    def _del_vcol(self, c):
        if QMessageBox.question(self, "Collection", f"Delete the collection '{c['name']}'? Its pictures and videos stay "
                                                    "in the vault.") == QMessageBox.StandardButton.Yes:
            if self.place == "vcol:" + c["id"]:
                self.place = "vault"
            vault.remove_collection(c["id"])
            self._fill_nav()
            self.fill()

    def _vcol_menu(self, entries, pos=None):
        """Vault collections for vault pictures: in or out (ticks), or a new one."""
        if not entries or not vault.is_open():
            return
        m = QMenu(self)
        for c in vault.collections:
            inside = all(c["id"] in (e.get("cols") or []) for e in entries)
            a = m.addAction(c["name"])
            a.setCheckable(True)
            a.setChecked(inside)
            a.triggered.connect(lambda _=False, c=c, inside=inside: self._vcol_set(entries, c, not inside))
        if vault.collections:
            m.addSeparator()
        m.addAction(icons.icon("plus", "#A1A1AA", 16), "New vault collection…").triggered.connect(
            lambda: self._new_vcol([e["id"] for e in entries]))
        m.exec(pos or self.col_btn.mapToGlobal(self.col_btn.rect().bottomLeft()))

    def _vcol_set(self, entries, c, on):
        vault.set_in_collection([e["id"] for e in entries], c["id"], on)
        self._fill_nav()
        self.fill()
        self.app.toast(f"{'Added to' if on else 'Taken out of'} {c['name']}.", "ok")

    def _rename(self, c):
        name, ok = QInputDialog.getText(self, "Rename collection", "Name:", text=c["name"])
        if ok and name.strip():
            self.history.rename_collection(c["id"], name)

    def _del_collection(self, c):
        if QMessageBox.question(self, "Collection", f"Delete the collection '{c['name']}'? Its pictures and videos stay "
                                                    "in the gallery.") == QMessageBox.StandardButton.Yes:
            if self.place == "col:" + c["id"]:
                self.place = "all"
            self.history.remove_collection(c["id"])
            self.fill()

    # ------------------------------------------------------------------ filters
    def _filter_menu(self):
        items = self.history.items
        models = sorted({e.get("model") for e in items if e.get("model")}, key=lambda x: nice_name(x).lower())
        loras = sorted({x for e in items for x in (e.get("loras") or []) if x}, key=lambda x: nice_name(x).lower())
        m = QMenu(self)
        mm = m.addMenu(icons.icon("image", "#A1A1AA", 16), "Checkpoint / model")
        for x in models:
            a = mm.addAction(nice_name(x))
            a.setCheckable(True)
            a.setChecked(self.flt.get("model") == x)
            a.triggered.connect(lambda _=False, x=x: self._set_filter("model", x))
        if not models:
            mm.addAction("Nothing yet").setEnabled(False)
        lm = m.addMenu(icons.icon("lora", "#A1A1AA", 16), "LoRA")
        for x in loras:
            a = lm.addAction(nice_name(x))
            a.setCheckable(True)
            a.setChecked(self.flt.get("lora") == x)
            a.triggered.connect(lambda _=False, x=x: self._set_filter("lora", x))
        if not loras:
            lm.addAction("Nothing yet").setEnabled(False)
        if self.flt:
            m.addSeparator()
            m.addAction("Clear filters").triggered.connect(lambda: (self.flt.clear(), self.fill()))
        m.exec(self.filter_btn.mapToGlobal(self.filter_btn.rect().bottomLeft()))

    def _set_filter(self, k, v):
        if self.flt.get(k) == v:
            self.flt.pop(k, None)
        else:
            self.flt[k] = v
        self.fill()

    def _chips(self):
        while self.chip_lay.count():
            w = self.chip_lay.takeAt(0).widget()
            if w and w is not self.save_smart:
                w.deleteLater()
        for k, title in (("model", "Model"), ("lora", "LoRA")):
            if self.flt.get(k):
                c = chip(f"{title}: {nice_name(self.flt[k])}   ✕", None, False, "Remove this filter")
                c.clicked.connect(lambda _=False, k=k: (self.flt.pop(k, None), self.fill()))
                self.chip_lay.addWidget(c)
        active = bool(self.flt) or bool(self.search.text().strip())
        self.chip_lay.addWidget(self.save_smart)
        self.save_smart.setVisible(active)
        self.chip_lay.addStretch(1)
        self.chips.setVisible(active)

    def _current_filter(self):
        f = dict(self.flt)
        if self.search.text().strip():
            f["q"] = self.search.text().strip()
        if self.place in ("fav", "marked"):
            f[self.place] = True
        elif self.place in ("image", "video"):
            f["kind"] = self.place
        return f

    def _save_smart(self):
        f = self._current_filter()
        name, ok = QInputDialog.getText(self, "Smart collection", "Name (it always shows everything that fits the "
                                                                  "current search and filters):")
        if ok and name.strip():
            c = self.history.add_collection(name, smart=f)
            self.flt.clear()
            self.search.clear()
            self.place = "col:" + c["id"]
            self._fill_nav()
            self.fill()

    # ------------------------------------------------------------------ grid
    def _icon(self, e):
        key = (e["id"], self.thumb, bool(e.get("fav")), bool(e.get("marked")))
        if e.get("vault"):
            return self._vicon(e)
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
                if e.get("marked"):
                    p.setOpacity(0.38)
                p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)
                p.setOpacity(1.0)
            p.setClipping(False)
            if e.get("kind") == "video":
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
            if e.get("fav"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 140))
                p.drawEllipse(QPointF(s - 34, 34), 22, 22)
                p.drawPixmap(QPointF(s - 50, 18), icons.pixmap("starf", 32, "#F5B041", dpr=1.0))
            if e.get("marked"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T.BAD))
                p.drawEllipse(QPointF(34, 34), 22, 22)
                p.drawPixmap(QPointF(19, 19), icons.pixmap("trash", 30, "#FFFFFF", dpr=1.0))
            p.end()
            out.setDevicePixelRatio(2.0)
            self._icons[key] = QIcon(out)
        return self._icons[key]

    def _vicon(self, e):
        """A vault thumbnail: decrypted in the background (a lock tile until then), only ever in memory and
        forgotten when the vault locks."""
        if not hasattr(self, "_vthumbs"):
            self._vthumbs, self._vdata, self._vgen, self._vqueue, self._vbusy = {}, {}, 0, deque(), False
            self._vph = {}                              # the lock tiles shown until a thumbnail is decrypted
            self._vthumb_ready.connect(self._vthumb_done)
        key = (e["id"], self.thumb)
        if key in self._vthumbs:
            return self._vthumbs[key]
        if e["id"] in self._vdata:
            self._vthumbs[key] = self._vrender(e, self._vdata[e["id"]])
            return self._vthumbs[key]
        self._vqueue.append(e)
        if not self._vbusy:
            self._vbusy = True
            threading.Thread(target=self._vwork, args=(self._vgen,), daemon=True).start()
        ph = (e.get("kind"), self.thumb)
        if ph not in self._vph:
            self._vph[ph] = self._vrender(e, None)
        return self._vph[ph]

    def _vwork(self, gen):
        while gen == self._vgen:
            try:
                e = self._vqueue.popleft()
            except IndexError:
                self._vbusy = False
                if not self._vqueue:                    # nothing came in while it was stopping
                    return
                self._vbusy = True
                continue
            try:
                data = vault.read(e, "thumb_blob") or b""
            except Exception:
                data = b""
            try:
                self._vthumb_ready.emit(e["id"], gen, data)
            except RuntimeError:                        # the window closed meanwhile
                return
        self._vbusy = False

    def _vthumb_done(self, eid, gen, data):
        if gen != self._vgen or not vault.is_open():
            return
        self._vdata[eid] = data
        e = vault.get(eid)
        if e is None:
            return
        ic = self._vthumbs[(eid, self.thumb)] = self._vrender(e, data)
        it = self._rows.get(eid)
        if it is not None:
            try:
                it.setIcon(ic)
            except RuntimeError:                        # the grid was refilled meanwhile
                pass

    def _vrender(self, e, data):
        s = self.thumb * 2
        src = QPixmap()
        if data:
            src.loadFromData(data)
        out = QPixmap(s, s)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, s, s), 20, 20)
        p.setClipPath(path)
        p.fillRect(out.rect(), QColor(T.SURFACE2))
        if not src.isNull():
            src = src.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((s - src.width()) // 2, (s - src.height()) // 2, src)
        p.setClipping(False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(QPointF(s - 34, s - 34), 22, 22)
        p.drawPixmap(QPointF(s - 49, s - 49), icons.pixmap("lock", 30, "#FFFFFF", dpr=1.0))
        if e.get("kind") == "video":
            p.drawEllipse(QPointF(34, s - 34), 22, 22)
            p.drawPixmap(QPointF(21, s - 47), icons.pixmap("play", 26, "#FFFFFF", dpr=1.0))
        p.end()
        out.setDevicePixelRatio(2.0)
        return QIcon(out)

    def _sorted(self, items):
        k = self.sort.currentData()
        if k == "old":
            return sorted(items, key=lambda e: e.get("created", ""))
        if k == "model":
            return sorted(items, key=lambda e: (nice_name(e.get("model") or "~").lower(), e.get("created", "")),
                          reverse=False)
        if k == "lora":
            return sorted(items, key=lambda e: (nice_name((e.get("loras") or ["~"])[0]).lower(), e.get("created", "")))
        if k == "preset":
            order = {"ultra": 0, "fast": 1, "draft": 1, "balanced": 2, "quality": 3, "best": 4}
            return sorted(items, key=lambda e: (order.get(e.get("preset"), 9), e.get("created", "")))
        if k == "type":
            return sorted(items, key=lambda e: (e.get("kind", ""), e.get("created", "")))
        if k == "size":
            return sorted(items, key=lambda e: -(int(e.get("w") or 0) * int(e.get("h") or 0)))
        return sorted(items, key=lambda e: e.get("created", ""), reverse=True)

    def fill(self):
        self._dirty = False
        f = dict(self.flt)
        if self.search.text().strip():
            f["q"] = self.search.text().strip()
        in_vault = self.place == "vault" or self.place.startswith("vcol:")
        source = (vault.entries if vault.is_open() else []) if in_vault else self.history.items
        items = [e for e in source if self._in_place(e) and _matches(e, f)]
        items = self._sorted(items)
        keep = self.cur["id"] if self.cur else None
        self._shown = items[:5000]
        self.grid.blockSignals(True)
        self.grid.clear()
        self._rows = {}
        self.grid.setIconSize(QSize(self.thumb, self.thumb))
        self.grid.setGridSize(QSize(self.thumb + 14, self.thumb + 14))
        for e in self._shown:
            it = QListWidgetItem(self._icon(e), "")
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setToolTip((e.get("prompt") or "")[:300])
            self.grid.addItem(it)
            self._rows[e["id"]] = it
            if e["id"] == keep:
                self.grid.setCurrentItem(it)
        self.grid.blockSignals(False)
        self.count.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")
        self.grid.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.empty.setText({"fav": "No favourites yet. Press F (or the star) on anything you like.",
                            "marked": "Nothing marked. Press X on what should go, then delete them all at once here.",
                            }.get(self.place, "Nothing here yet. Everything you generate shows up here, with its "
                                              "settings." if not (f or self.place.startswith("col:")) else
                                  "Nothing fits here."))
        locked = in_vault and not vault.is_open()
        self.lockpanel.setVisible(locked)
        self.vbar.setVisible(in_vault and vault.is_open())
        self.vbar_text.setText(f"{len(vault.entries)} in the vault  ·  decrypted only in memory, never to disk")
        if locked:
            self.grid.hide()
            self.empty.hide()
            made = vault.exists()
            self.lock_title.setText("The vault is locked" if made else "Your vault")
            self.lock_text.setText("Unlock it to see what is inside." if made else
                                   "A place for pictures and videos only you can open: encrypted with your password "
                                   "(Argon2id + AES-256-GCM), only ever decrypted in memory. Turn on Private next to "
                                   "Generate to make things straight into it, or move pictures in from the gallery.")
            self.lock_btn.setText("Unlock" if made else "Make the vault")
        elif in_vault and not items:
            self.empty.setText("The vault is empty. Turn on Private (the lock next to Generate) or use 'To vault' on a "
                               "picture.")
        marked = sum(1 for e in self.history.items if e.get("marked"))
        self.banner.setVisible(self.place == "marked" and marked > 0)
        self.banner_text.setText(f"{marked} marked for deletion")
        self._chips()
        if self.cur and (not self._get(self.cur["id"]) or self.cur["id"] not in {e["id"] for e in self._shown}):
            self.cur = None
            self.player.message("Pick something", "Its settings show here.")
            self.name.setText("")
            self.meta.setText("")
            self.prompt.setPlainText("")
        self._enable()

    # ------------------------------------------------------------------ details
    def show(self, eid):
        e = self._get(eid)
        if e is None:
            return
        self.cur = e
        self.name.setText((e.get("name") or f"Private {'video' if e.get('kind') == 'video' else 'picture'}")
                         if e.get("vault") else nice_name(e["file"]))
        p = e.get("params") or {}
        bits = [f"{e.get('w')} × {e.get('h')}", f"seed {e.get('seed')}", nice_name(e.get("model") or "")]
        if e.get("kind") == "video":
            bits[1:1] = [f"{e.get('seconds')} s · {int(e.get('fps', 16))} fps · {e.get('loop', '')}"]
        else:
            bits.append(p.get("preset", ""))
        if e.get("loras"):
            bits.append("LoRAs: " + ", ".join(nice_name(x) for x in e["loras"] if x))
        cols = [self.history.collection(c) for c in e.get("cols") or []]
        if any(cols):
            bits.append("In: " + ", ".join(c["name"] for c in cols if c))
        bits.append(e.get("created", ""))
        if e.get("took"):
            bits.append(f"made in {e['took']:.0f}s")
        self.meta.setText("  ·  ".join(b for b in bits if b))
        self.prompt.setPlainText(e.get("final_prompt") or e.get("prompt") or "")
        if e.get("vault"):
            if e["kind"] == "video":
                self.player.message("Loading…")
                self.loader.load(e["id"], None, e)
            else:
                pm = QPixmap()
                pm.loadFromData(vault.read(e))
                self.player.show_image(pm, "In the vault")
        elif e["kind"] == "video":
            self.player.show_image(e.get("thumb") or "", "Loading…", fade=False)
            self.loader.load(e["id"], e["file"])
        else:
            self.player.show_image(e["file"])
        self._flags_ui()
        self._enable()

    def _flags_ui(self):
        e = self.cur or {}
        self.fav_btn.setIcon(icons.icon("starf" if e.get("fav") else "star", "#F5B041" if e.get("fav") else "#A1A1AA", 17))
        self.mark_btn.setIcon(icons.icon("trash", "#FF6B6B" if e.get("marked") else "#A1A1AA", 17))
        self.fav_btn.setToolTip("Favourite (F)" + ("  ·  on" if e.get("fav") else ""))
        self.mark_btn.setToolTip("Marked for deletion (X) - delete all marked ones from 'Marked for deletion'"
                                 if e.get("marked") else "Mark for deletion (X)")

    def _loaded(self, key, frames, fps):
        if self.cur and self.cur["id"] == key and frames:
            self.player.play(frames, fps, f"{fps:.0f} fps")

    def _selected(self):
        sel = [self._get(i.data(Qt.ItemDataRole.UserRole)) for i in self.grid.selectedItems()]
        return [x for x in sel if x] or ([self.cur] if self.cur else [])

    def _sel_changed(self):
        sel = self._selected()
        n = len(sel)
        vids = len([e for e in sel if e["kind"] == "video"])
        pics = n - vids
        self.multi_row.setVisible(n > 1)
        self.multi.setText(f"{n} selected  ·  F, X and Del work on all of them")
        self.multi_join.setVisible(vids > 1)
        self.multi_anim.setVisible(pics > 0)

    def _enable(self):
        on = self.cur is not None
        for k, b in self.b.items():
            b.setEnabled(on)
        for b in (self.fav_btn, self.mark_btn, self.col_btn):
            b.setEnabled(on)
        # nothing picked: the buttons of a normal picture (greyed out)
        img = not on or self.cur["kind"] == "image"
        inv = on and bool(self.cur.get("vault"))
        self.b["animate"].setVisible(img)
        self.b["upscale"].setVisible(img)
        self.b["inpaint"].setVisible(img)
        self.b["extend"].setVisible(not img)
        self.b["compare"].setVisible(on and bool(self.cur.get("group")) and not inv)
        self.b["folder"].setVisible(not inv)
        self.b["move_vault"].setVisible(not inv)
        self.b["take_out"].setVisible(inv)
        for b in (self.fav_btn, self.mark_btn):
            b.setVisible(not inv)
        self.col_btn.setToolTip("Vault collections" if inv else "Collections")
        if hasattr(self, "multi_row"):
            self._sel_changed()

    # ------------------------------------------------------------------ actions
    def _flag(self, key):
        sel = [e for e in self._selected() if not e.get("vault")]
        if not sel:
            return
        on = self.app.toggle_flag(sel, key)
        if key == "marked" and on and len(sel) == 1 and self.place not in ("marked",):
            self._next()                # culling: on to the next one

    def _next(self):
        r = self.grid.currentRow()
        if 0 <= r < self.grid.count() - 1:
            self.grid.setCurrentRow(r + 1)

    def _unmark_all(self):
        for e in [x for x in self.history.items if x.get("marked")]:
            self.history.update(e["id"], marked=False)
        self.fill()

    def _delete_marked(self):
        marked = [x for x in self.history.items if x.get("marked")]
        if marked:
            self.app.delete_entries(marked)

    def _col_menu(self, entries=None, pos=None):
        entries = entries or self._selected()
        if not entries:
            return
        if entries[0].get("vault"):
            return self._vcol_menu([e for e in entries if e.get("vault")], pos)
        m = QMenu(self)
        manual = [c for c in self.history.collections if not c.get("smart")]
        for c in manual:
            inside = all(c["id"] in (e.get("cols") or []) for e in entries)
            a = m.addAction(c["name"])
            a.setCheckable(True)
            a.setChecked(inside)
            a.triggered.connect(lambda _=False, c=c, inside=inside: self.history.set_in_collection(
                [e["id"] for e in entries], c["id"], not inside))
        if manual:
            m.addSeparator()
        m.addAction(icons.icon("plus", "#A1A1AA", 16), "New collection…").triggered.connect(
            lambda: self._new_collection([e["id"] for e in entries]))
        m.exec(pos or self.col_btn.mapToGlobal(self.col_btn.rect().bottomLeft()))

    def _act(self, k):
        if not self.cur:
            return
        if k == "copyprompt" and self.cur.get("vault"):
            self.app.result_action(k, self.cur, self)
            return
        if k == "copyprompt":
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.cur.get("prompt") or "")
            self.app.toast("Prompt copied.", "ok")
            return
        if k == "delete":
            sel = self._selected()
            if sel and sel[0].get("vault"):
                self.app.delete_vault(sel)
            else:
                self.app.delete_entries(sel)
            return
        if k == "move_vault":
            self.app.move_to_vault([e for e in self._selected() if not e.get("vault")])
            return
        if k == "take_out":
            self.app.take_out_of_vault([e for e in self._selected() if e.get("vault")])
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
        e = self._get(it.data(Qt.ItemDataRole.UserRole))
        if e is None:
            return
        sel = self._selected() or [e]
        many = len(sel) > 1
        m = QMenu(self)
        if e.get("vault"):
            if not many:
                m.addAction(icons.icon("eye", "#A1A1AA", 16), "View").triggered.connect(self._view)
                m.addAction(icons.icon("refresh", "#A1A1AA", 16), "Reuse settings").triggered.connect(
                    lambda: self.app.result_action("reuse", e, self))
                if e["kind"] == "image":
                    for k, t, ic in (("animate", "Animate (private)", "video"), ("upscale", "Upscale (private)…", "scale"),
                                     ("inpaint", "Edit a part (private)…", "edit")):
                        m.addAction(icons.icon(ic, "#A1A1AA", 16), t).triggered.connect(
                            lambda _=False, k=k: self.app.result_action(k, e, self))
                else:
                    m.addAction(icons.icon("arrow", "#A1A1AA", 16), "Extend (private)").triggered.connect(
                        lambda: self.app.result_action("extend", e, self))
            m.addSeparator()
            m.addAction(icons.icon("layers", "#A1A1AA", 16), "Vault collections…").triggered.connect(
                lambda: self._vcol_menu(sel, self.grid.mapToGlobal(pos)))
            if self.place.startswith("vcol:"):
                c = vault.collection(self.place[5:])
                if c:
                    m.addAction(icons.icon("close", "#A1A1AA", 16), f"Take out of '{c['name']}'").triggered.connect(
                        lambda: self._vcol_set(sel, c, False))
            m.addAction(icons.icon("unlock", "#A1A1AA", 16), f"Take out of the vault{f' ({len(sel)})' if many else ''}"
                        ).triggered.connect(lambda: self.app.take_out_of_vault(sel))
            m.addAction(icons.icon("trash", "#FF8A8A", 16), f"Delete{f' {len(sel)} items' if many else ''}"
                        ).triggered.connect(lambda: self.app.delete_vault(sel))
            m.exec(self.grid.mapToGlobal(pos))
            return
        if not many:
            m.addAction(icons.icon("external", "#A1A1AA", 16), "Open").triggered.connect(lambda: self._open(e))
            m.addAction(icons.icon("refresh", "#A1A1AA", 16), "Reuse settings").triggered.connect(
                lambda: self.app.result_action("reuse", e, self))
        allfav = all(x.get("fav") for x in sel)
        allmark = all(x.get("marked") for x in sel)
        m.addAction(icons.icon("starf" if not allfav else "star", "#F5B041", 16),
                    ("Remove from favourites" if allfav else "Favourite") + (f" ({len(sel)})" if many else "")
                    ).triggered.connect(lambda: self.app.toggle_flag(sel, "fav"))
        m.addAction(icons.icon("trash", "#FF8A8A", 16),
                    ("Unmark" if allmark else "Mark for deletion") + (f" ({len(sel)})" if many else "")
                    ).triggered.connect(lambda: self.app.toggle_flag(sel, "marked"))
        m.addAction(icons.icon("layers", "#A1A1AA", 16), "Collections…").triggered.connect(
            lambda: self._col_menu(sel, self.grid.mapToGlobal(pos)))
        m.addSeparator()
        if not many:
            if e["kind"] == "image":
                m.addAction(icons.icon("video", "#A1A1AA", 16), "Animate").triggered.connect(
                    lambda: self.app.result_action("animate", e, self))
                m.addAction(icons.icon("scale", "#A1A1AA", 16), "Upscale…").triggered.connect(
                    lambda: self.app.result_action("upscale", e, self))
                m.addAction(icons.icon("edit", "#A1A1AA", 16), "Edit a part…").triggered.connect(
                    lambda: self.app.result_action("inpaint", e, self))
                m.addAction(icons.icon("copy", "#A1A1AA", 16), "Copy picture").triggered.connect(
                    lambda: self.app.result_action("copy", e, self))
            else:
                m.addAction(icons.icon("arrow", "#A1A1AA", 16), "Extend from last frame").triggered.connect(
                    lambda: self.app.result_action("extend", e, self))
            m.addAction(icons.icon("folder", "#A1A1AA", 16), "Show in folder").triggered.connect(
                lambda: self.app.result_action("folder", e, self))
            if e.get("group"):
                m.addAction(icons.icon("gallery", "#A1A1AA", 16), "Show the comparison").triggered.connect(
                    lambda: self.app.open_compare(e["group"]))
        if len([x for x in sel if x["kind"] == "video"]) > 1:
            m.addAction(icons.icon("film", "#A1A1AA", 16), "Join into one video").triggered.connect(
                lambda: self.app.join_entries(sel))
        if many and any(x["kind"] == "image" for x in sel):
            m.addAction(icons.icon("video", "#A1A1AA", 16), "Animate all").triggered.connect(
                lambda: self.app.animate_entries(sel))
        m.addSeparator()
        m.addAction(icons.icon("lock", "#A1A1AA", 16), f"Move to the vault{f' ({len(sel)})' if many else ''}"
                    ).triggered.connect(lambda: self.app.move_to_vault(sel))
        m.addAction(icons.icon("trash", "#FF8A8A", 16), f"Delete{f' {len(sel)} items' if many else ''}"
                    ).triggered.connect(lambda: self.app.delete_entries(sel))
        m.exec(self.grid.mapToGlobal(pos))
