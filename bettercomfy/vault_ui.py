"""Opening, creating and recovering the vault."""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout

from . import theme as T
from .vault import VaultError, WrongPassword, vault
from .widgets import ToggleRow, button, hrow, label


def strength(pw):
    """0..4 - a rough guess of how hard a password is to guess."""
    if len(pw) < 8:
        return 0
    kinds = sum(bool(any(f(c) for c in pw)) for f in (str.islower, str.isupper, str.isdigit,
                                                      lambda c: not c.isalnum()))
    score = 1 + (len(pw) >= 12) + (len(pw) >= 16) + (kinds >= 3)
    return min(4, score)


class _Busy(QDialog):
    """Base: a dialog that runs the slow key work in the background and shows the result."""
    _done = Signal(object)

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(480, 10)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(24, 22, 24, 20)
        self.v.setSpacing(10)
        self.error = label("", None, wrap=True)
        self.error.setStyleSheet(f"color: {T.BAD};")
        self.error.hide()
        self._done.connect(self._finished)

    def _pw(self, placeholder):
        e = QLineEdit()
        e.setEchoMode(QLineEdit.EchoMode.Password)
        e.setPlaceholderText(placeholder)
        e.setMinimumHeight(36)
        return e

    def _run(self, fn):
        self.error.hide()
        self.setEnabled(False)

        def work():
            try:
                self._done.emit(fn())
            except Exception as ex:                     # noqa: BLE001 - shown to the user
                self._done.emit(ex)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, res):
        self.setEnabled(True)
        if isinstance(res, Exception):
            self.error.setText(str(res))
            self.error.show()
            self.adjustSize()
            return
        self.result_value = res
        self.accept()


class UnlockDialog(_Busy):
    def __init__(self, parent, why=""):
        super().__init__(parent, "Vault")
        self.v.addWidget(label("Unlock the vault", "H2"))
        if why:
            self.v.addWidget(label(why, "Muted", wrap=True))
        self.pw = self._pw("Vault password")
        self.pw.returnPressed.connect(self._go)
        self.v.addWidget(self.pw)
        self.v.addWidget(self.error)
        self.v.addWidget(hrow(button("Use the recovery key", self._recover, "Ghost"), None,
                              button("Cancel", self.reject, "Ghost"), button("Unlock", self._go, "Accent", "unlock")))
        self.pw.setFocus()

    def _go(self):
        pw = self.pw.text()
        if pw:
            self._run(lambda: vault.unlock(pw))

    def _finished(self, res):
        if isinstance(res, WrongPassword):
            self.pw.selectAll()
        super()._finished(res)

    def _recover(self):
        d = RecoverDialog(self.parent())
        if d.exec():
            self.accept()


class RecoverDialog(_Busy):
    def __init__(self, parent):
        super().__init__(parent, "Vault")
        self.v.addWidget(label("Open with the recovery key", "H2"))
        self.v.addWidget(label("The key you got when the vault was made (8 groups of 4). Then pick a new password.",
                               "Muted", wrap=True))
        self.rk = QLineEdit()
        self.rk.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        self.rk.setMinimumHeight(36)
        self.p1 = self._pw("New password (at least 8 characters)")
        self.p2 = self._pw("New password again")
        for w in (self.rk, self.p1, self.p2):
            self.v.addWidget(w)
        self.v.addWidget(self.error)
        self.v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Open", self._go, "Accent", "unlock")))

    def _go(self):
        if self.p1.text() != self.p2.text():
            self.error.setText("The two passwords are not the same.")
            self.error.show()
            return
        rk, pw = self.rk.text(), self.p1.text()
        self._run(lambda: vault.unlock_with_recovery(rk, pw))


class CreateDialog(_Busy):
    def __init__(self, parent):
        super().__init__(parent, "Vault")
        self.v.addWidget(label("Make your vault", "H2"))
        self.v.addWidget(label("Pictures and videos in the vault are encrypted (AES-256) and only shown inside "
                               "Better Comfy after you unlock it. Without the password or the recovery key nobody can "
                               "open them - not even you, so keep both safe.", "Muted", wrap=True))
        self.p1 = self._pw("Password (at least 8 characters)")
        self.p2 = self._pw("Password again")
        self.meter = label("", "Faint")
        self.p1.textChanged.connect(self._meter)
        self.p2.returnPressed.connect(self._go)
        for w in (self.p1, self.p2, self.meter):
            self.v.addWidget(w)
        self.v.addWidget(self.error)
        self.v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Make the vault", self._go, "Accent",
                                                                                     "lock")))
        self._meter("")

    def _meter(self, t):
        s = strength(t)
        names = ["Too short", "Weak", "Okay", "Good", "Strong"]
        cols = [T.BAD, T.BAD, T.WARN, T.GOOD, T.GOOD]
        self.meter.setText(("●" * (s + 1)).ljust(5, "○") + "   " + names[s] if t else "A long sentence is easy to "
                                                                                    "remember and hard to guess.")
        self.meter.setStyleSheet(f"color: {cols[s] if t else T.TEXT3}; font-size: 12px;")

    def _go(self):
        if self.p1.text() != self.p2.text():
            self.error.setText("The two passwords are not the same.")
            self.error.show()
            return
        pw = self.p1.text()
        self._run(lambda: vault.create(pw))


