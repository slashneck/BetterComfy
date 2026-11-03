"""Characters: a name, pictures of it and (if trained) its LoRA with a trigger word, kept in the app's data folder. On
the Image page a character is picked and placed in any scene: by its LoRA, by its pictures (the Character reference
add-on), or both. The add-on is optional: a pinned, checked download into ComfyUI that can be taken out again."""
import hashlib
import json
import os
import shutil
import threading
import uuid
import zipfile

from PIL import Image

from . import comfy
from .config import BASE, cfg

_FILE = os.path.join(BASE, "characters.json")
_DIR = os.path.join(BASE, "characters")
_lock = threading.Lock()
MAX_REFS = 4                            # how many of its pictures go along with a job

NODE = {"name": "ComfyUI_IPAdapter_plus", "folder": "custom_nodes/ComfyUI_IPAdapter_plus",
        "commit": "a0f451a5113cf9becb0847b92884cb10cbdec0ef", "tree": "7310c51a2f7ad4d7501c98dbb95026ed6ee6c35abfdf0ddd485a64af26a0ef37",
        "size": 306422,
        "url": "https://github.com/cubiq/ComfyUI_IPAdapter_plus/archive/a0f451a5113cf9becb0847b92884cb10cbdec0ef.zip"}
MODEL = {"folder": "models/ipadapter", "file": "ip-adapter-plus_sdxl_vit-h.safetensors", "size": 847517512,
         "sha256": "3f5062b8400c94b7159665b21ba5c62acdcd7682262743d7f2aefedef00e6581",
         "url": "https://huggingface.co/h94/IP-Adapter/resolve/018e402774aeeddd60609b4ecdb7e298259dc729/sdxl_models/"
                "ip-adapter-plus_sdxl_vit-h.safetensors"}
VISION = {"folder": "models/clip_vision", "file": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors", "size": 2528373448,
          "sha256": "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030",
          "url": "https://huggingface.co/h94/IP-Adapter/resolve/018e402774aeeddd60609b4ecdb7e298259dc729/models/"
                 "image_encoder/model.safetensors"}


