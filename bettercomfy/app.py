"""Better Comfy's window: the rail on the left, the pages, the ComfyUI status and the queue at the top, toasts, the
tray icon, and what happens when the queue is done."""
import os
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage, QKeySequence,
                           QPalette, QShortcut)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QDialog, QHBoxLayout,
                               QMainWindow, QScrollArea, QSlider, QStyleFactory, QMenu, QMessageBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from . import comfy, icons, media, system, theme as T
from .config import APP_ID, APP_NAME, BASE, VERSION, cfg, resource
from .gallery_page import GalleryPage
from .history import History
from .image_page import ImagePage
from .jobs import Queue, sweep_leftovers
from .link import Link
from .loras_page import LorasPage
from .models_page import CheckpointsPage
from .queue_page import QueuePage
from .settings_page import SettingsPage
from .vault import vault
from .video_page import VideoPage
from .widgets import FadeStack, NavRail, PulseDot, Ring, Toasts, button, human_time, label

PAGES = [("image", "Image", "image"), ("video", "Video", "video"), ("queue", "Queue", "queue"),
         ("gallery", "Gallery", "gallery"), ("loras", "LoRAs", "lora"), ("models", "Checkpoints", "chip")]
BOTTOM = [("settings", "Settings", "settings")]


class Countdown(QDialog):
    """'Shutting down in 30 s' - with Cancel."""

    def __init__(self, parent, what, seconds=30):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setModal(True)
        self.left = seconds
        self.what = what
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 20)
        v.setSpacing(12)
        v.addWidget(label("The queue is done", "H2"))
        self.msg = label("", "Muted")
        v.addWidget(self.msg)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(button("Cancel", self.reject, None))
        h.addWidget(button("Now", self.accept, "Accent"))
        v.addLayout(h)
        self.t = QTimer(self, interval=1000, timeout=self._tick)
        self.t.start()
        self._tick(first=True)

    def _tick(self, first=False):
        if not first:
            self.left -= 1
        if self.left <= 0:
            self.t.stop()
            self.accept()
            return
        self.msg.setText(f"{self.what} in {self.left} seconds.")


