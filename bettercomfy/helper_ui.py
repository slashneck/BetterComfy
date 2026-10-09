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
from .widgets import Segmented, button, hrow, icon_button, label


def _mb(n):
    return f"{n / 2 ** 30:.1f} GB" if n >= 2 ** 30 else f"{n / 2 ** 20:.0f} MB"


# ================================================================================================ setup

class SetupDialog(QDialog):
    """Pick a model and a folder, then download - or use a .gguf file that is already on this PC."""
    _prog = Signal(int, int)
    _end = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Prompt helper")
        self.setModal(True)
        self.resize(560, 10)
        self.cancel_ev = None
        self.own_file = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(12)
        v.addWidget(label("Prompt helper", "H2"))
        v.addWidget(label("Writes and improves prompts with a small language model. It runs on your processor, so the "
                          "graphics card stays free for generating, and once it is set up it works without internet.",
                          "Muted", wrap=True))
        self.pick = Segmented([(m["key"], f"{m['name']}  ·  {_mb(m['size'])}", m["note"]) for m in H.MODELS],
                              lambda _v: self._update(), "2b", height=34)
        v.addWidget(self.pick)
        self.note = label("", "Faint", wrap=True)
        v.addWidget(self.note)
        v.addWidget(label("Download to", "Muted"))
        self.dir = QLineEdit(H.folder())
        self.dir.setMinimumHeight(34)
        self.dir.textChanged.connect(lambda _t: self._update())
        v.addWidget(hrow(self.dir, button("Browse…", self._browse, "Ghost", "folder"), spacing=6))
        self.space = label("", "Faint", wrap=True)
        v.addWidget(self.space)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.status = label("", "Muted", wrap=True)
        self.status.hide()
        v.addWidget(self.bar)
        v.addWidget(self.status)
        self.own_btn = button("Use a .gguf file I already have…", self._own, "Ghost", "upload")
        self.go_btn = button("Download", self._go, "Accent", "download")
        self.cancel_btn = button("Cancel", self._cancel, "Ghost")
        v.addWidget(hrow(self.own_btn, None, self.cancel_btn, self.go_btn, spacing=6))
        self._prog.connect(self._progress)
        self._end.connect(self._finished)
        self._update()

    def _model(self):
        return next(m for m in H.MODELS if m["key"] == self.pick.value())

    def _need(self):
        base = self.dir.text().strip()
        need = 0 if H.server_exe(base) else H.RUNTIME["size"]
        if self.own_file is None:
            m = self._model()
            p = os.path.join(base, "models", m["file"])
            if not (os.path.isfile(p) and os.path.getsize(p) == m["size"]):
                need += m["size"]
        return need

    def _update(self):
        m = self._model()
        self.note.setText(f"{m['note']} Comes from Hugging Face ({m['file']}), the runtime ({_mb(H.RUNTIME['size'])}) "
                          f"from the llama.cpp project on GitHub. Both are checked after the download.")
        base = self.dir.text().strip()
        free = system.disk_free(base) if base else None
        need = self._need()
        self.space.setText((f"Needs {_mb(need)}" if need else "Already downloaded there, nothing to fetch") +
                           (f"  ·  {_mb(free)} free there" if free is not None and need else "") +
                           ("  ·  not enough space" if free is not None and free < need + 2 ** 28 else ""))
        self.go_btn.setText("Download" if need else "Set up")

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Where should the prompt helper go?", self.dir.text())
        if d:
            self.dir.setText(os.path.normpath(os.path.join(d, "Better Comfy prompt helper"))
                             if os.listdir(d) else os.path.normpath(d))

    def _own(self):
        f, _ = QFileDialog.getOpenFileName(self, "A language model (.gguf)", "", "GGUF model (*.gguf)")
        if f:
            self.own_file = os.path.normpath(f)
            self.note.setText(f"Uses {os.path.basename(f)}. Small instruct models (1 to 4 billion parameters) work "
                              "best. Only the runtime is downloaded.")
            self.pick.setEnabled(False)
            self._update()

    def _go(self):
        base = self.dir.text().strip()
        if not base:
            return
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as ex:
            self.status.setText(f"That folder can't be used: {ex}")
            self.status.show()
            return
        self.cancel_ev = threading.Event()
        for w in (self.go_btn, self.own_btn, self.pick, self.dir):
            w.setEnabled(False)
        self.bar.show()
        self.status.show()
        self.status.setText("Starting…")
        self._t0, self._d0 = time.time(), None
        model = self.own_file or self._model()

        def work():
            try:
                path = H.install(model, base, lambda d, t: self._prog.emit(d, t), self.cancel_ev)
                self._end.emit(path)
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
        for w in (self.go_btn, self.own_btn, self.dir):
            w.setEnabled(True)
        self.pick.setEnabled(self.own_file is None)
        if isinstance(res, H.Cancelled):
            self.status.setText("Stopped. What was downloaded is kept, the next try goes on from there.")
            self.bar.hide()
            return
        if isinstance(res, Exception):
            self.status.setText(f"That did not work: {res}")
            self.bar.hide()
            return
        cfg.set("helper_dir", self.dir.text().strip())
        cfg.set("helper_model", res)
        self.accept()

    def _cancel(self):
        if self.cancel_ev is not None:
            self.cancel_ev.set()
        else:
            self.reject()

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

