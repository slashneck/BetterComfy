"""The QUEUE page: everything waiting / running / done, reorder, pause, retry - and what happens when it is done."""
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from . import icons, jobs, theme as T
from .config import cfg
from .widgets import Combo, Card, Scroll, button, hrow, human_time, icon_button, label, set_combo, vcol

AFTER = [("nothing", "Do nothing"), ("free", "Free graphics memory"), ("stop_comfy", "Stop ComfyUI"),
         ("close", "Close Better Comfy"), ("sleep", "Put the PC to sleep"), ("shutdown", "Shut down the PC")]

STATUS = {"queued": ("Waiting", T.TEXT3), "running": ("Running", None), "done": ("Done", T.GOOD),
          "failed": ("Failed", T.BAD), "canceled": ("Stopped", T.TEXT3)}


def _thumb(path, size=64):
    pm = QPixmap(path) if path else QPixmap()
    out = QPixmap(size * 2, size * 2)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path_ = QPainterPath()
    path_.addRoundedRect(QRectF(0, 0, size * 2, size * 2), 18, 18)
    p.setClipPath(path_)
    p.fillRect(out.rect(), QColor(T.SURFACE3))
    if not pm.isNull():
        pm = pm.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((size * 2 - pm.width()) // 2, (size * 2 - pm.height()) // 2, pm)
    p.end()
    out.setDevicePixelRatio(2.0)
    return out


ITEM = {"queued": ("Waiting", T.TEXT3), "running": ("Making it now…", None), "stopping": ("Stopping…", T.WARN),
        "done": ("Done", T.GOOD), "canceled": ("Stopped", T.TEXT3), "removed": ("Taken out", T.TEXT3),
        "deleted": ("Deleted", T.TEXT3), "failed": ("Failed", T.BAD), "vault": ("In the vault", None)}


class ItemRow(QWidget):
    """One picture / video of a job: show + delete when done, stop when running, take out when waiting."""

    def __init__(self, row, j, i, it):
        super().__init__()
        self.setObjectName("ItemRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#ItemRow {{ background: {T.FIELD}; border-radius: 10px; }}")
        page, q, jid = row.page, row.page.app.queue, j["id"]
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 8, 6)
        h.setSpacing(10)
        st = it.get("status", "queued")
        refs = it.get("entries") or []
        ents = [page.app.history.get(e) for e in refs if not str(e).startswith("vault:")]
        ents = [e for e in ents if e]
        private = [r[6:] for r in refs if str(r).startswith("vault:")]
        if st == "done" and private and not ents:
            st = "vault"
        elif st == "done" and not ents and refs:
            st = "deleted"
        th = QLabel()
        th.setFixedSize(36, 36)
        th.setPixmap(_thumb(ents[-1].get("thumb") if ents else None, 36))
        h.addWidget(th)
        name, col = ITEM.get(st, (st, T.TEXT3))
        if (st == "running" and j["status"] == "running") or st == "vault":
            col = T.accent().name()
        lb = label(it.get("label") or "", None)
        lb.setStyleSheet(f"color: {T.TEXT if st in ('running', 'done') else T.TEXT2};")
        h.addWidget(lb)
        s = label(name, None)
        s.setStyleSheet(f"color: {col or T.TEXT2}; font-size: 12px;")
        h.addWidget(s)
        h.addStretch(1)
        if st == "done" and ents:
            h.addWidget(icon_button("eye", lambda: page.app.show_entry(ents[-1]["id"]), "Show it", size=14))
            h.addWidget(icon_button("trash", lambda: (page.app.delete_entries(ents), page.rebuild()),
                                    "Delete it (recycle bin)", size=14))
        elif st == "vault":
            h.addWidget(icon_button("lock", lambda: page.app.show_vault_entry(private[-1]), "Show it (in the vault)",
                                    size=14))
        elif st == "running" and j["status"] == "running":
            h.addWidget(icon_button("stop", lambda: q.stop_item(jid, i), "Stop this one - the rest go on", size=14,
                                    color="#FF8A8A"))
        elif st == "queued" and j["status"] in ("queued", "running"):
            h.addWidget(icon_button("close", lambda: q.remove_item(jid, i), "Take this one out of the queue", size=14))


