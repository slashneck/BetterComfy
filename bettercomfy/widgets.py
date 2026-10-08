"""Better Comfy's own controls: cards, animated toggles / segmented controls / preset slider, the aspect preview, the
picture & video player, drop zones, toasts and animated page switching."""
import os

from PySide6.QtCore import (QEasingCurve, QEvent, QMimeData, QPoint, QUrl, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation, Signal, Property)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLayout, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QStackedWidget, QToolTip, QVBoxLayout, QWidget)

from . import icons, theme as T
from .config import cfg, resource

QMAX = 16777215


def anims_on():
    return bool(cfg.get("animations", True))


# ------------------------------------------------------------------------------------------------ small helpers

def label(text="", kind=None, wrap=False, sel=False):
    lb = QLabel(text)
    if kind:
        lb.setObjectName(kind)
    lb.setWordWrap(wrap)
    if sel:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(text="", on_click=None, kind=None, icon=None, tip=None, checkable=False, icon_color=None, size=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon:
        col = icon_color or (T.on_accent().name() if kind == "Accent" else ("#A1A1AA" if kind == "Ghost" else "#D4D4D8"))
        b.setIcon(icons.icon(icon, col, 18))
        b.setIconSize(icons.size(size or (16 if text else 18)))
    if tip:
        b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    return b


def icon_button(name, on_click=None, tip=None, checkable=False, size=18, color="#A1A1AA"):
    b = button("", on_click, "Icon", name, tip, checkable, color, size)
    b.setFixedSize(size + 14, size + 14)
    return b


def hrow(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    for x in widgets:
        if x is None:
            h.addStretch(1)
        elif isinstance(x, int):
            h.addSpacing(x)
        else:
            h.addWidget(x)
    return w


def vcol(*widgets, spacing=6, margins=(0, 0, 0, 0)):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    for x in widgets:
        if x is None:
            v.addStretch(1)
        else:
            v.addWidget(x)
    return w


def divider():
    f = QFrame()
    f.setObjectName("Divider")
    return f


def quiet(widget, fn, *a):
    widget.blockSignals(True)
    try:
        fn(*a)
    finally:
        widget.blockSignals(False)


def set_combo(cb, data):
    i = cb.findData(data)
    if i >= 0:
        quiet(cb, cb.setCurrentIndex, i)


def elide(text, width, font=None):
    fm = QFontMetrics(font or QFont())
    return fm.elidedText(text, Qt.TextElideMode.ElideMiddle, width)


def nice_name(f):
    return os.path.splitext(os.path.basename(f or ""))[0]


def human_time(s):
    s = int(max(0, s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


# ------------------------------------------------------------------------------------------------ card

class Card(QFrame):
    """A rounded surface with an optional small-caps title, a right-side widget and a body."""

    def __init__(self, title=None, right=None, spacing=10, margins=(16, 14, 16, 16), flat=False):
        super().__init__()
        self.setObjectName("Card")
        if flat:
            self.setProperty("flat", "true")
        v = QVBoxLayout(self)
        v.setContentsMargins(*margins)
        v.setSpacing(spacing)
        self.head = None
        if title or right:
            head = QWidget()
            h = QHBoxLayout(head)
            h.setContentsMargins(0, 0, 0, 2)
            h.setSpacing(6)
            if title:
                self.title = label(title.upper(), "CardTitle")
                h.addWidget(self.title)
            h.addStretch(1)
            if right is not None:
                for r in (right if isinstance(right, (list, tuple)) else [right]):
                    h.addWidget(r)
            self.head = head
            v.addWidget(head)
        self.body = v

    def add(self, *ws, stretch=0):
        for w in ws:
            if isinstance(w, QLayout):
                self.body.addLayout(w)
            else:
                self.body.addWidget(w, stretch)
        return self


def field(text, widget, tip=None, stacked=False, label_w=96):
    """A setting row: a muted name on the left (or above), the control on the right."""
    w = QWidget()
    lb = label(text, "Muted")
    if tip:
        lb.setToolTip(tip)
        widget.setToolTip(widget.toolTip() or tip)
    if stacked:
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        v.addWidget(lb)
        v.addWidget(widget)
    else:
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        lb.setFixedWidth(label_w)
        h.addWidget(lb)
        h.addWidget(widget, 1)
    return w


class Scroll(QScrollArea):
    """A vertically scrolling column."""

    def __init__(self, margins=(0, 0, 0, 0), spacing=12):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ScrollBody")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)
        self.setWidget(body)
        self.viewport().setObjectName("ScrollViewport")
        self.viewport().setStyleSheet("#ScrollViewport { background: transparent; }")

    def add(self, *ws):
        for w in ws:
            self.lay.addWidget(w)

    def end(self):
        self.lay.addStretch(1)


# ------------------------------------------------------------------------------------------------ toggle

class Toggle(QAbstractButton):
    def __init__(self, checked=False, on_change=None, tip=None):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._k = 1.0 if checked else 0.0
        self._hover = False
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._go)
        if on_change:
            self.toggled.connect(lambda v: on_change(bool(v)))
        if tip:
            self.setToolTip(tip)

    def _go(self, on):
        if not anims_on():
            self._k = 1.0 if on else 0.0
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._k)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set(self, on):
        quiet(self, self.setChecked, bool(on))
        self._k = 1.0 if on else 0.0
        self.update()

    def _get(self):
        return self._k

    def _set(self, v):
        self._k = v
        self.update()

    knob = Property(float, _get, _set)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def sizeHint(self):
        return QSize(38, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor(T.SURFACE3 if not self._hover else "#2C2C31")
        on = T.accent() if self.isEnabled() else QColor(T.TEXT3)
        p.setPen(QPen(QColor(T.BORDER_HI), 1) if self._k < 0.5 else Qt.PenStyle.NoPen)
        p.setBrush(T.mix(off, on, self._k))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.x() + 3 + (r.width() - d - 6) * self._k
        p.setPen(Qt.PenStyle.NoPen)
        knob = T.mix(QColor("#BDBDC4"), T.on_accent() if T.on_accent().lightness() > 128 else QColor("#FFFFFF"), self._k)
        if not self.isEnabled():
            knob = QColor("#55555C")
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, r.y() + 3, d, d))
        p.end()


class ToggleRow(QWidget):
    """Text (and a muted line under it) on the left, a toggle on the right."""

    def __init__(self, text, sub=None, checked=False, on_change=None, tip=None):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.text = label(text)
        col.addWidget(self.text)
        if sub is not None:
            self.sub = label(sub, "Faint", wrap=True)
            col.addWidget(self.sub)
        h.addLayout(col, 1)
        self.toggle = Toggle(checked, on_change)
        h.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        if tip:
            self.setToolTip(tip)

    def set(self, v):
        self.toggle.set(v)

    def isChecked(self):
        return self.toggle.isChecked()


# ------------------------------------------------------------------------------------------------ segmented

class Segmented(QWidget):
    """Options side by side; the picked one sits on a pill that slides over."""
    changed = Signal(str)

    def __init__(self, options, on_change=None, value=None, expand=True, height=32, icons_=None):
        super().__init__()
        self.opts = [(o[0], o[1], o[2] if len(o) > 2 else "") for o in options]
        self.icons = icons_ or {}
        self.expand = expand
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.idx = 0
        self.hover = -1
        self._hl = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        if value is not None:
            self.set(value, animate=False)
        if on_change:
            self.changed.connect(on_change)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed)

    def value(self):
        return self.opts[self.idx][0] if self.opts else None

    def _widths(self):
        fm = QFontMetrics(self.font())
        nat = [fm.horizontalAdvance(o[1]) + 26 + (20 if o[0] in self.icons else 0) for o in self.opts]
        if self.expand:
            avail = self.width() - 6
            tot = sum(nat)
            if tot < avail:
                extra = (avail - tot) / len(nat)
                nat = [n + extra for n in nat]
        return nat

    def _rect(self, i):
        ws = self._widths()
        x = 3 + sum(ws[:i])
        return QRectF(x, 3, ws[i], self.height() - 6)

    def sizeHint(self):
        return QSize(int(sum(self._widths()) + 6) if not self.expand else 200, self.height())

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(sum(fm.horizontalAdvance(o[1]) + 18 for o in self.opts) + 6, self.height())

    def set(self, key, animate=True):
        for i, o in enumerate(self.opts):
            if o[0] == key:
                self._go(i, animate)
                return

    def _go(self, i, animate=True):
        self.idx = i
        target = self._rect(i) if self.width() > 10 else None
        if animate and anims_on() and self._hl is not None and target is not None:
            self._anim.stop()
            self._anim.setStartValue(self._hl)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._hl = target
            self.update()

    def _moved(self, v):
        self._hl = v
        self.update()

    def resizeEvent(self, e):
        self._hl = self._rect(self.idx) if self.opts else None
        super().resizeEvent(e)

    def _at(self, x):
        ws = self._widths()
        acc = 3
        for i, w in enumerate(ws):
            if acc <= x < acc + w:
                return i
            acc += w
        return -1

    def mouseMoveEvent(self, e):
        h = self._at(e.position().x())
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position().x())
        if i >= 0 and i != self.idx and self.isEnabled():
            self._go(i)
            self.changed.emit(self.opts[i][0])

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            i = self._at(e.position().x() if hasattr(e, "position") else e.pos().x())
            if 0 <= i < len(self.opts) and self.opts[i][2]:
                QToolTip.showText(e.globalPos(), self.opts[i][2], self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T.BORDER_HI), 1))
        p.setBrush(QColor(T.FIELD))
        p.drawRoundedRect(r, 10, 10)
        if self._hl is None and self.opts:
            self._hl = self._rect(self.idx)
        if 0 <= self.hover < len(self.opts) and self.hover != self.idx:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.SURFACE2))
            p.drawRoundedRect(self._rect(self.hover), 7, 7)
        if self._hl is not None:
            p.setPen(QPen(T.mix(T.accent(), QColor(T.SURFACE3), 0.55), 1))
            p.setBrush(T.mix(QColor(T.SURFACE3), T.accent(), 0.16))
            p.drawRoundedRect(self._hl, 7, 7)
        for i, o in enumerate(self.opts):
            rr = self._rect(i)
            sel = i == self.idx
            col = QColor(T.TEXT if sel else T.TEXT2)
            if not self.isEnabled():
                col = QColor(T.TEXT3)
            f = QFont(self.font())
            if sel:
                f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(col)
            if o[0] in self.icons:
                fm = QFontMetrics(f)
                tw = fm.horizontalAdvance(o[1]) + 20
                x0 = rr.x() + (rr.width() - tw) / 2
                pm = icons.pixmap(self.icons[o[0]], 14, col.name())
                p.drawPixmap(QPointF(x0, rr.center().y() - 7), pm)
                p.drawText(QRectF(x0 + 20, rr.y(), rr.width(), rr.height()), Qt.AlignmentFlag.AlignVCenter, o[1])
            else:
                p.drawText(rr, Qt.AlignmentFlag.AlignCenter, o[1])
        p.end()


