"""Clean up: what generating leaves behind besides the pictures and videos themselves. Each part can be looked at
(how much is there) and cleaned on its own. Files go the way Settings, Deleting says (shredded or simply deleted;
the Recycle Bin makes no sense for leftovers)."""
import os
import re

from . import comfy
from .config import BASE, cfg

_UPLOAD = re.compile(r"^(bc_|bcp_).+\.png$")     # what Better Comfy put into ComfyUI's input folder


def _files(folder, match=None):
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if match is None or match.match(f):
                out.append(os.path.join(root, f))
    return out


def _size(paths):
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def _remove(paths):
    """Deleted (or shredded when Settings say so). Returns how many could not be removed (in use)."""
    from . import shred
    mode = cfg.get("delete_mode", "recycle")
    if mode in ("shred", "eraser"):
        return len(shred.delete(paths, mode))
    left = 0
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            left += 1
    return left


class Cleanup:
    """The parts, each: key, title, what it is, scan() -> (count, bytes), clean() -> how many were left."""

    def __init__(self, app):
        self.app = app

    def _install(self):
        return self.app.link.install()

    def parts(self):
        return [
            ("prompts", "Prompt history", "The prompts listed under the clock next to a prompt"),
            ("jobs", "Finished jobs in the queue list", "They keep the prompt and settings of what was made"),
            ("uploads", "Better Comfy's uploads in ComfyUI", "Start, source and mask pictures in ComfyUI's input folder"),
            ("temp", "ComfyUI's temporary folder", "ComfyUI's copies of results (it empties it itself when it starts)"),
            ("history", "ComfyUI's job history", "Prompts of past jobs ComfyUI keeps in its memory until it restarts"),
            ("thumbs", "Thumbnails without a picture", "Small previews of pictures that are not in the gallery any more"),
            ("work", "Masks and video frames", "Masks from Edit a part, last frames for Extend"),
            ("logs", "Logs", "Better Comfy's and ComfyUI's logs (an error can show a prompt)"),
        ]

    def _paths(self, key):
        ins = self._install()
        if key == "uploads":
            return _files(comfy.sub_dir(ins, "input"), _UPLOAD) if ins else []
        if key == "temp":
            return _files(comfy.sub_dir(ins, "temp")) if ins else []
        if key == "thumbs":
            used = {os.path.normcase(e.get("thumb") or "") for e in self.app.history.items}
            return [p for p in _files(os.path.join(BASE, "thumbs")) if os.path.normcase(p) not in used]
        if key == "work":
            return _files(os.path.join(BASE, "masks")) + _files(os.path.join(BASE, "frames"))
        if key == "logs":
            out = _files(os.path.join(BASE, "logs"))
            if ins:
                out += _files(os.path.join(ins["path"], "user"), re.compile(r"^comfyui.*\.log$"))
            return out
        return []

    def scan(self, key):
        """(count, bytes or None) - how much of it there is now."""
        if key == "prompts":
            return len(cfg.get("prompt_history") or []), None
        if key == "jobs":
            return len([j for j in self.app.queue.jobs if j["status"] not in ("queued", "running")]), None
        if key == "history":
            url = comfy.find_running()
            if not url:
                return 0, None
            try:
                return len(comfy.Client(url).get_json("/history", timeout=10) or {}), None
            except Exception:
                return 0, None
        paths = self._paths(key)
        return len(paths), _size(paths)

    def busy(self):
        return bool(self.app.queue.running())

    def clean(self, key):
        """Cleans one part. Returns how many things could not be removed (files in use)."""
        if key == "prompts":
            cfg.set("prompt_history", [])
            return 0
        if key == "jobs":
            self.app.queue.clear_finished()
            return 0
        if key == "history":
            url = comfy.find_running()
            if url:
                try:
                    comfy.Client(url).post_json("/history", {"clear": True}, 10)
                except Exception:
                    return 1
            return 0
        if key in ("temp", "uploads") and self.busy():
            return -1                                  # not while something is being made
        return _remove(self._paths(key))
