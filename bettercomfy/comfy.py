"""Better Comfy <-> ComfyUI on this PC: finds it (Pinokio, the portable build, ComfyUI Desktop, a folder you pick, or
one already running), starts / stops it, sends it workflows and pictures, follows the progress, takes the results back.

Only ever talks to ComfyUI on this computer (127.0.0.1 / localhost). The workflows use ComfyUI's built-in nodes."""
import base64
import json
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import BASE

DEFAULT_URL = "http://127.0.0.1:8188"
_NOWINDOW = 0x08000000 if os.name == "nt" else 0
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy


class ComfyError(RuntimeError):
    pass


class ComfyUnreachable(ComfyError):
    """Nobody answered (not running, or still starting / restarting)."""


def is_local(url):
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def port_of(url, default=8188):
    try:
        return urllib.parse.urlparse(url).port or default
    except ValueError:
        return default


# ------------------------------------------------------------------------------------------------ finding it

def pinokio_home():
    try:
        with open(os.path.join(os.path.expanduser("~"), ".pinokio", "config.json"), "r", encoding="utf-8") as fh:
            h = json.load(fh).get("home")
        return h if h and os.path.isdir(h) else None
    except (OSError, ValueError):
        return None


def install_at(app_dir, kind="folder", launcher=None):
    """A ComfyUI folder (the one with main.py) and the Python that runs it - or None."""
    if not app_dir or not os.path.isfile(os.path.join(app_dir, "main.py")) or \
            not os.path.isdir(os.path.join(app_dir, "comfy")):
        return None
    py = None
    for cand in (os.path.join(app_dir, "env", "Scripts", "python.exe"), os.path.join(app_dir, "venv", "Scripts", "python.exe"),
                 os.path.join(app_dir, ".venv", "Scripts", "python.exe"),
                 os.path.join(os.path.dirname(app_dir), "python_embeded", "python.exe"),
                 os.path.join(app_dir, "env", "bin", "python")):
        if os.path.isfile(cand):
            py = cand
            break
    ver = ""
    try:
        with open(os.path.join(app_dir, "comfyui_version.py"), "r", encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("__version__"):
                    ver = ln.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return {"kind": kind, "path": os.path.abspath(app_dir), "python": py, "launcher": launcher, "version": ver,
            "models": os.path.join(os.path.abspath(app_dir), "models")}


def sub_dir(install, name):
    """A ComfyUI folder (models, input, output, models/loras …) - ComfyUI Desktop keeps them apart from its code."""
    base = install.get("base") or install["path"]
    return os.path.join(base, *name.split("/"))


def desktop_install():
    """ComfyUI Desktop: its code is inside the app, models / input / output and the Python env in the folder picked
    when it was set up (Documents\\ComfyUI by default)."""
    try:
        with open(os.path.join(os.environ.get("APPDATA", ""), "ComfyUI", "config.json"), "r", encoding="utf-8") as fh:
            base = json.load(fh).get("basePath")
    except (OSError, ValueError):
        base = None
    base = base or os.path.join(os.path.expanduser("~"), "Documents", "ComfyUI")
    code = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "@comfyorgcomfyui-electron", "resources", "ComfyUI")
    py = os.path.join(base, ".venv", "Scripts", "python.exe")
    if not (os.path.isfile(os.path.join(code, "main.py")) and os.path.isfile(py)):
        return None
    it = install_at(code, "desktop")
    if it:
        it.update(python=py, base=base, models=os.path.join(base, "models"))
    return it


def _drives():
    if os.name != "nt":
        return ["/"]
    import string
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]


