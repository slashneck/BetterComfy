"""The LoRA library: what each LoRA file is for (read from inside the file), its trigger words, notes, favourite strength,
favourites and preview pictures. Your notes are kept in %LOCALAPPDATA%/BetterComfy/loras.json - for every page."""
import json
import os
import re
import threading

from . import comfy
from .config import BASE

_FILE = os.path.join(BASE, "loras.json")
_lock = threading.Lock()

FAMILY_NAMES = {"sdxl": "SDXL", "illustrious": "Illustrious", "pony": "Pony", "sd15": "SD 1.5", "wan": "WAN",
                "flux": "Flux", "qwen": "Qwen-Image", "anima": "Anima", "zimage": "Z-Image", "krea2": "Krea 2",
                "other": "Other"}
MADE_FOR = ["illustrious", "pony", "sdxl", "sd15", "flux", "qwen", "anima", "zimage", "krea2", "wan", "other"]

# tags for sorting the library; your own come on top (Settings keeps them with the LoRA notes)
TAGS = ["NSFW", "Character", "Art style", "Pose", "Clothing", "Concept", "Detailer", "Background", "Effect"]


def _load():
    try:
        with open(_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_DATA = _load()


def notes(name):
    return dict(_DATA.get(name) or {})


def set_note(name, **kw):
    with _lock:
        d = _DATA.setdefault(name, {})
        for k, v in kw.items():
            if v in (None, "", [], False) and k != "strength":
                d.pop(k, None)
            else:
                d[k] = v
        if not d:
            _DATA.pop(name, None)
        try:
            os.makedirs(BASE, exist_ok=True)
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_DATA, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, _FILE)
        except OSError:
            pass


def word_key(w):
    """The same trigger word however it was typed: case, underscores, spaces and escaped brackets aside."""
    w = (w or "").lower().replace("_", " ").replace("\\(", "(").replace("\\)", ")")
    return re.sub(r"\s+", " ", w).strip()


def split_words(text):
    out, seen = [], set()
    for w in re.split(r"[,\n;]", text or ""):
        w = w.strip()
        if w and word_key(w) not in seen:
            seen.add(word_key(w))
            out.append(w)
    return out


def triggers(name):
    return split_words(notes(name).get("triggers", ""))


def has_word(text, word):
    return bool(word) and re.search(r"(?i)(?<![\w-])" + re.escape(word) + r"(?![\w-])", text or "") is not None


def toggle_word(text, word):
    """A word into a prompt (at its front, where trigger words work best) - or out of it again."""
    if has_word(text, word):
        parts = [x for x in text.split(",") if x.strip().lower() != word.lower()]
        new = ",".join(parts)
        if has_word(new, word):
            new = re.sub(r"(?i)(?<![\w-])" + re.escape(word) + r"(?![\w-])", "", new, count=1)
        new = re.sub(r"\s*,\s*,", ",", new).strip().strip(",").strip()
        return re.sub(r"  +", " ", new)
    return word + (", " + text.strip() if text.strip() else "")


_INFO_CACHE = {}