class RecoveryKeyDialog(QDialog):
    """Shows the recovery key once."""

    def __init__(self, parent, key):
        super().__init__(parent)
        self.setWindowTitle("Vault")
        self.setModal(True)
        self.resize(520, 10)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(12)
        v.addWidget(label("Your recovery key", "H2"))
        v.addWidget(label("If you ever forget your password, this key is the only other way into the vault. It is shown "
                          "this one time - write it down and keep it somewhere safe, away from this PC.", "Muted",
                          wrap=True))
        k = label(key, None, sel=True)
        k.setAlignment(Qt.AlignmentFlag.AlignCenter)
        k.setStyleSheet(f"font-family: 'Cascadia Mono', Consolas, monospace; font-size: 18px; font-weight: 600; "
                        f"background: {T.FIELD}; border: 1px solid {T.BORDER_HI}; border-radius: 10px; padding: 14px;")
        v.addWidget(k)
        self.key = key
        copy = button("Copy", self._copy, "Ghost", "copy")
        self.ok = ToggleRow("I wrote it down", None, False, lambda on: self.done_btn.setEnabled(on))
        self.done_btn = button("Done", self.accept, "Accent", "check")
        self.done_btn.setEnabled(False)
        v.addWidget(hrow(copy, None))
        v.addWidget(self.ok)
        v.addWidget(hrow(None, self.done_btn))

    def _copy(self):
        from .system import copy_private
        copy_private(text=self.key)

    def reject(self):
        if self.ok.isChecked():
            self.accept()

    def accept(self):
        cb = QApplication.clipboard()
        if cb.text() == self.key:
            cb.clear()                  # the key doesn't stay on the clipboard
        super().accept()


class ChangePasswordDialog(_Busy):
    """A new password - and, if wanted, everything re-encrypted with a new key (then a new recovery key too).
    result_value: the new recovery key, or None."""
    _step = Signal(int, int)

    def __init__(self, parent):
        super().__init__(parent, "Vault")
        self.v.addWidget(label("Change the vault password", "H2"))
        self.old = self._pw("Current password")
        self.p1 = self._pw("New password (at least 8 characters)")
        self.p2 = self._pw("New password again")
        for w in (self.old, self.p1, self.p2):
            self.v.addWidget(w)
        self.rekey = ToggleRow("Re-encrypt everything with a new key",
                               "Slower: every file is rewritten. A copy of the vault made before can then not be "
                               "opened with the old password or recovery key any more. You get a new recovery key.",
                               False)
        self.v.addWidget(self.rekey)
        self.status = label("", "Faint")
        self.status.hide()
        self.v.addWidget(self.status)
        self.v.addWidget(self.error)
        self.v.addWidget(hrow(None, button("Cancel", self.reject, "Ghost"), button("Change", self._go, "Accent")))
        self._step.connect(lambda d, t: (self.status.show(), self.status.setText(f"Re-encrypting {d} of {t}…")))

    def _go(self):
        if self.p1.text() != self.p2.text():
            self.error.setText("The two new passwords are not the same.")
            self.error.show()
            return
        old, new, rekey = self.old.text(), self.p1.text(), self.rekey.isChecked()

        def work():
            # the current password has to be right, even while the vault is open
            from .vault import Vault
            Vault().unlock(old)
            if rekey:
                return vault.rekey(new, lambda d, t: self._step.emit(d, t))
            vault.set_password(new)
            return None
        self._run(work)


def ensure_open(parent, why=""):
    """The vault open - made first when there is none (with its recovery key), else unlocked. True when open."""
    if vault.is_open():
        return True
    if not vault.exists():
        d = CreateDialog(parent)
        if not d.exec():
            return False
        RecoveryKeyDialog(parent, d.result_value).exec()
        return vault.is_open()
    d = UnlockDialog(parent, why)
    return bool(d.exec()) and vault.is_open()


__all__ = ["ensure_open", "ChangePasswordDialog", "VaultError"]