def _scan_drives(add, budget=4.0):
    """Folders with 'comfy' in their name, at most two levels deep on every drive (quick - only names are read)."""
    t0 = time.time()
    for drive in _drives():
        if time.time() - t0 > budget:
            return
        for pin in ("pinokio", "Pinokio"):
            api = os.path.join(drive, pin, "api")
            try:
                for n in os.listdir(api):
                    add(install_at(os.path.join(api, n, "app"), "pinokio", os.path.join(api, n)))
            except OSError:
                pass
        try:
            top = [e for e in os.scandir(drive) if e.is_dir() and not e.name.startswith(("$", "."))]
        except OSError:
            continue
        for e in top:
            if time.time() - t0 > budget:
                return
            if e.name.lower() in ("windows", "program files", "program files (x86)", "programdata", "recovery",
                                  "system volume information"):
                continue
            cands = [e.path] if "comfy" in e.name.lower() else []
            if not cands:
                try:
                    cands = [s.path for s in os.scandir(e.path) if s.is_dir() and "comfy" in s.name.lower()]
                except OSError:
                    cands = []
            for c in cands:
                add(install_at(c, "portable" if "portable" in c.lower() else "folder") or
                    install_at(os.path.join(c, "ComfyUI"), "portable" if "portable" in c.lower() else "folder"))


def install_from_pick(folder):
    """A folder the user picked: the ComfyUI in it, its ComfyUI / app subfolder, or None."""
    return (install_at(folder) or install_at(os.path.join(folder, "ComfyUI")) or
            install_at(os.path.join(folder, "app")))


def find_installs(extra=None, deep=False):
    """Every ComfyUI found on this PC: a folder chosen before, Pinokio's, the portable one, ComfyUI Desktop's."""
    out, seen = [], set()

    def add(it):
        if it and os.path.normcase(it["path"]) not in seen:
            seen.add(os.path.normcase(it["path"]))
            out.append(it)
    if extra:
        add(install_from_pick(extra))
    home = pinokio_home()
    if home:
        api = os.path.join(home, "api")
        try:
            names = sorted(os.listdir(api))
        except OSError:
            names = []
        for n in names:
            add(install_at(os.path.join(api, n, "app"), "pinokio", os.path.join(api, n)))
    add(desktop_install())
    for base in (os.path.expanduser("~"), os.path.join(os.path.expanduser("~"), "Desktop"),
                 os.path.join(os.path.expanduser("~"), "Downloads"), os.path.join(os.path.expanduser("~"), "Documents"),
                 os.path.join(os.path.expanduser("~"), "pinokio")):
        add(install_at(os.path.join(base, "ComfyUI_windows_portable", "ComfyUI"), "portable"))
        add(install_at(os.path.join(base, "ComfyUI"), "folder"))
        try:
            for n in os.listdir(os.path.join(base, "api")):
                add(install_at(os.path.join(base, "api", n, "app"), "pinokio", os.path.join(base, "api", n)))
        except OSError:
            pass
    if not out or deep:
        _scan_drives(add)
    return out


def pterm_path():
    home = pinokio_home()
    if not home:
        return None
    for c in (os.path.join(home, "bin", "npm", "pterm.cmd"), os.path.join(home, "bin", "npm", "pterm"),
              os.path.join(home, "bin", "pterm")):
        if os.path.isfile(c):
            return c
    return None


