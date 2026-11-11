"""One zip with your settings, presets, notes and characters, to bring back on this PC or another. Pictures and the vault
are never in it. A restore is unpacked to the side and put in place at the next start, while nothing has the files open."""
import json
import os
import shutil
import zipfile

from .config import BASE, RESTORE_DIR, cfg

_FILES = ("settings.json", "loras.json", "checkpoints.json", "characters.json")
_GALLERY = ("history.jsonl", "collections.json")        # with prompts: only when asked for
_DIRS = ("characters", "lora_covers")
_MAX_FILE = 200 * 2 ** 20
# never leaves the PC in a backup: encrypted for this Windows account, or only meaningful for this very session
_LEAVE_OUT = ("civitai_key", "addon_files", "window", "image_state", "video_state")


def _settings(with_gallery):
    d = {k: v for k, v in cfg.data.items() if k not in _LEAVE_OUT}
    if not with_gallery:
        d.pop("prompt_history", None)
    return json.dumps(d, ensure_ascii=False, indent=1)


def create(path, with_gallery=False):
    """Writes the zip (a half-written one never takes the place of an older one). Returns how many files are in it."""
    tmp = path + ".part"
    n = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("settings.json", _settings(with_gallery))
            n += 1
            for name in _FILES[1:] + (_GALLERY if with_gallery else ()):
                p = os.path.join(BASE, name)
                if os.path.isfile(p):
                    z.write(p, name)
                    n += 1
            for d in _DIRS:
                for root, _dirs, files in os.walk(os.path.join(BASE, d)):
                    for f in files:
                        p = os.path.join(root, f)
                        z.write(p, os.path.relpath(p, BASE).replace("\\", "/"))
                        n += 1
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return n


def _allowed(name):
    if name in _FILES or name in _GALLERY:
        return True
    parts = name.split("/")
    return len(parts) in (2, 3) and parts[0] in _DIRS and all(p and p not in (".", "..") and ":" not in p for p in parts)


def check(path):
    """What is in a backup, as {'settings', 'gallery', 'files'}. Raises ValueError if it isn't one of ours."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
            if "settings.json" not in names:
                raise ValueError("This isn't a Better Comfy backup.")
            if not isinstance(json.loads(z.read("settings.json").decode("utf-8")), dict):
                raise ValueError("This isn't a Better Comfy backup.")
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise ValueError("This isn't a Better Comfy backup.") from ex
    return {"files": len([n for n in names if _allowed(n)]), "gallery": any(n in _GALLERY for n in names)}


def restore(path):
    """Unpacks only the files a backup can have (nothing can land outside the data folder). They apply at the next
    start: until then nothing has changed."""
    check(path)
    shutil.rmtree(RESTORE_DIR, ignore_errors=True)
    os.makedirs(RESTORE_DIR)
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            if i.is_dir() or not _allowed(i.filename) or i.file_size > _MAX_FILE:
                continue
            dst = os.path.join(RESTORE_DIR, *i.filename.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(i) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
