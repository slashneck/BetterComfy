"""Colours and the stylesheet. Black and white, one accent colour (changeable in Settings)."""
from PySide6.QtGui import QColor

from .config import resource

ACCENTS = [
    ("Mono", "#F4F4F5"),
    ("Iris", "#8B7CF6"),
    ("Azure", "#4F8CFF"),
    ("Cyan", "#22C3D6"),
    ("Mint", "#34D399"),
    ("Lime", "#A3E635"),
    ("Amber", "#F5B041"),
    ("Coral", "#FF7A59"),
    ("Rose", "#F472B6"),
    ("Crimson", "#EF4444"),
]

BG = "#09090A"
PANEL = "#0E0E10"
SURFACE = "#131315"
SURFACE2 = "#1A1A1D"
SURFACE3 = "#222226"
FIELD = "#0C0C0E"
BORDER = "#222226"
BORDER_HI = "#2E2E33"
TEXT = "#F4F4F5"
TEXT2 = "#A1A1AA"
TEXT3 = "#62626B"
GOOD = "#34D399"
WARN = "#F5B041"
BAD = "#EF4444"

_accent = QColor("#8B7CF6")


def accent():
    return QColor(_accent)


def set_accent(hex_):
    global _accent
    c = QColor(hex_)
    if c.isValid():
        _accent = c


def on_accent(c=None):
    """Black or white text, whichever reads better on the accent."""
    c = QColor(c or _accent)
    lum = 0.2126 * c.redF() ** 2.2 + 0.7152 * c.greenF() ** 2.2 + 0.0722 * c.blueF() ** 2.2
    return QColor("#0A0A0B") if lum > 0.28 else QColor("#FFFFFF")


def mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def rgba(c, alpha):
    c = QColor(c)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def qss():
    a = _accent.name()
    a_hi = mix(_accent, "#FFFFFF", 0.18).name()
    a_lo = mix(_accent, "#000000", 0.15).name()
    on = on_accent().name()
    arrow = resource("assets", "arrow.png").replace("\\", "/")
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, #Root {{ background: {BG}; }}
QDialog {{ background: {PANEL}; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER_HI}; padding: 6px 9px; border-radius: 6px; }}

#Main {{ background: {BG}; }}
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#Card[flat="true"] {{ background: transparent; border: none; }}
#Panel {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
#PanelR {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}
#ScrollBody {{ background: transparent; }}

QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 21px; font-weight: 600; letter-spacing: -0.2px; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#H3 {{ font-size: 13px; font-weight: 600; }}
QLabel#CardTitle {{ font-size: 11px; font-weight: 700; color: {TEXT3}; letter-spacing: 1.2px; }}
QLabel#Muted {{ color: {TEXT2}; }}
QLabel#Faint {{ color: {TEXT3}; font-size: 12px; }}
QLabel#Mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {TEXT2}; font-size: 12px; }}
QLabel#Badge {{ background: {SURFACE3}; color: {TEXT2}; border-radius: 6px; padding: 2px 7px; font-size: 11px; font-weight: 600; }}
QLabel#AccentBadge {{ background: {rgba(_accent, 40)}; color: {a}; border-radius: 6px; padding: 2px 7px; font-size: 11px; font-weight: 600; }}
QLabel#Link {{ color: {a}; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER_HI}; border-radius: 9px; padding: 7px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {SURFACE3}; border-color: #3C3C42; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {TEXT3}; border-color: {BORDER}; background: {SURFACE}; }}
QPushButton:checked {{ background: {rgba(_accent, 38)}; border-color: {a}; color: {TEXT}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT2}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ background: {SURFACE2}; color: {TEXT}; }}
QPushButton#Ghost:disabled {{ color: #3A3A40; }}
QPushButton#Icon {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
QPushButton#Icon:hover {{ background: {SURFACE3}; }}
QPushButton#Icon:checked {{ background: {rgba(_accent, 38)}; }}
QPushButton#Accent {{ background: {a}; color: {on}; border: none; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {a_hi}; }}
QPushButton#Accent:pressed {{ background: {a_lo}; }}
QPushButton#Accent:disabled {{ background: {SURFACE3}; color: {TEXT3}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {rgba(BAD, 90)}; color: #FF8A8A; }}
QPushButton#Danger:hover {{ background: {rgba(BAD, 30)}; }}
QPushButton#Chip {{ background: transparent; border: 1px solid {BORDER_HI}; border-radius: 12px; padding: 3px 10px; color: {TEXT2}; font-size: 12px; }}
QPushButton#Chip:hover {{ border-color: {TEXT3}; color: {TEXT}; }}
QPushButton#Chip:checked {{ background: {rgba(_accent, 40)}; border-color: {a}; color: {TEXT}; }}
QPushButton#Swatch {{ border-radius: 14px; padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {FIELD}; border: 1px solid {BORDER_HI}; border-radius: 9px; padding: 6px 10px;
    selection-background-color: {a}; selection-color: {on}; }}
QPlainTextEdit, QTextEdit {{ padding: 8px 10px; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: #3C3C42; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {a}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {TEXT3}; border-color: {BORDER}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 6px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 4px;
    selection-background-color: {SURFACE3}; selection-color: {TEXT}; outline: 0; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: 5px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 4px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: #45454C; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 4px; min-width: 36px; margin: 2px 0; }}
QScrollBar::handle:horizontal:hover {{ background: #45454C; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSlider {{ min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER_HI}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::sub-page:horizontal:disabled {{ background: {TEXT3}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER_HI}; background: {FIELD}; }}
QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid {BORDER_HI}; background: {FIELD}; }}
QRadioButton::indicator:checked {{ background: {a}; border: 1px solid {a}; }}

QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER_HI}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE3}; }}
QMenu::item:disabled {{ color: {TEXT3}; }}
QMenu::separator {{ height: 1px; background: {BORDER_HI}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QListWidget, QListView, QTreeView {{ background: transparent; border: none; }}
QListWidget::item, QListView::item {{ border-radius: 10px; color: {TEXT2}; }}
QListWidget::item:hover, QListView::item:hover {{ background: {SURFACE2}; }}
QListWidget::item:selected, QListView::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}

QProgressBar {{ background: {SURFACE3}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {a}; border-radius: 2px; }}

QTabBar::tab {{ background: transparent; color: {TEXT2}; padding: 8px 14px; border: none; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {a}; }}
QColorDialog {{ background: {PANEL}; }}
QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""
