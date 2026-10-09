"""Better Comfy's own settings and data folder (%LOCALAPPDATA%/BetterComfy). Plain JSON, written atomically."""
import copy
import json
import os
import sys

APP_NAME = "Better Comfy"
APP_ID = "BetterComfy"
VERSION = "1.1.0"
REPO = "slashneck/BetterComfy"
REPO_URL = "https://github.com/" + REPO

# BETTERCOMFY_DATA_DIR keeps a test run's settings, gallery and queue apart from the real ones
BASE = os.environ.get("BETTERCOMFY_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                                             APP_ID)
_FILE = os.path.join(BASE, "settings.json")


def _home_dir(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


DEFAULTS = {
    # ComfyUI link
    "comfy_folder": "",               # "" = found by itself (Pinokio, portable, desktop)
    "comfy_url": "http://127.0.0.1:8188",
    "auto_start": True,               # start ComfyUI when something is generated and it is not running
    "via_pinokio": False,             # start it through Pinokio instead of its own Python
    "stop_on_exit": True,             # stop the ComfyUI Better Comfy started when it closes
    "reserve_vram": 1.2,              # GB kept free on the graphics card (Windows slows down a lot when it is full)
    "fast_mode": False,               # --fast fp16_accumulation
    "live_previews": "fast",          # off | fast (latent2rgb) | sharp (TAESD when installed)
    # output
    "image_dir": _home_dir("Pictures", "Better Comfy"),
    "video_dir": _home_dir("Videos", "Better Comfy"),
    "date_folders": True,
    "embed_metadata": True,           # the settings (and a ComfyUI workflow) inside the PNG
    # queue
    "after_queue": "nothing",         # nothing | free | stop_comfy | close | sleep | shutdown
    "notify_done": True,
    "sound_done": True,
    "free_after_queue": False,
    # look & feel
    "accent": "#8B7CF6",
    "animations": True,
    "start_page": "image",
    "check_updates": True,            # ask GitHub for a new version (the only thing that goes online)
    "welcomed": False,                # the first start screen was shown
    "close_to_tray": False,
    "confirm_delete": True,
    "delete_mode": "recycle",         # recycle | shred (built-in) | eraser
    "shred_passes": 1,                # 1 is enough on any drive of the last 20 years; 3 for those who want it
    "gallery_sort": "new",
    "capture_block_app": False,
    "comfy_forget_private": True,     # after private jobs ComfyUI clears its cache (and unloads the models)       # the whole app is left out of screenshots, recordings and screen sharing
    "helper_dir": "",                 # where the prompt helper was set up ("" = in the app's data folder)
    "helper_model": "",
    "helper_button": True,
    "helper_free_min": 5,             # minutes unused until its memory is given back, 0 = keep it loaded
    "helper_threads": 0,              # 0 = half the processor's threads
    "tag_suggest": "auto",            # auto (for tag based models) | on | off
    "tag_blacklist": None,            # tags never suggested or added by the helper; None = the starting list
    "vault_autolock": 10,             # minutes without mouse / keyboard, 0 = never
    "vault_lock_minimized": True,
    "vault_hide_capture": True,       # the window is left out of screenshots and recordings while the vault is open
    # remembered
    "image_state": {},
    "video_state": {},
    "prompt_history": [],
    "rates": {},                      # learned seconds per unit of work (for the time estimates)
    "window": {},
}


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self.data.update(d)
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        v = self.data.get(key)
        if v is None:
            return copy.deepcopy(DEFAULTS.get(key, default)) if key in DEFAULTS else default
        return v

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def reset(self, keep=("image_state", "video_state", "prompt_history", "rates", "window")):
        kept = {k: self.data.get(k) for k in keep}
        self.data = copy.deepcopy(DEFAULTS)
        self.data.update({k: v for k, v in kept.items() if v is not None})
        self.save()

    def save(self):
        try:
            os.makedirs(BASE, exist_ok=True)
            tmp = _FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, _FILE)
        except OSError:
            pass


cfg = Config()


def data_path(*parts):
    p = os.path.join(BASE, *parts)
    os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


def resource(*parts):
    """A file shipped with the app (inside the exe when frozen)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def frozen():
    return bool(getattr(sys, "frozen", False))
