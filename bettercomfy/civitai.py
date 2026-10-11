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
    """(version, exact): the one in the link, else the one whose file is this LoRA (both exact), else the newest
    (a guess: a model page can hold versions for different base models, each with its own file)."""
    if vid:
        return _json(f"https://{host}/api/v1/model-versions/{vid}"), True
    model = _json(f"https://{host}/api/v1/models/{mid}")
    versions = model.get("modelVersions") or []
    if not versions:
        raise FetchError("That model has no versions on Civitai.")
    if local_file and os.path.isfile(local_file):
        mine = _sha256(local_file)
        for v in versions:
            for f in v.get("files") or []:
                if str((f.get("hashes") or {}).get("SHA256", "")).upper() == mine:
                    return v, True
    return versions[0], len(versions) == 1


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
    v, exact = version_for(host, mid, vid, local_file)
    words = []
    for w in v.get("trainedWords") or []:
        for part in str(w).split(","):
            part = part.strip()
            if part and part not in words:
                words.append(part)
    return {"triggers": words, "cover": save_cover(v, name), "made_for": made_for(v.get("baseModel")),
            "base": v.get("baseModel") or "", "version": v.get("name") or "", "exact": exact}


# ------------------------------------------------------------------------------------------------ market
# Browsing and downloading, only while the Market is open. Covers are kept in memory, never on the drive.

# "made for" -> Civitai's base model names (as its listing filter wants them)
MARKET_BASES = {
    "illustrious": ["Illustrious", "NoobAI"], "pony": ["Pony"], "sdxl": ["SDXL 1.0"], "sd15": ["SD 1.5"],
    "flux": ["Flux.1 D", "Flux.1 S"], "qwen": ["Qwen", "Qwen 2", "Qwen 2.1"], "anima": ["Anima"],
    "zimage": ["ZImageTurbo", "ZImageBase"], "krea2": ["Krea 2"],
    "wan": ["Wan Video 2.2 I2V-A14B", "Wan Video 2.2 T2V-A14B", "Wan Video 2.2 TI2V-5B", "Wan Video 14B i2v 480p",
            "Wan Video 14B i2v 720p", "Wan Video 14B t2v", "Wan Video 1.3B t2v", "Wan Video"],
}
SORTS = [("Most Downloaded", "AllTime", "Most downloaded"), ("Most Downloaded", "Month", "Most downloaded this month"),
         ("Highest Rated", "AllTime", "Highest rated"), ("Newest", "AllTime", "Newest")]
SITES = ["civitai.red", "civitai.com"]
MATURE = 4                       # Civitai's ratings: 1 PG, 2 PG-13, 4 R, 8 X, 16 XXX


class NeedKey(FetchError):
    pass


class Cancelled(Exception):
    pass


