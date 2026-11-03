"""The TRAIN tab: pictures in, a LoRA of your own out, with ComfyUI's built-in training nodes (no add-on needed). The
result lands in the LoRA library and as a character to pick on the Image page."""
import glob
import os
import re
import shutil
import threading
import time
import uuid

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout,
                               QWidget)

from . import charlib, comfy, loras, workflows as W
from .characters_page import tile
from .config import cfg
from .widgets import Card, Combo, ImageDrop, Scroll, button, field, hrow, human_time, label, nice_name

PICS = (".png", ".jpg", ".jpeg", ".webp")
# steps a picture, at least, at most, rank
EFFORT = {"quick": (30, 150, 300, 16), "good": (60, 300, 800, 16), "strong": (100, 500, 1500, 32)}


def steps_for(effort, n):
    per, least, most, _rank = EFFORT[effort]
    return min(most, max(least, per * n))


def prepare_dataset(pics, folder, size, trigger):
    """Square copies (fitted, the rest filled with the picture's corner colour) with captions: the trigger word plus a
    caption file next to the picture, if there is one."""
    os.makedirs(folder, exist_ok=True)
    for i, src in enumerate(pics):
        with Image.open(src) as im:
            im = im.convert("RGB")
            fill = im.getpixel((0, 0))
            im.thumbnail((size, size), Image.LANCZOS)
            out = Image.new("RGB", (size, size), fill)
            out.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
            out.save(os.path.join(folder, f"{i}.png"))
        words = trigger
        side = os.path.splitext(src)[0] + ".txt"
        if os.path.isfile(side):
            with open(side, encoding="utf-8", errors="replace") as fh:
                words = f"{trigger}, {fh.read().strip()}"
        with open(os.path.join(folder, f"{i}.txt"), "w", encoding="utf-8") as fh:
            fh.write(words)


def graph(ckpt, folder, steps, rank, prefix):
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "2": {"class_type": "LoadImageTextDataSetFromFolder", "inputs": {"folder": folder}},
            "3": {"class_type": "MakeTrainingDataset",
                  "inputs": {"images": ["2", 0], "texts": ["2", 1], "vae": ["1", 2], "clip": ["1", 1]}},
            "4": {"class_type": "TrainLoraNode",
                  "inputs": {"model": ["1", 0], "latents": ["3", 0], "positive": ["3", 1], "batch_size": 1,
                             "grad_accumulation_steps": 1, "steps": steps, "learning_rate": 0.0003, "rank": rank,
                             "optimizer": "AdamW", "loss_function": "MSE", "seed": 1, "training_dtype": "bf16",
                             "lora_dtype": "bf16", "quantized_backward": False,
                             "algorithm": "LoRA", "gradient_checkpointing": True, "checkpoint_depth": 3,
                             "offloading": True, "existing_lora": "[None]", "bucket_mode": False, "bypass_mode": False}},
            "5": {"class_type": "SaveLoRA", "inputs": {"lora": ["4", 0], "prefix": prefix, "steps": ["4", 2]}}}


