"""App icon: the crowned orchestrator critter, rendered from the sprite art.

Generated at runtime so it always matches the in-app pixel style — no binary
asset to ship. Multiple sizes so the dock/taskbar and title bars all get a
crisp version.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

from .sprites import SPRITE_H, SPRITE_W, draw_critter, look_for

_BG = QColor("#1d2026")
_ACCENT = QColor("#e5c07b")


def _render(size: int) -> QPixmap:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)

    # rounded dark tile
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(_BG)
    painter.setPen(QColor(0, 0, 0, 0))
    radius = max(2, size // 6)
    painter.drawRoundedRect(QRect(0, 0, size, size), radius, radius)
    painter.setRenderHint(QPainter.Antialiasing, False)

    # crowned orchestrator, centered (sprite is 12x11 + 2px crown headroom)
    scale = max(1, size // (SPRITE_W + 4))
    x = (size - SPRITE_W * scale) // 2
    y = (size - SPRITE_H * scale) // 2 + scale
    species, accent = look_for("orchestrator")
    draw_critter(painter, x, y, scale, species, _ACCENT,
                 state="done", crowned=True, accent=accent)
    painter.end()
    return QPixmap.fromImage(image)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256, 512):
        icon.addPixmap(_render(size))
    return icon
