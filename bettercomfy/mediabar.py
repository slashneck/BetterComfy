"""The controls under a video or GIF in the big viewer: play / pause, where it is (drag to jump), the time, volume
(only when it has sound), loop, save this frame, and trim & crop. A GIF gets a frame bar instead of a time bar."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSlider, QWidget

from . import icons, theme as T
from .config import cfg
from .widgets import icon_button, label


def _time(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _slider(width=None):
    s = QSlider(Qt.Orientation.Horizontal)
    s.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    s.setCursor(Qt.CursorShape.PointingHandCursor)
    s.setStyleSheet(f"""
        QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.16); border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {T.accent().name()}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: #FFFFFF; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
        QSlider::handle:horizontal:hover {{ background: {T.accent().lighter(140).name()}; }}""")
    if width:
        s.setFixedWidth(width)
    return s


class MediaBar(QWidget):
    play_toggled = Signal()
    seek = Signal(int)              # ms (video) or a frame (GIF)
    step = Signal(int)              # one frame back (-1) / on (1)
    volume = Signal(int)            # 0 - 100
    muted = Signal(bool)
    loop = Signal(bool)
    save_frame = Signal()
    edit = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("MediaBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#MediaBar { background: rgba(16, 16, 18, 0.9); border: 1px solid rgba(255,255,255,0.08); "
                           "border-radius: 14px; }")
        self.setFixedHeight(46)
        self.mode = "video"
        self._drag = False
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 12, 4)
        h.setSpacing(6)
        self.play = icon_button("pause", self.play_toggled.emit, "Play / pause (Space)", size=16)
        self.back = icon_button("back", lambda: self.step.emit(-1), "One frame back ( , )", size=14)
        self.fwd = icon_button("arrow", lambda: self.step.emit(1), "One frame on ( . )", size=14)
        self.pos_lbl = label("0:00", "Faint")
        self.pos_lbl.setMinimumWidth(44)
        self.pos_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = _slider()
        self.slider.sliderPressed.connect(lambda: setattr(self, "_drag", True))
        self.slider.sliderReleased.connect(self._released)
        self.slider.sliderMoved.connect(self._moved)
        self.slider.valueChanged.connect(self._clicked)
        self.len_lbl = label("0:00", "Faint")
        self.len_lbl.setMinimumWidth(44)
        self.vol_btn = icon_button("volume", self._toggle_mute, "Mute (M)", size=16)
        self.vol = _slider(86)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(cfg.get("viewer_volume", 80)))
        self.vol.valueChanged.connect(self._vol)
        self.is_muted = False
        self.loop_btn = icon_button("loop", self._loop, "Play it again and again", size=16)
        self.loop_btn.setCheckable(True)
        self.loop_btn.setChecked(True)
        self.frame_btn = icon_button("camera", self.save_frame.emit, "Save this frame as a picture", size=16)
        self.edit_btn = icon_button("crop", self.edit.emit, "Trim, crop, rotate… (E)", size=16)
        for w in (self.play, self.back, self.fwd, self.pos_lbl, self.slider, self.len_lbl, self.vol_btn, self.vol,
                  self.loop_btn, self.frame_btn, self.edit_btn):
            h.addWidget(w)
        h.setStretchFactor(self.slider, 1)
        self._loop_icon()

    # ---- what it shows
    def set_video(self, has_audio=False):
        self.mode = "video"
        self.back.setToolTip("A moment back ( , )")
        self.fwd.setToolTip("A moment on ( . )")
        self.set_audio(has_audio)
        self.set_length(0)
        self.set_pos(0)

    def set_gif(self, frames):
        self.mode = "gif"
        self.back.setToolTip("One frame back ( , )")
        self.fwd.setToolTip("One frame on ( . )")
        self.set_audio(False)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, frames - 1))
        self.slider.blockSignals(False)
        self.len_lbl.setText(f"{frames}")
        self.set_pos(0)

    def set_audio(self, on):
        self.vol_btn.setVisible(on)
        self.vol.setVisible(on)

    def set_length(self, ms):
        if self.mode != "video":
            return
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, int(ms)))
        self.slider.blockSignals(False)
        self.len_lbl.setText(_time(ms))

    def set_pos(self, v):
        if self._drag:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)
        self.pos_lbl.setText(_time(v) if self.mode == "video" else f"{int(v) + 1} /")

    def set_playing(self, on):
        self.play.setIcon(icons.icon("pause" if on else "play", "#E4E4E7", 16))

    # ---- input
    def _moved(self, v):
        self.pos_lbl.setText(_time(v) if self.mode == "video" else f"{int(v) + 1} /")
        if self.mode == "gif":
            self.seek.emit(int(v))

    def _released(self):
        self._drag = False
        self.seek.emit(self.slider.value())

    def _clicked(self, v):
        if not self._drag:                      # a click on the groove (not a drag): jump there
            self.seek.emit(int(v))

    def _vol(self, v):
        cfg.set("viewer_volume", int(v))
        if self.is_muted and v:
            self._toggle_mute()
        self.volume.emit(int(v))

    def _toggle_mute(self):
        self.is_muted = not self.is_muted
        self.vol_btn.setIcon(icons.icon("mute" if self.is_muted else "volume", "#E4E4E7", 16))
        self.muted.emit(self.is_muted)

    def _loop(self):
        self._loop_icon()
        self.loop.emit(self.loop_btn.isChecked())

    def _loop_icon(self):
        self.loop_btn.setIcon(icons.icon("loop", T.accent().name() if self.loop_btn.isChecked() else "#A1A1AA", 16))
