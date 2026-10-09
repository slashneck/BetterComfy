"""Tag suggestions while typing: Danbooru tags, searched as you type."""
import csv
import hashlib
import re
import threading

from .config import cfg, resource

CATS = {"0": "general", "1": "artist", "3": "series", "4": "character", "5": "meta"}
_tags = None            # [(search key, count, tag, category, matched alias or "")]
_aliases = None
_lock = threading.Lock()


def load():
    """Reads the list once (in the background the first time a prompt gets focus)."""
    global _tags, _aliases
    with _lock:
        if _tags is not None:
            return
        tags, aliases = [], []
        try:
            with open(resource("assets", "tags", "danbooru.csv"), encoding="utf-8", newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) < 3:
                        continue
                    name, cat, count = row[0], row[1], int(row[2] or 0)
                    shown = name.replace("_", " ") if len(name) > 3 else name     # ^_^ and the like stay
                    tags.append((shown.lower(), count, shown, CATS.get(cat, "general")))
                    for a in (row[3].split(",") if len(row) > 3 and row[3] else []):
                        a = a.strip().lstrip("/")
                        if len(a) > 2:
                            aliases.append((a.replace("_", " ").lower(), count, shown, CATS.get(cat, "general")))
        except OSError:
            pass
        _aliases = aliases
        _tags = tags


def ready():
    return _tags is not None


def load_async():
    if _tags is None:
        threading.Thread(target=load, daemon=True).start()


def search(text, n=8):
    """Best matches for what is being typed: tags starting with it first, then tags with a word starting with it,
    then aliases. Each: (tag, category, count, alias)."""
    if _tags is None:
        return []
    q = text.strip().lower().replace("_", " ")
    if len(q) < 2:
        return []
    starts, words = [], []
    for key, count, tag, cat in _tags:
        if key.startswith(q):
            starts.append((count, tag, cat, ""))
        elif len(words) < 400 and (" " + q) in key:
            words.append((count, tag, cat, ""))
    bl = blacklist()

    def first(items, k):
        """The best k of them, without blacklisted tags."""
        got = []
        for it in sorted(items, reverse=True):
            if not (bl and blocked(it[1], bl, exact=it[2] != "general")):
                got.append(it)
                if len(got) >= k:
                    break
        return got
    out = first(starts, n)
    if len(out) < n:
        out += first(words, n - len(out))
    if len(out) < n:
        have = {t for _c, t, _k, _a in out}
        al = [(c, t, k, a) for a, c, t, k in _aliases if a.startswith(q) and t not in have]
        for c, t, k, a in first(al, n * 3):
            if t not in have:
                have.add(t)
                out.append((c, t, k, a))
            if len(out) >= n:
                break
    return [(t, k, c, a) for c, t, k, a in out]


def for_prompt(tag):
    """How a tag goes into a prompt: brackets are escaped, they would change the weight otherwise."""
    return tag.replace("(", "\\(").replace(")", "\\)")


def short_count(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


_lookup = None


def lookup():
    """({tag lowercase: tag}, {alias lowercase: tag}, {tag lowercase: category}) for checking tags."""
    global _lookup
    load()
    if _lookup is None:
        _lookup = ({k: t for k, _c, t, _cat in _tags}, {a: t for a, _c, t, _cat in _aliases},
                   {k: cat for k, _c, _t, cat in _tags})
    return _lookup


# ------------------------------------------------------------------------------------------------ the blacklist
# Settings, Tag blacklist: tags you never want suggested or added by the prompt helper. Empty unless you fill it.

def norm(t):
    t = (t or "").lower().replace("_", " ").replace("\\(", "(").replace("\\)", ")")
    return re.sub(r"\s+", " ", t).strip()


# The starting blacklist (until it is changed in Settings), kept as SHA-256 fingerprints of the tag names: the names
# themselves come from the tag list when the app runs.
_START = {
    "0b67579a10e1bdf91d85e65414dc38fcab9b973a97ab661a169a0813338ce7bd",
    "0b9178fd24a7ec23164947e2b6ba1aaea4c8d44505fa16699f118d59c2bd4e96",
    "17156396d600f57a65512fad3395ad4be9cd376904b09ca00fb2aeb1152686ce",
    "1c7bae8ce85db858e4816f824268aed53bd3f0d5fff9e0aeda05ec7aaf5b3245",
    "2809f1c4b0b87fad73015c9624c16daae588e8bd1fcb3a420ad4322c6e7ff1a2",
    "30aecbd5fa5383fd8bee987dad9ab722e906e0015cee1b31038a7ce9156668c6",
    "43f5ffec450b716cdec5cae3737f888c1534e2e439bf04c38f1d47fddc0c6f49",
    "4d120f9504aa441ed75ab0bb92c735b829239018949580723aac32efa0032793",
    "8edb4fc92b919c7e21992b0ebe0e6acc6bdb5b8394a52f689b86d5d00c3d7b5f",
    "9b4a82bf810344bf2bee5766912a95598503d3db53b91b23dd9c00841fdfc479",
    "c21f7b0939ee01528745038ff9fd5245c223fec086cc40696f77a631a52c3f6b",
    "c55fd8a7f68dd354b6fa95d27909c4be931f56bdb28dc094cbfa1392f61ee0e1",
    "dcded0f0a4aa0a2a1b150c60089ca4205087cb3096fa334ab6fcbc0c18bae89c",
    "def4d938205533b17e0cfe7debe34ece1be7b23488848168d501e112872a6161",
    "e4895e5b23b10d6b22a8f4d795768e1094946986d2751e2aa44538e14231eb12",
    "ef43952c5964397ae4c051bec0efc4c38dd049eb5670a47c31188786ea16ad6c",
}
_start_names = None


def start_list():
    global _start_names
    if _start_names is None:
        load()
        _start_names = sorted({k for k, _c, _t, _cat in _tags
                               if hashlib.sha256(k.encode("utf-8")).hexdigest() in _START})
    return list(_start_names)


def blacklist():
    items = cfg.get("tag_blacklist")
    if items is None:
        items = start_list()
    return {norm(x) for x in items if norm(x)}


def blocked(text, bl=None, exact=False):
    """True when `text` is a blacklisted tag, or a phrase that contains one as whole words (thighhighs: also white
    thighhighs). exact: only the tag itself, for names of characters, artists and series."""
    bl = blacklist() if bl is None else bl
    if not bl:
        return False
    t = norm(text)
    if t in bl:
        return True
    if exact:
        return False
    return any(re.search(r"(?<![\w])" + re.escape(b) + r"(?![\w])", t) for b in bl)


def set_blacklist(items):
    clean, seen = [], set()
    for x in items:
        k = norm(x)
        if k and k not in seen:
            seen.add(k)
            clean.append(k)
    cfg.set("tag_blacklist", sorted(clean))
