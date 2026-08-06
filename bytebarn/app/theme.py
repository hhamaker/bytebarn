"""App-wide theming (spec §7.4): dark, light, or follow-system.

All three looks are generated from one QSS template fed by a token dict, so
the geometry (radii, spacing, hairline borders, the composer, transcript
bubbles) is identical everywhere and only the palette changes:

- "dark"   — cool graphite with the brand blue accent (default).
- "modern" — the Night Workshop: warm charcoal + lantern amber, the look the
             critters were drawn for.
- "light"  — the same system on paper-white surfaces.

Widget files style themselves through objectNames (#composer, #userBubble,
#partCard, …) so per-widget setStyleSheet calls stay out of the app code.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#61afef"
MODERN_ACCENT = "#e5a458"       # lantern amber

# -- token sets --------------------------------------------------------------

_DARK_TOKENS = {
    "rail": "#101217",          # nav rail — darkest of the three columns
    "bg": "#16181d",            # window canvas
    "surface": "#1b1e24",       # inputs, sidebar panels, composer
    "raised": "#232733",        # buttons, menus, hover surfaces
    "hover": "#20242d",
    "border": "#2a2f3a",        # 1px hairlines everywhere
    "text": "#dfe3ea",
    "muted": "#8b93a1",
    "accent": ACCENT,
    "accent_hover": "#7cbcf5",
    "accent_ink": "#10151c",    # text on accent fills
    "accent_soft": "#24303f",   # selected rows, accent-tinted surfaces
    "bubble": "#242936",        # user message container
    "code_bg": "#1d2027",
}

_MODERN_TOKENS = {
    "rail": "#100e12",          # nav rail — darkest of the three columns
    "bg": "#171519",            # warm charcoal room
    "surface": "#1d1a1f",
    "raised": "#272229",
    "hover": "#232025",
    "border": "#322d33",
    "text": "#e2ddd6",
    "muted": "#948d85",
    "accent": MODERN_ACCENT,
    "accent_hover": "#f0b56b",
    "accent_ink": "#201709",
    "accent_soft": "#332a1e",
    "bubble": "#28242a",
    "code_bg": "#201d22",
}

_LIGHT_TOKENS = {
    "rail": "#e9eaee",          # nav rail — one step darker than the canvas
    "bg": "#f7f7f8",
    "surface": "#ffffff",
    "raised": "#ececf0",
    "hover": "#eff0f3",
    "border": "#dcdde3",
    "text": "#2a2f38",
    "muted": "#6e7481",
    "accent": "#3d7fc4",
    "accent_hover": "#5b96d6",
    "accent_ink": "#ffffff",
    "accent_soft": "#e3edf8",
    "bubble": "#eceef2",
    "code_bg": "#f1f2f5",
}

_TOKENS = {"dark": _DARK_TOKENS, "modern": _MODERN_TOKENS, "light": _LIGHT_TOKENS}


def tokens() -> dict[str, str]:
    """Palette tokens for the theme currently applied (widget paint code)."""
    return _TOKENS[_current_mode]


def _palette(t: dict[str, str]) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.Window: t["bg"],
        QPalette.WindowText: t["text"],
        QPalette.Base: t["surface"],
        QPalette.AlternateBase: t["raised"],
        QPalette.Text: t["text"],
        QPalette.Button: t["raised"],
        QPalette.ButtonText: t["text"],
        QPalette.Highlight: t["accent"],
        QPalette.HighlightedText: t["accent_ink"],
        QPalette.ToolTipBase: t["raised"],
        QPalette.ToolTipText: t["text"],
        QPalette.PlaceholderText: t["muted"],
        QPalette.Link: t["accent"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    return palette


def _qss(t: dict[str, str]) -> str:
    mono = "font-family: ui-monospace, Menlo, monospace;"
    return f"""
QMainWindow, QDialog {{ background: {t['bg']}; }}

/* -- nav rail ----------------------------------------------------------- */
QWidget#navRail {{ background: {t['rail']}; border-right: 1px solid {t['border']}; }}
QFrame#railRule {{ background: {t['border']}; border: none; }}
QPushButton#railItem {{
    background: transparent; border: none; border-radius: 11px;
    color: {t['muted']}; font-size: 16px; padding: 0;
    {mono}
}}
QPushButton#railItem[expanded="true"] {{
    text-align: left; padding-left: 10px; font-size: 13px;
}}
QPushButton#railItem:hover {{ background: {t['hover']}; color: {t['text']}; }}
QPushButton#railItem:checked {{
    background: {t['accent_soft']}; color: {t['text']};
    border: 1px solid {t['accent']};
}}
QPushButton#railItem:focus {{ outline: none; background: {t['hover']}; }}
QPushButton#railItem:checked:focus {{ background: {t['accent_soft']}; }}

/* -- terminal panes ----------------------------------------------------- */
QFrame#termPane {{ border: 1px solid {t['border']}; border-radius: 6px; }}
QFrame#termPane[active="true"] {{ border-color: {t['accent']}; }}
QWidget#paneHeader {{ background: {t['surface']}; }}
QWidget#paneHeader QPushButton {{ padding: 0; color: {t['muted']}; }}
QWidget#paneHeader QPushButton:hover {{ color: {t['text']}; }}
QLabel#paneTitle {{ color: {t['muted']}; {mono} font-size: 11px; }}
QFrame#termPane[active="true"] QLabel#paneTitle {{ color: {t['text']}; }}

