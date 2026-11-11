"""The moving backgrounds (Settings, Look & feel): faint, behind everything, only drawn while the app is in front.
Under another program, or a game, they stop completely."""
import math
import random

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import theme as T

KINDS = (("none", "None"), ("dots", "Dots"), ("cloud", "Dot cloud"), ("code", "Code"))
_CODE = ("for seed in batch:", "    latent = noise(seed)", "    image = decode(latent)", "while steps_left:", "    x = sampler.step(x)",
         "prompt = weights(words)", "if cfg > 1.0:", "    guide(x, prompt)", "def blend(a, b, t):", "    return a + (b - a) * t",
         "model.load()", "vae.decode(x)", "queue.put(job)", "return image", "sigmas = schedule(steps)")
_GAP = 34                               # between the dots
_REACH = 130                            # how far the cursor is felt


class Backdrop(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.kind = "none"
        self.mouse = QPointF(-999, -999)
        self.points = []
        self.rows = []
        self.timer = QTimer(self, timeout=self._tick)
        QApplication.instance().applicationStateChanged.connect(lambda _s: self._run())

    def set_kind(self, kind):
        self.kind = kind
        self.points = []
        self.rows = []
        self.setVisible(kind != "none")
        self._run()
        self.update()

    def _run(self):
        from .widgets import anims_on
        on = self.kind != "none" and anims_on() and QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        if on and not self.timer.isActive():
            self.timer.start({"dots": 33, "cloud": 80, "code": 120}.get(self.kind, 80))
        elif not on:
            self.timer.stop()

    def _tick(self):
        if not self.window().isVisible():
            return
        m = QPointF(self.mapFromGlobal(QCursor.pos()))
        moved = (m - self.mouse).manhattanLength() > 1
        old, self.mouse = self.mouse, m
        if self.kind == "dots":
            if moved:                   # only around the cursor: the rest of the app is left alone
                for c in (old, m):
                    self.update(QRect(int(c.x() - _REACH - 4), int(c.y() - _REACH - 4), 2 * _REACH + 8, 2 * _REACH + 8))
        elif self.kind == "cloud":
            self._drift()
            self.update()
        elif self.kind == "code":
            self._type()

    # ---- dot cloud
    def _drift(self):
        w, h = max(1, self.width()), max(1, self.height())
        if not self.points:
            r = random.Random()
            self.points = [[r.uniform(0, w), r.uniform(0, h), r.uniform(-0.5, 0.5), r.uniform(-0.5, 0.5)] for _ in range(60)]
        for pt in self.points:
            dx, dy = pt[0] - self.mouse.x(), pt[1] - self.mouse.y()
            d = math.hypot(dx, dy)
            if 1 < d < _REACH:          # moves aside a little
                pt[2] += dx / d * 0.05
                pt[3] += dy / d * 0.05
            sp = math.hypot(pt[2], pt[3])
            if sp > 0.7:
                pt[2], pt[3] = pt[2] * 0.97, pt[3] * 0.97
            pt[0] = (pt[0] + pt[2]) % w
            pt[1] = (pt[1] + pt[3]) % h

    # ---- code being written
    def _type(self):
        w, h = self.width(), self.height()
        if not self.rows or len(self.rows) < 8 and random.random() < 0.12:
            self.rows.append({"x": random.randint(20, max(21, w - 340)), "y": random.randint(30, max(31, h - 30)),
                              "text": random.choice(_CODE), "n": 0, "life": 40})
        for r in list(self.rows):
            if r["n"] < len(r["text"]):
                r["n"] += 1
            else:
                r["life"] -= 1
                if r["life"] <= 0:
                    self.rows.remove(r)
            self.update(QRect(r["x"] - 4, r["y"] - 16, 340, 24))

    # ---- drawing
    def paintEvent(self, e):
        if self.kind == "none":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.kind == "dots":
            self._paint_dots(p, e.rect())
        elif self.kind == "cloud":
            self._paint_cloud(p)
        else:
            self._paint_code(p)
        p.end()

    def _paint_dots(self, p, area):
        base = T.col(T.BORDER_HI)
        accent = T.accent()
        p.setPen(Qt.PenStyle.NoPen)
        x0 = area.left() - area.left() % _GAP
        for x in range(x0, area.right() + _GAP, _GAP):
            for y in range(area.top() - area.top() % _GAP, area.bottom() + _GAP, _GAP):
                dx, dy = x - self.mouse.x(), y - self.mouse.y()
                d = math.hypot(dx, dy)
                k = max(0.0, 1 - d / _REACH)
                c = T.mix(base, accent, k)
                c.setAlphaF(0.55 + 0.45 * k)
                p.setBrush(c)
                # the dots lean a little away from the cursor
                off = 6 * k / max(d, 1)
                r = 1.2 + 1.3 * k
                p.drawEllipse(QPointF(x + dx * off, y + dy * off), r, r)

    def _paint_cloud(self, p):
        line = QColor(T.col(T.BORDER_HI))
        for i, a in enumerate(self.points):
            for b in self.points[i + 1:]:
                d = math.hypot(a[0] - b[0], a[1] - b[1])
                if d < 130:
                    line.setAlphaF(0.5 * (1 - d / 130))
                    p.setPen(QPen(line, 1))
                    p.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.col(T.TEXT3))
        for a in self.points:
            p.drawEllipse(QPointF(a[0], a[1]), 1.6, 1.6)

    def _paint_code(self, p):
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(13)
        p.setFont(f)
        col = QColor(T.col(T.TEXT3))
        for r in self.rows:
            col.setAlphaF(0.55 * min(1.0, r["life"] / 12))
            p.setPen(col)
            p.drawText(QRectF(r["x"], r["y"] - 14, 340, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       r["text"][:r["n"]])

