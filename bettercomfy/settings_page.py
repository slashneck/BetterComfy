"""SETTINGS: ComfyUI (found by itself, how it is started), output folders, the queue, the look, shortcuts."""
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import QColorDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, \
    QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import comfy, icons, system, theme as T
from .config import APP_NAME, BASE, VERSION, cfg, resource
from .queue_page import AFTER
from .widgets import (Combo, Card, Scroll, Segmented, Slider, ToggleRow, button, field, hrow, label, set_combo, vcol)


class SettingsPage(QWidget):
    title = "Settings"
    subtitle = "ComfyUI, folders, queue, look & feel"
    accent_changed = Signal()

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
        self.t_upd = ToggleRow("Look for new versions", "Asks GitHub every few hours. That is the only thing Better "
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
