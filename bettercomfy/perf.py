"""Performance: what on this PC (and in Better Comfy) makes generating faster, each with a recommendation and a
button to the setting. Only reads how Windows is set up; changes nothing by itself."""
import json
import os
import re
import subprocess

from .config import cfg

_NOWIN = 0x08000000

_PS = r"""
$o = @{}
try { $cs = Get-CimInstance Win32_ComputerSystem; $o.auto = [bool]$cs.AutomaticManagedPagefile;
      $o.ram = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1) } catch {}
try { $o.pf = @(Get-CimInstance Win32_PageFileSetting | ForEach-Object { [int]$_.MaximumSize }) } catch {}
try { $o.gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch {}
try { $d = Get-Partition -DriveLetter '__L__' -ErrorAction Stop | Get-Disk | Get-PhysicalDisk;
      $o.disk = "$($d.MediaType)" } catch {}
$o | ConvertTo-Json -Compress
"""

_PLANS = {"8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "high", "e9a42b02-d5df-448d-aa00-03f14749eb61": "high",
          "381b4222-f694-41f0-9685-ff5bb260df2e": "balanced", "a1841308-3541-4fab-bc81-f71556f20b4a": "saver"}


def _system(models_dir):
    letter = (os.path.splitdrive(os.path.abspath(models_dir or "C:\\"))[0] or "C:")[0]
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS.replace("__L__", letter)],
                             capture_output=True, text=True, timeout=30, creationflags=_NOWIN).stdout
        info = json.loads(out.strip() or "{}")
    except Exception:
        info = {}
    try:
        out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10,
                             creationflags=_NOWIN).stdout
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out, re.I)
        info["plan"] = _PLANS.get(m.group(0).lower(), "other") if m else None
    except Exception:
        info["plan"] = None
    return info


def checks(models_dir=None):
    """[{key, title, state: ok | tip | info, text, action: (button text, action key) or None}] - slow (asks
    Windows): call it in a thread."""
    info = _system(models_dir)
    out = []
    # Better Comfy's own
    fast = bool(cfg.get("fast_mode", True))
    out.append({"key": "fast", "title": "Faster maths", "state": "ok" if fast else "tip",
                "text": ("On: ComfyUI calculates in a quicker way that makes no visible difference." if fast else
                         "Off. Turning it on makes pictures and videos faster (about 15 to 20 %) with no visible "
                         "difference. Used the next time ComfyUI starts."),
                "action": None if fast else ("Turn on", "fast_on")})
    out.append({"key": "presets", "title": "Video presets", "state": "info",
                "text": "Fast HD makes Balanced-size videos in about half the time: made small, then sharpened with "
                        "an upscale model. Speed models (Lightning, lightx2v) need no more than 4 steps.",
                "action": None})
    if cfg.get("comfy_forget_private", True):
        out.append({"key": "private", "title": "Private jobs", "state": "info",
                    "text": "After a queue with private jobs, ComfyUI's memory is cleared, so the next job loads its "
                            "models again. Several private jobs in one queue load them only once (Settings, Privacy).",
                    "action": None})
    # Windows
    pf = [int(x) for x in (info.get("pf") or []) if isinstance(x, (int, float))]
    managed = info.get("auto") or (pf and all(x == 0 for x in pf))
    if info.get("auto") is None and not pf:
        state, text = "info", "Could not be read."
    elif managed:
        state, text = "ok", "Managed by Windows: it grows when big models need room."
    else:
        mb = sum(pf)
        state = "tip" if mb < 32768 else "ok"
        text = (f"Fixed at {mb / 1024:.0f} GB. Big models need room beside the memory: let Windows manage it "
                "(Advanced, Change…, Automatically manage paging file size) so a job doesn't stall or stop."
                if state == "tip" else f"Fixed at {mb / 1024:.0f} GB: enough room.")
    out.append({"key": "pagefile", "title": "Windows page file", "state": state, "text": text,
                "action": ("Open the setting", "pagefile") if state != "ok" else None})
    ram = info.get("ram")
    if ram:
        out.append({"key": "ram", "title": "Memory", "state": "ok" if ram >= 31 else "tip",
                    "text": (f"{ram:.0f} GB: video models fit beside each other." if ram >= 31 else
                             f"{ram:.0f} GB. Video models are big: with 32 GB or more they stay in memory between "
                             "jobs instead of being read again.")})
    plan = info.get("plan")
    out.append({"key": "power", "title": "Power mode", "state": "ok" if plan == "high" else "tip",
                "text": ("High performance: the processor runs at full speed." if plan == "high" else
                         "Best performance (or the High performance plan) lets the processor run at full speed: "
                         "faster loading, decoding and prompt helper."),
                "action": ("Open the setting", "power") if plan != "high" else None})
    disk = (info.get("disk") or "").upper()
    if disk:
        hdd = "HDD" in disk
        out.append({"key": "disk", "title": "Where the models are", "state": "tip" if hdd else "ok",
                    "text": ("On a hard drive: big models load slowly from it. An SSD loads them many times faster."
                             if hdd else "On an SSD: models load quickly.")})
    if any("nvidia" in (g or "").lower() for g in info.get("gpu") or []):
        out.append({"key": "sysmem", "title": "NVIDIA: memory fallback", "state": "info",
                    "text": "In the NVIDIA Control Panel, Manage 3D settings, CUDA - Sysmem Fallback Policy: "
                            "'Prefer No Sysmem Fallback' lets ComfyUI move models itself instead of the driver "
                            "slowly borrowing system memory - often faster. If jobs then run out of memory, set it "
                            "back.",
                    "action": ("Open NVIDIA Control Panel", "nvidia")})
    reserve = float(cfg.get("reserve_vram", 1.2) or 0)
    out.append({"key": "reserve", "title": "Keep free (graphics memory)", "state": "tip" if reserve > 1.5 else "ok",
                "text": (f"{reserve:.1f} GB kept free for Windows. Less leaves more room for models (faster); more "
                         "keeps the desktop smooth while generating. Settings, ComfyUI.")})
    return out


def act(key):
    """Opens the Windows (or driver) setting for a recommendation. Returns False when it couldn't."""
    try:
        if key == "pagefile":
            subprocess.Popen(["SystemPropertiesPerformance.exe"], creationflags=_NOWIN)
        elif key == "power":
            os.startfile("ms-settings:powersleep")
        elif key == "nvidia":
            for p in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation",
                                   "Control Panel Client", "nvcplui.exe"),):
                if os.path.isfile(p):
                    subprocess.Popen([p])
                    return True
            os.startfile("shell:AppsFolder\\NVIDIACorp.NVIDIAControlPanel_56jybvy8sckqj!NVIDIACorp.NVIDIAControlPanel")
        else:
            return False
        return True
    except Exception:
        return False
