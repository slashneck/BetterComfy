"""One key combination that works everywhere in Windows, even when another program (or a game) has the focus."""
import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QKeyCombination, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLineEdit

_ID = 0xBC01
_WM_HOTKEY = 0x0312
_NOREPEAT = 0x4000


def _vk(key):
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return key
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F24.value:
        return 0x70 + key - Qt.Key.Key_F1.value
    return {Qt.Key.Key_Space.value: 0x20, Qt.Key.Key_Pause.value: 0x13, Qt.Key.Key_ScrollLock.value: 0x91,
            Qt.Key.Key_Insert.value: 0x2D, Qt.Key.Key_Delete.value: 0x2E, Qt.Key.Key_Home.value: 0x24,
            Qt.Key.Key_End.value: 0x23, Qt.Key.Key_PageUp.value: 0x21, Qt.Key.Key_PageDown.value: 0x22,
            Qt.Key.Key_Escape.value: 0x1B}.get(key)


def _numpad(key):
    if Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return 0x60 + key - Qt.Key.Key_0.value
    return {Qt.Key.Key_Asterisk.value: 0x6A, Qt.Key.Key_Plus.value: 0x6B, Qt.Key.Key_Minus.value: 0x6D,
            Qt.Key.Key_Period.value: 0x6E, Qt.Key.Key_Slash.value: 0x6F}.get(key)


def parse(text):
    """(modifiers, virtual key) of a key combination, or None. Alone only F keys, numpad keys, Pause and Scroll Lock are
    allowed: a plain letter would stop working in every other program."""
    seq = QKeySequence(text or "")
    if seq.isEmpty():
        return None
    combo = seq[0]
    mods, m = 0, combo.keyboardModifiers()
    for flag, bit in ((Qt.KeyboardModifier.AltModifier, 1), (Qt.KeyboardModifier.ControlModifier, 2),
                      (Qt.KeyboardModifier.ShiftModifier, 4), (Qt.KeyboardModifier.MetaModifier, 8)):
        if m & flag:
            mods |= bit
    pad = bool(m & Qt.KeyboardModifier.KeypadModifier)
    vk = _numpad(combo.key().value) if pad else _vk(combo.key().value)
    if vk is None or not (mods or pad or 0x70 <= vk <= 0x87 or vk in (0x13, 0x91)):
        return None
    return mods, vk


class KeyCatcher(QLineEdit):
    """Click it and press the keys: F keys, numpad keys and combinations all count. Esc leaves it as it was."""
    caught = Signal(str)
    _SKIP = (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_unknown)

    def __init__(self, text=""):
        super().__init__(text)
        self.setReadOnly(True)
        self.setPlaceholderText("Click here, then press the key")

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:        # F1, Ctrl+K and the like are for this field while it has focus
            e.accept()
            return True
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() in self._SKIP:
            return
        if e.key() == Qt.Key.Key_Escape:
            self.clearFocus()
            return
        keep = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier |
                Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.KeypadModifier)
        seq = QKeySequence(QKeyCombination(e.modifiers() & keep, Qt.Key(e.key())))
        text = seq.toString(QKeySequence.SequenceFormat.PortableText)
        self.setText(text)
        self.clearFocus()
        self.caught.emit(text)


class GlobalKey(QAbstractNativeEventFilter):
    def __init__(self, app, on_press):
        super().__init__()
        self.on_press = on_press
        self.bound = False
        app.installNativeEventFilter(self)

    def bind(self, text):
        """True: it works (or there is none to set). False: not usable, or another program holds it."""
        self.clear()
        if not text:
            return True
        keys = parse(text)
        if keys is None or sys.platform != "win32":
            return False
        self.bound = bool(ctypes.windll.user32.RegisterHotKey(None, _ID, keys[0] | _NOREPEAT, keys[1]))
        return self.bound

    def clear(self):
        if self.bound and sys.platform == "win32":
            ctypes.windll.user32.UnregisterHotKey(None, _ID)
        self.bound = False

    def nativeEventFilter(self, event_type, message):
        name = bytes(event_type).decode("ascii", "ignore") if not isinstance(event_type, str) else event_type
        # a hotkey with no window of its own arrives as a message of the thread: the "dispatcher" kind
        if name in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and msg.wParam == _ID:
                self.on_press()
                return True, 0
        return False, 0
