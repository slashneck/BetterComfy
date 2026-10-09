"""Tools that work on a picture: the mask brush (redraw a part), the upscaler, the settings comparison."""
import io
import math
import os
import time

import numpy as np
from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from . import theme as T, workflows as W
from .components import PromptEdit
from .config import BASE
from .widgets import (Card, ChipBox, Combo, Segmented, Slider, button, chip, field, hrow, label, nice_name)


# ================================================================================================ mask brush

class MaskCanvas(QWidget):
    """The picture with the mask painted over it. Left mouse paints, right mouse erases, the wheel: brush size."""
    changed = Signal()
    size_changed = Signal(int)

    def __init__(self, path):
        # path: a file, or a 'vault:' / 'mem:' picture that is only decrypted into memory
        super().__init__()
        if str(path).startswith(("vault:", "mem:")):
            from .jobs import ref_bytes
            src = QImage()
            src.loadFromData(ref_bytes(path))
        else:
            src = QImage(path)
        self.img = src.convertToFormat(QImage.Format.Format_RGB32)
        self.mask = QImage(self.img.size(), QImage.Format.Format_Grayscale8)
        self.mask.fill(0)
        self.overlay = QImage(self.img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self.overlay.fill(Qt.GlobalColor.transparent)
        self.brush = max(16, int(min(self.img.width(), self.img.height()) * 0.06))
        self.erase = False
        self.undo_stack = []
        self.last = None
        self.cur = None
        self._erasing = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setMinimumSize(480, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- geometry
    def _fit(self):
        r = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        s = min(r.width() / max(1, self.img.width()), r.height() / max(1, self.img.height()))
        w, h = self.img.width() * s, self.img.height() * s
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h), s

    def _to_img(self, pos):
        rr, s = self._fit()
        return QPointF((pos.x() - rr.x()) / s, (pos.y() - rr.y()) / s)

    # ---- painting the mask
    def _push(self):
        self.undo_stack.append((self.mask.copy(), self.overlay.copy()))
        self.undo_stack = self.undo_stack[-30:]

    def undo(self):
        if self.undo_stack:
            self.mask, self.overlay = self.undo_stack.pop()
            self.update()
            self.changed.emit()

    def clear(self):
        self._push()
        self.mask.fill(0)
        self.overlay.fill(Qt.GlobalColor.transparent)
        self.update()
        self.changed.emit()

    def invert(self):
        self._push()
        self.mask.invertPixels()
        self._rebuild_overlay()
        self.update()
        self.changed.emit()

    def _rebuild_overlay(self):
        ov = QImage(self.img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        ov.fill(T.accent())
        ov.setAlphaChannel(self.mask)
        self.overlay = ov

    def _stroke(self, a, b, erase):
        for target in (self.mask, self.overlay):
            p = QPainter(target)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if target is self.mask:
                col = QColor(0, 0, 0) if erase else QColor(255, 255, 255)
            else:
                col = T.accent()
                if erase:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(col, self.brush, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            if a == b:
                p.drawPoint(a)
            else:
                p.drawLine(a, b)
            p.end()
        self.update()

    def mousePressEvent(self, e):
        self._push()
        self._erasing = self.erase or e.button() == Qt.MouseButton.RightButton
        pt = self._to_img(e.position())
        self.last = pt
        self._stroke(pt, pt, self._erasing)

    def mouseMoveEvent(self, e):
        self.cur = e.position()
        if self.last is not None and e.buttons():
            pt = self._to_img(e.position())
            self._stroke(self.last, pt, self._erasing)
            self.last = pt
        self.update()

    def mouseReleaseEvent(self, e):
        self.last = None
        self.changed.emit()

    def leaveEvent(self, e):
        self.cur = None
        self.update()

    def wheelEvent(self, e):
        f = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12
        self.set_brush(int(self.brush * f))

    def set_brush(self, v):
        self.brush = int(max(3, min(600, v)))
        self.size_changed.emit(self.brush)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(T.BG))
        rr, s = self._fit()
        p.drawImage(rr, self.img)
        p.setOpacity(0.55)
        p.drawImage(rr, self.overlay)
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRect(rr)
        if self.cur is not None:
            r = self.brush * s / 2
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 160), 3))
            p.drawEllipse(self.cur, r, r)
            p.setPen(QPen(QColor("#FFFFFF") if not (self.erase or self._erasing) else QColor(T.BAD), 1.4))
            p.drawEllipse(self.cur, r, r)
        p.end()

    # ---- result
    def has_mask(self):
        return bool(self._mask_array().any())

    def _mask_array(self):
        m = self.mask
        bpl = m.bytesPerLine()
        a = np.frombuffer(m.constBits(), np.uint8, count=bpl * m.height()).reshape(m.height(), bpl)
        return a[:, :m.width()].copy()

    def _mask_image(self, grow, feather):
        im = Image.fromarray(self._mask_array(), "L")
        g = int(grow)
        while g > 0:
            k = min(g, 12)
            im = im.filter(ImageFilter.MaxFilter(k * 2 + 1))
            g -= k
        if feather > 0:
            im = im.filter(ImageFilter.GaussianBlur(feather / 2))
        return im.convert("RGB")

    def save_mask(self, path, grow=8, feather=12):
        """The mask as a picture: grown a little (so edges are redrawn too) and softened (no hard seam)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._mask_image(grow, feather).save(path)
        return path

    def mask_bytes(self, grow=8, feather=12):
        """The same mask, kept in memory (for a private picture)."""
        buf = io.BytesIO()
        self._mask_image(grow, feather).save(buf, "PNG")
        return buf.getvalue()


def _title(entry):
    if entry.get("vault") or str(entry.get("file", "")).startswith("vault:"):
        return entry.get("name") or "Private picture"
    return nice_name(entry["file"])


class MaskEditor(QDialog):
    """Paint over what should change, describe it, redraw only that part."""

    def __init__(self, parent, entry):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle("Edit a part - " + _title(entry))
        self.resize(1320, 880)
        self.result_params = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.canvas = MaskCanvas(entry["file"])
        h.addWidget(self.canvas, 1)
        side = QWidget()
        side.setObjectName("Panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(340)
        v = QVBoxLayout(side)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)
        v.addWidget(label("Edit a part", "H2"))
        v.addWidget(label("Paint over what should change. Right mouse erases, the wheel sets the brush size.", "Muted",
                          wrap=True))
        self.tool = Segmented([("brush", "Brush", "B"), ("erase", "Eraser", "E")], self._tool, "brush", height=32)
        v.addWidget(self.tool)
        self.size = Slider(3, 600, 1, 0, self.canvas.brush, lambda x: self.canvas.set_brush(int(x)), " px", width=76)
        self.canvas.size_changed.connect(self.size.set)
        v.addWidget(field("Brush", self.size, "[ and ] change it too", label_w=58))
        v.addWidget(hrow(button("Undo", self.canvas.undo, "Ghost", "back", "Ctrl+Z"),
                         button("Invert", self.canvas.invert, "Ghost", "swap", "Redraw everything except the painted part"),
                         button("Clear", self.canvas.clear, "Ghost", "close"), None, spacing=2))
        c = Card("Edges", margins=(14, 12, 14, 14))
        self.grow = Slider(0, 64, 1, 0, 8, None, " px", width=70)
        self.feather = Slider(0, 64, 1, 0, 14, None, " px", width=70)
        c.add(field("Grow", self.grow, "Widens the mask so the border of what you painted is redrawn too.", label_w=58),
              field("Soft edge", self.feather, "Blends the new part into the old picture.", label_w=58))
        v.addWidget(c)
        v.addWidget(label("What should be there?", "Muted"))
        self.prompt = PromptEdit("e.g. a red scarf, closed eyes, a cat on the table…", 90)
        self.prompt.setPlainText(entry.get("prompt") or "")
        self.prompt.submit.connect(self._go)
        v.addWidget(self.prompt)
        self.strength = Slider(0.2, 1.0, 0.01, 2, 0.85, None, width=70)
        v.addWidget(field("Change", self.strength, "Low: only touches it up. High: draws it new.", label_w=58))
        self.count = QSpinBox()
        self.count.setRange(1, 16)
        self.count.setPrefix("× ")
        self.count.setFixedWidth(70)
        self.count.setToolTip("How many versions (each with its own seed)")
        v.addStretch(1)
        self.go_btn = button("Redraw the part", self._go, "Accent", "sparkle")
        self.go_btn.setMinimumHeight(40)
        v.addWidget(hrow(self.count, self.go_btn, spacing=8))
        v.addWidget(button("Cancel", self.reject, "Ghost"))
        h.addWidget(side)
        for key, fn in (("[", lambda: self.canvas.set_brush(self.canvas.brush / 1.2)),
                        ("]", lambda: self.canvas.set_brush(self.canvas.brush * 1.2)),
                        ("Ctrl+Z", self.canvas.undo), ("E", lambda: self.tool.set("erase") or self._tool("erase")),
                        ("B", lambda: self.tool.set("brush") or self._tool("brush"))):
            QShortcut(QKeySequence(key), self, activated=fn)

    def _tool(self, t):
        self.canvas.erase = t == "erase"
        self.canvas.update()

    def _go(self):
        if not self.canvas.has_mask():
            self.go_btn.setText("Paint over a part first")
            return
        if str(self.entry["file"]).startswith(("vault:", "mem:")):
            from .jobs import remember_bytes
            path = remember_bytes(self.canvas.mask_bytes(int(self.grow.value()), int(self.feather.value())))
        else:
            path = os.path.join(BASE, "masks", f"mask_{int(time.time() * 1000)}.png")
            self.canvas.save_mask(path, int(self.grow.value()), int(self.feather.value()))
        p = dict(self.entry.get("params") or {})
        p.update(op="inpaint", source_image=self.entry["file"], mask_image=path, prompt=self.prompt.toPlainText(),
                 inpaint_strength=float(self.strength.value()), count=int(self.count.value()), seed=-1,
                 init_image="", variants=[])
        self.result_params = p
        self.accept()


# ================================================================================================ upscaler

class UpscaleDialog(QDialog):
    """The same picture bigger: 'Detailed' redraws fine detail with the picture's own model and prompt (sharper),
    'Clean' only enlarges it with an upscale model (fast, changes nothing)."""

    def __init__(self, parent, entry, has_upscaler=True):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle("Upscale")
        self.resize(520, 10)
        self.result_params = None
        self.w, self.h = entry.get("w") or 1024, entry.get("h") or 1024
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label("Upscale", "H2"))
        v.addWidget(label(_title(entry) + f"  ·  {self.w} × {self.h}", "Faint"))
        self.mode = Segmented([("detail", "Detailed", "Enlarges, then redraws fine detail with the same model, prompt and "
                                                      "seed - sharper, adds detail"),
                               ("clean", "Clean", "Only enlarges with an upscale model - fast, changes nothing")],
                              lambda _v: self._update(), "detail", height=34)
        v.addWidget(self.mode)
        self.scale = Segmented([("1.5", "1.5×"), ("2", "2×"), ("3", "3×"), ("4", "4×")], lambda _v: self._update(), "2",
                               height=32)
        v.addWidget(field("Size", self.scale, label_w=70))
        self.strength = Slider(0.1, 0.7, 0.01, 2, 0.35, lambda _v: self._update(), width=70)
        self.str_row = field("Detail", self.strength, "How much the detail pass may change: 0.25 keeps it very close, "
                                                      "0.45 adds a lot.", label_w=70)
        v.addWidget(self.str_row)
        self.info = label("", "Muted", wrap=True)
        v.addWidget(self.info)
        if not has_upscaler:
            v.addWidget(label("No upscale model found in ComfyUI (models/upscale_models) - the picture is stretched "
                              "before the detail pass. An anime / photo 2× or 4× model gives better results.", "Faint",
                              wrap=True))
        v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Upscale", self._go, "Accent", "scale")))
        self._update()

    def _update(self):
        sc = float(self.scale.value())
        fw, fh = W.snap(self.w * sc, 8), W.snap(self.h * sc, 8)
        detail = self.mode.value() == "detail"
        self.str_row.setVisible(detail)
        mp = fw * fh / 1e6
        t = f"Result: {fw} × {fh}  ({mp:.1f} MP)."
        if detail and mp > 4.5:
            t += "  Very big for a detail pass on 8 GB - it will be slow; 'Clean' or a smaller size is quicker."
        elif detail:
            t += "  Uses the picture's model, prompt, LoRAs and seed."
        self.info.setText(t)

    def _go(self):
        p = dict(self.entry.get("params") or {})
        p.update(op="upscale", source_image=self.entry["file"], up_mode=self.mode.value(),
                 up_scale=float(self.scale.value()), up_strength=float(self.strength.value()), count=1,
                 seed=int(self.entry.get("seed") if self.entry.get("seed") is not None else -1), init_image="",
                 variants=[])
        self.result_params = p
        self.accept()


# ================================================================================================ compare

COMPARE = [("seed", "Seeds"), ("cfg", "Guidance"), ("steps", "Steps"), ("sampler", "Sampler"),
           ("scheduler", "Scheduler"), ("preset", "Preset"), ("lora", "LoRA strength"), ("model", "Model")]


class CompareDialog(QDialog):
    """Make the same picture with one setting changed - side by side to see what it does."""

    def __init__(self, parent, params, lists, pictures):
        super().__init__(parent)
        self.setWindowTitle("Compare settings")
        self.resize(560, 10)
        self.params, self.lists, self.pictures = params, lists, pictures
        self.variants = None
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label("Compare settings", "H2"))
        v.addWidget(label("The same prompt and seed, one setting changed - the results open side by side.", "Muted",
                          wrap=True))
        self.what = Combo()
        for k, t in COMPARE:
            self.what.addItem(t, k)
        self.what.currentIndexChanged.connect(lambda _i: self._show())
        v.addWidget(field("Change", self.what, label_w=70))
        self.values = QLineEdit()
        self.values_row = field("Values", self.values, "Separated by commas", label_w=70)
        v.addWidget(self.values_row)
        self.n = QSpinBox()
        self.n.setRange(2, 16)
        self.n.setValue(4)
        self.n_row = field("How many", self.n, label_w=70)
        v.addWidget(self.n_row)
        self.lora = Combo()
        for i, lo in enumerate(params.get("loras") or []):
            self.lora.addItem(nice_name(lo.get("file")), i)
        self.lora_row = field("LoRA", self.lora, label_w=70)
        v.addWidget(self.lora_row)
        self.chips = ChipBox(6)
        v.addWidget(self.chips)
        self.note = label("", "Faint", wrap=True)
        v.addWidget(self.note)
        v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Compare", self._go, "Accent", "gallery")))
        self._chip_btns = []
        self._show()

    def _show(self):
        k = self.what.currentData()
        defaults = {"cfg": "3, 4.5, 6, 7.5", "steps": "12, 20, 28, 36", "lora": "0.4, 0.7, 1.0, 1.3"}
        self.values_row.setVisible(k in defaults)
        if k in defaults:
            self.values.setText(defaults[k])
        self.n_row.setVisible(k == "seed")
        self.lora_row.setVisible(k == "lora")
        self.chips.clear()
        self._chip_btns = []
        opts = []
        if k == "sampler":
            opts = self.lists.get("samplers") or ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde"]
            pre = {"euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde"}
        elif k == "scheduler":
            opts = self.lists.get("schedulers") or ["normal", "karras", "simple", "sgm_uniform"]
            pre = {"normal", "karras", "simple", "sgm_uniform"}
        elif k == "model":
            opts = [f"{src}|{name}" for name, src, _k in self.pictures]
            pre = set(opts[:3])
        elif k == "preset":
            opts = [x[0] for x in W.IMAGE_PRESETS]
            pre = set(opts)
        for o in opts:
            text = nice_name(o.split("|", 1)[-1]) if k == "model" else (W.IMAGE_PRESET[o][1] if k == "preset" else o)
            b = chip(text, None, True)
            b.setChecked(o in pre)
            self.chips.add(b)
            self._chip_btns.append((b, o))
        self.chips.setVisible(bool(opts))
        notes = {"seed": "Different seeds = different pictures from the same settings - pick your favourite.",
                 "lora": "" if self.lora.count() else "Add a LoRA on the Image page first.",
                 "model": "Each model with its own family defaults."}
        self.note.setText(notes.get(k, ""))
        self.adjustSize()

    def _nums(self):
        out = []
        for x in self.values.text().replace(";", ",").split(","):
            try:
                out.append(float(x.strip()))
            except ValueError:
                pass
        return out

    def _go(self):
        k = self.what.currentData()
        vs = []
        if k == "seed":
            import random
            vs = [{"label": f"Seed {s}", "set": {"seed": s}} for s in
                  (random.randint(1, 2 ** 31 - 1) for _ in range(self.n.value()))]
        elif k == "cfg":
            vs = [{"label": f"Guidance {x:g}", "set": {"cfg": x}} for x in self._nums()]
        elif k == "steps":
            vs = [{"label": f"{int(x)} steps", "set": {"steps": int(x)}} for x in self._nums()]
        elif k == "lora":
            if not self.lora.count():
                return
            i = self.lora.currentData()
            vs = [{"label": f"{nice_name(self.lora.currentText())} {x:g}", "set": {"lora_strength": [i, x]}}
                  for x in self._nums()]
        else:
            picked = [o for b, o in self._chip_btns if b.isChecked()]
            if k in ("sampler", "scheduler"):
                vs = [{"label": o, "set": {k: o}} for o in picked]
            elif k == "preset":
                vs = [{"label": W.IMAGE_PRESET[o][1], "set": {"preset": o, "steps": 0, "cfg": 0.0, "sampler": "",
                                                              "scheduler": "", "hires": "preset"}} for o in picked]
            elif k == "model":
                kinds = {f"{src}|{name}": kk for name, src, kk in self.pictures}
                vs = [{"label": nice_name(o.split("|", 1)[1]),
                       "set": {"model_src": o.split("|", 1)[0], "ckpt": o.split("|", 1)[1], "family": "auto",
                               "_kind": kinds.get(o), "loras": []}} for o in picked]
        if len(vs) < 2:
            self.note.setText("Pick at least two to compare.")
            return
        self.variants = vs[:16]
        self.accept()


class CompareView(QDialog):
    """The results of a comparison side by side."""
    use = Signal(dict)

    def __init__(self, parent, entries, title="Comparison"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.entries = entries
        self.resize(1300, 860)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        p0 = (entries[0].get("prompt") or "") if entries else ""
        v.addWidget(label(title, "H2"))
        v.addWidget(label(p0[:200], "Faint"))
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        self.grid = QGridLayout(body)
        self.grid.setSpacing(12)
        area.setWidget(body)
        v.addWidget(area, 1)
        self.cells = []
        cols = min(4, max(2, math.ceil(math.sqrt(len(entries)))))
        for i, e in enumerate(entries):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(6)
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pic.setMinimumSize(200, 200)
            pic.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            pic.setStyleSheet(f"background: {T.FIELD}; border-radius: 10px;")
            pic.setCursor(Qt.CursorShape.PointingHandCursor)
            pic.mouseDoubleClickEvent = lambda _e, f=e["file"]: os.startfile(f)
            pic.setToolTip("Double-click: open")
            cap = label(e.get("label") or f"Seed {e.get('seed')}", "H3")
            use = button("Use these settings", lambda e=e: self.use.emit(e), "Ghost", "refresh")
            cv.addWidget(pic, 1)
            cv.addWidget(hrow(cap, None, use))
            self.grid.addWidget(cell, i // cols, i % cols)
            self.cells.append((pic, QPixmap(e["file"])))
        rows = math.ceil(len(entries) / cols)
        for r in range(rows):
            self.grid.setRowStretch(r, 1)
        v.addWidget(hrow(None, button("Close", self.close, None)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._scale()

    def showEvent(self, e):
        super().showEvent(e)
        self._scale()

    def _scale(self):
        for pic, pm in self.cells:
            if not pm.isNull() and pic.width() > 10:
                pic.setPixmap(pm.scaled(QSize(pic.width() - 8, pic.height() - 8), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
