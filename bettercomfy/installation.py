"""The per-user install the setup creates: the "Installed apps" entry and uninstalling. Uninstalling removes the
program only. Pictures, videos, settings and the gallery stay on the PC."""
import os
import subprocess
import sys
import tempfile

from .config import APP_NAME, BASE, REPO_URL, VERSION

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\BetterComfy"


def message(title, text, ask=False):
    """A plain Windows message box (works before any window exists). With ask: True for Yes."""
    import ctypes
    flags = (0x04 | 0x20) if ask else 0x40                 # MB_YESNO | MB_ICONQUESTION, else MB_ICONINFORMATION
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x10000) == 6       # MB_SETFOREGROUND


def _size_kb(folder):
    total = 0
    for r, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total // 1024


def refresh_uninstall_entry(folder):
    if os.environ.get("BETTERCOMFY_DATA_DIR"):
        return                                             # test runs never touch the real "Installed apps" list
    try:
        import winreg
        exe = os.path.join(folder, "BetterComfy.exe")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            for name, val in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_NAME),
                              ("DisplayIcon", exe), ("InstallLocation", folder),
                              ("UninstallString", f'"{exe}" --uninstall'), ("URLInfoAbout", REPO_URL)):
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
            for name, val in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", min(2 ** 31 - 1, _size_kb(folder)))):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)
    except OSError:
        pass


def _points_to(lnk, exe):
    """Does this shortcut start `exe`? (So only our own shortcuts are removed.) A .lnk keeps its target path as
    plain text inside, in the PC's code page or as UTF-16."""
    try:
        with open(lnk, "rb") as fh:
            data = fh.read(64 * 1024).lower()
    except OSError:
        return False
    exe = exe.lower()
    return exe.encode("utf-16-le") in data or exe.encode("mbcs", "replace") in data


def uninstall():
    from . import system, updater
    folder = os.path.dirname(os.path.abspath(sys.executable))
    if not os.path.isfile(os.path.join(folder, updater.MANIFEST)):
        message(APP_NAME, "This copy of Better Comfy wasn't installed by the setup, so you can simply delete its folder.")
        return 1
    if not message("Uninstall Better Comfy?", "Better Comfy is removed from this PC. Your pictures, videos, settings "
                                              "and gallery stay where they are.", ask=True):
        return 1
    exe = os.path.join(folder, "BetterComfy.exe")
    for d in (system.desktop_dir(), system.start_menu_dir()):
        lnk = os.path.join(d or "", APP_NAME + ".lnk")
        if os.path.isfile(lnk) and _points_to(lnk, exe):
            try:
                os.remove(lnk)
            except OSError:
                pass
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    for sub in ("updates", "thumbs", "frames", "masks", "logs"):
        import shutil
        shutil.rmtree(os.path.join(BASE, sub), ignore_errors=True)   # caches only; settings and the gallery stay
    # the running program can't delete itself: a short-lived script removes the listed files once it has closed
    files = updater.read_manifest(folder) + [updater.MANIFEST]
    dirs = set()
    for f in files:
        d = os.path.dirname(os.path.join(folder, f))
        while len(d) > len(folder) and d.lower().startswith(folder.lower()):
            dirs.add(d)
            d = os.path.dirname(d)
    dirs = sorted(dirs, key=len, reverse=True) + [folder]
    pid = os.getpid()
    lines = ["@echo off", "set n=0", ":wait",
             f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (set /a n+=1 & ping 127.0.0.1 -n 2 >nul '
             f'& if %n% lss 60 goto wait)', "ping 127.0.0.1 -n 2 >nul"]
    lines += [f'del /f /q "{os.path.join(folder, f)}" >nul 2>&1' for f in files]
    # deleted files can stay "in use" for a moment (an antivirus scan): the folders are removed twice
    for _ in range(2):
        lines.append("ping 127.0.0.1 -n 3 >nul")
        lines += [f'rd "{d}" >nul 2>&1' for d in dirs]
    lines.append('del /f /q "%~f0" >nul 2>&1')
    script = os.path.join(tempfile.gettempdir(), f"bettercomfy-uninstall-{os.getpid()}.cmd")
    with open(script, "w", encoding="mbcs", errors="replace") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    message(APP_NAME, "Better Comfy is uninstalled. Your pictures and videos are still in your Pictures and Videos "
                      "folders.")
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=0x08000000, close_fds=True)   # hidden console: cmd needs one
    return 0
