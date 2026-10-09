"""Deleting for good: the built-in shredder (overwrites a file with random data, renames it, then deletes it) or Eraser,
when it is installed. Plain deleting moves files to the Recycle Bin.

On SSDs no overwriting is fully reliable: the drive may keep old copies of the data in places a program can't reach.
Files that never reach the disk unencrypted (the vault's private mode) don't have that problem."""
import os
import subprocess
import tempfile
import time
import uuid

_NOWIN = 0x08000000

MODES = [("recycle", "Recycle Bin", "Deleted files can be restored from the Recycle Bin."),
         ("shred", "Shred", "Overwritten with random data (hidden streams too), dates scrubbed, renamed three times, "
                            "then deleted. Built in, nothing to install."),
         ("eraser", "Eraser", "Uses Eraser and the erasure method you picked in it.")]


def eraser_path():
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")):
        if base:
            p = os.path.join(base, "Eraser", "Eraser.exe")
            if os.path.isfile(p):
                return p
    return None


def overwrite(path, passes=1):
    """Overwrites a file in place with random data and flushes it to the drive (it is not deleted)."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b", buffering=0) as fh:
            for _ in range(passes):
                fh.seek(0)
                left = size
                while left > 0:
                    n = min(left, 1 << 20)
                    fh.write(os.urandom(n))
                    left -= n
                os.fsync(fh.fileno())
        return True
    except OSError:
        return False


def extra_streams(path):
    """The alternate data streams of a file (NTFS keeps extra hidden data next to a file, e.g. where it was
    downloaded from). Returns their names like ':Zone.Identifier'."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class FIND_STREAM_DATA(ctypes.Structure):
        _fields_ = [("StreamSize", ctypes.c_longlong), ("cStreamName", ctypes.c_wchar * 296)]
    k32 = ctypes.WinDLL("kernel32")
    k32.FindFirstStreamW.restype = wintypes.HANDLE
    k32.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.FindClose.argtypes = [wintypes.HANDLE]
    data = FIND_STREAM_DATA()
    h = k32.FindFirstStreamW(path, 0, ctypes.byref(data), 0)
    if h in (None, wintypes.HANDLE(-1).value):
        return []
    out = []
    try:
        while True:
            name = data.cStreamName                     # '::$DATA' is the file itself
            if name and name != "::$DATA" and name.endswith(":$DATA"):
                out.append(name[:-len(":$DATA")])
            if not k32.FindNextStreamW(h, ctypes.byref(data)):
                break
    finally:
        k32.FindClose(h)
    return out


def special_storage(path):
    """'compressed', 'sparse' or '' - NTFS stores such files in a way overwriting in place can't fully reach."""
    if os.name != "nt":
        return ""
    import ctypes
    a = ctypes.windll.kernel32.GetFileAttributesW(path)
    if a == -1 or a == 0xFFFFFFFF:
        return ""
    return "compressed" if a & 0x800 else ("sparse" if a & 0x200 else "")


def _scrub_times(path):
    try:
        os.utime(path, (0, 0))          # 1970: the real dates are gone from the entry before it is deleted
    except OSError:
        pass


def shred_file(path, passes=None):
    """Overwrite the file and its hidden streams with random data, scrub its dates, rename it to random names a few
    times (so the old name is gone from the folder too), then delete it."""
    if not os.path.isfile(path):
        return True
    if passes is None:
        from .config import cfg
        passes = int(cfg.get("shred_passes", 1) or 1)
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass
    for s in extra_streams(path):
        overwrite(path + s, passes)
        try:
            os.remove(path + s)
        except OSError:
            pass
    overwrite(path, passes)
    _scrub_times(path)
    target = path
    try:
        for _ in range(3):
            nxt = os.path.join(os.path.dirname(path), uuid.uuid4().hex[:len(os.path.basename(path)) or 8])
            os.replace(target, nxt)
            target = nxt
        with open(target, "r+b") as fh:
            fh.truncate(0)
    except OSError:
        target = target if os.path.exists(target) else path
    for _ in range(20):
        try:
            os.remove(target)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.2)
    return not os.path.exists(target)


def eraser_delete(paths, timeout=600):
    """Hands the files to Eraser and waits until they are gone. Returns the ones that are still there."""
    exe = eraser_path()
    paths = [p for p in paths if os.path.isfile(p)]
    if not exe or not paths:
        return paths
    args = [f"file={os.path.abspath(p)}" for p in paths]
    if sum(len(a) + 1 for a in args) > 20000:
        rsp = os.path.join(tempfile.gettempdir(), f"bettercomfy-eraser-{uuid.uuid4().hex}.rsp")
        with open(rsp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f'"{a}"' for a in args))
        cmd = [exe, "erase", "/quiet", "@" + rsp]
    else:
        rsp = None
        cmd = [exe, "erase", "/quiet"] + args
    try:
        subprocess.run(cmd, creationflags=_NOWIN, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return paths
    t0 = time.time()
    while time.time() - t0 < timeout:
        left = [p for p in paths if os.path.exists(p)]
        if not left:
            break
        time.sleep(0.5)
    if rsp:
        try:
            os.remove(rsp)
        except OSError:
            pass
    return [p for p in paths if os.path.exists(p)]


def delete(paths, mode="recycle"):
    """Deletes files the way Settings say. Returns the files that could not be deleted."""
    paths = [p for p in dict.fromkeys(paths) if p and os.path.isfile(p)]
    if not paths:
        return []
    if mode == "recycle":
        from . import system
        system.recycle(paths)
        return [p for p in paths if os.path.exists(p)]
    if mode == "eraser" and eraser_path():
        left = eraser_delete(paths)
        if not left:
            return []
        paths = left                    # whatever Eraser couldn't do goes through the built-in shredder
    return [p for p in paths if not shred_file(p)]


def drive_kind(path):
    """'SSD', 'HDD' or '' for the drive a folder is on (asked once, takes a moment)."""
    p = os.path.abspath(path)
    letter = os.path.splitdrive(p)[0].rstrip(":")
    if not letter:
        return ""
    ps = (f"$d=(Get-Partition -DriveLetter {letter} -ErrorAction Stop | Get-Disk).Number; "
          f"(Get-PhysicalDisk | Where-Object DeviceId -eq $d).MediaType")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=20, creationflags=_NOWIN)
        out = r.stdout.strip().upper()
        return "SSD" if "SSD" in out else ("HDD" if "HDD" in out else "")
    except Exception:
        return ""
