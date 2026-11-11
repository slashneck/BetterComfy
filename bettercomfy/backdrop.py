"""The moving backgrounds (Settings, Look & feel): behind everything, only drawn while the app is in front. Under another
program, or a game, they stop completely. No pictures come with them: each picture in the folder Pictures\\Better
Comfy\\Dot art is offered as a Dot art background of its own."""
import math
import os
import random
import threading

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from . import theme as T

KINDS = (("none", "None"), ("dots", "Dots"), ("cloud", "Constellation"), ("code", "Binary city"))
ART_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Better Comfy", "Dot art")     # pictures for Dot art, if any
_GAP = 34                               # between the dots
_REACH = 130                            # how far the cursor is felt
DENSITY = 0.42                          # particles per pixel of the picture
_LAYERS = 8                             # groups of particles that move separately (and 2 more for the loose ones)
_DRIFT = 2                              # how many pixels they wander
_DRIFT_LOOSE = 7                        # the loose ones around the edges wander further
_PAD = 40                               # room around the picture for the haze
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


def particles(img, height, light=False, detail=1.0):
    """The picture as particles: (xs, ys, colours 0..1, energies 0..1, scattered). Each takes its colour from the picture and a
    brightness of its own; only outlines and the background stay empty, and some drift off the edges as haze."""
    colour = img.convertToFormat(QImage.Format.Format_RGB888)       # stays alive while the array below reads from it
    rows = np.frombuffer(colour.constBits(), np.uint8).reshape(colour.height(), colour.bytesPerLine())
    rgb = rows[:, :colour.width() * 3].reshape(colour.height(), colour.width(), 3).astype(np.float32) / 255
    h, w = rgb.shape[:2]
    full = rgb.mean(axis=2)
    scale = height / h
    rng = np.random.default_rng(7)
    step = 1 / (scale * math.sqrt(DENSITY * detail))
    gx, gy = np.meshgrid(np.arange(0, w, step), np.arange(0, h, step))
    gx, gy = gx.ravel(), gy.ravel()
    n = gx.size
    x = np.clip(gx + rng.random(n) * step, 0, w - 1)
    y = np.clip(gy + rng.random(n) * step, 0, h - 1)
    ix, iy = x.astype(int), y.astype(int)
    c = rgb[iy, ix]
    shade = full[iy, ix]
    figure = shade > 0.012
    broad = _broad_dark(full)[iy, ix]
    outline = figure & (shade <= _LINE) & ~broad
    keep = figure & ~(outline & (rng.random(n) > 0.06))
    x, y, c, shade, broad = x[keep], y[keep], c[keep], shade[keep], broad[keep]
    n = x.size
    c = c * 0.86 + c.mean(axis=1, keepdims=True) * 0.14      # the picture's own colours, a touch softer
    # shadows lifted so that dark clothes stay readable
    tone = np.where(broad, 0.3, shade ** 0.72)
    c = np.clip(c * (tone / np.maximum(shade, 0.05))[:, None], 0, 1)
    # brightening a shadow would blow up its tint: the darker it was, the closer to grey it comes out
    keep_colour = np.clip((shade - 0.08) / 0.3, 0, 1)[:, None]
    c = c * keep_colour + c.mean(axis=1, keepdims=True) * (1 - keep_colour)
    sparkle = 0.28 + 0.72 * rng.random(n) ** 1.4
    energy = sparkle * (0.5 + 0.5 * tone)
    if light:
        c, energy = c * 0.45, sparkle * (0.2 + 0.8 * (1 - tone))
    # the haze: particles near an edge of the figure come loose
    mask = full > 0.012
    total, area = _window_sum(mask, 45)
    inside = (total / area)[y.astype(int), x.astype(int)]
    loose = (inside < 0.82) & (rng.random(n) < 0.34)
    spread = 7 + 20 * rng.random(n) ** 2
    x = np.where(loose, x + rng.normal(0, 1, n) * spread / scale, x)
    y = np.where(loose, y + rng.normal(0, 1, n) * spread / scale, y)
    energy = np.where(loose, energy * 0.55, energy)
    return x * scale, y * scale, c, energy, loose


def splat(xs, ys, colours, energies, w, h, light):
    """Soft points on a see-through picture: each shared between the four pixels around it, added up like light."""
    fx, fy = xs - np.floor(xs), ys - np.floor(ys)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    sum_rgb, sum_a = np.zeros((h, w, 3), np.float32), np.zeros((h, w), np.float32)
    for dx, dy, part in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        px, py = x0 + dx, y0 + dy
        ok = (px >= 0) & (px < w) & (py >= 0) & (py < h)
        weight = (energies * part)[ok]
        np.add.at(sum_a, (py[ok], px[ok]), weight)
        np.add.at(sum_rgb, (py[ok], px[ok]), colours[ok] * weight[:, None])
    gain = 2.7
    rgb = np.clip(sum_rgb * gain, 0, 1)
    alpha = np.clip(sum_a * gain, 0, 1) if light else rgb.max(axis=2)
    if light:
        rgb = np.minimum(rgb, alpha[:, :, None])
    out = np.dstack((rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], alpha))
    return QImage((out * 255).astype(np.uint8).tobytes(), w, h, 4 * w, QImage.Format.Format_ARGB32_Premultiplied).copy()


