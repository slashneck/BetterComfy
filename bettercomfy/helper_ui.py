"""The prompt helper in the app: its setup (an optional download to a folder you pick), the answer panel under a
prompt, and the tag suggestions popup."""
import os
import threading
import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QStyle, QStyledItemDelegate, QVBoxLayout)

from . import assistant as H, system, tags, theme as T
from .config import cfg
from .widgets import button, field, hrow, icon_button, label


def _mb(n):
    return f"{n / 2 ** 30:.1f} GB" if n >= 2 ** 30 else f"{n / 2 ** 20:.0f} MB"


# ================================================================================================ setup

class _ModelRow(QFrame):
    """One model in the list: what it is for, and Download / Use / Delete."""

    def __init__(self, dlg, m):
        super().__init__()
        self.dlg, self.m = dlg, m
        self.setObjectName("ModelRow")
        self.setStyleSheet(f"#ModelRow {{ background: {T.FIELD}; border-radius: 10px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 10, 10)
        v.setSpacing(4)
        self.title = label(f"{m['name']}  ·  {_mb(m['size'])}", None)
        self.badge = label("In use", "Badge")
        self.dl = button("Download", lambda: dlg.download(m), "Accent", "download")
        self.use = button("Use", lambda: dlg.use(m), "Ghost", "check")
        self.rm = button("", lambda: dlg.delete(m), "Ghost", "trash", "Delete the downloaded file", icon_color="#FF8A8A")
        v.addWidget(hrow(self.title, None, self.badge, self.use, self.rm, self.dl, spacing=4))
        note = label(m["note"], "Faint", wrap=True)
        v.addWidget(note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.status = label("", "Muted")
        v.addWidget(self.bar)
        v.addWidget(self.status)
        self.refresh()

    def refresh(self, busy=False):
        base = self.dlg.base()
        have = H.installed(self.m, base)
        inuse = have and os.path.normcase(H.model_path(self.m["role"]) or "") == os.path.normcase(
            H.local_path(self.m, base))
        downloading = self.dlg.current is self.m
        self.badge.setVisible(inuse and not downloading)
        self.use.setVisible(have and not inuse and not downloading)
        self.rm.setVisible(have and not downloading)
        self.dl.setVisible(not have and not downloading)
        self.dl.setEnabled(not busy)
        self.use.setEnabled(not busy)
        self.rm.setEnabled(not busy)
        self.bar.setVisible(downloading)
        self.status.setVisible(downloading or bool(self.status.text()) and not have)


class SetupDialog(QDialog):
    """The prompt helper's models: download (to a folder you pick), use or delete each one."""
    _prog = Signal(object, object)          # bytes: above 2 GB a Qt int would wrap around
    _end = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Prompt helper")
        self.setModal(True)
        self.resize(640, 10)
        self.cancel_ev = None
        self.current = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(10)
        v.addWidget(label("Prompt helper models", "H2"))
        v.addWidget(label("Small language models that write and improve prompts. They run on your processor, so the "
                          "graphics card stays free for generating, and once downloaded they work without internet. "
                          "Pick one for writing and, for tag based models, the tag model.", "Muted", wrap=True))
        self.dir = QLineEdit(H.folder())
        self.dir.setMinimumHeight(34)
        self.dir.editingFinished.connect(self._refresh)
        self.space = label("", "Faint")
        v.addWidget(field("Folder", hrow(self.dir, button("Browse…", self._browse, "Ghost", "folder"), spacing=6),
                          label_w=60))
        v.addWidget(self.space)
        self.rows = []
        groups = [("For writing", [m for m in H.MODELS if m["role"] == "write" and not m.get("old")]),
                  ("For tags", [m for m in H.MODELS if m["role"] == "tags"]),
                  ("Older standard models", [m for m in H.MODELS if m.get("old") and H.installed(m)])]
        for title, ms in groups:
            if not ms:
                continue
            v.addWidget(label(title.upper(), "CardTitle"))
            for m in ms:
                r = _ModelRow(self, m)
                self.rows.append(r)
                v.addWidget(r)
        v.addWidget(label(f"The runtime that runs them (llama.cpp, {_mb(H.RUNTIME['size'])}) comes with the first "
                          "download. Models come from Hugging Face, the runtime from GitHub; every file is checked "
                          "against a fixed checksum.", "Faint", wrap=True))
        self.own_btn = button("Use a .gguf file I already have…", self._own, "Ghost", "upload")
        v.addWidget(hrow(self.own_btn, None, button("Close", self.accept, "Accent"), spacing=6))
        self._prog.connect(self._progress)
        self._end.connect(self._finished)
        self._refresh()

    def base(self):
        return self.dir.text().strip() or H.folder()

    def _row(self, m):
        return next((r for r in self.rows if r.m is m), None)

    def _refresh(self):
        busy = self.current is not None
        for r in self.rows:
            r.refresh(busy)
        free = system.disk_free(self.base())
        self.space.setText(f"{_mb(free)} free there" if free is not None else "")
        self.dir.setEnabled(not busy)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Where should the prompt helper go?", self.base())
        if d:
            self.dir.setText(os.path.normpath(os.path.join(d, "Better Comfy prompt helper"))
                             if os.listdir(d) else os.path.normpath(d))
            self._refresh()

    def _own(self):
        f, _ = QFileDialog.getOpenFileName(self, "A language model (.gguf)", "", "GGUF model (*.gguf)")
        if not f:
            return
        self.current = {"name": os.path.basename(f), "role": "write", "own": os.path.normpath(f)}
        self._start(self.current["own"])

    # ---- actions
    def download(self, m):
        free = system.disk_free(self.base())
        if free is not None and free < m["size"] + 2 ** 28:
            r = self._row(m)
            r.status.setText("Not enough free space in that folder.")
            r.status.show()
            return
        self.current = m
        self._start(m)

    def _start(self, what):
        base = self.base()
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as ex:
            self.space.setText(f"That folder can't be used: {ex}")
            self.current = None
            return
        self.cancel_ev = threading.Event()
        self._t0, self._d0 = time.time(), None
        r = self._row(self.current) if isinstance(self.current, dict) and "key" in self.current else None
        if r:
            r.status.setText("Starting…")
            r.bar.setValue(0)
            r.dl.setText("Stop")
            r.dl.show()
            r.dl.setEnabled(True)
            try:
                r.dl.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            r.dl.clicked.connect(self._stop)
        self._refresh()
        if r:
            r.dl.show()
            r.dl.setEnabled(True)

        def work():
            try:
                self._end.emit(H.install(what, base, lambda d, t: self._prog.emit(d, t), self.cancel_ev))
            except Exception as ex:                     # noqa: BLE001 - shown to the user
                self._end.emit(ex)
        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        if self.cancel_ev is not None:
            self.cancel_ev.set()

    def _progress(self, done, total):
        r = self._row(self.current)
        if r is None:
            return
        if self._d0 is None:
            self._d0, self._t0 = done, time.time()
        r.bar.setValue(int(1000 * done / max(1, total)))
        speed = (done - self._d0) / max(0.5, time.time() - self._t0)
        left = (total - done) / speed if speed > 0 else 0
        r.status.setText(f"{_mb(done)} of {_mb(total)}" + (f"  ·  {speed / 2 ** 20:.1f} MB/s  ·  about "
                                                             f"{int(left // 60)}:{int(left % 60):02d} left"
                                                             if speed > 0 and done < total else ""))

    def _finished(self, res):
        m, self.current, self.cancel_ev = self.current, None, None
        r = self._row(m)
        if r is not None:
            r.dl.setText("Download")
            try:
                r.dl.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            r.dl.clicked.connect(lambda: self.download(m))
        if isinstance(res, H.Cancelled):
            if r:
                r.status.setText("Stopped. What was downloaded is kept, the next try goes on from there.")
        elif isinstance(res, Exception):
            if r:
                r.status.setText(f"That did not work: {res}")
            else:
                self.space.setText(f"That did not work: {res}")
        else:
            cfg.set("helper_dir", self.base())
            role = m.get("role", "write")
            if not H.model_path(role) or m.get("own"):
                cfg.set(H.ROLE_KEY[role], res)          # the first model of a role is used right away
            if r:
                r.status.setText("")
        self._refresh()

    def use(self, m):
        cfg.set("helper_dir", self.base())
        cfg.set(H.ROLE_KEY[m["role"]], H.local_path(m, self.base()))
        self._refresh()

    def delete(self, m):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Prompt helper", f"Delete {m['name']} ({_mb(m['size'])})?") != \
                QMessageBox.StandardButton.Yes:
            return
        H.remove_model(m, self.base())
        self._refresh()

    def reject(self):
        if self.cancel_ev is not None:
            self.cancel_ev.set()
        super().reject()


class PartsDialog(QDialog):
    """Missing engine parts (text encoder, VAE) fetched into ComfyUI's model folders: optional, on request."""
    _prog = Signal(object, object)
    _end = Signal(object)

    def __init__(self, parent, family, missing, install):
        super().__init__(parent)
        from . import comfy, workflows as W
        self.setWindowTitle("Missing parts")
        self.setModal(True)
        self.resize(560, 10)
        self.install = install
        self.parts = [(what, W.PART_DOWNLOADS[family][what]) for what in missing if what in W.PART_DOWNLOADS.get(family, {})]
        self.cancel_ev = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(10)
        v.addWidget(label(f"{W.FAMILIES[family]['name']}: missing parts", "H2"))
        total = sum(p["size"] for _w, p in self.parts)
        v.addWidget(label("These are the official files from Comfy-Org on Hugging Face, checked against a fixed "
                          f"checksum after the download. Together {_mb(total)}.", "Muted", wrap=True))
        for what, p in self.parts:
            v.addWidget(label(f"{what}:  {p['file']}  ·  {_mb(p['size'])}", None))
        self.dir = QLineEdit(comfy.sub_dir(install, "models") if install else "")
        self.dir.setMinimumHeight(34)
        self.dir.textChanged.connect(lambda _t: self._check())
        v.addWidget(field("Into", hrow(self.dir, button("Browse…", self._browse, "Ghost", "folder"), spacing=6),
                          label_w=40))
        self.warn = label("", "Faint", wrap=True)
        v.addWidget(self.warn)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.bar)
        v.addWidget(self.status)
        self.go_btn = button("Download", self._go, "Accent", "download")
        v.addWidget(hrow(None, button("Close", self.reject, "Ghost"), self.go_btn, spacing=6))
        self._prog.connect(self._progress)
        self._end.connect(self._finished)
        self._check()

    def _check(self):
        from . import comfy
        models = os.path.normcase(os.path.abspath(comfy.sub_dir(self.install, "models"))) if self.install else ""
        here = os.path.normcase(os.path.abspath(self.dir.text().strip() or "."))
        free = system.disk_free(self.dir.text().strip())
        bits = []
        if models and here != models:
            bits.append("ComfyUI only finds them in its own models folder (or one set up in its extra_model_paths.yaml).")
        if free is not None:
            bits.append(f"{_mb(free)} free there.")
        self.warn.setText(" ".join(bits))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Download the parts into", self.dir.text())
        if d:
            self.dir.setText(os.path.normpath(d))

    def _go(self):
        if self.cancel_ev is not None:
            self.cancel_ev.set()
            return
        root = self.dir.text().strip()
        self.cancel_ev = threading.Event()
        self.go_btn.setText("Stop")
        self.bar.show()
        self._t0, self._d0 = time.time(), None
        parts = self.parts
        total = sum(p["size"] for _w, p in parts)

        def work():
            try:
                done = 0
                for _what, p in parts:
                    sub = p["folder"].split("/", 1)[1]
                    dest = os.path.join(root, sub, p["file"])
                    if not (os.path.isfile(dest) and os.path.getsize(dest) == p["size"]):
                        H.download(p["url"], dest, p["size"], p["sha256"],
                                   lambda d, _t, base=done: self._prog.emit(base + d, total), self.cancel_ev)
                    done += p["size"]
                self._end.emit(True)
            except Exception as ex:                     # noqa: BLE001 - shown to the user
                self._end.emit(ex)
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, done, total):
        if self._d0 is None:
            self._d0, self._t0 = done, time.time()
        self.bar.setValue(int(1000 * done / max(1, total)))
        speed = (done - self._d0) / max(0.5, time.time() - self._t0)
        left = (total - done) / speed if speed > 0 else 0
        self.status.setText(f"{_mb(done)} of {_mb(total)}" + (f"  ·  {speed / 2 ** 20:.1f} MB/s  ·  about "
                                                                f"{int(left // 60)}:{int(left % 60):02d} left"
                                                                if speed > 0 and done < total else ""))

    def _finished(self, res):
        self.cancel_ev = None
        self.go_btn.setText("Download")
        if isinstance(res, H.Cancelled):
            self.status.setText("Stopped. What was downloaded is kept, the next try goes on from there.")
        elif isinstance(res, Exception):
            self.status.setText(f"That did not work: {res}")
        else:
            self.accept()

    def reject(self):
        if self.cancel_ev is not None:
            self.cancel_ev.set()
        super().reject()


