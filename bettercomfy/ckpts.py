"""The checkpoint library: notes for picture and video models (checkpoints and diffusion models): what they are made
for, tags, link, cover, favourites. Kept in %LOCALAPPDATA%/BetterComfy/checkpoints.json. A model is "ckpt|name" or
"unet|name" (diffusion_models)."""
import json
import os
import re
import threading

from . import workflows
from .config import BASE

_FILE = os.path.join(BASE, "checkpoints.json")
_lock = threading.Lock()

TAGS = ["NSFW", "Realistic", "Anime", "Art style", "Fast", "Inpaint", "Video"]
TYPE_NAMES = {"ckpt": "Checkpoint", "unet": "Diffusion model"}


def _load():
    try:
        with open(_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_DATA = _load()


def key(src, name):
    return f"{src}|{name}"


def split(k):
    src, _sep, name = k.partition("|")
    return src, name


def notes(k):
    return dict(_DATA.get(k) or {})


def set_note(k, **kw):
    with _lock:
        d = _DATA.setdefault(k, {})
        for n, v in kw.items():
            if v in (None, "", [], False):
                d.pop(n, None)
            else:
                d[n] = v
        if not d:
            _DATA.pop(k, None)
        try:
            os.makedirs(BASE, exist_ok=True)
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_DATA, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, _FILE)
        except OSError:
            pass


def forget(k):
    if k in _DATA:
        set_note(k, **{n: None for n in list(_DATA[k])})


def tags(k):
    return list(notes(k).get("tags") or [])


def set_tags(k, tags_):
    seen, out = set(), []
    for t in tags_:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    set_note(k, tags=out)


def all_tags():
    own = []
    for d in _DATA.values():
        for t in (d or {}).get("tags") or []:
            if t not in TAGS and t not in own:
                own.append(t)
    return TAGS + sorted(own, key=str.lower)


def is_nsfw(k):
    return any(t.lower() == "nsfw" for t in tags(k))


def detected(src, name, kind):
    """The family read from the file (or its name): sdxl, pony, illustrious, sd15, flux, qwen, anima, zimage,
    krea2, wan or other."""
    if kind == "wan" or (kind in (None, "other") and workflows.name_kind(name) == "wan"):
        return "wan"
    if src == "unet":
        k = kind if kind not in (None, "other") else workflows.name_kind(name)
        return k or "other"
    if kind not in (None, "sdxl", "sd15") + workflows.ENGINE_KINDS:
        return "other"
    return workflows.guess_family(name, kind)


def made_for(k, kind):
    src, name = split(k)
    return notes(k).get("made_for") or detected(src, name, kind)


def suggest_tags(k, kind):
    src, name = split(k)
    lo = name.lower()
    have = {t.lower() for t in tags(k)}
    out = []
    if made_for(k, kind) == "wan":
        out.append("Video")
    if any(w in lo for w in workflows._FAST_WORDS):
        out.append("Fast")
    if re.search(r"inpaint", lo):
        out.append("Inpaint")
    if re.search(r"real|photo", lo):
        out.append("Realistic")
    if re.search(r"anime|illustrious|noob|pony", lo) or made_for(k, kind) in ("illustrious", "anima"):
        out.append("Anime")
    if re.search(r"nsfw|xxx|porn|hentai|lewd", lo):
        out.append("NSFW")
    return [t for t in out if t.lower() not in have]


def is_full_checkpoint(header):
    """A file with its own VAE / text encoder in it (a checkpoint) - else only the diffusion model."""
    if not header:
        return True
    return any(k.startswith(("first_stage_model.", "vae.", "cond_stage_model.", "conditioner.", "text_encoders.",
                             "text_encoder.")) for k in header)
