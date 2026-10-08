"""Finds new releases on GitHub and installs them. This is the only thing Better Comfy does online, and it can be
switched off in Settings. An update only swaps the program files named in the release's file list, so settings, the
gallery and everything you made are never touched."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

from .config import APP_ID, BASE, REPO, REPO_URL, VERSION, frozen

MANIFEST = "bettercomfy-files.txt"
EXE = "BetterComfy.exe"
UPDATES = os.path.join(BASE, "updates")
CHECK_EVERY = 6 * 3600


def parse(v):
    out = []
    for part in str(v).strip().lstrip("vV").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def install_dir():
    return os.path.dirname(os.path.abspath(sys.executable)) if frozen() else None


def can_self_update():
    """Only installed copies update themselves; a copy run from source never overwrites itself."""
    d = install_dir()
    return bool(d) and os.path.isfile(os.path.join(d, MANIFEST))


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_ID}/{VERSION}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(current=VERSION):
    """The newest release when it is newer than this one: {version, notes, page, url, size, sha256} - else None."""
    try:
        with _get(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
            rel = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None                              # nothing released yet
        raise
    tag = rel.get("tag_name") or ""
    if parse(tag) <= parse(current):
        return None
    ver = ".".join(str(x) for x in parse(tag))
    want = f"BetterComfy-{ver}-win-x64.zip"
    z = next((a for a in rel.get("assets") or [] if a.get("name", "").lower() == want.lower()), None)
    if z is None:
        return None
    sha = None
    dig = z.get("digest") or ""
    if dig.startswith("sha256:"):
        sha = dig[7:]
    if not sha:
        s = next((a for a in rel.get("assets") or [] if a.get("name", "").lower() == (want + ".sha256").lower()), None)
        if s:
            with _get(s["browser_download_url"]) as r:
                sha = r.read().decode("utf-8", "replace").split()[0]
    return {"version": ver, "notes": rel.get("body") or "", "page": rel.get("html_url") or REPO_URL,
            "url": z["browser_download_url"], "size": int(z.get("size") or 0), "sha256": sha}


def download(info, progress=None, cancel=lambda: False):
    """Downloads and checks the release, then unpacks it. Returns the unpacked program folder."""
    sha = (info.get("sha256") or "").lower()
    if len(sha) != 64:
        raise RuntimeError("This release has no checksum, so it can't be checked. Download it from GitHub instead.")
    os.makedirs(UPDATES, exist_ok=True)
    zpath = os.path.join(UPDATES, f"BetterComfy-{info['version']}.zip")
    h = hashlib.sha256()
    with _get(info["url"], timeout=60) as r, open(zpath, "wb") as out:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            if cancel():
                raise InterruptedError("cancelled")
            b = r.read(1 << 16)
            if not b:
                break
            out.write(b)
            h.update(b)
            done += len(b)
            if progress and total:
                progress(min(1.0, done / total))
    if h.hexdigest().lower() != sha:
        os.remove(zpath)
        raise RuntimeError("The download was damaged, so nothing was changed. Try again later.")
    stage = os.path.join(UPDATES, f"BetterComfy-{info['version']}")
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                raise RuntimeError(f"Unexpected file in the download: {n}")
        z.extractall(stage)
    os.remove(zpath)
    app = stage if os.path.isfile(os.path.join(stage, EXE)) else next(
        (os.path.join(stage, d) for d in os.listdir(stage) if os.path.isfile(os.path.join(stage, d, EXE))), None)
    if not app or not os.path.isfile(os.path.join(app, MANIFEST)):
        raise RuntimeError("The download doesn't contain Better Comfy")
    return app


def start_install(staged, restart=True):
    """Starts the downloaded copy, which swaps the files as soon as this one has closed."""
    args = [os.path.join(staged, EXE), "--apply-update", install_dir(), str(os.getpid())]
    if not restart:
        args.append("--no-restart")
    subprocess.Popen(args, close_fds=True, creationflags=0x00000008)          # DETACHED_PROCESS


def read_manifest(folder):
    out = []
    with open(os.path.join(folder, MANIFEST), "r", encoding="utf-8") as fh:
        for raw in fh:
            rel = raw.strip().replace("/", "\\")
            if not rel:
                continue
            if os.path.isabs(rel) or ".." in rel.split("\\") or rel.lower() == MANIFEST:
                raise RuntimeError(f"Unexpected entry in the file list: {rel}")
            out.append(rel)
    return out


def _retry(fn, tries=40):
    for i in range(tries):
        try:
            return fn()
        except (PermissionError, OSError):
            if i == tries - 1:
                raise
            time.sleep(0.25)           # antivirus or the closing app still holding the file


def remove_empty_dirs(root):
    for r, dirs, files in sorted(os.walk(root), key=lambda x: -len(x[0])):
        if r != root and not os.listdir(r):
            try:
                os.rmdir(r)
            except OSError:
                pass


def apply_update(target, pid, restart=True):
    """Runs from the downloaded copy: waits for the old one to close, removes files the new version no longer has,
    copies the new ones in and starts Better Comfy again. Nothing outside the file lists is touched."""
    from . import installation
    src = os.path.dirname(os.path.abspath(sys.executable))
    try:
        t0 = time.time()
        while _alive(pid) and time.time() - t0 < 60:
            time.sleep(0.3)
        if not os.path.isfile(os.path.join(target, MANIFEST)):
            raise RuntimeError("That folder isn't an installed copy of Better Comfy")
        new, old = read_manifest(src), read_manifest(target)
        newl = {x.lower() for x in new}
        for rel in old:
            if rel.lower() not in newl:
                p = os.path.join(target, rel)
                if os.path.isfile(p):
                    _retry(lambda p=p: os.remove(p))
        for rel in new:
            to = os.path.join(target, rel)
            os.makedirs(os.path.dirname(to), exist_ok=True)
            _retry(lambda rel=rel, to=to: shutil.copyfile(os.path.join(src, rel), to))
        shutil.copyfile(os.path.join(src, MANIFEST), os.path.join(target, MANIFEST))
        remove_empty_dirs(target)
        installation.refresh_uninstall_entry(target)
        if restart:
            subprocess.Popen([os.path.join(target, EXE), "--updated"], close_fds=True, creationflags=0x00000008)
        return 0
    except Exception as ex:
        installation.message("Better Comfy", f"The update couldn't be finished:\n{ex}\n\nYour settings, gallery and "
                                             f"everything you made are untouched. The new version is on {REPO_URL}.")
        exe = os.path.join(target, EXE)
        if restart and os.path.isfile(exe):
            subprocess.Popen([exe], close_fds=True, creationflags=0x00000008)
        return 1


def _alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))    # SYNCHRONIZE
        if not h:
            return False
        r = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
        ctypes.windll.kernel32.CloseHandle(h)
        return r == 0x102                                                      # WAIT_TIMEOUT: still running
    except Exception:
        return False


def clean_up():
    """Leftovers of a finished update (not while running from them)."""
    if not os.path.isdir(UPDATES) or os.path.abspath(sys.executable).lower().startswith(UPDATES.lower()):
        return

    def work():
        for _ in range(5):
            shutil.rmtree(UPDATES, ignore_errors=True)
            if not os.path.isdir(UPDATES):
                return
            time.sleep(3)
    threading.Thread(target=work, daemon=True).start()