class TopBar(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedHeight(64)
        h = QHBoxLayout(self)
        h.setContentsMargins(28, 10, 20, 6)
        h.setSpacing(12)
        self.title = label("", "H1")
        self.sub = label("", "Faint")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        h.addLayout(col)
        h.addStretch(1)
        # comfy pill
        self.pill = QWidget()
        self.pill.setObjectName("Pill")
        self.pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.pill.setStyleSheet(f"#Pill {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 17px; }}"
                                f"#Pill:hover {{ border-color: {T.BORDER_HI}; }}")
        self.pill.setCursor(Qt.CursorShape.PointingHandCursor)
        ph = QHBoxLayout(self.pill)
        ph.setContentsMargins(10, 5, 14, 5)
        ph.setSpacing(6)
        self.dot = PulseDot(9)
        self.ptext = label("Looking for ComfyUI…")
        self.ptext.setStyleSheet("font-size: 12px;")
        self.vram = label("", "Faint")
        ph.addWidget(self.dot)
        ph.addWidget(self.ptext)
        ph.addWidget(self.vram)
        self.pill.mousePressEvent = lambda e: self.win.comfy_menu(self.pill.mapToGlobal(QPoint(0, self.pill.height() + 4)))
        h.addWidget(self.pill)
        # queue
        self.qw = QWidget()
        self.qw.setObjectName("Pill2")
        self.qw.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.qw.setStyleSheet(f"#Pill2 {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 17px; }}"
                              f"#Pill2:hover {{ border-color: {T.BORDER_HI}; }}")
        self.qw.setCursor(Qt.CursorShape.PointingHandCursor)
        qh = QHBoxLayout(self.qw)
        qh.setContentsMargins(4, 2, 14, 2)
        qh.setSpacing(6)
        self.ring = Ring(30)
        self.qtext = label("Queue empty")
        self.qtext.setStyleSheet("font-size: 12px;")
        qh.addWidget(self.ring)
        qh.addWidget(self.qtext)
        self.qw.mousePressEvent = lambda e: self.win.go("queue")
        h.addWidget(self.qw)
        self.upd = button("  Update", lambda: self.win.update_now(), "Accent", "download")
        self.upd.setStyleSheet("QPushButton#Accent { border-radius: 17px; padding: 7px 14px; }")
        self.upd.hide()
        h.insertWidget(h.count() - 2, self.upd)

    def set_page(self, page):
        self.title.setText(page.title)
        self.sub.setText(page.subtitle)


class Window(QMainWindow):
    _joined = Signal(dict)
    _shredded = Signal(int, int)
    _moved = Signal(list, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(resource("assets", "icon.ico")))
        self.history = History()
        self.history.compact()
        self.link = Link()
        self.queue = Queue(self.link, self.history)
        from .update_service import UpdateService
        self.updates = UpdateService()
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.rail = NavRail(PAGES, BOTTOM)
        self.rail.changed.connect(self.go)
        h.addWidget(self.rail)
        main = QWidget()
        main.setObjectName("Main")
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        self.top = TopBar(self)
        mv.addWidget(self.top)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T.BORDER};")
        mv.addWidget(line)
        self.stack = FadeStack()
        mv.addWidget(self.stack, 1)
        h.addWidget(main, 1)
        self.pages = {}
        for key, cls in (("image", ImagePage), ("video", VideoPage), ("queue", QueuePage), ("gallery", GalleryPage),
                         ("loras", LorasPage), ("models", CheckpointsPage), ("settings", SettingsPage)):
            pg = cls(self)
            self.pages[key] = pg
            self.stack.addWidget(pg)
            if hasattr(pg, "toast") and hasattr(pg.toast, "connect"):
                pg.toast.connect(self.toast)
        self.pages["settings"].accent_changed.connect(self.apply_accent)
        self.toasts = Toasts(root)
        from .viewer import Viewer
        self.viewer = Viewer(root)
        self.viewer.app = self
        self.link.status.connect(self._status)
        threading.Thread(target=lambda: sweep_leftovers(self.link.install()), daemon=True).start()
        self.queue.changed.connect(self._queue)
        self.queue.job_changed.connect(lambda _j: self._queue())
        self.queue.finished.connect(self._queue_done)
        self.queue.output.connect(self._output)
        self.queue.done.connect(self._job_done)
        self.queue.changed.connect(self._sync_stop_buttons)
        for k in ("image", "video"):
            self.pages[k].bar.stop_current.connect(self.stop_current)
        self._joined.connect(self._join_done)
        self._shredded.connect(self._shred_done)
        self._moved.connect(self._moved_done)
        self._vt = QTimer(self, interval=15000, timeout=self._vault_tick)
        self._vt.start()
        from . import assistant
        self._ht = QTimer(self, interval=30000, timeout=assistant.idle_stop)      # the helpers' memory back
        self._ht.start()
        self._views = []
        for i, (k, _t, _i) in enumerate(PAGES + BOTTOM):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=k: self.go(k))
        self._tray()
        self._restore()
        self.go(cfg.get("start_page") or "image", animate=False)
        self.link.begin()
        self.updates.changed.connect(self._update_state)
        self.updates.begin()
        self._queue()
        if self.queue.paused and self.queue.pending():
            QTimer.singleShot(1200, lambda: self.toast(f"{len(self.queue.pending())} jobs from last time are waiting - "
                                                       "Resume them on the Queue page.", "info"))

    # ---------------------------------------------------------------- navigation
    def go(self, key, animate=True):
        if key not in self.pages:
            return
        pg = self.pages[key]
        self.rail.select(key)
        if animate:
            self.stack.switch(self.stack.indexOf(pg))
        else:
            self.stack.setCurrentWidget(pg)
        self.top.set_page(pg)

    def toast(self, text, kind="info"):
        self.toasts.show_toast(text, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toasts"):
            self.toasts.relayout()

    # ---------------------------------------------------------------- comfy status
    def _status(self, st):
        s = st.get("state")
        if s == "running":
            self.top.dot.set(T.GOOD)
            gpu = st.get("gpu")
            self.top.ptext.setText("ComfyUI")
            self.top.vram.setText(f"{gpu[0]}  ·  {gpu[1] - gpu[2]:.1f} / {gpu[1]:.0f} GB" if gpu else st.get("version", ""))
            self.top.pill.setToolTip(f"ComfyUI {st.get('version', '')} at {st.get('url')}")
        elif s == "starting":
            self.top.dot.set(T.WARN, pulse=True)
            self.top.ptext.setText("Starting ComfyUI…")
            self.top.vram.setText("")
        elif s == "missing":
            self.top.dot.set(T.BAD)
            self.top.ptext.setText("ComfyUI not found")
            self.top.vram.setText("")
        else:
            self.top.dot.set(T.TEXT3)
            self.top.ptext.setText("ComfyUI off")
            self.top.vram.setText("starts when you generate" if cfg.get("auto_start") else "")

    def _update_state(self):
        u = self.updates
        b = self.top.upd
        if u.state == "ready" and u.info:
            b.setText(f"  Update to {u.info['version']}")
            b.setToolTip("Restarts Better Comfy with the new version (also happens by itself when you close it)")
            b.show()
        elif u.state == "available" and u.info:
            b.setText(f"  {u.info['version']} is out")
            b.setToolTip("A new version is on GitHub")
            b.show()
        else:
            b.hide()
        self.pages["settings"].update_state()

    def update_now(self):
        u = self.updates
        if u.state == "available" and u.info:
            QDesktopServices.openUrl(QUrl(u.info["page"]))
            return
        if u.state != "ready":
            return
        if self.queue.running():
            if QMessageBox.question(self, APP_NAME, "Something is being made right now. Stop it and update?") != \
                    QMessageBox.StandardButton.Yes:
                return
        if not self._parked_ok():
            return
        if u.install(restart=True):
            self._updating = True
            self._quit()

    def comfy_menu(self, pos):
        m = QMenu(self)
        if self.link.running():
            m.addAction(icons.icon("power", "#FF8A8A", 16), "Stop ComfyUI").triggered.connect(self.link.stop)
            m.addAction(icons.icon("chip", "#A1A1AA", 16), "Free graphics memory").triggered.connect(self.free_vram)
            m.addAction(icons.icon("external", "#A1A1AA", 16), "Open ComfyUI in the browser").triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(self.link.url())) if comfy.is_local(self.link.url()) else None)
        else:
            a = m.addAction(icons.icon("power", T.GOOD, 16), "Start ComfyUI")
            a.triggered.connect(self._start)
            a.setEnabled(self.link.install() is not None and self.link.state.get("state") != "starting")
        m.addSeparator()
        m.addAction(icons.icon("refresh", "#A1A1AA", 16), "Read the model lists again").triggered.connect(
            self.link.refresh_models)
        m.addAction(icons.icon("settings", "#A1A1AA", 16), "ComfyUI settings…").triggered.connect(
            lambda: self.go("settings"))
        m.exec(pos)

    def _start(self):
        try:
            self.link.start()
        except Exception as ex:
            QMessageBox.warning(self, "ComfyUI", str(ex))

    def free_vram(self):
        url = self.link.url()
        threading.Thread(target=lambda: comfy.Client(url).free(), daemon=True).start()
        self.toast("Graphics memory freed.", "ok")

    # ---------------------------------------------------------------- queue
    def _queue(self):
        q = self.queue
        n = q.active_count()
        f, left = q.overall()
        self.top.ring.set(f if q.running() else 0.0, n)
        if not n:
            self.top.qtext.setText("Queue empty")
        else:
            cur = q.running()
            t = f"{n} in queue" + ("  ·  paused" if q.paused else "")
            if left:
                t += f"  ·  {human_time(left)}"
            self.top.qtext.setText(t)
            if cur:
                self.top.qw.setToolTip(f"{cur.get('title')}\n{cur.get('text')}")
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME}  ·  " + (f"{n} in queue" if n else "idle"))
            self.pause_act.setText("Resume queue" if q.paused else "Pause queue")

    def _output(self, jid, e):
        if e.get("vault"):
            self.pages["image" if e.get("kind") == "image" else "video"].pane.show_private(e)
            self.pages["gallery"].vault_changed()

    def _queue_done(self, made):
        if getattr(self, "_private_ran", False) and cfg.get("comfy_forget_private", True):
            # nothing of a private job stays in ComfyUI's memory (its cache of pictures goes with the models)
            self._private_ran = False
            url = self.link.url()
            threading.Thread(target=lambda: comfy.Client(url).free(), daemon=True).start()
        if not made:
            return
        if cfg.get("sound_done"):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                QApplication.beep()
        if cfg.get("notify_done") and self.tray and not self.isActiveWindow():
            self.tray.showMessage(APP_NAME, "The queue is done.", QIcon(resource("assets", "icon.ico")), 5000)
        self.toast("The queue is done.", "ok")
        if cfg.get("free_after_queue"):
            self.free_vram()
        what = cfg.get("after_queue", "nothing")
        if what == "free":
            self.free_vram()
        elif what == "stop_comfy":
            self.link.stop()
            self.toast("ComfyUI stopped.", "info")
        elif what in ("close", "shutdown") and vault.parked:
            # private results waiting for the vault only live in memory: closing would lose them
            self._show()
            msg = "Private results are waiting for the vault. Unlock it to keep them, Better Comfy stays open."
            if self.tray:
                self.tray.showMessage(APP_NAME, msg, QIcon(resource("assets", "icon.ico")), 8000)
            self.toast(msg, "warn")
        elif what in ("close", "sleep", "shutdown"):
            text = {"close": "Better Comfy closes", "sleep": "The PC goes to sleep", "shutdown": "The PC shuts down"}[what]
            self.showNormal()
            self.raise_()
            d = Countdown(self, text, 30)
            if d.exec() != QDialog.DialogCode.Accepted:
                self.toast("Cancelled.", "info")
                return
            if what == "close":
                self._quit()
            elif what == "sleep":
                system.sleep_pc()
            else:
                self._shutdown_cleanup()
                system.shutdown_pc()
                self._quit()

    # ---------------------------------------------------------------- results
    def stop_current(self):
        """Only the picture / video being made right now stops; everything else in the queue goes on."""
        j = self.queue.running()
        if j is None:
            return
        i = next((k for k, it in enumerate(j.get("items") or []) if it.get("status") == "running"), None)
        if i is None:
            return
        self.queue.stop_item(j["id"], i)
        self.toast("Stopping this one - the queue goes on.", "info")

    def _sync_stop_buttons(self, *_):
        j = self.queue.running()
        for k in ("image", "video"):
            self.pages[k].bar.set_running(j is not None and j.get("kind") == k,
                                          "picture" if k == "image" else "video")

    def _job_done(self, jid):
        self._prune_memory()
        j = self.queue.job(jid)
        if j:
            from .jobs import is_private
            if is_private(j.get("params") or {}):
                self._private_ran = True
        if j and j["kind"] == "image" and j["params"].get("variants"):
            self.open_compare(jid)

    def open_compare(self, group):
        from .tools import CompareView
        items = sorted([e for e in self.history.items if e.get("group") == group], key=lambda e: e.get("created", ""))
        if not items:
            self.toast("The pictures of this comparison are not there any more.", "warn")
            return
        v = CompareView(self, items, "Comparison")
        v.use.connect(lambda e: self.result_action("reuse", e))
        v.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        v.show()
        self._views = [x for x in self._views if x.isVisible()] + [v]

    def _queue_image(self, params, title, thumb=None):
        from . import workflows as W
        ip = self.pages["image"]
        kind = self.link.kind_of(params.get("ckpt"), params.get("model_src", "ckpt"))
        src = None
        if params.get("source_image"):
            from .jobs import ref_size
            try:
                src = ref_size(params["source_image"])      # a file, or a vault / memory picture
            except Exception:
                src = None
        pl = W.image_plan(params, kind, src)
        self.queue.add("image", params, title, thumb=thumb, units=W.work_units("image", pl, params.get("count", 1)),
                       ckpt_kind=kind)
        ip.bar.flash()

    def view(self, entries, idx=0, on_move=None):
        self.viewer.open(entries, idx, on_move)

    # ================================================================ the vault
    def ensure_vault(self, why=""):
        from . import vault_ui
        was = vault.is_open()
        ok = vault_ui.ensure_open(self, why)
        if ok and not was:
            self._vault_opened()
        return ok

    def _vault_opened(self):
        self._capture_guard()
        self.pages["gallery"].vault_changed()
        self.toast("Vault unlocked.", "ok")

    def lock_vault(self, quiet=False):
        if not vault.is_open():
            return
        vault.lock()
        if any(e.get("vault") for e in self.viewer.entries):
            self.viewer.forget()
        for pg in ("image", "video"):
            self.pages[pg].pane.forget_private()
        for drop in (self.pages["image"].init, self.pages["video"].start, self.pages["video"].end):
            if str(drop.path or "").startswith(("vault:", "mem:")):
                drop.clear()                # no decrypted picture stays on screen
        self._prune_memory()
        system.clear_private_clipboard()
        self.pages["gallery"].vault_changed()
        self._capture_guard()
        if not quiet:
            self.toast("Vault locked.", "info")

    def _prune_memory(self):
        from .jobs import prune_memory
        shown = [self.pages["image"].init.path, self.pages["video"].start.path, self.pages["video"].end.path]
        prune_memory(self.queue, [str(x or "") for x in shown])

    def _capture_guard(self):
        """Every window of the app is left out of screen captures, recordings and screen sharing: always (Settings,
        Privacy) or while the vault is open (Settings, Vault)."""
        system.capture_block(cfg.get("capture_block_app", False) or
                             (vault.is_open() and cfg.get("vault_hide_capture", True)))

    def _vault_tick(self):
        if not vault.is_open() or self._queue_busy():
            return
        if self.isMinimized() and cfg.get("vault_lock_minimized", True):
            self.lock_vault(quiet=True)             # minimized while the queue was busy: locks once it is done
            return
        mins = int(cfg.get("vault_autolock", 10) or 0)
        if mins > 0 and system.idle_seconds() >= mins * 60 and not self._vault_video_playing():
            self.lock_vault()

    def _queue_busy(self):
        """Something is being made, or waits in a queue that is not paused: auto-lock waits for it."""
        q = self.queue
        return bool(q.running() or (q.pending() and not q.paused))

    def _vault_video_playing(self):
        """A vault video plays on screen: watching it does not count as being away."""
        g = self.pages["gallery"]
        if g.isVisible() and g.cur and g.cur.get("vault") and g.player.timer.isActive():
            return True
        v = self.viewer
        if v.isVisible() and v.timer.isActive() and (v.cur() or {}).get("vault"):
            return True
        for k in ("image", "video"):
            pane = self.pages[k].pane
            if pane.isVisible() and getattr(pane, "private", None) and pane.player.timer.isActive():
                return True
        return False

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and self.isMinimized() and cfg.get("vault_lock_minimized", True) \
                and not self._queue_busy():
            self.lock_vault(quiet=True)

    def move_to_vault(self, entries):
        """Encrypts pictures / videos into the vault, then shreds the originals, their thumbnails and their lines in
        the gallery file (a Recycle Bin copy would defeat the point)."""
        entries = [e for e in entries if e and not e.get("vault") and os.path.isfile(e.get("file", ""))]
        if not entries or not self.ensure_vault("Unlock the vault to move pictures into it."):
            return
        n = len(entries)
        self.toast(f"Moving {n} into the vault…", "info")
        mode = cfg.get("delete_mode") if cfg.get("delete_mode") in ("shred", "eraser") else "shred"

        def work():
            from . import shred
            done, plain = [], []
            for e in entries:
                try:
                    thumb = None
                    if e.get("thumb") and os.path.isfile(e["thumb"]):
                        with open(e["thumb"], "rb") as fh:
                            thumb = fh.read()
                    meta = {k: e[k] for k in ("kind", "w", "h", "seed", "prompt", "final_prompt", "negative", "model",
                                              "params", "preset", "loras", "fps", "frames", "seconds", "loop",
                                              "created", "took", "op", "fav") if k in e}
                    meta.update(name=os.path.splitext(os.path.basename(e["file"]))[0],
                                fmt=os.path.splitext(e["file"])[1].lstrip(".").lower() or "png")
                    vault.add_file(e["file"], meta, thumb)
                    done.append(e)
                    plain += [e["file"], e.get("thumb"), e["file"] + ".json"]
                except Exception:
                    pass
            shred.delete([p for p in plain if p and os.path.exists(p)], mode)
            self._moved.emit([e["id"] for e in done], n - len(done))
        threading.Thread(target=work, daemon=True).start()

    def _moved_done(self, ids, failed):
        self.forget_pictures([(self.history.get(i) or {}).get("file") for i in ids])
        self.history.remove_many(ids, purge=True)
        self.pages["gallery"].vault_changed()
        msg = f"{len(ids)} moved into the vault, the originals shredded."
        if failed:
            msg += f" {failed} couldn't be moved."
        self.toast(msg, "ok" if not failed else "warn")

    def take_out_of_vault(self, entries):
        """Decrypts pictures / videos back into your folders (as normal gallery items) and takes them out."""
        entries = [e for e in entries if e and e.get("vault")]
        if not entries or not vault.is_open():
            return
        if QMessageBox.question(self, "Vault", f"Decrypt {'this' if len(entries) == 1 else f'these {len(entries)}'} "
                                               "back into your normal folders? They are then no longer protected.") \
                != QMessageBox.StandardButton.Yes:
            return
        from .jobs import _out_dir
        moved = 0
        for e in entries:
            try:
                ext = e.get("fmt") or ("png" if e["kind"] == "image" else "mp4")
                path = media.unique(os.path.join(_out_dir(e["kind"]), f"{e.get('name') or 'BC_' + e['id']}.{ext}"))
                vault.export(e, path)
                tb = vault.read(e, "thumb_blob")
                thumb = None
                if tb:
                    thumb = os.path.join(BASE, "thumbs", os.path.basename(path).rsplit(".", 1)[0] + f"_{e['id']}.jpg")
                    os.makedirs(os.path.dirname(thumb), exist_ok=True)
                    with open(thumb, "wb") as fh:
                        fh.write(tb)
                h = {k: v for k, v in e.items() if k not in ("id", "vault", "blob", "thumb_blob", "size", "added",
                                                                "name", "fmt", "cols")}
                if isinstance(h.get("params"), dict):
                    h["params"] = {k: v for k, v in h["params"].items() if k != "private"}
                h.update(file=path, thumb=thumb)
                self.history.add(h)
                vault.remove([e["id"]])
                self.forget_pictures(["vault:" + e["id"]])
                moved += 1
            except Exception as ex:
                QMessageBox.warning(self, "Vault", f"Couldn't take it out:\n{ex}")
        self.pages["gallery"].vault_changed()
        self.toast(f"{moved} taken out of the vault.", "ok")

    def delete_vault(self, entries):
        entries = [e for e in entries if e and e.get("vault")]
        if not entries or not vault.is_open():
            return
        n = len(entries)
        if QMessageBox.question(self, "Vault", f"Delete {'this' if n == 1 else f'these {n}'} from the vault for "
                                               "good?") != QMessageBox.StandardButton.Yes:
            return
        vault.remove([e["id"] for e in entries])
        self.forget_pictures(["vault:" + e["id"] for e in entries])
        if self.viewer.isVisible():
            self.viewer.drop([e["id"] for e in entries])
        self.pages["gallery"].vault_changed()
        self.toast(f"{n} deleted from the vault.", "ok")

    def _vault_action(self, key, e):
        """Things to do with something in the vault - everything made from it stays private too."""
        from . import jobs
        ref = "vault:" + e["id"]
        if key == "view":
            items = vault.recent()
            ids = [x["id"] for x in items]
            self.view(items, ids.index(e["id"]) if e["id"] in ids else 0)
        elif key in ("upscale", "inpaint"):
            p = dict(e.get("params") or {})
            if not p.get("ckpt"):
                p.update({k: v for k, v in self.pages["image"].p.items() if k in ("ckpt", "model_src", "family",
                                                                                   "te1", "te2", "evae")})
            e2 = dict(e, params=p, file=ref)
            if key == "upscale":
                from .tools import UpscaleDialog
                d = UpscaleDialog(self, e2, bool(self.link.lists.get("upscale")))
            else:
                from .tools import MaskEditor
                d = MaskEditor(self, e2)
            if d.exec() and d.result_params:
                d.result_params["private"] = True
                self._queue_image(d.result_params, "Private picture")
                self.toast("Queued - the result goes into the vault.", "ok")
        elif key == "animate":
            self.pages["video"].set_start(ref)
            self.go("video")
        elif key == "reuse":
            pg = self.pages["image" if e["kind"] == "image" else "video"]
            pg.use_settings(dict(e.get("params") or {}, private=True))
            self.go("image" if e["kind"] == "image" else "video")
        elif key == "copy":
            data = vault.read(e)
            img = QImage()
            img.loadFromData(data)
            system.copy_private(image=img)
            self.toast("Picture copied. It stays out of the clipboard history and is cleared when the vault locks.",
                       "info")
        elif key == "copyprompt":
            system.copy_private(text=e.get("final_prompt") or e.get("prompt") or "")
            self.toast("Prompt copied.", "ok")
        elif key == "take_out":
            self.take_out_of_vault([e])
        elif key == "delete":
            self.delete_vault([e])
        elif key == "extend":
            try:
                data = vault.read(e)
                frames, _fps = media.read_video_bytes(data, e.get("w"), e.get("h"), max_h=100000)
                buf = media.png_bytes_of(frames[-1])
            except Exception as ex:
                QMessageBox.warning(self, "Extend", f"Could not read the last frame:\n{ex}")
                return
            key_ = jobs.remember_bytes(buf)
            params = dict(e.get("params") or {})
            params.update(start_image=key_, loop="free", seed=-1, join_with=ref, join=True,
                          join_name=e.get("name") or "the clip", private=True)
            self.pages["video"].load(params)
            self.go("video")
            self.toast("The last frame is the new start - the result goes into the vault.", "ok")

    def result_action(self, key, e, page=None):
        if e and e.get("vault"):
            self._vault_action(key, e)
            return
        if key == "move_vault":
            self.move_to_vault([e])
            return
        if key == "view":
            items = self.history.recent(e.get("kind"), 2000)
            ids = [x["id"] for x in items]
            self.view(items, ids.index(e["id"]) if e["id"] in ids else 0)
            return
        if key == "upscale":
            from .tools import UpscaleDialog
            if e.get("kind") != "image":
                return
            p = dict(e.get("params") or {})
            if not p.get("ckpt"):
                p.update({k: v for k, v in self.pages["image"].p.items() if k in ("ckpt", "model_src", "family",
                                                                                   "te1", "te2", "evae")})
                e = dict(e, params=p)
            d = UpscaleDialog(self, e, bool(self.link.lists.get("upscale")))
            if d.exec() and d.result_params:
                self._queue_image(d.result_params, "Upscale: " + os.path.basename(e["file"]), e.get("thumb"))
                self.toast("Upscale queued.", "ok")
            return
        if key == "inpaint":
            from .tools import MaskEditor
            if e.get("kind") != "image":
                return
            p = dict(e.get("params") or {})
            if not p.get("ckpt"):
                p.update({k: v for k, v in self.pages["image"].p.items() if k in ("ckpt", "model_src", "family",
                                                                                   "te1", "te2", "evae")})
                e = dict(e, params=p)
            d = MaskEditor(self, e)
            if d.exec() and d.result_params:
                self._queue_image(d.result_params, "Edit: " + (d.result_params.get("prompt") or "")[:60], e.get("thumb"))
                self.toast("Edit queued - the new version appears next to the old one.", "ok")
            return
        if key == "compare":
            if e.get("group"):
                self.open_compare(e["group"])
            return
        if key == "animate":
            self.pages["video"].set_start(e["file"])
            self.go("video")
        elif key == "reuse":
            pg = self.pages["image" if e["kind"] == "image" else "video"]
            pg.use_settings(e.get("params") or {})
            self.go("image" if e["kind"] == "image" else "video")
        elif key == "start":
            self.pages["image"].set_start(e["file"])
            self.toast("Set as the start picture - lower 'Change' keeps more of it.", "ok")
        elif key == "copy":
            QApplication.clipboard().setImage(QImage(e["file"]))
            self.toast("Picture copied.", "ok")
        elif key == "folder":
            system.reveal(e["file"])
        elif key == "delete":
            self.delete_entries([e])
        elif key == "extend":
            try:
                fr = media.last_frame(e["file"])
                if fr is None:
                    raise RuntimeError("no frames")
                d = os.path.join(BASE, "frames")
                os.makedirs(d, exist_ok=True)
                p = media.unique(os.path.join(d, os.path.splitext(os.path.basename(e["file"]))[0] + "_last.png"))
                from PIL import Image
                Image.fromarray(fr).save(p)
            except Exception as ex:
                QMessageBox.warning(self, "Extend", f"Could not read the last frame:\n{ex}")
                return
            vp = self.pages["video"]
            params = dict(e.get("params") or {})
            params.update(start_image=p, loop="free", seed=-1, join_with=e["file"], join=True,
                          join_name=os.path.basename(e["file"]))
            vp.load(params)
            self.go("video")
            self.toast("The last frame is the new start - describe what happens next.", "ok")
        elif key == "gif":
            self._save_gif(e)

    def _save_gif(self, e):
        from PySide6.QtWidgets import QFileDialog
        out, _ = QFileDialog.getSaveFileName(self, "Save as GIF", os.path.splitext(e["file"])[0] + ".gif", "GIF (*.gif)")
        if not out:
            return
        self.toast("Making the GIF…", "info")

        def work():
            try:
                frames, fps = media.read_video(e["file"], max_h=100000)
                media.write_gif(frames, fps, out)
                QTimer.singleShot(0, lambda: self.toast("GIF saved.", "ok"))
            except Exception as ex:
                msg = str(ex)
                QTimer.singleShot(0, lambda: self.toast(f"GIF failed: {msg}", "error"))
        threading.Thread(target=work, daemon=True).start()

    def join_entries(self, entries):
        """Videos one after the other as one video (oldest first)."""
        vids = sorted([e for e in entries if e.get("kind") == "video"], key=lambda e: e.get("created", ""))
        if len(vids) < 2:
            self.toast("Pick two or more videos to join.", "warn")
            return
        self.toast(f"Joining {len(vids)} videos…", "info")
        from .jobs import _out_dir

        def work():
            try:
                frames, fps = media.join_clips([e["file"] for e in vids])
                ext = os.path.splitext(vids[0]["file"])[1].lstrip(".") or "mp4"
                path = media.unique(os.path.join(_out_dir("video"), f"BC_joined_{len(vids)}.{ext}"))
                media.write_video(frames, fps, path, ext)
                thumb = media.thumbnail(frames[0], os.path.join(BASE, "thumbs", os.path.basename(path) + ".jpg"))
                e = {"kind": "video", "file": path, "thumb": thumb, "w": frames[0].shape[1], "h": frames[0].shape[0],
                     "seed": vids[0].get("seed"), "fps": fps, "frames": len(frames),
                     "seconds": round(len(frames) / fps, 2), "prompt": vids[0].get("prompt", ""),
                     "params": vids[0].get("params") or {}, "joined": True, "loop": "free"}
                self._joined.emit(e)
            except Exception as ex:
                self._joined.emit({"error": str(ex)})
        threading.Thread(target=work, daemon=True).start()

    def _join_done(self, e):
        if e.get("error"):
            self.toast("Joining failed: " + e["error"], "error")
            return
        self.history.add(e)
        self.toast(f"Joined: {e['seconds']} s.", "ok")

    def animate_entries(self, entries):
        pics = [e["file"] for e in entries if e.get("kind") == "image"]
        if pics:
            self.pages["video"].batch(pics)

    def delete_entries(self, entries, ask=True):
        """Pictures and videos away for good: to the Recycle Bin, or shredded (Settings, Deleting). Their thumbnails and
        settings files go the same way; with shredding their gallery lines are rewritten out of the gallery file too."""
        entries = [e for e in entries if e]
        if not entries:
            return
        from . import shred
        mode = cfg.get("delete_mode", "recycle")
        if mode == "eraser" and not shred.eraser_path():
            mode = "shred"
        n = len(entries)
        what = "this" if n == 1 else f"these {n}"
        if ask and cfg.get("confirm_delete"):
            q = {"recycle": f"Move {what} to the Recycle Bin?",
                 "shred": f"Shred {what}? They are overwritten and can't be brought back.",
                 "eraser": f"Erase {what} with Eraser? They can't be brought back."}[mode]
            if QMessageBox.question(self, "Delete", q) != QMessageBox.StandardButton.Yes:
                return
        files, caches = [], []
        for e in entries:
            if e.get("file"):
                files.append(e["file"])
                if os.path.isfile(e["file"] + ".json"):
                    files.append(e["file"] + ".json")
            if e.get("thumb"):
                caches.append(e["thumb"])
        self.history.remove_many([e["id"] for e in entries], purge=mode != "recycle")
        self.forget_pictures([e["file"] for e in entries])
        self.queue.forget_entries([e["id"] for e in entries])
        if cfg.get("forget_prompts", True):
            # their prompts leave the prompt history too, unless a picture that is still there has the same one
            gone = {(e.get("prompt") or "").strip() for e in entries}
            still = {(x.get("prompt") or "").strip() for x in self.history.items}
            hist = cfg.get("prompt_history") or []
            new = [t for t in hist if t.strip() not in gone or t.strip() in still]
            if len(new) != len(hist):
                cfg.set("prompt_history", new)
        if self.viewer.isVisible():
            self.viewer.drop([e["id"] for e in entries])
        if mode == "recycle":
            shred.delete(files, "recycle")
            for c in caches:
                try:
                    os.remove(c)
                except OSError:
                    pass
            return
        self.toast(f"{'Shredding' if mode == 'shred' else 'Erasing'} {n} item{'s' if n != 1 else ''}…", "info")

        def work():
            left = shred.delete(files + caches, mode)
            self._shredded.emit(n, len([f for f in left if f in files]))
        threading.Thread(target=work, daemon=True).start()

    def forget_pictures(self, refs):
        """Pictures that are gone (deleted, moved into or out of the vault) leave the start / end picture slots too,
        so Generate never runs into a missing picture."""
        gone = {os.path.normcase(os.path.abspath(r)) if not str(r).startswith(("vault:", "mem:")) else r
                for r in refs if r}
        for drop in (self.pages["image"].init, self.pages["video"].start, self.pages["video"].end):
            p = drop.path or ""
            key = p if p.startswith(("vault:", "mem:")) else (os.path.normcase(os.path.abspath(p)) if p else "")
            if key and key in gone:
                drop.clear()

    def _shred_done(self, n, failed):
        if failed:
            self.toast(f"{failed} file{'s' if failed != 1 else ''} couldn't be deleted (in use?).", "warn")
        else:
            self.toast(f"{n} item{'s' if n != 1 else ''} deleted for good.", "ok")

    def toggle_flag(self, entries, key):
        """Favourite ('fav') or marked for deletion ('marked') on / off for these (on when any of them is off)."""
        entries = [e for e in entries if e]
        if not entries:
            return
        on = not all(e.get(key) for e in entries)
        for e in entries:
            self.history.update(e["id"], **{key: on})
        return on

    def show_entry(self, eid):
        e = self.history.get(eid)
        if not e:
            return
        self.go("gallery")
        g = self.pages["gallery"]
        g.fill()
        g.show(eid)

    def show_vault_entry(self, vid):
        if not self.ensure_vault() or not vault.get(vid):
            return
        self.go("gallery")
        g = self.pages["gallery"]
        g.place = "vault"
        g._fill_nav()
        g.fill()
        g._follow({"id": vid})
        g.show(vid)

    def add_lora(self, where, name):
        pg = self.pages["image" if where == "image" else "video"]
        pg.loras.add(name)
        self.go(where)
        self.toast(f"Added {os.path.splitext(os.path.basename(name))[0]}.", "ok")

    # ---------------------------------------------------------------- look
    def apply_accent(self):
        T.set_accent(cfg.get("accent"))
        _palette(QApplication.instance())
        QApplication.instance().setStyleSheet(T.qss())
        for pg in ("image", "video"):
            self.pages[pg].bar.refresh_style()
        for w in QApplication.instance().allWidgets():
            w.update()

    # ---------------------------------------------------------------- tray / window
    def _tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.pause_act = QAction(self)
            return
        self.tray = QSystemTrayIcon(QIcon(resource("assets", "icon.ico")), self)
        m = QMenu()
        m.addAction("Show Better Comfy").triggered.connect(self._show)
        self.pause_act = m.addAction("Pause queue")
        self.pause_act.triggered.connect(lambda: self.queue.resume() if self.queue.paused else self.queue.pause())
        m.addSeparator()
        m.addAction("Quit").triggered.connect(lambda: self._parked_ok() and self._quit())
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self._show() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore(self):
        w = cfg.get("window") or {}
        scr = QApplication.primaryScreen().availableGeometry()
        if w.get("geo"):
            x, y, ww, hh = w["geo"]
            r = QRect(x, y, ww, hh)
            if scr.intersects(r):
                self.setGeometry(r)
            else:
                self.resize(min(1560, scr.width() - 80), min(980, scr.height() - 80))
        else:
            self.resize(min(1560, scr.width() - 80), min(980, scr.height() - 80))
            self.move(scr.center() - self.rect().center())
        if w.get("max"):
            QTimer.singleShot(0, self.showMaximized)

    def _save_window(self):
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        cfg.set("window", {"geo": [g.x(), g.y(), g.width(), g.height()], "max": self.isMaximized()})

    def showEvent(self, e):
        super().showEvent(e)
        system.dark_title_bar(self.winId())

    def closeEvent(self, e):
        if getattr(self, "_quitting", False):
            e.accept()
            return
        if cfg.get("close_to_tray") and self.tray:
            e.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "Still here - the queue keeps working.", QIcon(resource("assets", "icon.ico")),
                                  2500)
            return
        if self.queue.active_count():
            from .jobs import is_private
            priv = len([j for j in self.queue.jobs if j["status"] in ("queued", "running") and is_private(j["params"])])
            note = "Waiting ones are kept for next time" + (
                f", except {priv} private one{'s' if priv != 1 else ''}: private jobs are never saved" if priv else "")
            r = QMessageBox.question(self, APP_NAME, f"{self.queue.active_count()} jobs are still in the queue. Close "
                                                     f"anyway? ({note}.)")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        if not self._parked_ok():
            e.ignore()
            return
        self._quit()
        e.accept()

    def _parked_ok(self):
        """Private results made while the vault was locked only live in memory: closing must not lose them unasked.
        True when it is fine to close."""
        n = len(vault.parked)
        if not n:
            return True
        self._show()
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                          f"{n} private result{'s are' if n != 1 else ' is'} waiting to go into the vault. Until you "
                          f"unlock it {'they only exist' if n != 1 else 'it only exists'} in memory, so closing now "
                          f"loses {'them' if n != 1 else 'it'}.", parent=self)
        keep = box.addButton("Unlock and keep", QMessageBox.ButtonRole.AcceptRole)
        lose = box.addButton("Close and lose", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is keep:
            return self.ensure_vault("Unlock to keep the private results.") and not vault.parked
        return box.clickedButton() is lose

    def _shutdown_cleanup(self):
        vault.lock()
        from . import assistant
        assistant.stop_all()
        self._save_window()
        self.pages["image"]._save()
        cfg.set("video_state", dict(self.pages["video"].p))
        if self.queue.worker is not None:
            self.queue.worker.stop()
        self.queue._save()
        if cfg.get("stop_on_exit") and comfy.anything_started():
            comfy.stop_started()

    def _quit(self):
        self._quitting = True
        self._shutdown_cleanup()
        if not getattr(self, "_updating", False) and self.updates.staged:
            self.updates.install(restart=False)          # a downloaded update goes in when Better Comfy closes
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()


# ==================================================================================================== start

class CaptureGuard(QObject):
    """Every window that opens (dialogs, menus, popups) gets the screen capture block if it is on."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow() and system.capture_blocked():
            system.guard_window(obj)
        return False


class WheelGuard(QObject):
    """The mouse wheel never changes a setting it happens to be over - it scrolls the page instead."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QAbstractScrollArea):
                w = w.parentWidget()
            if w is not None:
                sb = w.verticalScrollBar()
                sb.setValue(sb.value() - int(e.angleDelta().y() / 120 * max(20, sb.singleStep()) * 3))
            return True
        return False


def _palette(app):
    """A dark base under the stylesheet - nothing ever flashes white."""
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, col in ((QPalette.ColorRole.Window, T.BG), (QPalette.ColorRole.WindowText, T.TEXT),
                      (QPalette.ColorRole.Base, T.FIELD), (QPalette.ColorRole.AlternateBase, T.SURFACE),
                      (QPalette.ColorRole.Text, T.TEXT), (QPalette.ColorRole.Button, T.SURFACE2),
                      (QPalette.ColorRole.ButtonText, T.TEXT), (QPalette.ColorRole.ToolTipBase, T.SURFACE2),
                      (QPalette.ColorRole.ToolTipText, T.TEXT), (QPalette.ColorRole.PlaceholderText, T.TEXT3),
                      (QPalette.ColorRole.Mid, T.BORDER_HI), (QPalette.ColorRole.Dark, T.PANEL),
                      (QPalette.ColorRole.Light, T.SURFACE3)):
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.ColorRole.Highlight, T.accent())
    pal.setColor(QPalette.ColorRole.HighlightedText, T.on_accent())
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(T.TEXT3))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(T.TEXT3))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(T.TEXT3))
    app.setPalette(pal)


