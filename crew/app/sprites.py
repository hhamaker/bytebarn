"""Pixel-art chibi critters for the crew stage (spec §7.2).

12x11 logical-pixel sprites drawn at integer scale. Species chosen by a
stable hash of the agent name; body pixels tinted with the agent color.

Grid legend: '.' transparent, 'B' body (tinted), 'O' outline, 'W' white belly.
Eyes and mouth are drawn programmatically so animation states (blink,
worried, happy, sleeping) don't need separate grids.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter

SPRITE_W = 12
SPRITE_H = 11

_CAT = [
    ".O........O.",
    ".OO......OO.",
    ".OBOOOOOOBO.",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBWWWWWWBBO",
    ".OBBWWWWBBO.",
    ".OBBBBBBBBO.",
    "..OOOOOOOO..",
]

_DOG = [
    "............",
    ".OOO....OOO.",
    "OBBBOOOOBBBO",
    "OBOBBBBBBOBO",
    "OBOBBBBBBOBO",
    ".OBBBBBBBBO.",
    ".OBBBBBBBBO.",
    ".OBWWWWWWBO.",
    ".OBBWWWWBBO.",
    ".OBBBBBBBBO.",
    "..OOOOOOOO..",
]

_BUNNY = [
    "..OO....OO..",
    "..OBO..OBO..",
    "..OBO..OBO..",
    ".OBBOOOOBBO.",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBWWWWWWBBO",
    ".OBBWWWWBBO.",
    ".OBBBBBBBBO.",
    "..OOOOOOOO..",
]

_BEAR = [
    ".OO......OO.",
    "OBBO....OBBO",
    "OBBOOOOOOBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBBBBBBBBBO",
    "OBBWWWWWWBBO",
    ".OBBWWWWBBO.",
    ".OBBBBBBBBO.",
    "..OOOOOOOO..",
]

SPECIES = ["cat", "dog", "bunny", "bear"]
_GRIDS = {"cat": _CAT, "dog": _DOG, "bunny": _BUNNY, "bear": _BEAR}


def species_for(agent_name: str) -> str:
    digest = hashlib.md5(agent_name.encode()).digest()
    return SPECIES[digest[0] % len(SPECIES)]


def _tint(color: QColor, state: str) -> QColor:
    if state == "retrying":
        return QColor(
            min(255, color.red() + 70), max(0, color.green() - 40), max(0, color.blue() - 40)
        )
    if state == "waiting":
        gray = (color.red() + color.green() + color.blue()) // 3
        return QColor(gray, gray, gray, 140)
    return color


def draw_critter(
    painter: QPainter,
    x: int,
    y: int,
    scale: int,
    species: str,
    color: QColor,
    state: str = "working",   # working | retrying | done | waiting
    frame: int = 0,
    crowned: bool = False,
) -> None:
    """Draw one critter with its top-left logical origin at (x, y)."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)  # crisp pixels
    grid = _GRIDS.get(species, _CAT)
    body = _tint(color, state)
    outline = QColor(30, 30, 36, 140 if state == "waiting" else 255)
    white = QColor(240, 236, 226, 140 if state == "waiting" else 255)

    # bob: working critters bounce ±1 logical px
    bob = 0
    if state in ("working", "retrying"):
        bob = -1 if (frame // 3) % 2 else 0

    def px(cx: int, cy: int, c: QColor) -> None:
        painter.fillRect(x + cx * scale, y + (cy + bob) * scale, scale, scale, c)

    for row_index, row in enumerate(grid):
        for col_index, ch in enumerate(row):
            if ch == "B":
                px(col_index, row_index, body)
            elif ch == "O":
                px(col_index, row_index, outline)
            elif ch == "W":
                px(col_index, row_index, white)

    dark = QColor(25, 25, 30)
    eye_y = 5
    blink = state == "working" and (frame % 36) in (0, 1)
    if state == "waiting":
        # sleeping: closed-line eyes
        px(3, eye_y, dark), px(4, eye_y, dark), px(7, eye_y, dark), px(8, eye_y, dark)
    elif state == "done":
        # happy arc eyes ^ ^ + smile
        px(3, eye_y - 1, dark), px(4, eye_y, dark), px(2, eye_y, dark)
        px(7, eye_y, dark), px(8, eye_y - 1, dark), px(9, eye_y, dark)
        px(5, 7, dark), px(6, 7, dark)
    elif blink:
        px(3, eye_y, dark), px(4, eye_y, dark), px(7, eye_y, dark), px(8, eye_y, dark)
    else:
        px(3, eye_y, dark), px(8, eye_y, dark)
        if state == "retrying":
            # worried brows
            brow = QColor(120, 30, 30)
            px(2, eye_y - 2, brow), px(3, eye_y - 2, brow)
            px(8, eye_y - 2, brow), px(9, eye_y - 2, brow)

    if crowned:
        gold = QColor(240, 200, 60)
        for cx in (3, 5, 7):
            px(cx, -2, gold)
        for cx in (3, 4, 5, 6, 7):
            px(cx, -1, gold)

    if state == "waiting":
        # drifting z pixels
        z = QColor(170, 170, 190, 200)
        phase = (frame // 4) % 3
        px(10, 1 - phase if 1 - phase >= -2 else 1, z)
        if phase > 0:
            px(11, 0, z)
    painter.restore()
