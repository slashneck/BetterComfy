"""Small edits: trim a video or GIF, crop, turn and flip anything. The result is a new picture / video next to the
original (same folder, drive, vault and collections); the original stays as it is. Vault items are only ever edited in
memory."""
import io
import threading

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QSlider, QVBoxLayout, QWidget

from . import media, theme as T
from .widgets import Combo, button, field, hrow, label

PREVIEW_FPS = 8.0
ASPECTS = [("free", "Free"), ("1:1", "1:1"), ("4:5", "4:5"), ("2:3", "2:3"), ("3:2", "3:2"), ("16:9", "16:9"),
           ("9:16", "9:16")]


def _turn(im, rot, flip):
    """A PIL picture turned clockwise by rot degrees, then mirrored."""
    if rot == 90:
        im = im.transpose(Image.Transpose.ROTATE_270)
    elif rot == 180:
        im = im.transpose(Image.Transpose.ROTATE_180)
    elif rot == 270:
        im = im.transpose(Image.Transpose.ROTATE_90)
    if flip:
        im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return im


def _box(size, crop):
    w, h = size
    x0, y0, x1, y1 = crop
    return (int(round(x0 * w)), int(round(y0 * h)), max(int(round(x0 * w)) + 2, int(round(x1 * w))),
            max(int(round(y0 * h)) + 2, int(round(y1 * h))))


def _vf(rot, flip, crop):
    f = []
    if rot == 90:
        f.append("transpose=1")
    elif rot == 180:
        f.append("hflip,vflip")
    elif rot == 270:
        f.append("transpose=2")
    if flip:
        f.append("hflip")
    x0, y0, x1, y1 = crop
    if (x0, y0, x1, y1) != (0, 0, 1, 1):
        f.append(f"crop=w='trunc(iw*{x1 - x0:.5f}/2)*2':h='trunc(ih*{y1 - y0:.5f}/2)*2':"
                 f"x='iw*{x0:.5f}':y='ih*{y0:.5f}'")
    f.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ",".join(f)


def _streamable(data):
    """An MP4 whose index comes first (can be read from memory without seeking back)."""
    i, j = data.find(b"moov"), data.find(b"mdat")
    return i != -1 and (j == -1 or i < j)


