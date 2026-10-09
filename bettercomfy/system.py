"""Windows things: shortcuts, folders in Explorer, the recycle bin, sleep / shut down, dark title bars."""
import ctypes
import os
import subprocess
import sys
import uuid

from .config import APP_NAME, frozen, resource

_NOWIN = 0x08000000 if os.name == "nt" else 0


def known_folder(guid):
    """A Windows known folder (follows OneDrive / moved Desktop)."""
    if os.name != "nt":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]
    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    p = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
        return None
    out = p.value
    ctypes.windll.ole32.CoTaskMemFree(p)
    return out


def desktop_dir():
    return known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641") or os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def launch_target():
    """(program, arguments, working folder) that start this app."""
    if frozen():
        return sys.executable, "", os.path.dirname(sys.executable)
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return (pyw if os.path.isfile(pyw) else py), f'"{main}"', os.path.dirname(main)


def make_shortcut(folder, name=APP_NAME):
    """A .lnk to this app in `folder`. Returns its path."""
    target, args, cwd = launch_target()
    lnk = os.path.join(folder, name + ".lnk")
    icon = sys.executable if frozen() else resource("assets", "icon.ico")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:BC_LNK);"
          "$s.TargetPath=$env:BC_TARGET;$s.Arguments=$env:BC_ARGS;$s.WorkingDirectory=$env:BC_CWD;"
          "$s.IconLocation=$env:BC_ICON;$s.Description='Better Comfy - image and video generation';$s.Save()")
    env = dict(os.environ, BC_LNK=lnk, BC_TARGET=target, BC_ARGS=args, BC_CWD=cwd, BC_ICON=icon + ",0")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       creationflags=_NOWIN, capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not os.path.isfile(lnk):
        raise RuntimeError((r.stderr or "Could not create the shortcut").strip()[:300])
    return lnk


def shortcut_exists(folder, name=APP_NAME):
    return os.path.isfile(os.path.join(folder, name + ".lnk"))


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    os.startfile(path)


def reveal(path):
    """Explorer with the file selected."""
    if os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_folder(os.path.dirname(path))


def recycle(paths):
    """Move files to the recycle bin (can be restored from there)."""
    paths = [os.path.abspath(p) for p in paths if p and os.path.exists(p)]
    if not paths:
        return True
    if os.name != "nt":
        for p in paths:
            os.remove(p)
        return True
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                                         # FO_DELETE
    op.pFrom = "\0".join(paths) + "\0\0"
    op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004        # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


def shutdown_pc():
    subprocess.Popen(["shutdown", "/s", "/t", "5", "/c", "Better Comfy: the queue is done."], creationflags=_NOWIN)


def sleep_pc():
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=_NOWIN)


def dark_title_bar(win_id, color="#09090A"):
    """Windows 10/11: a dark caption (11: in the app's own black)."""
    if os.name != "nt":
        return
    try:
        hwnd = int(win_id)
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        c = color.lstrip("#")
        bgr = ctypes.c_int(int(c[4:6] + c[2:4] + c[0:2], 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(bgr), ctypes.sizeof(bgr))       # caption colour
        txt = ctypes.c_int(0x00F5F4F4)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))       # caption text
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))  # rounded corners
    except Exception:
        pass


def app_user_model_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BetterComfy.App")
    except Exception:
        pass


def disk_free(path):
    import shutil
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def idle_seconds():
    """How long the mouse and keyboard have not been touched."""
    if os.name != "nt":
        return 0.0
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
