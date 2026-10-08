"""The generation queue: pictures and videos one after the other, live progress and previews, learned time estimates,
kept across restarts. The work itself runs in a background thread; everything shown runs in the main thread."""
import copy
import json
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from . import comfy, media, workflows
from .config import BASE, cfg

_FILE = os.path.join(BASE, "queue.json")


def _out_dir(kind):
    d = cfg.get("image_dir" if kind == "image" else "video_dir")
    if cfg.get("date_folders"):
        d = os.path.join(d, time.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    return d


def rate(kind):
    """Learned seconds per unit of work on this PC (None until something was made)."""
    return (cfg.get("rates") or {}).get(kind)


def learn(kind, seconds, units):
    if units <= 0 or seconds <= 0:
        return
    rates = dict(cfg.get("rates") or {})
    r = seconds / units
    old = rates.get(kind)
    rates[kind] = r if old is None else old * 0.6 + r * 0.4
    cfg.set("rates", rates)


def estimate(kind, units):
    r = rate(kind)
    return None if r is None else r * units


# ------------------------------------------------------------------------------------------------ the work

class _Ctx:
    def __init__(self, worker, client):
        self.w, self.client = worker, client

    def progress(self, f, text):
        self.w.sig_progress.emit(self.w.job["id"], float(f), text)

    def preview(self, b):
        qi = QImage.fromData(b)
        if not qi.isNull():
            self.w.sig_preview.emit(self.w.job["id"], qi)

    def output(self, entry):
        self.w.sig_output.emit(self.w.job["id"], entry)

    def cancelled(self):
        return self.w.cancel

    def skip(self, i):
        """Taken out of the queue before it started."""
        return i in self.w.ctrl["skip"]

    def stopped(self, i):
        """Stopped while it was being made (the rest of the job goes on)."""
        return i in self.w.ctrl["stop"]

    def item(self, i, status):
        self.w.sig_item.emit(self.w.job["id"], i, status)

    def run(self, i, prompt, labels, on_progress, on_preview, weights):
        """client.run for item i - stoppable on its own. Returns None when only this item was stopped."""
        try:
            return self.client.run(prompt, labels, on_progress, on_preview,
                                   lambda: self.cancelled() or self.stopped(i), weights)
        except (InterruptedError, comfy.ComfyError):
            if self.stopped(i) and not self.cancelled():
                self.item(i, "canceled")
                return None
            raise


def _variants(p):
    """[(label, settings)] - one per picture to make (a comparison: one per value)."""
    vs = p.get("variants") or []
    if vs:
        out = []
        for v in vs:
            q = dict(p)
            for k, val in (v.get("set") or {}).items():
                if k == "lora_strength":
                    i, st = val
                    q["loras"] = [dict(lo) for lo in q.get("loras") or []]
                    if 0 <= i < len(q["loras"]):
                        q["loras"][i]["strength"] = st
                else:
                    q[k] = val
            out.append((v.get("label", ""), q))
        return out
    return [("", p)] * max(1, int(p.get("count", 1)))


def run_image(ctx, job):
    c, p = ctx.client, job["params"]
    info = c.info()
    for n in ("KSampler", "PreviewImage", "VAEDecode"):
        if n not in info:
            raise comfy.ComfyError(f"Your ComfyUI has no {n} node - update ComfyUI.")
    op = p.get("op", "generate")
    src = p.get("model_src", "ckpt")
    lists = {"clip": c.choices("CLIPLoader", "clip_name"), "vae": c.choices("VAELoader", "vae_name")}
    if not (op == "upscale" and p.get("up_mode") == "clean"):
        have = c.choices("UNETLoader", "unet_name") if src == "unet" else c.choices("CheckpointLoaderSimple", "ckpt_name")
        if not p.get("ckpt") or p["ckpt"] not in have:
            raise comfy.ComfyError(f"The model '{p.get('ckpt') or '-'}' is not in ComfyUI - pick another one.")
    src_size = None
    init = mask = None
    if op in ("upscale", "inpaint"):
        if not os.path.isfile(p.get("source_image", "")):
            raise comfy.ComfyError("The picture to work on is not there any more.")
        src_size = media.image_size(p["source_image"])
        ctx.progress(0.0, "Handing the picture over")
        init = c.upload(media.png_bytes_of(p["source_image"]), f"bc_src_{job['id']}.png")
        if op == "inpaint":
            if not os.path.isfile(p.get("mask_image", "")):
                raise comfy.ComfyError("The mask is missing - paint where to redraw first.")
            mask = c.upload(media.png_bytes_of(p["mask_image"]), f"bc_mask_{job['id']}.png")
    elif p.get("init_image"):
        ctx.progress(0.0, "Handing the start picture over")
        init = c.upload(media.png_bytes_of(p["init_image"]), f"bc_init_{job['id']}.png")
    ups = c.choices("UpscaleModelLoader", "model_name") if c.has("UpscaleModelLoader") else []
    todo = _variants(p)
    n = len(todo)
    base_seed = int(p.get("seed", -1))
    shared_seed = comfy.new_seed() if base_seed < 0 else base_seed
    units = 0.0
    for i, (vlabel, q) in enumerate(todo):
        if ctx.cancelled():
            raise InterruptedError("stopped")
        if ctx.skip(i):
            ctx.item(i, "removed")
            continue
        ctx.item(i, "running")
        if p.get("variants"):
            seed = int(q.get("seed")) if int(q.get("seed", -1)) >= 0 else shared_seed
        else:
            seed = base_seed + i if base_seed >= 0 else comfy.new_seed()
        plan = workflows.image_plan(q, q.get("_kind") or job.get("ckpt_kind"), src_size)
        units += workflows.work_units("image", plan)
        try:
            P, labels, weights, pos, neg = workflows.build_image(q, plan, seed, c, init, ups, lists, mask)
        except RuntimeError as ex:
            raise comfy.ComfyError(str(ex))
        tag = f"  ·  {vlabel or (str(i + 1) + ' of ' + str(n))}" if n > 1 else ""
        t0 = time.time()
        hist = ctx.run(i, P, labels, lambda f, t, i=i: ctx.progress((i + 0.97 * f) / n, t + tag), ctx.preview,
                       weights)
        if hist is None:
            continue
        files = comfy.result_images(hist)
        if not files:
            raise comfy.ComfyError("ComfyUI made no picture")
        for f in files:
            data = c.fetch(f)
            stamp = time.strftime("%H%M%S")
            suffix = {"upscale": "_up", "inpaint": "_edit"}.get(op, "")
            path = media.unique(os.path.join(_out_dir("image"), f"BC_{stamp}_{seed}{suffix}.png"))
            meta = {"steps": plan["steps"], "sampler": plan["sampler"], "scheduler": plan["scheduler"], "cfg": plan["cfg"],
                    "seed": seed, "w": plan["final_w"], "h": plan["final_h"], "model": q.get("ckpt"),
                    "loras": [lo["file"] for lo in q.get("loras") or [] if lo.get("on", True) and lo.get("file")]}
            keep = {k: v for k, v in q.items() if k not in ("variants", "_kind")}
            text = None
            if cfg.get("embed_metadata"):
                text = {"parameters": media.a1111_text(pos, neg, meta), "prompt": json.dumps(P),
                        "bettercomfy": json.dumps({"kind": "image", "params": keep, "seed": seed})}
            size = media.save_png(data, path, text)
            thumb = media.thumbnail(path, os.path.join(BASE, "thumbs", os.path.basename(path)[:-4] + f"_{job['id']}.jpg"))
            e = {"kind": "image", "file": path, "thumb": thumb, "w": size[0], "h": size[1], "seed": seed,
                 "prompt": q.get("prompt", ""), "final_prompt": pos, "negative": neg, "model": q.get("ckpt"),
                 "params": dict(keep, seed=seed, op="generate"), "took": round(time.time() - t0, 1),
                 "preset": q.get("preset"), "loras": meta["loras"], "op": op}
            if p.get("variants"):
                e.update(group=job["id"], label=vlabel)
            if op != "generate":
                e["source"] = p.get("source_image")
            e["_item"] = i
            ctx.output(e)
        ctx.item(i, "done")
    return units


def run_video(ctx, job):
    c, p = ctx.client, job["params"]
    info = c.info()
    need = ["UNETLoader", "CLIPLoader", "VAELoader", "KSamplerAdvanced", "PreviewImage"]
    mode = workflows.video_mode(p)
    need += ["WanImageToVideo"] if mode == "i2v" else ["EmptyHunyuanLatentVideo"]
    missing = [x for x in need if x not in info]
    if missing:
        raise comfy.ComfyError("Your ComfyUI is too old for this (missing: " + ", ".join(missing) +
                               "). Update ComfyUI (in Pinokio: Update).")
    lists = {"unet": c.choices("UNETLoader", "unet_name"), "clip": c.choices("CLIPLoader", "clip_name"),
             "vae": c.choices("VAELoader", "vae_name"), "interp": c.choices("FrameInterpolationModelLoader", "model_name")}
    guess = workflows.guess_video_models(lists)
    p = dict(p)
    for k in ("unet_high", "unet_low", "unet", "clip", "vae", "interp_model"):
        if not p.get(k):
            p[k] = guess.get(k, "")
    if mode == "t2v":
        p["unet"] = p.get("unet_t2v") or guess.get("unet_t2v") or ""
        if not p["unet"]:
            raise comfy.ComfyError("Text-to-video needs a WAN T2V model (e.g. wan2.1_t2v_1.3B) in "
                                   "models/diffusion_models - or add a start picture for image-to-video.")
    if p.get("models") == "two" and not (p.get("unet_high") and p.get("unet_low")):
        p["models"] = "one"
    if not (p.get("unet") or p.get("unet_high")) or not p.get("clip") or not p.get("vae"):
        raise comfy.ComfyError("ComfyUI has no WAN model, text encoder or VAE yet - see Settings → ComfyUI → Check.")
    aspect = 2 / 3
    start = end = None
    if mode == "i2v":
        if not p.get("start_image") or not os.path.isfile(p["start_image"]):
            raise comfy.ComfyError("The start picture is not there any more.")
        sz = media.image_size(p["start_image"])
        aspect = sz[0] / sz[1] if sz else aspect
        ctx.progress(0.0, "Handing the picture over")
        start = c.upload(media.png_bytes_of(p["start_image"]), f"bc_{job['id']}.png")
        if p.get("loop") == "pair" and p.get("end_image") and os.path.isfile(p["end_image"]):
            end = c.upload(media.png_bytes_of(p["end_image"]), f"bc_{job['id']}_end.png")
    n = max(1, int(p.get("count", 1)))
    base_seed = int(p.get("seed", -1))
    units = 0
    for v in range(n):
        if ctx.cancelled():
            raise InterruptedError("stopped")
        if ctx.skip(v):
            ctx.item(v, "removed")
            continue
        ctx.item(v, "running")
        seed = base_seed + v if base_seed >= 0 else comfy.new_seed()
        pl = workflows.video_plan(p, aspect)
        if pl["loop"] == "pair" and not end:
            pl["loop"] = "free"
        units += workflows.work_units("video", pl)
        P, labels, weights, text = workflows.build_video(p, pl, start, end, c, seed)
        tag = f"  ·  {v + 1} of {n}" if n > 1 else ""
        t0 = time.time()
        hist = ctx.run(v, P, labels, lambda f, t, v=v: ctx.progress((v + 0.93 * f) / n, t + tag), ctx.preview,
                       weights)
        if hist is None:
            continue
        files = comfy.result_images(hist)
        if not files:
            raise comfy.ComfyError("ComfyUI made no frames")
        frames = []
        for i, f in enumerate(files):
            if ctx.cancelled():
                raise InterruptedError("stopped")
            frames.append(media.decode(c.fetch(f)))
            if i % 8 == 0:
                ctx.progress((v + 0.93 + 0.04 * i / len(files)) / n, "Taking the frames back" + tag)
        frames = media.finish_loop(frames, pl["loop"], int(p.get("seam", 0)), pl["interp"], bool(p.get("steady", True)))
        fps = pl["fps"]
        joined = None
        if p.get("join", True) and p.get("join_with") and os.path.isfile(p["join_with"]):
            ctx.progress((v + 0.97) / n, "Joining with the clip it continues" + tag)
            frames, fps = media.join_clips([p["join_with"]], frames, fps)
            joined = p["join_with"]
        ctx.progress((v + 0.98) / n, "Writing the video" + tag)
        fmt = p.get("format", "mp4")
        stamp = time.strftime("%H%M%S")
        path = media.unique(os.path.join(_out_dir("video"), f"BC_{stamp}_{seed}.{fmt}"))
        media.write_video(frames, fps, path, fmt, int(p.get("crf", 16)))
        thumb = media.thumbnail(frames[0], os.path.join(BASE, "thumbs", os.path.basename(path).rsplit(".", 1)[0] +
                                                        f"_{job['id']}.jpg"))
        try:
            with open(path + ".json", "w", encoding="utf-8") as fh:
                json.dump({"kind": "video", "params": dict(p, seed=seed), "prompt": text}, fh, ensure_ascii=False,
                          indent=1)
        except OSError:
            pass
        ctx.output({"kind": "video", "file": path, "thumb": thumb, "w": frames[0].shape[1], "h": frames[0].shape[0],
                    "seed": seed, "fps": fps, "frames": len(frames), "seconds": round(len(frames) / fps, 2),
                    "joined": bool(joined),
                    "prompt": p.get("prompt", ""), "final_prompt": text, "model": p.get("unet_high") or p.get("unet"),
                    "params": dict(p, seed=seed), "took": round(time.time() - t0, 1), "preset": p.get("preset"),
                    "loop": pl["loop"], "loras": [lo.get("file") for lo in p.get("loras") or [] if lo.get("on", True)],
                    "_item": v})
        ctx.item(v, "done")
    return units


class Worker(QObject):
    sig_progress = Signal(str, float, str)
    sig_preview = Signal(str, object)
    sig_output = Signal(str, dict)
    sig_done = Signal(str, str, float, float)     # job id, error ('' = fine, 'stopped'), seconds of work, units
    sig_item = Signal(str, int, str)              # job id, item, its status

    def __init__(self, link, job, ctrl):
        super().__init__()
        self.link, self.job = link, job
        self.ctrl = ctrl                          # {"skip": set, "stop": set} - shared with the queue
        self.cancel = False
        self.client = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        jid = self.job["id"]
        err, took, units = "", 0.0, 0.0
        for attempt in (1, 2):
            try:
                url = self.link.ensure(lambda t: self.sig_progress.emit(jid, 0.0, t), lambda: self.cancel)
                self.client = comfy.Client(url)
                t0 = time.time()
                ctx = _Ctx(self, self.client)
                units = (run_image if self.job["kind"] == "image" else run_video)(ctx, self.job)
                took = time.time() - t0
                err = ""
            except InterruptedError:
                err = "stopped"
            except comfy.ComfyUnreachable as ex:
                if attempt == 1 and not self.cancel:
                    self.sig_progress.emit(jid, 0.0, "Waiting for ComfyUI…")
                    time.sleep(4)
                    continue
                err = str(ex)
            except Exception as ex:                       # noqa: BLE001 - shown to the user
                err = str(ex) or ex.__class__.__name__
            break
        self.sig_done.emit(jid, err, took, units)

    def stop(self):
        self.cancel = True
        if self.client is not None:
            threading.Thread(target=self.client.interrupt, daemon=True).start()


# ------------------------------------------------------------------------------------------------ the queue

class Queue(QObject):
    changed = Signal()                 # jobs added / removed / reordered / status
    job_changed = Signal(str)          # progress of one job
    preview = Signal(str, object)      # job id, QImage
    output = Signal(str, dict)         # job id, history entry (already added)
    finished = Signal(bool)            # the queue ran dry (True: something was made since it started)
    done = Signal(str)                 # one job finished well

    def __init__(self, link, history):
        super().__init__()
        self.link, self.history = link, history
        self.jobs = []
        self.paused = False
        self.worker = None
        self._made = 0
        self.ctrl = {}                     # job id -> {"skip": items taken out, "stop": items stopped}
        self._load()
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save)

    # ---- persistence
    def _load(self):
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for j in d.get("jobs", []):
                if j.get("status") in ("queued", "running"):
                    j["status"] = "queued"
                    j["progress"], j["text"] = 0.0, ""
                    for it in j.get("items") or []:
                        if it.get("status") == "running":
                            it["status"] = "queued"
                    self.jobs.append(j)
            if self.jobs:
                self.paused = True               # left over from last time: waits for Resume
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(BASE, exist_ok=True)
            keep = [j for j in self.jobs if j["status"] in ("queued", "running")]
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"jobs": keep}, fh, ensure_ascii=False)
            os.replace(tmp, _FILE)
        except OSError:
            pass

    def _dirty(self):
        self.changed.emit()
        self._save_timer.start()

    # ---- reading
    def job(self, jid):
        return next((j for j in self.jobs if j["id"] == jid), None)

    def running(self):
        return next((j for j in self.jobs if j["status"] == "running"), None)

    def pending(self):
        return [j for j in self.jobs if j["status"] == "queued"]

    def active_count(self):
        return len([j for j in self.jobs if j["status"] in ("queued", "running")])

    def overall(self):
        """(0..1 of the whole queue, seconds left or None)."""
        act = [j for j in self.jobs if j["status"] in ("queued", "running")]
        if not act:
            return 0.0, None
        left = 0.0
        known = True
        for j in act:
            e = estimate(j["kind"], j.get("units", 0))
            if e is None:
                known = False
                continue
            left += e * (1 - (j.get("progress", 0) if j["status"] == "running" else 0))
        cur = self.running()
        f = cur.get("progress", 0) if cur else 0
        return f, (left if known else None)

    # ---- changing
    def add(self, kind, params, title, thumb=None, units=0.0, ckpt_kind=None):
        vs = params.get("variants") or []
        n = len(vs) or max(1, int(params.get("count", 1)))
        noun = "Image" if kind == "image" else "Video"
        items = [{"label": (vs[i].get("label") if vs else f"{noun} {i + 1}"), "status": "queued", "entries": []}
                 for i in range(n)]
        j = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "params": copy.deepcopy(params),
             "status": "queued", "progress": 0.0, "text": "", "created": time.time(), "thumb": thumb,
             "units": units, "ckpt_kind": ckpt_kind, "outputs": [], "error": "", "items": items}
        self.jobs.append(j)
        self._dirty()
        self._next()
        return j

    def remove(self, jid):
        j = self.job(jid)
        if j is None:
            return
        if j["status"] == "running":
            self.cancel(jid)
            return
        self.jobs.remove(j)
        self._dirty()

    def move(self, jid, d):
        j = self.job(jid)
        if j is None or j["status"] != "queued":
            return
        i = self.jobs.index(j)
        k = i + d
        while 0 <= k < len(self.jobs) and self.jobs[k]["status"] != "queued":
            k += d
        if 0 <= k < len(self.jobs):
            self.jobs[i], self.jobs[k] = self.jobs[k], self.jobs[i]
            self._dirty()

    def to_top(self, jid):
        j = self.job(jid)
        if j is None or j["status"] != "queued":
            return
        self.jobs.remove(j)
        first = next((i for i, x in enumerate(self.jobs) if x["status"] == "queued"), len(self.jobs))
        self.jobs.insert(first, j)
        self._dirty()

    def _ctrl(self, jid):
        if jid not in self.ctrl:
            j = self.job(jid)
            skip = {i for i, it in enumerate((j or {}).get("items") or []) if it.get("status") == "removed"}
            self.ctrl[jid] = {"skip": skip, "stop": set()}
        return self.ctrl[jid]

    def remove_item(self, jid, i):
        """Take one waiting picture / video of a job out of the queue."""
        j = self.job(jid)
        if j is None or not (0 <= i < len(j.get("items") or [])) or j["items"][i]["status"] != "queued":
            return
        self._ctrl(jid)["skip"].add(i)
        j["items"][i]["status"] = "removed"
        if j["status"] == "queued" and all(it["status"] == "removed" for it in j["items"]):
            j["status"] = "canceled"
        self._dirty()

    def stop_item(self, jid, i):
        """Stop the picture / video being made now - the rest of its job goes on."""
        j = self.job(jid)
        if j is None or j["status"] != "running":
            return
        self._ctrl(jid)["stop"].add(i)
        j["items"][i]["status"] = "stopping"
        self._dirty()

    def _item(self, jid, i, status):
        j = self.job(jid)
        if j is not None and 0 <= i < len(j.get("items") or []):
            j["items"][i]["status"] = status
            self._dirty()

    def retry(self, jid):
        j = self.job(jid)
        if j is None or j["status"] in ("queued", "running"):
            return
        j.update(status="queued", progress=0.0, text="", error="")
        for it in j.get("items") or []:
            if it["status"] not in ("done",):
                it["status"] = "queued"
        self.ctrl.pop(jid, None)
        if any(it["status"] == "done" for it in j.get("items") or []):
            self._ctrl(jid)["skip"].update(i for i, it in enumerate(j["items"]) if it["status"] == "done")
        self.jobs.remove(j)
        self.jobs.append(j)
        self._dirty()
        self._next()

    def cancel(self, jid):
        j = self.job(jid)
        if j is None:
            return
        if j["status"] == "running" and self.worker is not None:
            j["text"] = "Stopping…"
            self.worker.stop()
            self.job_changed.emit(jid)
        elif j["status"] == "queued":
            j["status"] = "canceled"
            self._dirty()

    def cancel_all(self):
        for j in self.jobs:
            if j["status"] == "queued":
                j["status"] = "canceled"
        cur = self.running()
        if cur:
            self.cancel(cur["id"])
        self._dirty()

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j["status"] in ("queued", "running")]
        self._dirty()

    def pause(self):
        self.paused = True
        self._dirty()

    def resume(self):
        self.paused = False
        self._dirty()
        self._next()

    # ---- running
    def _next(self):
        if self.paused or self.worker is not None:
            return
        j = next((x for x in self.jobs if x["status"] == "queued"), None)
        if j is None:
            return
        j.update(status="running", progress=0.0, text="Getting ready…", started=time.time(), error="")
        w = Worker(self.link, copy.deepcopy(j), self._ctrl(j["id"]))
        w.sig_item.connect(self._item)
        w.sig_progress.connect(self._progress)
        w.sig_preview.connect(lambda jid, qi: self.preview.emit(jid, qi))
        w.sig_output.connect(self._output)
        w.sig_done.connect(self._done)
        self.worker = w
        self._dirty()
        w.start()

    def _progress(self, jid, f, text):
        j = self.job(jid)
        if j is None:
            return
        j["progress"], j["text"] = f, text
        self.job_changed.emit(jid)

    def _output(self, jid, entry):
        i = entry.pop("_item", None)
        e = self.history.add(entry)
        j = self.job(jid)
        if j is not None:
            j["outputs"].append(e["id"])
            if i is not None and 0 <= i < len(j.get("items") or []):
                j["items"][i]["entries"].append(e["id"])
        self._made += 1
        self.output.emit(jid, e)

    def _done(self, jid, err, took, units):
        j = self.job(jid)
        self.worker = None
        if j is not None:
            j["finished"] = time.time()
            if err == "stopped":
                j.update(status="canceled", text="Stopped")
            elif err:
                j.update(status="failed", error=err, text=err.splitlines()[0][:160])
            elif not any(it["status"] == "done" for it in j.get("items") or [{"status": "done"}]):
                j.update(status="canceled", text="Stopped")
            else:
                j.update(status="done", progress=1.0, text=f"Done in {int(took)}s")
                learn(j["kind"], took, units)
                self.done.emit(jid)
            for it in j.get("items") or []:
                if it["status"] in ("queued", "running", "stopping"):
                    it["status"] = "canceled" if j["status"] != "done" or it["status"] != "queued" else "removed"
            self.ctrl.pop(jid, None)
        self._dirty()
        if self.pending() and not self.paused:
            self._next()
        elif not self.pending():
            made = self._made > 0
            self._made = 0
            self.finished.emit(made)
