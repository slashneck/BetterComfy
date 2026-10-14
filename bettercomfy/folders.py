"""Collections as folders: main folders, each with its own subfolders (one level, so the column stays tidy), in an
order you set. Works on the list of collections of the gallery and of the vault alike: [{"id", "name", "parent"}]."""


def find(cols, cid):
    return next((c for c in cols if c["id"] == cid), None)


def children(cols, cid):
    return [c for c in cols if c.get("parent") == cid]


def family(cols, cid):
    """The folder and its subfolders (a main folder shows everything in them)."""
    return {cid} | {c["id"] for c in children(cols, cid)}


def is_main(c):
    return not c.get("parent")


def ordered(cols):
    """Main folders in their order, each followed by its subfolders. A subfolder whose main folder is gone becomes a
    main folder."""
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
    """A folder with its subfolders (they move together)."""
    return [c] + (children(cols, c["id"]) if is_main(c) else [])


def move(cols, cid, target, where):
    """A folder before / after another one, or into it (where: before | after | into). Returns the new list, or None
    when that can't be (a folder with subfolders into another one, a subfolder into a subfolder, onto itself)."""
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
            return None                 # a folder with subfolders stays a main folder
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
    """One place up (d=-1) or down (d=1) among the folders next to it (same main folder, or the main folders)."""
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
    """A main folder is deleted: its subfolders become main folders."""
    for c in cols:
        if c.get("parent") == cid:
            c["parent"] = None