def probe(url, timeout=1.5):
    """ComfyUI's system info if it answers at url, else None."""
    if not is_local(url):
        return None
    try:
        with _OPENER.open(url.rstrip("/") + "/system_stats", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d if isinstance(d, dict) and "system" in d else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _port_open(url, timeout=0.35):
    try:
        u = urllib.parse.urlparse(url)
        with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def find_running(saved=None):
    """The address of a ComfyUI running on this PC (the saved one first), or None. The ports are only knocked on
    first (Windows takes seconds to say 'nobody there' to a full request)."""
    cands = []
    for u in [saved, DEFAULT_URL, "http://127.0.0.1:8000", "http://127.0.0.1:8189", "http://127.0.0.1:8288"]:
        if u and is_local(u) and u.rstrip("/") not in cands:
            cands.append(u.rstrip("/"))
    open_ = {}
    ths = [threading.Thread(target=lambda u=u: open_.__setitem__(u, _port_open(u)), daemon=True) for u in cands]
    for t in ths:
        t.start()
    for t in ths:
        t.join(1.5)
    for u in cands:
        if open_.get(u) and probe(u, 3.0):
            return u
    return None


_STARTED = {"proc": None, "log": None}


def clean_env():
    """This PC's environment without Better Comfy's own (its unpacked files, Qt / Python settings)."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEI", "PYINSTALLER", "QT_", "PYTHON", "TCL_", "TK_", "SSL_CERT",
                                        "REQUESTS_CA", "VIRTUAL_ENV", "CONDA_PREFIX"))}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and "_MEI" not in p)
    return env


def log_file():
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    return os.path.join(BASE, "logs", "comfyui.log")


def start(install, port=8188, fast=False, previews=True, via_pinokio=False, reserve_gb=1.2):
    """Start ComfyUI (does not wait). Through Pinokio when asked and possible, else with its own Python - always
    only reachable from this PC."""
    quiet_errors()
    if via_pinokio and install.get("launcher") and pterm_path():
        subprocess.Popen([pterm_path(), "run", install["launcher"]], creationflags=_NOWINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        _STARTED["pinokio"] = install
        _STARTED["t"] = time.time()
        return "pinokio"
    py = install.get("python")
    if not py:
        raise ComfyError("No Python found for ComfyUI in " + install["path"])
    if ours_alive():
        return _STARTED.get("proc")
    args = [py, "-u", "main.py", "--listen", "127.0.0.1", "--port", str(int(port)), "--disable-auto-launch"]
    if install.get("base"):
        args += ["--base-directory", install["base"]]
    method = {True: "latent2rgb", False: None, "fast": "latent2rgb", "sharp": "auto", "off": None}.get(previews, previews)
    if method:
        args += ["--preview-method", method]          # "auto": TAESD when its decoders are there, else latent2rgb
    if fast:
        args += ["--fast", "fp16_accumulation"]
    if reserve_gb and float(reserve_gb) > 0:
        args += ["--reserve-vram", f"{float(reserve_gb):.1f}"]
    env = clean_env()
    env.update(PYTHONIOENCODING="utf-8", TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1")
    log = open(log_file(), "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(args, cwd=install["path"], env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=_NOWINDOW)
    _STARTED.update(proc=proc, log=log, install=install, t=time.time())
    return proc


def started_here():
    p = _STARTED.get("proc")
    return p is not None and p.poll() is None


def _running_exes():
    """{pid: program path} of the programs running now (Windows; quick - no PowerShell)."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes
    psapi, k32 = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    arr = (wintypes.DWORD * 8192)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(got)):
        return {}
    k32.OpenProcess.restype = wintypes.HANDLE
    out = {}
    buf = ctypes.create_unicode_buffer(1024)
    for pid in arr[:got.value // ctypes.sizeof(wintypes.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            n = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                out[int(pid)] = buf.value
        finally:
            k32.CloseHandle(h)
    return out


def _env_dir(install):
    py = (install or {}).get("python")
    return os.path.dirname(os.path.dirname(py)) if py else None


def install_alive(install):
    """A program of this ComfyUI's own Python is running (also after ComfyUI-Manager restarted it)."""
    d = _env_dir(install)
    if not d:
        return False
    d = os.path.normcase(os.path.abspath(d)) + os.sep
    try:
        return any(os.path.normcase(e).startswith(d) for e in _running_exes().values())
    except Exception:
        return False


def ours_alive():
    if started_here():
        return True
    return bool(_STARTED.get("install")) and install_alive(_STARTED["install"])


def anything_started():
    return started_here() or bool(_STARTED.get("pinokio")) or bool(_STARTED.get("install"))


def _kill_in(folder):
    """Stop the programs running from inside a folder (ComfyUI's Python, whoever started it)."""
    if not folder or os.name != "nt":
        return
    want = os.path.normcase(os.path.abspath(folder)) + os.sep
    me = os.getpid()
    for pid, exe in _running_exes().items():
        if pid != me and os.path.normcase(exe).startswith(want):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], creationflags=_NOWINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_started():
    """Stop the ComfyUI Better Comfy started - with everything it started itself."""
    p = _STARTED.get("proc")
    if p is not None and p.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], creationflags=_NOWINDOW,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            else:
                p.terminate()
            p.wait(10)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _STARTED["proc"] = None
    for key in ("pinokio", "install"):
        ins = _STARTED.pop(key, None)
        if ins and ins.get("python") and (key == "pinokio" or install_alive(ins)):
            _kill_in(_env_dir(ins))
    log = _STARTED.get("log")
    if log is not None:
        try:
            log.close()
        except Exception:
            pass
        _STARTED["log"] = None


