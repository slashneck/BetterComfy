"""What Better Comfy asks ComfyUI to do: model families, the speed / quality presets, and the workflows (ComfyUI's
built-in nodes only) for pictures (SDXL / Pony / Illustrious / SD 1.5 / Flux / Qwen-Image / Anima / Z-Image) -
generate, upscale, inpaint - and videos (WAN 2.1 / 2.2)."""
import random
import re

# ================================================================================================ picture models

# engine "sd": one checkpoint file (model + text encoder + VAE inside). The others: a diffusion model with its own
# text encoder(s) and VAE, found in ComfyUI's text_encoders / vae folders by name.
FAMILIES = {
    "sdxl": {"name": "SDXL", "engine": "sd", "base": 1024, "clip_skip": 1, "cfg": 6.0, "quality": "",
             "negative": "lowres, bad anatomy, bad hands, blurry, jpeg artifacts, watermark, text, signature, "
                         "worst quality, low quality"},
    "pony": {"name": "Pony", "engine": "sd", "base": 1024, "clip_skip": 2, "cfg": 7.0,
             "quality": "score_9, score_8_up, score_7_up",
             "negative": "score_4, score_5, score_6, lowres, bad anatomy, bad hands, blurry, watermark, text, signature"},
    "illustrious": {"name": "Illustrious", "engine": "sd", "base": 1024, "clip_skip": 2, "cfg": 5.5,
                    "quality": "masterpiece, best quality, amazing quality, very aesthetic",
                    "negative": "worst quality, low quality, lowres, bad anatomy, bad hands, extra digits, blurry, "
                                "jpeg artifacts, watermark, signature, text"},
    "sd15": {"name": "SD 1.5", "engine": "sd", "base": 512, "clip_skip": 1, "cfg": 7.0,
             "quality": "masterpiece, best quality",
             "negative": "lowres, bad anatomy, bad hands, missing fingers, blurry, worst quality, low quality, "
                         "watermark, text"},
    "flux": {"name": "Flux", "engine": "flux", "base": 1024, "clip_skip": 1, "cfg": 1.0, "guidance": 3.5, "steps": 20,
             "sampler": "euler", "scheduler": "simple", "quality": "", "negative": "", "neg": "zero", "latent": "sd3",
             "clip_type": "flux", "te": [("clip_l",), ("t5xxl", "t5")], "vae": ("ae.", "flux_vae", "flux"),
             "need": "Flux: clip_l + t5xxl in models/text_encoders, ae.safetensors in models/vae"},
    "qwen": {"name": "Qwen-Image", "engine": "qwen", "base": 1328, "clip_skip": 1, "cfg": 4.0, "steps": 20,
             "sampler": "euler", "scheduler": "simple", "quality": "", "negative": "", "neg": "text", "latent": "sd3",
             "clip_type": "qwen_image", "te": [("qwen_2.5_vl", "qwen2.5_vl", "qwen_2_5_vl", "qwen25")],
             "vae": ("qwen_image_vae", "qwen"), "shift": 3.1,
             "need": "Qwen-Image: qwen_2.5_vl_7b in models/text_encoders, qwen_image_vae in models/vae"},
    "anima": {"name": "Anima", "engine": "anima", "base": 1024, "clip_skip": 1, "cfg": 4.0, "steps": 30,
              "sampler": "er_sde", "scheduler": "simple", "quality": "masterpiece, best quality",
              "negative": "worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, sepia",
              "neg": "text", "latent": "sd", "clip_type": "stable_diffusion",
              "te": [("qwen_3_06b", "qwen3_06b", "qwen_3_0.6b", "qwen3-0.6b", "qwen_3_0_6b")],
              "vae": ("qwen_image_vae", "qwen"),
              "need": "Anima: qwen_3_06b_base in models/text_encoders, qwen_image_vae in models/vae"},
    "zimage": {"name": "Z-Image", "engine": "zimage", "base": 1024, "clip_skip": 1, "cfg": 1.0, "steps": 8,
               "sampler": "res_multistep", "scheduler": "simple", "quality": "", "negative": "", "neg": "zero",
               "latent": "sd3", "clip_type": "lumina2", "te": [("qwen_3_4b", "qwen3_4b", "qwen_3_4")],
               "vae": ("ae.", "flux_vae", "flux"), "shift": 3.0, "distilled": True,
               "need": "Z-Image: qwen_3_4b in models/text_encoders, ae.safetensors in models/vae"},
    "krea2": {"name": "Krea 2", "engine": "krea2", "base": 1024, "clip_skip": 1, "cfg": 1.0, "steps": 8,
              "sampler": "euler", "scheduler": "simple", "quality": "", "negative": "", "neg": "zero",
              "latent": "sd3", "clip_type": "krea2", "te": [("qwen3vl_4b", "qwen3_vl_4b", "qwen3vl")],
              "vae": ("qwen_image_vae", "wan_2.1_vae", "wan2.1_vae", "wan_2_1_vae"), "distilled": True,
              "need": "Krea 2: qwen3vl_4b in models/text_encoders and qwen_image_vae in models/vae"},
}
FAMILY_ORDER = ["sdxl", "pony", "illustrious", "sd15", "flux", "qwen", "anima", "zimage", "krea2"]
# parts an engine needs that the app can fetch on request (official files, fixed checksums), by what is missing
PART_DOWNLOADS = {
    "krea2": {
        "text encoder": {"folder": "models/text_encoders", "file": "qwen3vl_4b_fp8_scaled.safetensors",
                         "size": 5242467968, "sha256": "54bd5144df0bbc25dd6ccadfcb826b521445a1b06ae5a42570bdd2974ca87094",
                         "url": "https://huggingface.co/Comfy-Org/Krea-2/resolve/eb1eddd3983a54678545a9b2c178c5853b30f7be/text_encoders/qwen3vl_4b_fp8_scaled.safetensors"},
        "VAE": {"folder": "models/vae", "file": "qwen_image_vae.safetensors", "size": 253806246,
                "sha256": "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f",
                "url": "https://huggingface.co/Comfy-Org/Krea-2/resolve/eb1eddd3983a54678545a9b2c178c5853b30f7be/vae/qwen_image_vae.safetensors"},
    },
}
ENGINE_KINDS = ("flux", "qwen", "anima", "zimage", "krea2")

