"""The live link to ComfyUI: is it running, which graphics card, which models - checked in the background."""
import os
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import comfy, workflows
from .config import cfg


class Link(QObject):
    status = Signal(dict)          # {"state": running|starting|stopped|missing, "url", "version", "gpu", "install"}
    models = Signal(dict)          # the model lists (see refresh_models)
    _polled = Signal(dict)
    _listed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.state = {"state": "unknown"}
        self.lists = {}
        self._busy = False
        self._listing = False
        self._starting_since = None
        self._installs = None
        self._kinds = {}
        self._polled.connect(self._on_poll)
        self._listed.connect(self._on_list)
        self.timer = QTimer(self, interval=3500, timeout=self.poll)

    def begin(self):
        self.poll()
        self.refresh_models()
        self.timer.start()

    # ---- installs
    def installs(self, rescan=False):
        if self._installs is None or rescan:
            self._installs = comfy.find_installs(cfg.get("comfy_folder") or None, deep=rescan)
        return self._installs

    def install(self):
        ins = self.installs()
        f = cfg.get("comfy_folder")
        if f:
            for i in ins:
                if os.path.normcase(i["path"]).startswith(os.path.normcase(os.path.abspath(f))):
                    return i
        return ins[0] if ins else None

    def url(self):
        return self.state.get("url") or cfg.get("comfy_url") or comfy.DEFAULT_URL

    def running(self):
        return self.state.get("state") == "running"

    # ---- status
    def poll(self):
        if self._busy:
            return
        self._busy = True

        def work():
            out = {"state": "stopped"}
            try:
                url = comfy.find_running(cfg.get("comfy_url"))
                if url:
                    c = comfy.Client(url)
                    st = c.stats()
                    out = {"state": "running", "url": url, "version": st.get("system", {}).get("comfyui_version", ""),
                           "gpu": c.gpu(), "argv": [str(a) for a in (st.get("system", {}).get("argv") or [])]}
            except Exception:
                out = {"state": "stopped"}
            out["install"] = self.install()
            if out["state"] != "running":
                if out["install"] is None:
                    out["state"] = "missing"
                elif comfy.ours_alive() or (self._starting_since and time.time() - self._starting_since < 300):
                    out["state"] = "starting"
            self._polled.emit(out)
        threading.Thread(target=work, daemon=True).start()

    def _on_poll(self, st):
        self._busy = False
        was = self.state.get("state")
        if st["state"] == "running":
            self._starting_since = None
        elif self._starting_since and st["state"] == "stopped" and not comfy.ours_alive() and \
                time.time() - self._starting_since > 25 and not comfy._STARTED.get("pinokio"):
            self._starting_since = None
        self.state = st
        self.status.emit(st)
        if st["state"] == "running" and (was != "running" or self.lists.get("source") != "comfy"):
            self.refresh_models()

    # ---- models
    def refresh_models(self):
        if self._listing:
            return
        self._listing = True
        state = dict(self.state)

        def work():
            lists = {}
            ins = self.install()
            try:
                if state.get("state") == "running":
                    c = comfy.Client(state["url"])
                    c.info(refresh=True)
                    lists = {"checkpoints": c.choices("CheckpointLoaderSimple", "ckpt_name"),
                             "unet": c.choices("UNETLoader", "unet_name"), "clip": c.choices("CLIPLoader", "clip_name"),
                             "vae": c.choices("VAELoader", "vae_name"), "loras": c.choices("LoraLoader", "lora_name"),
                             "upscale": c.choices("UpscaleModelLoader", "model_name"),
                             "interp": c.choices("FrameInterpolationModelLoader", "model_name"),
                             "samplers": c.choices("KSampler", "sampler_name"),
                             "schedulers": c.choices("KSampler", "scheduler"), "source": "comfy",
                             "nodes": {n: c.has(n) for n in ("WanImageToVideo", "WanFirstLastFrameToVideo",
                                                             "FrameInterpolate", "EmptyHunyuanLatentVideo",
                                                             "UpscaleModelLoader", "VAEDecodeTiled")}}
            except Exception:
                lists = {}
            if not lists:
                loc = comfy.local_lists(ins)
                lists = dict(loc, source="files" if ins else "none", samplers=[], schedulers=[], nodes={})
            # what each model file is (read from inside it): picture models in checkpoints / diffusion_models
            for src, key, folder in (("ckpt", "ckpt_kind", "checkpoints"), ("unet", "unet_kind", "unet")):
                kinds = {}
                for name in lists.get("checkpoints" if src == "ckpt" else "unet") or []:
                    p = comfy.model_path(ins, folder, name)
                    try:
                        ck = (p, os.path.getsize(p)) if p else (name, 0)
                    except OSError:
                        ck = (name, 0)
                    if ck not in self._kinds:
                        h = comfy.safetensors_header(p) if p and p.lower().endswith((".safetensors", ".sft")) else None
                        self._kinds[ck] = workflows.model_kind(h) if h else workflows.name_kind(name)
                    kinds[name] = self._kinds[ck]
                lists[key] = kinds
            lists["video_guess"] = workflows.guess_video_models(lists)
            lists["damaged"] = comfy.damaged_launchers(ins)
            self._listed.emit(lists)
        threading.Thread(target=work, daemon=True).start()

    def _on_list(self, lists):
        self._listing = False
        self.lists = lists
        self.models.emit(lists)

    def picture_models(self):
        """[(name, 'ckpt' | 'unet', kind)] of every picture model this app can run (unknown checkpoints included)."""
        out = []
        ck = self.lists.get("ckpt_kind") or {}
        for c in self.lists.get("checkpoints") or []:
            if ck.get(c) in (None, "sdxl", "sd15") + workflows.ENGINE_KINDS:
                out.append((c, "ckpt", ck.get(c)))
        uk = self.lists.get("unet_kind") or {}
        for u in self.lists.get("unet") or []:
            if uk.get(u) in workflows.ENGINE_KINDS and not u.lower().endswith(".gguf"):
                out.append((u, "unet", uk.get(u)))
        return out

    def kind_of(self, name, src="ckpt"):
        return (self.lists.get("unet_kind" if src == "unet" else "ckpt_kind") or {}).get(name)

    # ---- start / stop
    def start(self):
        ins = self.install()
        if ins is None:
            raise comfy.ComfyError("No ComfyUI found on this PC - see Settings → ComfyUI.")
        if self.running() or comfy.ours_alive():
            return
        comfy.start(ins, comfy.port_of(cfg.get("comfy_url")), cfg.get("fast_mode"), cfg.get("live_previews"),
                    cfg.get("via_pinokio"), cfg.get("reserve_vram"))
        self._starting_since = time.time()
        self.state = dict(self.state, state="starting")
        self.status.emit(self.state)

    def stop(self):
        ins = self.install()
        threading.Thread(target=lambda: comfy.stop_install(ins), daemon=True).start()
        self._starting_since = None
        self.state = dict(self.state, state="stopped")
        self.status.emit(self.state)
        QTimer.singleShot(2500, self.poll)

    def ensure(self, report=None, cancel=lambda: False):
        """(From a worker thread.) The address of a running ComfyUI - started now when allowed."""
        url = comfy.find_running(cfg.get("comfy_url"))
        if url:
            return url
        if not cfg.get("auto_start"):
            raise comfy.ComfyError("ComfyUI is not running. Start it (top right), or turn on 'Start ComfyUI by itself' "
                                   "in Settings.")
        ins = self.install()
        if ins is None:
            raise comfy.ComfyError("No ComfyUI found on this PC. Settings → ComfyUI shows where to get it.")
        if report:
            report("Starting ComfyUI…")
        if not comfy.ours_alive():
            comfy.start(ins, comfy.port_of(cfg.get("comfy_url")), cfg.get("fast_mode"), cfg.get("live_previews"),
                        cfg.get("via_pinokio"), cfg.get("reserve_vram"))
        self._starting_since = time.time()
        t0 = time.time()
        gone = None
        while time.time() - t0 < 300:
            if cancel():
                raise InterruptedError("stopped")
            url = comfy.find_running(cfg.get("comfy_url"))
            if url:
                return url
            if not cfg.get("via_pinokio"):
                if comfy.ours_alive():
                    gone = None
                elif gone is None:
                    gone = time.time()
                elif time.time() - gone > 20:
                    raise comfy.ComfyError("ComfyUI stopped while starting:\n" + comfy.log_tail(25))
            time.sleep(1.5)
            if report:
                report(f"Starting ComfyUI…  {int(time.time() - t0)}s")
        raise comfy.ComfyError("ComfyUI did not start within 5 minutes:\n" + comfy.log_tail(25))
