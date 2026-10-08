"""Everything made with Better Comfy (the gallery): one JSON line per picture / video, with its settings."""
import json
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

from .config import BASE

_FILE = os.path.join(BASE, "history.jsonl")


class History(QObject):
    added = Signal(dict)
    removed = Signal(str)
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.items = []
        self._load()

    def _load(self):
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
                    if e.get("_deleted"):
                        self.items = [x for x in self.items if x.get("id") != e.get("id")]
                    elif isinstance(e, dict) and e.get("file"):
                        self.items.append(e)
        except OSError:
            pass
        self.items = [e for e in self.items if os.path.isfile(e.get("file", ""))]

    def _append(self, obj):
        os.makedirs(BASE, exist_ok=True)
        with open(_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def add(self, entry):
        """Called from the main thread."""
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            self.items.append(entry)
            self._append(entry)
        self.added.emit(entry)
        return entry

    def remove(self, eid):
        with self._lock:
            self.items = [e for e in self.items if e.get("id") != eid]
            self._append({"id": eid, "_deleted": True})
        self.removed.emit(eid)

    def get(self, eid):
        return next((e for e in self.items if e.get("id") == eid), None)

    def recent(self, kind=None, n=None):
        out = [e for e in reversed(self.items) if kind is None or e.get("kind") == kind]
        return out[:n] if n else out

    def compact(self):
        """Rewrite the file without deleted lines (at start-up, when it got long)."""
        try:
            if os.path.getsize(_FILE) < 2 << 20:
                return
        except OSError:
            return
        with self._lock:
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for e in self.items:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, _FILE)