def setup(parent):
    """The setup window. True when the helper is ready afterwards."""
    return bool(SetupDialog(parent).exec()) and H.ready()


# ================================================================================================ answer panel

TITLES = {"improve": "Improved", "detail": "More detail", "shorter": "Shorter", "idea": "From your words",
          "convert": "Rewritten", "surprise": "Something new", "motion": "Motion"}


class Panel(QFrame):
    """Under the prompt: the helper's answer as it is written, then Use it / Again."""
    use = Signal(str)
    _text = Signal(int, str)
    _done = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("HelperPanel")
        self.setStyleSheet(f"#HelperPanel {{ background: {T.rgba(T.accent(), 16)}; border: 1px solid "
                           f"{T.rgba(T.accent(), 60)}; border-radius: 10px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 8, 10)
        v.setSpacing(6)
        self.title = label("", "Muted")
        v.addWidget(hrow(self.title, None, icon_button("close", self.close_panel, "Close", size=14), spacing=4))
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(64)
        self.out.setMaximumHeight(150)
        v.addWidget(self.out)
        self.use_btn = button("Use it", lambda: self.use.emit(self.out.toPlainText().strip()), "Accent", "check")
        self.again_btn = button("Again", self.again, "Ghost", "refresh")
        v.addWidget(hrow(None, self.again_btn, self.use_btn, spacing=4))
        self._text.connect(self._show_text)
        self._done.connect(self._finished)
        self.run_id = 0
        self.cancel = None
        self.last = None
        self.hide()

    def start(self, task, text, style, what="image"):
        self.last = (task, text, style, what)
        if self.cancel is not None:
            self.cancel.set()
        self.run_id += 1
        rid, cancel = self.run_id, threading.Event()
        self.cancel = cancel
        self.title.setText("Starting the helper…" if not H.helper.running() else "Writing…")
        self.out.setPlainText("")
        self.use_btn.setEnabled(False)
        self.again_btn.setEnabled(False)
        self.show()

        def work():
            try:
                res = H.ask(task, text, style, lambda t: self._text.emit(rid, t), cancel, what)
                self._done.emit(rid, res)
            except H.Cancelled:
                pass
            except Exception as ex:                     # noqa: BLE001 - shown to the user
                self._done.emit(rid, ex)
        threading.Thread(target=work, daemon=True).start()

    def _show_text(self, rid, t):
        if rid == self.run_id:
            self.title.setText("Writing…")
            self.out.setPlainText(t)

    def _finished(self, rid, res):
        if rid != self.run_id:
            return
        self.cancel = None
        self.again_btn.setEnabled(True)
        if isinstance(res, Exception):
            self.title.setText("The helper ran into a problem")
            self.out.setPlainText(str(res))
            return
        self.title.setText(TITLES.get(self.last[0], "Done"))
        self.out.setPlainText(res)
        self.use_btn.setEnabled(bool(res.strip()))

    def again(self):
        if self.last:
            self.start(*self.last)

    def close_panel(self):
        if self.cancel is not None:
            self.cancel.set()
            self.cancel = None
        self.run_id += 1
        self.hide()


# ================================================================================================ tag suggestions

CAT_COLORS = {"general": "#A1A1AA", "artist": "#FF8A8A", "series": "#C792EA", "character": "#7EE787",
              "meta": "#F5B041"}


class _Row(QStyledItemDelegate):
    def paint(self, p, opt, idx):
        tag, cat, count, alias = idx.data(Qt.ItemDataRole.UserRole)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = opt.rect
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.rgba(T.accent(), 60)))
            p.drawRoundedRect(r.adjusted(2, 1, -2, -1), 6, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CAT_COLORS.get(cat, "#A1A1AA")))
        p.drawEllipse(QPoint(r.left() + 12, r.center().y()), 3, 3)
        p.setPen(QColor(T.TEXT))
        txt = tag + (f"   ← {alias}" if alias else "")
        p.drawText(QRect(r.left() + 24, r.top(), r.width() - 90, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   opt.fontMetrics.elidedText(txt, Qt.TextElideMode.ElideRight, r.width() - 96))
        p.setPen(QColor(T.TEXT3))
        p.drawText(QRect(r.right() - 64, r.top(), 56, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, tags.short_count(count))
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(300, 26)


class TagPopup(QListWidget):
    """The suggestions under the cursor. It never takes the focus: the prompt box keeps the keyboard."""
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setItemDelegate(_Row(self))
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QListWidget {{ background: {T.SURFACE2}; border: 1px solid {T.BORDER_HI}; "
                           f"border-radius: 8px; padding: 3px; outline: none; }}")
        self.itemClicked.connect(lambda it: self.picked.emit(it.data(Qt.ItemDataRole.UserRole)[0]))

    def show_for(self, results, at):
        self.clear()
        for r in results:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.addItem(it)
        self.setCurrentRow(0)
        self.resize(360, 26 * len(results) + 10)
        scr = self.screen().availableGeometry() if self.screen() else None
        pos = QPoint(at)
        if scr is not None and pos.y() + self.height() > scr.bottom():
            pos.setY(pos.y() - self.height() - 28)
        self.move(pos)
        self.show()
        system.guard_window(self)

    def move_sel(self, d):
        self.setCurrentRow(max(0, min(self.count() - 1, self.currentRow() + d)))

    def current_tag(self):
        it = self.currentItem()
        return it.data(Qt.ItemDataRole.UserRole)[0] if it else None