class TrainPage(QWidget):
    title = "Train"
    subtitle = "Train a LoRA of your own from pictures, on your own PC"
    _prog = Signal(float, str)
    _end = Signal(object)

    def __init__(self, app):
        super().__init__()
        self.app, self.link = app, app.link
        self.pics = []
        self.char_id = None
        self.ev = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(24, 16, 16, 12)
        lv.setSpacing(12)
        self.drop = ImageDrop("Drop pictures here, or click to choose", 84)
        self.drop.changed.connect(lambda path: path and self._add([path]))
        self.drop.many.connect(self._add)
        lv.addWidget(self.drop)
        lv.addWidget(hrow(button("Add a folder…", self._folder, "Ghost", "plus"),
                          button("Clear", self._clear, "Ghost", "trash"), None,
                          label("Different poses and angles work best; 10 to 30 pictures are plenty.", "Faint"), spacing=6))
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setIconSize(QSize(120, 120))
        self.grid.setGridSize(QSize(132, 132))
        lv.addWidget(self.grid, 1)
        h.addWidget(left, 1)

        side = QWidget()
        side.setObjectName("Panel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(400)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        det = Scroll((18, 18, 16, 18), 12)
        sv.addWidget(det)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Its name")
        self.trigger = QLineEdit()
        self.trigger.setPlaceholderText("A made-up word, like zzmascot")
        self.base = Combo()
        self.effort = Combo()
        for text, key in (("Quick try", "quick"), ("Good", "good"), ("Strong", "strong")):
            self.effort.addItem(text, key)
        self.effort.setCurrentIndex(1)
        self.size = Combo()
        for text, key in (("768: faster, less memory", 768), ("1024: sharper, slower", 1024)):
            self.size.addItem(text, key)
        c = Card("Your LoRA")
        c.add(field("Name", self.name, label_w=64), field("Trigger", self.trigger, label_w=64),
              field("Model", self.base, "The model it is trained on, and works best with.", label_w=64),
              field("Effort", self.effort, label_w=64), field("Size", self.size, label_w=64))
        self.plan = label("", "Faint", wrap=True)
        c.add(self.plan)
        self.effort.currentIndexChanged.connect(lambda _i: self._plan())
        self.size.currentIndexChanged.connect(lambda _i: self._plan())
        det.add(c)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.status = label("", "Muted", wrap=True)
        self.start_btn = button("Start training", self._start, "Accent", "dumbbell")
        self.stop_btn = button("Stop", self._stop, "Ghost", "close")
        self.stop_btn.hide()
        self.bar.hide()
        det.add(hrow(self.start_btn, self.stop_btn, None, spacing=6))
        det.add(self.bar)
        det.add(self.status)
        det.end()
        h.addWidget(side)
        self._prog.connect(self._progress)
        self._end.connect(self._finished)
        self.link.models.connect(lambda _l: self._fill_base())

    def showEvent(self, e):
        super().showEvent(e)
        self._fill_base()
        self._plan()

    def _plan(self):
        n = len(self.pics)
        if not n:
            self.plan.setText("Training is heavy work for a graphics card and can take a long time.")
            return
        steps = steps_for(self.effort.currentData(), n)
        speed = (cfg.get("train_speed") or {}).get(str(self.size.currentData()))
        when = f"about {human_time(speed * steps)}" if speed else "the time shows once it runs, and it can take an hour or more"
        self.plan.setText(f"{n} pictures, {steps} steps: {when}.")

    def _fill_base(self):
        keep = self.base.currentData()
        self.base.blockSignals(True)
        self.base.clear()
        for name, src, kind in self.link.picture_models():
            if src == "ckpt" and W.FAMILIES.get(W.guess_family(name, kind), {}).get("engine") == "sd":
                self.base.addItem(nice_name(name), name)
        i = self.base.findData(keep)
        if i >= 0:
            self.base.setCurrentIndex(i)
        self.base.blockSignals(False)

    # ---- pictures
    def from_character(self, cid):
        c = charlib.get(cid)
        if c:
            self.char_id = cid
            self.name.setText(c["name"])
            self.trigger.setText(c.get("trigger") or "")
            self._clear()
            self._add(c.get("refs") or [])

    def _add(self, paths):
        for p in paths:
            if p not in self.pics and p.lower().endswith(PICS) and os.path.isfile(p):
                self.pics.append(p)
                self.grid.addItem(QListWidgetItem(tile(p, 240), ""))
        self.drop.set_path("", emit=False)
        self._plan()

    def _folder(self):
        d = QFileDialog.getExistingDirectory(self, "A folder of pictures")
        if d:
            self._add(sorted(os.path.join(d, f) for f in os.listdir(d)))

    def _clear(self):
        self.pics = []
        self.grid.clear()
        self._plan()

    # ---- training
    def _start(self):
        name, trigger, ckpt = self.name.text().strip(), self.trigger.text().strip(), self.base.currentData()
        ins = self.link.install()
        if not (name and trigger and ckpt and self.pics and ins):
            self.app.toast("It needs pictures, a name, a trigger word and a model.", "warn")
            return
        if self.app.queue.active_count():
            self.app.toast("Let the queue finish first: training needs the whole graphics card.", "warn")
            return
        effort = self.effort.currentData()
        size = 512 if self.link.kind_of(ckpt) == "sd15" else self.size.currentData()
        self.ev = threading.Event()
        self.start_btn.hide()
        self.stop_btn.show()
        self.bar.show()
        self.bar.setValue(0)
        self.status.setText("Getting ready…")
        job = dict(name=name, trigger=trigger, ckpt=ckpt, ins=ins, size=size, pics=list(self.pics), effort=effort,
                   char=self.char_id, ev=self.ev)
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def _stop(self):
        if self.ev:
            self.ev.set()
            self.status.setText("Stopping…")

    def _work(self, job):
        try:
            res = self._train(job)
        except BaseException as ex:         # noqa: B036 - stopped too
            res = ex
        try:
            self._end.emit(res)
        except RuntimeError:
            pass

    def _train(self, job):
        ins, ev = job["ins"], job["ev"]
        url = self.link.ensure(lambda t: self._prog.emit(0.0, t), ev.is_set)
        c = comfy.Client(url)
        if not c.has("TrainLoraNode"):
            raise comfy.ComfyError("This ComfyUI has no training yet. Update ComfyUI and try again.")
        input_dir = comfy.sub_dir(ins, "input")
        for old in glob.glob(os.path.join(input_dir, "bc_train_*")):     # what a killed run left behind
            shutil.rmtree(old, ignore_errors=True)
        tag = uuid.uuid4().hex[:10]
        folder = f"bc_train_{tag}"
        ds = os.path.join(input_dir, folder)
        steps = steps_for(job["effort"], len(job["pics"]))
        rank = EFFORT[job["effort"]][3]
        t0 = time.time()

        def prog(f, _text):
            left = (time.time() - t0) * (1 - f) / f if f > 0.01 else 0
            more = f"  ·  about {max(1, round(left / 60))} min left" if left else ""
            self._prog.emit(f, f"Training: {f * 100:.0f} %{more}")
        try:
            prepare_dataset(job["pics"], ds, job["size"], job["trigger"])
            c.info(refresh=True)
            try:
                c.run(graph(job["ckpt"], folder, steps, rank, f"loras/bc_train_{tag}"), {"4": "Training"}, prog,
                      cancel=ev.is_set, forget=True)
            except Exception:
                if ev.is_set():
                    return "stopped"
                raise
            if ev.is_set():
                return "stopped"
            files = glob.glob(os.path.join(comfy.sub_dir(ins, "output"), "loras", f"bc_train_{tag}*"))
            if not files:
                raise comfy.ComfyError("Training ended without a file.")
            safe = re.sub(r"[^\w\- ]", "", job["name"]).strip() or "my_lora"
            lora_dir = comfy.sub_dir(ins, "models/loras")
            os.makedirs(lora_dir, exist_ok=True)
            file, n = f"{safe}.safetensors", 2
            while os.path.exists(os.path.join(lora_dir, file)):
                file, n = f"{safe}_{n}.safetensors", n + 1
            shutil.move(files[0], os.path.join(lora_dir, file))
            return dict(job, file=file, speed=round((time.time() - t0) / steps, 2))
        finally:
            shutil.rmtree(ds, ignore_errors=True)
            for f in glob.glob(os.path.join(comfy.sub_dir(ins, "output"), "loras", f"bc_train_{tag}*")):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _progress(self, f, text):
        self.bar.setValue(int(f * 1000))
        self.status.setText(text)

    def _finished(self, res):
        self.start_btn.show()
        self.stop_btn.hide()
        self.bar.hide()
        self.ev = None
        if res == "stopped":
            self.status.setText("Stopped. Nothing was kept.")
        elif isinstance(res, BaseException):
            self.status.setText(f"That didn't work: {res}")
        else:
            file = res["file"]
            if res.get("speed"):        # for the next estimate
                cfg.set("train_speed", dict(cfg.get("train_speed") or {}, **{str(res["size"]): res["speed"]}))
                self._plan()
            loras.set_note(file, triggers=res["trigger"])
            cid = res["char"] if charlib.get(res["char"] or "") else None
            if cid is None:
                cid = charlib.add(res["name"])["id"]
                charlib.add_refs(cid, res["pics"][:charlib.MAX_REFS])
            charlib.update(cid, lora=file, trigger=res["trigger"])
            self.char_id = None
            self.status.setText(f"Done: {file} is in your LoRAs, and {res['name']} is ready under Character on the Image "
                                "page.")
            self.link.refresh_models()
