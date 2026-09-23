"""Line icons drawn from inline SVG, tinted with the current theme color.

Inline SVG keeps the chrome crisp on HiDPI screens without shipping image files.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS: dict[str, str] = {
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "forward": '<path d="M9 5l7 7-7 7"/>',
    "reload": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "stop": '<path d="M6 6l12 12M18 6L6 18"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M7 7l10 10M17 7L7 17"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    "menu": ''.join(f'<circle cx="12" cy="{y}" r="1.5" fill="FILL" stroke="none"/>' for y in (5.5, 12, 18.5)),
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
    "lilx": '<path d="M8 4v12a4 4 0 0 0 4 4h4"/>',
    "puzzle": '<path d="M9 4.5a2 2 0 0 1 4 0V6h3.5a1 1 0 0 1 1 1v3.5H19a2 2 0 0 1 0 4h-1.5V18a1 1 0 0 1-1 1H13v-1.5a2 2 0 0 0-4 0V19H5.5a1 1 0 0 1-1-1v-3.5H6a2 2 0 0 0 0-4H4.5V7a1 1 0 0 1 1-1H9z"/>',
    "star": '<path d="M12 3.6l2.6 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.2 2.7 1-5.7-4.1-4 5.7-.8z"/>',
    "star-filled": '<path fill="FILL" d="M12 3.6l2.6 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.2 2.7 1-5.7-4.1-4 5.7-.8z"/>',
    "shield": '<path d="M12 3.5l7 2.8v5.2c0 4.3-2.9 7.7-7 9-4.1-1.3-7-4.7-7-9V6.3z"/><path d="M9 12l2.2 2.2L15.2 10"/>',
    "shield-off": '<path d="M12 3.5l7 2.8v5.2c0 4.3-2.9 7.7-7 9-4.1-1.3-7-4.7-7-9V6.3z"/><path d="M5 5l14 14"/>',
}


def _svg(name: str, color: str, stroke: float) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>'
    ).replace("FILL", color).encode()


@lru_cache(maxsize=128)
def icon(name: str, color: str, size: int = 18, stroke: float = 1.8) -> QIcon:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(QSize(size * scale, size * scale))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result
