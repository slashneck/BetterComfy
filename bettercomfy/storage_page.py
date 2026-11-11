"""The STORAGE tab: what takes up space, biggest first, and what to do about it: delete, move or compress. The vault's
files are counted but never touched here."""
import os
import shutil
import subprocess
import threading

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QMessageBox, QProgressBar, QSizePolicy,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import comfy, media, shred, theme as T, vault
from .config import BASE, cfg
from .export import _IMG, clean_path, strip_picture
from .widgets import Card, Combo, Slider, button, field, hrow, label

_ROW = Qt.ItemDataRole.UserRole
MAX_ROWS = 1000


def nice_size(n):
    return f"{n / 2 ** 30:.2f} GB" if n >= 2 ** 30 else f"{n / 2 ** 20:.1f} MB" if n >= 2 ** 20 else f"{n / 1024:.0f} KB"


def _folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _is_video(e):
    return e.get("kind") == "video"


def compress_picture(src, fmt, quality, longest):
    """A smaller file next to the original. Returns its path (the original is still there)."""
    ext = {"WEBP": ".webp", "JPEG": ".jpg"}[fmt]
    tmp = os.path.splitext(src)[0] + ".compressing" + ext
    strip_picture(src, tmp, fmt, longest, quality)
    return tmp


def compress_video(src, crf, half):
    import imageio_ffmpeg
    tmp = os.path.splitext(src)[0] + ".compressing.mp4"
    args = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-c:v", "libx264", "-crf",
            str(crf), "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    if half:
        args += ["-vf", "scale=trunc(iw/4)*2:trunc(ih/4)*2"]
    # below normal priority: a game or anything else you are doing comes first
    r = subprocess.run(args + [tmp], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    if r.returncode:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise OSError(r.stderr.decode("utf-8", "replace")[-300:])
    return tmp


class CompressDialog(QDialog):
    def __init__(self, parent, pics, vids):
        super().__init__(parent)
        self.setWindowTitle("Compress")
        self.resize(500, 10)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(label(f"Compress {pics + vids} item{'s' if pics + vids != 1 else ''}", "H2"))
        self.fmt = Combo()
        self.fmt.addItem("WebP (smallest)", "WEBP")
        self.fmt.addItem("JPG (opens everywhere)", "JPEG")
        self.quality = Slider(60, 100, 1, 0, 90, width=70)
        self.size = Combo()
        for t, k in (("Keep the size", 0), ("Longest side 2048", 2048), ("Longest side 1536", 1536), ("Longest side 1024", 1024)):
            self.size.addItem(t, k)
        self.video = Combo()
        for t, k in (("High quality", 23), ("Medium", 28), ("Smallest", 33)):
            self.video.addItem(t, k)
        self.half = Combo()
        self.half.addItem("Keep the size", False)
        self.half.addItem("Half the size", True)
        if pics:
            v.addWidget(field("Pictures", self.fmt, label_w=80))
            v.addWidget(field("Quality", self.quality, label_w=80))
            v.addWidget(field("Size", self.size, label_w=80))
        if vids:
            v.addWidget(field("Videos", self.video, label_w=80))
            v.addWidget(field("Size", self.half, label_w=80))
        v.addWidget(label("Each one is only replaced if the new file is really smaller. The original goes the way your "
                          "deleted files go (Settings, Deleting). The gallery keeps the prompt and settings, but the "
                          "compressed file no longer carries them.", "Faint", wrap=True))
        v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Compress", self.accept, "Accent"), spacing=6))

    def options(self):
        return {"fmt": self.fmt.currentData(), "quality": int(self.quality.value()), "longest": self.size.currentData() or None,
                "crf": self.video.currentData(), "half": self.half.currentData()}


class StoragePage(QWidget):
    title = "Storage"
    subtitle = "What takes up space, biggest first"
    _scanned = Signal(object, object)           # sizes per entry id, totals per area
    _prog = Signal(int, int, str)
    _moved = Signal(object, int)                # [(entry id, its new fields)], how many failed
    _freed = Signal(object, int, object)        # the same, bytes saved, the first error

    def __init__(self, app):
        super().__init__()
        self.app, self.history = app, app.history
        self.sizes = {}
        self.busy = False
        self._dirty = True
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 16, 24, 14)
        v.setSpacing(12)
        self.summary = Card("On this PC")
        self.bars = QWidget()
        self.bars_lay = QHBoxLayout(self.bars)
        self.bars_lay.setContentsMargins(0, 0, 0, 0)
        self.bars_lay.setSpacing(24)
        self.summary.add(self.bars)
        v.addWidget(self.summary)
        self.show_what = Combo()
        for t, k in (("Pictures and videos", ""), ("Pictures", "image"), ("Videos", "video")):
            self.show_what.addItem(t, k)
        self.show_what.setMinimumWidth(190)
        self.show_what.currentIndexChanged.connect(lambda _i: self.fill())
        self.sel_text = label("", "Muted")
        self.del_btn = button("Delete", self._delete, "Ghost", "trash", icon_color="#FF8A8A")
        self.move_btn = button("Move…", self._move, "Ghost", "folder")
        self.comp_btn = button("Compress…", self._compress, "Ghost", "scale")
        self.refresh_btn = button("", lambda: self.scan(), "Ghost", "refresh", "Measure again")
        box = Card(None, margins=(14, 12, 14, 12))     # one calm surface: the list stays readable over a background
        v.addWidget(box, 1)
        box.body.addWidget(hrow(self.show_what, self.sel_text, None, self.comp_btn, self.move_btn, self.del_btn, self.refresh_btn,
                         spacing=6))
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(40, 40))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setHeaderLabels(["Name", "Type", "Made", "Size"])
        self.tree.setColumnWidth(0, 560)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 110)
        self.tree.itemSelectionChanged.connect(self._selection)
        box.body.addWidget(self.tree, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = label("", "Faint")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.body.addWidget(self.bar)
        box.body.addWidget(self.status)
        self.todo = []
        self.timer = QTimer(self, interval=0, timeout=self._load_thumbs)
        self._scanned.connect(self._got_scan)
        self._prog.connect(self._progress)
        self._moved.connect(self._apply_moved)
        self._freed.connect(self._apply_freed)
        for sig in (self.history.added, self.history.removed):
            sig.connect(lambda *_a: self._mark())
        self.history.restored.connect(self._mark)
        self._selection()

    def _mark(self):
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(400, self._scan_if_dirty)

    def _scan_if_dirty(self):
        if self._dirty and self.isVisible():
            self.scan()

    def showEvent(self, e):
        super().showEvent(e)
        self._scan_if_dirty()

    # ---- measuring (off the main thread: a big gallery or a models folder takes a moment)
    def scan(self):
        self._dirty = False
        self.status.setText("Measuring…")
        items = list(self.history.items)
        ins = self.app.link.install()

        def work():
            sizes = {}
            pics = vids = 0
            for e in items:
                try:
                    n = os.path.getsize(e["file"])
                except (OSError, KeyError):
                    continue
                sizes[e["id"]] = n
                if _is_video(e):
                    vids += n
                else:
                    pics += n
            totals = [("Pictures", pics), ("Videos", vids), ("Vault", _folder_size(vault.DIR))]
            totals.append(("App cache", sum(_folder_size(os.path.join(BASE, d)) for d in
                                            ("thumbs", "masks", "frames", "drag-out", "logs", "lora_covers"))))
            if ins:
                totals.append(("Models", _folder_size(comfy.sub_dir(ins, "models"))))
            try:
                self._scanned.emit(sizes, totals)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _got_scan(self, sizes, totals):
        self.sizes = sizes
        while self.bars_lay.count():
            w = self.bars_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        top = max([n for _t, n in totals] + [1])
        colours = {"Pictures": T.accent().name(), "Videos": T.GOOD, "Vault": T.WARN, "App cache": T.TEXT3, "Models": "#4F8CFF"}
        for name, n in totals:
            col = QVBoxLayout()
            col.setSpacing(3)
            box = QWidget()
            box.setLayout(col)
            col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(label(name, "Muted"))
            col.addWidget(label(nice_size(n), "H2"))
            line = QProgressBar()
            line.setRange(0, 1000)
            line.setValue(int(n * 1000 / top))
            line.setTextVisible(False)
            line.setFixedHeight(5)
            line.setStyleSheet(f"QProgressBar {{ background: {T.SURFACE3}; border: none; border-radius: 2px; }}"
                               f"QProgressBar::chunk {{ background: {colours[name]}; border-radius: 2px; }}")
            col.addWidget(line)
            self.bars_lay.addWidget(box, 1)
        self.status.setText("")
        self.fill()

    def fill(self):
        what = self.show_what.currentData()
        rows = [e for e in self.history.items if e["id"] in self.sizes and (not what or e.get("kind") == what)]
        rows.sort(key=lambda e: -self.sizes[e["id"]])
        self.tree.clear()
        self.timer.stop()
        self.todo = []
        for e in rows[:MAX_ROWS]:
            n = self.sizes[e["id"]]
            kind = f"{e.get('w', '?')} x {e.get('h', '?')}"
            it = QTreeWidgetItem([os.path.basename(e["file"]), kind, (e.get("created") or "")[:10], nice_size(n)])
            it.setData(0, _ROW, e["id"])
            it.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(it)
            self.todo.append((it, e.get("thumb")))
        if len(rows) > MAX_ROWS:
            self.status.setText(f"The {MAX_ROWS} biggest of {len(rows)} are shown.")
        self.timer.start()
        self._selection()

    def _load_thumbs(self):
        for it, thumb in self.todo[:8]:
            if thumb:
                pm = QPixmap(thumb)
                if not pm.isNull():
                    it.setIcon(0, QIcon(pm.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.FastTransformation)))
        del self.todo[:8]
        if not self.todo:
            self.timer.stop()

    def _selected(self):
        got = [self.history.get(i.data(0, _ROW)) for i in self.tree.selectedItems()]
        return [e for e in got if e]

    def _selection(self):
        sel = self._selected()
        total = sum(self.sizes.get(e["id"], 0) for e in sel)
        self.sel_text.setText(f"{len(sel)} selected, {nice_size(total)}" if sel else "Select what to work on")
        for b in (self.del_btn, self.move_btn, self.comp_btn):
            b.setEnabled(bool(sel) and not self.busy)

    # ---- the three actions
    def _delete(self):
        self.app.delete_entries(self._selected())

    def _begin(self, text):
        self.busy = True
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText(text)
        self._selection()

    def _end(self, text):
        self.busy = False
        self.bar.hide()
        self.status.setText(text)
        self._selection()

    def _progress(self, done, total, text):
        self.bar.setValue(int(done * 1000 / max(1, total)))
        self.status.setText(text)

    def _move(self):
        sel = [e for e in self._selected() if not e.get("store")]
        if not sel:
            self.app.toast("What is on a drive moves in the Gallery (right click, Move to).", "warn")
            return
        folder = QFileDialog.getExistingDirectory(self, "Move to", cfg.get("move_dir") or "")
        if not folder:
            return
        cfg.set("move_dir", folder)
        self._begin("Moving…")

        def work():
            done, failed = [], 0
            for i, e in enumerate(sel):
                try:
                    dst = media.unique(os.path.join(folder, os.path.basename(e["file"])))
                    src = e["file"]
                    shutil.move(src, dst)
                    side = src + ".json"
                    if os.path.isfile(side):
                        shutil.move(side, dst + ".json")
                    clean = clean_path(src)
                    if os.path.isfile(clean):
                        os.makedirs(os.path.dirname(clean_path(dst)), exist_ok=True)
                        shutil.move(clean, clean_path(dst))
                    done.append((e["id"], {"file": dst}))
                except (OSError, shutil.Error):
                    failed += 1
                self._prog.emit(i + 1, len(sel), f"Moving {i + 1} of {len(sel)}…")
            self._moved.emit(done, failed)
        threading.Thread(target=work, daemon=True).start()

    def _apply_moved(self, done, failed):
        for eid, fields in done:
            self.history.update(eid, **fields)
        self._end(f"Moved {len(done)}." + (f" {failed} couldn't be moved." if failed else ""))
        self.scan()

    def _compress(self):
        sel = [e for e in self._selected() if not e.get("store")]
        pics = [e for e in sel if not _is_video(e) and os.path.splitext(e["file"])[1].lower() in _IMG]
        vids = [e for e in sel if _is_video(e) and not e["file"].lower().endswith(".gif")]
        if not pics and not vids:
            self.app.toast("Nothing here can be compressed.", "warn")
            return
        d = CompressDialog(self, len(pics), len(vids))
        if not d.exec():
            return
        o = d.options()
        todo = pics + vids
        mode = cfg.get("delete_mode", "recycle")
        if mode == "eraser" and not shred.eraser_path():
            mode = "shred"
        self._begin("Compressing…")

        def work():
            done, saved, err = [], 0, None
            for i, e in enumerate(todo):
                self._prog.emit(i, len(todo), f"Compressing {i + 1} of {len(todo)}…")
                src = e["file"]
                tmp = None
                try:
                    tmp = compress_video(src, o["crf"], o["half"]) if _is_video(e) else \
                        compress_picture(src, o["fmt"], o["quality"], o["longest"])
                    old, new = os.path.getsize(src), os.path.getsize(tmp)
                    if new >= old * 0.95:
                        os.remove(tmp)
                        continue
                    shred.delete([src], mode)
                    final = tmp.replace(".compressing", "")
                    final = media.unique(final) if os.path.exists(final) else final
                    os.replace(tmp, final)
                    saved += old - new
                    done.append((e["id"], {"file": final, "fmt": os.path.splitext(final)[1][1:]}))
                except Exception as ex:
                    err = err or ex
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
            self._freed.emit(done, saved, err)
        threading.Thread(target=work, daemon=True).start()

    def _apply_freed(self, done, saved, err):
        for eid, fields in done:
            self.history.update(eid, **fields)
        self._end(f"{len(done)} compressed, {nice_size(saved)} saved." if done else "Nothing got smaller, so nothing changed.")
        if err is not None:
            QMessageBox.warning(self, "Compress", f"Some couldn't be compressed:\n{err}")
        self.scan()
