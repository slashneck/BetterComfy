"""What changed in this version: shown once after an update, and any time from Settings. Written into the app, so showing
it needs no connection."""
from PySide6.QtWidgets import QDialog, QVBoxLayout

from .config import VERSION, cfg
from .widgets import Scroll, button, hrow, label

NOTES = {
    "1.5.0": [
        ("Characters", "Make a character from a few pictures, pick it on the Image page and put it in any scene."),
        ("Train", "Train a LoRA of your own from pictures, right in the app."),
        ("Storage", "A new tab that shows what takes up space. Delete, move or compress big pictures and videos."),
        ("Duplicates", "Finds look-alike pictures without AI and stacks them. Nothing is deleted: go through a stack, "
                       "keep what you want, mark the rest."),
        ("Export", "Export many pictures at once, with the prompt and settings taken out if you like. New results can "
                   "also get a second, clean copy."),
        ("Side by side", "Select 2 to 4 pictures to compare them. Zoom and move one and all of them follow."),
        ("Undo", "A few seconds to take back a delete, a cleared prompt or Unmark all. Ctrl+Z works too."),
        ("Quick search", "Ctrl+K jumps to any tab, setting, model, LoRA or character. F1 shows all shortcuts."),
        ("Panic key", "Pick a key in Settings, Privacy: it hides the app and locks the vault, even inside a game."),
        ("Connection log", "Settings, Privacy lists everything the app connected to since it opened."),
        ("Pick models by picture", "The model list on the Image page is a grid with covers and a search."),
        ("Backup", "Your settings, presets, notes and characters in one zip."),
        ("Look", "Light mode and animated backgrounds, an interface size setting, and cards and tab groups that fold "
                 "away. A clean start each time the app opens."),
    ],
}


class WhatsNew(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("What's new")
        self.resize(560, 560)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(label(f"What's new in {VERSION}", "H2"))
        body = Scroll((0, 4, 8, 4), 12)
        for title, text in NOTES.get(VERSION, []):
            body.add(label(title, "H3"), label(text, "Muted", wrap=True))
        body.end()
        v.addWidget(body, 1)
        v.addWidget(hrow(None, button("Got it", self.accept, "Accent")))


def show_if_new(parent):
    """Once per version, and only after an update: a brand new install has the welcome instead."""
    seen = cfg.get("seen_version")
    cfg.set("seen_version", VERSION)
    if seen != VERSION and cfg.get("welcomed") and VERSION in NOTES:
        WhatsNew(parent).exec()