_FAST_WORDS = ("lightning", "hyper", "turbo", "dmd", "lcm", "lightspeed", "lightx2v", "distill", "fastwan", "4step",
               "4-step", "8step", "8-step", "rapid", "causvid", "schnell")


def is_fast_name(name):
    lo = (name or "").lower()
    return any(w in lo for w in _FAST_WORDS)


def model_kind(header):
    """What a model file is, from its tensor names: sdxl | sd15 | flux | qwen | anima | zimage | wan | other."""
    if not header:
        return None
    keys = [k for k in header if k != "__metadata__"]
    j = " ".join(keys[:3000])
    if any(k.startswith("conditioner.embedders.1") for k in keys):
        return "sdxl"
    if any(k.startswith("cond_stage_model.") for k in keys):
        return "sd15"
    if "txtfusion.projector.weight" in j or "txtfusion.layerwise_blocks" in j:
        return "krea2"
    if "llm_adapter" in j:
        return "anima"
    if "double_blocks." in j and "single_blocks." in j:
        return "flux"
    if "img_mod." in j and "txt_mod." in j and "transformer_blocks." in j:
        return "qwen"
    if "cap_embedder" in j and ("context_refiner" in j or "noise_refiner" in j):
        return "zimage"
    if ("patch_embedding" in j or "blocks.0.cross_attn" in j) and "text_embedding" in j:
        return "wan"
    if "blocks.0.cross_attn" in j or "blocks.0.self_attn" in j:
        return "wan"
    return "other"


ckpt_kind = model_kind


def name_kind(name):
    """A guess from a file name, when its inside cannot be read."""
    lo = (name or "").lower()
    if "wan" in lo:
        return "wan"
    if re.search(r"krea.?2", lo):
        return "krea2"
    for k, words in (("flux", ("flux", "schnell", "krea")), ("qwen", ("qwen",)), ("anima", ("anima",)),
                     ("zimage", ("z_image", "zimage", "z-image"))):
        if any(w in lo for w in words):
            return k
    return None


def guess_family(name, kind=None):
    """The family of a picture model (by its tensor names when known, then its name)."""
    lo = (name or "").lower()
    if kind in ENGINE_KINDS:
        return kind
    if kind in (None, "other") and name_kind(name) in ENGINE_KINDS:
        return name_kind(name)
    if kind == "sd15" or (kind is None and re.search(r"(sd ?1\.?5|v1-5|sd15|_15_)", lo)):
        return "sd15"
    if "pony" in lo or "pdxl" in lo:
        return "pony"
    if any(w in lo for w in ("illustrious", "noob", "wai", "_il_", "-il-", "il_", "ilxl", "nai")):
        return "illustrious"
    return "sdxl"


def pick_part(options, words):
    """The first file whose name has one of the words (a text encoder / VAE for an engine)."""
    for w in words:
        for o in options:
            if w in o.lower():
                return o
    return ""


def engine_parts(fam, p, lists):
    """(text encoder 1, text encoder 2, vae, [what is missing]) for an engine family."""
    F = FAMILIES[fam]
    if F["engine"] == "sd":
        return "", "", "", []
    clips, vaes = lists.get("clip") or [], lists.get("vae") or []
    te = [p.get("te1") or pick_part(clips, F["te"][0])]
    if len(F["te"]) > 1:
        te.append(p.get("te2") or pick_part(clips, F["te"][1]))
    vae = p.get("evae") or pick_part(vaes, F["vae"])
    miss = []
    if not all(te):
        miss.append("text encoder")
    if not vae:
        miss.append("VAE")
    return te[0], (te[1] if len(te) > 1 else ""), vae, miss


# ------------------------------------------------------------------------------------------------ presets

# key, name, short line, steps, cfg factor, sampler, scheduler, hires (scale, steps, denoise) or None
IMAGE_PRESETS = [
    ("ultra", "Ultra Fast", "Drafts in seconds - for finding the prompt.", 12, 0.85, "euler_ancestral", "normal", None),
    ("fast", "Fast", "Good everyday quality, quick.", 18, 0.9, "euler_ancestral", "normal", None),
    ("balanced", "Balanced", "Cleaner details, still quick.", 26, 1.0, "dpmpp_2m", "karras", None),
    ("quality", "Quality", "Upscaled pass for crisp detail.", 28, 1.0, "dpmpp_2m", "karras", (1.5, 14, 0.40)),
    ("best", "Best", "Maximum detail - slow on 8 GB.", 34, 1.0, "dpmpp_2m_sde", "karras", (1.5, 20, 0.45)),
]
IMAGE_PRESET = {p[0]: p for p in IMAGE_PRESETS}
# speed models (Lightning / Hyper / DMD2 / Turbo): few steps, low guidance
FAST_IMAGE = {"ultra": (4, None), "fast": (6, None), "balanced": (8, None), "quality": (8, (1.5, 4, 0.35)),
              "best": (10, (1.5, 6, 0.40))}
ENGINE_STEP_FACTOR = {"ultra": 0.5, "fast": 0.7, "balanced": 1.0, "quality": 1.0, "best": 1.3}