# ------------------------------------------------------------------------------------------------ preset slider

class PresetPicker(QWidget):
    """From 'Ultra Fast' to 'Best': stops on a track, the thumb glides to the one picked."""
    changed = Signal(str)

    def __init__(self, presets, on_change=None, value=None):
        super().__init__()
        self.presets = presets            # [(key, name, line)]
        self.idx = 0
        self._x = 0.0
        self.custom = False
        self.setFixedHeight(66)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.hover = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_x", float(v)), self.update()))
        self._drag = False
        if value is not None:
            self.set(value, animate=False)
        if on_change:
            self.changed.connect(on_change)

    def value(self):
        return self.presets[self.idx][0]

    def _stop_x(self, i):
        n = len(self.presets)
        left, right = 26, self.width() - 26
        return left + (right - left) * (i / max(1, n - 1))

    def set(self, key, animate=True):
        for i, pr in enumerate(self.presets):
            if pr[0] == key:
                self._go(i, animate)

    def _go(self, i, animate=True):
        self.idx = i
        if animate and anims_on() and self.width() > 20:
            self._anim.stop()
            self._anim.setStartValue(self._x)
            self._anim.setEndValue(float(i))
            self._anim.start()
        else:
            self._x = float(i)
            self.update()

    def _nearest(self, x):
        return min(range(len(self.presets)), key=lambda i: abs(self._stop_x(i) - x))

    def mousePressEvent(self, e):
        if not self.isEnabled():
            return
        self._drag = True
        i = self._nearest(e.position().x())
        if i != self.idx:
            self._go(i)
            self.changed.emit(self.presets[i][0])

    def mouseMoveEvent(self, e):
        h = self._nearest(e.position().x())
        if self._drag and h != self.idx:
            self._go(h)
            self.changed.emit(self.presets[h][0])
        if h != self.hover:
            self.hover = h
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.presets)
        y = 20
        x0, x1 = self._stop_x(0), self._stop_x(n - 1)
        acc = T.accent()
        # track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.SURFACE3))
        p.drawRoundedRect(QRectF(x0, y - 2, x1 - x0, 4), 2, 2)
        xt = x0 + (x1 - x0) * (self._x / max(1, n - 1))
        grad = QLinearGradient(x0, 0, max(x0 + 1, xt), 0)
        grad.setColorAt(0, T.mix(acc, QColor(T.SURFACE3), 0.65))
        grad.setColorAt(1, acc)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(x0, y - 2, xt - x0, 4), 2, 2)
        # stops
        for i in range(n):
            sx = self._stop_x(i)
            passed = i <= self._x + 0.01
            p.setBrush(acc if passed else QColor("#3A3A40"))
            r = 4 if i != self.hover else 5
            p.drawEllipse(QPointF(sx, y), r, r)
        # thumb
        glow = QColor(acc)
        glow.setAlpha(60)
        p.setBrush(glow)
        p.drawEllipse(QPointF(xt, y), 12, 12)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(xt, y), 7.5, 7.5)
        p.setBrush(acc)
        p.drawEllipse(QPointF(xt, y), 3, 3)
        # labels
        f = QFont(self.font())
        f.setPixelSize(12)
        for i, pr in enumerate(self.presets):
            sx = self._stop_x(i)
            sel = i == self.idx
            f.setWeight(QFont.Weight.DemiBold if sel else QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(T.TEXT if sel else (T.TEXT2 if i == self.hover else T.TEXT3)))
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(pr[1])
            lx = max(0, min(self.width() - tw, sx - tw / 2))
            p.drawText(QPointF(lx, y + 30), pr[1])
        p.end()


