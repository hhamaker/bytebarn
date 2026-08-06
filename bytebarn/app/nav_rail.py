"""Left navigation rail — the spine of the Termius-style shell.

A fixed icon column that switches the app between its four views
(Projects / Chat / Code / Terminal) and opens the global tool dialogs
(Agents / Providers / Settings). Pure presentation: emits signals, holds
no app state beyond which view button is checked. See
docs/superpowers/specs/2026-08-05-ui-redesign-design.md.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QPushButton, QVBoxLayout, QWidget

VIEWS = ("projects", "chat", "code", "terminal")

_VIEW_ITEMS = [
    ("projects", "🛖", "Projects — all projects and sessions  (⌘1 / Ctrl+1)"),
    ("chat", "💬", "Chat — this project's conversations  (⌘2 / Ctrl+2)"),
    ("code", "🛠", "Code — goal runs, queue, routines  (⌘3 / Ctrl+3)"),
    ("terminal", ">_", "Terminal — local shells and agent output  (⌘4 / Ctrl+4)"),
]

_TOOL_ITEMS = [
    ("agents", "🐾", "Agents — models, prompts, tools, colors"),
    ("providers", "⚡", "Providers — connect LLM services"),
    ("settings", "⚙", "Settings — defaults, permissions, theme"),
]


class NavRail(QWidget):
    view_selected = Signal(str)   # one of VIEWS
    tool_selected = Signal(str)   # "agents" | "providers" | "settings"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(56)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(6)

        self._view_buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, (view, glyph, tip) in enumerate(_VIEW_ITEMS):
            button = self._make_button(glyph, tip)
            button.setCheckable(True)
            # user click → navigate; programmatic setChecked stays silent
            button.clicked.connect(lambda _=False, v=view: self.view_selected.emit(v))
            self._group.addButton(button)
            self._view_buttons[view] = button
            layout.addWidget(button, 0, Qt.AlignHCenter)
            if i == 0:  # hairline between the home view and project views
                rule = QFrame()
                rule.setObjectName("railRule")
                rule.setFixedSize(24, 1)
                layout.addWidget(rule, 0, Qt.AlignHCenter)

        layout.addStretch(1)

        for tool, glyph, tip in _TOOL_ITEMS:
            button = self._make_button(glyph, tip)
            button.clicked.connect(lambda _=False, t=tool: self.tool_selected.emit(t))
            layout.addWidget(button, 0, Qt.AlignHCenter)

        self._view_buttons["chat"].setChecked(True)

    def _make_button(self, glyph: str, tip: str) -> QPushButton:
        button = QPushButton(glyph)
        button.setObjectName("railItem")
        button.setFixedSize(40, 40)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.TabFocus)
        return button

    # -- state ----------------------------------------------------------------

    def set_active(self, view: str) -> None:
        """Reflect navigation done elsewhere (menus, sidebar) — no signal."""
        button = self._view_buttons.get(view)
        if button is not None:
            button.setChecked(True)

    def active_view(self) -> str:
        for view, button in self._view_buttons.items():
            if button.isChecked():
                return view
        return "chat"