ASPECTS = [("1:1", 1, 1), ("4:5", 4, 5), ("3:4", 3, 4), ("2:3", 2, 3), ("9:16", 9, 16), ("9:21", 9, 21),
           ("5:4", 5, 4), ("4:3", 4, 3), ("3:2", 3, 2), ("16:9", 16, 9), ("21:9", 21, 9)]
ASPECT = {a[0]: a for a in ASPECTS}

IMAGE_DEFAULT = {
    "ckpt": "", "model_src": "ckpt", "family": "auto", "prompt": "", "negative": "", "auto_negative": True,
    "quality_tags": True, "loras": [], "aspect": "2:3", "size_mode": "aspect", "width": 832, "height": 1216,
    "megapixels": 1.0, "preset": "balanced", "steps": 0, "cfg": 0.0, "sampler": "", "scheduler": "", "clip_skip": 0,
    "hires": "preset", "hires_scale": 1.5, "hires_denoise": 0.4, "hires_steps": 0, "upscale_model": "auto", "vae": "",
    "te1": "", "te2": "", "evae": "", "guidance": 0.0, "dtype": "default",
    "count": 1, "seed": -1, "init_image": "", "denoise": 0.6,
    # operations on a picture that exists: op = generate | upscale | inpaint
    "op": "generate", "source_image": "", "mask_image": "", "up_mode": "detail", "up_scale": 2.0, "up_strength": 0.35,
    "inpaint_strength": 0.85,
}


def snap(v, m=64):
    return max(m, int(round(v / m)) * m)


def size_from_aspect(aspect_key, base=1024, mp=1.0, mult=64):
    """The size (multiples of 64) closest to the shape without going over the pixel budget - for SDXL at 1 MP these
    are exactly its training sizes (832 x 1216, 1216 x 832, 768 x 1344 …)."""
    a = ASPECT.get(aspect_key, ASPECT["1:1"])
    ratio = a[1] / a[2]
    area = base * base * mp
    best, score = None, None
    for w in range(mult * 4, int((area * 8) ** 0.5) + mult, mult):
        for h in (int(area / w / mult) * mult, int(area / w / mult) * mult + mult):
            if h < mult * 4 or w * h > area * 1.012:
                continue
            sc = abs((w / h) / ratio - 1) * 2 + (1 - w * h / area) * 0.5
            if score is None or sc < score:
                best, score = (w, h), sc
    return best or (snap(area ** 0.5, mult), snap(area ** 0.5, mult))


def family_of(p, kind=None):
    f = p.get("family") or "auto"
    return f if f in FAMILIES else guess_family(p.get("ckpt"), kind)