# ------------------------------------------------------------------------------------------------ slider + number

class Slider(QWidget):
    """A slider with its number next to it (typed numbers may go past the slider's range)."""
    changed = Signal(float)

    def __init__(self, lo, hi, step=0.05, decimals=2, value=None, on_change=None, suffix="", soft_hi=None, width=66):
        super().__init__()
        self.lo, self.hi, self.step = lo, (soft_hi if soft_hi is not None else hi), step
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.s = QSlider(Qt.Orientation.Horizontal)
        self.s.setRange(0, int(round((self.hi - lo) / step)))
        self.s.setCursor(Qt.CursorShape.PointingHandCursor)
        self.n = QDoubleSpinBox()
        self.n.setDecimals(decimals)
        self.n.setRange(lo, hi)
        self.n.setSingleStep(step)
        self.n.setFixedWidth(width)
        self.n.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if suffix:
            self.n.setSuffix(suffix)
        h.addWidget(self.s, 1)
        h.addWidget(self.n)
        self.s.valueChanged.connect(self._from_slider)
        self.n.valueChanged.connect(self._from_box)
        if value is not None:
            self.set(value)
        if on_change:
            self.changed.connect(on_change)

    def value(self):
        return float(self.n.value())

    def set(self, v):
        quiet(self.n, self.n.setValue, float(v))
        quiet(self.s, self.s.setValue, int(round((min(self.hi, max(self.lo, float(v))) - self.lo) / self.step)))

    def _from_slider(self, i):
        v = self.lo + i * self.step
        quiet(self.n, self.n.setValue, v)
        self.changed.emit(float(self.n.value()))

    def _from_box(self, v):
        quiet(self.s, self.s.setValue, int(round((min(self.hi, max(self.lo, v)) - self.lo) / self.step)))
        self.changed.emit(float(v))


# ------------------------------------------------------------------------------------------------ chips / flow

def chip(text, on_click=None, checkable=True, tip=None):
    c = QPushButton(text)
    c.setObjectName("Chip")
    c.setCheckable(checkable)
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        c.setToolTip(tip)
    if on_click:
        c.clicked.connect(lambda *_: on_click())
    return c