/* -- inputs & lists ---------------------------------------------------- */
QPlainTextEdit, QLineEdit, QTreeWidget, QListWidget {{
    background: {t['surface']}; border: 1px solid {t['border']};
    border-radius: 10px; padding: 4px;
    selection-background-color: {t['accent']}; selection-color: {t['accent_ink']};
}}
QPlainTextEdit:focus, QLineEdit:focus {{ border-color: {t['accent']}; }}
QTreeWidget::item, QListWidget::item {{ padding: 4px 6px; border-radius: 8px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {t['hover']}; }}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {t['accent_soft']}; color: {t['text']};
}}

/* -- buttons ------------------------------------------------------------ */
QPushButton {{
    background: {t['raised']}; border: 1px solid {t['border']};
    border-radius: 9px; padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['hover']}; }}
QPushButton:flat {{ background: transparent; border: none; color: {t['muted']};
    padding: 2px 4px; }}
QPushButton:flat:hover {{ color: {t['accent']}; background: transparent; }}
QPushButton#send {{
    background: {t['accent']}; border: none; color: {t['accent_ink']};
    border-radius: 9px; padding: 6px 18px; font-weight: 600;
}}
QPushButton#send:hover {{ background: {t['accent_hover']}; }}
QPushButton#send:pressed {{ background: {t['accent']}; }}
QPushButton#pill {{
    background: {t['surface']}; border: 1px solid {t['border']};
    border-radius: 11px; padding: 2px 10px; color: {t['muted']};
    {mono} font-size: 11px;
}}
QPushButton#pill:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}

/* -- combos ------------------------------------------------------------- */
QComboBox {{
    background: {t['raised']}; border: 1px solid {t['border']};
    border-radius: 9px; padding: 3px 10px;
}}
QComboBox:hover {{ border-color: {t['accent']}; }}
QComboBox QAbstractItemView {{
    background: {t['raised']}; border: 1px solid {t['border']}; border-radius: 8px;
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
}}

/* -- the composer (prompt bar) ------------------------------------------ */
QFrame#composer {{
    background: {t['surface']}; border: 1px solid {t['border']};
    border-radius: 14px;
}}
QFrame#composer QPlainTextEdit {{
    background: transparent; border: none; border-radius: 0; padding: 2px;
}}
QFrame#composer QComboBox {{
    background: transparent; border: none; color: {t['muted']};
    padding: 2px 8px; border-radius: 7px;
}}
QFrame#composer QComboBox:hover {{ background: {t['hover']}; color: {t['text']}; }}

/* -- transcript ---------------------------------------------------------- */
QLabel#userBubble {{
    background: {t['bubble']}; color: {t['text']};
    border-radius: 12px; padding: 10px 14px;
}}
QLabel#userBubble[queued="true"] {{ border: 1px dashed {t['accent']}; }}
QFrame#partCard {{
    background: transparent; border: 1px solid {t['border']}; border-radius: 10px;
}}
QFrame#partCard:hover {{ background: {t['hover']}; }}
QFrame#partCard QLabel {{ padding: 2px 6px 6px 6px; }}
QLabel#fileThumb {{
    border: 1px solid {t['border']}; border-radius: 10px; padding: 4px;
}}

/* -- tabs ---------------------------------------------------------------- */
QTabWidget::pane {{ border: none; margin-top: 4px; }}
QTabBar::tab {{
    background: transparent; color: {t['muted']}; padding: 6px 12px;
    border: none; border-bottom: 2px solid transparent; font-size: 12px;
}}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; }}

/* -- chrome -------------------------------------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QStatusBar {{ background: {t['bg']}; color: {t['muted']}; {mono} font-size: 11px; }}
QStatusBar QLabel {{ color: {t['muted']}; }}
QSplitter::handle {{ background: {t['bg']}; }}
QToolTip {{
    background: {t['raised']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 6px; padding: 4px;
}}
QGroupBox {{ border: 1px solid {t['border']}; border-radius: 10px;
    margin-top: 8px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {t['muted']};
    {mono} font-size: 11px; }}
QMenu {{ background: {t['raised']}; border: 1px solid {t['border']};
    border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
"""


def resolve_mode(mode: str, app: QApplication) -> str:
    """"follow system" -> "dark" | "light" using the platform color scheme."""
    if mode in ("dark", "light", "modern"):
        return mode
    try:
        from PySide6.QtCore import Qt

        scheme = app.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:
        return "dark"


_current_mode = "dark"


def current_mode() -> str:
    """The theme most recently applied ("dark" | "light" | "modern")."""
    return _current_mode


def is_modern() -> bool:
    return _current_mode == "modern"


def crossfade(stack, index: int, duration: int = 160) -> None:
    """Switch a QStackedWidget with a fade-in — only in the modern theme.

    Classic themes switch instantly, exactly as before the overhaul."""
    stack.setCurrentIndex(index)
    if not is_modern():
        return
    from PySide6.QtCore import QEasingCurve, QPropertyAnimation
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    widget = stack.currentWidget()
    if widget is None:
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    # drop the effect afterwards — a lingering effect breaks child repaints
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)


def apply_theme(app: QApplication, mode: str = "follow system") -> None:
    global _current_mode
    resolved = resolve_mode(mode, app)
    _current_mode = resolved
    t = _TOKENS[resolved]
    app.setPalette(_palette(t))
    app.setStyleSheet(_qss(t))
