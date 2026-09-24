"""Line icons as inline SVG, drawn in whatever color the theme asks for."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def _stroke(width: float = 2) -> str:
    return f'fill="none" stroke="{{c}}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'


_S = _stroke()

_SVGS = {
    "chevron_down": f'<g {_S}><path d="M7 10l5 5 5-5"/></g>',
    "chevron_left": f'<g {_S}><path d="M14.5 6.5L9 12l5.5 5.5"/></g>',
    "chevron_right": f'<g {_S}><path d="M9.5 6.5L15 12l-5.5 5.5"/></g>',
    "check": f'<g {_stroke(3)}><path d="M5.5 12.5l4.2 4.2L18.5 7.5"/></g>',
    "check_circle": f'<g {_stroke(2)}><circle cx="12" cy="12" r="8.5"/><path d="M8.3 12.3l2.6 2.6 4.9-5.2"/></g>',
    "lock": f'<g {_S}><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/></g>',
    "gear": f'<g {_stroke(1.8)}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 '
            '2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 '
            '0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 '
            '1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 '
            '1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 '
            '0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></g>',
    "help": f'<g {_stroke(1.8)}><circle cx="12" cy="12" r="9"/><path d="M9.4 9.3a2.7 2.7 0 0 1 5.2.9c0 1.8-2.6 2.2-2.6 '
            '3.9"/><path d="M12 17.2v.1"/></g>',
    "note": f'<g {_stroke(1.7)}><path d="M5 4.5h14v11l-4.5 4.5H5z"/><path d="M14.5 20v-4.5H19"/><path d="M8.5 9h7M8.5 '
            '12.5h4.5"/></g>',
    "plus": f'<g {_S}><path d="M12 5.5v13M5.5 12h13"/></g>',
    "fill": f'<g {_stroke(1.8)}><path d="M4 7h10M4 12h7M4 17h5"/><path d="M17 9v10M13.5 15.5L17 19l3.5-3.5"/></g>',
    "export": f'<g {_stroke(1.8)}><path d="M12 4v11M7.5 8.5L12 4l4.5 4.5"/><path d="M5 14v5h14v-5"/></g>',
    "logo": '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c83ff"/>'
            '<stop offset="1" stop-color="#4338ca"/></linearGradient></defs>'
            '<rect width="24" height="24" rx="6" fill="url(#g)"/>'
            '<path d="M7.5 5.5h9M7.5 18.5h9" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
            '<path d="M8.6 5.5c0 3.4 3.4 4.3 3.4 6.5s-3.4 3.1-3.4 6.5M15.4 5.5c0 3.4-3.4 4.3-3.4 6.5s3.4 3.1 3.4 6.5" '
            'fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>'
            '<path d="M10 17.4c.6-1.3 1.3-1.9 2-1.9s1.4.6 2 1.9z" fill="#fff"/>',
}


def svg(name: str, color: str = "#000000") -> str:
    body = _SVGS[name].replace("{c}", color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int, dpr: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    px = round(size * dpr)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 16) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(pixmap(name, color, size, dpr))
    return ic