class Flow(QLayout):
    """Lays its widgets out in lines that wrap."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._place(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, r, test):
        x, y, line = r.x(), r.y(), 0
        sp = self.spacing()
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line > 0:
                x, y, line = r.x(), y + line + sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        return y + line - r.y()

    def clear(self):
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().deleteLater()


class ChipBox(QWidget):
    def __init__(self, spacing=6):
        super().__init__()
        self.flow = Flow(self, spacing)

    def add(self, w):
        self.flow.addWidget(w)
        return w

    def clear(self):
        self.flow.clear()
        self.updateGeometry()


# ------------------------------------------------------------------------------------------------ collapsible

class Collapsible(QWidget):
    """A section that opens and closes (smoothly)."""

    def __init__(self, title, open_=False, sub=None):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = QPushButton()
        self.head.setObjectName("Ghost")
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.setStyleSheet("QPushButton { text-align: left; padding: 6px 2px; color: #A1A1AA; font-weight: 600; }"
                                "QPushButton:hover { color: #F4F4F5; background: transparent; }")
        self._title = title
        self.head.clicked.connect(self.toggle)
        if sub:
            self.head.setToolTip(sub)
        v.addWidget(self.head)
        self.box = QWidget()
        self.lay = QVBoxLayout(self.box)
        self.lay.setContentsMargins(0, 6, 0, 4)
        self.lay.setSpacing(10)
        v.addWidget(self.box)
        self._open = open_
        self._anim = QPropertyAnimation(self.box, b"maximumHeight", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._done)
        self.box.setMaximumHeight(QMAX if open_ else 0)
        self.box.setVisible(open_)
        self._paint_head()

    def _paint_head(self):
        self.head.setIcon(icons.icon("chevron" if self._open else "chevron_r", "#8A8A92", 14))
        self.head.setText("  " + self._title)

    def add(self, *ws):
        for w in ws:
            self.lay.addWidget(w)
        return self

    def toggle(self):
        self.set_open(not self._open)

    def set_open(self, on):
        self._open = on
        self._paint_head()
        if not anims_on():
            self.box.setVisible(on)
            self.box.setMaximumHeight(QMAX if on else 0)
            return
        self._anim.stop()
        self.box.setVisible(True)
        h = self.box.sizeHint().height()
        self._anim.setStartValue(self.box.height() if self.box.maximumHeight() == QMAX else self.box.maximumHeight())
        self._anim.setEndValue(h if on else 0)
        self._anim.start()

    def _done(self):
        if self._open:
            self.box.setMaximumHeight(QMAX)
        else:
            self.box.setVisible(False)


# ------------------------------------------------------------------------------------------------ aspect preview

class AspectPreview(QWidget):
    """The size a picture will be, drawn to scale against the model's native square."""

    def __init__(self, height=150):
        super().__init__()
        self.setFixedHeight(height)
        self.w, self.h, self.fw, self.fh, self.base, self.note = 1024, 1024, 1024, 1024, 1024, ""

    def set(self, w, h, fw=None, fh=None, base=1024, note=""):
        self.w, self.h, self.fw, self.fh, self.base, self.note = w, h, fw or w, fh or h, base, note
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.setBrush(QColor(T.FIELD))
        p.drawRoundedRect(r, 12, 12)
        area = QRectF(r.x() + 14, r.y() + 12, r.width() * 0.46, r.height() - 24)
        big = max(self.fw, self.fh, self.base) * 1.0
        s = min(area.width() / big, area.height() / big)
        cx, cy = area.center().x(), area.center().y()
        # native square
        bs = self.base * s
        pen = QPen(QColor("#3A3A40"), 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(cx - bs / 2, cy - bs / 2, bs, bs), 3, 3)
        acc = T.accent()
        if (self.fw, self.fh) != (self.w, self.h):
            fw, fh = self.fw * s, self.fh * s
            p.setPen(QPen(T.mix(acc, QColor(T.FIELD), 0.45), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(QRectF(cx - fw / 2, cy - fh / 2, fw, fh), 4, 4)
        gw, gh = self.w * s, self.h * s
        fill = QColor(acc)
        fill.setAlpha(36)
        p.setPen(QPen(acc, 1.4))
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(cx - gw / 2, cy - gh / 2, gw, gh), 4, 4)
        # text
        tx = area.right() + 18
        f = QFont(self.font())
        f.setPixelSize(19)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        y = r.y() + r.height() / 2 - 22
        p.drawText(QPointF(tx, y), f"{self.w} × {self.h}")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(T.TEXT2))
        p.drawText(QPointF(tx, y + 22), f"{self.w * self.h / 1e6:.2f} MP  ·  {self._ratio()}")
        if (self.fw, self.fh) != (self.w, self.h):
            p.setPen(acc)
            p.drawText(QPointF(tx, y + 42), f"→ {self.fw} × {self.fh} final")
        if self.note:
            p.setPen(QColor(T.TEXT3))
            p.drawText(QPointF(tx, y + (62 if (self.fw, self.fh) != (self.w, self.h) else 42)), self.note)
        p.end()

    def _ratio(self):
        r = self.w / max(1, self.h)
        best = min(((a, b) for a in range(1, 22) for b in range(1, 22)), key=lambda ab: abs(ab[0] / ab[1] - r) + 0.002 * (ab[0] + ab[1]))
        return f"{best[0]}:{best[1]}"


# ------------------------------------------------------------------------------------------------ drop zone

