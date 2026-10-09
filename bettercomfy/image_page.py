"""The IMAGE page: text (or a picture) to picture with SDXL / Pony / Illustrious / SD 1.5 checkpoints."""
import copy
import json
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

from . import jobs, media, workflows as W
from .components import GenerateBar, LoraStack, PromptCard, ResultPane, SeedBox, UserPresets, remember_prompt
from .config import cfg
from .widgets import (Combo, AspectPreview, Card, ChipBox, Collapsible, ImageDrop, PresetPicker, Scroll, Segmented, Slider,
                      ToggleRow, chip, field, hrow, icon_button, label, nice_name, quiet, set_combo, human_time)


class ImagePage(QWidget):
    title = "Image"
    subtitle = "Text to picture  ·  picture to picture"
    to_video = Signal(str)                 # a picture to animate
    toast = Signal(str, str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link, self.queue, self.history = app.link, app.queue, app.history
        self.p = copy.deepcopy(W.IMAGE_DEFAULT)
        self.p.update(cfg.get("image_state") or {})
        self._loading = False
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # ---------------------------------------------------------------- the settings column
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
        self.bar = GenerateBar("image", [("Compare settings…", "gallery", self.compare),
                                         ("Start pictures from a folder…", "folder", self.batch_folder)])
        self.bar.generate.connect(self.generate)
        self.bar.private_changed.connect(lambda on: self._set("private", on))
        sv.addWidget(self.bar)

        # my presets
        self.presets = UserPresets("image", lambda: dict(self.p), skip=("init_image", "source_image", "mask_image",
                                                                        "op", "variants", "count"))
        self.presets.apply.connect(self._apply_preset)
        sc.add(self.presets)

        # model
        self.ckpt = Combo()
        self.ckpt.currentIndexChanged.connect(self._pick_model)
        self.family = Combo()
        self.family.addItem("Auto", "auto")
        for k in W.FAMILY_ORDER:
            self.family.addItem(W.FAMILIES[k]["name"], k)
        self.family.currentIndexChanged.connect(lambda _=0: self._set("family", self.family.currentData()))
        self.family.setFixedWidth(118)
        self.fam_badge = label("", "AccentBadge")
        self.model_note = label("", "Faint", wrap=True)
        reload_btn = icon_button("refresh", self.link.refresh_models, "Read the model lists again", size=15)
        self.parts = Collapsible("Engine parts", False, "The text encoder(s) and VAE a Flux / Qwen / Anima / Z-Image "
                                                        "model needs (found by themselves)")
        self.te1, self.te2, self.evae = Combo(), Combo(), Combo()
        for cb, k in ((self.te1, "te1"), (self.te2, "te2"), (self.evae, "evae")):
            cb.currentIndexChanged.connect(lambda _=0, cb=cb, k=k: self._set(k, cb.currentData() or ""))
        self.te2_row = field("Encoder 2", self.te2, "Flux uses two: clip_l and t5xxl.", label_w=76)
        self.guidance = QDoubleSpinBox()
        self.guidance.setRange(0, 10)
        self.guidance.setSingleStep(0.5)
        self.guidance.setDecimals(1)
        self.guidance.setSpecialValueText("Preset (3.5)")
        self.guidance.valueChanged.connect(lambda v: self._set("guidance", float(v)))
        self.guid_row = field("Flux guidance", self.guidance, "How closely Flux follows the prompt (2 - 4).", label_w=76)
        self.dtype = Segmented([("default", "As saved", "As the file is"), ("fp8_e4m3fn", "fp8", "Less graphics memory")],
                               lambda v: self._set("dtype", v), height=30)
        self.dtype_row = field("Weights", self.dtype, label_w=76)
        self.parts.add(field("Encoder", self.te1, label_w=76), self.te2_row, field("VAE", self.evae, label_w=76),
                       self.guid_row, self.dtype_row)
        c = Card("Model", [self.fam_badge, reload_btn])
        c.add(self.ckpt, field("Family", hrow(self.family, None), "Decides the base size, quality words and "
                                                                    "defaults. Auto reads it from the file.", label_w=60),
              self.model_note, self.parts)
        sc.add(c)

        # prompt
        self.prompt = PromptCard("Prompt", "Describe the picture…  e.g. 1girl, silver hair, night city, neon rain, "
                                           "cinematic lighting", neg_toggle_text="Add the model's usual words to avoid",
                                 neg_sub="Typical negatives for this model family, added after yours")
        self.prompt.changed.connect(self._prompt_changed)
        self.prompt.submit.connect(lambda: self.generate(self.bar.count.value()))
        self.quality_tags = ToggleRow("Quality words", "", True, lambda v: self._set("quality_tags", v))
        self.prompt.body.insertWidget(2, self.quality_tags)
        sc.add(self.prompt)

        # loras
        self.loras = LoraStack("image", self.prompt, self._target, self.link.install)
        self.loras.changed.connect(lambda: self._set("loras", self.loras.value()))
        sc.add(Card("LoRAs").add(self.loras))

        # size
        self.aspects = ChipBox(6)
        self._aspect_btns = {}
        for key, a, b in W.ASPECTS:
            btn = chip(key, None, True, f"{a}:{b}")
            btn.clicked.connect(lambda _=False, k=key: self._aspect(k))
            self.aspects.add(btn)
            self._aspect_btns[key] = btn
        cbtn = chip("Custom", None, True, "Your own width and height")
        cbtn.clicked.connect(lambda: self._aspect("custom"))
        self.aspects.add(cbtn)
        self._aspect_btns["custom"] = cbtn
        self.mp = Slider(0.5, 1.6, 0.05, 2, 1.0, lambda v: self._set("megapixels", v), " MP", width=78)
        self.mp_row = field("Pixels", self.mp, "How many pixels compared to the model's native size (1.0 = what it "
                                               "was trained on, best).", label_w=60)
        self.cw = QSpinBox()
        self.ch = QSpinBox()
        for sb in (self.cw, self.ch):
            sb.setRange(256, 4096)
            sb.setSingleStep(64)
            sb.setSuffix(" px")
        self.cw.valueChanged.connect(lambda v: self._set("width", int(v)))
        self.ch.valueChanged.connect(lambda v: self._set("height", int(v)))
        swap = icon_button("swap", self._swap, "Swap width and height", size=16)
        self.custom_row = hrow(label("W", "Muted"), self.cw, label("H", "Muted"), self.ch, swap, spacing=8)
        self.preview = AspectPreview(140)
        c = Card("Size")
        c.add(self.aspects, self.mp_row, self.custom_row, self.preview)
        sc.add(c)

        # quality
        self.preset = PresetPicker([(x[0], x[1], x[2]) for x in W.IMAGE_PRESETS], self._preset)
        self.preset_line = label("", "Muted", wrap=True)
        self.adv = Collapsible("Advanced", False, "The fine controls - the preset sets all of them unless you do")
        self.steps = QSpinBox()
        self.steps.setRange(0, 150)
        self.steps.setSpecialValueText("Preset")
        self.steps.valueChanged.connect(lambda v: self._set("steps", int(v)))
        self.cfgs = QDoubleSpinBox()
        self.cfgs.setRange(0, 30)
        self.cfgs.setSingleStep(0.5)
        self.cfgs.setDecimals(1)
        self.cfgs.setSpecialValueText("Preset")
        self.cfgs.valueChanged.connect(lambda v: self._set("cfg", float(v)))
        self.sampler = Combo()
        self.sampler.currentIndexChanged.connect(lambda _=0: self._set("sampler", self.sampler.currentData() or ""))
        self.scheduler = Combo()
        self.scheduler.currentIndexChanged.connect(lambda _=0: self._set("scheduler", self.scheduler.currentData() or ""))
        self.clip_skip = QSpinBox()
        self.clip_skip.setRange(0, 12)
        self.clip_skip.setSpecialValueText("Family")
        self.clip_skip.valueChanged.connect(lambda v: self._set("clip_skip", int(v)))
        self.hires = Segmented([("preset", "Preset", "On for Quality and Best"), ("on", "On", "Always"),
                                ("off", "Off", "Never")], lambda v: self._set("hires", v), height=30)
        self.hires_scale = Slider(1.1, 2.5, 0.05, 2, 1.5, lambda v: self._set("hires_scale", v), "×", width=70)
        self.hires_den = Slider(0.1, 0.8, 0.01, 2, 0.4, lambda v: self._set("hires_denoise", v), width=70)
        self.upscaler = Combo()
        self.upscaler.currentIndexChanged.connect(lambda _=0: self._set("upscale_model", self.upscaler.currentData()))
        self.vae = Combo()
        self.vae.currentIndexChanged.connect(lambda _=0: self._set("vae", self.vae.currentData() or ""))
        lw = 92
        self.clip_row = field("Clip skip", self.clip_skip, "2 for Pony / Illustrious / most anime models (set by the "
                                                          "family).", label_w=lw)
        self.vae_row = field("VAE", self.vae, "From the model (usual), or another VAE.", label_w=lw)
        self.adv.add(field("Steps", self.steps, "More = cleaner and slower. 0 = the preset's.", label_w=lw),
                     field("Guidance", self.cfgs, "CFG: how strictly it follows the prompt. 0 = the preset's.", label_w=lw),
                     field("Sampler", self.sampler, label_w=lw), field("Scheduler", self.scheduler, label_w=lw),
                     self.clip_row,
                     field("Upscale pass", self.hires, "Draws the picture again bigger, adding fine detail.", label_w=lw),
                     field("Scale", self.hires_scale, label_w=lw),
                     field("Strength", self.hires_den, "How much the upscale pass may change (0.3 - 0.5).", label_w=lw),
                     field("Upscaler", self.upscaler, "The model that enlarges the picture first (an anime one for "
                                                      "anime).", label_w=lw),
                     self.vae_row)
        c = Card("Quality")
        c.add(self.preset, self.preset_line, self.adv)
        sc.add(c)

        # seed + start picture
        self.seed = SeedBox()
        self.seed.changed.connect(lambda: self._set("seed", self.seed.value()))
        self.init = ImageDrop("Drop a picture to start from (optional)", 150)
        self.init.changed.connect(lambda path: self._set("init_image", path))
        self.denoise = Slider(0.05, 1.0, 0.01, 2, 0.6, lambda v: self._set("denoise", v), width=70)
        self.init_box = Collapsible("Start from a picture", False, "Picture to picture (img2img)")
        self.init_box.add(self.init, field("Change", self.denoise, "How much of the picture may change: low keeps it, "
                                                                     "high only keeps the idea.", label_w=60))
        c = Card("Seed & start picture")
        c.add(self.seed, self.init_box)
        sc.add(c)
        sc.end()
        h.addWidget(side)

        # ---------------------------------------------------------------- the picture
        self.pane = ResultPane("image", self.history, [
            ("animate", "Animate", "video", "Turn this picture into a video (Video page)"),
            ("reuse", "Reuse", "refresh", "Load this picture's settings (and seed)"),
            ("upscale", "Upscale", "scale", "The same picture bigger and sharper"),
            ("inpaint", "Edit", "edit", "Paint over a part and redraw only that (mask brush)"),
            ("start", "Vary", "image", "Use as the start picture (picture to picture)"),
            ("copy", "", "copy", "Copy the picture"),
            ("folder", "", "folder", "Show in its folder"),
            ("delete", "", "trash", "Move to the recycle bin")],
            "Nothing made yet", "Write a prompt on the left and press Generate.  Results appear here, live while "
                                "they are drawn.")
        self.pane.action.connect(self._action)
        self.pane.dropped.connect(self.drop_file)
        h.addWidget(self.pane, 1)

        self.link.models.connect(self._models)
        self.queue.job_changed.connect(self._job_changed)
        self.queue.preview.connect(self._job_preview)
        self.queue.changed.connect(self._queue_changed)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save)
        self.load(self.p)
        if self.link.lists:
            self._models(self.link.lists)

    # ------------------------------------------------------------------ state
    def _set(self, k, v):
        if self._loading:
            return
        self.p[k] = v
        if k in ("ckpt", "family", "aspect", "megapixels", "width", "height", "preset", "steps", "cfg", "hires",
                 "hires_scale", "loras", "init_image", "sampler", "scheduler"):
            self._update()
        self._save_timer.start()

    def _save(self):
        keep = dict(self.p)
        cfg.set("image_state", keep)

    def load(self, p):
        self._loading = True
        self.p = copy.deepcopy(W.IMAGE_DEFAULT)
        self.p.update(p or {})
        p = self.p
        self.prompt.set_text(p.get("prompt", ""))
        quiet(self.prompt.neg_edit, self.prompt.neg_edit.setPlainText, p.get("negative", ""))
        self.prompt.neg_toggle.set(p.get("auto_negative", True))
        self.quality_tags.set(p.get("quality_tags", True))
        set_combo(self.family, p.get("family", "auto"))
        set_combo(self.ckpt, f"{p.get('model_src', 'ckpt')}|{p.get('ckpt', '')}")
        for cb, k in ((self.te1, "te1"), (self.te2, "te2"), (self.evae, "evae")):
            set_combo(cb, p.get(k, ""))
        quiet(self.guidance, self.guidance.setValue, float(p.get("guidance") or 0))
        self.dtype.set(p.get("dtype", "default"), animate=False)
        self.loras.set_items(p.get("loras") or [])
        self._aspect_ui(p.get("aspect") if p.get("size_mode") != "custom" else "custom")
        self.mp.set(float(p.get("megapixels") or 1.0))
        quiet(self.cw, self.cw.setValue, int(p.get("width") or 832))
        quiet(self.ch, self.ch.setValue, int(p.get("height") or 1216))
        self.preset.set(p.get("preset", "balanced"), animate=False)
        quiet(self.steps, self.steps.setValue, int(p.get("steps") or 0))
        quiet(self.cfgs, self.cfgs.setValue, float(p.get("cfg") or 0))
        set_combo(self.sampler, p.get("sampler", ""))
        set_combo(self.scheduler, p.get("scheduler", ""))
        quiet(self.clip_skip, self.clip_skip.setValue, int(p.get("clip_skip") or 0))
        self.hires.set(p.get("hires", "preset"), animate=False)
        self.hires_scale.set(float(p.get("hires_scale") or 1.5))
        self.hires_den.set(float(p.get("hires_denoise") or 0.4))
        set_combo(self.upscaler, p.get("upscale_model", "auto"))
        set_combo(self.vae, p.get("vae", ""))
        self.seed.set(p.get("seed", -1))
        self.init.set_path(p.get("init_image", ""), emit=False)
        if p.get("init_image"):
            self.init_box.set_open(True)
        self.denoise.set(float(p.get("denoise", 0.6)))
        self.bar.set_private(p.get("private", False))
        self._loading = False
        self._update()

    def _prompt_changed(self):
        if self._loading:
            return
        self.p["prompt"] = self.prompt.text()
        self.p["negative"] = self.prompt.neg_edit.toPlainText()
        self.p["auto_negative"] = self.prompt.neg_toggle.isChecked()
        self._save_timer.start()

    # ------------------------------------------------------------------ model lists
    def _models(self, lists):
        self._loading = True
        cur = f"{self.p.get('model_src', 'ckpt')}|{self.p.get('ckpt') or ''}" if self.p.get("ckpt") else ""
        pics = self.link.picture_models()
        self.ckpt.clear()
        if not pics:
            self.ckpt.addItem("No picture models found", "")
        for name, src, k in pics:
            fam = W.FAMILIES[W.guess_family(name, k)]["name"]
            self.ckpt.addItem(f"{nice_name(name)}   ·  {fam}", f"{src}|{name}")
            self.ckpt.setItemData(self.ckpt.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
        if cur and self.ckpt.findData(cur) < 0:
            self.ckpt.addItem(f"{nice_name(cur.split('|', 1)[1])}   (not found)", cur)
        if not cur and pics:
            pref = next((x for x in pics if W.guess_family(x[0], x[2]) == "illustrious"), pics[0])
            cur = f"{pref[1]}|{pref[0]}"
            self.p["model_src"], self.p["ckpt"] = pref[1], pref[0]
        set_combo(self.ckpt, cur)
        for cb, key, opts in ((self.te1, "te1", lists.get("clip") or []), (self.te2, "te2", lists.get("clip") or []),
                              (self.evae, "evae", lists.get("vae") or [])):
            cb.clear()
            cb.addItem("Automatic", "")
            for o in opts:
                cb.addItem(nice_name(o), o)
            set_combo(cb, self.p.get(key, ""))
        for cb, opts, key, first in ((self.sampler, lists.get("samplers") or [], "sampler", "Preset"),
                                     (self.scheduler, lists.get("schedulers") or [], "scheduler", "Preset")):
            cb.clear()
            cb.addItem(first, "")
            v = self.p.get(key, "")
            for o in opts or ([v] if v else []):
                cb.addItem(o, o)
            set_combo(cb, v)
        self.upscaler.clear()
        self.upscaler.addItem("Auto (best fit)", "auto")
        self.upscaler.addItem("None - stretch the latent", "latent")
        for u in lists.get("upscale") or []:
            self.upscaler.addItem(nice_name(u), u)
        set_combo(self.upscaler, self.p.get("upscale_model", "auto"))
        self.vae.clear()
        self.vae.addItem("From the model", "")
        for x in lists.get("vae") or []:
            if "wan" not in x.lower():
                self.vae.addItem(nice_name(x), x)
        set_combo(self.vae, self.p.get("vae", ""))
        self.loras.set_options(lists.get("loras") or [])
        self._loading = False
        self._update()
        if not self.loras.items:
            self.loras.rebuild()

    def _kind(self):
        return self.link.kind_of(self.p.get("ckpt"), self.p.get("model_src", "ckpt"))

    def _pick_model(self, _i=0):
        d = self.ckpt.currentData() or ""
        if self._loading or "|" not in d:
            return
        src, name = d.split("|", 1)
        self.p["model_src"] = src
        self._set("ckpt", name)
        self.loras.rebuild()

    def _apply_preset(self, d):
        p = dict(self.p)
        p.update(d)
        self.load(p)
        self.toast.emit("Preset loaded.", "ok")

    def _target(self):
        return W.family_of(self.p, self._kind())

    # ------------------------------------------------------------------ size / preset
    def _aspect(self, key):
        self._aspect_ui(key)
        if key == "custom":
            if self.p.get("size_mode") != "custom":
                pl = W.image_plan(self.p, self._kind())
                quiet(self.cw, self.cw.setValue, pl["w"])
                quiet(self.ch, self.ch.setValue, pl["h"])
                self.p.update(width=pl["w"], height=pl["h"])
            self._set("size_mode", "custom")
        else:
            self.p["size_mode"] = "aspect"
            self._set("aspect", key)

    def _aspect_ui(self, key):
        for k, b in self._aspect_btns.items():
            quiet(b, b.setChecked, k == key)
        custom = key == "custom"
        self.custom_row.setVisible(custom)
        self.mp_row.setVisible(not custom)

    def _swap(self):
        w, h = self.cw.value(), self.ch.value()
        self.cw.setValue(h)
        self.ch.setValue(w)

    def _preset(self, key):
        self._set("preset", key)

    def _update(self):
        kind = self._kind()
        fam = W.family_of(self.p, kind)
        F = W.FAMILIES[fam]
        pl = W.image_plan(self.p, kind)
        auto = (self.p.get("family") or "auto") == "auto"
        self.fam_badge.setText(F["name"] + (" · auto" if auto else ""))
        notes = []
        if pl["fast"]:
            notes.append("Speed model / LoRA found: few steps and low guidance are used.")
        if kind == "other":
            notes.append("This file does not look like a picture model this app knows.")
        eng = F["engine"]
        self.parts.setVisible(eng != "sd")
        self.clip_row.setVisible(eng == "sd")
        self.vae_row.setVisible(eng == "sd")
        self.te2_row.setVisible(eng == "flux")
        self.guid_row.setVisible(eng == "flux")
        self.dtype_row.setVisible(self.p.get("model_src") == "unet")
        if eng != "sd":
            te1, te2, evae, miss = W.engine_parts(fam, self.p, self.link.lists or {})
            for cb, auto in ((self.te1, te1), (self.te2, te2), (self.evae, evae)):
                if cb.count():
                    cb.setItemText(0, f"Automatic  ({nice_name(auto)})" if auto else "Automatic  (none found)")
            if miss:
                notes.append(f"Missing its {' and '.join(miss)} - {F['need']}.")
                self.parts.set_open(True)
        self.model_note.setText("  ".join(notes))
        self.model_note.setVisible(bool(notes))
        self.quality_tags.text.setText("Quality words" + (" (none for this family)" if not F["quality"] else ""))
        if hasattr(self.quality_tags, "sub"):
            self.quality_tags.sub.setText(F["quality"] or "Plain SDXL needs none.")
        pr = W.IMAGE_PRESET[self.p.get("preset", "balanced")]
        hires = f"  ·  upscale ×{pl['hires'][0]:g}" if pl["hires"] else ""
        self.preset_line.setText(f"{pr[2]}\n{pl['steps']} steps  ·  {pl['sampler']} / {pl['scheduler']}  ·  "
                                 f"guidance {pl['cfg']:g}{hires}")
        self.preview.set(pl["w"], pl["h"], pl["final_w"], pl["final_h"], F["base"],
                         "picture to picture" if pl["img2img"] else "")
        on = self.p.get("hires", "preset")
        for w in (self.hires_scale, self.hires_den):
            w.setEnabled(on == "on")
        self._update_bar(pl)

    def _update_bar(self, pl=None):
        pl = pl or W.image_plan(self.p, self._kind())
        n = self.bar.count.value()
        units = W.work_units("image", pl, n)
        est = jobs.estimate("image", units)
        parts = [f"{pl['final_w']} × {pl['final_h']}", f"{pl['steps']} steps"]
        parts.append(f"≈ {human_time(est)}" if est else "time shown after the first picture")
        self.bar.info.setText("  ·  ".join(parts))

    # ------------------------------------------------------------------ generate
    def generate(self, count=1):
        self._prompt_changed()
        p = copy.deepcopy(self.p)
        p["count"] = int(count)
        if not p.get("ckpt"):
            self.toast.emit("Pick a picture model first (Model card).", "warn")
            return
        fam = W.family_of(p, self._kind())
        miss = W.engine_parts(fam, p, self.link.lists or {})[3]
        if miss:
            self.toast.emit(f"{W.FAMILIES[fam]['name']} needs its {' and '.join(miss)} - see the Model card.", "warn")
            return
        if not (p.get("prompt") or "").strip() and not p.get("init_image"):
            self.toast.emit("Write a prompt first - what should the picture show?", "warn")
            self.prompt.edit.setFocus()
            return
        from .jobs import is_private
        private = is_private(p)
        if private and not self.app.ensure_vault("Unlock the vault: private pictures go straight into it."):
            return
        if not private:
            remember_prompt(p.get("prompt"))
        kind = self._kind()
        pl = W.image_plan(p, kind)
        title = "Private picture" if private else (p.get("prompt") or "Picture").strip().replace("\n", " ")[:70]
        self.queue.add("image", p, title, thumb=None if private else (p.get("init_image") or None),
                       units=W.work_units("image", pl, count), ckpt_kind=kind)
        self.bar.flash()
        if not self.link.running() and cfg.get("auto_start"):
            self.toast.emit("Starting ComfyUI - the first picture takes a little longer.", "info")

    # ------------------------------------------------------------------ live
    def _job_changed(self, jid):
        j = self.queue.job(jid)
        if j and j["kind"] == "image" and j["status"] == "running":
            self.pane.live(jid, j.get("progress", 0), j.get("text", ""))

    def _job_preview(self, jid, qi):
        j = self.queue.job(jid)
        if j and j["kind"] == "image":
            self.pane.live_preview(qi)

    def _queue_changed(self):
        cur = self.queue.running()
        if self.pane.live_job and (cur is None or cur["id"] != self.pane.live_job):
            self.pane.live_end()
        if cur and cur["kind"] == "image":
            self.pane.live(cur["id"], cur.get("progress", 0), cur.get("text", ""))
        self._update_bar()

    # ------------------------------------------------------------------ actions
    def _action(self, key, e):
        self.app.result_action(key, e, self)

    def use_settings(self, params):
        p = dict(params)
        self.load(p)
        self.toast.emit("Settings loaded - the same seed makes the same picture.", "ok")

    # ------------------------------------------------------------------ more ways
    def compare(self):
        from .tools import CompareDialog
        self._prompt_changed()
        if not (self.p.get("prompt") or "").strip():
            self.toast.emit("Write a prompt first - the comparison uses it.", "warn")
            return
        d = CompareDialog(self.window(), self.p, self.link.lists or {}, self.link.picture_models())
        if not d.exec() or not d.variants:
            return
        p = copy.deepcopy(self.p)
        p["variants"] = d.variants
        if int(p.get("seed", -1)) < 0:
            import random
            p["seed"] = random.randint(1, 2 ** 31 - 1)
        kind = self._kind()
        pl = W.image_plan(p, kind)
        self.queue.add("image", p, "Compare: " + ", ".join(v["label"] for v in d.variants)[:80],
                       units=W.work_units("image", pl, len(d.variants)), ckpt_kind=kind)
        self.toast.emit(f"{len(d.variants)} pictures queued - they open side by side when done.", "ok")

    def batch_folder(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        d = QFileDialog.getExistingDirectory(self, "A folder of start pictures", cfg.get("last_pick_dir") or "")
        if not d:
            return
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(media.IMAGE_EXT))
        if not files:
            self.toast.emit("No pictures in that folder.", "warn")
            return
        if QMessageBox.question(self, "Start pictures", f"Queue {len(files)} pictures, each starting from one of the "
                                                         f"pictures (Change: {self.p.get('denoise', 0.6):.2f})?")                 != QMessageBox.StandardButton.Yes:
            return
        kind = self._kind()
        for f in files:
            p = copy.deepcopy(self.p)
            p.update(init_image=f, count=1)
            pl = W.image_plan(p, kind)
            self.queue.add("image", p, nice_name(f), thumb=f, units=W.work_units("image", pl), ckpt_kind=kind)
        self.toast.emit(f"{len(files)} pictures queued.", "ok")

    def drop_file(self, f):
        """A picture dropped on the big view: its settings when it has them, else it becomes the start picture."""
        txt = media.read_png_text(f) if f.lower().endswith(".png") else {}
        if txt.get("bettercomfy"):
            try:
                d = json.loads(txt["bettercomfy"])
                self.load(dict(d.get("params") or {}, seed=d.get("seed", -1)))
                self.toast.emit("Settings read from the picture.", "ok")
                return
            except ValueError:
                pass
        if txt.get("parameters"):
            p = dict(self.p)
            p.update(media.parse_a1111(txt["parameters"]))
            self.load(p)
            self.toast.emit("Prompt and settings read from the picture.", "ok")
            return
        self.set_start(f)
        self.toast.emit("Set as the start picture.", "ok")

    def set_start(self, path):
        self.init.set_path(path)
        self.init_box.set_open(True)
