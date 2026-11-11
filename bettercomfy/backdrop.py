"""The moving backgrounds (Settings, Look & feel): behind everything, only drawn while the app is in front. Under another
program, or a game, they stop completely. No pictures come with them: each picture in the folder Pictures\\Better
Comfy\\Dot art is offered as a Dot art background of its own."""
import math
import os
import random

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from . import theme as T

KINDS = (("none", "None"), ("dots", "Dots"), ("cloud", "Constellation"), ("code", "Binary city"))
ART_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Better Comfy", "Dot art")     # pictures for Dot art, if any
_GAP = 34                               # between the dots
_REACH = 130                            # how far the cursor is felt
DENSITY = 0.1                           # dots per pixel where a picture is at its brightest
_LAYERS = 8                             # groups of dots that move separately
_DRIFT = 2                              # how many pixels they wander
_LINE = 0.075                           # up to this brightness a thin stroke counts as an outline
_COLOURED = 0.34                        # from this saturation on, a place in the picture counts as having colour
_CITY_TOP = 230                         # pixels from the top of the window that the city leaves free
_ART_REACH = 120                        # how far the cursor lights up the dot art
_SPEED = {"dots": 33, "cloud": 70, "code": 110, "art": 70}


# ------------------------------------------------------------------------------------------------ dot art
def art_pictures():
    """[(name, path)] of the pictures kept for Dot art."""
    try:
        names = sorted(n for n in os.listdir(ART_DIR) if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    except OSError:
        return []
    return [(os.path.splitext(n)[0], os.path.join(ART_DIR, n)) for n in names]


def _window_sum(mask, size):
    """How many set pixels each pixel has in the square around it, and how big that square is there."""
    h, w = mask.shape
    r = size // 2
    table = np.pad(mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    y0, y1 = np.clip(np.arange(h) - r, 0, h), np.clip(np.arange(h) + r + 1, 0, h)
    x0, x1 = np.clip(np.arange(w) - r, 0, w), np.clip(np.arange(w) + r + 1, 0, w)
    total = table[y1][:, x1] - table[y0][:, x1] - table[y1][:, x0] + table[y0][:, x0]
    return total, (y1 - y0)[:, None] * (x1 - x0)


def _broad_dark(shade):
    """Near-black places that are areas (dark clothes) and not lines (outlines): only the lines stay fully black."""
    dark = (shade > 0.012) & (shade <= _LINE)
    total, area = _window_sum(dark, 9)
    return _window_sum(total == area, 9)[0] > 0


def stipple(img, height, invert=False, detail=1.0):
    """The dots on a canvas of that height, as (xs, ys, colours, widths): bright places get bigger dots, and as many in a place as it is bright (or dark, for a light
    theme). Colours: 0 for a plain dot, else the softened 0xRRGGBB where the picture is red; blue goes dark. detail: below 1
    for a small picture, which has less to show and gets fewer dots."""
    view = img.convertToFormat(QImage.Format.Format_Grayscale8)
    colour = img.convertToFormat(QImage.Format.Format_RGB888)       # stays alive while the array below reads from it
    rows = np.frombuffer(colour.constBits(), np.uint8).reshape(colour.height(), colour.bytesPerLine())
    rgb = rows[:, :colour.width() * 3].reshape(view.height(), view.width(), 3).astype(np.float32)
    lum = np.frombuffer(view.constBits(), np.uint8).reshape(view.height(), view.bytesPerLine())[:, :view.width()] / 255
    lo, hi = np.percentile(lum, (2, 98))        # any picture uses the whole range, from no dots to many
    lum = np.clip((lum - lo) / max(hi - lo, 0.01), 0, 1)
    scale = height / view.height()
    rng = np.random.default_rng(7)
    # one try per small cell of an even grid, somewhere inside the cell: no clumps, so lines stay clean
    step = 1 / (scale * math.sqrt(DENSITY * detail))
    gx, gy = np.meshgrid(np.arange(0, view.width(), step), np.arange(0, view.height(), step))
    gx, gy = gx.ravel(), gy.ravel()
    n = gx.size
    # (only a little off the grid: calm rather than grainy)
    x = np.clip(gx + (0.3 + rng.random(n) * 0.4) * step, 0, view.width() - 1)
    y = np.clip(gy + (0.3 + rng.random(n) * 0.4) * step, 0, view.height() - 1)
    v = lum[y.astype(int), x.astype(int)]
    if invert:
        v = np.where(v > 0.01, 1 - v, 0)
    # how much colour each place has; coloured places keep their dots even when they are darkish
    c = rgb[y.astype(int), x.astype(int)]
    top = c.max(axis=1)
    sat = np.where(top > 40, (top - c.min(axis=1)) / np.maximum(top, 1), 0)
    red = (sat > _COLOURED) & (c[:, 0] > c[:, 1] * 1.35) & (c[:, 0] > c[:, 2] * 1.25)
    # tones show as shades of grey as well as by how many dots there are: skin and dark clothes stay apart, and only
    # the outlines and the background are really black
    shade = c.mean(axis=1) / 255
    if not invert:
        v = np.where(shade > _LINE, 0.34 + 0.66 * shade ** 0.9, 0)
        broad = _broad_dark(rgb.mean(axis=2) / 255)[y.astype(int), x.astype(int)]
        v = np.where(broad, 0.3, v)     # a dark grey instead of a black hole
        shade = np.where(broad, 0.13, shade)
    v = np.maximum(v, np.where(red, 0.62, 0))
    keep = v ** 0.8 > rng.random(n)
    # softened: a little towards grey and never too dark to see; in steps, so few pens are needed
    soft = c[keep] * 0.78 + c[keep].mean(axis=1, keepdims=True) * 0.22
    soft = np.clip(soft * (190 / np.maximum(soft.max(axis=1, keepdims=True), 1)), 0, 255).astype(np.uint32) & 0xE0
    tint = (soft[:, 0] << 16) | (soft[:, 1] << 8) | soft[:, 2]
    grey = 0
    if not invert:
        level = (70 + 185 * shade[keep] ** 0.75).astype(np.uint32) & 0xF0 | 0x0F
        grey = level * 0x10101
    widths = np.round((1.5 + 2.2 * v[keep]) * 2) / 2        # in half pixels, so few pens are needed
    return x[keep] * scale, y[keep] * scale, np.where(red[keep], tint, grey), widths


# ------------------------------------------------------------------------------------------------ the widget
class Backdrop(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.kind = "none"
        self.art_file = ""
        self.mouse = QPointF(-999, -999)
        self.points = []
        self.base = None                # what doesn't change: drawn once, then only copied
        self.art_layers = []            # dot art: the dots in groups that drift separately
        self.art_xs = self.art_ys = self.art_red = None
        self.art_origin = QPoint(0, 0)
        self.art_t = 0.0
        self.city = []
        self.falling = []
        self.glyphs = {}
        self.stars = []
        self._art_box = self._art_mask = self._near = None
        self.timer = QTimer(self, timeout=self._tick)
        self.resized = QTimer(self, singleShot=True, interval=220, timeout=self.rebuild)    # once, when the resizing stops
        QApplication.instance().applicationStateChanged.connect(lambda _s: self._run())

    def set_kind(self, kind):
        """kind: one of KINDS, or "art:<name>" for a kept picture."""
        self.art_file = ""
        if kind.startswith("art"):
            kept = dict(art_pictures())
            self.art_file = kept.get(kind[4:]) or next(iter(kept.values()), "")
            kind = "art" if self.art_file else "none"
        self.kind = kind
        self.points = []
        self.rebuild()
        self._run()
        self.update()

    def rebuild(self):
        """After a change of size or look."""
        self.base = None
        self.city = []
        self.falling = []
        self.art_layers = []
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.start()

    def _run(self):
        from .widgets import anims_on
        on = self.kind != "none" and anims_on() and QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        self.timer.stop()
        if on:
            self.timer.start(_SPEED.get(self.kind, 80))

    def _tick(self):
        if not self.window().isVisible():
            return
        m = QPointF(self.mapFromGlobal(QCursor.pos()))
        moved = (m - self.mouse).manhattanLength() > 1
        old, self.mouse = self.mouse, m
        if self.kind == "dots":
            if moved:                   # only around the cursor: the rest of the app is left alone
                r = _REACH + 8
                for c in (old, m):
                    self.update(QRect(int(c.x() - r), int(c.y() - r), 2 * r, 2 * r))
        elif self.kind == "cloud":
            self._drift()
            self._render_cloud()
            self.update()
        elif self.kind == "code":
            self._city_tick()
        if self.kind == "art":
            self._stars_tick()
            self._art_tick()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), T.col(T.BG))       # the window's own background: follows light and dark at once
        if self.kind == "none":
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.kind == "dots":
            self._paint_dots(p, e.rect())
        elif self.kind == "cloud":
            self._paint_cloud(p)
        elif self.kind == "code":
            self._paint_city(p)
        else:
            self._paint_art(p)
        p.end()

    # ---- dots
    def _build_dots(self):
        """All the dots at rest, drawn once: a page that scrolls over them only has to copy this."""
        w, h = self.width(), self.height()
        self.base = QPixmap(max(1, w), max(1, h))
        self.base.fill(Qt.GlobalColor.transparent)
        c = QColor(T.col(T.BORDER_HI))
        c.setAlphaF(0.55)
        grid = QPolygonF()
        for x in range(0, w + _GAP, _GAP):
            for y in range(0, h + _GAP, _GAP):
                grid.append(QPointF(x, y))
        q = QPainter(self.base)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(QPen(c, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        q.drawPoints(grid)
        q.end()

    def _paint_dots(self, p, area):
        if self.base is None:
            self._build_dots()
        m = self.mouse
        near = QRect(int(m.x() - _REACH), int(m.y() - _REACH), 2 * _REACH, 2 * _REACH)
        p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(near, QRegion.RegionType.Ellipse)))
        p.drawPixmap(0, 0, self.base)
        p.setClipping(False)
        if not near.intersects(area):
            return
        base = T.col(T.BORDER_HI)
        accent = T.accent()
        p.setPen(Qt.PenStyle.NoPen)
        for x in range(max(0, near.left() - near.left() % _GAP), near.right() + _GAP, _GAP):
            for y in range(max(0, near.top() - near.top() % _GAP), near.bottom() + _GAP, _GAP):
                dx, dy = x - m.x(), y - m.y()
                d = math.hypot(dx, dy)
                if d >= _REACH:
                    continue
                k = 1 - d / _REACH
                c = T.mix(base, accent, k)
                c.setAlphaF(0.55 + 0.45 * k)
                p.setBrush(c)
                # the dots lean a little away from the cursor
                off = 6 * k / max(d, 1)
                r = 1.2 + 1.3 * k
                p.drawEllipse(QPointF(x + dx * off, y + dy * off), r, r)

    # ---- constellation
    def _drift(self):
        w, h = max(1, self.width()), max(1, self.height())
        if not self.points:
            r = random.Random()
            self.points = [[r.uniform(0, w), r.uniform(0, h), r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6)]
                           for _ in range(max(70, w * h // 11000))]
        for pt in self.points:
            dx, dy = pt[0] - self.mouse.x(), pt[1] - self.mouse.y()
            d = math.hypot(dx, dy)
            if 1 < d < _REACH:          # moves aside a little
                pt[2] += dx / d * 0.06
                pt[3] += dy / d * 0.06
            if math.hypot(pt[2], pt[3]) > 0.8:
                pt[2], pt[3] = pt[2] * 0.97, pt[3] * 0.97
            pt[0] = (pt[0] + pt[2]) % w
            pt[1] = (pt[1] + pt[3]) % h

    def _render_cloud(self):
        """One frame into a picture; a page scrolling over it only copies that."""
        w, h = max(1, self.width()), max(1, self.height())
        if self.base is None or self.base.width() != w or self.base.height() != h:
            self.base = QPixmap(w, h)
        self.base.fill(Qt.GlobalColor.transparent)
        q = QPainter(self.base)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = QColor(T.col(T.TEXT3))
        for i, a in enumerate(self.points):
            for b in self.points[i + 1:]:
                d = abs(a[0] - b[0]) + abs(a[1] - b[1])
                if d < 150:
                    line.setAlphaF(0.42 * (1 - d / 150))
                    q.setPen(QPen(line, 1))
                    q.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(T.col(T.TEXT2))
        for a in self.points:
            q.drawEllipse(QPointF(a[0], a[1]), 1.8, 1.8)
        q.end()

    def _paint_cloud(self, p):
        if self.base is not None:
            p.drawPixmap(0, 0, self.base)

    # ---- binary city
    def _glyph(self, ch, alpha):
        key = (ch, alpha, T.MODE)
        if key not in self.glyphs:
            pm = QPixmap(13, 18)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            f = QFont("Cascadia Mono")
            f.setStyleHint(QFont.StyleHint.Monospace)
            f.setPixelSize(13)
            q.setFont(f)
            c = QColor(T.col(T.TEXT))
            c.setAlphaF(alpha / 10)
            q.setPen(c)
            q.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, ch)
            q.end()
            self.glyphs[key] = pm
        return self.glyphs[key]

    def _build_city(self):
        """Towers of 0 and 1 seen from a corner, each one dissolving at the bottom."""
        w, h = self.width(), self.height()
        rng = random.Random(11)
        self.base = QPixmap(max(1, w), max(1, h))
        self.base.fill(Qt.GlobalColor.transparent)
        self.city = []
        cw, ch = 13, 18
        cols = w // cw + 1
        rows = h // ch + 1
        sky = _CITY_TOP // ch                   # nothing above this row: the top bar and page titles stay clear
        q = QPainter(self.base)
        c = -2
        while c < cols:
            bw = rng.randint(12, 22)
            mid = c + bw / 2
            roof = rng.uniform(0.30, 0.56) * rows
            spire = rng.random() < 0.6
            for col in range(max(0, c), min(cols, c + bw)):
                off = abs(col - mid)
                top = roof + off * 0.55             # the roof falls away on both sides of the corner
                if spire and off < 2.4:
                    top -= rows * 0.09
                top = max(top, sky)
                # towers stand on a line that dips at each corner
                bottom = rows * 0.97 - off * 0.8
                span = max(1.0, bottom - top)
                for row in range(max(0, int(top)), min(rows, int(bottom) + 1)):
                    d = (row - top) / span
                    if d > 0.58 and rng.random() < (d - 0.58) / 0.42 * 1.25:     # thinner and thinner towards the ground
                        continue
                    alpha = int(round(max(2.0, 8.0 - 5.0 * d)))
                    digit = rng.choice("01")
                    x, y = col * cw, row * ch
                    q.drawPixmap(x, y, self._glyph(digit, alpha))
                    self.city.append((x, y, alpha))
            c += bw + rng.randint(0, 2)             # towers side by side, never through each other
        q.end()

    def _city_tick(self):
        if self.base is None:
            self._build_city()
            self.update()
            return
        if not self.city:
            return
        q = QPainter(self.base)
        for _ in range(26):             # a few digits change all the time
            x, y, alpha = random.choice(self.city)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            q.fillRect(QRect(x, y, 13, 18), Qt.GlobalColor.transparent)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            q.drawPixmap(x, y, self._glyph(random.choice("01"), alpha))
            self.update(QRect(x, y, 13, 18))
        q.end()
        # and some come loose and fall
        if len(self.falling) < 40 and random.random() < 0.7:
            x, y, alpha = random.choice(self.city)
            self.falling.append([x, y, random.choice("01"), random.uniform(2.5, 6.5)])
        for f in list(self.falling):
            self.update(QRect(f[0], int(f[1]), 13, 18))
            f[1] += f[3]
            self.update(QRect(f[0], int(f[1]), 13, 18))
            if f[1] > self.height():
                self.falling.remove(f)

    def _paint_city(self, p):
        if self.base is None:
            self._build_city()
        p.drawPixmap(0, 0, self.base)
        for f in self.falling:
            p.drawPixmap(f[0], int(f[1]), self._glyph(f[2], 4))

    # ---- the stars behind the dot art
    def _stars_tick(self):
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            return
        if not self.stars:
            r = random.Random()
            self.stars = [[r.uniform(0, w), r.uniform(0, h), r.uniform(-0.55, 0.55), r.uniform(-0.75, -0.08),
                           r.uniform(0, 6.28), r.uniform(0.7, 1.9), r.random() < 0.16] for _ in range(max(70, w * h // 9000))]
        for s in self.stars:
            self.update(QRect(int(s[0]) - 4, int(s[1]) - 4, 9, 9))
            s[0] = (s[0] + s[2] * 1.75) % w
            s[1] = (s[1] + s[3] * 1.75) % h
            s[4] += 0.12
            self.update(QRect(int(s[0]) - 4, int(s[1]) - 4, 9, 9))

    def _behind_art(self, x, y):
        """Is this spot part of the figure? (The stars pass behind it.)"""
        box = self._art_box
        if box is None or self._art_mask is None or not box.contains(QPointF(x, y)):
            return False
        return (self._art_mask.pixel(int((x - box.x()) / 8), int((y - box.y()) / 8)) & 0xFF) > 6

    def _paint_stars(self, p):
        white, red = T.col(T.TEXT), QColor(226, 88, 88)
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _vx, _vy, phase, size, is_red in self.stars:
            if self._behind_art(x, y):
                continue
            c = QColor(red if is_red else white)
            c.setAlphaF(0.28 + 0.42 * (0.5 + 0.5 * math.sin(phase)))    # they twinkle
            p.setBrush(c)
            p.drawEllipse(QPointF(x, y), size, size)

    # ---- dot art
    def _build_art(self):
        """The dots, sorted onto a few see-through layers. Each layer drifts and breathes on its own, which is cheap: only
        whole layers move, and a page scrolling over them only copies the finished picture."""
        w, h = self.width(), self.height()
        self.base = QPixmap(1, 1)
        self.base.fill(Qt.GlobalColor.transparent)
        self.art_layers = []
        self.art_xs = np.empty(0)
        self.dot_col = QColor(T.col(T.TEXT if T.MODE == "dark" else T.TEXT2))
        img = QImage(self.art_file)
        if img.isNull():
            return
        detail = min(1.0, img.height() / 800) ** 0.55
        img = img.scaledToHeight(900, Qt.TransformationMode.SmoothTransformation)
        size = max(1, int(h * 0.97))
        xs, ys, tints, widths = stipple(img, size, T.MODE == "light", detail)
        art_w = size * img.width() / img.height()
        ox, oy = max(0, min(w - art_w, w * 0.64 - art_w / 2)), h - size
        bw, bh = int(art_w) + 2 * _DRIFT + 2, size + 2 * _DRIFT + 2
        rng = np.random.default_rng(3)
        layer = rng.integers(0, _LAYERS, xs.size)
        for n in range(_LAYERS):
            pm = QPixmap(bw, bh)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            mine = layer == n
            for tint in np.unique(tints[mine]).tolist():
                same = mine & (tints == tint)
                for width in np.unique(widths[same]).tolist():
                    poly = QPolygonF()
                    for x, y in zip((xs[same & (widths == width)] + _DRIFT).tolist(), (ys[same & (widths == width)] + _DRIFT).tolist()):
                        poly.append(QPointF(x, y))
                    q.setPen(QPen(QColor(tint | 0xFF000000) if tint else self.dot_col, width, Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap))
                    q.drawPoints(poly)
            q.end()
            self.art_layers.append(pm)
        self.art_xs, self.art_ys, self.art_red = xs + ox, ys + oy, tints != 0
        if T.MODE == "dark":                    # (greys are colours too here: only real red counts for the glow)
            self.art_red = np.array([(t >> 16) - (t & 0xFF) > 40 for t in tints.tolist()], bool)
        self.base = QPixmap(bw, bh)
        self.art_origin = QPoint(int(ox) - _DRIFT, int(oy) - _DRIFT)
        self._art_box = QRectF(ox, oy, art_w, size)
        self._art_mask = img.scaled(int(art_w) // 8 + 1, size // 8 + 1).convertToFormat(QImage.Format.Format_Grayscale8)
        self._near = None
        self._art_frame()

    def _art_frame(self):
        """This moment of the picture: every layer a pixel or two off its place, a little brighter or dimmer."""
        if not self.art_layers:
            return
        self.base.fill(Qt.GlobalColor.transparent)
        t = self.art_t
        q = QPainter(self.base)
        for n, pm in enumerate(self.art_layers):
            phase = n * 2.399                   # spread evenly, so they never move together
            q.setOpacity(0.84 + 0.16 * math.sin(t * 1.15 + phase * 1.7))
            q.drawPixmap(round(_DRIFT * 0.5 * (math.sin(t * 0.8 + phase) + math.sin(t * 0.37 + phase * 2.3))),
                         round(_DRIFT * 0.5 * (math.cos(t * 0.65 + phase * 1.3) + math.sin(t * 0.29 + phase * 0.7))), pm)
        q.end()

    def _art_tick(self):
        if self.base is None or not self.art_layers:
            return
        self.art_t += _SPEED["art"] / 1000
        self._art_frame()
        self.update(QRect(self.art_origin, self.base.size()))

    def _paint_art(self, p):
        if self.base is None:
            self._build_art()
        self._paint_stars(p)
        if not self.art_layers:
            return
        p.drawPixmap(self.art_origin, self.base)
        # near the cursor the dots simply light up, softly, more the closer they are
        m = self.mouse
        if self._near is None or self._near[0] != (m.x(), m.y()):
            d = np.hypot(self.art_xs - m.x(), self.art_ys - m.y())
            k = 1 - d / _ART_REACH
            bands = []
            for lo, hi, alpha, width in ((0.0, 0.33, 0.16, 3.0), (0.33, 0.66, 0.3, 3.4), (0.66, 1.01, 0.46, 3.8)):
                pick = (k > lo) & (k <= hi)
                for red in (False, True):
                    poly = QPolygonF()
                    for x, y in zip(self.art_xs[pick & (self.art_red == red)].tolist(), self.art_ys[pick & (self.art_red == red)].tolist()):
                        poly.append(QPointF(x, y))
                    bands.append((poly, alpha, width, red))
            self._near = ((m.x(), m.y()), bands)
        for poly, alpha, width, red in self._near[1]:
            c = QColor(232, 76, 76) if red else QColor(T.col(T.TEXT))
            c.setAlphaF(alpha)
            p.setPen(QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPoints(poly)