class ImageDrop(QFrame):
    """Drop a picture (or click to choose one); shows it, with a button to take it away."""
    changed = Signal(str)
    many = Signal(list)                # several pictures dropped at once

    def __init__(self, text="Drop a picture here, or click to choose", height=170):
        super().__init__()
        self.text = text
        self.path = ""
        self.pm = None
        self.setFixedHeight(height)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self._drag = False
        self.clear_btn = icon_button("close", self.clear, "Remove the picture", size=14, color="#E4E4E7")
        self.clear_btn.setParent(self)
        self.clear_btn.setStyleSheet("QPushButton#Icon { background: rgba(0,0,0,150); border-radius: 12px; }"
                                     "QPushButton#Icon:hover { background: rgba(0,0,0,210); }")
        self.clear_btn.hide()

    def set_path(self, path, emit=True):
        self.path = path if path and os.path.isfile(path) else ""
        self.pm = QPixmap(self.path) if self.path else None
        if self.pm is not None and self.pm.isNull():
            self.pm, self.path = None, ""
        self.clear_btn.setVisible(bool(self.path))
        self.setToolTip(self.path)
        self.update()
        if emit:
            self.changed.emit(self.path)

    def clear(self):
        self.set_path("")

    def resizeEvent(self, e):
        self.clear_btn.move(self.width() - self.clear_btn.width() - 8, 8)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            d = cfg.get("last_pick_dir") or os.path.expanduser("~")
            f, _ = QFileDialog.getOpenFileName(self, "Choose a picture", d, "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)")
            if f:
                cfg.set("last_pick_dir", os.path.dirname(f))
                self.set_path(f)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag = True
            self.update()

    def dragLeaveEvent(self, e):
        self._drag = False
        self.update()

    def dropEvent(self, e):
        self._drag = False
        files = [u.toLocalFile() for u in e.mimeData().urls()
                 if os.path.splitext(u.toLocalFile())[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")]
        if len(files) > 1 and self.receivers(self.many) > 0:
            self.many.emit(files)
        elif files:
            self.set_path(files[0])
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        acc = T.accent()
        if self.pm is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.FIELD))
            p.drawRoundedRect(r, 12, 12)
            inner = r.adjusted(6, 6, -6, -6)
            s = min(inner.width() / self.pm.width(), inner.height() / self.pm.height())
            w, h = self.pm.width() * s, self.pm.height() * s
            tr = QRectF(inner.center().x() - w / 2, inner.center().y() - h / 2, w, h)
            path = QPainterPath()
            path.addRoundedRect(tr, 8, 8)
            p.setClipPath(path)
            p.drawPixmap(tr.toRect(), self.pm)
            p.setClipping(False)
            p.setPen(QPen(QColor(acc if self._drag else (T.BORDER_HI if self._hover else T.BORDER)), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 12, 12)
            f = QFont(self.font())
            f.setPixelSize(11)
            p.setFont(f)
            txt = f"{self.pm.width()} × {self.pm.height()}"
            fm = QFontMetrics(f)
            bw = fm.horizontalAdvance(txt) + 14
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            br = QRectF(r.x() + 10, r.bottom() - 30, bw, 20)
            p.drawRoundedRect(br, 6, 6)
            p.setPen(QColor(T.TEXT))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
        else:
            p.setPen(QPen(acc if self._drag else QColor("#3A3A40" if self._hover else "#2A2A2F"), 1.3, Qt.PenStyle.DashLine))
            bg = QColor(acc)
            bg.setAlpha(18) if self._drag else bg.setAlpha(0)
            p.setBrush(bg if self._drag else QColor(T.FIELD))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
            pm = icons.pixmap("upload", 26, acc.name() if (self._drag or self._hover) else "#6B6B73")
            p.drawPixmap(QPointF(r.center().x() - 13, r.center().y() - 30), pm)
            p.setPen(QColor(T.TEXT2 if self._hover else T.TEXT3))
            p.drawText(r.adjusted(16, 30, -16, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter |
                       Qt.TextFlag.TextWordWrap, self.text)
        p.end()


# ------------------------------------------------------------------------------------------------ player

class Player(QWidget):
    """Shows a picture or plays frames, fitted and rounded; live progress drawn over it."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.frames, self.fps, self.i = [], 16.0, 0
        self.title, self.sub = "", ""
        self.badge = ""
        self.prog, self.prog_text = None, ""
        self.paused = False
        self._fade = 1.0
        self.timer = QTimer(self, timeout=self._next)
        self._fa = QVariantAnimation(self)
        self._fa.setDuration(260)
        self._fa.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fa.valueChanged.connect(lambda v: (setattr(self, "_fade", float(v)), self.update()))
        self.logo = QPixmap(resource("assets", "logo.png"))
        self.click_pauses = True               # a click pauses a video (off: the owner opens the big viewer)
        self._shimmer = 0.0
        self._sh = QTimer(self, interval=33, timeout=self._shim)

    def _fade_in(self):
        if anims_on():
            self._fa.stop()
            self._fa.setStartValue(0.0)
            self._fa.setEndValue(1.0)
            self._fa.start()
        else:
            self._fade = 1.0

    def show_image(self, img, badge="", fade=True):
        self.timer.stop()
        if isinstance(img, str):
            img = QPixmap(img)
        elif isinstance(img, QImage):
            img = QPixmap.fromImage(img)
        self.frames = [img] if img is not None and not img.isNull() else []
        self.i, self.badge, self.title, self.sub = 0, badge, "", ""
        if fade:
            self._fade_in()
        self.update()

    def play(self, images, fps, badge=""):
        self.frames = [QPixmap.fromImage(q) if isinstance(q, QImage) else q for q in images]
        self.fps, self.i, self.badge, self.title, self.sub = max(1.0, float(fps)), 0, badge, "", ""
        self.paused = False
        self.timer.start(int(1000 / self.fps))
        self._fade_in()
        self.update()

    def message(self, title, sub=""):
        self.timer.stop()
        self.frames, self.title, self.sub, self.badge = [], title, sub, ""
        self.update()

    def set_progress(self, f, text=""):
        self.prog, self.prog_text = f, text
        if f is not None and not self._sh.isActive() and anims_on():
            self._sh.start()
        if f is None:
            self._sh.stop()
        self.update()

    def _shim(self):
        self._shimmer = (self._shimmer + 0.012) % 1.0
        self.update()

    def _next(self):
        if self.frames and not self.paused:
            self.i = (self.i + 1) % len(self.frames)
            self.update()

    def mousePressEvent(self, e):
        if len(self.frames) > 1 and self.click_pauses:
            self.paused = not self.paused
            self.update()
        self.clicked.emit()

    def enterEvent(self, e):
        if not self.click_pauses and self.frames:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def leaveEvent(self, e):
        self.unsetCursor()

    def image_rect(self):
        if not self.frames:
            return QRectF()
        pm = self.frames[self.i % len(self.frames)]
        r = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        s = min(r.width() / max(1, pm.width()), r.height() / max(1, pm.height()))
        w, h = pm.width() * s, pm.height() * s
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.BG))
        if self.frames:
            pm = self.frames[self.i % len(self.frames)]
            tr = self.image_rect()
            path = QPainterPath()
            path.addRoundedRect(tr, 10, 10)
            p.setClipPath(path)
            p.setOpacity(self._fade)
            p.drawPixmap(tr.toRect(), pm)
            p.setOpacity(1.0)
            p.setClipping(False)
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(tr, 10, 10)
            if len(self.frames) > 1 and self.paused:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 140))
                c = tr.center()
                p.drawEllipse(c, 30, 30)
                p.drawPixmap(QPointF(c.x() - 13, c.y() - 14), icons.pixmap("play", 28, "#FFFFFF"))
        else:
            c = QRectF(self.rect()).center()
            if not self.logo.isNull():
                p.setOpacity(0.10)
                s = 92
                p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2 - 40, s, s).toRect(), self.logo)
                p.setOpacity(1.0)
            f = QFont(self.font())
            f.setPixelSize(15)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(QColor(T.TEXT2))
            p.drawText(QRectF(40, c.y() + 18, self.width() - 80, 24), Qt.AlignmentFlag.AlignCenter, self.title)
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(T.TEXT3))
            p.drawText(QRectF(60, c.y() + 44, self.width() - 120, 60),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.sub)
        if self.badge:
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            fm = QFontMetrics(f)
            bw = fm.horizontalAdvance(self.badge) + 16
            tr = self.image_rect() if self.frames else QRectF(self.rect()).adjusted(18, 18, -18, -18)
            br = QRectF(tr.x() + 10, tr.y() + 10, bw, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(br, 7, 7)
            p.setPen(QColor("#E4E4E7"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, self.badge)
        if self.prog is not None:
            w = self.width() - 36
            y = self.height() - 14
            acc = T.accent()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.SURFACE3))
            p.drawRoundedRect(QRectF(18, y, w, 3), 1.5, 1.5)
            fw = max(3.0, w * max(0.0, min(1.0, self.prog)))
            grad = QLinearGradient(18, 0, 18 + fw, 0)
            grad.setColorAt(0, T.mix(acc, QColor(T.BG), 0.4))
            grad.setColorAt(1, acc)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(18, y, fw, 3), 1.5, 1.5)
            sx = 18 + fw * self._shimmer
            sg = QLinearGradient(sx - 40, 0, sx + 40, 0)
            sg.setColorAt(0, QColor(255, 255, 255, 0))
            sg.setColorAt(0.5, QColor(255, 255, 255, 120))
            sg.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(sg))
            p.drawRoundedRect(QRectF(max(18, sx - 40), y, min(80, fw), 3), 1.5, 1.5)
            if self.prog_text:
                f = QFont(self.font())
                f.setPixelSize(12)
                p.setFont(f)
                fm = QFontMetrics(f)
                t = fm.elidedText(self.prog_text, Qt.TextElideMode.ElideRight, w)
                tw = fm.horizontalAdvance(t) + 18
                br = QRectF(18, y - 32, tw, 24)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawRoundedRect(br, 8, 8)
                p.setPen(QColor(T.TEXT))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, t)
        p.end()


# ------------------------------------------------------------------------------------------------ filmstrip

class FileList(QListWidget):
    """A list whose items can be dragged out as files (onto a drop zone, Explorer, another app)."""
    file_of = None                     # set by the owner: entry id -> file path

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeData(self, items):
        md = QMimeData()
        urls = []
        for it in items:
            f = self.file_of(it.data(Qt.ItemDataRole.UserRole)) if self.file_of else None
            if f:
                urls.append(QUrl.fromLocalFile(f))
        md.setUrls(urls)
        return md

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction


class Filmstrip(FileList):
    """Recent results in a row (newest first)."""
    picked = Signal(str)

    def __init__(self, height=112, icon=84):
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setIconSize(QSize(icon, icon))
        self.setGridSize(QSize(icon + 12, icon + 12))
        self.setFixedHeight(height)
        self.setMovement(QListWidget.Movement.Static)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(0)
        self.setStyleSheet("QListWidget::item { padding: 4px; margin: 0; }"
                           "QListWidget::item:selected { background: #222226; border: 1px solid " + T.accent().name() + "; }")
        self.currentItemChanged.connect(lambda cur, _p: cur and self.picked.emit(cur.data(Qt.ItemDataRole.UserRole)))
        self._thumbs = {}
        self.icon = icon

    def thumb_icon(self, entry):
        key = entry.get("thumb") or entry.get("file")
        if key not in self._thumbs:
            pm = QPixmap(key)
            if pm.isNull():
                pm = QPixmap(self.icon, self.icon)
                pm.fill(QColor(T.SURFACE2))
            pm = pm.scaled(self.icon * 2, self.icon * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
            sq = QPixmap(self.icon * 2, self.icon * 2)
            sq.fill(Qt.GlobalColor.transparent)
            p = QPainter(sq)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, sq.width(), sq.height()), 14, 14)
            p.setClipPath(path)
            p.drawPixmap(int((sq.width() - pm.width()) / 2), int((sq.height() - pm.height()) / 2), pm)
            if entry.get("kind") == "video":
                p.setClipping(False)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 150))
                p.drawEllipse(QPointF(sq.width() - 26, sq.height() - 26), 16, 16)
                p.drawPixmap(QPointF(sq.width() - 37, sq.height() - 37), icons.pixmap("play", 22, "#FFFFFF"))
            p.end()
            sq.setDevicePixelRatio(2.0)
            self._thumbs[key] = QIcon(sq)
        return self._thumbs[key]

    def fill(self, entries, keep=None):
        self.blockSignals(True)
        self.clear()
        for e in entries:
            it = QListWidgetItem(self.thumb_icon(e), "")
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setToolTip((e.get("prompt") or "")[:300])
            self.addItem(it)
            if keep and e["id"] == keep:
                self.setCurrentItem(it)
        self.blockSignals(False)

    def prepend(self, e, select=True):
        it = QListWidgetItem(self.thumb_icon(e), "")
        it.setData(Qt.ItemDataRole.UserRole, e["id"])
        it.setToolTip((e.get("prompt") or "")[:300])
        self.insertItem(0, it)
        if select:
            self.blockSignals(True)
            self.setCurrentItem(it)
            self.blockSignals(False)

    def remove_id(self, eid):
        for i in range(self.count()):
            if self.item(i).data(Qt.ItemDataRole.UserRole) == eid:
                self.takeItem(i)
                return


# ------------------------------------------------------------------------------------------------ status dot / ring

class PulseDot(QWidget):
    def __init__(self, size=10):
        super().__init__()
        self.setFixedSize(size + 8, size + 8)
        self.col = QColor(T.TEXT3)
        self.pulse = False
        self._t = 0.0
        self._timer = QTimer(self, interval=40, timeout=self._tick)

    def set(self, color, pulse=False):
        self.col = QColor(color)
        self.pulse = pulse
        if pulse and anims_on():
            self._timer.start()
        else:
            self._timer.stop()
            self._t = 0.0
        self.update()

    def _tick(self):
        self._t = (self._t + 0.035) % 1.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = (self.width() - 8) / 2
        if self.pulse:
            g = QColor(self.col)
            g.setAlphaF(0.45 * (1 - self._t))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r + 4 * self._t, r + 4 * self._t)
        else:
            g = QColor(self.col)
            g.setAlpha(50)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r + 2.5, r + 2.5)
        p.setBrush(self.col)
        p.drawEllipse(c, r, r)
        p.end()


class Ring(QWidget):
    """A small circular progress ring with a number inside (the queue)."""

    def __init__(self, size=30):
        super().__init__()
        self.setFixedSize(size, size)
        self.f, self.n = 0.0, 0
        self._spin = 0.0
        self._timer = QTimer(self, interval=30, timeout=self._tick)

    def set(self, f, n):
        self.f, self.n = f, n
        if n and anims_on():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._spin = (self._spin + 4) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor(T.SURFACE3), 3))
        p.drawEllipse(r)
        if self.n:
            pen = QPen(T.accent(), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if self.f > 0.001:
                p.drawArc(r, 90 * 16, -int(360 * 16 * max(0.02, min(1, self.f))))
            else:
                p.drawArc(r, int(-self._spin * 16), 70 * 16)
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QColor(T.TEXT))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.n))
        else:
            p.drawPixmap(QPointF(self.width() / 2 - 7, self.height() / 2 - 7), icons.pixmap("check", 14, "#62626B"))
        p.end()


# ------------------------------------------------------------------------------------------------ toasts

class Toasts(QWidget):
    """Little messages that slide in at the bottom right and fade away."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.items = []
        self.hide()

    def show_toast(self, text, kind="info", ms=3800):
        host = self.parentWidget()
        t = QFrame(host)
        t.setObjectName("Toast")
        col = {"ok": T.GOOD, "warn": T.WARN, "error": T.BAD}.get(kind, T.accent().name())
        t.setStyleSheet(f"#Toast {{ background: {T.SURFACE2}; border: 1px solid {T.BORDER_HI}; border-radius: 12px; }}"
                        f"QLabel {{ color: {T.TEXT}; }}")
        h = QHBoxLayout(t)
        h.setContentsMargins(14, 11, 16, 11)
        h.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap({"ok": "check", "warn": "info", "error": "info"}.get(kind, "sparkle"), 16, col))
        h.addWidget(ic)
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setMaximumWidth(360)
        lb.setMinimumWidth(min(360, QFontMetrics(lb.font()).horizontalAdvance(text) + 4))
        h.addWidget(lb)
        t.adjustSize()
        t.setFixedWidth(min(420, max(240, t.sizeHint().width())))
        t.adjustSize()
        eff = QGraphicsOpacityEffect(t)
        eff.setOpacity(0.0)
        t.setGraphicsEffect(eff)
        t.show()
        t.raise_()
        self.items.append(t)
        self._layout(new=t)
        fade = QPropertyAnimation(eff, b"opacity", t)
        fade.setDuration(220 if anims_on() else 1)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.start()
        QTimer.singleShot(ms, lambda: self._close(t))

    def _layout(self, new=None):
        host = self.parentWidget()
        y = host.height() - 22
        for t in reversed(self.items):
            y -= t.height()
            x = host.width() - t.width() - 22
            if t is new and anims_on():
                a = QPropertyAnimation(t, b"pos", t)
                a.setDuration(260)
                a.setEasingCurve(QEasingCurve.Type.OutCubic)
                a.setStartValue(QPoint(x + 40, y))
                a.setEndValue(QPoint(x, y))
                a.start()
            else:
                t.move(x, y)
            y -= 10

    def _close(self, t):
        if t not in self.items:
            return
        eff = t.graphicsEffect()
        a = QPropertyAnimation(eff, b"opacity", t)
        a.setDuration(260 if anims_on() else 1)
        a.setStartValue(1.0)
        a.setEndValue(0.0)

        def gone():
            if t in self.items:
                self.items.remove(t)
            t.deleteLater()
            self._layout()
        a.finished.connect(gone)
        a.start()

    def relayout(self):
        self._layout()


# ------------------------------------------------------------------------------------------------ animated stack

class _Swap(QWidget):
    """Drawn over the pages while they change: the old one lifts away and fades, the new one rises in."""

    def __init__(self, parent, old, new):
        super().__init__(parent)
        self.old, self.new, self.t = old, new, 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_t(self, t):
        self.t = float(t)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))
        t = self.t
        out = min(1.0, t * 1.6)                    # the old page is gone a bit before the new one settles
        if out < 1.0:
            p.setOpacity(1.0 - out)
            p.drawPixmap(QPointF(0, -10 * out), self.old)
        p.setOpacity(t)
        p.drawPixmap(QPointF(0, 18 * (1.0 - t)), self.new)
        p.end()