def stop_install(install):
    """Stop a ComfyUI no matter who started it (its Python is ended - Pinokio itself stays open)."""
    stop_started()
    if install:
        _kill_in(_env_dir(install))


def quiet_errors():
    """No Windows error popups (e.g. 'Unsupported 16-Bit Application' for a broken pip.exe in ComfyUI's venv) from
    this process or anything it starts - the programs that start them just get an error back."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass


def damaged_launchers(install):
    """Program launchers (.exe) in ComfyUI's venv Scripts folder that Windows cannot start (written through a text
    conversion once - every byte above 127 became '?'). ComfyUI itself runs through python.exe and is not affected."""
    out = []
    if not install or not install.get("python"):
        return out
    d = os.path.dirname(install["python"])
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(".exe"):
            continue
        try:
            with open(os.path.join(d, n), "rb") as fh:
                b = fh.read(4096)
            pe = struct.unpack_from("<I", b, 0x3C)[0]
            if b[:2] != b"MZ" or b[pe:pe + 4] != b"PE\x00\x00":
                out.append(n)
        except (OSError, struct.error):
            out.append(n)
    return sorted(out)


def _entry_points(site):
    """{script name: ('module:func', gui?)} from every installed package's entry_points.txt."""
    out = {}
    try:
        dists = [x for x in os.listdir(site) if x.endswith(".dist-info")]
    except OSError:
        return out
    for dist in dists:
        p = os.path.join(site, dist, "entry_points.txt")
        if not os.path.isfile(p):
            continue
        sect = None
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if ln.startswith("[") and ln.endswith("]"):
                        sect = ln[1:-1].strip()
                    elif "=" in ln and sect in ("console_scripts", "gui_scripts"):
                        k, v = ln.split("=", 1)
                        out.setdefault(k.strip().lower(), (v.split("[")[0].strip(), sect == "gui_scripts"))
        except OSError:
            pass
    return out


def _launcher(template, python, spec):
    """A Windows script launcher exactly as pip / distlib makes it: stub exe + #!python + zip(__main__.py)."""
    import io
    import zipfile
    mod, _, func = spec.partition(":")
    func = func.strip() or "main"
    main_py = ("# -*- coding: utf-8 -*-\nimport re\nimport sys\nfrom %s import %s\nif __name__ == '__main__':\n"
               "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n    sys.exit(%s())\n"
               % (mod.strip(), func.split(".")[0], func))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", main_py.encode("utf-8"))
    shebang = (f'#!"{python}"' if " " in python else f"#!{python}").encode("utf-8") + b"\n"
    with open(template, "rb") as fh:
        stub = fh.read()
    return stub + shebang + buf.getvalue()


