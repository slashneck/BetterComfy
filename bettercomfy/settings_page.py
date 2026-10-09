"""SETTINGS: ComfyUI (found by itself, how it is started), output folders, the queue, the look, shortcuts."""
import os
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import QColorDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, \
    QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import comfy, icons, system, theme as T
from .config import APP_NAME, BASE, VERSION, cfg, resource
from .queue_page import AFTER
from .widgets import (ChipBox, Combo, Card, Scroll, Segmented, Slider, ToggleRow, button, chip, field, hrow, label,
                      set_combo, vcol)


class SettingsPage(QWidget):
    title = "Settings"
    subtitle = "ComfyUI, folders, queue, look & feel"
    accent_changed = Signal()
    _drive_found = Signal(str)
    _privacy_found = Signal(str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.link = app.link
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        sc = Scroll((28, 18, 28, 28), 14)
        v.addWidget(sc)
        cols = QHBoxLayout()
        cols.setSpacing(14)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(14)
        right.setSpacing(14)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        holder = QWidget()
        holder.setLayout(cols)
        sc.add(holder)
        sc.end()

        # ---------------------------------------------------------------- ComfyUI
        c = Card("ComfyUI")
        self.status = label("", "H3")
        self.status_sub = label("", "Faint", sel=True)
        self.status_sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.start_btn = button("Start", self._start_stop, "Accent", "power")
        c.add(hrow(vcol(self.status, self.status_sub, spacing=2), None, self.start_btn))
        self.inst = Combo()
        self.inst.currentIndexChanged.connect(self._pick_install)
        browse = button("Browse…", self._browse, "Ghost", "folder")
        c.add(field("Install", hrow(self.inst, browse, spacing=6), "The ComfyUI Better Comfy uses. Found by itself "
                                                                     "(Pinokio, portable, ComfyUI Desktop).", label_w=90))
        self.url = QLineEdit(cfg.get("comfy_url"))
        self.url.setPlaceholderText(comfy.DEFAULT_URL)
        self.url.editingFinished.connect(self._url)
        c.add(field("Address", self.url, "Where ComfyUI answers (this PC only).", label_w=90))
        self.t_auto = ToggleRow("Start ComfyUI by itself", "When you generate and it is not running",
                                cfg.get("auto_start"), lambda v: cfg.set("auto_start", v))
        self.t_pin = ToggleRow("Start it through Pinokio", "Off: started directly with its own Python (faster, with "
                                                          "the options below)", cfg.get("via_pinokio"),
                               lambda v: cfg.set("via_pinokio", v))
        self.t_stop = ToggleRow("Stop it when Better Comfy closes", "Only the ComfyUI Better Comfy started",
                                cfg.get("stop_on_exit"), lambda v: cfg.set("stop_on_exit", v))
        c.add(self.t_auto, self.t_pin, self.t_stop)
        self.prev = Segmented([("off", "Off", "No live preview"),
                               ("fast", "Fast", "latent2rgb: blurry but free"),
                               ("sharp", "Sharp", "TAESD: clearer (needs the taesd decoders in models/vae_approx; "
                                                  "falls back to Fast)")],
                              lambda v: cfg.set("live_previews", v), self._prev_val(), height=30)
        c.add(field("Live preview", self.prev, "Watch the picture appear while it is drawn.", label_w=90))
        self.reserve = Slider(0, 4, 0.1, 1, float(cfg.get("reserve_vram") or 0),
                              lambda v: cfg.set("reserve_vram", round(v, 1)), " GB", width=74)
        c.add(field("Keep free", self.reserve, "Graphics memory kept free. On Windows a full card spills into normal "
                                               "memory and gets many times slower - 1 - 1.5 GB is good for 8 GB.",
                    label_w=90))
        self.t_fast = ToggleRow("Faster maths (fp16 accumulation)", "A bit faster on RTX cards, tiny quality change",
                                cfg.get("fast_mode"), lambda v: cfg.set("fast_mode", v))
        c.add(self.t_fast)
        c.add(label("Start options are used the next time Better Comfy starts ComfyUI.", "Faint"))
        c.add(hrow(button("Open ComfyUI", self._open_web, "Ghost", "external", "Its own page in your browser"),
                   button("Models folder", lambda: self._open_sub("models"), "Ghost", "folder"),
                   button("Log", self._log, "Ghost", "info"),
                   button("Rescan", self._rescan, "Ghost", "refresh"), None, spacing=4))
        left.addWidget(c)

        # ---------------------------------------------------------------- setup check
        self.check = Card("Setup check")
        self.check_body = label("", wrap=True)
        self.check_body.setTextFormat(Qt.TextFormat.RichText)
        self.check.add(self.check_body)
        self.repair_lbl = label("", "Muted", wrap=True)
        self.repair_btn = button("Repair them", self._repair, None, "wand",
                                 "Rebuilds them from pip's own templates in your ComfyUI - nothing is downloaded, the "
                                 "broken files are kept in Scripts\\_broken_launchers")
        self.repair_row = vcol(self.repair_lbl, hrow(self.repair_btn, None), spacing=6)
        self.repair_row.hide()
        self.check.add(self.repair_row)
        left.addWidget(self.check)

        # ---------------------------------------------------------------- output
        c = Card("Output")
        self.img_dir = self._dir_row(c, "Pictures", "image_dir")
        self.vid_dir = self._dir_row(c, "Videos", "video_dir")
        c.add(ToggleRow("A folder per day", "e.g. …/2026-10-08/", cfg.get("date_folders"),
                        lambda v: cfg.set("date_folders", v)),
              ToggleRow("Settings inside the PNG", "Prompt, seed, model - and the workflow, so dropping the picture "
                                                  "on ComfyUI opens it", cfg.get("embed_metadata"),
                        lambda v: cfg.set("embed_metadata", v)),
              hrow(button("ComfyUI input", lambda: self._open_sub("input"), "Ghost", "folder"),
                   button("ComfyUI output", lambda: self._open_sub("output"), "Ghost", "folder"), None, spacing=4))
        right.addWidget(c)

        # ---------------------------------------------------------------- deleting
        from . import shred
        c = Card("Deleting")
        has_eraser = bool(shred.eraser_path())
        self.del_mode = Segmented([(k, t, d) for k, t, d in shred.MODES], self._del_mode,
                                  cfg.get("delete_mode", "recycle"), height=32)
        self.del_line = label("", "Muted", wrap=True)
        self.del_drive = label("", "Faint", wrap=True)
        self.passes = Segmented([("1", "1 pass", "Random data once - enough for any drive of the last 20 years"),
                                 ("3", "3 passes", "Random data three times - slower, for peace of mind")],
                                lambda v: cfg.set("shred_passes", int(v)), str(cfg.get("shred_passes", 1)), height=30)
        self.passes_row = field("Overwrite", self.passes, label_w=90)
        c.add(self.del_mode, self.del_line, self.passes_row, self.del_drive)
        if not has_eraser:
            c.add(label("Eraser isn't installed on this PC, so 'Eraser' uses the built-in shredder.", "Faint",
                        wrap=True))
        right.addWidget(c)
        self._del_mode(cfg.get("delete_mode", "recycle"), save=False)
        self._drive_found.connect(self._show_drive)
        threading.Thread(target=lambda: self._drive_found.emit(shred.drive_kind(cfg.get("image_dir"))),
                         daemon=True).start()

        # ---------------------------------------------------------------- vault
        c = Card("Vault")
        self.v_state = label("", "Muted", wrap=True)
        self.v_make = button("Make the vault", self._vault_open, "Accent", "lock")
        self.v_lock = button("Lock now", lambda: (self.app.lock_vault(), self.vault_status()), "Ghost", "lock")
        self.v_pw = button("Change password", self._vault_pw, "Ghost", "edit")
        self.v_del = button("Delete the vault…", self._vault_destroy, "Ghost", "trash", icon_color="#FF8A8A")
        self.autolock = Combo()
        for m, t in ((0, "Never"), (5, "After 5 minutes"), (10, "After 10 minutes"), (15, "After 15 minutes"),
                     (30, "After 30 minutes"), (60, "After an hour")):
            self.autolock.addItem(t, m)
        set_combo(self.autolock, int(cfg.get("vault_autolock", 10)))
        self.autolock.currentIndexChanged.connect(lambda _=0: cfg.set("vault_autolock", self.autolock.currentData()))
        c.add(self.v_state, hrow(self.v_make, self.v_lock, self.v_pw, None, self.v_del, spacing=4),
              field("Lock when idle", self.autolock, "No mouse or keyboard for that long locks it. It waits while the "
                                                     "queue is working or a vault video plays.", label_w=110),
              ToggleRow("Lock when minimized", "Once the queue is done, if it is still working",
                        cfg.get("vault_lock_minimized", True), lambda v: cfg.set("vault_lock_minimized", v)))
        right.addWidget(c)
        self.vault_status()

        # ---------------------------------------------------------------- privacy
        c = Card("Privacy")
        c.add(ToggleRow("Block screen capture", "Screenshots, recordings and screen sharing can't see any window of "
                                                "Better Comfy", cfg.get("capture_block_app", False),
                        lambda v: (cfg.set("capture_block_app", v), self.app._capture_guard())),
              ToggleRow("Block screen capture while the vault is open", "The same, only while the vault is unlocked",
                        cfg.get("vault_hide_capture", True),
                        lambda v: (cfg.set("vault_hide_capture", v), self.app._capture_guard())),
              ToggleRow("Clear ComfyUI's memory after private jobs", "Once the queue is done, no private picture stays "
                                                                     "in ComfyUI's cache. The models load again for "
                                                                     "the next job.",
                        cfg.get("comfy_forget_private", True), lambda v: cfg.set("comfy_forget_private", v)))
        self.pc_report = label("", None, wrap=True)
        self.pc_report.setTextFormat(Qt.TextFormat.RichText)
        self.pc_check = button("Check again", self._check_pc, "Ghost", "refresh")
        c.add(label("This PC", "Muted"), self.pc_report, hrow(self.pc_check, None))
        self._privacy_found.connect(self._show_privacy)
        self._pc_checked = False
        right.addWidget(c)

        # ---------------------------------------------------------------- prompt helper
        c = Card("Prompt helper")
        self.h_state = label("", "Muted", wrap=True)
        self.h_setup = button("Set up…", self._helper_setup, "Accent", "sparkle")
        self.h_open = button("Open folder", lambda: system.open_folder(self._helper_dir()), "Ghost", "folder")
        self.h_remove = button("Remove…", self._helper_remove, "Ghost", "trash", icon_color="#FF8A8A")
        self.h_free = Combo()
        for m, t in ((2, "After 2 minutes unused"), (5, "After 5 minutes unused"), (15, "After 15 minutes unused"),
                     (0, "Keep it loaded")):
            self.h_free.addItem(t, m)
        set_combo(self.h_free, int(cfg.get("helper_free_min", 5)))
        self.h_free.currentIndexChanged.connect(lambda _=0: cfg.set("helper_free_min", self.h_free.currentData()))
        self.tag_mode = Segmented([("auto", "Auto", "For models that use tags (Illustrious, Pony, Anima, SD 1.5)"),
                                   ("on", "Always"), ("off", "Off")],
                                  lambda v: cfg.set("tag_suggest", v), cfg.get("tag_suggest", "auto"), height=30)
        c.add(self.h_state, hrow(self.h_setup, self.h_open, None, self.h_remove, spacing=4),
              ToggleRow("Show the helper button", "The sparkle next to the prompt", cfg.get("helper_button", True),
                        self._helper_button),
              field("Free its memory", self.h_free, "It loads again by itself the next time (a few seconds).",
                    label_w=110),
              field("Tag suggestions", self.tag_mode, "While typing a prompt: Tab or Enter takes a suggestion.",
                    label_w=110))
        right.addWidget(c)
        self.helper_status()

        # ---------------------------------------------------------------- tag blacklist
        c = Card("Tag blacklist")
        self.bl_info = label("", "Faint", wrap=True)
        self.bl_edit = QLineEdit()
        self.bl_edit.setPlaceholderText("Add tags, separated by commas")
        self.bl_edit.setMinimumHeight(34)
        self.bl_edit.returnPressed.connect(self._bl_add)
        from PySide6.QtCore import QStringListModel
        from PySide6.QtWidgets import QCompleter
        self._bl_model = QStringListModel()
        comp = QCompleter(self._bl_model, self.bl_edit)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        comp.activated.connect(self._bl_pick)
        self.bl_edit.setCompleter(comp)
        self.bl_edit.textEdited.connect(self._bl_suggest)
        self.bl_box = ChipBox(4)
        self.bl_clear = button("Clear all", self._bl_clear_all, "Ghost", "trash", icon_color="#FF8A8A")
        c.add(self.bl_info, hrow(self.bl_edit, button("Add", self._bl_add, "Ghost", "plus"), spacing=6), self.bl_box,
              hrow(None, self.bl_clear))
        right.addWidget(c)
        self._bl_fill()

        # ---------------------------------------------------------------- queue
        c = Card("Queue")
        self.after = Combo()
        for k, t in AFTER:
            self.after.addItem(t, k)
        set_combo(self.after, cfg.get("after_queue"))
        self.after.currentIndexChanged.connect(self._after)
        c.add(field("When done", self.after, "Sleep / shut down / close wait 30 seconds with a Cancel button first.",
                    label_w=90))
        c.add(ToggleRow("Free graphics memory after the queue", "Unloads the models - other programs get the card back",
                        cfg.get("free_after_queue"), lambda v: cfg.set("free_after_queue", v)),
              ToggleRow("Notification when it is done", None, cfg.get("notify_done"), lambda v: cfg.set("notify_done", v)),
              ToggleRow("Sound when it is done", None, cfg.get("sound_done"), lambda v: cfg.set("sound_done", v)))
        right.addWidget(c)

        # ---------------------------------------------------------------- look
        c = Card("Look & feel")
        sw = QWidget()
        sh = QHBoxLayout(sw)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        self.swatches = []
        for name, hexc in T.ACCENTS:
            b = QPushButton()
            b.setObjectName("Swatch")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(name)
            b.clicked.connect(lambda _=False, h=hexc: self._accent(h))
            self.swatches.append((b, hexc))
            sh.addWidget(b)
        custom = button("", self._custom_accent, "Icon", "palette", "Any colour…", icon_color="#A1A1AA")
        custom.setFixedSize(30, 30)
        sh.addWidget(custom)
        sh.addStretch(1)
        c.add(field("Accent", sw, label_w=90))
        c.add(ToggleRow("Animations", "Smooth transitions, sliding controls, fades", cfg.get("animations"),
                        lambda v: cfg.set("animations", v)))
        self.start_page = Combo()
        for k, t in (("image", "Image"), ("video", "Video"), ("queue", "Queue"), ("gallery", "Gallery"),
                     ("loras", "LoRAs")):
            self.start_page.addItem(t, k)
        set_combo(self.start_page, cfg.get("start_page"))
        self.start_page.currentIndexChanged.connect(lambda _=0: cfg.set("start_page", self.start_page.currentData()))
        c.add(field("Open on", self.start_page, label_w=90))
        right.addWidget(c)
        self._paint_swatches()

        # ---------------------------------------------------------------- app
        c = Card("App")
        self.desk_btn = button("Shortcut on the desktop", lambda: self._shortcut(system.desktop_dir()), None, "shortcut")
        self.menu_btn = button("Start menu entry", lambda: self._shortcut(system.start_menu_dir()), "Ghost", "shortcut")
        c.add(hrow(self.desk_btn, self.menu_btn, None, spacing=6))
        c.add(ToggleRow("Close to the tray", "The window hides, the queue keeps working (quit from the tray icon)",
                        cfg.get("close_to_tray"), lambda v: cfg.set("close_to_tray", v)),
              ToggleRow("Ask before deleting", "Deleted results go to the recycle bin either way",
                        cfg.get("confirm_delete"), lambda v: cfg.set("confirm_delete", v)))
        c.add(hrow(button("App data folder", lambda: system.open_folder(BASE), "Ghost", "folder"), None,
                   button("Reset settings", self._reset, "Danger"), spacing=6))
        right.addWidget(c)

        # ---------------------------------------------------------------- updates
        c = Card("Updates")
        self.t_upd = ToggleRow("Look for new versions", "Asks GitHub every few hours. Apart from the prompt helper "
                                                       "download you start yourself, that is the only thing Better "
                                                       "Comfy does online.", cfg.get("check_updates"),
                               lambda v: cfg.set("check_updates", v))
        self.upd_status = label("", "Muted", wrap=True)
        self.upd_check = button("Check now", lambda: self.app.updates.check(), None, "refresh")
        self.upd_install = button("Restart and update", lambda: self.app.update_now(), "Accent", "download")
        self.upd_install.hide()
        c.add(self.t_upd, self.upd_status, hrow(self.upd_check, self.upd_install, None, spacing=6))
        right.addWidget(c)

        # ---------------------------------------------------------------- about
        c = Card(None)
        lg = QLabel()
        pm = QPixmap(resource("assets", "icon.png")).scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(2.0)
        lg.setPixmap(pm)
        c.add(hrow(lg, vcol(label(APP_NAME, "H2"), label(f"Version {VERSION}", "Faint"),
                            label("Image & video generation on ComfyUI", "Muted"), spacing=2), None, spacing=14))
        right.addWidget(c)
        left.addStretch(1)
        right.addStretch(1)

        self.link.status.connect(self._status)
        self.link.models.connect(lambda _l: self._check())
        self._fill_installs()
        self._status(self.link.state)
        self._shortcut_state()
        self.update_state()

    # ---------------------------------------------------------------- helpers
    def _prev_val(self):
        v = cfg.get("live_previews")
        return {True: "fast", False: "off"}.get(v, v) if not isinstance(v, str) else v

    def _dir_row(self, card, name, key):
        e = QLineEdit(cfg.get(key))
        e.editingFinished.connect(lambda: cfg.set(key, e.text().strip() or cfg.get(key)))

        def pick():
            d = QFileDialog.getExistingDirectory(self, f"Folder for {name.lower()}", e.text())
            if d:
                e.setText(os.path.normpath(d))
                cfg.set(key, os.path.normpath(d))
        card.add(field(name, hrow(e, button("", pick, "Icon", "folder", "Choose…", icon_color="#A1A1AA"),
                                  button("", lambda: system.open_folder(cfg.get(key)), "Icon", "external", "Open",
                                         icon_color="#A1A1AA"), spacing=4), label_w=90))
        return e

    def _fill_installs(self, rescan=False):
        ins = self.link.installs(rescan)
        self.inst.blockSignals(True)
        self.inst.clear()
        cur = self.link.install()
        for i in ins:
            kind = {"pinokio": "Pinokio", "portable": "Portable", "desktop": "Desktop"}.get(i["kind"], "Folder")
            self.inst.addItem(f"{kind}  ·  {i['path']}" + (f"  ·  v{i['version']}" if i.get("version") else ""), i["path"])
        if not ins:
            self.inst.addItem("No ComfyUI found - Browse… to pick its folder", "")
        if cur:
            self.inst.setCurrentIndex(max(0, self.inst.findData(cur["path"])))
        self.inst.blockSignals(False)

    def _pick_install(self, _i):
        path = self.inst.currentData()
        if path:
            cfg.set("comfy_folder", path)
            self.link.installs(True)
            self.link.refresh_models()
            self.link.poll()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Your ComfyUI folder (the one with main.py)")
        if not d:
            return
        it = comfy.install_from_pick(d)
        if it is None:
            QMessageBox.warning(self, "ComfyUI", "That folder has no ComfyUI in it (main.py + comfy folder).")
            return
        cfg.set("comfy_folder", it["path"])
        self._fill_installs(True)
        self.link.refresh_models()
        self.link.poll()

    def _url(self):
        u = self.url.text().strip().rstrip("/") or comfy.DEFAULT_URL
        if not u.startswith("http"):
            u = "http://" + u
        if not comfy.is_local(u):
            QMessageBox.warning(self, "Address", "Better Comfy only talks to ComfyUI on this PC "
                                                 "(127.0.0.1 or localhost).")
            self.url.setText(cfg.get("comfy_url"))
            return
        self.url.setText(u)
        cfg.set("comfy_url", u)
        self.link.poll()

    def _status(self, st):
        s = st.get("state")
        ins = st.get("install") or self.link.install()
        if s == "running":
            gpu = st.get("gpu")
            self.status.setText(f"Running  ·  ComfyUI {st.get('version', '')}")
            self.status_sub.setText(f"{st.get('url')}" + (f"  ·  {gpu[0]}  ·  {gpu[2]:.1f} of {gpu[1]:.0f} GB free"
                                                          if gpu else ""))
            self.start_btn.setText("Stop")
            self.start_btn.setObjectName("Danger")
            self.start_btn.setEnabled(True)
        elif s == "starting":
            self.status.setText("Starting…")
            self.status_sub.setText("This takes a little while (models are loaded when needed).")
            self.start_btn.setText("Starting…")
            self.start_btn.setEnabled(False)
        elif s == "missing":
            self.status.setText("No ComfyUI found")
            self.status_sub.setText("Install ComfyUI with Pinokio (Discover → ComfyUI), or Browse… to your ComfyUI "
                                    "folder.")
            self.start_btn.setText("Start")
            self.start_btn.setEnabled(False)
        else:
            self.status.setText("Not running")
            self.status_sub.setText(ins["path"] if ins else "")
            self.start_btn.setText("Start")
            self.start_btn.setObjectName("Accent")
            self.start_btn.setEnabled(bool(ins))
        self.start_btn.setIcon(icons.icon("power", T.on_accent().name() if self.start_btn.objectName() == "Accent"
                                          else "#FF8A8A"))
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._check()

    def _check(self):
        L = self.link.lists or {}
        ins = self.link.install()
        ok, no, opt = (f"<span style='color:{T.GOOD}'>●</span>", f"<span style='color:{T.BAD}'>●</span>",
                       f"<span style='color:{T.TEXT3}'>●</span>")
        rows = []

        def row(state, text, sub=""):
            dot = ok if state is True else (no if state is False else opt)
            rows.append(f"<tr><td style='padding:3px 10px 3px 0'>{dot}</td><td style='padding:3px 0'>{text}"
                        + (f"<br><span style='color:{T.TEXT3}; font-size:12px'>{sub}</span>" if sub else "")
                        + "</td></tr>")
        row(bool(ins), "ComfyUI found" if ins else "ComfyUI not found",
            (ins or {}).get("path", "Install it with Pinokio (Discover → ComfyUI)"))
        row(self.link.running() or None, "Running" if self.link.running() else "Not running (starts when you generate)")
        pics = self.link.picture_models()
        row(bool(pics), f"{len(pics)} picture model{'s' if len(pics) != 1 else ''}",
            "SDXL / Pony / Illustrious / SD 1.5 → models/checkpoints; Flux / Qwen / Z-Image → models/diffusion_models" if not pics else
            ", ".join(os.path.splitext(os.path.basename(x[0]))[0] for x in pics[:4]) + ("…" if len(pics) > 4 else ""))
        g = L.get("video_guess") or {}
        wan = (g.get("unet_high") and g.get("unet_low")) or g.get("unet")
        row(bool(wan), "WAN video model" + (" pair (motion + detail)" if g.get("models") == "two" else ""),
            (os.path.basename(g.get("unet_high") or g.get("unet") or "") if wan else
             "WAN 2.2 I2V high + low (or a WAN 2.1 I2V model) → models/diffusion_models"))
        row(bool(g.get("clip")), "Text encoder for WAN", g.get("clip") or "umt5_xxl_fp8_e4m3fn_scaled → models/text_encoders")
        row(bool(g.get("vae")), "WAN VAE", g.get("vae") or "wan_2.1_vae → models/vae")
        row(True if g.get("interp_model") else None, "Smoothing (RIFE) - optional",
            g.get("interp_model") or "rife_v4.26 → models/frame_interpolation")
        row(True if g.get("unet_t2v") else None, "Text-to-video model - optional",
            g.get("unet_t2v") or "wan2.1_t2v_1.3B → models/diffusion_models")
        ups = L.get("upscale") or []
        row(True if ups else None, f"Upscalers - optional ({len(ups)})",
            ", ".join(os.path.splitext(os.path.basename(x))[0] for x in ups[:3]) or "e.g. 4x-AnimeSharp → models/upscale_models")
        lo = L.get("loras") or []
        row(True if lo else None, f"{len(lo)} LoRAs", "")
        bad = L.get("damaged") or []
        self.repair_row.setVisible(bool(bad))
        if bad:
            self.repair_lbl.setText(f"{len(bad)} small launcher programs in your ComfyUI's env\\Scripts are damaged "
                                    f"({', '.join(bad[:3])}…). That is what shows 'Unsupported 16-Bit Application' when "
                                    "ComfyUI starts. ComfyUI itself is fine.")
        src = {"comfy": "read from the running ComfyUI", "files": "read from its model folders"}.get(L.get("source"), "")
        self.check_body.setText("<table>" + "".join(rows) + "</table>" +
                                (f"<p style='color:{T.TEXT3}; font-size:12px'>Lists {src}.</p>" if src else ""))

    # ---- privacy check (only reads how Windows is set up)
    def _check_pc(self):
        self.pc_check.setEnabled(False)
        self.pc_report.setText("Checking…")
        threading.Thread(target=lambda: self._privacy_found.emit(self._privacy_report()), daemon=True).start()

    def _show_privacy(self, html):
        self.pc_report.setText(html)
        self.pc_check.setEnabled(True)

    @staticmethod
    def _privacy_report():
        lines = []
        sysdrive = (os.environ.get("SystemDrive") or "C:").upper()
        places = {}
        for what, path in (("Windows: page file, hibernation, thumbnails", sysdrive + "\\"),
                           ("your pictures", cfg.get("image_dir")), ("your videos", cfg.get("video_dir")),
                           ("the app's data and the vault", BASE)):
            d = os.path.splitdrive(os.path.abspath(path or sysdrive + "\\"))[0].upper()
            if d:
                places.setdefault(d, []).append(what)
        states = {}
        for d, whats in places.items():
            st = states[d] = system.drive_encryption(d + "\\")
            text = {"on": "encrypted", "paused": "encryption paused (BitLocker is suspended, its key lies readable "
                                                 "on the drive)",
                    "off": "not encrypted", None: "encryption unknown"}[st]
            lines.append((st == "on", f"Drive {d} ({', '.join(whats)}): {text}"))
        if states.get(sysdrive) != "on":
            pf = system.pagefile_encrypted()
            lines.append((pf, "Page file encrypted" if pf else
                          "Page file not encrypted: memory Windows moves to the drive is stored readable"))
            if system.hibernation_on():
                lines.append((False, "Hibernation is on: when the PC hibernates, its memory (an open vault too) is "
                                     "written to the drive"))
        for what, path in (("pictures", cfg.get("image_dir")), ("videos", cfg.get("video_dir")),
                           ("app data and vault", BASE)):
            svc = system.cloud_synced(path)
            if svc:
                lines.append((False, f"The {what} folder is synced with {svc}: what is saved there is uploaded"))
        out = []
        for ok, t in lines:
            col = T.GOOD if ok else T.WARN
            out.append(f"<span style='color:{col}'>●</span>&nbsp; {t}")
        if any(s != "on" for s in states.values()):
            out.append(f"<span style='color:{T.TEXT3}'>Drive encryption (BitLocker, or Device encryption in Windows "
                       "Settings, Privacy &amp; security) covers everything Windows keeps on its own: the page file, "
                       "hibernation, thumbnails and leftovers of deleted files on SSDs. Better Comfy does not change "
                       "these settings.</span>")
        return "<br>".join(out)

    # ---- tag blacklist
    def _bl_fill(self):
        from . import tags
        items = sorted(tags.blacklist())
        self.bl_box.clear()
        for t in items:
            self.bl_box.add(chip(f"{t}   ×", lambda t=t: self._bl_remove(t), False, "Take it off the blacklist"))
        self.bl_info.setText((f"{len(items)} tag{'s' if len(items) != 1 else ''}. " if items else "Empty. ") +
                             "Tags here, and tags that contain them (thighhighs: also white thighhighs), are never "
                             "suggested while typing and never added by the prompt helper. What you type yourself "
                             "always stays in your prompt.")
        self.bl_clear.setVisible(bool(items))
        self.bl_box.setVisible(bool(items))

    def _bl_suggest(self, text):
        from . import tags
        part = text.split(",")[-1].strip()
        if not tags.ready():
            tags.load()
        head = text[:len(text) - len(text.split(",")[-1])]
        self._bl_model.setStringList([head + (" " if head else "") + t for t, _k, _c, _a in tags.search(part)]
                                     if len(part) >= 2 else [])

    def _bl_pick(self, text):
        self.bl_edit.setText(text)
        self._bl_add()

    def _bl_add(self):
        from . import tags
        new = [x for x in self.bl_edit.text().split(",") if tags.norm(x)]
        if not new:
            return
        tags.set_blacklist(list(tags.blacklist()) + new)
        self.bl_edit.clear()
        self._bl_fill()

    def _bl_remove(self, t):
        from . import tags
        tags.set_blacklist([x for x in tags.blacklist() if x != t])
        self._bl_fill()

    def _bl_clear_all(self):
        from . import tags
        if QMessageBox.question(self, "Tag blacklist", "Take every tag off the blacklist?") == \
                QMessageBox.StandardButton.Yes:
            tags.set_blacklist([])
            self._bl_fill()

    # ---- prompt helper
    def _helper_dir(self):
        from . import assistant
        return assistant.folder()

    def helper_status(self):
        from . import assistant
        if assistant.ready():
            t = f"Ready: {assistant.model_name()}  ·  {assistant.folder()}"
        else:
            t = ("Not set up. It improves and writes prompts with a small language model on your processor. "
                 "Optional: about 0.5 to 2.6 GB, downloaded once to a folder you pick.")
        self.h_state.setText(t)
        self.h_setup.setText("Change model…" if assistant.ready() else "Set up…")
        self.h_open.setVisible(os.path.isdir(assistant.folder()))
        self.h_remove.setVisible(os.path.isdir(assistant.folder()))

    def _helper_setup(self):
        from .helper_ui import setup
        if setup(self):
            self.app.toast("The prompt helper is ready: the sparkle next to a prompt.", "ok")
        self.helper_status()

    def _helper_remove(self):
        from . import assistant
        if QMessageBox.question(self, "Prompt helper", f"Delete the prompt helper's runtime and the models it "
                                                       f"downloaded from\n{assistant.folder()}?") != \
                QMessageBox.StandardButton.Yes:
            return
        assistant.remove()
        self.helper_status()
        self.app.toast("The prompt helper is removed.", "info")

    def _helper_button(self, on):
        cfg.set("helper_button", on)
        for k in ("image", "video"):
            self.app.pages[k].prompt.refresh_helper()

    # ---- vault
    def vault_status(self):
        from .vault import vault
        made, is_open = vault.exists(), vault.is_open()
        if not made:
            t = ("No vault yet. It keeps pictures and videos encrypted with your password (Argon2id + AES-256-GCM); "
                 "they are only ever decrypted in memory, inside this app.")
        elif is_open:
            t = f"Unlocked  ·  {len(vault.entries)} item{'s' if len(vault.entries) != 1 else ''} inside."
        else:
            t = "Locked."
        self.v_state.setText(t)
        self.v_make.setText("Unlock" if made else "Make the vault")
        self.v_make.setVisible(not is_open)
        self.v_lock.setVisible(is_open)
        self.v_pw.setVisible(made)
        self.v_del.setVisible(made)

    def _vault_open(self):
        self.app.ensure_vault()
        self.vault_status()

    def _vault_pw(self):
        from . import vault_ui
        d = vault_ui.ChangePasswordDialog(self)
        if d.exec():
            if d.result_value:
                vault_ui.RecoveryKeyDialog(self, d.result_value).exec()
                self.app.pages["gallery"].vault_changed()
                self.app.toast("Password changed and everything re-encrypted with a new key.", "ok")
            else:
                self.app.toast("Vault password changed.", "ok")

    def _vault_destroy(self):
        from PySide6.QtWidgets import QInputDialog
        from .vault import vault
        word, ok = QInputDialog.getText(
            self, "Delete the vault",
            "Everything in the vault is gone for good, and nothing can bring it back.\n"
            "Take out what you want to keep first.\n\nType DELETE to go on:")
        if not ok or word.strip() != "DELETE":
            return
        self.app.lock_vault(quiet=True)
        vault.destroy()
        self.app.pages["gallery"].vault_changed()
        self.vault_status()
        self.app.toast("The vault is deleted.", "info")

    def showEvent(self, e):
        super().showEvent(e)
        self.vault_status()
        self.helper_status()
        if not self._pc_checked:
            self._pc_checked = True
            self._check_pc()

    def _del_mode(self, mode, save=True):
        from . import shred
        if save:
            cfg.set("delete_mode", mode)
        self.del_line.setText({k: d for k, _t, d in shred.MODES}.get(mode, "") +
                              ("" if mode == "recycle" else "  Thumbnails, settings files and the gallery entry go "
                                                            "the same way."))
        self.passes_row.setVisible(mode == "shred")
        self._show_drive(getattr(self, "_drive", ""))

    def _show_drive(self, kind):
        self._drive = kind
        mode = cfg.get("delete_mode", "recycle")
        if kind == "SSD" and mode != "recycle":
            self.del_drive.setText("Your pictures folder is on an SSD. An SSD can keep old copies of data where no "
                                   "program can overwrite them, so shredding there is very good but not perfect. "
                                   "Pictures made straight into the vault never reach the drive unencrypted.")
        elif kind == "HDD" and mode != "recycle":
            self.del_drive.setText("Your pictures folder is on a hard disk: one pass of random data is enough there.")
        else:
            self.del_drive.setText("")
        self.del_drive.setVisible(bool(self.del_drive.text()))

    def update_state(self):
        from . import updater
        u = self.app.updates
        txt = {"idle": f"You have version {VERSION}.", "checking": "Looking for a new version…",
               "latest": f"Version {VERSION} is the newest.",
               "downloading": f"Downloading {u.info['version'] if u.info else ''}…  {int(u.progress * 100)} %",
               "ready": f"Version {u.info['version'] if u.info else ''} is ready - it goes in when Better Comfy "
                        "restarts.",
               "available": f"Version {u.info['version'] if u.info else ''} is out."
                            + ("" if updater.can_self_update() else " This copy isn't installed by the setup, so get "
                                                                     "it from GitHub."),
               "error": f"Couldn't check: {u.error}"}.get(u.state, "")
        self.upd_status.setText(txt)
        self.upd_install.setVisible(u.state in ("ready", "available"))
        self.upd_install.setText("Restart and update" if u.state == "ready" else "Open the release page")
        self.upd_check.setEnabled(u.state not in ("checking", "downloading", "ready"))

    def _repair(self):
        ins = self.link.install()
        if not ins:
            return
        if QMessageBox.question(self, "Repair", "Rebuild the damaged launcher programs in your ComfyUI's env\\Scripts "
                                                "folder?\n\nThey are made again from pip's own templates (nothing is "
                                                "downloaded). The broken ones are kept in "
                                                "Scripts\\_broken_launchers.") != QMessageBox.StandardButton.Yes:
            return
        try:
            fixed, left = comfy.repair_launchers(ins)
        except Exception as ex:
            QMessageBox.warning(self, "Repair", f"Could not repair them:\n{ex}")
            return
        msg = f"Repaired {len(fixed)} launcher{'s' if len(fixed) != 1 else ''}."
        if left:
            msg += f" {len(left)} could not be rebuilt: {', '.join(left[:4])}."
        self.app.toast(msg, "ok" if not left else "warn")
        self.link.refresh_models()

    def _start_stop(self):
        try:
            if self.link.running():
                self.link.stop()
            else:
                self.link.start()
        except Exception as ex:
            QMessageBox.warning(self, "ComfyUI", str(ex))

    def _open_web(self):
        u = self.link.url()
        if comfy.is_local(u):
            QDesktopServices.openUrl(QUrl(u))

    def _open_sub(self, sub):
        ins = self.link.install()
        if ins:
            system.open_folder(comfy.sub_dir(ins, sub))

    def _log(self):
        f = comfy.log_file()
        if os.path.isfile(f):
            os.startfile(f)
        else:
            self.app.toast("No log yet - it is written when Better Comfy starts ComfyUI.", "info")

    def _rescan(self):
        self._fill_installs(True)
        self.link.refresh_models()
        self.link.poll()
        self.app.toast("Looking for ComfyUI and models again…", "info")

    def _after(self, _i):
        cfg.set("after_queue", self.after.currentData())
        self.app.pages["queue"].sync_after()

    def sync_after(self):
        set_combo(self.after, cfg.get("after_queue"))

    def _paint_swatches(self):
        cur = QColor(cfg.get("accent")).name().lower()
        for b, hexc in self.swatches:
            sel = hexc.lower() == cur
            b.setStyleSheet(f"QPushButton#Swatch {{ background: {hexc}; border: 2px solid "
                            f"{'#FFFFFF' if sel else 'transparent'}; }}"
                            f"QPushButton#Swatch:hover {{ border: 2px solid #8A8A92; }}")

    def _accent(self, hexc):
        cfg.set("accent", hexc)
        self._paint_swatches()
        self.accent_changed.emit()

    def _custom_accent(self):
        c = QColorDialog.getColor(QColor(cfg.get("accent")), self, "Accent colour")
        if c.isValid():
            self._accent(c.name())

    def _shortcut(self, folder):
        try:
            p = system.make_shortcut(folder)
            self.app.toast(f"Shortcut made: {p}", "ok")
        except Exception as ex:
            QMessageBox.warning(self, "Shortcut", f"Could not make the shortcut:\n{ex}")
        self._shortcut_state()

    def _shortcut_state(self):
        if system.shortcut_exists(system.desktop_dir()):
            self.desk_btn.setText("Desktop shortcut ✓ (make again)")
        if system.shortcut_exists(system.start_menu_dir()):
            self.menu_btn.setText("Start menu entry ✓")

    def _reset(self):
        if QMessageBox.question(self, "Reset", "Put all settings back to how they were at first? (Your pictures, "
                                               "videos, gallery and LoRA notes stay.)") == QMessageBox.StandardButton.Yes:
            cfg.reset()
            self.app.toast("Settings reset - restart Better Comfy to see all of them.", "ok")