def _auth_headers(key=None):
    h = {"User-Agent": f"BetterComfy/{VERSION}", "Accept": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def search(host, kind="LORA", query="", made_for=None, sort=0, cursor=None, limit=40, nsfw=False):
    """One page of the listing: (models, next cursor or None)."""
    if host not in _HOSTS:
        raise FetchError("Unknown site.")
    s, period, _t = SORTS[sort]
    q = [("limit", str(limit)), ("types", kind), ("sort", s), ("period", period)]
    if not nsfw:
        q.append(("nsfw", "false"))
    if query.strip():
        q.append(("query", query.strip()))
    for b in MARKET_BASES.get(made_for or "", []):
        q.append(("baseModels", b))
    if cursor:
        q.append(("cursor", str(cursor)))
    d = _json(f"https://{host}/api/v1/models?" + urllib.parse.urlencode(q))
    return d.get("items") or [], (d.get("metadata") or {}).get("nextCursor")


def model(host, mid):
    if host not in _HOSTS:
        raise FetchError("Unknown site.")
    return _json(f"https://{host}/api/v1/models/{int(mid)}")


def page_link(host, mid, vid=None):
    return f"https://{host}/models/{int(mid)}" + (f"?modelVersionId={int(vid)}" if vid else "")


def first_media(version, sfw=False):
    """(url, rating) of the version's first picture or video (with sfw: the first one rated below R), or (None, 0)."""
    for im in version.get("images") or []:
        lvl = int(im.get("nsfwLevel") or 0)
        if im.get("url") and not (sfw and lvl >= MATURE):
            return im["url"], lvl
    return None, 0


def shows(m, nsfw):
    """With NSFW off (the default) only models Civitai doesn't flag as NSFW and that have a cover rated below R."""
    if nsfw:
        return True
    if m.get("nsfw"):
        return False
    v = (m.get("modelVersions") or [{}])[0]
    return not v.get("images") or first_media(v, sfw=True)[0] is not None


def thumb_url(url, width=320):
    """A small still: pictures get a width, videos their first frame as a picture."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    if not (host == "image.civitai.com" or host.endswith(".civitai.com")):
        return None
    seg = (f"transcode=true,anim=false,width={width}" if re.search(r"\.(mp4|webm|mov)$", url, re.I)
           else f"width={width}")
    head, _sep, file = url.rpartition("/")
    head2, _s2, last = head.rpartition("/")
    if "=" in last:                                # the size part already there
        head = head2
    return f"{head}/{seg}/{file}"


def thumb(url, width=320):
    u = thumb_url(url, width)
    if not u:
        return None
    return _get(u, timeout=40)


def model_file(version):
    """The version's model file to download: the primary .safetensors one (other formats can run code when
    loaded), or None."""
    files = [f for f in version.get("files") or [] if str(f.get("name", "")).lower().endswith(".safetensors")
             and f.get("type", "Model") == "Model"]
    files.sort(key=lambda f: not f.get("primary"))
    return files[0] if files else None


def safe_name(name):
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base)
    return base if base.lower().endswith(".safetensors") and len(base) > 12 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _open_download(host, vid, key, scheme="https"):
    """The download's response. The key (if any) only goes to Civitai itself: the file host it sends on to gets
    a plain request."""
    url = f"{scheme}://{host}/api/download/models/{int(vid)}?type=Model&format=SafeTensor"
    op = urllib.request.build_opener(_NoRedirect)
    try:
        return op.open(urllib.request.Request(url, headers=_auth_headers(key)), timeout=60)
    except urllib.error.HTTPError as ex:
        if ex.code in (301, 302, 303, 307, 308):
            loc = urllib.parse.urljoin(url, ex.headers.get("Location") or "")
            if not loc.lower().startswith(scheme + "://"):
                raise FetchError("Civitai sent the download somewhere odd.") from None
            try:
                return urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": f"BetterComfy/{VERSION}"}),
                                              timeout=60)
            except (urllib.error.URLError, TimeoutError, OSError) as ex2:
                raise FetchError(f"The download could not start: {getattr(ex2, 'reason', ex2)}") from None
        if ex.code == 401:
            raise NeedKey("The creator only allows this download when logged in: add your Civitai API key in "
                          "Settings, Civitai.") from None
        if ex.code == 403:
            raise FetchError("Not available to download (early access, paid, or the key is not accepted).") from None
        if ex.code == 404:
            raise FetchError("Civitai doesn't have that file (any more).") from None
        raise FetchError(f"Civitai answered with an error ({ex.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise FetchError(f"Civitai can't be reached: {getattr(ex, 'reason', ex)}") from None


def download(host, version, dest_dir, key=None, progress=None, cancel=None):
    """The version's model file into dest_dir, checked against Civitai's SHA-256. progress(done, total);
    cancel: a threading.Event. Returns the new file's path."""
    import time
    f = model_file(version)
    if not f:
        raise FetchError("This version has no .safetensors file.")
    name = safe_name(f.get("name"))
    if not name:
        raise FetchError("The file has an odd name.")
    dest = os.path.join(dest_dir, name)
    if os.path.exists(dest):
        raise FetchError(f"{name} is already in the folder.")
    want = str((f.get("hashes") or {}).get("SHA256", "")).upper()
    r = _open_download(host, version["id"], key)
    total = int(r.headers.get("Content-Length") or 0) or int(float(f.get("sizeKB") or 0) * 1024)
    os.makedirs(dest_dir, exist_ok=True)
    part = dest + ".part"
    h = hashlib.sha256()
    done, last = 0, 0.0
    try:
        with r, open(part, "wb") as out:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                b = r.read(1 << 20)
                if not b:
                    break
                out.write(b)
                h.update(b)
                done += len(b)
                if progress and time.time() - last > 0.2:
                    last = time.time()
                    progress(done, total)
        if progress:
            progress(done, total)
        if want and h.hexdigest().upper() != want:
            raise FetchError("The download came out damaged (its checksum does not match). Please try again.")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


# ---- the optional API key, kept encrypted for this Windows account (DPAPI)
def _crypt(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    c = ctypes.windll.crypt32
    fn = c.CryptProtectData if protect else c.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):   # 0x1: no prompts
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def api_key():
    import base64
    from .config import cfg
    s = cfg.get("civitai_key")
    if not s:
        return None
    try:
        return _crypt(base64.b64decode(s), False).decode("utf-8")
    except Exception:
        return None


def set_api_key(key):
    import base64
    from .config import cfg
    key = (key or "").strip()
    cfg.set("civitai_key", base64.b64encode(_crypt(key.encode("utf-8"), True)).decode("ascii") if key else None)