def repair_launchers(install, out_dir=None):
    """Rebuild the damaged launchers from pip's own templates (nothing is downloaded). The broken files are kept in
    Scripts\\_broken_launchers. out_dir: write the new ones there instead (a dry run). Returns (fixed, left)."""
    bad = damaged_launchers(install)
    if not bad:
        return [], []
    scripts = os.path.dirname(install["python"])
    env = os.path.dirname(scripts)
    site = os.path.join(env, "Lib", "site-packages")
    tdir = os.path.join(site, "pip", "_vendor", "distlib")
    eps = _entry_points(site)
    fixed, left = [], []
    for n in bad:
        stem = n[:-4].lower()
        ep = eps.get(stem)
        if ep is None and stem.startswith("pip"):
            ep = ("pip._internal.cli.main:main", False)
        tmpl = os.path.join(tdir, "w64.exe" if ep and ep[1] else "t64.exe")
        if ep is None or not os.path.isfile(tmpl):
            left.append(n)
            continue
        py = os.path.join(scripts, "pythonw.exe" if ep[1] else "python.exe")
        data = _launcher(tmpl, py, ep[0])
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, n), "wb") as fh:
                fh.write(data)
        else:
            bak = os.path.join(scripts, "_broken_launchers")
            os.makedirs(bak, exist_ok=True)
            src = os.path.join(scripts, n)
            try:
                if not os.path.exists(os.path.join(bak, n)):
                    os.replace(src, os.path.join(bak, n))
                with open(src + ".new", "wb") as fh:
                    fh.write(data)
                os.replace(src + ".new", src)
            except OSError:
                left.append(n)
                continue
        fixed.append(n)
    return fixed, left


