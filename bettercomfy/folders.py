"""Collections with sub-collections: each collection can hold its own (one level, so the column stays tidy), in an
order you set. Works on the list of collections of the gallery and of the vault alike: [{"id", "name", "parent"}]."""


def find(cols, cid):
    return next((c for c in cols if c["id"] == cid), None)


def children(cols, cid):
    return [c for c in cols if c.get("parent") == cid]


def family(cols, cid):
    """The collection and its sub-collections (a collection shows everything in them)."""
    return {cid} | {c["id"] for c in children(cols, cid)}


def is_main(c):
    return not c.get("parent")


def ordered(cols):
    """Collections in their order, each followed by its sub-collections. A sub-collection whose collection is gone
    becomes a collection of its own."""
    ids = {c["id"] for c in cols}
    out = []
    for c in cols:
        if c.get("parent") and c["parent"] not in ids:
            c["parent"] = None
    for c in cols:
        if is_main(c):
            out.append(c)
            out += children(cols, c["id"])
    return out


def _block(cols, c):
    """A collection with its sub-collections (they move together)."""
    return [c] + (children(cols, c["id"]) if is_main(c) else [])


def move(cols, cid, target, where):
    """A collection before / after another one, or into it (where: before | after | into). Returns the new list, or
    None when that can't be (one with sub-collections into another, a sub-collection into a sub-collection, onto
    itself)."""
    cols = ordered(list(cols))
    src, tgt = find(cols, cid), find(cols, target)
    if not src or not tgt or src is tgt:
        return None
    if where == "into":
        if not is_main(tgt) or children(cols, cid) or tgt.get("parent") == cid:
            return None
        parent = tgt["id"]
    else:
        parent = tgt.get("parent")
        if parent and children(cols, cid):
            return None                 # one with sub-collections stays a collection of its own
        if parent == cid:
            return None
    block = _block(cols, src)
    rest = [c for c in cols if c not in block]
    src["parent"] = parent
    if where == "into":
        kids = children(rest, tgt["id"])
        anchor = kids[-1] if kids else tgt
        i = rest.index(anchor) + 1
    elif where == "before":
        i = rest.index(tgt)
    else:
        tb = _block(rest, tgt) if not parent else [tgt]
        i = rest.index(tb[-1]) + 1
    return ordered(rest[:i] + block + rest[i:])


def step(cols, cid, d):
    """One place up (d=-1) or down (d=1) among the ones next to it (in the same collection, or the collections)."""
    cols = ordered(list(cols))
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
    """A collection is deleted: its sub-collections become collections of their own."""
    for c in cols:
        if c.get("parent") == cid:
            c["parent"] = None
