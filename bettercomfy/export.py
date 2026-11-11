"""Clean copies and batch export: pictures and videos out of the app, with the prompt and settings taken out if you want."""
import os
import shutil
import subprocess
import threading

from PIL import Image
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QMessageBox, QProgressBar, QVBoxLayout

from . import media
from .config import cfg
from .widgets import Combo, ToggleRow, button, field, hrow, label

_IMG = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def clean_path(path):
    """Where the clean copy of a result goes: a "clean" folder next to it."""
    return os.path.join(os.path.dirname(path), "clean", os.path.basename(path))


def strip_picture(src, dst, fmt=None, longest=None, quality=92):
    """A copy with nothing but the pixels: no prompt, no settings, no workflow, no camera data."""
    with Image.open(src) as im:
        im.load()
        keep_alpha = "A" in im.getbands() and fmt != "JPEG"
        pic = im.convert("RGBA" if keep_alpha else "RGB")
    pic.info = {}
    if longest and max(pic.size) > longest:
        pic.thumbnail((longest, longest), Image.LANCZOS)
    fmt = fmt or _IMG.get(os.path.splitext(src)[1].lower(), "PNG")
    extra = {"quality": quality} if fmt in ("JPEG", "WEBP") else {}
    pic.save(dst, fmt, **extra)


def strip_video(src, dst):
    """The same video and sound, without its metadata (nothing is encoded again, so it is quick and loses nothing)."""
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                        "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", dst],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise OSError(r.stderr.decode("utf-8", "replace")[-300:])


def clean_copy(path):
    """The second, clean file of a new result (Settings, Output). A failure never harms the result itself."""
    dst = clean_path(path)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.splitext(path)[1].lower() in _IMG:
            strip_picture(path, dst)
        else:
            strip_video(path, dst)
    except Exception:
        pass


def export_one(e, folder, clean, fmt, longest, name):
    src = e["file"]
    ext = os.path.splitext(src)[1].lower()
    is_pic = ext in _IMG
    if is_pic and fmt:
        ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
    dst = media.unique(os.path.join(folder, name + ext))
    if is_pic and (clean or fmt or longest):
        strip_picture(src, dst, fmt or _IMG[os.path.splitext(src)[1].lower()], longest)
    elif not is_pic and clean:
        strip_video(src, dst)
    else:
        shutil.copy2(src, dst)


class ExportDialog(QDialog):
    _prog = Signal(int, int)
    _end = Signal(int, object)

    def __init__(self, app, entries):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Export")
        self.entries = [e for e in entries if not e.get("vault")]
        self.left_out = len(entries) - len(self.entries)
        self.ev = threading.Event()
        self.resize(520, 10)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        n = len(self.entries)
        v.addWidget(label(f"Export {n} item{'s' if n != 1 else ''}", "H2"))
        if self.left_out:
            v.addWidget(label(f"{self.left_out} from the vault stay{'s' if self.left_out == 1 else ''} in the vault.", "Muted"))
        self.clean = ToggleRow("Remove prompt and settings", "Only the picture stays. Nobody can read how it was made.", True)
        self.fmt = Combo()
        for t, k in (("Keep as it is", ""), ("PNG", "PNG"), ("JPG", "JPEG"), ("WebP", "WEBP")):
            self.fmt.addItem(t, k)
        self.size = Combo()
        for t, k in (("Keep as it is", 0), ("Longest side 2048", 2048), ("Longest side 1536", 1536), ("Longest side 1024", 1024)):
            self.size.addItem(t, k)
        self.naming = Combo()
        self.naming.addItem("Keep the names", "keep")
        self.naming.addItem("Number them", "number")
        self.prefix = QLineEdit("picture")
        self.prefix.setPlaceholderText("Name before the number")
        self.prefix.setVisible(False)
        self.naming.currentIndexChanged.connect(lambda _i: self.prefix.setVisible(self.naming.currentData() == "number"))
        v.addWidget(self.clean)
        v.addWidget(field("Format", self.fmt, label_w=80))
        v.addWidget(field("Size", self.size, label_w=80))
        v.addWidget(field("Names", hrow(self.naming, self.prefix, spacing=6), label_w=80))
        v.addWidget(label("Format and size apply to pictures. Videos keep theirs.", "Faint"))
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, n))
        self.bar.setTextVisible(False)
        self.bar.hide()
        v.addWidget(self.bar)
        self.go_btn = button("Choose a folder and export", self._go, "Accent", "download")
        v.addWidget(hrow(None, button("Close", self.reject, "Ghost"), self.go_btn, spacing=6))
        self._prog.connect(lambda d, t: self.bar.setValue(d))
        self._end.connect(self._finished)

    def _go(self):
        start = cfg.get("export_dir") or ""
        folder = QFileDialog.getExistingDirectory(self, "Export to", start)
        if not folder:
            return
        cfg.set("export_dir", folder)
        self.go_btn.setEnabled(False)
        self.bar.show()
        self.folder = folder
        args = (self.clean.toggle.isChecked(), self.fmt.currentData() or None,
                self.size.currentData() or None, self.naming.currentData(), self.prefix.text().strip() or "picture")
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _work(self, clean, fmt, longest, naming, prefix):
        done, err = 0, None
        for i, e in enumerate(self.entries):
            if self.ev.is_set():
                break
            name = os.path.splitext(os.path.basename(e["file"]))[0] if naming == "keep" else f"{prefix}_{i + 1:04d}"
            try:
                export_one(e, self.folder, clean, fmt, longest, name)
                done += 1
            except Exception as ex:
                err = ex
            try:
                self._prog.emit(i + 1, len(self.entries))
            except RuntimeError:
                return
        try:
            self._end.emit(done, err)
        except RuntimeError:
            pass

    def _finished(self, done, err):
        if err is not None and not done:
            QMessageBox.warning(self, "Export", f"That didn't work:\n{err}")
            self.go_btn.setEnabled(True)
            return
        missed = len(self.entries) - done
        self.app.toast(f"Exported {done}." + (f" {missed} couldn't be." if missed else ""), "warn" if missed else "ok")
        self.accept()

    def reject(self):
        self.ev.set()
        super().reject()
