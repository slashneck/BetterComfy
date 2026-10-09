"""Parts shared by the Image and Video pages: the prompt editor, the LoRA stack (with trigger words), the LoRA picker,
the seed control, the Generate bar and the result pane (player + filmstrip + actions)."""
import re
import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QTextCursor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import comfy, icons, loras, media, theme as T, workflows
from .config import cfg
from .widgets import (BLUR_ROLE, BlurTextDelegate, Combo, Card, ChipBox, Collapsible, Filmstrip, Player, Segmented, Slider,
                      Toggle, ToggleRow, blur_pixmap, blur_widget, button, chip, field, hrow, icon_button, label, nice_name,
                      quiet, vcol)


# ------------------------------------------------------------------------------------------------ prompt editor

class PromptEdit(QPlainTextEdit):
    """Ctrl+Enter: generate. Ctrl+Up / Ctrl+Down: weight of the selected words (or the word at the cursor)."""
    submit = Signal()

    def __init__(self, placeholder="", height=120):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height + 160)
        self.setTabChangesFocus(True)
        self.tags_on = lambda: False            # set by the page: tag suggestions for this model or not
        self._pop = None
        self._frag = None
        self._sug = QTimer(self, singleShot=True, interval=70, timeout=self._suggest)
        self.cursorPositionChanged.connect(self._moved)
        self._typing = False

    # ---- tag suggestions
    def _popup(self):
        if self._pop is None:
            from .helper_ui import TagPopup
            self._pop = TagPopup()
            self._pop.picked.connect(self._take)
        return self._pop

    def _hide_tags(self):
        if self._pop is not None:
            self._pop.hide()

    def _tags_open(self):
        return self._pop is not None and self._pop.isVisible()

    def _moved(self):
        if not self._typing:
            self._hide_tags()

    def _suggest(self):
        self._typing = False
        if not self.hasFocus() or not self.tags_on():
            return self._hide_tags()
        from . import tags
        if not tags.ready():
            tags.load()                         # once, about 70 ms (normally already loaded on focus)
        c = self.textCursor()
        txt, pos = self.toPlainText(), c.position()
        if c.hasSelection() or (pos < len(txt) and txt[pos] not in ",\n)}|] "):
            return self._hide_tags()
        a = pos
        while a > 0:
            ch = txt[a - 1]
            if ch in ",\n(){}|[]:<>":
                # "miku (c": a bracket after a word belongs to the tag, one at the start is a weight
                if ch == "(" and a >= 3 and txt[a - 2] == " " and txt[a - 3].isalnum():
                    a -= 1
                    continue
                if ch == "(" and a >= 2 and txt[a - 2] == "\\":
                    a -= 2
                    continue
                break
            a -= 1
        if txt.rfind("<", 0, pos) > txt.rfind(">", 0, pos):
            return self._hide_tags()            # inside <lora:...>
        frag = txt[a:pos]
        lead = len(frag) - len(frag.lstrip())
        frag = frag.strip()
        res = tags.search(frag) if len(frag) >= 2 else []
        if not res or (len(res) == 1 and res[0][0].lower() == frag.lower()):
            return self._hide_tags()
        self._frag = (a + lead, pos)
        at = self.viewport().mapToGlobal(self.cursorRect().bottomLeft()) + QPoint(-6, 6)
        self._popup().show_for(res, at)

    def _take(self, tag):
        from . import tags
        if not tag or not self._frag:
            return
        a, b = self._frag
        txt = self.toPlainText()
        if b > len(txt):
            return self._hide_tags()
        new = tags.for_prompt(tag)
        nxt = txt[b:b + 1]
        if nxt in ("", "\n"):
            new += ", "
        c = self.textCursor()
        c.beginEditBlock()
        c.setPosition(a)
        c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(new)
        c.endEditBlock()
        self.setTextCursor(c)
        self._hide_tags()

    def event(self, e):
        # Tab normally moves on to the next field: with suggestions open it takes the suggestion
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Tab and self._tags_open():
            self._take(self._pop.current_tag())
            return True
        return super().event(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if self.tags_on():
            from . import tags
            tags.load_async()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTimer.singleShot(150, lambda: None if self.hasFocus() else self._hide_tags())

    def hideEvent(self, e):
        self._hide_tags()
        super().hideEvent(e)

    def keyPressEvent(self, e):
        mod = e.modifiers()
        if self._tags_open():
            k = e.key()
            if k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not mod & Qt.KeyboardModifier.ControlModifier:
                self._pop.move_sel(-1 if k == Qt.Key.Key_Up else 1)
                return
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mod & Qt.KeyboardModifier.ControlModifier:
                self._take(self._pop.current_tag())
                return
            if k == Qt.Key.Key_Escape:
                self._hide_tags()
                return
        if e.text() and (e.text().isprintable() or e.key() == Qt.Key.Key_Backspace) and \
                not mod & Qt.KeyboardModifier.ControlModifier:
            self._typing = True
            super().keyPressEvent(e)
            self._sug.start()
            return
        if e.key() == Qt.Key.Key_Backspace:
            self._typing = True
            super().keyPressEvent(e)
            self._sug.start()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mod & Qt.KeyboardModifier.ControlModifier:
            self.submit.emit()
            return
        if mod & Qt.KeyboardModifier.ControlModifier and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.weight(0.05 if e.key() == Qt.Key.Key_Up else -0.05)
            return
        super().keyPressEvent(e)

    def weight(self, d):
        txt = self.toPlainText()
        c = self.textCursor()
        pos = c.position()
        # inside "(words:1.2)" already?
        for m in re.finditer(r"\(([^()]+?):(-?\d+(?:\.\d+)?)\)", txt):
            if m.start() <= pos <= m.end():
                w = round(float(m.group(2)) + d, 2)
                new = m.group(1) if abs(w - 1.0) < 1e-6 else f"({m.group(1)}:{w:g})"
                self._replace(m.start(), m.end(), new, select=True)
                return
        if c.hasSelection():
            a, b = c.selectionStart(), c.selectionEnd()
        else:
            a = b = pos
            while a > 0 and txt[a - 1] not in ",\n":
                a -= 1
            while b < len(txt) and txt[b] not in ",\n":
                b += 1
        seg = txt[a:b]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        a, b = a + lead, b - trail
        if a >= b:
            return
        self._replace(a, b, f"({txt[a:b]}:{1 + d:g})", select=True)

    def _replace(self, a, b, new, select=False):
        c = self.textCursor()
        c.beginEditBlock()
        c.setPosition(a)
        c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(new)
        c.endEditBlock()
        if select:
            c.setPosition(a)
            c.setPosition(a + len(new), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)


def remember_prompt(text):
    text = (text or "").strip()
    if not text:
        return
    h = [x for x in (cfg.get("prompt_history") or []) if x != text]
    cfg.set("prompt_history", ([text] + h)[:40])


def history_menu(parent, on_pick):
    m = QMenu(parent)
    hist = cfg.get("prompt_history") or []
    if not hist:
        a = m.addAction("No prompts yet - they are kept here when you generate")
        a.setEnabled(False)
    for t in hist[:25]:
        short = t.replace("\n", " ")
        a = m.addAction(short[:80] + ("…" if len(short) > 80 else ""))
        a.setToolTip(t)
        a.triggered.connect(lambda _=False, t=t: on_pick(t))
    return m


class PromptCard(Card):
    """Prompt + words to avoid, history, token count, optional quick chips."""
    changed = Signal()
    submit = Signal()

    def __init__(self, title="Prompt", placeholder="", chips=None, neg_toggle_text=None, neg_sub=None, height=118):
        self.ai_btn = icon_button("sparkle", None, "Prompt helper: improve, extend or write the prompt", size=16)
        self.hist_btn = icon_button("clock", None, "Recent prompts", size=16)
        self.clear_btn = icon_button("close", None, "Clear the prompt", size=16)
        super().__init__(title, [self.ai_btn, self.hist_btn, self.clear_btn])
        self.style_fn = lambda: "natural"       # set by the page: tags / mixed / natural / motion
        self.what = "image"
        self.edit = PromptEdit(placeholder, height)
        self.edit.tags_on = self._tags_on
        self.edit.textChanged.connect(self._changed)
        self.edit.submit.connect(self.submit.emit)
        self.hist_btn.clicked.connect(lambda: history_menu(self, self.set_text).exec(
            self.hist_btn.mapToGlobal(self.hist_btn.rect().bottomLeft())))
        self.clear_btn.clicked.connect(lambda: self.edit.setPlainText(""))
        self.ai_btn.clicked.connect(self._helper_menu)
        self.add(self.edit)
        from .helper_ui import Panel
        self.panel = Panel()
        self.panel.use.connect(self._use_answer)
        self.add(self.panel)
        self.refresh_helper()
        self.info = label("", "Faint")
        self.tip = label("Ctrl+Enter generates · Ctrl+↑/↓ weights a word · {a|b} picks one per image", "Faint",
                         wrap=True)
        self.tip.setStyleSheet("color:#4A4A52; font-size:11px;")
        self.info.setStyleSheet("font-size:11px;")
        row = hrow(self.tip, self.info, spacing=10)
        row.layout().setStretch(0, 1)
        self.add(row)
        self.chipbox = None
        if chips:
            self.chipbox = ChipBox()
            self._chips = []
            for c in chips:
                b = chip(c, None, True, "Press: into the prompt (again: out of it)")
                b.clicked.connect(lambda _=False, c=c: self.toggle_word(c, front=False))
                self.chipbox.add(b)
                self._chips.append((b, c))
            self.add(self.chipbox)
        self.neg = Collapsible("Words to avoid", False, "The negative prompt")
        self.neg_toggle = None
        if neg_toggle_text:
            self.neg_toggle = ToggleRow(neg_toggle_text, neg_sub, False, lambda v: self._changed())
            self.neg.add(self.neg_toggle)
        self.neg_edit = PromptEdit("Things you do not want, e.g. blurry, extra fingers, text", 64)
        self.neg_edit.tags_on = self._tags_on
        self.neg_edit.textChanged.connect(self._changed)
        self.neg_edit.submit.connect(self.submit.emit)
        self.neg.add(self.neg_edit)
        self.add(self.neg)

    def text(self):
        return self.edit.toPlainText()

    # ---- prompt helper and tag suggestions
    def refresh_helper(self):
        self.ai_btn.setVisible(bool(cfg.get("helper_button", True)))

    def _tags_on(self):
        mode = cfg.get("tag_suggest", "auto")
        return mode == "on" or (mode == "auto" and self.style_fn() in ("tags", "mixed"))

    def _helper_menu(self):
        from . import assistant
        m = QMenu(self)
        if not assistant.ready():
            m.addAction(icons.icon("sparkle", "#A1A1AA", 16), "Set up the prompt helper…").triggered.connect(
                self._setup)
        else:
            style = self.style_fn()
            has = bool(self.text().strip())
            if style == "motion":
                items = [("motion", "Write the motion", "video", True), ("improve", "Improve it", "sparkle", has),
                         ("shorter", "Make it shorter", "close", has)]
            else:
                tagged = style in ("tags", "mixed")
                items = [("improve", "Improve it", "sparkle", has), ("detail", "More detail", "plus", has),
                         ("shorter", "Make it shorter", "close", has),
                         ("idea", "Write a prompt from my words", "edit", has),
                         ("convert", "Turn into sentences" if tagged else "Turn into tags", "swap", has),
                         ("surprise", "Surprise me", "dice", True)]
            for key, text, ic, on in items:
                a = m.addAction(icons.icon(ic, "#A1A1AA", 16), text)
                a.setEnabled(on)
                a.triggered.connect(lambda _=False, k=key: self._ask(k))
            if not has:
                m.addSeparator()
                m.addAction("Write a few words first for the other options").setEnabled(False)
            if style == "tags" and assistant.model_path("tags"):
                # what the tag model may add: by default it follows the prompt
                m.addSeparator()
                rm = m.addMenu("Rating for new tags")
                cur = cfg.get("tipo_rating", "auto")
                for k, t in (("auto", "Like the prompt"), ("safe", "Safe"), ("sensitive", "Sensitive"),
                             ("nsfw", "NSFW"), ("explicit", "Explicit")):
                    a = rm.addAction(t)
                    a.setCheckable(True)
                    a.setChecked(cur == k)
                    a.triggered.connect(lambda _=False, k=k: cfg.set("tipo_rating", k))
            m.addSeparator()
            m.addAction(icons.icon("settings", "#A1A1AA", 16), "Models…").triggered.connect(self._setup)
        m.exec(self.ai_btn.mapToGlobal(self.ai_btn.rect().bottomLeft()))

    def _ask(self, task):
        style = self.style_fn()
        if task == "convert":
            style = "natural" if style in ("tags", "mixed") else "tags"
        self.panel.start(task, self.text(), style, self.what)

    def _setup(self):
        from .helper_ui import setup
        from . import assistant
        was = assistant.ready()
        if setup(self.window()) and not was:
            self._helper_menu()

    def _use_answer(self, t):
        """Into the prompt as one step, so Ctrl+Z brings the old prompt back."""
        if not t:
            return
        c = self.edit.textCursor()
        c.beginEditBlock()
        c.select(QTextCursor.SelectionType.Document)
        c.insertText(t)
        c.endEditBlock()
        self.edit.setTextCursor(c)
        self.panel.close_panel()

    def set_text(self, t):
        self.edit.setPlainText(t or "")
        c = self.edit.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.edit.setTextCursor(c)

    def toggle_word(self, word, front=True):
        t = self.text()
        if loras.has_word(t, word):
            self.edit.setPlainText(loras.toggle_word(t, word))
        elif front:
            self.edit.setPlainText(loras.toggle_word(t, word))
        else:
            self.edit.setPlainText((t.rstrip().rstrip(",") + ", " if t.strip() else "") + word)

    def _changed(self):
        t = self.text()
        n = len(re.findall(r"[\w']+|[^\w\s]", t))
        self.info.setText(f"~{n} tokens" + ("  · long - may lose focus" if n > 150 else ""))
        if self.chipbox:
            for b, c in self._chips:
                quiet(b, b.setChecked, loras.has_word(t, c))
        self.changed.emit()


# ------------------------------------------------------------------------------------------------ lora stack

class LoraPicker(QDialog):
    """Pick a LoRA: search, favourites first, trigger words shown."""

    def __init__(self, parent, names, target, install, title="Add a LoRA"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 560)
        self.chosen = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        v.addWidget(label(title, "H2"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search LoRAs…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        v.addWidget(self.search)
        self.only = ToggleRow("Only ones that fit this model", "Ones that can't be told are shown with a ?", True,
                              lambda _v: self._fill())
        self.tag = Combo()
        self.tag.addItem("All tags", "")
        for t in loras.all_tags():
            self.tag.addItem(t, t)
        self.tag.currentIndexChanged.connect(lambda _=0: self._fill())
        v.addWidget(hrow(self.only, self.tag, spacing=10))
        self.list = QListWidget()
        self.list.setItemDelegate(BlurTextDelegate(self.list))
        self.list.setIconSize(QSize(44, 44))
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda it: self._pick(it))
        v.addWidget(self.list, 1)
        bb = hrow(None, button("Cancel", self.reject, "Ghost"), button("Add", lambda: self._pick(self.list.currentItem()),
                                                                       "Accent", "plus"))
        v.addWidget(bb)
        self.names, self.target, self.install = names, target, install
        self.info = {}
        for n in names:
            p = comfy.model_path(install, "loras", n)
            self.info[n] = loras.file_info(p) if p else {"family": "other"}
        self._fill()
        self.search.setFocus()

    def _fill(self):
        q = self.search.text().strip().lower()
        tag = self.tag.currentData()
        self.list.clear()
        rows = []
        for n in self.names:
            fam = loras.made_for_label(n, self.info[n])
            fits = loras.fit(n, self.info[n], self.target)
            if self.only.isChecked() and fits == "no":
                continue
            if tag and tag.lower() not in (t.lower() for t in loras.lora_tags(n)):
                continue
            if self.target == "wan" and self.only.isChecked() and workflows.is_low_half(n) and \
                    workflows.lora_partner(n.replace("Low", "High").replace("low", "high").replace("_L_", "_H_"),
                                           self.names):
                continue                          # the low half of a pair: added with its high half
            nt = loras.notes(n)
            if q and q not in n.lower() and q not in nt.get("triggers", "").lower() and q not in nt.get("note", "").lower():
                continue
            rows.append((not nt.get("favorite"), fits != "yes", n.lower(), n, fam, nt, fits))
        hide = cfg.get("lora_blur_nsfw", False)
        for _, _, _, n, fam, nt, fits in sorted(rows):
            it = QListWidgetItem()
            star = "★  " if nt.get("favorite") else ""
            trig = nt.get("triggers", "")
            maybe = "  ?" if fits == "maybe" else ""
            it.setText(f"{star}{nice_name(n)}{maybe}\n{fam}" + (f"  ·  {trig[:60]}" if trig else ""))
            if fits == "maybe":
                it.setToolTip("It can't be told what this LoRA was made for. Set it on the LoRAs page (Made for).")
            blur = hide and loras.is_nsfw(n)
            it.setData(BLUR_ROLE, blur)
            prev = loras.preview_for(self.install, n)
            if prev:
                pm = QPixmap(prev).scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation)
                it.setIcon(blur_pixmap(pm, 6) if blur else pm)
            else:
                it.setIcon(icons.icon("lora", "#6B6B73", 22))
            it.setData(Qt.ItemDataRole.UserRole, n)
            it.setSizeHint(QSize(0, 54))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _pick(self, it):
        if it is None:
            return
        self.chosen = it.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._pick(self.list.currentItem())
            return
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.search.hasFocus():
            self.list.setFocus()
        super().keyPressEvent(e)


class LoraStack(QWidget):
    """The LoRAs on a page: on/off, strength, trigger words to click into the prompt; WAN pairs found by themselves."""
    changed = Signal()

    def __init__(self, mode, prompt_card, get_target, get_install):
        super().__init__()
        self.mode = mode                       # 'image' | 'video'
        self.prompt = prompt_card
        self.get_target = get_target
        self.get_install = get_install
        self.items = []
        self.options = []
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(8)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.v.addLayout(self.rows)
        self.empty = label("No LoRAs yet. Add one to change the style, a character or the motion.", "Faint", wrap=True)
        self.v.addWidget(self.empty)
        self.add_btn = button("Add LoRA", self.pick, None, "plus")
        self.v.addWidget(self.add_btn)
        prompt_card.changed.connect(self._sync_chips)
        self._chips = []

    def set_options(self, names):
        self.options = list(names or [])

    def set_items(self, items):
        self.items = [dict(x) for x in items or []]
        self.rebuild()

    def value(self):
        return [dict(x) for x in self.items]

    def pick(self):
        if not self.options:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "LoRAs", "No LoRA files found yet. Put .safetensors LoRAs into ComfyUI's "
                                                   "models/loras folder (LoRAs page → Open folder).")
            return
        d = LoraPicker(self.window(), self.options, self.get_target(), self.get_install())
        if d.exec() and d.chosen:
            self.add(d.chosen)

    def add(self, name, strength=None):
        nt = loras.notes(name)
        s = float(strength if strength is not None else (nt.get("strength") or 1.0))
        it = {"file": name, "strength": s, "on": True}
        if self.mode == "video":
            part = workflows.lora_partner(name, self.options)
            if part:
                it.update(use="pair", low_file=part)
            else:
                it["use"] = "both"
        self.items.append(it)
        self.rebuild()
        trig = loras.triggers(name)
        if trig and cfg.get("auto_triggers", True):
            t = self.prompt.text()
            for w in reversed(trig[:1]):
                if not loras.has_word(t, w):
                    self.prompt.toggle_word(w)
        self.changed.emit()

    def rebuild(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._chips = []
        self._shown_words = set()               # a trigger word shows once, however often it is saved
        for i, it in enumerate(self.items):
            self.rows.addWidget(self._row(i, it))
        self.empty.setVisible(not self.items)
        self._sync_chips()

    def _row(self, i, it):
        box = QWidget()
        box.setObjectName("LoraRow")
        box.setStyleSheet(f"#LoraRow {{ background: {T.FIELD}; border: 1px solid {T.BORDER}; border-radius: 11px; }}")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 9, 8, 10)
        v.setSpacing(6)
        tg = Toggle(it.get("on", True), lambda val, it=it: self._set(it, "on", val))
        name = label(nice_name(it.get("file")), "H3")
        name.setToolTip(it.get("file"))
        name.setMinimumWidth(10)
        if cfg.get("lora_blur_nsfw", False) and loras.is_nsfw(it.get("file")):
            blur_widget(name, True)
            name.setToolTip("")
        info = loras.file_info(self._path(it.get("file"))) if self._path(it.get("file")) else {"family": "other"}
        fam = info.get("family", "other")
        warn = None
        if self.mode == "image" and loras.fit(it.get("file"), info, self.get_target()) == "no":
            warn = label("⚠", "Muted")
            warn.setStyleSheet("color:#F5B041;")
            warn.setToolTip(f"This looks like a {loras.made_for_label(it.get('file'), info)} LoRA - it may not fit "
                            "this model.")
        rm = icon_button("close", lambda i=i: self._remove(i), "Take this LoRA out", size=14)
        up = icon_button("up", lambda i=i: self._move(i, -1), "Earlier", size=13)
        up.setEnabled(i > 0)
        top = hrow(tg, name, *( [warn] if warn else []), None, up, rm, spacing=8)
        v.addWidget(top)
        sl = Slider(-2.0, 4.0, 0.05, 2, float(it.get("strength", 1.0)), lambda val, it=it: self._set(it, "strength", val),
                    soft_hi=2.0)
        v.addWidget(field("Strength", sl, "How strongly it applies. Too strong (burnt / odd)? Lower it.", label_w=62))
        if self.mode == "video":
            use = Segmented([("both", "Both", "A single-file LoRA: on the motion and the detail model"),
                             ("high", "Motion", "Only on the motion (high noise) model"),
                             ("low", "Detail", "Only on the detail (low noise) model"),
                             ("pair", "Pair", "A pair: this file on motion, its partner on detail")],
                            lambda val, it=it: (self._set(it, "use", val), self.rebuild()), it.get("use", "both"),
                            height=28)
            v.addWidget(field("Goes on", use, label_w=62))
            if it.get("use") == "pair":
                cb = Combo()
                cb.addItem("Pick the low file", "")
                for o in self.options:
                    cb.addItem(nice_name(o), o)
                idx = cb.findData(it.get("low_file", ""))
                cb.setCurrentIndex(max(0, idx))
                cb.currentIndexChanged.connect(lambda _=0, it=it, cb=cb: self._set(it, "low_file", cb.currentData()))
                v.addWidget(field("Low file", cb, label_w=62))
        words = []
        for w in loras.triggers(it.get("file")):
            if loras.word_key(w) not in self._shown_words:
                self._shown_words.add(loras.word_key(w))
                words.append(w)
        if words:
            cbx = ChipBox(5)
            for w in words:
                c = chip(w, None, True, "Press: into the prompt (again: out of it)")
                c.clicked.connect(lambda _=False, w=w: self.prompt.toggle_word(w))
                cbx.add(c)
                self._chips.append((c, w))
            v.addWidget(cbx)
        elif fam != "wan" or self.mode == "image":
            hint = label("No trigger words saved - add them on the LoRAs page.", "Faint")
            hint.setStyleSheet("color:#4A4A52; font-size:11px;")
            v.addWidget(hint)
        return box

    def _path(self, name):
        from . import comfy
        return comfy.model_path(self.get_install(), "loras", name) if name else None

    def _set(self, it, k, v):
        it[k] = v
        if k == "strength" and abs(float(v)) > 0:
            pass
        self.changed.emit()

    def _remove(self, i):
        if 0 <= i < len(self.items):
            self.items.pop(i)
        self.rebuild()
        self.changed.emit()

    def _move(self, i, d):
        k = i + d
        if 0 <= i < len(self.items) and 0 <= k < len(self.items):
            self.items[i], self.items[k] = self.items[k], self.items[i]
            self.rebuild()
            self.changed.emit()

    def _sync_chips(self):
        t = self.prompt.text()
        for c, w in self._chips:
            try:
                quiet(c, c.setChecked, loras.has_word(t, w))
            except RuntimeError:
                pass