class CropView(QWidget):
    """The picture with a crop frame: drag inside to move it, near an edge or corner to resize."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 420)
        self.setMouseTracking(True)
        self.pm = QPixmap()
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.aspect = None              # width / height of the frame, None: free
        self._drag, self._grab = None, None

    def set_pixmap(self, pm):
        self.pm = pm
        self.update()

    def _img_rect(self):
        if self.pm.isNull():
            return QRectF()
        r = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        s = min(r.width() / self.pm.width(), r.height() / self.pm.height())
        w, h = self.pm.width() * s, self.pm.height() * s
        return QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)

    def _crop_rect(self):
        ir = self._img_rect()
        x0, y0, x1, y1 = self.crop
        return QRectF(ir.left() + x0 * ir.width(), ir.top() + y0 * ir.height(), (x1 - x0) * ir.width(),
                      (y1 - y0) * ir.height())

    def set_aspect(self, a):
        """a: width / height of the frame (on screen), None: free. The frame becomes the largest of that shape."""
        self.aspect = a
        if a and not self.pm.isNull():
            ia = self.pm.width() / self.pm.height()
            k = a / ia                          # frame width / height in normalised units
            if k >= 1:
                w, h = 1.0, 1.0 / k
            else:
                w, h = k, 1.0
            cx, cy = 0.5, 0.5
            self.crop = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
        elif not a:
            pass
        self.update()
        self.changed.emit()

    def reset(self):
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.aspect = None
        self.update()
        self.changed.emit()

    def _hit(self, pos):
        r = self._crop_rect()
        m = 10
        ed = ""
        if abs(pos.y() - r.top()) < m:
            ed += "t"
        elif abs(pos.y() - r.bottom()) < m:
            ed += "b"
        if abs(pos.x() - r.left()) < m:
            ed += "l"
        elif abs(pos.x() - r.right()) < m:
            ed += "r"
        if ed and r.adjusted(-m, -m, m, m).contains(pos):
            return ed
        return "move" if r.contains(pos) else None

    def mousePressEvent(self, e):
        self._drag = self._hit(e.position())
        self._grab = (e.position(), list(self.crop))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            h = self._hit(pos)
            self.setCursor({None: Qt.CursorShape.ArrowCursor, "move": Qt.CursorShape.SizeAllCursor,
                            "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                            "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor}.get(
                h, Qt.CursorShape.SizeFDiagCursor if h in ("tl", "br") else Qt.CursorShape.SizeBDiagCursor))
            return
        ir = self._img_rect()
        if ir.isEmpty():
            return
        start, c0 = self._grab
        dx, dy = (pos.x() - start.x()) / ir.width(), (pos.y() - start.y()) / ir.height()
        x0, y0, x1, y1 = c0
        if self._drag == "move":
            w, h = x1 - x0, y1 - y0
            x0 = min(max(0.0, x0 + dx), 1 - w)
            y0 = min(max(0.0, y0 + dy), 1 - h)
            self.crop = [x0, y0, x0 + w, y0 + h]
        else:
            mn = 0.04
            if "l" in self._drag:
                x0 = min(max(0.0, x0 + dx), x1 - mn)
            if "r" in self._drag:
                x1 = max(min(1.0, x1 + dx), x0 + mn)
            if "t" in self._drag:
                y0 = min(max(0.0, y0 + dy), y1 - mn)
            if "b" in self._drag:
                y1 = max(min(1.0, y1 + dy), y0 + mn)
            if self.aspect:
                # keep the shape: the height follows the width (in screen units)
                k = self.aspect * ir.height() / ir.width()
                h = (x1 - x0) / k
                if "t" in self._drag and "b" not in self._drag:
                    y0 = max(0.0, y1 - h)
                else:
                    y1 = min(1.0, y0 + h)
                if y1 - y0 < h - 1e-6:          # hit the edge: the width gives way
                    w = (y1 - y0) * k
                    if "l" in self._drag:
                        x0 = x1 - w
                    else:
                        x1 = x0 + w
            self.crop = [x0, y0, x1, y1]
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.SURFACE))
        if self.pm.isNull():
            p.setPen(QColor(T.TEXT3))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…")
            return
        ir = self._img_rect()
        p.drawPixmap(ir, self.pm, QRectF(self.pm.rect()))
        cr = self._crop_rect()
        shade = QPainterPath()
        shade.addRect(ir)
        hole = QPainterPath()
        hole.addRect(cr)
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 150))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cr)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.accent()))
        for pt in (cr.topLeft(), cr.topRight(), cr.bottomLeft(), cr.bottomRight()):
            p.drawEllipse(pt, 5, 5)


class EditDialog(QDialog):
    _loaded = Signal(object)
    _saved = Signal(object)

    def __init__(self, parent, app, entry):
        super().__init__(parent)
        self.app, self.e = app, entry
        self.gif = media.is_gif(entry)
        self.video = entry.get("kind") == "video" and not self.gif
        self.setWindowTitle("Edit")
        self.resize(1040, 720)
        self.rot, self.flip = 0, False
        self.frames, self.durs, self.image = [], [], None
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 18, 18, 18)
        h.setSpacing(16)
        self.view = CropView()
        self.view.changed.connect(self._info)
        h.addWidget(self.view, 1)
        side = QVBoxLayout()
        side.setSpacing(12)
        side.addWidget(label("Edit", "H2"))
        side.addWidget(label("Saved as a new item next to the original, which stays as it is." +
                             (" Vault items are edited in memory only." if entry.get("vault") else ""),
                             "Faint", wrap=True))
        self.aspect = Combo()
        for k, t in ASPECTS:
            self.aspect.addItem(t, k)
        self.aspect.currentIndexChanged.connect(lambda _i: self._aspect(self.aspect.currentData()))
        side.addWidget(field("Crop", hrow(self.aspect, button("Reset", lambda: self._aspect("reset"), "Ghost",
                                                               "refresh"), spacing=6), stacked=True))
        side.addWidget(label("Drag the frame on the picture, or its edges and corners.", "Faint", wrap=True))
        side.addWidget(field("Turn", hrow(button("Left", lambda: self._turn(-90), "Ghost", "rotate"),
                                          button("Right", lambda: self._turn(90), "Ghost", "rotate_r"),
                                          button("Mirror", self._mirror, "Ghost", "flip"), None, spacing=4),
                             stacked=True))
        self.trim_box = QWidget()
        tv = QVBoxLayout(self.trim_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)
        self.start = QSlider(Qt.Orientation.Horizontal)
        self.end = QSlider(Qt.Orientation.Horizontal)
        self.start_lbl, self.end_lbl = label("", "Faint"), label("", "Faint")
        self.start.valueChanged.connect(lambda v: self._trim(v, "start"))
        self.end.valueChanged.connect(lambda v: self._trim(v, "end"))
        tv.addWidget(label("Trim", None))
        tv.addWidget(hrow(label("Start", "Muted"), self.start, self.start_lbl, spacing=8))
        tv.addWidget(hrow(label("End", "Muted"), self.end, self.end_lbl, spacing=8))
        self.trim_box.setVisible(self.video or self.gif)
        side.addWidget(self.trim_box)
        self.info = label("", "Muted", wrap=True)
        side.addWidget(self.info)
        side.addStretch(1)
        self.status = label("", "Faint", wrap=True)
        side.addWidget(self.status)
        self.save_btn = button("Save as new", self._save, "Accent", "check")
        self.save_btn.setEnabled(False)
        side.addWidget(hrow(button("Cancel", self.reject, "Ghost"), None, self.save_btn, spacing=6))
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(320)
        h.addWidget(sw)
        self._loaded.connect(self._got)
        self._saved.connect(self._done)
        threading.Thread(target=self._load, daemon=True).start()

    # ---- loading
    def _bytes(self):
        if self.e.get("vault"):
            from .vault import vault
            return vault.read(self.e)
        with open(self.e["file"], "rb") as fh:
            return fh.read()

    def _load(self):
        try:
            data = self._bytes()
            if self.gif:
                im = Image.open(io.BytesIO(data))
                frames, durs = [], []
                try:
                    while True:
                        frames.append(im.convert("RGB"))
                        durs.append(im.info.get("duration", 80) or 80)
                        im.seek(im.tell() + 1)
                except EOFError:
                    pass
                res = {"data": data, "frames": frames, "durs": durs}
            elif self.video:
                w, h = int(self.e.get("w") or 0), int(self.e.get("h") or 0)
                ph = 360
                pw = int(round(w * ph / h / 2)) * 2 if w and h else 640
                args = ["-i", "pipe:0" if self.e.get("vault") else self.e["file"], "-vf",
                        f"fps={PREVIEW_FPS},scale={pw}:{ph}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
                raw = media._ffmpeg_pipe(args, data_in=data if self.e.get("vault") else None)
                n = len(raw) // (pw * ph * 3)
                arr = np.frombuffer(raw[:n * pw * ph * 3], np.uint8).reshape(n, ph, pw, 3) if n else []
                res = {"data": data, "frames": [Image.fromarray(a) for a in arr]}
            else:
                res = {"data": data, "image": Image.open(io.BytesIO(data))}
        except Exception as ex:
            res = ex
        try:
            self._loaded.emit(res)
        except RuntimeError:
            pass

    def _got(self, res):
        if isinstance(res, Exception):
            self.status.setText(f"It could not be read: {res}")
            return
        self.data = res["data"]
        if self.gif or self.video:
            self.frames = res["frames"]
            self.durs = res.get("durs") or []
            n = len(self.frames)
            if not n:
                self.status.setText("No frames could be read.")
                return
            for s in (self.start, self.end):
                s.blockSignals(True)
                s.setRange(0, n - 1)
                s.blockSignals(False)
            self.end.setValue(n - 1)
            self.start.setValue(0)
            self._show(0)
        else:
            self.image = res["image"]
            self.image.load()
            self._show(0)
        self.save_btn.setEnabled(True)
        self._info()

    # ---- the preview
    def _frame(self, i):
        return self.frames[i] if self.frames else self.image

    def _show(self, i):
        im = _turn(self._frame(i).convert("RGB"), self.rot, self.flip)
        q = QImage(im.tobytes(), im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()
        self.view.set_pixmap(QPixmap.fromImage(q))

    def _t(self, i):
        if self.gif:
            return sum(self.durs[:i]) / 1000.0
        return i / PREVIEW_FPS

    def _trim(self, v, which):
        if which == "start" and v > self.end.value():
            self.end.setValue(v)
        if which == "end" and v < self.start.value():
            self.start.setValue(v)
        self.start_lbl.setText(f"{self._t(self.start.value()):.1f} s")
        self.end_lbl.setText(f"{self._t(self.end.value() + 1):.1f} s")
        self._show(v)
        self._info()

    def _aspect(self, key):
        if key == "reset":
            self.view.reset()
            self.aspect.blockSignals(True)
            self.aspect.setCurrentIndex(0)
            self.aspect.blockSignals(False)
            return
        if key == "free":
            self.view.aspect = None
            self.view.update()
            return
        a, b = key.split(":")
        self.view.set_aspect(int(a) / int(b))

    def _turn(self, d):
        self.rot = (self.rot + d) % 360
        self.view.reset()
        self._show(self.start.value() if self.frames else 0)
        self._info()

    def _mirror(self):
        self.flip = not self.flip
        self._show(self.start.value() if self.frames else 0)

    def _size(self):
        w, h = int(self.e.get("w") or 0), int(self.e.get("h") or 0)
        if not (w and h) and (self.image or self.frames):
            w, h = self._frame(0).size
        if self.rot in (90, 270):
            w, h = h, w
        x0, y0, x1, y1 = self.view.crop
        return max(2, int(w * (x1 - x0)) // 2 * 2), max(2, int(h * (y1 - y0)) // 2 * 2)

    def _info(self):
        w, h = self._size()
        bits = [f"{w} × {h}"]
        if self.frames:
            bits.append(f"{self._t(self.end.value() + 1) - self._t(self.start.value()):.1f} s")
        self.info.setText("Result:  " + "  ·  ".join(bits))

    # ---- saving
    def _save(self):
        self.save_btn.setEnabled(False)
        self.status.setText("Saving…")
        job = dict(rot=self.rot, flip=self.flip, crop=list(self.view.crop), a=self.start.value(), b=self.end.value())
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def _work(self, j):
        try:
            if self.gif:
                frames = [_turn(f, j["rot"], j["flip"]) for f in self.frames[j["a"]:j["b"] + 1]]
                frames = [f.crop(_box(f.size, j["crop"])) for f in frames]
                buf = io.BytesIO()
                frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], loop=0,
                               duration=self.durs[j["a"]:j["b"] + 1] or 80, disposal=2)
                w, h = frames[0].size
                res = {"data": buf.getvalue(), "ext": "gif", "kind": "image", "w": w, "h": h}
            elif self.video:
                res = self._video_out(j)
            else:
                im = _turn(self.image.convert("RGBA" if self.image.mode in ("RGBA", "LA", "P") else "RGB"), j["rot"],
                           j["flip"])
                im = im.crop(_box(im.size, j["crop"]))
                buf = io.BytesIO()
                im.save(buf, "PNG")
                res = {"data": buf.getvalue(), "ext": "png", "kind": "image", "w": im.width, "h": im.height}
        except Exception as ex:
            res = ex
        try:
            self._saved.emit(res)
        except RuntimeError:
            pass

    def _video_out(self, j):
        t0, t1 = self._t(j["a"]), self._t(j["b"] + 1)
        vf = _vf(j["rot"], j["flip"], j["crop"])
        enc = ["-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-vf", vf, "-c:v", "libx264", "-crf", "16", "-preset", "medium",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
        fps = float(self.e.get("fps") or 16)
        vault_item = bool(self.e.get("vault"))
        if not vault_item or _streamable(self.data):
            src = ["-i", "pipe:0" if vault_item else self.e["file"]]
            out = media._ffmpeg_pipe(src + enc + ["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4",
                                                  "pipe:1"], data_in=self.data if vault_item else None)
        else:
            # a vault video that can't be read from memory as it is: its frames (the picture only)
            frames, fps = media.read_video_bytes(self.data, self.e.get("w"), self.e.get("h"), max_h=100000, fps=fps)
            a, b = int(t0 * fps), int(t1 * fps)
            ims = [_turn(Image.fromarray(f), j["rot"], j["flip"]) for f in frames[a:b]]
            ims = [im.crop(_box(im.size, j["crop"])) for im in ims]
            out = media.encode_video_bytes([np.asarray(im) for im in ims], fps)
        w, h = self._size()
        return {"data": out, "ext": "mp4", "kind": "video", "w": w, "h": h, "seconds": round(t1 - t0, 1), "fps": fps}

    def _done(self, res):
        if isinstance(res, Exception):
            self.status.setText(f"It could not be saved: {res}")
            self.save_btn.setEnabled(True)
            return
        self.app.add_derived(self.e, res, "_edit")
        self.accept()
