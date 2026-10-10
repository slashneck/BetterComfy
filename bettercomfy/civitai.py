"""Fetch for the LoRA library: trigger words, a small cover picture and what a LoRA was made for, read from the
Civitai page saved as its link. Only when Fetch is pressed, and only from the site in that link (civitai.com,
civitai.red, civitai.green) and its picture host. Nothing about your files is sent beyond the model and version in
the link (or, for a link without a version, nothing at all: the matching version is found here, by checksum)."""
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request

from .config import BASE, VERSION

COVERS = os.path.join(BASE, "lora_covers")
COVER_SIZE = 320
_HOSTS = ("civitai.com", "civitai.red", "civitai.green")

# Civitai's base model names -> "made for" in the LoRA library
_BASES = [(r"illustrious|noob", "illustrious"), (r"pony", "pony"), (r"sdxl|sd ?xl", "sdxl"), (r"sd ?1\.?5", "sd15"),
          (r"flux", "flux"), (r"qwen", "qwen"), (r"wan", "wan"), (r"z ?image", "zimage"), (r"anima", "anima"),
          (r"krea ?2", "krea2")]


class FetchError(Exception):
    pass


def parse(url):
    """(host, model id, version id or None) from a Civitai link, or None when it is not one."""
    u = (url or "").strip()
    if not u:
        return None
    if not re.match(r"(?i)https?://", u):
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _HOSTS:
        return None
    m = re.search(r"/models/(\d+)", p.path)
    if not m:
        return None
    q = urllib.parse.parse_qs(p.query)
    vid = (q.get("modelVersionId") or [None])[0]
    return host, int(m.group(1)), int(vid) if vid and vid.isdigit() else None


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"BetterComfy/{VERSION}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            raise FetchError("Civitai doesn't know that model (any more).") from None
        raise FetchError(f"Civitai answered with an error ({ex.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise FetchError(f"Civitai can't be reached: {getattr(ex, 'reason', ex)}") from None


def _json(url):
    try:
        return json.loads(_get(url).decode("utf-8"))
    except ValueError:
        raise FetchError("Civitai sent something unexpected.") from None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 22)
            if not b:
                break
            h.update(b)
    return h.hexdigest().upper()


def version_for(host, mid, vid, local_file=None):
    """The model version: the one in the link, else the one whose file is this LoRA, else the newest."""
    if vid:
        return _json(f"https://{host}/api/v1/model-versions/{vid}")
    model = _json(f"https://{host}/api/v1/models/{mid}")
    versions = model.get("modelVersions") or []
    if not versions:
        raise FetchError("That model has no versions on Civitai.")
    if local_file and os.path.isfile(local_file) and len(versions) > 1:
        mine = _sha256(local_file)
        for v in versions:
            for f in v.get("files") or []:
                if str((f.get("hashes") or {}).get("SHA256", "")).upper() == mine:
                    return v
    return versions[0]


def made_for(base_model):
    b = (base_model or "").lower()
    for pat, key in _BASES:
        if re.search(pat, b):
            return key
    return None


def _small(url):
    """Civitai's picture links can ask for a size: a small one is enough for a cover."""
    return re.sub(r"/(original=true|width=\d+)/", f"/width={COVER_SIZE * 2}/", url)


def save_cover(version, name):
    """The first picture of the version, made small (longest side 320 px) and kept in the app's data folder."""
    from PIL import Image
    imgs = [i for i in version.get("images") or [] if i.get("type", "image") == "image" and i.get("url")]
    if not imgs:
        return None
    url = imgs[0]["url"]
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not (host == "image.civitai.com" or host.endswith(".civitai.com")):
        return None
    data = _get(_small(url), timeout=60)
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise FetchError("The cover picture could not be read.") from None
    im = im.convert("RGB")
    im.thumbnail((COVER_SIZE, COVER_SIZE))
    os.makedirs(COVERS, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(name))[0])[:80] or "lora"
    path = os.path.join(COVERS, f"{safe}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:8]}.jpg")
    im.save(path, "JPEG", quality=88)
    return path


def fetch(name, url, local_file=None):
    """Everything Fetch fills in for one LoRA: {"triggers": [...], "cover": path or None, "made_for": key or None,
    "version": name}. Raises FetchError with a plain message."""
    link = parse(url)
    if not link:
        raise FetchError("The link is not a Civitai model page (civitai.com / .red / .green).")
    host, mid, vid = link
    v = version_for(host, mid, vid, local_file)
    words = []
    for w in v.get("trainedWords") or []:
        for part in str(w).split(","):
            part = part.strip()
            if part and part not in words:
                words.append(part)
    return {"triggers": words, "cover": save_cover(v, name), "made_for": made_for(v.get("baseModel")),
            "version": v.get("name") or ""}
