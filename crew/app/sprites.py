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
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

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

# small overlays drawn after the base sprite + eyes
ACCENTS = ["none", "glasses", "goggles", "hat", "scarf", "bow"]

# known agent types -> distinct species + accent; same type always same look
_LOOK_BY_TYPE: dict[str, tuple[str, str]] = {
    "explore": ("bunny", "none"),
    "general": ("dog", "none"),
    "tester": ("cat", "goggles"),
    "test": ("cat", "goggles"),
    "reviewer": ("bear", "glasses"),
    "review": ("bear", "glasses"),
    "orchestrator": ("bear", "hat"),
    "worker": ("dog", "scarf"),
    "decomposer": ("bear", "hat"),
    "planner": ("dog", "hat"),
    "plan": ("dog", "hat"),
    "verifier": ("cat", "glasses"),
    "researcher": ("bunny", "glasses"),
    "research": ("bunny", "glasses"),
    "ui-design": ("cat", "bow"),
    "ui": ("cat", "bow"),
    "hub": ("bear", "none"),
    "build": ("dog", "scarf"),
    "chat": ("bunny", "bow"),
}

_SUBSTRING_LOOKS = [
    ("test", "tester"),
    ("review", "reviewer"),
    ("explor", "explore"),
    ("research", "researcher"),
    ("verif", "verifier"),
    ("decompos", "decomposer"),
    ("plan", "planner"),
    ("orchestr", "orchestrator"),
    ("design", "ui-design"),
    ("ui", "ui-design"),
    ("work", "worker"),
    ("general", "general"),
]

_ACCENT_FALLBACK = ["none", "glasses", "hat", "scarf", "bow"]


def species_for(agent_name: str) -> str:
    return look_for(agent_name)[0]


def look_for(agent_name: str) -> tuple[str, str]:
    """Stable (species, accent) for an agent name; known types get fixed looks."""
    key = agent_name.strip().lower()
    exact = _LOOK_BY_TYPE.get(key)
    if exact:
        return exact
    for needle, alias in _SUBSTRING_LOOKS:
        if needle in key:
            return _LOOK_BY_TYPE[alias]
    # custom agents: stable species + accent from name hash
    digest = hashlib.md5(key.encode()).digest()
    return (
        SPECIES[digest[0] % len(SPECIES)],
        _ACCENT_FALLBACK[digest[1] % len(_ACCENT_FALLBACK)],
    )


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        a.alpha(),
    )


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
    accent: str = "none",     # none | glasses | goggles | hat | scarf | bow
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

    # type-specific accents (after eyes so frames sit on top)
    _draw_accent(px, accent if not (crowned and accent == "hat") else "none",
                 body, eye_y, state)

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


DEFAULT_CAST = (("orchestrator", "#e5c07b"), ("build", "#61afef"),
                ("explore", "#56b6c2"), ("general", "#98c379"))


def crew_banner(cast=DEFAULT_CAST, scale: int = 4) -> QPixmap:
    """Row of critters (welcome screen, first-run wizard, docs shots)."""
    image = QImage((SPRITE_W + 6) * scale * len(cast), (SPRITE_H + 4) * scale,
                   QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for i, (name, color) in enumerate(cast):
        species, accent = look_for(name)
        draw_critter(painter, (i * (SPRITE_W + 6) + 3) * scale, 3 * scale, scale,
                     species, QColor(color), state="done", accent=accent,
                     crowned=name == "orchestrator")
    painter.end()
    return QPixmap.fromImage(image)


def critter_pixmap(agent_name: str, color: str | QColor, scale: int = 4) -> QPixmap:
    """Standalone critter portrait (icons, previews)."""
    image = QImage((SPRITE_W + 2) * scale, (SPRITE_H + 3) * scale, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    species, accent = look_for(agent_name)
    draw_critter(painter, scale, 3 * scale, scale, species,
                 QColor(color) if isinstance(color, str) else color,
                 state="done", accent=accent)
    painter.end()
    return QPixmap.fromImage(image)


_INK = QColor(38, 36, 48)
_PINK = QColor(255, 158, 180)
_WHITE = QColor(250, 250, 250)


def _draw_accent(px, accent: str, body: QColor, eye_y: int, state: str) -> None:
    """Overlay accessory pixels; px(cx, cy, color) plots one logical pixel."""
    if accent == "none":
        return
    alpha = 140 if state == "waiting" else 255

    def a(c: QColor) -> QColor:
        c = QColor(c)
        c.setAlpha(alpha)
        return c

    if accent in ("glasses", "goggles"):
        frame_c = a(_INK)
        bridge = a(_mix(_INK, _WHITE, 0.15))
        for ex in (3, 7):
            px(ex - 1, eye_y, frame_c)
            px(ex + 2, eye_y, frame_c)
            px(ex - 1, eye_y + 1, frame_c)
            px(ex + 2, eye_y + 1, frame_c)
            if accent == "goggles":
                for gx in range(ex - 1, ex + 3):
                    px(gx, eye_y - 1, frame_c)
        px(5, eye_y, bridge)
        px(6, eye_y, bridge)
        return

    if accent == "hat":
        brim = a(_mix(body, _INK, 0.55))
        top = a(_mix(body, _INK, 0.35))
        for cx in range(2, 10):
            px(cx, -1, brim)
        for cx in range(4, 8):
            px(cx, -2, top)
        return

    if accent == "scarf":
        cloth = a(_mix(body, _PINK, 0.45))
        knot = a(_mix(_PINK, _INK, 0.2))
        for cx in range(3, 9):
            px(cx, 8, cloth)
        px(4, 9, cloth)
        px(5, 9, knot)
        px(6, 10, cloth)
        return

    if accent == "bow":
        ribbon = a(_mix(_PINK, body, 0.25))
        shine = a(_mix(_PINK, _WHITE, 0.3))
        px(5, 1, ribbon)
        px(6, 1, ribbon)
        px(4, 2, ribbon)
        px(7, 2, ribbon)
        px(5, 2, shine)
        px(6, 2, shine)
