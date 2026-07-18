"""App-wide theming (spec §7.4): dark, light, or follow-system.

The transcript, crew stage, and cards were designed on the dark palette, so
dark is the flagship look; light keeps Qt's native palette with the same
accent styling.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#61afef"

_DARK = {
    QPalette.Window: "#21252b",
    QPalette.WindowText: "#c8ccd4",
    QPalette.Base: "#1d2026",
    QPalette.AlternateBase: "#2c313c",
    QPalette.Text: "#c8ccd4",
    QPalette.Button: "#2c313c",
    QPalette.ButtonText: "#c8ccd4",
    QPalette.Highlight: ACCENT,
    QPalette.HighlightedText: "#1d2026",
    QPalette.ToolTipBase: "#2c313c",
    QPalette.ToolTipText: "#c8ccd4",
    QPalette.PlaceholderText: "#6b717d",
    QPalette.Link: ACCENT,
}

_DARK_QSS = f"""
QMainWindow, QDialog {{ background: #21252b; }}
QPlainTextEdit, QLineEdit, QTreeWidget, QListWidget {{
    background: #1d2026; border: 1px solid #3a3f4b; border-radius: 6px;
    selection-background-color: {ACCENT}; padding: 3px;
}}
QPlainTextEdit:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox {{
    background: #2c313c; border: 1px solid #3a3f4b; border-radius: 6px; padding: 3px 8px;
}}
QComboBox QAbstractItemView {{ background: #2c313c; border: 1px solid #3a3f4b; }}
QPushButton {{
    background: #2c313c; border: 1px solid #3a3f4b; border-radius: 6px; padding: 4px 12px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #353b48; }}
QPushButton:flat {{ background: transparent; border: none; }}
QPushButton:flat:hover {{ color: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a3f4b; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #4b5263; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QStatusBar {{ background: #1d2026; }}
QSplitter::handle {{ background: #21252b; width: 3px; }}
QToolTip {{ background: #2c313c; color: #c8ccd4; border: 1px solid #3a3f4b; }}
QGroupBox {{ border: 1px solid #3a3f4b; border-radius: 6px; margin-top: 8px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: #8f96a3; }}
"""

_LIGHT_QSS = f"""
QPushButton:flat:hover {{ color: {ACCENT}; }}
"""

# -- "Night Workshop" — the opt-in overhaul look ----------------------------
#
# Crew's identity is its pixel-art critters working late on your codebase, so
# the overhaul leans into that instead of a generic flat dark theme: a warm
# charcoal room, amber lantern light for the accent, and chunky 2px-bordered
# controls with a pixel-button press (content shifts down a pixel). The green
# "running" color stays — it belongs to the sprites.

MODERN_ACCENT = "#e5a458"       # lantern amber
_M_BG = "#16181d"               # room
_M_PANEL = "#1b1f26"            # panels / inputs
_M_RAISED = "#252b36"           # buttons / elevated
_M_BORDER = "#333a47"
_M_TEXT = "#d6dae2"
_M_MUTED = "#7d8590"

_MODERN = {
    QPalette.Window: _M_BG,
    QPalette.WindowText: _M_TEXT,
    QPalette.Base: _M_PANEL,
    QPalette.AlternateBase: _M_RAISED,
    QPalette.Text: _M_TEXT,
    QPalette.Button: _M_RAISED,
    QPalette.ButtonText: _M_TEXT,
    QPalette.Highlight: MODERN_ACCENT,
    QPalette.HighlightedText: "#1a130a",
    QPalette.ToolTipBase: _M_RAISED,
    QPalette.ToolTipText: _M_TEXT,
    QPalette.PlaceholderText: "#6a707c",
    QPalette.Link: MODERN_ACCENT,
}

_MODERN_QSS = f"""
QMainWindow, QDialog {{ background: {_M_BG}; }}

QPlainTextEdit, QLineEdit, QTreeWidget, QListWidget {{
    background: {_M_PANEL}; border: 2px solid {_M_BORDER}; border-radius: 8px;
    selection-background-color: {MODERN_ACCENT}; selection-color: #1a130a;
    padding: 4px;
}}
QPlainTextEdit:focus, QLineEdit:focus {{ border-color: {MODERN_ACCENT}; }}

QTreeWidget::item, QListWidget::item {{
    padding: 3px 4px; border-radius: 6px;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: #232833; }}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: #3a3325; color: {MODERN_ACCENT};
}}

QComboBox {{
    background: {_M_RAISED}; border: 2px solid {_M_BORDER}; border-radius: 8px;
    padding: 3px 10px;
}}
QComboBox:hover {{ border-color: #4a5261; }}
QComboBox QAbstractItemView {{
    background: {_M_RAISED}; border: 2px solid {_M_BORDER}; border-radius: 8px;
    selection-background-color: #3a3325; selection-color: {MODERN_ACCENT};
}}

QPushButton {{
    background: {_M_RAISED}; border: 2px solid {_M_BORDER}; border-radius: 8px;
    padding: 5px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {MODERN_ACCENT}; color: {MODERN_ACCENT}; }}
QPushButton:pressed {{
    background: #1f242d; padding-top: 6px; padding-bottom: 4px;  /* pixel press */
}}
QPushButton:flat {{ background: transparent; border: none; font-weight: 500; }}
QPushButton:flat:hover {{ color: {MODERN_ACCENT}; }}
QPushButton#send {{
    background: {MODERN_ACCENT}; border: 2px solid #c98c44; color: #1a130a;
}}
QPushButton#send:hover {{ background: #f0b56b; color: #1a130a; }}
QPushButton#send:pressed {{ background: #c98c44; }}

QTabWidget::pane {{ border: none; margin-top: 4px; }}
QTabBar::tab {{
    background: transparent; color: {_M_MUTED}; padding: 6px 12px;
    border: none; border-bottom: 2px solid transparent;
    font-family: ui-monospace, Menlo, monospace; font-size: 12px;
}}
QTabBar::tab:hover {{ color: {_M_TEXT}; }}
QTabBar::tab:selected {{ color: {MODERN_ACCENT}; border-bottom: 2px solid {MODERN_ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {_M_BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {MODERN_ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{ background: {_M_BORDER}; border-radius: 4px; min-width: 24px; }}

QStatusBar {{ background: {_M_PANEL}; color: {_M_MUTED};
    font-family: ui-monospace, Menlo, monospace; font-size: 11px; }}
QSplitter::handle {{ background: {_M_BG}; width: 4px; }}
QToolTip {{ background: {_M_RAISED}; color: {_M_TEXT};
    border: 2px solid {_M_BORDER}; border-radius: 6px; padding: 4px; }}
QGroupBox {{ border: 2px solid {_M_BORDER}; border-radius: 8px;
    margin-top: 8px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {_M_MUTED};
    font-family: ui-monospace, Menlo, monospace; font-size: 11px; }}
QMenu {{ background: {_M_RAISED}; border: 2px solid {_M_BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #3a3325; color: {MODERN_ACCENT}; }}
QPushButton#pill {{
    background: #1f242d; border: 1px solid {_M_BORDER}; border-radius: 10px;
    padding: 2px 10px; font-family: ui-monospace, Menlo, monospace;
    font-size: 11px; font-weight: 500;
}}
QPushButton#pill:hover {{ border-color: {MODERN_ACCENT}; }}
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
    if resolved == "dark":
        palette = QPalette()
        for role, color in _DARK.items():
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(_DARK_QSS)
    elif resolved == "modern":
        palette = QPalette()
        for role, color in _MODERN.items():
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(_MODERN_QSS)
    else:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(_LIGHT_QSS)
