"""Everything made with Better Comfy (the gallery): one JSON line per picture / video, with its settings. Changes
(favourite, marked, collections) are added as small patch lines; deleting rewrites the file when files are shredded,
so no prompt of a deleted picture stays behind."""
import json
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

from .config import BASE

_FILE = os.path.join(BASE, "history.jsonl")
_COLS = os.path.join(BASE, "collections.json")


class History(QObject):
    added = Signal(dict)
    removed = Signal(str)
    updated = Signal(str)
    changed = Signal()                  # collections changed
    _bg_removed = Signal(list)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.items = []
        self._by_id = {}
        self.collections = []           # [{"id", "name", "smart": None | {filters}}]
        self._load()
        self._load_cols()

    # ---- file
    def _load(self):
        items = {}
        order = []
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if not isinstance(e, dict):
                        continue
                    eid = e.get("id")
                    if e.get("_deleted"):
                        items.pop(eid, None)
                    elif "_patch" in e:
                        if eid in items:
                            items[eid].update(e["_patch"])
                    elif e.get("file"):
                        if eid not in items:
                            order.append(eid)
                        items[eid] = e
        except OSError:
            pass
        self.items = [items[i] for i in order if i in items and os.path.isfile(items[i].get("file", ""))]
        self._by_id = {e["id"]: e for e in self.items}

    def _append(self, obj):
        os.makedirs(BASE, exist_ok=True)
        with open(_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _rewrite(self, shred_old=False):
        """The file again with only what is there now. With shred_old the previous file is overwritten first."""
        os.makedirs(BASE, exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for e in self.items:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        if shred_old and os.path.isfile(_FILE):
            from . import shred
            shred.overwrite(_FILE)
        os.replace(tmp, _FILE)

    # ---- entries
    def add(self, entry):
        """Called from the main thread."""
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            self.items.append(entry)
            self._by_id[entry["id"]] = entry
            self._append(entry)
        self.added.emit(entry)
        return entry

    def update(self, eid, **fields):
        e = self._by_id.get(eid)
        if e is None:
            return
        with self._lock:
            e.update(fields)
            self._append({"id": eid, "_patch": fields})
        self.updated.emit(eid)

    def remove(self, eid, purge=False):
        """Takes an entry out. purge: rewrite the file so its prompt and settings are gone from it too."""
        self.remove_many([eid], purge)

    def remove_many(self, ids, purge=False):
        ids = set(ids)
        with self._lock:
            self.items = [e for e in self.items if e.get("id") not in ids]
            for i in ids:
                self._by_id.pop(i, None)
            # the file is written again without them (a "deleted" mark would leave their prompts in it);
            # with shredding the old file is overwritten first
            self._rewrite(shred_old=purge)
        for i in ids:
            self.removed.emit(i)

    def get(self, eid):
        return self._by_id.get(eid)

    def recent(self, kind=None, n=None):
        out = [e for e in reversed(self.items) if kind is None or e.get("kind") == kind]
        return out[:n] if n else out

    def compact(self):
        """Rewrite the file without old lines (at start-up, when it got long)."""
        try:
            if os.path.getsize(_FILE) < 2 << 20:
                return
        except OSError:
            return
        with self._lock:
            self._rewrite()

    # ---- collections
    def _load_cols(self):
        try:
            with open(_COLS, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            self.collections = [c for c in d.get("collections", []) if c.get("id") and c.get("name")]
        except (OSError, ValueError):
            self.collections = []

    def _save_cols(self):
        os.makedirs(BASE, exist_ok=True)
        tmp = _COLS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"collections": self.collections}, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, _COLS)
        self.changed.emit()

    def collection(self, cid):
        return next((c for c in self.collections if c["id"] == cid), None)

    def add_collection(self, name, smart=None):
        c = {"id": uuid.uuid4().hex[:8], "name": name.strip() or "Collection", "smart": smart}
        self.collections.append(c)
        self._save_cols()
        return c

    def rename_collection(self, cid, name):
        c = self.collection(cid)
        if c and name.strip():
            c["name"] = name.strip()
            self._save_cols()

    def remove_collection(self, cid):
        """The collection only - its pictures and videos stay."""
        self.collections = [c for c in self.collections if c["id"] != cid]
        for e in self.items:
            if cid in (e.get("cols") or []):
                self.update(e["id"], cols=[x for x in e["cols"] if x != cid])
        self._save_cols()

    def set_in_collection(self, eids, cid, on=True):
        for eid in eids:
            e = self._by_id.get(eid)
            if e is None:
                continue
            cols = list(e.get("cols") or [])
            if on and cid not in cols:
                cols.append(cid)
            elif not on and cid in cols:
                cols.remove(cid)
            else:
                continue
            self.update(eid, cols=cols)
        self.changed.emit()