# ------------------------------------------------------------------------------------------------ the characters
def _load():
    try:
        with open(_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


_DATA = _load()


def _save():
    os.makedirs(BASE, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_DATA, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _FILE)


def all_characters():
    return list(_DATA)


def get(cid):
    return next((c for c in _DATA if c["id"] == cid), None)


def add(name):
    with _lock:
        c = {"id": uuid.uuid4().hex[:10], "name": name.strip()[:60] or "Character", "refs": [], "lora": "",
             "trigger": "", "strength": 0.8, "note": ""}
        _DATA.append(c)
        _save()
    return c


def update(cid, **fields):
    with _lock:
        c = get(cid)
        if c:
            c.update(fields)
            _save()


def remove(cid):
    """The character and its copies of the pictures. A LoRA trained for it stays in the LoRA library."""
    with _lock:
        _DATA[:] = [c for c in _DATA if c["id"] != cid]
        _save()
    shutil.rmtree(os.path.join(_DIR, cid), ignore_errors=True)


def add_refs(cid, paths):
    """Copies pictures in (as PNG, the longest side at most 1536). Returns how many could be read."""
    c = get(cid)
    if not c:
        return 0
    folder = os.path.join(_DIR, cid)
    os.makedirs(folder, exist_ok=True)
    n = 0
    for p in paths:
        try:
            with Image.open(p) as im:
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                im.thumbnail((1536, 1536), Image.LANCZOS)
                dst = os.path.join(folder, uuid.uuid4().hex[:10] + ".png")
                im.save(dst, "PNG")
            c["refs"].append(dst)
            n += 1
        except Exception:
            pass
    update(cid, refs=c["refs"])
    return n


def remove_ref(cid, path):
    c = get(cid)
    if not c:
        return
    update(cid, refs=[r for r in c["refs"] if r != path])
    try:
        os.remove(path)
    except OSError:
        pass


def cover(c):
    return next((r for r in c.get("refs") or [] if os.path.isfile(r)), None)


# ------------------------------------------------------------------------------------------------ the add-on
def _tree_hash(folder):
    """One checksum over all files of a folder (their relative paths and contents)."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, folder).replace("\\", "/").encode("utf-8"))
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


def _vision_there(install):
    d = comfy.sub_dir(install, "models/clip_vision")
    try:
        return any(("vit-h" in f.lower() or "clip_vision_h" in f.lower()) and f.endswith(".safetensors")
                   for f in os.listdir(d))
    except OSError:
        return False


def addon_state(install):
    """{'node', 'model', 'vision'}: which parts are there."""
    if not install:
        return {"node": False, "model": False, "vision": False}
    return {"node": os.path.isfile(os.path.join(comfy.sub_dir(install, NODE["folder"]), "__init__.py")),
            "model": os.path.isfile(os.path.join(comfy.sub_dir(install, MODEL["folder"]), MODEL["file"])),
            "vision": _vision_there(install)}


def addon_ready(install):
    return all(addon_state(install).values())


def addon_todo(install):
    """What a download still has to fetch: [(what, size)]."""
    st = addon_state(install)
    out = []
    if not st["node"]:
        out.append(("Add-on: ComfyUI_IPAdapter_plus", NODE["size"]))
    if not st["model"]:
        out.append((MODEL["file"], MODEL["size"]))
    if not st["vision"]:
        out.append((VISION["file"], VISION["size"]))
    return out


def addon_install(install, progress=None, cancel=None):
    """Fetches what is missing. Everything is checked against a fixed checksum; a bad download changes nothing."""
    from . import assistant
    st = addon_state(install)
    mine = list(cfg.get("addon_files") or [])
    if not st["model"]:
        dest = os.path.join(comfy.sub_dir(install, MODEL["folder"]), MODEL["file"])
        assistant.download(MODEL["url"], dest, MODEL["size"], MODEL["sha256"], progress, cancel)
        mine.append(dest)
        cfg.set("addon_files", mine)
    if not st["vision"]:
        dest = os.path.join(comfy.sub_dir(install, VISION["folder"]), VISION["file"])
        assistant.download(VISION["url"], dest, VISION["size"], VISION["sha256"], progress, cancel)
        mine.append(dest)
        cfg.set("addon_files", mine)
    if not st["node"]:
        import urllib.request
        from .config import VERSION
        tmp = os.path.join(BASE, "addon-download")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        try:
            zp = os.path.join(tmp, "node.zip")
            req = urllib.request.Request(NODE["url"], headers={"User-Agent": f"BetterComfy/{VERSION}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(zp, "wb") as out:
                shutil.copyfileobj(r, out)
            with zipfile.ZipFile(zp) as z:
                z.extractall(os.path.join(tmp, "x"))
            top = os.path.join(tmp, "x", os.listdir(os.path.join(tmp, "x"))[0])
            if _tree_hash(top) != NODE["tree"]:
                raise RuntimeError("The add-on came out different from the checked version. Nothing was installed.")
            shutil.move(top, comfy.sub_dir(install, NODE["folder"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def addon_remove(install):
    """Takes the add-on and the files it brought out of ComfyUI (models that were there before stay)."""
    shutil.rmtree(comfy.sub_dir(install, NODE["folder"]), ignore_errors=True)
    for f in cfg.get("addon_files") or []:
        try:
            os.remove(f)
        except OSError:
            pass
    cfg.set("addon_files", [])


# ------------------------------------------------------------------------------------------------ for a job
def prepare(p, client, upload):
    """A picture job with a character: returns the settings with its LoRA, trigger word and uploaded pictures for the
    workflow (under keys of their own, so they never end up in the job's list of LoRAs). upload(bytes, name) -> ref."""
    c = get(p.get("character") or "")
    if not c:
        return p
    q = dict(p)
    s = float(p.get("char_strength", c.get("strength", 0.8)))
    lora = c.get("lora") or ""
    if lora and lora in (client.choices("LoraLoader", "lora_name") or []):
        q["_char_lora"] = {"file": lora, "strength": round(0.4 + s * 0.8, 2), "on": True}
        q["_char_trigger"] = c.get("trigger") or ""
    else:
        lora = ""
    refs = [r for r in c.get("refs") or [] if os.path.isfile(r)][:MAX_REFS]
    if refs and client.has("IPAdapterAdvanced"):
        names = []
        for i, r in enumerate(refs):
            with open(r, "rb") as fh:
                names.append(upload(fh.read(), f"bc_char_{c['id']}_{i}.png"))
        vision = client.choices("CLIPVisionLoader", "clip_name") or []
        vit = [v for v in vision if "vit-h" in v.lower() or "clip_vision_h" in v.lower()]
        q["_char_refs"] = names
        q["_char_vision"] = (vit or vision or [""])[0]
        # with a LoRA the pictures only help a little
        q["_char_weight"] = round(s * (0.5 if lora else 1.0), 2)
    elif not lora:
        raise RuntimeError(f"{c['name']} needs the Character reference add-on (Characters tab) or a trained LoRA.")
    return q
