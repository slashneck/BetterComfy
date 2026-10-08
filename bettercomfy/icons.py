"""Line icons drawn from small SVGs (24 x 24, stroke = the colour asked for)."""
from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "image": '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/>'
             '<path d="M4.5 18.5l5-5 4 4 2.5-2.5 4 4"/>',
    "video": '<rect x="2.5" y="5" width="13.5" height="14" rx="3"/><path d="M16 10.2l5.5-3.2v10l-5.5-3.2z"/>',
    "queue": '<path d="M4 6h16M4 11h16M4 16h9"/><path d="M16.5 14.5l4.5 2.75-4.5 2.75z" fill="currentColor"/>',
    "gallery": '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/>'
               '<rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
    "lora": '<path d="M12 3l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.2l9 4.8 9-4.8"/><path d="M3 16.4l9 4.8 9-4.8"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "play": '<path d="M7.5 4.8v14.4L19.5 12z" fill="currentColor"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/>'
             '<rect x="14" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" stroke="none"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13M10 11v5M14 11v5"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20.5 4v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "swap": '<path d="M7.5 4v16M4.5 7l3-3 3 3M16.5 20V4M13.5 17l3 3 3-3"/>',
    "dice": '<rect x="4" y="4" width="16" height="16" rx="3.5"/><circle cx="9" cy="9" r="1.3" fill="currentColor"/>'
            '<circle cx="15" cy="15" r="1.3" fill="currentColor"/><circle cx="15" cy="9" r="1.3" fill="currentColor"/>'
            '<circle cx="9" cy="15" r="1.3" fill="currentColor"/>',
    "lock": '<rect x="5" y="11" width="14" height="9.5" rx="2.5"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',
    "unlock": '<rect x="5" y="11" width="14" height="9.5" rx="2.5"/><path d="M8.5 11V8a3.5 3.5 0 0 1 6.8-1.2"/>',
    "star": '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "starf": '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" fill="currentColor"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "upload": '<path d="M12 15.5V4M7 8.5L12 4l5 4.5M4 15v2.5A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5V15"/>',
    "download": '<path d="M12 4v11.5M7 11l5 4.5 5-4.5M4 20h16"/>',
    "sparkle": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'
               '<path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "loop": '<path d="M17 2.5l3 3-3 3M4 11V9.5a4 4 0 0 1 4-4h12M7 21.5l-3-3 3-3M20 13v1.5a4 4 0 0 1-4 4H4"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "power": '<path d="M12 3v8.5M6.3 6.6a8 8 0 1 0 11.4 0"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "back": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.4"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "chevron_r": '<path d="M9 6l6 6-6 6"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7.5 4v16M16.5 4v16M3 9h4.5M3 15h4.5M16.5 9H21M16.5 15H21"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2.5"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>',
    "wand": '<path d="M4 20L14.5 9.5M13 6.5l4.5 4.5"/><path d="M17 3v3M15.5 4.5h3M20 8v2M19 9h2" stroke-width="1.5"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "tag": '<path d="M3.5 12.5V4.5a1 1 0 0 1 1-1h8l8 8-9 9z"/><circle cx="8.5" cy="8.5" r="1.5"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2.5"/><path d="M8 20h8M12 16v4"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.4 0 2-1 2-2 0-1.4-1-1.8-1-3 0-1.1.9-2 2-2h2a4 4 0 0 0 4-4c0-4-4-7-9-7z"/>'
               '<circle cx="7.5" cy="11" r="1.2" fill="currentColor"/><circle cx="10" cy="7" r="1.2" fill="currentColor"/>'
               '<circle cx="15" cy="7.5" r="1.2" fill="currentColor"/>',
    "layers": '<path d="M12 3l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.2l9 4.8 9-4.8"/>',
    "expand": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "dot": '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
    "bolt": '<path d="M13 2.5L5 13.5h6l-1 8 8-11h-6z"/>',
    "gem": '<path d="M6.5 4h11l3.5 5-9 11L3 9z"/><path d="M3 9h18M9.5 4L8 9l4 11 4-11-1.5-5"/>',
    "scale": '<path d="M4 4h7v7H4z"/><path d="M13 20h7v-7M20 4l-9 9M15 4h5v5"/>',
    "shortcut": '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><path d="M9 15l6-6M10 9h5v5"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "grip": '<circle cx="9" cy="6" r="1.2" fill="currentColor"/><circle cx="15" cy="6" r="1.2" fill="currentColor"/>'
            '<circle cx="9" cy="12" r="1.2" fill="currentColor"/><circle cx="15" cy="12" r="1.2" fill="currentColor"/>'
            '<circle cx="9" cy="18" r="1.2" fill="currentColor"/><circle cx="15" cy="18" r="1.2" fill="currentColor"/>',
}

_cache = {}


def svg(name, color="#F4F4F5", width=1.8):
    body = _P.get(name, _P["dot"]).replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def pixmap(name, size=18, color="#F4F4F5", dpr=2.0, width=1.8):
    color = QColor(color).name(QColor.NameFormat.HexArgb) if not isinstance(color, str) else color
    key = (name, size, color, dpr, width)
    if key not in _cache:
        r = QSvgRenderer(QByteArray(svg(name, QColor(color).name(), width).encode()))
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if QColor(color).alpha() < 255:
            p.setOpacity(QColor(color).alphaF())
        r.render(p, QRectF(0, 0, size * dpr, size * dpr))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return _cache[key]


def icon(name, color="#D4D4D8", size=18, active=None):
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, size, "#4A4A50"), QIcon.Mode.Disabled)
    if active:
        ic.addPixmap(pixmap(name, size, active), QIcon.Mode.Normal, QIcon.State.On)
    return ic


def size(n=18):
    return QSize(n, n)