def log_tail(n=60):
    try:
        with open(log_file(), "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:])
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------ model files

MODEL_DIRS = {"checkpoints": ("checkpoints",), "unet": ("diffusion_models", "unet"), "clip": ("text_encoders", "clip"),
              "vae": ("vae",), "loras": ("loras",), "upscale": ("upscale_models",),
              "interp": ("frame_interpolation",)}
MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")


def model_path(install, kind, name):
    """The file behind a model name as ComfyUI lists it (sub\\file), or None."""
    if not install or not name:
        return None
    for d in MODEL_DIRS.get(kind, (kind,)):
        p = os.path.join(install.get("models") or os.path.join(install["path"], "models"), d, name.replace("/", os.sep))
        if os.path.isfile(p):
            return p
    return None


def local_lists(install):
    """The model files in a ComfyUI's model folders (Pinokio links them in from its drive - followed)."""
    out = {k: [] for k in MODEL_DIRS}
    if not install:
        return out
    base = install.get("models") or os.path.join(install["path"], "models")
    for key, dirs in MODEL_DIRS.items():
        seen = set()
        for d in dirs:
            root = os.path.join(base, d)
            for r, _, files in os.walk(root, followlinks=True):
                for f in files:
                    if f.lower().endswith(MODEL_EXT):
                        rel = os.path.relpath(os.path.join(r, f), root)
                        if rel not in seen:
                            seen.add(rel)
                            out[key].append(rel)
        out[key].sort(key=str.lower)
    return out


def safetensors_header(path, limit=64 << 20):
    """The JSON header of a .safetensors file (tensor names + __metadata__) - only the first bytes are read."""
    try:
        with open(path, "rb") as fh:
            n = struct.unpack("<Q", fh.read(8))[0]
            if n <= 0 or n > limit:
                return None
            return json.loads(fh.read(n).decode("utf-8", "replace"))
    except (OSError, ValueError, struct.error):
        return None


# ------------------------------------------------------------------------------------------------ talking to it

class _WS:
    """A small websocket reader (ComfyUI's progress messages) - local connections only."""

    def __init__(self, url, client_id):
        u = urllib.parse.urlparse(url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = f"/ws?clientId={client_id}"
        self.sock = None
        self._buf = b""

    def connect(self, timeout=5):
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            ch = s.recv(4096)
            if not ch:
                raise ComfyError("ComfyUI closed the progress connection")
            buf += ch
        head, rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ComfyError("ComfyUI did not accept the progress connection")
        self.sock, self._buf = s, rest
        s.settimeout(None)

    def _read(self, n):
        while len(self._buf) < n:
            ch = self.sock.recv(65536)
            if not ch:
                raise ConnectionError("closed")
            self._buf += ch
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send(self, op, data=b""):
        mask = os.urandom(4)
        hdr = bytes([0x80 | op])
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        """(opcode, payload) of the next whole message."""
        msg, first_op = b"", None
        while True:
            b0, b1 = self._read(2)
            fin, op = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if b1 & 0x80:
                mk = self._read(4)
                data = bytes(b ^ mk[i % 4] for i, b in enumerate(self._read(n)))
            else:
                data = self._read(n)
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op == 0x8:
                raise ConnectionError("closed")
            if op in (0x1, 0x2):
                first_op, msg = op, data
            elif op == 0x0:
                msg += data
            if fin and first_op is not None:
                return first_op, msg

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass


class Client:
    def __init__(self, url=DEFAULT_URL):
        if not is_local(url):
            raise ComfyError("Better Comfy only connects to ComfyUI on this PC (127.0.0.1 / localhost).")
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self._info = None

    def _req(self, path, data=None, headers=None, timeout=30, method=None):
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {}, method=method)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            body = ex.read().decode("utf-8", "replace")
            raise ComfyError(explain(body) or f"ComfyUI answered {ex.code}") from None
        except (urllib.error.URLError, OSError) as ex:
            raise ComfyUnreachable(f"ComfyUI is not reachable at {self.url} ({ex})") from None

    def get_json(self, path, timeout=30):
        return json.loads(self._req(path, timeout=timeout).decode("utf-8"))

    def post_json(self, path, obj, timeout=30):
        out = self._req(path, json.dumps(obj).encode("utf-8"), {"Content-Type": "application/json"}, timeout)
        try:
            return json.loads(out.decode("utf-8")) if out else {}
        except ValueError:
            return {}

    def stats(self):
        return self.get_json("/system_stats", 5)

    def info(self, refresh=False):
        if self._info is None or refresh:
            self._info = self.get_json("/object_info", 60)
        return self._info

    def has(self, node):
        return node in self.info()

    def choices(self, node, name):
        try:
            spec = self.info()[node]["input"]
        except KeyError:
            return []
        v = (spec.get("required") or {}).get(name) or (spec.get("optional") or {}).get(name)
        if not v:
            return []
        if isinstance(v[0], list):
            return list(v[0])
        if len(v) > 1 and isinstance(v[1], dict):
            return list(v[1].get("options") or [])
        return []

    def defaults(self, node):
        """{input: default} of a node's required inputs that have one (or a choice list: its first)."""
        try:
            req = self.info()[node]["input"].get("required") or {}
        except KeyError:
            return {}
        out = {}
        for k, v in req.items():
            if not isinstance(v, (list, tuple)) or not v:
                continue
            if isinstance(v[0], list):
                if v[0]:
                    out[k] = v[0][0]
            elif len(v) > 1 and isinstance(v[1], dict):
                if "default" in v[1]:
                    out[k] = v[1]["default"]
                elif v[1].get("options"):
                    out[k] = v[1]["options"][0]
        return out

    def upload(self, data, name, subfolder="bettercomfy", mime="image/png"):
        """Put a picture into ComfyUI's input folder; returns the name its LoadImage node wants."""
        bnd = "----bettercomfy" + uuid.uuid4().hex
        parts = []
        for k, v in (("type", "input"), ("subfolder", subfolder), ("overwrite", "true")):
            parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{bnd}--\r\n".encode())
        out = json.loads(self._req("/upload/image", b"".join(parts),
                                   {"Content-Type": f"multipart/form-data; boundary={bnd}"}, 60).decode("utf-8"))
        sub = out.get("subfolder") or ""
        return (sub + "/" if sub else "") + out["name"]

    def queue(self, prompt):
        r = self.post_json("/prompt", {"prompt": prompt, "client_id": self.client_id}, 60)
        if r.get("node_errors"):
            raise ComfyError(explain(json.dumps(r)))
        if "prompt_id" not in r:
            raise ComfyError(explain(json.dumps(r)) or "ComfyUI did not take the job")
        return r["prompt_id"]

    def history(self, pid):
        return self.get_json("/history/" + urllib.parse.quote(pid), 30).get(pid)

    def fetch(self, f):
        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                    "type": f.get("type", "output")})
        return self._req("/view?" + q, timeout=120)

    def interrupt(self):
        try:
            self._req("/interrupt", b"", {"Content-Type": "application/json"}, 10, method="POST")
        except ComfyError:
            pass

    def unqueue(self, pid):
        try:
            self.post_json("/queue", {"delete": [pid]}, 10)
        except ComfyError:
            pass

    def forget(self, pid):
        """Takes a finished job out of ComfyUI's history (it keeps every prompt in memory until it restarts)."""
        try:
            self.post_json("/history", {"delete": [pid]}, 10)
        except ComfyError:
            pass

    def free(self):
        """Let ComfyUI unload its models (the graphics card is free again)."""
        try:
            self.post_json("/free", {"unload_models": True, "free_memory": True}, 20)
        except ComfyError:
            pass

    def queue_size(self):
        try:
            q = self.get_json("/queue", 5)
            return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        except ComfyError:
            return 0

    def gpu(self):
        """(name, total GB, free GB) of its graphics card, or None."""
        try:
            d = self.stats().get("devices") or []
        except ComfyError:
            return None
        if not d:
            return None
        dev = d[0]
        name = str(dev.get("name", "")).split(" : ")[0]
        name = re.sub(r"^\s*cuda:\d+\s*", "", name).replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()
        return name, float(dev.get("vram_total", 0)) / 2 ** 30, float(dev.get("vram_free", 0)) / 2 ** 30

    def run(self, prompt, labels=None, on_progress=None, on_preview=None, cancel=None, weights=None, capture=None,
            forget=False):
        """forget: a private job - ComfyUI is told to drop it from its history however it ends (done, failed or
        stopped). A stopped job only lands there a moment later, so it is asked again a few times."""
        box = {}
        try:
            return self._run(prompt, labels, on_progress, on_preview, cancel, weights, capture, box)
        finally:
            if forget and box.get("pid"):
                self._forget_soon(box["pid"])

    def _forget_soon(self, pid):
        self.forget(pid)

        def later():
            for wait in (2, 3, 5, 5):
                time.sleep(wait)
                self.forget(pid)
        threading.Thread(target=later, daemon=True).start()

    def _run(self, prompt, labels, on_progress, on_preview, cancel, weights, capture, box):
        """Queue a prompt and wait for it. on_progress(fraction, text); on_preview(jpeg/png bytes).
        weights: {node id: share of the work}. capture: (node id, list) - the pictures that node sends over the
        connection (SaveImageWebsocket) are put into the list instead of being shown as previews; nothing of them is
        written to disk. Returns its history entry."""
        ws = _WS(self.url, self.client_id)
        try:
            ws.connect()
        except Exception:
            ws = None
            if capture:
                raise ComfyError("The live connection to ComfyUI couldn't be opened, so a private result couldn't be "
                                 "received. Nothing was made.")
        pid = self.queue(prompt)
        box["pid"] = pid
        labels, weights = labels or {}, weights or {}
        total_w = sum(weights.values()) or 1.0
        done_w = 0.0
        cur, cur_frac = None, 0.0
        state = {"error": None}

        def report(text=None):
            if on_progress:
                f = (done_w + weights.get(cur, 0.0) * cur_frac) / total_w
                on_progress(min(0.99, f), text or labels.get(cur, "Preparing"))

        def poll_done():
            h = self.history(pid)
            if h and (h.get("status") or {}).get("completed") is not None:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    state["error"] = exec_error(msgs[-1][1] if msgs else {})
                return h
            return None

        if on_progress:
            on_progress(0.0, "Waiting in ComfyUI's queue")
        last_poll = time.time()
        try:
            while True:
                if cancel and cancel():
                    self.unqueue(pid)
                    self.interrupt()
                    raise InterruptedError("stopped")
                if ws is None:
                    time.sleep(1.0)
                    if poll_done() is not None:
                        break
                    continue
                ws.sock.settimeout(1.0)
                try:
                    op, data = ws.recv()
                except socket.timeout:
                    if time.time() - last_poll > 5:
                        last_poll = time.time()
                        if poll_done() is not None:
                            break
                    continue
                except (ConnectionError, OSError):
                    ws = None
                    if capture:
                        self.unqueue(pid)
                        self.interrupt()
                        raise ComfyError("The live connection to ComfyUI broke, so the private result couldn't be "
                                         "received.")
                    continue
                if op == 0x2:
                    ev = struct.unpack(">I", data[:4])[0] if len(data) > 8 else 0
                    if capture and ev == 1 and cur == capture[0]:
                        capture[1].append(data[8:])
                    elif on_preview and len(data) > 8:
                        if ev == 1:
                            on_preview(data[8:])
                        elif ev == 4:
                            ml = struct.unpack(">I", data[4:8])[0]
                            on_preview(data[8 + ml:])
                    continue
                try:
                    msg = json.loads(data.decode("utf-8"))
                except ValueError:
                    continue
                t, d = msg.get("type"), msg.get("data") or {}
                if d.get("prompt_id") not in (None, pid):
                    continue
                if t == "executing":
                    if cur is not None:
                        done_w += weights.get(cur, 0.0)
                    cur, cur_frac = d.get("node"), 0.0
                    if cur is None and d.get("prompt_id") == pid:
                        break
                    report()
                elif t == "progress":
                    cur = d.get("node", cur)
                    mx = max(1, int(d.get("max", 1)))
                    cur_frac = float(d.get("value", 0)) / mx
                    report(f"{labels.get(cur, 'Working')}  {int(d.get('value', 0))}/{mx}")
                elif t == "execution_cached":
                    for n in d.get("nodes") or []:
                        done_w += weights.get(n, 0.0)
                    report()
                elif t == "execution_error":
                    state["error"] = exec_error(d)
                    break
                elif t == "execution_interrupted":
                    state["error"] = "Stopped in ComfyUI"
                    break
                elif t == "execution_success":
                    break
        finally:
            if ws is not None:
                ws.close()
        if state["error"]:
            raise ComfyError(state["error"])
        for _ in range(20):
            h = self.history(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    raise ComfyError(exec_error(msgs[-1][1] if msgs else {}))
                return h
            time.sleep(0.5)
        raise ComfyError("ComfyUI finished but its result was not found")


def exec_error(d):
    node = d.get("node_type") or ""
    msg = (d.get("exception_message") or "").strip()
    low = msg.lower()
    if "out of memory" in low or "allocation on device" in low:
        return ("The graphics card ran out of memory. Pick a faster preset (smaller / shorter), close other programs "
                "that use the graphics card, or raise 'Keep free on the graphics card' in Settings.")
    return f"{node}: {msg}" if node else (msg or "ComfyUI stopped with an error")


def explain(body):
    """ComfyUI's error answer in plain words."""
    try:
        d = json.loads(body)
    except ValueError:
        return body.strip()[:400]
    out = []
    e = d.get("error")
    if isinstance(e, dict) and e.get("message"):
        out.append(e["message"])
    for nid, ne in (d.get("node_errors") or {}).items():
        for er in ne.get("errors", []):
            det = er.get("details") or er.get("message") or ""
            if "not in" in det and "[" in det:
                det = det.split("[", 1)[0].strip().rstrip(":") + " (it is not in your ComfyUI's model folders)"
            out.append(f"{ne.get('class_type', nid)}: {det}")
    return "\n".join(out)[:1200]


def new_seed():
    return random.randint(1, 2 ** 48)


def result_images(history):
    """Every picture a finished job left (in node order)."""
    out = []
    for nid in sorted((history or {}).get("outputs", {}), key=lambda k: int(k) if str(k).isdigit() else 0):
        for f in history["outputs"][nid].get("images", []) or []:
            out.append(f)
    return out