def compute_art(path, w, h, light):
    """Everything that takes a moment, away from the window: the finished layers as images."""
    img = QImage(path)
    if img.isNull():
        return None
    detail = min(1.0, img.height() / 800) ** 0.55
    img = img.scaledToHeight(900, Qt.TransformationMode.SmoothTransformation)
    # as big as fits: standing on the bottom edge of the window, and never wider than it
    size = max(1, int(min(h * 0.97, w * 0.96 * img.height() / img.width())))
    xs, ys, colours, energies, loose = particles(img, size, light, detail)
    art_w = size * img.width() / img.height()
    ox, oy = max(0, min(w - art_w, w * 0.64 - art_w / 2)), h - size
    bw, bh = int(art_w) + 2 * _PAD, size + 2 * _PAD
    rng = np.random.default_rng(3)
    # most particles on layers that hardly move; the loose ones on their own, which wander further
    layer = np.where(loose, _LAYERS + rng.integers(0, 2, xs.size), rng.integers(0, _LAYERS, xs.size))
    images = []
    for n in range(_LAYERS + 2):
        pick = layer == n
        images.append(splat(xs[pick] + _PAD, ys[pick] + _PAD, colours[pick], energies[pick], bw, bh, light))
    mask = img.scaled(int(art_w) // 8 + 1, size // 8 + 1).convertToFormat(QImage.Format.Format_Grayscale8)
    return {"images": images, "size": (bw, bh), "origin": (int(ox) - _PAD, int(oy) - _PAD), "box": (ox, oy, art_w, size),
            "mask": mask, "count": int(xs.size)}


# ------------------------------------------------------------------------------------------------ the widget
class Backdrop(QWidget):
    _art_done = Signal(int, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.kind = "none"
        self.art_file = ""
        self.mouse = QPointF(-999, -999)
        self.points = []
        self.base = None                # what doesn't change: drawn once, then only copied
        self.art_layers = []            # dot art: the dots in groups that drift separately
        self.art_count = 0
        self.art_gen = 0                # which build of the picture is wanted; older ones arriving late are dropped
        self.art_busy = -1
        self.art_ready = -1
        self._art_done.connect(self._use_art)
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
        self.base = None
        self.art_layers = []
        self.rebuild()
        self._run()
        self.update()

    def rebuild(self):
        """After a change of size or look."""
        if self.kind != "art":
            self.base = None
        self.city = []
        self.falling = []
        self.art_gen += 1                       # (the dot art stays up until its new version is ready)
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
        """All at once (for tests); the window itself uses _start_art."""
        self._use_art(self.art_gen, compute_art(self.art_file, self.width(), self.height(), T.MODE == "light"))

    def _start_art(self):
        """The particles are worked out beside the window, which keeps responding meanwhile."""
        if self.art_busy == self.art_gen:
            return
        self.art_busy = self.art_gen
        job = (self.art_gen, self.art_file, self.width(), self.height(), T.MODE == "light")

        def work():
            try:
                res = compute_art(*job[1:])
            except Exception:
                res = None
            try:
                self._art_done.emit(job[0], res)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _use_art(self, gen, res):
        if gen != self.art_gen:
            return
        self.art_ready = gen
        self.base = QPixmap(1, 1)
        self.base.fill(Qt.GlobalColor.transparent)
        self.art_layers = []
        self.art_count = 0
        if res is None:
            self.update()
            return
        self.art_layers = [QPixmap.fromImage(i) for i in res["images"]]
        self.art_count = res["count"]
        self.base = QPixmap(*res["size"])
        self.art_origin = QPoint(*res["origin"])
        self._art_box = QRectF(*res["box"])
        self._art_mask = res["mask"]
        self._art_frame()
        self.update()

    def _art_frame(self):
        """This moment of the picture: every layer a little off its place, a little brighter or dimmer."""
        if not self.art_layers or self.base is None:
            return
        self.base.fill(Qt.GlobalColor.transparent)
        t = self.art_t
        q = QPainter(self.base)
        if T.MODE == "dark":
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for n, pm in enumerate(self.art_layers):
            phase = n * 2.399                   # spread evenly, so they never move together
            reach = _DRIFT_LOOSE if n >= _LAYERS else _DRIFT
            q.setOpacity(0.86 + 0.14 * math.sin(t * 1.15 + phase * 1.7))
            q.drawPixmap(round(reach * 0.5 * (math.sin(t * 0.8 + phase) + math.sin(t * 0.37 + phase * 2.3))),
                         round(reach * 0.5 * (math.cos(t * 0.65 + phase * 1.3) + math.sin(t * 0.29 + phase * 0.7))), pm)
        q.end()

    def _art_tick(self):
        if self.base is None or not self.art_layers:
            return
        self.art_t += _SPEED["art"] / 1000
        self._art_frame()
        self.update(QRect(self.art_origin, self.base.size()))

    def _paint_art(self, p):
        if self.art_ready != self.art_gen:
            self._start_art()
        self._paint_stars(p)
        if not self.art_layers or self.base is None:
            return
        p.drawPixmap(self.art_origin, self.base)
        # near the cursor the particles simply light up, softly, more the closer they are
        m = self.mouse
        at = QPoint(int(m.x()), int(m.y())) - self.art_origin
        r = _ART_REACH
        spot = QRect(at.x() - r, at.y() - r, 2 * r, 2 * r).intersected(self.base.rect())
        if spot.isEmpty():
            return
        glow = self.base.copy(spot)
        q = QPainter(glow)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        fall = QRadialGradient(QPointF(at - spot.topLeft()), r)
        fall.setColorAt(0.0, QColor(0, 0, 0, 200))
        fall.setColorAt(1.0, QColor(0, 0, 0, 0))
        q.fillRect(glow.rect(), fall)
        q.end()
        if T.MODE == "dark":
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.drawPixmap(spot.topLeft() + self.art_origin, glow)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
