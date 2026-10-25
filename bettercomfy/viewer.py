"""The big viewer: a picture (or video) over the whole window. Click: zoom in where you clicked / back to fit,
wheel: zoom, drag: move around, ← →: the one before / after, Esc: close. Videos play with their sound, a time bar and
volume (vault videos straight from memory); GIFs get a frame bar. S: slideshow."""
import os

from PySide6.QtCore import QByteArray, QBuffer, QEasingCurve, QIODevice, QPointF, QRectF, Qt, QTimer, QUrl, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from . import icons, media, theme as T
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
        from .mediabar import MediaBar
        self.bar = MediaBar(self)
        self.bar.hide()
        self.bar.play_toggled.connect(self._toggle_play)
        self.bar.seek.connect(self._seek)
        self.bar.step.connect(self._step_frame)
        self.bar.volume.connect(lambda v: self.audio and self.audio.setVolume(v / 100))
        self.bar.muted.connect(lambda m: self.audio and self.audio.setMuted(m))
        self.bar.loop.connect(self._set_loop)
        self.bar.save_frame.connect(self._save_frame)
        self.bar.edit.connect(self._edit)
        self.player = self.audio = self.sink = None
        self._buf = None
        self._video = False
        self._sized = False
        self.slides = QTimer(self, singleShot=True, timeout=lambda: self.step(1))

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
        self.slides.stop()
        self._stop_media()
        self.bar.hide()
        self._fade_to(0.0)

    def forget(self):
        """The vault locked: decrypted pixels go at once, no fade."""
        self.timer.stop()
        self.slides.stop()
        self._stop_media()
        self.bar.hide()
        self.frames, self.entries, self.pm = [], [], QPixmap()
        self._op = 0.0
        self.hide()

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
            self._place_bar()
            if self.fit:
                self._fit()
        return False

    # ---------------------------------------------------------------- playing
    def _place_bar(self):
        w = min(820, self.width() - 180)
        self.bar.setGeometry(int((self.width() - w) / 2), self.height() - 46 - 46, int(w), 46)

    def _ensure_player(self):
        if self.player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(self.bar.vol.value() / 100)
            self.sink = QVideoSink(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self._vframe)
            self.player.positionChanged.connect(self.bar.set_pos)
            self.player.durationChanged.connect(self.bar.set_length)
            self.player.hasAudioChanged.connect(self.bar.set_audio)
            self.player.playbackStateChanged.connect(
                lambda s: self.bar.set_playing(s == QMediaPlayer.PlaybackState.PlayingState))
            self.player.errorOccurred.connect(self._media_error)
        return self.player

    def _play_video(self, e):
        """A video with its sound. One in the vault is decrypted into memory and played from there."""
        from PySide6.QtMultimedia import QMediaPlayer
        p = self._ensure_player()
        self._video, self._sized = True, False
        self.bar.set_video(False)
        self.bar.set_playing(True)
        p.setLoops(QMediaPlayer.Loops.Infinite if self.bar.loop_btn.isChecked() and not self.slides.isActive() else 1)
        if e.get("vault"):
            from .vault import vault
            data = vault.read(e)
            self._buf = QBuffer(self)
            self._buf.setData(QByteArray(data))
            self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
            p.setSourceDevice(self._buf, QUrl(f"memory.{e.get('fmt') or 'mp4'}"))
        else:
            p.setSource(QUrl.fromLocalFile(e["file"]))
        p.play()
        self._place_bar()
        self.bar.show()
        self.bar.raise_()

    def _stop_media(self):
        self._video = False
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        if self._buf is not None:
            self._buf.close()
            self._buf.setData(QByteArray())         # the decrypted video leaves memory
            self._buf.deleteLater()
            self._buf = None

    def _vframe(self, frame):
        if not self._video:
            return
        img = frame.toImage()
        if img.isNull():
            return
        self.pm = QPixmap.fromImage(img)
        if not self._sized:                     # the first frame: fitted to the window
            self._sized = True
            self._fit()
        self.update()

    def _media_error(self, *_):
        """Qt could not play it: the old way (its frames, no sound)."""
        e = self.cur()
        if e is None or not self._video:
            return
        self._stop_media()
        self.bar.hide()
        self.loader.load(e["id"], None if e.get("vault") else e["file"], e if e.get("vault") else None)

    def _toggle_play(self):
        if self._video and self.player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
        elif self.frames:
            if self.timer.isActive():
                self.timer.stop()
            else:
                self.timer.start(int(1000 / self.fps))
            self.bar.set_playing(self.timer.isActive())

    def _seek(self, v):
        if self._video and self.player is not None:
            self.player.setPosition(int(v))
        elif self.frames:
            self.fi = max(0, min(int(v), len(self.frames) - 1))
            self.pm = self.frames[self.fi]
            self.update()

    def _step_frame(self, d):
        if self._video and self.player is not None:
            self.player.pause()
            self.player.setPosition(max(0, self.player.position() + d * 1000 // 16))
        elif self.frames:
            self.timer.stop()
            self.bar.set_playing(False)
            self._seek(self.fi + d)
            self.bar.set_pos(self.fi)

    def _set_loop(self, on):
        if self.player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            self.player.setLoops(QMediaPlayer.Loops.Infinite if on else 1)

    def _save_frame(self):
        e = self.cur()
        app = getattr(self, "app", None)
        if e is None or app is None or not self.pm or self.pm.isNull():
            return
        app.save_frame(e, self.pm.toImage())

    def _edit(self):
        e = self.cur()
        app = getattr(self, "app", None)
        if e is not None and app is not None:
            if self._video and self.player is not None:
                self.player.pause()
            app.edit_entry(e)

    def toggle_slides(self):
        if self.slides.isActive():
            self.slides.stop()
        else:
            self._next_slide()
        self.update()

    def _next_slide(self):
        e = self.cur()
        moving = e is not None and (e.get("kind") == "video" or media.is_gif(e))
        self.slides.start(9000 if moving else 4000)

    # ---------------------------------------------------------------- content
    def cur(self):
        return self.entries[self.idx] if self.entries else None

    def _load(self):
        e = self.cur()
        self.timer.stop()
        self.frames = []
        self._stop_media()
        self.bar.hide()
        if self.slides.isActive():
            self._next_slide()
        if e is None:
            return
        if e.get("vault"):
            # decrypted into memory only
            from .vault import vault
            self.pm = QPixmap()
            try:
                self.pm.loadFromData(vault.read(e, "thumb_blob" if e.get("kind") == "video" else "blob") or b"")
            except Exception:
                pass
            if media.is_gif(e):
                self.loader.load(e["id"], None, gif=e)
            elif e.get("kind") == "video":
                self._fit()
                self._play_video(e)
                return
        elif media.is_gif(e):
            self.pm = QPixmap(e["file"])
            self.loader.load(e["id"], e["file"], gif=e)
        elif e.get("kind") == "video":
            self.pm = QPixmap(e.get("thumb") or "")
            self._fit()
            self._play_video(e)
            return
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
        self.bar.set_gif(len(self.frames))
        self.bar.set_playing(True)
        self._place_bar()
        self.bar.show()
        self.bar.raise_()

    def _next_frame(self):
        if self.frames:
            self.fi = (self.fi + 1) % len(self.frames)
            self.pm = self.frames[self.fi]
            if self.bar.isVisible():
                self.bar.set_pos(self.fi)
            self.update()

    def drop(self, ids):
        """Entries deleted while the viewer is open: shown no more (on to the next one, or closed)."""
        ids = set(ids)
        cur = self.cur()
        self.entries = [e for e in self.entries if e["id"] not in ids]
        if not self.entries:
            self.close_view()
            return
        if cur and cur["id"] in ids:
            self.idx = min(self.idx, len(self.entries) - 1)
            self._load()
        else:
            self.idx = self.entries.index(cur) if cur in self.entries else 0
        self.update()

    def _flag(self, key):
        e = self.cur()
        app = getattr(self, "app", None)
        if e is None or app is None:
            return
        on = app.toggle_flag([e], key)
        self.update()
        if key == "marked" and on:
            QTimer.singleShot(180, lambda: self.step(1))            # culling: on to the next one

    def step(self, d):
        if len(self.entries) > 1:
            self.idx = (self.idx + d) % len(self.entries)
            self._load()
            if getattr(self, "on_move", None):
                self.on_move(self.cur())

    # ---------------------------------------------------------------- zoom
    def _area(self):
        return QRectF(self.rect()).adjusted(70, 64, -70, -110 if self.bar.isVisible() or self._video else -54)

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
        elif k == Qt.Key.Key_F:
            self._flag("fav")
        elif k == Qt.Key.Key_X:
            self._flag("marked")
        elif k == Qt.Key.Key_Delete and getattr(self, "app", None) and self.cur():
            if self.cur().get("vault"):
                self.app.delete_vault([self.cur()])
            else:
                self.app.delete_entries([self.cur()])
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self.step(-1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self.step(1)
        elif k == Qt.Key.Key_Space and (self.frames or self._video):
            self._toggle_play()
        elif k in (Qt.Key.Key_Comma, Qt.Key.Key_Period) and (self.frames or self._video):
            self._step_frame(-1 if k == Qt.Key.Key_Comma else 1)
        elif k == Qt.Key.Key_M and self._video:
            self.bar._toggle_mute()
        elif k == Qt.Key.Key_E and self.cur():
            self._edit()
        elif k == Qt.Key.Key_S:
            self.toggle_slides()
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
            p.drawText(QRectF(24, 18, self.width() - 140, 24), Qt.AlignmentFlag.AlignVCenter, (e.get("name") or "Private") if e.get("vault") else nice_name(e["file"]))
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(T.TEXT2))
            zoom = f"{self.scale * 100:.0f} %"
            info = f"{self.idx + 1} / {len(self.entries)}    {e.get('w')} × {e.get('h')}    {zoom}"
            fm = QFontMetrics(f)
            p.drawText(QPointF(self.width() - 80 - fm.horizontalAdvance(info), 35), info)
            hint = ("Click: zoom   ·   ← →: before / after   ·   F: favourite   ·   X: mark   ·   E: edit   ·   "
                    "S: slideshow   ·   Esc: close")
            if self.frames or self._video:
                hint += "   ·   Space: pause   ·   , .: frame"
            if self.slides.isActive():
                hint = "Slideshow   ·   S: stop   ·   " + hint
            p.setPen(QColor(T.TEXT3))
            p.drawText(QRectF(0, self.height() - 34, self.width(), 20), Qt.AlignmentFlag.AlignCenter, hint)
        # favourite / marked
        if e:
            x = 24
            f2 = QFont(self.font())
            f2.setPixelSize(12)
            f2.setWeight(QFont.Weight.DemiBold)
            p.setFont(f2)
            for on, text, col in ((e.get("fav"), "★  Favourite", QColor("#F5B041")),
                                  (e.get("marked"), "Marked for deletion", QColor(T.BAD))):
                if not on:
                    continue
                w = QFontMetrics(f2).horizontalAdvance(text) + 22
                r = QRectF(x, 48, w, 24)
                p.setPen(Qt.PenStyle.NoPen)
                bg = QColor(col)
                bg.setAlpha(46)
                p.setBrush(bg)
                p.drawRoundedRect(r, 8, 8)
                p.setPen(col)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
                x += w + 8
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
