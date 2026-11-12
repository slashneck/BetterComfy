"""Collections with sub-collections: a collection can hold its own, and those can hold theirs again (up to MAX_DEPTH
levels, so the column stays readable), in an order you set. Works on the list of collections of the gallery and of
the vault alike: [{"id", "name", "parent"}]."""

MAX_DEPTH = 4                           # a collection, and three levels of sub-collections under it


def find(cols, cid):
    return next((c for c in cols if c["id"] == cid), None)


def children(cols, cid):
    return [c for c in cols if c.get("parent") == cid]


def descendants(cols, cid):
    """Everything under a collection, at any level, in list order."""
    out, todo, seen = [], [cid], {cid}
    while todo:
        cur = todo.pop(0)
        for c in children(cols, cur):
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
                todo.append(c["id"])
    return out


def family(cols, cid):
    """The collection and everything under it (a collection shows everything in them)."""
    return {cid} | {c["id"] for c in descendants(cols, cid)}


def is_main(c):
    return not c.get("parent")


def depth(cols, c):
    """1 for a collection, 2 for one of its sub-collections, and so on."""
    d, seen = 1, {c["id"]}
    while c and c.get("parent"):
        c = find(cols, c["parent"])
        if not c or c["id"] in seen:
            break
        seen.add(c["id"])
        d += 1
    return d


def root(cols, c):
    """The top collection a sub-collection is in (itself for a collection)."""
    seen = {c["id"]}
    while c.get("parent"):
        p = find(cols, c["parent"])
        if not p or p["id"] in seen:
            break
        seen.add(p["id"])
        c = p
    return c


def height(cols, c):
    """How many levels a collection takes up together with what is under it (1: nothing under it)."""
    kids = children(cols, c["id"])
    return 1 + max((height(cols, k) for k in kids), default=0)


def can_hold(cols, c):
    """Whether a new sub-collection can go inside it."""
    return depth(cols, c) < MAX_DEPTH


def ordered(cols):
    """Collections in their order, each followed by what is under it. A sub-collection whose collection is gone (or
    that would be inside itself) becomes a collection of its own."""
    ids = {c["id"] for c in cols}
    for c in cols:
        if c.get("parent") and (c["parent"] not in ids or c["parent"] == c["id"]):
            c["parent"] = None
    out, seen = [], set()

    def walk(c):
        if c["id"] in seen:
            return
        seen.add(c["id"])
        out.append(c)
        for k in children(cols, c["id"]):
            walk(k)
    for c in cols:
        if is_main(c):
            walk(c)
    for c in cols:                      # caught in a loop of parents: out on its own
        if c["id"] not in seen:
            c["parent"] = None
            walk(c)
    return out


def _block(cols, c):
    """A collection with everything under it (they move together)."""
    return [c] + descendants(cols, c["id"])


def move(cols, cid, target, where):
    """A collection before / after another one, or into it (where: before | after | into). Returns the new list, or
    None when that can't be (into itself or something under it, or deeper than MAX_DEPTH). Works on a copy: the list
    it was given stays as it is (menus try out every move before one is picked)."""
    cols = ordered([dict(c) for c in cols])
    src, tgt = find(cols, cid), find(cols, target)
    if not src or not tgt or src is tgt:
        return None
    if tgt["id"] in family(cols, cid):
        return None
    parent = tgt["id"] if where == "into" else tgt.get("parent")
    above = depth(cols, tgt) if where == "into" else depth(cols, tgt) - 1
    if above + height(cols, src) > MAX_DEPTH:
        return None
    block = _block(cols, src)
    rest = [c for c in cols if c not in block]
    src["parent"] = parent
    if where == "into":
        i = rest.index(_block(rest, tgt)[-1]) + 1
    elif where == "before":
        i = rest.index(tgt)
    else:
        i = rest.index(_block(rest, tgt)[-1]) + 1
    return ordered(rest[:i] + block + rest[i:])


def step(cols, cid, d):
    """One place up (d=-1) or down (d=1) among the ones next to it (in the same collection, or the collections)."""
    cols = ordered([dict(c) for c in cols])
    c = find(cols, cid)
    if not c:
        return None
    sibs = [x for x in cols if x.get("parent") == c.get("parent")]
    i = sibs.index(c)
    j = i + d
    if not 0 <= j < len(sibs):
        return None
    return move(cols, cid, sibs[j]["id"], "before" if d < 0 else "after")


def promote_children(cols, cid):
    """A collection is deleted: what was directly in it moves up one level, into the collection it was in."""
    gone = find(cols, cid)
    up = gone.get("parent") if gone else None
    for c in cols:
        if c.get("parent") == cid:
            c["parent"] = up