class JobRow(QFrame):
    def __init__(self, page, j):
        super().__init__()
        self.page, self.jid = page, j["id"]
        self.setObjectName("JobRow")
        self.setStyleSheet(f"#JobRow {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 14px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        top = QWidget()
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        self.th = QLabel()
        self.th.setFixedSize(64, 64)
        h.addWidget(self.th)
        mid = QVBoxLayout()
        mid.setSpacing(5)
        self.kind = label("", "Badge")
        self.title = label("", "H3")
        self.title.setMinimumWidth(10)
        self.status = label("", "Faint")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        mid.addWidget(hrow(self.kind, self.title, None, spacing=8))
        mid.addWidget(self.bar)
        mid.addWidget(self.status)
        h.addLayout(mid, 1)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(2)
        h.addLayout(self.btns)
        self.exp_btn = icon_button("chevron_r", self.toggle, "Show each one", size=15)
        h.addWidget(self.exp_btn)
        outer.addWidget(top)
        self.sub = QWidget()
        self.sub_lay = QVBoxLayout(self.sub)
        self.sub_lay.setContentsMargins(78, 0, 0, 0)
        self.sub_lay.setSpacing(4)
        outer.addWidget(self.sub)
        self.multi = len(j.get("items") or []) > 1
        self.exp_btn.setVisible(self.multi)
        if self.multi:
            top.setCursor(Qt.CursorShape.PointingHandCursor)
            top.mousePressEvent = lambda e: self.toggle()
        self.refresh(j, full=True)

    def expanded(self):
        return self.jid in self.page.expanded

    def toggle(self):
        if self.expanded():
            self.page.expanded.discard(self.jid)
        else:
            self.page.expanded.add(self.jid)
        j = self.page.app.queue.job(self.jid)
        if j:
            self._items(j)

    def _items(self, j):
        while self.sub_lay.count():
            w = self.sub_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        on = self.multi and self.expanded()
        self.exp_btn.setIcon(icons.icon("chevron" if on else "chevron_r", "#A1A1AA", 15))
        self.sub.setVisible(on)
        if on:
            for i, it in enumerate(j.get("items") or []):
                self.sub_lay.addWidget(ItemRow(self, j, i, it))

    def refresh(self, j, full=False):
        q = self.page.app.queue
        st = j["status"]
        items = j.get("items") or []
        if full:
            thumb = j.get("thumb")
            if j.get("outputs"):
                e = self.page.app.history.get(j["outputs"][-1])
                thumb = (e or {}).get("thumb") or thumb
            self.th.setPixmap(_thumb(thumb))
            self.kind.setText("IMAGE" if j["kind"] == "image" else "VIDEO")
            n = len(items) or int(j["params"].get("count", 1))
            self.title.setText((j.get("title") or "").strip() + (f"   × {n}" if n > 1 else ""))
            self.title.setToolTip(j["params"].get("prompt", ""))
            while self.btns.count():
                w = self.btns.takeAt(0).widget()
                if w:
                    w.deleteLater()
            add = self.btns.addWidget
            if st == "queued":
                add(icon_button("up", lambda: q.move(self.jid, -1), "Earlier", size=15))
                add(icon_button("down", lambda: q.move(self.jid, 1), "Later", size=15))
                add(icon_button("bolt", lambda: q.to_top(self.jid), "Next up", size=15))
                add(icon_button("close", lambda: q.cancel(self.jid), "Take the whole job out of the queue", size=15))
            elif st == "running":
                add(icon_button("stop", lambda: q.cancel(self.jid), "Stop the whole job", size=15, color="#FF8A8A"))
            else:
                if st in ("failed", "canceled"):
                    add(icon_button("refresh", lambda: q.retry(self.jid), "Try again (what is not done yet)", size=15))
                if j.get("outputs"):
                    add(icon_button("eye", lambda: self.page.app.show_entry(j["outputs"][-1]), "Show the result",
                                    size=15))
                add(icon_button("trash", lambda: q.remove(self.jid), "Remove from the list", size=15))
            self._items(j)
        name, col = STATUS.get(st, (st, T.TEXT3))
        txt = j.get("text") or ""
        if st == "queued":
            est = jobs.estimate(j["kind"], j.get("units", 0))
            txt = "Waiting" + (f"  ·  ≈ {human_time(est)}" if est else "")
        elif st == "running":
            el = time.time() - j.get("started", time.time())
            est = jobs.estimate(j["kind"], j.get("units", 0))
            left = f"  ·  {human_time(max(0, est - el))} left" if est and est > el else ""
            txt = f"{txt}  ·  {human_time(el)}{left}"
        elif st == "failed":
            txt = "Failed: " + (j.get("error") or "")
        if self.multi:
            cnt = {}
            for it in items:
                cnt[it["status"]] = cnt.get(it["status"], 0) + 1
            bits = [f"{cnt[k]} {w}" for k, w in (("done", "done"), ("running", "now"), ("queued", "waiting"),
                                                 ("canceled", "stopped"), ("removed", "taken out")) if cnt.get(k)]
            txt += ("  ·  " if txt else "") + ", ".join(bits)
        self.status.setText(txt)
        self.status.setStyleSheet(f"color: {col or T.TEXT2}; font-size: 12px;")
        self.bar.setVisible(st in ("running",))
        self.bar.setValue(int(1000 * (j.get("progress") or 0)))
        self.setStyleSheet(f"#JobRow {{ background: {T.SURFACE}; border: 1px solid "
                           f"{T.accent().name() if st == 'running' else T.BORDER}; border-radius: 14px; }}")


class QueuePage(QWidget):
    title = "Queue"
    subtitle = "Everything waiting and running"

    def __init__(self, app):
        super().__init__()
        self.app = app
        q = app.queue
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 18, 28, 18)
        v.setSpacing(14)
        self.pause_btn = button("Pause", self._pause, None, "pause", "Finish the current one, then wait")
        self.summary = label("", "Muted")
        self.after = Combo()
        for k, t in AFTER:
            self.after.addItem(t, k)
        set_combo(self.after, cfg.get("after_queue", "nothing"))
        self.after.currentIndexChanged.connect(lambda _=0: cfg.set("after_queue", self.after.currentData()))
        self.after.setMinimumWidth(200)
        top = Card(None, margins=(16, 12, 16, 12))
        top.add(hrow(self.pause_btn, button("Stop all", q.cancel_all, "Danger", "stop", "Stop the running one and "
                                                                                       "empty the queue"),
                     button("Clear finished", q.clear_finished, "Ghost", "trash"), 12, self.summary, None,
                     label("When the queue is done", "Muted"), self.after, spacing=8))
        v.addWidget(top)
        self.scroll = Scroll((0, 0, 4, 0), 10)
        v.addWidget(self.scroll, 1)
        self.empty = vcol(label("The queue is empty", "H2"),
                          label("Everything you press Generate on waits here and runs one after the other - pictures "
                                "and videos mixed. You can keep adding while it works.", "Muted", wrap=True),
                          spacing=6)
        self.rows = {}
        self.expanded = set()
        q.changed.connect(self.rebuild)
        q.job_changed.connect(self._one)
        self._tick = QTimer(self, interval=1000, timeout=self._clock)
        self._tick.start()
        self.rebuild()

    def _pause(self):
        q = self.app.queue
        q.resume() if q.paused else q.pause()

    def sync_after(self):
        set_combo(self.after, cfg.get("after_queue", "nothing"))

    def rebuild(self):
        q = self.app.queue
        lay = self.scroll.lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() and it.widget() is not self.empty:
                it.widget().deleteLater()
        self.rows = {}
        order = [j for j in q.jobs if j["status"] == "running"] + [j for j in q.jobs if j["status"] == "queued"] + \
                list(reversed([j for j in q.jobs if j["status"] not in ("running", "queued")]))
        if not order:
            lay.addWidget(self.empty)
            self.empty.show()
        else:
            self.empty.hide()
            for j in order:
                r = JobRow(self, j)
                self.rows[j["id"]] = r
                lay.addWidget(r)
        lay.addStretch(1)
        self.pause_btn.setText("Resume" if q.paused else "Pause")
        self.pause_btn.setIcon(icons.icon("play" if q.paused else "pause", "#D4D4D8"))
        self._summary()

    def _summary(self):
        q = self.app.queue
        n = q.active_count()
        _, left = q.overall()
        s = "Nothing waiting" if not n else f"{n} to do" + (f"  ·  ≈ {human_time(left)} left" if left else "")
        if q.paused and q.pending():
            s += "  ·  paused"
        self.summary.setText(s)

    def _one(self, jid):
        j = self.app.queue.job(jid)
        if j and jid in self.rows:
            self.rows[jid].refresh(j)
        self._summary()

    def _clock(self):
        cur = self.app.queue.running()
        if cur and cur["id"] in self.rows and self.isVisible():
            self.rows[cur["id"]].refresh(cur)
            self._summary()
