"""The big viewer: a picture (or video) over the whole window. Click: zoom in where you clicked / back to fit,
wheel: zoom, drag: move around, ← →: the one before / after, Esc: close."""
import os

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from . import icons, theme as T
from .components import _Loader
from .widgets import anims_on, nice_name


class Viewer(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.hide()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.entries, self.idx = [], 0
        self.pm, self.frames, self.fi, self.fps = None, [], 0, 16.0
        self.scale, self.center, self.fit = 1.0, QPointF(), True
        self._press, self._moved, self._last = None, False, None
        self._op = 0.0
        self.hover = None
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(lambda v: (setattr(self, "_op", float(v)), self.update()))
        self._fade.finished.connect(lambda: self._op <= 0.01 and self.hide())
        self._zoom = QVariantAnimation(self)
        self._zoom.setDuration(220)
        self._zoom.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom.valueChanged.connect(self._zoom_step)
        self.timer = QTimer(self, timeout=self._next_frame)
        self.loader = _Loader()
        self.loader.done.connect(self._loaded)
        host.installEventFilter(self)

    # ---------------------------------------------------------------- open / close
    def open(self, entries, idx, on_move=None):
        if not entries:
            return
        self.on_move = on_move
        self.entries, self.idx = list(entries), max(0, min(idx, len(entries) - 1))
        self.setGeometry(self.host.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._load()
        self._fade_to(1.0)

    def close_view(self):
        self.timer.stop()
        self._fade_to(0.0)

    def _fade_to(self, v):
        if not anims_on():
            self._op = v
            self.setVisible(v > 0)
            self.update()
            return
        self._fade.stop()
        self._fade.setStartValue(self._op)
        self._fade.setEndValue(v)
        self._fade.start()

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == e.Type.Resize and self.isVisible():
            self.setGeometry(self.host.rect())
            if self.fit:
                self._fit()
        return False

    # ---------------------------------------------------------------- content
    def cur(self):
        return self.entries[self.idx] if self.entries else None

    def _load(self):
        e = self.cur()
        self.timer.stop()
        self.frames = []
        if e is None:
            return
        if e.get("kind") == "video":
            self.pm = QPixmap(e.get("thumb") or "")
            self.loader.load(e["id"], e["file"])
        else:
            self.pm = QPixmap(e["file"])
        self._fit()

    def _loaded(self, key, frames, fps):
        e = self.cur()
        if not e or e["id"] != key or not frames:
            return
        self.frames = [QPixmap.fromImage(q) for q in frames]
        self.fps, self.fi = max(1.0, fps), 0
        self.pm = self.frames[0]
        self._fit()
        self.timer.start(int(1000 / self.fps))

    def _next_frame(self):
        if self.frames:
            self.fi = (self.fi + 1) % len(self.frames)
            self.pm = self.frames[self.fi]
            self.update()

    def step(self, d):
        if len(self.entries) > 1:
            self.idx = (self.idx + d) % len(self.entries)
            self._load()
            if getattr(self, "on_move", None):
                self.on_move(self.cur())

    # ---------------------------------------------------------------- zoom
    def _area(self):
        return QRectF(self.rect()).adjusted(70, 64, -70, -54)

    def _fit_scale(self):
        if not self.pm or self.pm.isNull():
            return 1.0
        a = self._area()
        return min(a.width() / self.pm.width(), a.height() / self.pm.height())

    def _fit(self):
        self.fit = True
        self.scale = self._fit_scale()
        if self.pm and not self.pm.isNull():
            self.center = QPointF(self.pm.width() / 2, self.pm.height() / 2)
        self.update()

    def _zoom_to(self, scale, anchor_img=None, anchor_widget=None):
        """Animate to `scale`, keeping the picture point under anchor_widget where it is (or centring anchor_img)."""
        if not self.pm or self.pm.isNull():
            return
        s0, c0 = self.scale, QPointF(self.center)
        if anchor_img is not None and anchor_widget is not None:
            a = self._area().center()
            c1 = anchor_img - (anchor_widget - a) / scale
        else:
            c1 = anchor_img if anchor_img is not None else c0
        self._za = (s0, scale, c0, c1)
        if not anims_on():
            self._zoom_step(1.0)
            return
        self._zoom.stop()
        self._zoom.setStartValue(0.0)
        self._zoom.setEndValue(1.0)
        self._zoom.start()

    def _zoom_step(self, t):
        s0, s1, c0, c1 = self._za
        t = float(t)
        self.scale = s0 + (s1 - s0) * t
        self.center = c0 + (c1 - c0) * t
        self._clamp()
        self.update()

    def _to_img(self, pos):
        a = self._area().center()
        return self.center + (pos - a) / self.scale

    def _clamp(self):
        if not self.pm or self.pm.isNull():
            return
        a = self._area()
        w, h = self.pm.width(), self.pm.height()
        hw, hh = a.width() / 2 / self.scale, a.height() / 2 / self.scale
        x = w / 2 if hw * 2 >= w else min(max(self.center.x(), hw), w - hw)
        y = h / 2 if hh * 2 >= h else min(max(self.center.y(), hh), h - hh)
        self.center = QPointF(x, y)

    # ---------------------------------------------------------------- input
    def _zone(self, pos):
        if QRectF(self.width() - 56, 12, 44, 44).contains(pos):
            return "close"
        if len(self.entries) > 1 and pos.x() < 64:
            return "prev"
        if len(self.entries) > 1 and pos.x() > self.width() - 64 and pos.y() > 64:
            return "next"
        return None

    def mousePressEvent(self, e):
        self._press, self._last, self._moved = e.position(), e.position(), False

    def mouseMoveEvent(self, e):
        pos = e.position()
        z = self._zone(pos)
        if z != self.hover:
            self.hover = z
            self.update()
        if self._press is not None and e.buttons():
            if (pos - self._press).manhattanLength() > 4:
                self._moved = True
            if self._moved and not self.fit:
                self.center -= (pos - self._last) / self.scale
                self._clamp()
                self.update()
            self._last = pos
        self.setCursor(Qt.CursorShape.PointingHandCursor if z else
                       (Qt.CursorShape.ClosedHandCursor if (self._press is not None and not self.fit) else
                        (Qt.CursorShape.OpenHandCursor if not self.fit else Qt.CursorShape.CrossCursor)))

    def mouseReleaseEvent(self, e):
        pos = e.position()
        if self._press is not None and not self._moved:
            z = self._zone(pos)
            if z == "close":
                self.close_view()
            elif z == "prev":
                self.step(-1)
            elif z == "next":
                self.step(1)
            elif not self._area().adjusted(-70, -10, 70, 10).contains(pos):
                self.close_view()
            elif self.fit:
                target = max(1.0, self._fit_scale() * 2.5)
                self.fit = False
                self._zoom_to(target, self._to_img(pos), pos)
            else:
                self.fit = True
                self._zoom_to(self._fit_scale(), QPointF(self.pm.width() / 2, self.pm.height() / 2))
        self._press = None

    def wheelEvent(self, e):
        if not self.pm or self.pm.isNull():
            return
        f = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        fs = self._fit_scale()
        s = max(fs, min(8.0, self.scale * f))
        pos = e.position()
        anchor = self._to_img(pos)
        self.fit = abs(s - fs) < 1e-3
        a = self._area().center()
        self.scale = s
        self.center = anchor - (pos - a) / s
        self._clamp()
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_view()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self.step(-1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self.step(1)
        elif k == Qt.Key.Key_Space and self.frames:
            self.timer.stop() if self.timer.isActive() else self.timer.start(int(1000 / self.fps))
        elif k in (Qt.Key.Key_0, Qt.Key.Key_F):
            self._fit()
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.fit = False
            self._zoom_to(min(8.0, self.scale * 1.5))
        elif k == Qt.Key.Key_Minus:
            s = max(self._fit_scale(), self.scale / 1.5)
            self.fit = abs(s - self._fit_scale()) < 1e-3
            self._zoom_to(s)
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _):
        p = QPainter(self)
        p.setOpacity(self._op)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(4, 4, 5, 238))
        e = self.cur()
        if self.pm and not self.pm.isNull():
            a = self._area()
            w, h = self.pm.width() * self.scale, self.pm.height() * self.scale
            c = a.center()
            tl = QPointF(c.x() - self.center.x() * self.scale, c.y() - self.center.y() * self.scale)
            r = QRectF(tl.x(), tl.y(), w, h)
            p.save()
            p.setClipRect(QRectF(self.rect()).adjusted(0, 56, 0, -44))
            if self.fit:
                path = QPainterPath()
                path.addRoundedRect(r, 8, 8)
                p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
            p.restore()
        # top bar
        f = QFont(self.font())
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        if e:
            p.drawText(QRectF(24, 18, self.width() - 140, 24), Qt.AlignmentFlag.AlignVCenter, nice_name(e["file"]))
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(T.TEXT2))
            zoom = f"{self.scale * 100:.0f} %"
            info = f"{self.idx + 1} / {len(self.entries)}    {e.get('w')} × {e.get('h')}    {zoom}"
            fm = QFontMetrics(f)
            p.drawText(QPointF(self.width() - 80 - fm.horizontalAdvance(info), 35), info)
            hint = "Click: zoom in / fit   ·   Wheel: zoom   ·   Drag: move   ·   ← →: before / after   ·   Esc: close"
            if self.frames:
                hint += "   ·   Space: pause"
            p.setPen(QColor(T.TEXT3))
            p.drawText(QRectF(0, self.height() - 34, self.width(), 20), Qt.AlignmentFlag.AlignCenter, hint)
        # close + arrows
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40 if self.hover == "close" else 18))
        p.drawEllipse(QRectF(self.width() - 52, 16, 36, 36))
        p.drawPixmap(QPointF(self.width() - 43, 25), icons.pixmap("close", 18, "#FFFFFF"))
        if len(self.entries) > 1:
            for zone, x, ic in (("prev", 14, "back"), ("next", self.width() - 54, "arrow")):
                cy = self.height() / 2
                p.setBrush(QColor(255, 255, 255, 46 if self.hover == zone else 16))
                p.drawEllipse(QRectF(x, cy - 20, 40, 40))
                p.drawPixmap(QPointF(x + 10, cy - 10), icons.pixmap(ic, 20, "#FFFFFF"))
        p.end()


def entry_path_ok(e):
    return e and os.path.isfile(e.get("file", ""))
