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


def resolve_mode(mode: str, app: QApplication) -> str:
    """"follow system" -> "dark" | "light" using the platform color scheme."""
    if mode in ("dark", "light"):
        return mode
    try:
        from PySide6.QtCore import Qt

        scheme = app.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:
        return "dark"


def apply_theme(app: QApplication, mode: str = "follow system") -> None:
    resolved = resolve_mode(mode, app)
    if resolved == "dark":
        palette = QPalette()
        for role, color in _DARK.items():
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(_DARK_QSS)
    else:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(_LIGHT_QSS)