class FadeStack(QStackedWidget):
    """Pages that cross-fade and glide into place when switched (both drawn as pictures meanwhile - no flicker)."""

    def __init__(self):
        super().__init__()
        self._ov = None
        self._anim = None

    def switch(self, i):
        if i == self.currentIndex():
            return
        if not anims_on() or self.currentWidget() is None or not self.isVisible():
            self.setCurrentIndex(i)
            return
        if self._ov is not None:
            self._anim.stop()
            self._ov.deleteLater()
            self._ov = None
        old = self.currentWidget().grab()
        self.setCurrentIndex(i)
        new_w = self.currentWidget()
        new_w.resize(self.size())
        new = new_w.grab()
        ov = _Swap(self, old, new)
        ov.setGeometry(self.rect())
        ov.show()
        ov.raise_()
        self._ov = ov
        a = QVariantAnimation(self)
        a.setDuration(300)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(ov.set_t)

        def done(ov=ov):
            ov.deleteLater()
            if self._ov is ov:
                self._ov = None
        a.finished.connect(done)
        self._anim = a
        a.start()


# ------------------------------------------------------------------------------------------------ combo

class Combo(QComboBox):
    """A drop-down that never forces its column wider than there is room for (long model names are cut short,
    the open list shows them in full)."""

    def __init__(self, min_chars=8):
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(min_chars)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def showPopup(self):
        fm = QFontMetrics(self.font())
        w = max([fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())] or [0]) + 48
        self.view().setMinimumWidth(min(max(self.width(), w), 760))
        super().showPopup()


