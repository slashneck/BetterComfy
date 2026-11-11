"""Finds pictures that look the same by comparing them (a small fingerprint of each one; no AI, nothing leaves the PC).
Nothing is ever deleted here: look-alikes only get the same stack number."""
import uuid

from PIL import Image

MAX_DIST = 4                            # of 64 bits: near-identical only


def fingerprint(path):
    """64 bits: is each pixel of a tiny grey version brighter than the one next to it."""
    with Image.open(path) as im:
        px = list(im.convert("L").resize((9, 8), Image.BILINEAR).getdata())
    h = 0
    for y in range(8):
        for x in range(8):
            h = (h << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
    return h


def _flat(h):
    """A plain colour has hardly any edges: all such pictures would look alike."""
    return h.bit_count() < 6 or h.bit_count() > 58


def groups(entries, progress=None, cancel=None):
    """[[entry, …], …]: the stacks. Same kind only (a picture is never stacked with a video)."""
    prints = []
    for i, e in enumerate(entries):
        if cancel and cancel():
            return []
        try:
            h = fingerprint(e.get("thumb") or e["file"])
        except Exception:
            continue
        if not _flat(h):
            prints.append((e, h))
        if progress and i % 25 == 0:
            progress(i, len(entries))
    parent = list(range(len(prints)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    # two prints at most 4 bits apart agree completely on at least one of 5 slices of the 64 bits
    buckets = {}
    for i, (e, h) in enumerate(prints):
        for band in range(5):
            key = (band, e.get("kind"), (h >> (band * 13)) & 0x1FFF)
            for j in buckets.setdefault(key, []):
                if (h ^ prints[j][1]).bit_count() <= MAX_DIST:
                    parent[find(i)] = find(j)
            buckets[key].append(i)
    out = {}
    for i, (e, _h) in enumerate(prints):
        out.setdefault(find(i), []).append(e)
    return [g for g in out.values() if len(g) > 1]


def new_id():
    return uuid.uuid4().hex[:8]