def image_plan(p, kind=None, src_size=None):
    """What a picture will be: sizes, steps, sampler, upscale pass. src_size: (w, h) of the picture an upscale /
    inpaint works on."""
    fam = family_of(p, kind)
    F = FAMILIES[fam]
    eng = F["engine"]
    key = p.get("preset") if p.get("preset") in IMAGE_PRESET else "balanced"
    pr = IMAGE_PRESET[key]
    fast = (is_fast_name(p.get("ckpt")) or F.get("distilled", False) and "base" not in (p.get("ckpt") or "").lower()
            or any(is_fast_name(lo.get("file")) for lo in p.get("loras") or [] if lo.get("on", True)))
    if p.get("size_mode") == "custom":
        w, h = snap(int(p.get("width") or F["base"]), 8), snap(int(p.get("height") or F["base"]), 8)
    else:
        w, h = size_from_aspect(p.get("aspect", "1:1"), F["base"], float(p.get("megapixels") or 1.0),
                                64 if F["base"] >= 1024 and eng == "sd" else 32)
    if eng == "sd":
        if fast:
            steps, hires = FAST_IMAGE[key]
            cfg, sampler, sched = 1.5, "euler", "sgm_uniform"
        else:
            steps, hires = pr[3], pr[7]
            cfg, sampler, sched = round(F["cfg"] * pr[4], 1), pr[5], pr[6]
        if fam == "sd15" and hires:
            hires = (2.0, hires[1], hires[2] + 0.05)
    else:
        base = F["steps"]
        if fast:
            steps = {"ultra": 4, "fast": 6, "balanced": 8, "quality": 8, "best": 10}[key]
            if F.get("distilled"):
                steps = max(steps, {"ultra": 5, "fast": 6}.get(key, 8))
            cfg = 1.0
        else:
            steps = max(4, int(round(base * ENGINE_STEP_FACTOR[key])))
            cfg = F["cfg"]
        sampler, sched = F["sampler"], F["scheduler"]
        hires = (1.5, max(4, steps // 2), 0.35) if pr[7] else None
    steps = int(p.get("steps") or 0) or steps
    cfg = float(p.get("cfg") or 0) or cfg
    sampler = p.get("sampler") or sampler
    sched = p.get("scheduler") or sched
    hmode = p.get("hires", "preset")
    if hmode == "on":
        hires = (float(p.get("hires_scale") or 1.5), int(p.get("hires_steps") or 0) or (hires[1] if hires else 14),
                 float(p.get("hires_denoise") or 0.4))
    elif hmode == "off":
        hires = None
    op = p.get("op", "generate")
    img2img = op == "generate" and bool(p.get("init_image"))
    out = {"family": fam, "engine": eng, "w": w, "h": h, "steps": steps, "cfg": cfg, "sampler": sampler,
           "scheduler": sched, "clip_skip": int(p.get("clip_skip") or 0) or F["clip_skip"], "fast": fast,
           "img2img": img2img, "denoise": float(p.get("denoise", 0.6)) if img2img else 1.0, "hires": hires,
           "guidance": float(p.get("guidance") or 0) or F.get("guidance", 0.0), "op": op}
    if op in ("upscale", "inpaint") and src_size:
        out["w"], out["h"] = int(src_size[0]), int(src_size[1])
        out["hires"] = None
    if op == "upscale":
        sc = float(p.get("up_scale") or 2.0)
        out["final_w"], out["final_h"] = snap(out["w"] * sc, 8), snap(out["h"] * sc, 8)
        out["up_steps"] = max(6, int(round(steps * 0.55))) if not fast else max(4, steps)
        out["denoise"] = float(p.get("up_strength") or 0.35)
    elif op == "inpaint":
        out["final_w"], out["final_h"] = out["w"], out["h"]
        out["denoise"] = float(p.get("inpaint_strength") or 0.85)
    else:
        out["final_w"] = snap(out["w"] * hires[0], 8) if hires else out["w"]
        out["final_h"] = snap(out["h"] * hires[0], 8) if hires else out["h"]
    return out


def expand_wildcards(text, rng):
    """{a|b|c} -> one of them (picked per picture)."""
    pat = re.compile(r"\{([^{}]*\|[^{}]*)\}")
    for _ in range(20):
        new = pat.sub(lambda m: rng.choice(m.group(1).split("|")).strip(), text)
        if new == text:
            break
        text = new
    return text


def final_prompts(p, plan, seed):
    rng = random.Random(seed)
    F = FAMILIES[plan["family"]]
    pos = expand_wildcards((p.get("prompt") or "").strip(), rng)
    if p.get("quality_tags", True) and F["quality"]:
        low = pos.lower()
        if F["quality"].split(",")[0].strip().lower() not in low:
            pos = F["quality"] + (", " + pos if pos else "")
    neg = (p.get("negative") or "").strip()
    if p.get("auto_negative", True) and F["negative"]:
        neg = (neg + ", " if neg else "") + F["negative"]
    return pos, expand_wildcards(neg, rng)


class _G:
    """Builds an API-format prompt: numbered nodes, labels and weights for the progress bar."""

    def __init__(self, client=None):
        self.P, self.labels, self.weights = {}, {}, {}
        self.n = 0
        self.client = client

    def add(self, cls, label=None, w=0.0, **inputs):
        self.n += 1
        k = str(self.n)
        if self.client is not None:
            try:
                full = dict(self.client.defaults(cls))
                full.update(inputs)
                inputs = full
            except Exception:
                pass
        self.P[k] = {"class_type": cls, "inputs": inputs}
        if label:
            self.labels[k] = label
        if w:
            self.weights[k] = w
        return k


def pick_upscaler(options, family):
    """An upscale model: an anime one for anime families, else any 2x/4x."""
    if not options:
        return ""
    anime = family in ("pony", "illustrious", "anima")
    pri = [o for o in options if ("anime" in o.lower()) == anime] or options
    two = [o for o in pri if re.search(r"(^|[^0-9])2x|x2", o.lower())]
    return (two or pri)[0]


def _model(g, p, plan, lists):
    """Loaders for any engine -> (model, clip, vae) references, LoRAs applied."""
    fam, eng = plan["family"], plan["engine"]
    F = FAMILIES[fam]
    if eng == "sd":
        ck = g.add("CheckpointLoaderSimple", "Loading the model", 3.0, ckpt_name=p["ckpt"])
        model, clip, vae = [ck, 0], [ck, 1], [ck, 2]
        if p.get("vae"):
            vae = [g.add("VAELoader", vae_name=p["vae"]), 0]
        for lo in p.get("loras") or []:
            if not lo.get("on", True) or not lo.get("file") or not float(lo.get("strength", 1.0)):
                continue
            s = float(lo.get("strength", 1.0))
            k = g.add("LoraLoader", "Adding LoRAs", 0.3, model=model, clip=clip, lora_name=lo["file"],
                      strength_model=s, strength_clip=s)
            model, clip = [k, 0], [k, 1]
        if plan["clip_skip"] > 1:
            clip = [g.add("CLIPSetLastLayer", clip=clip, stop_at_clip_layer=-int(plan["clip_skip"])), 0]
        return model, clip, vae
    te1, te2, evae, miss = engine_parts(fam, p, lists or {})
    if miss:
        raise RuntimeError(f"{F['name']} needs its {' and '.join(miss)}. {F['need']}.")
    if eng == "krea2" and p.get("model_src") == "ckpt":
        raise RuntimeError("Krea 2 files belong in models/diffusion_models - the Model card can move it there.")
    if p.get("model_src") == "ckpt":
        model = [g.add("CheckpointLoaderSimple", "Loading the model", 3.0, ckpt_name=p["ckpt"]), 0]
    else:
        model = [g.add("UNETLoader", "Loading the model", 3.0, unet_name=p["ckpt"],
                       weight_dtype=p.get("dtype") or "default"), 0]
    if eng == "flux":
        clip = [g.add("DualCLIPLoader", "Loading the text encoders", 1.0, clip_name1=te1, clip_name2=te2,
                      type="flux"), 0]
    else:
        clip = [g.add("CLIPLoader", "Loading the text encoder", 1.0, clip_name=te1, type=F["clip_type"]), 0]
    vae = [g.add("VAELoader", vae_name=evae), 0]
    for lo in p.get("loras") or []:
        if not lo.get("on", True) or not lo.get("file") or not float(lo.get("strength", 1.0)):
            continue
        model = [g.add("LoraLoaderModelOnly", "Adding LoRAs", 0.3, model=model, lora_name=lo["file"],
                       strength_model=float(lo.get("strength", 1.0))), 0]
    if F.get("shift"):
        model = [g.add("ModelSamplingAuraFlow", model=model, shift=float(F["shift"])), 0]
    return model, clip, vae


def _conds(g, plan, clip, pos_t, neg_t):
    F = FAMILIES[plan["family"]]
    pos = [g.add("CLIPTextEncode", "Reading the prompt", 0.3, text=pos_t, clip=clip), 0]
    if F.get("neg") == "zero":
        neg = [g.add("ConditioningZeroOut", conditioning=pos), 0]
    else:
        neg = [g.add("CLIPTextEncode", "Reading the prompt", 0.2, text=neg_t, clip=clip), 0]
    if plan["engine"] == "flux":
        pos = [g.add("FluxGuidance", conditioning=pos, guidance=float(plan.get("guidance") or 3.5)), 0]
    return pos, neg


def _empty(g, plan, w, h):
    F = FAMILIES[plan["family"]]
    cls = "EmptySD3LatentImage" if F.get("latent") == "sd3" else "EmptyLatentImage"
    return [g.add(cls, width=w, height=h, batch_size=1), 0]


def _decode(g, client, samples, vae, mp):
    if client is not None and client.has("VAEDecodeTiled") and mp > 1.6:
        return [g.add("VAEDecodeTiled", "Decoding", mp * 0.8, samples=samples, vae=vae, tile_size=768, overlap=64), 0]
    return [g.add("VAEDecode", "Decoding", mp * 0.6, samples=samples, vae=vae), 0]


def _sample(g, label, plan, model, pos, neg, latent, seed, steps, denoise, w):
    return [g.add("KSampler", label, w, model=model, seed=int(seed), steps=int(steps), cfg=plan["cfg"],
                  sampler_name=plan["sampler"], scheduler=plan["scheduler"], positive=pos, negative=neg,
                  latent_image=latent, denoise=float(denoise)), 0]


def _upscale_pixels(g, p, plan, img, upscalers, w, h):
    """A picture enlarged to w x h: with an upscale model first when there is one, then resized exactly."""
    um = p.get("upscale_model", "auto")
    if um == "auto":
        um = pick_upscaler(upscalers or [], plan["family"])
    if um and um != "latent":
        ul = g.add("UpscaleModelLoader", model_name=um)
        img = [g.add("ImageUpscaleWithModel", "Upscaling", plan["w"] * plan["h"] / 1e6 * 1.5, upscale_model=[ul, 0],
                     image=img), 0]
    return [g.add("ImageScale", image=img, upscale_method="lanczos", width=w, height=h, crop="disabled"), 0]


def build_image(p, plan, seed, client=None, init_name=None, upscalers=None, lists=None, mask_name=None):
    """Any picture job (generate / upscale / inpaint, any engine). Returns (prompt, labels, weights, pos, neg)."""
    g = _G(client)
    op = plan["op"]
    pos_t, neg_t = final_prompts(p, plan, seed)
    mp = plan["w"] * plan["h"] / 1e6
    if op == "upscale" and p.get("up_mode") == "clean":
        img = [g.add("LoadImage", image=init_name), 0]
        out = _upscale_pixels(g, p, plan, img, upscalers, plan["final_w"], plan["final_h"])
        g.add("PreviewImage", "Handing the picture over", 0.2, images=out)
        return g.P, g.labels, g.weights, pos_t, neg_t
    model, clip, vae = _model(g, p, plan, lists)
    pos, neg = _conds(g, plan, clip, pos_t, neg_t)
    if op == "upscale":
        img = [g.add("LoadImage", image=init_name), 0]
        img = _upscale_pixels(g, p, plan, img, upscalers, plan["final_w"], plan["final_h"])
        mp2 = plan["final_w"] * plan["final_h"] / 1e6
        lat = [g.add("VAEEncode", "Reading the picture", mp2 * 0.4, pixels=img, vae=vae), 0]
        samples = _sample(g, "Adding detail", plan, model, pos, neg, lat, seed, plan["up_steps"], plan["denoise"],
                          plan["up_steps"] * mp2 * 1.15)
        out = _decode(g, client, samples, vae, mp2)
    elif op == "inpaint":
        img = [g.add("LoadImage", image=init_name), 0]
        mask = [g.add("LoadImageMask", image=mask_name, channel="red"), 0]
        lat = [g.add("VAEEncode", "Reading the picture", mp * 0.4, pixels=img, vae=vae), 0]
        lat = [g.add("SetLatentNoiseMask", samples=lat, mask=mask), 0]
        if client is None or client.has("DifferentialDiffusion"):
            model = [g.add("DifferentialDiffusion", model=model), 0]
        samples = _sample(g, "Redrawing", plan, model, pos, neg, lat, seed, plan["steps"], plan["denoise"],
                          plan["steps"] * mp)
        dec = _decode(g, client, samples, vae, mp)
        out = [g.add("ImageCompositeMasked", destination=img, source=dec, x=0, y=0, resize_source=False,
                     mask=mask), 0]
    else:
        if plan["img2img"] and init_name:
            im = [g.add("LoadImage", image=init_name), 0]
            im = [g.add("ImageScale", image=im, upscale_method="lanczos", width=plan["w"], height=plan["h"],
                        crop="center"), 0]
            lat = [g.add("VAEEncode", "Reading the start picture", 0.3, pixels=im, vae=vae), 0]
        else:
            lat = _empty(g, plan, plan["w"], plan["h"])
        samples = _sample(g, "Drawing", plan, model, pos, neg, lat, seed, plan["steps"], plan["denoise"],
                          plan["steps"] * mp)
        if plan["hires"]:
            scale, hsteps, hden = plan["hires"]
            img = _decode(g, client, samples, vae, mp)
            img = _upscale_pixels(g, p, plan, img, upscalers, plan["final_w"], plan["final_h"])
            mp2 = plan["final_w"] * plan["final_h"] / 1e6
            lat2 = [g.add("VAEEncode", "Preparing the detail pass", mp2 * 0.4, pixels=img, vae=vae), 0]
            samples = _sample(g, "Refining details", plan, model, pos, neg, lat2, int(seed) + 1, hsteps, hden,
                              hsteps * mp2 * 1.15)
            out = _decode(g, client, samples, vae, mp2)
        else:
            out = _decode(g, client, samples, vae, mp)
    g.add("PreviewImage", "Handing the picture over", 0.2, images=out)
    return g.P, g.labels, g.weights, pos_t, neg_t


# ================================================================================================ video (WAN)

WAN_FPS = 16

# key, name, line, pixel budget, steps, of them motion, smoothing
VIDEO_PRESETS = [
    ("draft", "Ultra Fast", "Quick look at the motion - small, not smoothed.", 320 * 480, 4, 2, 1),
    ("fast", "Fast", "Small and quick, smoothed to 32 fps.", 384 * 576, 4, 2, 2),
    ("balanced", "Balanced", "Sharper, a little slower.", 512 * 768, 6, 3, 2),
    ("quality", "Quality", "Sharp details - slow on 8 GB.", 576 * 864, 8, 4, 2),
    ("best", "Best", "As sharp as it gets - needs lots of memory.", 720 * 1080, 10, 5, 2),
]
VIDEO_PRESET = {p[0]: p for p in VIDEO_PRESETS}
FULL_STEPS = {"draft": 12, "fast": 16, "balanced": 20, "quality": 26, "best": 30}

LOOPS = [
    ("loop", "Seamless loop", "Ends where it starts (first + last frame = your picture), then the smoothest loop point "
                              "is found and the seam blended. Best for loops."),
    ("pingpong", "Ping-pong", "Plays forward, then backward - always seamless, works with any motion."),
    ("crossfade", "Crossfade loop", "Free motion, then the end is blended into the start - smooth wrap-around."),
    ("pair", "Start → End", "Moves from your start picture to a second picture."),
    ("free", "Free", "Plain motion, no special ending."),
]
LOOP = {lp[0]: lp for lp in LOOPS}

LOOP_WORDS = "seamless loop, continuous looping motion, constant rhythm, smooth fluid motion"
VIDEO_NEGATIVE = ("blurry, out of focus, low detail, bad anatomy, deformed, extra limbs, extra fingers, distorted face, "
                  "duplicate, glitch, noise, flicker, watermark, text, static, still frame, jerky motion")

MOTION_CHIPS = ["hair swaying gently", "slow breathing", "blinking", "subtle head movement", "soft smile",
                "wind blowing through clothes", "camera slowly zooms in", "slow camera pan", "static camera",
                "particles floating", "light flickering", "water rippling"]

VIDEO_DEFAULT = {
    "mode": "auto", "start_image": "", "end_image": "", "prompt": "", "negative": VIDEO_NEGATIVE, "use_negative": False,
    "seconds": 3.0, "loop": "loop", "loop_words": True, "seam": 0, "steady": True, "preset": "fast", "interp": 2, "models": "two",
    "unet_high": "", "unet_low": "", "unet": "", "clip": "", "vae": "", "interp_model": "", "dtype": "default",
    "loras": [], "steps": 0, "high_steps": 0, "cfg_high": 0.0, "cfg_low": 0.0, "shift": 5.0, "sampler": "euler",
    "scheduler": "simple", "custom_size": 0, "aspect": "2:3", "unet_t2v": "", "seed": -1, "count": 1, "format": "mp4", "crf": 16,
}


def frames_for(seconds):
    """WAN makes 4n+1 frames at 16 fps."""
    n = max(5, int(round(float(seconds) * WAN_FPS)) + 1)
    return ((n - 1 + 2) // 4) * 4 + 1


def size_for(aspect, area, mult=16):
    h = (area / max(1e-6, aspect)) ** 0.5
    w = h * aspect
    return max(mult, int(round(w / mult)) * mult), max(mult, int(round(h / mult)) * mult)


def pick(options, *words, avoid=()):
    for o in options:
        lo = o.lower()
        if all(w in lo for w in words) and not any(a in lo for a in avoid):
            return o
    return ""


def guess_video_models(lists):
    """Sensible WAN model choices from what this ComfyUI has. lists: {"unet", "clip", "vae", "interp"}."""
    unets = lists.get("unet") or []
    wan = [u for u in unets if "wan" in u.lower()] or unets
    i2v = [u for u in wan if "i2v" in u.lower()] or wan
    t2v = [u for u in wan if "t2v" in u.lower()]
    hi = pick(i2v, "high") or pick(i2v, "_h_")
    lo = pick(i2v, "low") or pick(i2v, "_l_")
    clips = lists.get("clip") or []
    clip = pick(clips, "umt5") or (clips[0] if clips else "")
    vaes = lists.get("vae") or []
    vae = pick(vaes, "wan_2.1") or pick(vaes, "wan2.1") or pick(vaes, "wan") or (vaes[0] if vaes else "")
    interp = (lists.get("interp") or [""])[0]
    single = next((u for u in i2v if u not in (hi, lo)), "") or (i2v[0] if i2v else "")
    return {"unet_high": hi, "unet_low": lo, "unet": single, "unet_t2v": t2v[0] if t2v else "",
            "models": "two" if hi and lo and hi != lo else "one", "clip": clip, "vae": vae, "interp_model": interp}


def lora_partner(name, options):
    """The other half of a WAN 2.2 LoRA pair (…High… -> …Low…, _H_ -> _L_), if it is there."""
    for a, b in (("high", "low"), ("High", "Low"), ("HIGH", "LOW"), ("_H_", "_L_"), ("-H-", "-L-"), ("_H.", "_L."),
                 ("-H.", "-L."), ("_HN", "_LN"), ("high_noise", "low_noise"), ("-H-", "-L-")):
        if a in name:
            cand = name.replace(a, b)
            if cand in options and cand != name:
                return cand
    m = re.search(r"(?i)(^|[_\-. ])h([_\-. ]|$)", name)
    if m:
        cand = name[:m.start()] + m.group(0).replace("h", "l").replace("H", "L") + name[m.end():]
        if cand in options and cand != name:
            return cand
    return None


_HL = (("high", "low"), ("High", "Low"), ("HIGH", "LOW"), ("_H_", "_L_"), ("-H-", "-L-"), ("_H.", "_L."),
       ("-H.", "-L."), ("_HN", "_LN"), ("high_noise", "low_noise"))


def lora_pair(name, options):
    """('high' | 'low' | None, the other half or None) of a WAN 2.2 LoRA - found in both directions."""
    part = lora_partner(name, options)
    if part:
        return "high", part
    for a, b in _HL:
        if b in name:
            cand = name.replace(b, a)
            if cand in options and cand != name:
                return "low", cand
    m = re.search(r"(?i)(^|[_\-. ])l([_\-. ]|$)", name)
    if m:
        cand = name[:m.start()] + m.group(0).replace("l", "h").replace("L", "H") + name[m.end():]
        if cand in options and cand != name:
            return "low", cand
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if re.search(r"(?i)(high|(^|[_\-. ])h([_\-. ]|$)|_hn)", base) and not re.search(r"(?i)low", base):
        return "high", None
    if is_low_half(base):
        return "low", None
    return None, None


def is_low_half(name):
    lo = name.lower()
    return bool(re.search(r"(low|_l_|-l-|_l\.|-l\.|low_noise|_ln)", lo)) and not re.search(r"(high|_h_|-h-)", lo)


def lora_files(lo):
    f = lo.get("file") if lo.get("file") not in (None, "", "None") else None
    use = lo.get("use", "both")
    if use == "high":
        return f, None
    if use == "low":
        return None, f
    if use == "pair":
        lf = lo.get("low_file") if lo.get("low_file") not in (None, "", "None") else None
        return f, lf
    return f, f


def video_is_fast(p):
    if video_mode(p) == "t2v":
        names = [p.get("unet_t2v")]
    else:
        names = [p.get("unet_high"), p.get("unet_low")] if p.get("models", "two") == "two" else [p.get("unet")]
    names += [lo.get("file") for lo in p.get("loras") or [] if lo.get("on", True)]
    return any(is_fast_name(n) for n in names if n)


def video_mode(p):
    m = p.get("mode", "auto")
    if m == "auto":
        return "i2v" if p.get("start_image") else "t2v"
    return m


def video_plan(p, aspect):
    """What a video will be: size, frames, steps, fps, guidance."""
    q = VIDEO_PRESET.get(p.get("preset"), VIDEO_PRESET["fast"])
    mode = video_mode(p)
    if mode == "t2v":
        a = ASPECT.get(p.get("aspect", "2:3"), ASPECT["2:3"])
        aspect = a[1] / a[2]
    area = int(p.get("custom_size") or 0) or q[3]
    w, h = size_for(aspect, area)
    fast = video_is_fast(p)
    steps = int(p.get("steps") or 0) or (q[4] if fast else FULL_STEPS[q[0]])
    hs = int(p.get("high_steps") or 0) or (q[5] if fast else steps // 2)
    cfg_def = 1.0 if fast else (6.0 if mode == "t2v" else 3.5)
    interp = int(p.get("interp") if p.get("interp") is not None else q[6])
    seconds = float(p.get("seconds", 3.0))
    loop = p.get("loop", "loop")
    if mode == "t2v" and loop in ("loop", "pair"):
        loop = "free"
    if loop == "crossfade":
        seconds += 0.75                  # extra frames that get blended into the start
    return {"w": w, "h": h, "frames": frames_for(seconds), "steps": steps, "high_steps": max(1, min(hs, steps)),
            "interp": max(1, interp), "fps": WAN_FPS * max(1, interp), "fast": fast, "mode": mode, "loop": loop,
            "cfg_high": float(p.get("cfg_high") or 0) or cfg_def, "cfg_low": float(p.get("cfg_low") or 0) or cfg_def}


def build_video(p, pl, start_name, end_name, client, seed):
    """WAN image-to-video (2.2 motion + detail pair, or one model) / text-to-video. Returns (prompt, labels,
    weights, positive text)."""
    g = _G(client)
    two = pl["mode"] == "i2v" and p.get("models") == "two" and p.get("unet_high") and p.get("unet_low")
    dtype = p.get("dtype") or "default"
    if two:
        mh = g.add("UNETLoader", "Loading the motion model", 2.0, unet_name=p["unet_high"], weight_dtype=dtype)
        ml = g.add("UNETLoader", "Loading the detail model", 2.0, unet_name=p["unet_low"], weight_dtype=dtype)
    else:
        name = p.get("unet") or p.get("unet_high")
        mh = ml = g.add("UNETLoader", "Loading the model", 2.0, unet_name=name, weight_dtype=dtype)
    clip = g.add("CLIPLoader", "Loading the text encoder", 1.0, clip_name=p["clip"], type="wan")
    vae = g.add("VAELoader", vae_name=p["vae"])
    hi_ref, lo_ref = [mh, 0], [ml, 0]
    for lo in p.get("loras") or []:
        if not lo.get("on", True) or not float(lo.get("strength", 1.0)):
            continue
        s = float(lo.get("strength", 1.0))
        hi_file, lo_file = lora_files(lo)
        if not two:
            hi_file = hi_file or lo_file
            lo_file = None
        if hi_file:
            hi_ref = [g.add("LoraLoaderModelOnly", "Adding LoRAs", 0.3, model=hi_ref, lora_name=hi_file,
                            strength_model=s), 0]
        if two and lo_file:
            lo_ref = [g.add("LoraLoaderModelOnly", "Adding LoRAs", 0.3, model=lo_ref, lora_name=lo_file,
                            strength_model=s), 0]
    shift = float(p.get("shift", 5.0))
    hi_ref = [g.add("ModelSamplingSD3", model=hi_ref, shift=shift), 0]
    if two:
        lo_ref = [g.add("ModelSamplingSD3", model=lo_ref, shift=shift), 0]
    rng = random.Random(seed)
    text = expand_wildcards((p.get("prompt") or "").strip(), rng)
    if pl["loop"] == "loop" and p.get("loop_words", True):
        text = (text + ", " if text else "") + LOOP_WORDS
    use_neg = bool(p.get("use_negative"))
    pos = g.add("CLIPTextEncode", "Reading the prompt", 0.5, text=text, clip=[clip, 0])
    neg = g.add("CLIPTextEncode", "Reading the prompt", 0.2, text=(p.get("negative") or "") if use_neg else "",
                clip=[clip, 0])
    cfg_h, cfg_l = pl["cfg_high"], pl["cfg_low"]
    if use_neg and cfg_h <= 1.0:
        cfg_h = 1.5                      # words to avoid only work with guidance above 1
    common = dict(width=pl["w"], height=pl["h"], length=pl["frames"], batch_size=1)
    if pl["mode"] == "t2v":
        lat = g.add("EmptyHunyuanLatentVideo", **common)
        cond_pos, cond_neg, latent = [pos, 0], [neg, 0], [lat, 0]
    else:
        img = g.add("LoadImage", image=start_name)
        end = img if pl["loop"] == "loop" else (g.add("LoadImage", image=end_name) if pl["loop"] == "pair" and end_name
                                                 else None)
        if end is not None:
            cond = g.add("WanFirstLastFrameToVideo", "Preparing the frames", 0.5, positive=[pos, 0],
                         negative=[neg, 0], vae=[vae, 0], start_image=[img, 0], end_image=[end, 0], **common)
        else:
            cond = g.add("WanImageToVideo", "Preparing the frames", 0.5, positive=[pos, 0], negative=[neg, 0],
                         vae=[vae, 0], start_image=[img, 0], **common)
        cond_pos, cond_neg, latent = [cond, 0], [cond, 1], [cond, 2]
    sampler, sched = p.get("sampler") or "euler", p.get("scheduler") or "simple"
    steps, split = pl["steps"], pl["high_steps"]
    per_step = pl["w"] * pl["h"] * pl["frames"] / 1e6
    if two and split < steps:
        k1 = g.add("KSamplerAdvanced", "Making the motion", per_step * split * (2 if cfg_h > 1 else 1),
                   model=hi_ref, add_noise="enable", noise_seed=int(seed), steps=steps, cfg=cfg_h, sampler_name=sampler,
                   scheduler=sched, positive=cond_pos, negative=cond_neg, latent_image=latent, start_at_step=0,
                   end_at_step=split, return_with_leftover_noise="enable")
        lat = g.add("KSamplerAdvanced", "Drawing the details", per_step * (steps - split) * (2 if cfg_l > 1 else 1),
                    model=lo_ref, add_noise="disable", noise_seed=int(seed), steps=steps, cfg=cfg_l,
                    sampler_name=sampler, scheduler=sched, positive=cond_pos, negative=cond_neg,
                    latent_image=[k1, 0], start_at_step=split, end_at_step=10000, return_with_leftover_noise="disable")
    else:
        lat = g.add("KSampler", "Making the animation", per_step * steps * (2 if cfg_h > 1 else 1), model=hi_ref,
                    seed=int(seed), steps=steps, cfg=cfg_h, sampler_name=sampler, scheduler=sched, positive=cond_pos,
                    negative=cond_neg, latent_image=latent, denoise=1.0)
    out = g.add("VAEDecode", "Turning it into frames", per_step * 0.6, samples=[lat, 0], vae=[vae, 0])
    if pl["interp"] > 1 and p.get("interp_model") and (client is None or (
            client.has("FrameInterpolate") and client.has("FrameInterpolationModelLoader"))):
        im = g.add("FrameInterpolationModelLoader", model_name=p["interp_model"])
        out = g.add("FrameInterpolate", "Smoothing", per_step * 0.4, interp_model=[im, 0], images=[out, 0],
                    multiplier=int(pl["interp"]))
    else:
        pl["interp"], pl["fps"] = 1, WAN_FPS
    g.add("PreviewImage", "Handing the frames over", 0.2, images=[out, 0])
    return g.P, g.labels, g.weights, text


def work_units(kind, plan, count=1):
    """A number proportional to the time a job takes (for learned time estimates)."""
    if kind == "image":
        if plan.get("op") == "upscale":
            return (plan.get("up_steps", 8) * plan["final_w"] * plan["final_h"] / 1e6 * 1.2 + 2) * count
        u = plan["steps"] * plan["w"] * plan["h"] / 1e6 * (plan.get("denoise") or 1.0)
        if plan.get("hires"):
            u += plan["hires"][1] * plan["final_w"] * plan["final_h"] / 1e6 * 1.2
        return u * count
    u = plan["steps"] * plan["w"] * plan["h"] * plan["frames"] / 1e6 / 20.0
    return u * count