# ------------------------------------------------------------------------------------------------ nav rail

class NavButton(QAbstractButton):
    def __init__(self, key, text, icon_name):
        super().__init__()
        self.key, self.text_, self.icon_name = key, text, icon_name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(72, 60)
        self._h = 0.0
        self._a = QVariantAnimation(self)
        self._a.setDuration(150)
        self._a.valueChanged.connect(lambda v: (setattr(self, "_h", float(v)), self.update()))
        self.setToolTip(text)

    def enterEvent(self, e):
        self._anim(1.0)

    def leaveEvent(self, e):
        self._anim(0.0)

    def _anim(self, to):
        if not anims_on():
            self._h = to
            self.update()
            return
        self._a.stop()
        self._a.setStartValue(self._h)
        self._a.setEndValue(to)
        self._a.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        if self._h > 0 and not on:
            c = QColor(T.SURFACE2)
            c.setAlphaF(self._h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(8, 4, self.width() - 16, self.height() - 8), 12, 12)
        col = QColor(T.TEXT) if on else T.mix(QColor("#7A7A83"), QColor(T.TEXT), self._h * 0.7)
        ic = icons.pixmap(self.icon_name, 21, T.accent().name() if on else col.name())
        p.drawPixmap(QPointF(self.width() / 2 - 10.5, 10), ic)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(col)
        p.drawText(QRectF(0, 35, self.width(), 16), Qt.AlignmentFlag.AlignCenter, self.text_)
        p.end()


