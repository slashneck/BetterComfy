"""Side by side: 2 to 4 pictures next to each other. Zoom and move one and all of them follow, to spot small differences."""
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from . import theme as T
from .widgets import button, hrow, label


class Canvas(QWidget):
    def __init__(self, paths):
        super().__init__()
        self.pics = [(QPixmap(p), os.path.basename(p)) for p in paths]
        self.zoom = 1.0                 # 1: each picture fits its cell
        self.pan = QPointF(0, 0)        # in cell-sized units, the same for all
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _cells(self):
        n = len(self.pics)
        cols = 2 if n == 4 else n
        rows = 2 if n == 4 else 1
        w, h = self.width() / cols, self.height() / rows
        return [QRectF((i % cols) * w, (i // cols) * h, w, h).adjusted(3, 3, -3, -3) for i in range(n)]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 3)
        for (pm, name), c in zip(self.pics, self._cells()):
            p.fillRect(c, QColor(T.FIELD))
            p.save()
            p.setClipRect(c)
            if not pm.isNull():
                s = min(c.width() / pm.width(), c.height() / pm.height()) * self.zoom
                w, h = pm.width() * s, pm.height() * s
                x = c.center().x() - w / 2 + self.pan.x() * c.width()
                y = c.center().y() - h / 2 + self.pan.y() * c.height()
                p.drawPixmap(QRectF(x, y, w, h), pm, QRectF(pm.rect()))
            p.restore()
            p.setPen(QColor(T.TEXT2))
            p.drawText(c.adjusted(8, 6, -8, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, name)
        p.end()

    def wheelEvent(self, e):
        old = self.zoom
        self.zoom = max(1.0, min(16.0, self.zoom * (1.25 if e.angleDelta().y() > 0 else 0.8)))
        c = next((c for c in self._cells() if c.contains(e.position())), None)
        if c is not None and old:
            # the spot under the cursor stays where it is
            at = QPointF((e.position().x() - c.center().x()) / c.width(), (e.position().y() - c.center().y()) / c.height())
            self.pan = at - (at - self.pan) * (self.zoom / old)
        if self.zoom == 1.0:
            self.pan = QPointF(0, 0)
        self.update()
        e.accept()

    def mousePressEvent(self, e):
        self._drag = e.position()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None and self._cells():
            c = self._cells()[0]
            d = e.position() - self._drag
            self.pan += QPointF(d.x() / c.width(), d.y() / c.height())
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def reset(self):
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()


class CompareDialog(QDialog):
    def __init__(self, parent, paths):
        super().__init__(parent)
        self.setWindowTitle("Side by side")
        self.resize(1200, 760)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        self.canvas = Canvas(paths[:4])
        v.addWidget(self.canvas, 1)
        v.addWidget(hrow(label("Mouse wheel zooms, dragging moves. All of them follow.", "Faint"), None,
                         button("Fit", self.canvas.reset, "Ghost"), button("Close", self.accept, "Ghost"), spacing=6))