# ------------------------------------------------------------------------------------------------ seed

class SeedBox(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.mode = Segmented([("random", "Random", "A new result every time"),
                               ("fixed", "Fixed", "The same seed again - change one setting and compare")],
                              self._mode, "random", expand=False, height=32)
        self.num = QSpinBox()
        self.num.setRange(0, 2 ** 31 - 1)
        self.num.setFixedWidth(118)
        self.num.valueChanged.connect(lambda _v: self.changed.emit())
        self.dice = icon_button("dice", self._roll, "A new random seed", size=16)
        h.addWidget(self.mode)
        h.addWidget(self.num, 1)
        h.addWidget(self.dice)
        self.last = None
        self._mode(self.mode.value())

    def _mode(self, m):
        self.num.setEnabled(m == "fixed")
        self.dice.setEnabled(m == "fixed")
        self.changed.emit()

    def _roll(self):
        import random
        self.num.setValue(random.randint(0, 2 ** 31 - 1))

    def value(self):
        return int(self.num.value()) if self.mode.value() == "fixed" else -1

    def set(self, seed):
        if seed is None or int(seed) < 0:
            self.mode.set("random", animate=False)
            self._mode("random")
        else:
            self.mode.set("fixed", animate=False)
            quiet(self.num, self.num.setValue, int(seed) % (2 ** 31))
            self._mode("fixed")


# ------------------------------------------------------------------------------------------------ my presets

class UserPresets(QWidget):
    """Your own presets for a page: save the settings under a name, load them with one click."""
    apply = Signal(dict)

    def __init__(self, kind, get_params, skip=()):
        super().__init__()
        self.kind, self.get_params, self.skip = kind, get_params, set(skip)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.cb = Combo()
        self.cb.setToolTip("Your saved presets")
        self.cb.activated.connect(self._picked)
        self.save_btn = icon_button("download", self._save, "Save the current settings as a preset", size=16)
        self.del_btn = icon_button("trash", self._delete, "Delete this preset", size=16)
        h.addWidget(self.cb, 1)
        h.addWidget(self.save_btn)
        h.addWidget(self.del_btn)
        self.fill()

    def _all(self):
        return dict((cfg.get("user_presets") or {}).get(self.kind) or {})

    def _store(self, d):
        allp = dict(cfg.get("user_presets") or {})
        allp[self.kind] = d
        cfg.set("user_presets", allp)

    def fill(self, keep=None):
        self.cb.blockSignals(True)
        self.cb.clear()
        names = sorted(self._all(), key=str.lower)
        self.cb.addItem("My presets…" if names else "My presets - none saved yet", "")
        for n in names:
            self.cb.addItem(n, n)
        if keep:
            self.cb.setCurrentIndex(max(0, self.cb.findData(keep)))
        self.cb.blockSignals(False)
        self.del_btn.setEnabled(bool(self.cb.currentData()))

    def _picked(self, _i):
        n = self.cb.currentData()
        self.del_btn.setEnabled(bool(n))
        if n and n in self._all():
            self.apply.emit(dict(self._all()[n]))

    def _save(self):
        d = QDialog(self.window())
        d.setWindowTitle("Save preset")
        v = QVBoxLayout(d)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Save as a preset", "H2"))
        name = QLineEdit(self.cb.currentData() or "")
        name.setPlaceholderText("Name, e.g. Anime portrait - quality")
        v.addWidget(name)
        with_prompt = ToggleRow("Include the prompt", "Off: only the settings (model, size, quality, LoRAs …)", False)
        v.addWidget(with_prompt)
        v.addWidget(hrow(None, button("Cancel", d.reject, "Ghost"), button("Save", d.accept, "Accent", "check")))
        name.returnPressed.connect(d.accept)
        d.resize(420, 10)
        if not d.exec() or not name.text().strip():
            return
        p = {k: v for k, v in self.get_params().items() if k not in self.skip}
        if not with_prompt.isChecked():
            for k in ("prompt", "negative"):
                p.pop(k, None)
        allp = self._all()
        allp[name.text().strip()] = p
        self._store(allp)
        self.fill(name.text().strip())

    def _delete(self):
        n = self.cb.currentData()
        if not n:
            return
        allp = self._all()
        allp.pop(n, None)
        self._store(allp)
        self.fill()


# ------------------------------------------------------------------------------------------------ generate bar

class GenerateBar(QWidget):
    """Sticky at the bottom of the settings: how many, Generate, and what it will be / how long it takes."""
    generate = Signal(int)
    private_changed = Signal(bool)

    def __init__(self, noun="image", more=None):
        super().__init__()
        self.setObjectName("GenBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#GenBar {{ background: {T.PANEL}; border-top: 1px solid {T.BORDER}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 12, 18, 14)
        v.setSpacing(8)
        self.count = QSpinBox()
        self.count.setRange(1, 100)
        self.count.setFixedWidth(64)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count.setToolTip(f"How many {noun}s (each with its own seed)")
        self.count.setPrefix("× ")
        self.btn = QPushButton("  Generate")
        self.btn.setObjectName("Accent")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setMinimumHeight(42)
        self.btn.setIcon(icons.icon("sparkle", T.on_accent().name(), 18))
        self.btn.setIconSize(QSize(18, 18))
        self.btn.setStyleSheet("QPushButton#Accent { font-size: 14px; border-radius: 11px; }")
        self.btn.setToolTip("Add to the queue (Ctrl+Enter)")
        self.btn.clicked.connect(lambda: self.generate.emit(int(self.count.value())))
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(self.count)
        h.addWidget(self.btn, 1)
        self.lock = QPushButton()
        self.lock.setObjectName("Icon")
        self.lock.setCheckable(True)
        self.lock.setFixedSize(42, 42)
        self.lock.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock.setToolTip("Private: straight into the encrypted vault - nothing of it is saved unencrypted, not even "
                             "in ComfyUI's folders")
        self.lock.toggled.connect(self._private)
        h.addWidget(self.lock)
        self._private(False, emit=False)
        if more:
            mb = QPushButton()
            mb.setObjectName("Icon")
            mb.setIcon(icons.icon("layers", "#C4C4CC", 18))
            mb.setFixedSize(42, 42)
            mb.setCursor(Qt.CursorShape.PointingHandCursor)
            mb.setToolTip("More ways to generate")
            mb.setStyleSheet(f"QPushButton#Icon {{ border: 1px solid {T.BORDER_HI}; border-radius: 11px; }}"
                             f"QPushButton#Icon:hover {{ background: {T.SURFACE3}; }}")
            menu = QMenu(mb)
            for text, ic, fn in more:
                menu.addAction(icons.icon(ic, "#A1A1AA", 16), text).triggered.connect(lambda _=False, fn=fn: fn())
            mb.clicked.connect(lambda: menu.exec(mb.mapToGlobal(mb.rect().topLeft()) - QPoint(0, menu.sizeHint().height() + 6)))
            h.addWidget(mb)
        v.addLayout(h)
        self.info = label("", "Faint")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.info)

    def _private(self, on, emit=True):
        acc = T.accent().name()
        self.lock.setIcon(icons.icon("lock" if on else "unlock", acc if on else "#A1A1AA", 18))
        self.lock.setStyleSheet(f"QPushButton#Icon {{ border: 1px solid {acc if on else T.BORDER_HI}; border-radius: 11px;"
                                f" background: {T.rgba(T.accent(), 40) if on else 'transparent'}; }}"
                                f"QPushButton#Icon:hover {{ background: {T.SURFACE3}; }}")
        self.btn.setText("  Generate privately" if on else "  Generate")
        if emit:
            self.private_changed.emit(on)

    def set_private(self, on):
        quiet(self.lock, self.lock.setChecked, bool(on))
        self._private(bool(on), emit=False)

    def refresh_style(self):
        self.btn.setIcon(icons.icon("sparkle", T.on_accent().name(), 18))
        self.setStyleSheet(f"#GenBar {{ background: {T.PANEL}; border-top: 1px solid {T.BORDER}; }}")

    def flash(self):
        """A short 'added' pulse on the button."""
        old = "  Generate privately" if self.lock.isChecked() else "  Generate"
        self.btn.setText("  Added to the queue")
        self.btn.setIcon(icons.icon("check", T.on_accent().name(), 18))
        QTimer.singleShot(900, lambda: (self.btn.setText(old), self.btn.setIcon(icons.icon("sparkle", T.on_accent().name(), 18))))


# ------------------------------------------------------------------------------------------------ result pane

class _Loader(QObject):
    done = Signal(str, object, float)

    def load(self, key, path, entry=None):
        """Frames of a video file - or, with a vault entry, of its decrypted bytes (in memory only)."""
        def work():
            try:
                if entry is not None:
                    from .vault import vault
                    frames, fps = media.read_video_bytes(vault.read(entry), entry.get("w"), entry.get("h"), max_h=1080,
                                                         fps=entry.get("fps") or 16)
                else:
                    frames, fps = media.read_video(path, max_h=1080)
                qs = [QImage(f.data, f.shape[1], f.shape[0], f.strides[0], QImage.Format.Format_RGB888).copy()
                      for f in frames]
                self.done.emit(key, qs, fps)
            except Exception:
                self.done.emit(key, None, 0.0)
        threading.Thread(target=work, daemon=True).start()


class ResultPane(QWidget):
    """The big view: live preview while generating, results to look at, their actions, the filmstrip."""
    action = Signal(str, dict)         # action key, entry
    dropped = Signal(str)              # a picture file dropped onto the view

    def __init__(self, kind, history, actions, empty_title, empty_sub):
        super().__init__()
        self.kind, self.history = kind, history
        self.cur = None
        self.live_job = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(20, 12, 16, 4)
        hh.setSpacing(4)
        self.title = label("", "H3")
        self.meta = label("", "Faint")
        hh.addWidget(vcol(self.title, self.meta, spacing=1))
        hh.addStretch(1)
        self.btns = {}
        for key, text, ic, tip in actions:
            b = button(text, lambda k=key: self._act(k), "Ghost", ic, tip)
            self.btns[key] = b
            hh.addWidget(b)
        v.addWidget(head)
        self.player = Player()
        self.player.message(empty_title, empty_sub)
        if kind == "image":
            self.player.click_pauses = False
            self.player.setToolTip("Click: view big (zoom, ← →)")
            self.player.clicked.connect(lambda: self.cur and self.live_job is None and self.action.emit("view", self.cur))
        self.empty = (empty_title, empty_sub)
        v.addWidget(self.player, 1)
        self.strip = Filmstrip()
        self.strip.file_of = lambda eid: (self.history.get(eid) or {}).get("file")
        self.strip.picked.connect(self.show_entry)
        self.strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(self._menu)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(14, 2, 14, 10)
        sl.addWidget(self.strip)
        v.addWidget(sw)
        self.loader = _Loader()
        self.loader.done.connect(self._loaded)
        self._actions = actions
        self.setAcceptDrops(True)
        self.reload()
        self._enable()
        history.added.connect(self._added)
        history.removed.connect(self._removed)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(media.IMAGE_EXT)
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if f.lower().endswith(media.IMAGE_EXT):
                self.dropped.emit(f)
                return

    def reload(self):
        items = self.history.recent(self.kind, 120)
        self.strip.fill(items, self.cur["id"] if self.cur else None)
        if self.cur is None and items:
            self.show_entry(items[0]["id"], fade=False)

    def _added(self, e):
        if e.get("kind") != self.kind:
            return
        self.strip.prepend(e)
        self.show_entry(e["id"])

    def _removed(self, eid):
        self.strip.remove_id(eid)
        if self.cur and self.cur["id"] == eid:
            self.cur = None
            items = self.history.recent(self.kind, 1)
            if items:
                self.show_entry(items[0]["id"])
            else:
                self.player.message(*self.empty)
                self.title.setText("")
                self.meta.setText("")
            self._enable()

    def show_entry(self, eid, fade=True):
        e = self.history.get(eid)
        if e is None:
            return
        self.private = None
        self.cur = e
        self.title.setText(nice_name(e["file"]))
        bits = [f"{e.get('w')} × {e.get('h')}"]
        if e.get("kind") == "video":
            bits += [f"{e.get('seconds')} s", f"{int(e.get('fps', 16))} fps"]
        bits += [f"seed {e.get('seed')}"]
        if e.get("took"):
            bits.append(f"made in {e['took']:.0f}s")
        self.meta.setText("  ·  ".join(bits))
        if self.live_job is None:
            self._display(e, fade)
        self._enable()

    def _display(self, e, fade=True):
        if e["kind"] == "video":
            self.player.show_image(e.get("thumb") or "", "Loading…", fade=False)
            self.loader.load(e["id"], e["file"])
        else:
            self.player.show_image(e["file"], "", fade)

    def _loaded(self, key, frames, fps):
        if key.startswith("private:"):
            p = getattr(self, "private", None)
            if p and key == "private:" + p["id"] and frames and self.live_job is None:
                self.player.play(frames, fps, "In the vault")
            return
        if not self.cur or self.cur["id"] != key or self.live_job is not None:
            return
        if not frames:
            self.player.message("Could not read this video", self.cur["file"])
            return
        self.player.play(frames, fps, f"{len(frames)} frames · {fps:.0f} fps")

    def _enable(self):
        for k, b in self.btns.items():
            b.setEnabled(self.cur is not None)

    def _act(self, key):
        if self.cur:
            self.action.emit(key, self.cur)

    def _menu(self, pos):
        it = self.strip.itemAt(pos)
        if it is None:
            return
        e = self.history.get(it.data(Qt.ItemDataRole.UserRole))
        if e is None:
            return
        m = QMenu(self)
        for key, text, ic, tip in self._actions:
            m.addAction(icons.icon(ic, "#A1A1AA", 16), text or tip).triggered.connect(
                lambda _=False, k=key, e=e: self.action.emit(k, e))
        m.exec(self.strip.mapToGlobal(pos))

    # ---- live
    def live(self, jid, f, text):
        self.live_job = jid
        self.player.set_progress(f, text)

    def live_preview(self, qi):
        self.player.show_image(qi, "Live preview", fade=False)

    def live_end(self):
        self.live_job = None
        self.player.set_progress(None)
        if getattr(self, "private", None):
            return                          # a private result is on show: it stays
        if self.cur:
            self._display(self.cur, fade=True)
        else:
            self.player.message(*self.empty)

    # ---- private results (from the vault, only in memory)
    def show_private(self, entry):
        from .vault import vault
        if not vault.is_open() or entry.get("parked"):
            self.player.message("Saved into the vault", "Unlock the vault to see it.")
            return
        self.private = entry
        self.title.setText("Private result")
        self.meta.setText(f"{entry.get('w')} × {entry.get('h')}  ·  seed {entry.get('seed')}  ·  in the vault, "
                          "decrypted only in memory")
        if entry["kind"] == "video":
            self.loader.load("private:" + entry["id"], None, entry)
            self.player.message("Loading…")
        else:
            img = QImage()
            img.loadFromData(vault.read(entry))
            self.player.show_image(img, "In the vault")
        for b in self.btns.values():
            b.setEnabled(False)

    def forget_private(self):
        """The vault was locked: nothing decrypted stays on screen."""
        if getattr(self, "private", None) is None:
            return
        self.private = None
        self.player.message(*self.empty)
        self.title.setText("")
        self.meta.setText("")
        if self.cur and self.live_job is None:
            self.show_entry(self.cur["id"], fade=False)
