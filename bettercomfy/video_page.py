"""The VIDEO page: a picture (or only words) to a video with WAN 2.1 / 2.2 - seamless loops, ping-pong, crossfade."""
import copy
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

from . import jobs, media, workflows as W
from .components import GenerateBar, LoraStack, PromptCard, ResultPane, SeedBox, UserPresets, remember_prompt
from .config import cfg
from .widgets import (Combo, AspectPreview, Card, ChipBox, Collapsible, ImageDrop, PresetPicker, Scroll, Segmented, Slider,
                      ToggleRow, chip, field, label, nice_name, quiet, set_combo, human_time)


class VideoPage(QWidget):
    title = "Video"
    subtitle = "Picture to video  ·  text to video  ·  WAN"
    toast = Signal(str, str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link, self.queue, self.history = app.link, app.queue, app.history
        self.p = copy.deepcopy(W.VIDEO_DEFAULT)
        self.p.update(cfg.get("video_state") or {})
        self._loading = False
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setObjectName("PanelR")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(440)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        sc = Scroll((16, 16, 14, 16), 12)
        self.scroll = sc
        sv.addWidget(sc, 1)
        self.bar = GenerateBar("video", [("Animate several pictures…", "image", self.batch_files),
                                         ("Animate a whole folder…", "folder", self.batch_folder)])
        self.bar.generate.connect(self.generate)
        self.bar.private_changed.connect(lambda on: self._set("private", on))
        sv.addWidget(self.bar)

        # my presets
        self.presets = UserPresets("video", lambda: dict(self.p), skip=("start_image", "end_image", "join_with", "join",
                                                                        "join_name", "count"))
        self.presets.apply.connect(self._apply_preset)
        sc.add(self.presets)

        # pictures
        self.start = ImageDrop("Drop the picture to animate, or click to choose.\nEmpty: text to video.  "
                               "Several pictures: one video each.", 190)
        self.start.changed.connect(self._start_changed)
        self.start.many.connect(self.batch)
        self.join = ToggleRow("Join with the clip it continues", "", True, lambda v: self._set("join", v))
        self.end = ImageDrop("Drop the end picture", 120)
        self.end.changed.connect(lambda path: self._set("end_image", path))
        self.end_row = field("End picture", self.end, "The video moves from the start picture to this one.",
                             stacked=True)
        self.mode_note = label("", "Faint", wrap=True)
        c = Card("Start picture")
        c.add(self.start, self.join, self.end_row, self.mode_note)
        sc.add(c)

        # motion prompt
        self.prompt = PromptCard("Motion", "What moves, and how - one clear motion works best.\n"
                                           "e.g. her hair sways in the wind, she breathes slowly, camera static",
                                 chips=W.MOTION_CHIPS, neg_toggle_text="Use the words to avoid",
                                 neg_sub="Needs guidance above 1 - the motion pass then takes about twice as long",
                                 height=96)
        self.prompt.changed.connect(self._prompt_changed)
        self.prompt.submit.connect(lambda: self.generate(self.bar.count.value()))
        self.prompt.what = "video"
        self.prompt.style_fn = lambda: "motion" if self.p.get("start_image") else "natural"
        sc.add(self.prompt)

        # length & loop
        self.seconds = Slider(1.0, 10.0, 0.25, 2, 3.0, lambda v: self._set("seconds", v), " s", width=72)
        self.frames_lbl = label("", "Faint")
        self.loop = Segmented([("loop", "Loop", W.LOOP["loop"][2]), ("pingpong", "Ping-pong", W.LOOP["pingpong"][2]),
                               ("crossfade", "Crossfade", W.LOOP["crossfade"][2]), ("pair", "Start→End", W.LOOP["pair"][2]),
                               ("free", "Free", W.LOOP["free"][2])], lambda v: self._set("loop", v), height=32)
        self.loop_line = label("", "Muted", wrap=True)
        self.loop_words = ToggleRow("Add loop words to the prompt", "Helps WAN make one repeating motion", True,
                                    lambda v: self._set("loop_words", v))
        self.steady = ToggleRow("Steady colours", "Fixes WAN's brightness / colour drift (often a flash at the end)",
                                True, lambda v: self._set("steady", v))
        self.seam = Slider(0, 12, 1, 0, 0, lambda v: self._set("seam", int(v)), " fr", width=72)
        self.seam.n.setSpecialValueText("Auto")
        self.seam_row = field("Seam blend", self.seam, "How many frames are blended across the loop point. Auto picks "
                                                       "it from the length.", label_w=82)
        c = Card("Length & loop")
        c.add(field("Length", self.seconds, "WAN makes 16 frames a second. 2 - 4 s loops are the sweet spot; longer "
                                            "gets slow and may drift.", label_w=82),
              self.frames_lbl, self.loop, self.loop_line, self.loop_words, self.steady, self.seam_row)
        sc.add(c)

        # quality
        self.preset = PresetPicker([(x[0], x[1], x[2]) for x in W.VIDEO_PRESETS], lambda v: self._preset(v))
        self.preset_line = label("", "Muted", wrap=True)
        self.size_prev = AspectPreview(120)
        self.aspects = ChipBox(6)
        self._aspect_btns = {}
        for key, a, b in W.ASPECTS:
            if key in ("9:21", "21:9"):
                continue
            btn = chip(key, None, True)
            btn.clicked.connect(lambda _=False, k=key: self._aspect(k))
            self.aspects.add(btn)
            self._aspect_btns[key] = btn
        self.interp = Segmented([("1", "Off", "16 fps, as WAN makes it"), ("2", "× 2", "32 fps - smooth (RIFE)"),
                                 ("3", "× 3", "48 fps"), ("4", "× 4", "64 fps")],
                                lambda v: self._set("interp", int(v)), height=30)
        self.fmt = Segmented([("mp4", "MP4", "H.264 - plays everywhere"), ("webm", "WebM", "VP9 - smaller, for the web"),
                              ("gif", "GIF", "Animated GIF (big files, 256 colours)")],
                             lambda v: self._set("format", v), height=30)
        c = Card("Quality")
        c.add(self.preset, self.preset_line, self.aspects, self.size_prev,
              field("Smoothing", self.interp, "Adds frames in between (RIFE) - almost free, much smoother. Needs a "
                                              "frame interpolation model (rife).", label_w=82),
              field("Format", self.fmt, label_w=82))
        sc.add(c)

        # loras
        self.loras = LoraStack("video", self.prompt, lambda: "wan", self.link.install)
        self.loras.changed.connect(lambda: self._set("loras", self.loras.value()))
        sc.add(Card("LoRAs").add(self.loras))

        # models
        self.models_box = Collapsible("Models", False, "Which of your ComfyUI's WAN models are used (picked by "
                                                       "themselves)")
        self.two = Segmented([("two", "Motion + detail", "WAN 2.2 14B: a high-noise (motion) and a low-noise (detail) "
                                                         "model"), ("one", "One model", "WAN 2.1, or WAN 2.2 5B")],
                             lambda v: self._set("models", v), height=30)
        self.combos = {}
        rows = []
        for key, name, tip in (("unet_high", "Motion", "WAN 2.2 'high noise' model: motion and shapes"),
                               ("unet_low", "Detail", "WAN 2.2 'low noise' model: the details"),
                               ("unet", "Model", "The one WAN image-to-video model"),
                               ("unet_t2v", "Text→video", "The WAN text-to-video model (used without a start picture)"),
                               ("clip", "Text encoder", "umt5 xxl"),
                               ("vae", "VAE", "wan_2.1_vae for WAN 2.1 / 2.2 14B"),
                               ("interp_model", "Smoother", "RIFE model for Smoothing")):
            cb = Combo()
            cb.currentIndexChanged.connect(lambda _=0, k=key, cb=cb: self._set(k, cb.currentData() or ""))
            self.combos[key] = cb
            w = field(name, cb, tip, label_w=86)
            rows.append(w)
            setattr(self, "row_" + key, w)
        self.dtype = Segmented([("default", "As saved", "As the file is"), ("fp8_e4m3fn", "fp8", "Less memory"),
                                ("fp8_e4m3fn_fast", "fp8 fast", "Less memory, faster on new cards")],
                               lambda v: self._set("dtype", v), height=30)
        self.speed_note = label("", "AccentBadge")
        self.models_box.add(self.two, *rows, field("Weights", self.dtype, label_w=86))

        # fine tuning
        self.tune = Collapsible("Fine tuning", False, "The sampler - the preset sets these unless you do")
        self.steps = QSpinBox()
        self.steps.setRange(0, 80)
        self.steps.setSpecialValueText("Preset")
        self.steps.valueChanged.connect(lambda v: self._set("steps", int(v)))
        self.hsteps = QSpinBox()
        self.hsteps.setRange(0, 80)
        self.hsteps.setSpecialValueText("Preset")
        self.hsteps.valueChanged.connect(lambda v: self._set("high_steps", int(v)))
        self.cfg_h = QDoubleSpinBox()
        self.cfg_l = QDoubleSpinBox()
        for sb, k in ((self.cfg_h, "cfg_high"), (self.cfg_l, "cfg_low")):
            sb.setRange(0, 15)
            sb.setSingleStep(0.1)
            sb.setDecimals(1)
            sb.setSpecialValueText("Preset")
            sb.valueChanged.connect(lambda v, k=k: self._set(k, float(v)))
        self.shift = Slider(1.0, 15.0, 0.5, 1, 5.0, lambda v: self._set("shift", v), width=64)
        self.sampler = Combo()
        self.sampler.currentIndexChanged.connect(lambda _=0: self._set("sampler", self.sampler.currentData() or "euler"))
        self.scheduler = Combo()
        self.scheduler.currentIndexChanged.connect(lambda _=0: self._set("scheduler",
                                                                         self.scheduler.currentData() or "simple"))
        self.custom = QSpinBox()
        self.custom.setRange(0, 2000000)
        self.custom.setSingleStep(16384)
        self.custom.setSpecialValueText("Preset")
        self.custom.setSuffix(" px")
        self.custom.valueChanged.connect(lambda v: self._set("custom_size", int(v)))
        lw = 108
        self.tune.add(field("Steps", self.steps, "All sampling steps. Speed (Lightning) models: 4 - 8. Normal WAN: 20+.",
                            label_w=lw),
                      field("Motion steps", self.hsteps, "How many of them the motion model does.", label_w=lw),
                      field("Guidance motion", self.cfg_h, "CFG of the motion model. 1 = fastest (words to avoid "
                                                           "ignored).", label_w=lw),
                      field("Guidance detail", self.cfg_l, label_w=lw),
                      field("Shift", self.shift, "Higher: more motion. Lower: stays closer to the picture. Motion "
                                                 "stalls near the end? Try 6 - 8.", label_w=lw),
                      field("Sampler", self.sampler, label_w=lw), field("Scheduler", self.scheduler, label_w=lw),
                      field("Pixels", self.custom, "Width × height instead of the preset's (the shape stays).",
                            label_w=lw))
        self.seed = SeedBox()
        self.seed.changed.connect(lambda: self._set("seed", self.seed.value()))
        c = Card("Models & tuning", [self.speed_note])
        c.add(self.models_box, self.tune, field("Seed", self.seed, label_w=40))
        sc.add(c)
        sc.end()
        h.addWidget(side)

        self.pane = ResultPane("video", self.history, [
            ("extend", "Extend", "arrow", "Continue the motion from this video's last frame"),
            ("reuse", "Reuse", "refresh", "Load this video's settings (and seed)"),
            ("gif", "GIF", "download", "Save a copy as GIF"),
            ("folder", "", "folder", "Show in its folder"),
            ("delete", "", "trash", "Move to the recycle bin")],
            "No videos yet", "Drop a picture on the left (or make one on the Image page and press Animate), describe "
                             "the motion and press Generate.")
        self.pane.action.connect(lambda k, e: self.app.result_action(k, e, self))
        self.pane.dropped.connect(self.set_start)
        h.addWidget(self.pane, 1)

        self.link.models.connect(self._models)
        self.queue.job_changed.connect(self._job_changed)
        self.queue.preview.connect(self._job_preview)
        self.queue.changed.connect(self._queue_changed)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=lambda: cfg.set("video_state", dict(self.p)))
        self.load(self.p)
        if self.link.lists:
            self._models(self.link.lists)

    # ------------------------------------------------------------------ state
    def _set(self, k, v):
        if self._loading:
            return
        self.p[k] = v
        self._update()
        self._save_timer.start()

    def _prompt_changed(self):
        if self._loading:
            return
        self.p["prompt"] = self.prompt.text()
        self.p["negative"] = self.prompt.neg_edit.toPlainText()
        self.p["use_negative"] = self.prompt.neg_toggle.isChecked()
        self._save_timer.start()

    def load(self, p):
        self._loading = True
        self.p = copy.deepcopy(W.VIDEO_DEFAULT)
        self.p.update(p or {})
        p = self.p
        self.start.set_path(p.get("start_image", ""), emit=False)
        self.join.set(p.get("join", True))
        self.join.setVisible(bool(p.get("join_with")))
        if p.get("join_with"):
            self.join.sub.setText("Extends " + (p.get("join_name") or os.path.basename(p["join_with"])) +
                                  " - saved as one longer video")
        self.end.set_path(p.get("end_image", ""), emit=False)
        self.prompt.set_text(p.get("prompt", ""))
        quiet(self.prompt.neg_edit, self.prompt.neg_edit.setPlainText, p.get("negative", ""))
        self.prompt.neg_toggle.set(p.get("use_negative", False))
        self.seconds.set(float(p.get("seconds", 3.0)))
        self.loop.set(p.get("loop", "loop"), animate=False)
        self.loop_words.set(p.get("loop_words", True))
        self.seam.set(int(p.get("seam", 0)))
        self.steady.set(p.get("steady", True))
        self.preset.set(p.get("preset", "fast"), animate=False)
        self.interp.set(str(int(p.get("interp", 2))), animate=False)
        self.fmt.set(p.get("format", "mp4"), animate=False)
        for k, cb in self.combos.items():
            set_combo(cb, p.get(k, ""))
        self.two.set(p.get("models", "two"), animate=False)
        self.dtype.set(p.get("dtype", "default"), animate=False)
        quiet(self.steps, self.steps.setValue, int(p.get("steps") or 0))
        quiet(self.hsteps, self.hsteps.setValue, int(p.get("high_steps") or 0))
        quiet(self.cfg_h, self.cfg_h.setValue, float(p.get("cfg_high") or 0))
        quiet(self.cfg_l, self.cfg_l.setValue, float(p.get("cfg_low") or 0))
        self.shift.set(float(p.get("shift", 5.0)))
        set_combo(self.sampler, p.get("sampler", "euler"))
        set_combo(self.scheduler, p.get("scheduler", "simple"))
        quiet(self.custom, self.custom.setValue, int(p.get("custom_size") or 0))
        self.seed.set(p.get("seed", -1))
        self.loras.set_items(p.get("loras") or [])
        self.bar.set_private(p.get("private", False))
        for k, b in self._aspect_btns.items():
            quiet(b, b.setChecked, k == p.get("aspect", "2:3"))
        self._loading = False
        self._update()

    def _start_changed(self, path):
        if not self._loading and self.p.get("join_with"):
            for k in ("join_with", "join_name"):
                self.p.pop(k, None)          # another picture: not a continuation any more
            self.join.setVisible(False)
        self._set("start_image", path)

    def _apply_preset(self, d):
        p = dict(self.p)
        p.update(d)
        self.load(p)
        self.toast.emit("Preset loaded.", "ok")

    def batch_files(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, "Pictures to animate", cfg.get("last_pick_dir") or "",
                                                "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)")
        if files:
            cfg.set("last_pick_dir", os.path.dirname(files[0]))
            self.batch(files)

    def batch_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "A folder of pictures to animate", cfg.get("last_pick_dir") or "")
        if not d:
            return
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(media.IMAGE_EXT))
        if not files:
            self.toast.emit("No pictures in that folder.", "warn")
            return
        self.batch(files)

    def batch(self, files):
        """One video per picture, all with the settings on this page."""
        from PySide6.QtWidgets import QMessageBox
        self._prompt_changed()
        q = QMessageBox.question(self, "Animate several", f"Queue {len(files)} videos - one per picture, all with "
                                                          "the settings and motion prompt on this page?")
        if q != QMessageBox.StandardButton.Yes:
            return
        for f in files:
            p = copy.deepcopy(self.p)
            p.update(start_image=f, count=1, mode="auto")
            for k in ("join_with", "join_name"):
                p.pop(k, None)
            if p.get("loop") == "pair":
                p["loop"] = "loop"
            sz = media.image_size(f)
            pl = W.video_plan(dict(self._resolved(), start_image=f), sz[0] / sz[1] if sz else 2 / 3)
            self.queue.add("video", p, nice_name(f), thumb=f, units=W.work_units("video", pl))
        self.toast.emit(f"{len(files)} videos queued.", "ok")

    def _aspect(self, k):
        for key, b in self._aspect_btns.items():
            quiet(b, b.setChecked, key == k)
        self._set("aspect", k)

    def _preset(self, key):
        q = W.VIDEO_PRESET[key]
        self.p["interp"] = q[6]
        self.interp.set(str(q[6]))
        self._set("preset", key)

    # ------------------------------------------------------------------ lists
    def _models(self, lists):
        self._loading = True
        guess = lists.get("video_guess") or {}
        src = {"unet_high": "unet", "unet_low": "unet", "unet": "unet", "unet_t2v": "unet", "clip": "clip",
               "vae": "vae", "interp_model": "interp"}
        for k, cb in self.combos.items():
            opts = list(lists.get(src[k]) or [])
            if k.startswith("unet"):
                wan = [o for o in opts if "wan" in o.lower()]
                opts = wan + [o for o in opts if o not in wan]
            cur = self.p.get(k) or guess.get(k, "")
            cb.clear()
            cb.addItem("Automatic", "")
            for o in opts:
                cb.addItem(nice_name(o), o)
                cb.setItemData(cb.count() - 1, o, Qt.ItemDataRole.ToolTipRole)
            set_combo(cb, self.p.get(k, ""))
            if not self.p.get(k) and cur:
                cb.setItemText(0, f"Automatic  ({nice_name(cur)})")
        if not self.p.get("unet_high") and not self.p.get("unet") and guess.get("models"):
            self.p["models"] = guess["models"]
            self.two.set(guess["models"], animate=False)
        for cb, key, dflt in ((self.sampler, "samplers", "euler"), (self.scheduler, "schedulers", "simple")):
            k = "sampler" if cb is self.sampler else "scheduler"
            opts = lists.get(key) or [self.p.get(k, dflt)]
            cb.clear()
            for o in opts:
                cb.addItem(o, o)
            set_combo(cb, self.p.get(k, dflt))
        wan_loras = list(lists.get("loras") or [])
        self.loras.set_options(wan_loras)
        self._loading = False
        self._update()

    def _resolved(self):
        """The settings with 'Automatic' models filled in (for the speed check and the size line)."""
        p = dict(self.p)
        g = self.link.lists.get("video_guess") or {}
        for k in ("unet_high", "unet_low", "unet", "unet_t2v", "clip", "vae", "interp_model"):
            p[k] = p.get(k) or g.get(k, "")
        return p

    # ------------------------------------------------------------------ the summary
    def _aspect_now(self):
        sz = None
        if self.p.get("start_image"):
            try:
                from .jobs import ref_size
                sz = ref_size(self.p["start_image"])
            except Exception:
                sz = None
        return sz[0] / sz[1] if sz else 2 / 3

    def _update(self):
        p = self._resolved()
        mode = W.video_mode(p)
        loop = p.get("loop", "loop")
        self.end_row.setVisible(loop == "pair" and mode == "i2v")
        self.aspects.setVisible(mode == "t2v")
        if mode == "t2v":
            t2v = p.get("unet_t2v")
            self.mode_note.setText("No start picture: text to video" + (f" with {nice_name(t2v)}." if t2v else
                                   " - needs a WAN T2V model (none found)."))
        else:
            self.mode_note.setText("")
        self.mode_note.setVisible(bool(self.mode_note.text()))
        two = p.get("models", "two") == "two"
        self.row_unet_high.setVisible(two)
        self.row_unet_low.setVisible(two)
        self.row_unet.setVisible(not two)
        self.loop_words.setVisible(loop == "loop")
        self.steady.setVisible(loop != "loop")          # (a seamless loop is always steadied)
        self.seam_row.setVisible(loop in ("loop", "crossfade"))
        pl = W.video_plan(p, self._aspect_now())
        if mode == "t2v" and loop in ("loop", "pair"):
            self.loop_line.setText(f"{W.LOOP[loop][1]} needs a start picture - it is made as Free.")
        else:
            self.loop_line.setText(W.LOOP[loop][2])
        frames = pl["frames"]
        fin = frames * 2 - 2 if pl["loop"] == "pingpong" else frames
        cut = "up to " if pl["loop"] == "loop" else "about "
        self.frames_lbl.setText(f"{frames} frames from WAN  →  {cut}{fin * pl['interp']} frames at {pl['fps']} fps"
                                f" ({fin / 16:.1f} s)")
        q = W.VIDEO_PRESET[p.get("preset", "fast")]
        self.preset_line.setText(f"{q[2]}\n{pl['steps']} steps ({pl['high_steps']} motion)  ·  guidance "
                                 f"{pl['cfg_high']:g}  ·  {pl['fps']} fps")
        self.size_prev.set(pl["w"], pl["h"], base=int((q[3]) ** 0.5), note=f"{frames} frames")
        self.speed_note.setText("speed model" if pl["fast"] else "full steps")
        self.speed_note.setToolTip("Lightning / Lightspeed / lightx2v found: 4 - 10 steps, guidance 1." if pl["fast"]
                                   else "No speed model or LoRA: 12 - 30 steps, guidance 3.5 (slower).")
        n = self.bar.count.value()
        est = jobs.estimate("video", W.work_units("video", pl, n))
        self.bar.info.setText(f"{pl['w']} × {pl['h']}  ·  {frames} frames  ·  " +
                              (f"≈ {human_time(est)}" if est else "time shown after the first video"))

    # ------------------------------------------------------------------ generate
    def generate(self, count=1):
        self._prompt_changed()
        p = copy.deepcopy(self.p)
        p["count"] = int(count)
        mode = W.video_mode(p)
        from .jobs import ref_ok
        try:
            ok = ref_ok(p.get("start_image"))
        except Exception:
            ok = False
        if mode == "i2v" and not ok:
            self.toast.emit("The start picture is missing - drop one in again.", "warn")
            return
        if mode == "t2v" and not (p.get("prompt") or "").strip():
            self.toast.emit("Without a start picture, describe the whole scene in the prompt.", "warn")
            return
        if p.get("loop") == "pair" and mode == "i2v" and not p.get("end_image"):
            self.toast.emit("Start→End needs an end picture (or pick another loop mode).", "warn")
            return
        from .jobs import is_private
        private = is_private(p)
        if private and not self.app.ensure_vault("Unlock the vault: private videos go straight into it."):
            return
        if not private:
            remember_prompt(p.get("prompt"))
        pl = W.video_plan(self._resolved(), self._aspect_now())
        title = "Private video" if private else ((p.get("prompt") or "").strip().replace("\n", " ")[:70] or
                                                 nice_name(p.get("start_image")) or "Video")
        self.queue.add("video", p, title, thumb=None if private else (p.get("start_image") or None),
                       units=W.work_units("video", pl, count))
        self.bar.flash()
        if not self.link.running() and cfg.get("auto_start"):
            self.toast.emit("Starting ComfyUI - the first video takes a little longer.", "info")

    def set_start(self, path):
        self.start.set_path(path)
        if str(path).startswith(("vault:", "mem:")):
            self.bar.set_private(True)
            self._set("private", True)
        self.toast.emit("Picture set - describe the motion and press Generate.", "ok")

    def use_settings(self, params):
        self.load(dict(params))
        self.toast.emit("Settings loaded.", "ok")

    # ------------------------------------------------------------------ live
    def _job_changed(self, jid):
        j = self.queue.job(jid)
        if j and j["kind"] == "video" and j["status"] == "running":
            self.pane.live(jid, j.get("progress", 0), j.get("text", ""))

    def _job_preview(self, jid, qi):
        j = self.queue.job(jid)
        if j and j["kind"] == "video":
            self.pane.live_preview(qi)

    def _queue_changed(self):
        cur = self.queue.running()
        if self.pane.live_job and (cur is None or cur["id"] != self.pane.live_job):
            self.pane.live_end()
        if cur and cur["kind"] == "video":
            self.pane.live(cur["id"], cur.get("progress", 0), cur.get("text", ""))
