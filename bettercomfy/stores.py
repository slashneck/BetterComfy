"""Drives: a folder on a USB stick or another drive where pictures, videos and vault files can be kept (Settings,
Drives). What is on a drive shows in the gallery while it is plugged in; vault files only once the vault is open too.
A drive is known by its volume serial number, so it is found again under another drive letter.

On the drive:  <folder>/Pictures, <folder>/Videos, <folder>/.thumbs, <folder>/.vault (encrypted, like the one here)."""
import ctypes
import os
import shutil
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from .config import cfg

try:                                            # an empty card reader must not bring up "There is no disk in the drive"
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)
except Exception:
    pass

_REMOVABLE, _FIXED, _REMOTE = 2, 3, 4


def _letters():
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return []
    return [chr(65 + i) + ":\\" for i in range(26) if mask & (1 << i)]


def drive_type(root):
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
    except Exception:
        return 0


def volume(root):
    """(serial, label, file system) of a drive root, or (None, '', '')."""
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint32()
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, ctypes.byref(serial),
                                                          None, None, fs, 261)
    except Exception:
        ok = 0
    return (serial.value, name.value, fs.value) if ok else (None, "", "")


def _root_of(path):
    return os.path.splitdrive(os.path.abspath(path))[0] + "\\"


def all_stores():
    return list(cfg.get("stores") or [])


def get(sid):
    return next((s for s in all_stores() if s.get("id") == sid), None)


def add(path):
    """A folder as a drive. A drive root gets a 'Better Comfy' folder on it. Returns the new entry."""
    path = os.path.abspath(path)
    if os.path.splitdrive(path)[1] in ("", "\\", "/"):
        path = os.path.join(_root_of(path), "Better Comfy")
    root = _root_of(path)
    serial, label, _fs = volume(root)
    if serial is None:
        raise OSError("That drive can't be read.")
    for s in all_stores():
        if s.get("serial") == serial and os.path.normcase(os.path.splitdrive(s["path"])[1]) == \
                os.path.normcase(os.path.splitdrive(path)[1]):
            raise OSError("That folder is in the list already.")
    os.makedirs(path, exist_ok=True)
    kind = "usb" if drive_type(root) == _REMOVABLE else "drive"
    s = {"id": uuid.uuid4().hex[:8], "path": path, "serial": serial, "label": label, "kind": kind}
    cfg.set("stores", all_stores() + [s])
    watcher.check(force=True)
    return s


def remove(sid):
    cfg.set("stores", [s for s in all_stores() if s.get("id") != sid])
    watcher.check(force=True)


def root(sid):
    """The folder of a drive while it is plugged in, else None."""
    return watcher.roots.get(sid)


def online():
    return [s for s in all_stores() if watcher.roots.get(s["id"])]


def label(s, letter=True):
    """'USB stick (E:)', 'Backup (F:)', 'USB stick, not plugged in'."""
    name = s.get("label") or ("USB stick" if s.get("kind") == "usb" else "Drive")
    r = watcher.roots.get(s["id"])
    if not letter:
        return name
    return f"{name} ({r[:2]})" if r else f"{name}, not plugged in"


def file_system(sid):
    r = root(sid)
    return volume(_root_of(r))[2] if r else ""


def free(sid):
    r = root(sid)
    try:
        return shutil.disk_usage(r).free if r else 0
    except OSError:
        return 0


def store_of(path):
    """The drive a path lies on (the id), or None for this PC."""
    p = os.path.normcase(os.path.abspath(path or ""))
    for sid, r in watcher.roots.items():
        rr = os.path.normcase(r.rstrip("\\/")) + os.sep
        if p.startswith(rr):
            return sid
    return None


def _find(s):
    """Where the drive's folder is now: its letter may have changed since it was added."""
    path = s["path"]
    rest = os.path.splitdrive(path)[1]
    first = _root_of(path)
    for r in [first] + [x for x in _letters() if x != first]:
        t = drive_type(r)
        if t == _REMOTE and r != first:
            continue                            # network drives can hang: only the one it was added from
        if t not in (_REMOVABLE, _FIXED, _REMOTE):
            continue
        serial = volume(r)[0]
        if serial is not None and serial == s.get("serial"):
            cand = r[:2] + rest
            return cand if os.path.isdir(cand) else None
    return None


class _Watcher(QObject):
    """Looks every few seconds whether a drive was plugged in or out."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.roots = {}
        self._mask = None
        self._tick = 0
        self._timer = None

    def start(self):
        self.check(force=True)
        if self._timer is None:
            self._timer = QTimer(self, interval=3000, timeout=self.check)
            self._timer.start()

    def check(self, force=False):
        self._tick += 1
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            mask = 0
        if not force and mask == self._mask and self._tick % 5:
            return
        self._mask = mask
        roots = {}
        for s in all_stores():
            r = _find(s)
            if r:
                roots[s["id"]] = r
        if roots != self.roots:
            self.roots = roots
            self.changed.emit()


watcher = _Watcher()


# ---- paths on a drive
def pictures_dir(sid, kind):
    r = root(sid)
    d = os.path.join(r, "Pictures" if kind == "image" else "Videos")
    os.makedirs(d, exist_ok=True)
    return d


def thumbs_dir(sid):
    d = os.path.join(root(sid), ".thumbs")
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        _hide(d)
    return d


def vault_dir(sid):
    d = os.path.join(root(sid), ".vault")
    if not os.path.isdir(d):
        os.makedirs(os.path.join(d, "files"), exist_ok=True)
        _hide(d)
    return d


def _hide(path):
    try:
        ctypes.windll.kernel32.SetFileAttributesW(ctypes.c_wchar_p(path), 0x2)
    except Exception:
        pass
