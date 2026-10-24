"""Векторные иконки (SVG), которые перекрашиваются в любой цвет."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
    'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)
_FILL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="{c}" '
    'stroke-width="1.5" stroke-linejoin="round">{body}</svg>'
)

ICONS: dict[str, tuple[str, str]] = {
    "grid": ("stroke", '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/>'
                       '<rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>'),
    "search": ("stroke", '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>'),
    "settings": ("stroke", '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
                           '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>'),
    "stats": ("stroke", '<path d="M3.5 20.5h17"/><rect x="5" y="11" width="3.2" height="6.5" rx="1"/>'
                        '<rect x="10.4" y="5.5" width="3.2" height="12" rx="1"/><rect x="15.8" y="13.5" width="3.2" height="4" rx="1"/>'),
    "play": ("fill", '<path d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.6-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8 0 0 0 8 5.2z"/>'),
    "check": ("stroke", '<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    "bookmark": ("stroke", '<path d="M7 3.5h10a1 1 0 0 1 1 1v16l-6-4-6 4v-16a1 1 0 0 1 1-1z"/>'),
    "pause": ("stroke", '<path d="M8.5 5.5v13M15.5 5.5v13"/>'),
    "x": ("stroke", '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>'),
    "plus": ("stroke", '<path d="M12 5v14M5 12h14"/>'),
    "trash": ("stroke", '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9 7V4.5h6V7"/>'),
    "star": ("fill", '<path d="M12 3.2l2.6 5.5 6 .8-4.4 4.2 1.1 6-5.3-2.9-5.3 2.9 1.1-6L3.4 9.5l6-.8z"/>'),
    "gamepad": ("stroke", '<path d="M7.5 7h9a5 5 0 0 1 4.9 6l-.6 3a2.6 2.6 0 0 1-4.5 1.2L14.5 15h-5l-1.8 2.2A2.6 2.6 0 0 1 3.2 16l-.6-3A5 5 0 0 1 7.5 7z"/>'
                          '<path d="M7.5 10v3M6 11.5h3"/><circle cx="15.5" cy="10.5" r=".6"/><circle cx="17.5" cy="12.5" r=".6"/>'),
    "clock": ("stroke", '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    "calendar": ("stroke", '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>'),
    "external": ("stroke", '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
    "eye": ("stroke", '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>'),
    "folder": ("stroke", '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2.2h7.2a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
    "alert": ("stroke", '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.8v5M12 16.2v.1"/>'),
    "key": ("stroke", '<circle cx="8" cy="15" r="4"/><path d="M11 12l8.5-8.5M16.5 6.5l2.5 2.5M14.5 8.5l2 2"/>'),
    "flame": ("stroke", '<path d="M12 3c.8 3.6 5 5.4 5 10.2a5 5 0 0 1-10 0c0-2.2 1-3.7 2.2-4.8.1 1.9 1 2.9 2 3.1-.4-3 .1-5.7.8-8.5z"/>'),
    "trophy": ("stroke", '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H4.5v1.5A3 3 0 0 0 8 10.5M16 6h3.5v1.5a3 3 0 0 1-3.5 3M12 13v4M8.5 20.5h7M9.5 17h5"/>'),
    "chevron-down": ("stroke", '<path d="m6 9 6 6 6-6"/>'),
    "chevron-up": ("stroke", '<path d="m6 15 6-6 6 6"/>'),
    "unlink": ("stroke", '<path d="M9.5 14.5l-2 2a3.2 3.2 0 0 1-4.5-4.5l2-2M14.5 9.5l2-2a3.2 3.2 0 0 1 4.5 4.5l-2 2'
                         'M4.5 4.5l2 2M17.5 17.5l2 2M10 3.5v2M3.5 10h2M14 20.5v-2M20.5 14h-2"/>'),
    "thumbs-up": ("stroke", '<path d="M7.5 10.5V20H4.5a1 1 0 0 1-1-1v-7.5a1 1 0 0 1 1-1zM7.5 10.5l3.6-6.6a2.2 2.2 0 0 1 '
                            '2.9 2.2l-.4 3.4h5.1a2 2 0 0 1 2 2.4l-1.3 6.4A2 2 0 0 1 17.4 20H7.5"/>'),
    "globe": ("stroke", '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.4 5.2 3.4 8.5s-1 6.1-3.4 '
                        '8.5c-2.4-2.4-3.4-5.2-3.4-8.5s1-6.1 3.4-8.5z"/>'),
    "monitor": ("stroke", '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
    "sparkles": ("stroke", '<path d="M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
}

_cache: dict[tuple, QPixmap] = {}


def svg_markup(name: str, color: str, stroke_width: float = 2.0) -> str:
    kind, body = ICONS[name]
    template = _STROKE if kind == "stroke" else _FILL
    return template.format(c=color, w=stroke_width, body=body)


def _dpr() -> float:
    app = QGuiApplication.instance()
    return app.devicePixelRatio() if app else 1.0


def pixmap(name: str, color: str, size: int = 20, dpr: float | None = None) -> QPixmap:
    dpr = dpr or _dpr()
    key = (name, color, size, dpr)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg_markup(name, color).encode("utf-8")))
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


def logo_pixmap(size: int, dpr: float | None = None) -> QPixmap:
    """Логотип: фиолетовый градиентный квадрат с геймпадом."""
    dpr = dpr or _dpr()
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#7c3aed"))
    gradient.setColorAt(1, QColor("#d946ef"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    inner = round(size * 0.64)
    painter.drawPixmap(round((size - inner) / 2), round((size - inner) / 2), pixmap("gamepad", "#ffffff", inner, dpr))
    painter.end()
    return pm


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(logo_pixmap(size, 1.0))
    return result