def _fonts(app):
    fams = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Inter", "Segoe UI"):
        if name in fams:
            f = QFont(name, 10)
            f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            app.setFont(f)
            return


def _single_instance():
    """Only one Better Comfy: a second start brings the first one forward."""
    name = "BetterComfy-" + (os.environ.get("USERNAME") or "user")
    s = QLocalSocket()
    s.connectToServer(name)
    if s.waitForConnected(300):
        s.write(b"show")
        s.flush()
        s.waitForBytesWritten(300)
        return None
    QLocalServer.removeServer(name)
    srv = QLocalServer()
    srv.listen(name)
    return srv


def snapshot(win, folder):
    """Renders every page to a PNG (for checking the look without clicking)."""
    os.makedirs(folder, exist_ok=True)
    for key in list(win.pages):
        win.go(key, animate=False)
        QApplication.processEvents()
        win.grab().save(os.path.join(folder, f"{key}.png"))
        sc = getattr(win.pages[key], "scroll", None)
        if isinstance(sc, QScrollArea):
            body = sc.widget()
            body.resize(sc.viewport().width(), body.sizeHint().height())
            QApplication.processEvents()
            body.grab().save(os.path.join(folder, f"{key}_panel.png"))


def main():
    from . import updater
    argv = sys.argv[1:]
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    comfy.quiet_errors()
    system.app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_ID)
    app.setWindowIcon(QIcon(resource("assets", "icon.ico")))
    app.setQuitOnLastWindowClosed(False)
    snap = None
    if "--snapshot" in sys.argv:
        snap = sys.argv[sys.argv.index("--snapshot") + 1]
    if snap:
        cfg.save = lambda: None                       # a look-check never changes the user's settings
        cfg.data["animations"] = False
    srv = None if snap else _single_instance()
    if srv is None and not snap:
        return 0
    T.set_accent(cfg.get("accent"))
    _palette(app)
    _fonts(app)
    app.setStyleSheet(T.qss())
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    cap = CaptureGuard(app)
    app.installEventFilter(cap)
    win = Window()
    if srv is not None:
        def other():
            c = srv.nextPendingConnection()
            if c:
                c.readyRead.connect(win._show)
        srv.newConnection.connect(other)
    win.show()
    win._capture_guard()                              # the whole app hidden from screen capture, if that is on
    updater.clean_up()
    if "--updated" in argv:
        QTimer.singleShot(1500, lambda: win.toast(f"Updated to Better Comfy {VERSION}.", "ok"))
    elif not cfg.get("welcomed") and not snap:
        from .welcome import Welcome
        QTimer.singleShot(500, lambda: (Welcome(win).exec(), win.link.installs(True), win.link.refresh_models(),
                                        win.link.poll(), win.pages["settings"]._fill_installs(True)))
    if snap:
        def shoot():
            try:
                snapshot(win, snap)
            finally:
                win._quit()
        QTimer.singleShot(2500, shoot)
    return app.exec()