def file_info(path):
    """What is inside a LoRA file: its family, base model, rank, suggested trigger words (from training tags)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_size, int(st.st_mtime))
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]
    h = comfy.safetensors_header(path) if path.lower().endswith(".safetensors") else None
    info = {"size": st.st_size, "family": "other", "base": "", "rank": None, "suggest": [], "title": ""}
    if h:
        meta = h.get("__metadata__") or {}
        keys = [k for k in h if k != "__metadata__"]
        joined = " ".join(keys[:400])
        if "lora_te2" in joined or "lora_unet_input_blocks" in joined or "lora_unet_output_blocks" in joined or \
                "conditioner" in joined or "lora_unet_label_emb" in joined:
            info["family"] = "sdxl"
        elif "lora_unet_down_blocks" in joined or "lora_te_text_model" in joined:
            info["family"] = "sd15"
        elif re.search(r"blocks[._]\d+[._]attn[._]w[qkvo]\b|blocks[._]\d+[._]mlp[._](gate|up|down)\b|txtfusion", joined):
            info["family"] = "krea2"                   # Krea 2: attn.wq / mlp.gate (WAN has cross_attn / ffn)
        elif re.search(r"(self|cross)_attn[._](q|k|v|output)_proj|adaln_modulation|llm_adapter", joined):
            info["family"] = "anima"                   # Anima (Cosmos): q_proj / output_proj, adaln modulation
        elif re.search(r"(^|\.)blocks\.\d+\.(cross_attn|self_attn|ffn)", joined) or "diffusion_model.blocks" in joined:
            info["family"] = "wan"
        elif "double_blocks" in joined or "single_blocks" in joined or "transformer.single_transformer" in joined:
            info["family"] = "flux"
        elif "transformer_blocks" in joined and ("img_mod" in joined or "txt_mlp" in joined or "img_mlp" in joined):
            info["family"] = "qwen"
        base = str(meta.get("ss_base_model_version") or meta.get("modelspec.architecture") or "")
        info["base"] = base
        lb = base.lower()
        if info["family"] == "other":
            if "xl" in lb:
                info["family"] = "sdxl"
            elif "v1" in lb or "sd1" in lb:
                info["family"] = "sd15"
            elif "wan" in lb:
                info["family"] = "wan"
            elif "flux" in lb:
                info["family"] = "flux"
        try:
            info["rank"] = int(meta.get("ss_network_dim")) if meta.get("ss_network_dim") else None
        except ValueError:
            pass
        info["title"] = meta.get("modelspec.title") or meta.get("ss_output_name") or ""
        tf = meta.get("ss_tag_frequency")
        if tf:
            try:
                d = json.loads(tf) if isinstance(tf, str) else tf
                cnt = {}
                for _, tags in d.items():
                    for t, n in tags.items():
                        t = t.strip()
                        if t:
                            cnt[t] = cnt.get(t, 0) + int(n)
                info["suggest"] = [t for t, _ in sorted(cnt.items(), key=lambda x: -x[1])[:16]]
            except (ValueError, AttributeError, TypeError):
                pass
        tw = meta.get("modelspec.trigger_phrase") or meta.get("ss_trigger_words")
        if tw:
            info["suggest"] = split_words(str(tw)) + [s for s in info["suggest"] if s not in split_words(str(tw))]
    lo = os.path.basename(path).lower()
    if info["family"] == "other":
        if re.search(r"(wan|i2v|t2v|14b|_high|_low|lightx2v)", lo):
            info["family"] = "wan"
        elif re.search(r"(xl|pony|illustrious|_il)", lo):
            info["family"] = "sdxl"
    info["base_family"] = _sdxl_kind(meta if h else {}, lo, info) if info["family"] == "sdxl" else info["family"]
    _INFO_CACHE[key] = info
    return info


def _sdxl_kind(meta, filename, info):
    """Which SDXL model an SDXL LoRA was trained on: illustrious / pony / sdxl, or "" when it can't be told. They all
    look the same inside, so it comes from the training details (the base model's name, Pony's score tags) and
    then the file name."""
    trained = " ".join(str(meta.get(k) or "") for k in ("ss_sd_model_name", "ss_base_model", "modelspec.title",
                                                        "ss_output_name", "modelspec.description")).lower()
    tf = str(meta.get("ss_tag_frequency") or "")
    pony = r"pony|pdxl|autismmix"
    illu = r"illustrious|illu|noob|ilxl|\bixl\b|\(ixl\)|\bwai[^a-z]|[-_ (]il[-_ .)]|[-_ ]il\.safetensors|ilx\b"
    plain = r"sd_xl_base|sdxl_base_1|sd_xl_1\.0|juggernaut|realvis"
    # what it was trained on decides first; the file name only when that says nothing
    for kind, pat in (("pony", pony), ("illustrious", illu), ("sdxl", plain)):
        if re.search(pat, trained):
            return kind
    if "score_9" in tf or "score_8_up" in tf:
        return "pony"
    for kind, pat in (("pony", pony), ("illustrious", illu)):
        if re.search(pat, filename):
            return kind
    return ""


def made_for(name, info):
    """What a LoRA is for: what you set in the LoRA library, else what was read from the file ("" = SDXL, unclear)."""
    return notes(name).get("made_for") or info.get("base_family", info.get("family", "other"))


def made_for_label(name, info):
    m = made_for(name, info)
    return "SDXL ?" if m == "" else FAMILY_NAMES.get(m, m)


def fit(name, info, target):
    """Does a LoRA fit a picture model of `target`: 'yes', 'maybe' (can't be told) or 'no'."""
    m = made_for(name, info)
    if m == "other":
        return "maybe"
    sdxl_world = ("sdxl", "illustrious", "pony")
    if target in sdxl_world:
        if m == "":
            return "maybe"
        return "yes" if m == target else "no"
    if target in ("anima", "zimage", "krea2"):
        return "yes" if m == target else ("maybe" if m in ("", "other") else "no")
    return "yes" if m == target or (m == "" and target == "sdxl") else "no"


# ------------------------------------------------------------------------------------------------ tags

def forget(name):
    """A LoRA that is gone: its notes go too."""
    with _lock:
        if _DATA.pop(name, None) is not None:
            try:
                tmp = _FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(_DATA, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, _FILE)
            except OSError:
                pass


def lora_tags(name):
    return list(notes(name).get("tags") or [])


def set_tags(name, tags_):
    seen, out = set(), []
    for t in tags_:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    set_note(name, tags=out)


def all_tags():
    """The built-in tags, then every own tag used anywhere."""
    own = []
    for d in _DATA.values():
        for t in (d or {}).get("tags") or []:
            if t not in TAGS and t not in own:
                own.append(t)
    return TAGS + sorted(own, key=str.lower)


def is_nsfw(name):
    return any(t.lower() == "nsfw" for t in lora_tags(name))


def suggest_tags(name, info):
    """Tags that look right for a LoRA (from its training tags and name), not yet set on it."""
    from . import tags as T
    have = {t.lower() for t in lora_tags(name)}
    words = ", ".join(info.get("suggest") or [])
    lo = os.path.basename(name).lower()
    out = []
    if T.rating_of(words) != "safe" or re.search(r"nsfw|hentai|lewd|porn|sex", lo):
        out.append("NSFW")
    try:
        T.load()
        if any(T.category(w) == "character" for w in (info.get("suggest") or [])[:6]):
            out.append("Character")
    except Exception:
        pass
    if re.search(r"style|artist|aesthetic", lo):
        out.append("Art style")
    if re.search(r"detail|enhanc|fix|sharp", lo):
        out.append("Detailer")
    if re.search(r"pose|position|spread|from.?behind|doggy|missionary|cowgirl", lo + " " + words.lower()):
        out.append("Pose")
    if re.search(r"outfit|cloth|dress|uniform|costume|bikini|armor", lo):
        out.append("Clothing")
    return [t for t in dict.fromkeys(out) if t.lower() not in have]


def preview_for(install, name):
    """A picture for a LoRA: one you set, or one next to its file (name.preview.png / name.png / .jpg / .webp)."""
    n = notes(name)
    if n.get("preview") and os.path.isfile(n["preview"]):
        return n["preview"]
    p = comfy.model_path(install, "loras", name)
    if not p:
        return None
    stem = os.path.splitext(p)[0]
    for ext in (".preview.png", ".preview.jpg", ".preview.jpeg", ".preview.webp", ".png", ".jpg", ".jpeg", ".webp"):
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def compatible(family_lora, target):
    """May a LoRA of this family go on a picture model of `target` ('sdxl' | 'sd15') or on WAN ('wan')?"""
    if family_lora == "other":
        return True
    if target in ("sdxl", "pony", "illustrious"):
        return family_lora == "sdxl"
    if target in ("anima", "zimage"):
        return family_lora not in ("sdxl", "sd15", "wan", "flux", "qwen")
    return family_lora == target