class NavRail(QWidget):
    """The left rail: logo, pages, an indicator that glides to the active one."""
    changed = Signal(str)

    def __init__(self, items, bottom):
        super().__init__()
        self.setObjectName("PanelR")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(80)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 14, 4, 12)
        v.setSpacing(2)
        logo = QLabel()
        pm = QPixmap(resource("assets", "icon.png"))
        if not pm.isNull():
            pm = pm.scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
            logo.setPixmap(pm)
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo.setToolTip("Better Comfy")
        v.addWidget(logo)
        v.addSpacing(14)
        self.btns = {}
        for key, text, ic in items:
            b = NavButton(key, text, ic)
            b.clicked.connect(lambda _=False, k=key: self.select(k, emit=True))
            v.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            self.btns[key] = b
        v.addStretch(1)
        for key, text, ic in bottom:
            b = NavButton(key, text, ic)
            b.clicked.connect(lambda _=False, k=key: self.select(k, emit=True))
            v.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            self.btns[key] = b
        self.cur = None
        self._y = None
        self._a = QVariantAnimation(self)
        self._a.setDuration(280)
        self._a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(lambda v: (setattr(self, "_y", float(v)), self.update()))

    def select(self, key, emit=False):
        if key not in self.btns:
            return
        for k, b in self.btns.items():
            b.setChecked(k == key)
        target = self.btns[key].geometry().center().y()
        if self._y is None or not anims_on() or not self.isVisible():
            self._y = float(target)
            self.update()
        else:
            self._a.stop()
            self._a.setStartValue(self._y)
            self._a.setEndValue(float(target))
            self._a.start()
        self.cur = key
        if emit:
            self.changed.emit(key)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.cur:
            QTimer.singleShot(0, lambda: (setattr(self, "_y", float(self.btns[self.cur].geometry().center().y())),
                                          self.update()))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._y is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        bg = T.mix(QColor(T.SURFACE2), T.accent(), 0.10)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(12, self._y - 26, self.width() - 24, 52), 12, 12)
        p.setBrush(T.accent())
        p.drawRoundedRect(QRectF(0, self._y - 12, 3.5, 24), 1.75, 1.75)
        p.end()
