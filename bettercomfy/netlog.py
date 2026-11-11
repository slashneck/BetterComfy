"""Every connection this app opens, for Settings, Privacy. Kept in memory for this session only, never written anywhere.
Python's own audit hook sees each socket, whichever part of the app opened it. ComfyUI is a program of its own: what it
(or an add-on inside it) does is not in here."""
import ipaddress
import sys
import threading
import time

_lock = threading.Lock()
_rows = {}                              # (host, port) -> {"host", "port", "local", "times", "first", "last"}
_asked = threading.local()              # the name this thread looked up last: its next connection goes there
_on = False

_KNOWN = (("civitai.com", "Civitai"), ("github.com", "GitHub"), ("githubusercontent.com", "GitHub"),
          ("huggingface.co", "Hugging Face"), ("hf.co", "Hugging Face"))


def _local(ip):
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return ip == "localhost"


def _hook(event, args):
    if event == "socket.getaddrinfo":
        if args and args[0]:
            host = args[0]
            _asked.name = host.decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else str(host)
    elif event == "socket.connect" and len(args) > 1:
        addr = args[1]
        if not (isinstance(addr, tuple) and len(addr) >= 2):
            return
        ip, port = str(addr[0]), addr[1]
        host = getattr(_asked, "name", None) or ip
        now = time.time()
        with _lock:
            r = _rows.get((host, port))
            if r is None:
                r = _rows[(host, port)] = {"host": host, "port": port, "local": _local(ip), "times": 0, "first": now}
            r["times"] += 1
            r["last"] = now


def start():
    """Once, as early as possible (an audit hook can't be taken away again; it costs next to nothing)."""
    global _on
    if not _on:
        _on = True
        sys.addaudithook(_hook)


def rows():
    """Newest first."""
    with _lock:
        return sorted((dict(r) for r in _rows.values()), key=lambda r: -r["last"])


def what(row):
    if row["local"]:
        return "This PC (ComfyUI)"
    h = row["host"].lower()
    return next((name for dom, name in _KNOWN if h == dom or h.endswith("." + dom)), "Unknown")
