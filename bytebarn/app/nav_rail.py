"""Left navigation rail — the spine of the Termius-style shell.

A fixed icon column that switches the app between its four views
(Projects / Chat / Code / Terminal) and opens the global tool dialogs
(Agents / Providers / Settings). The » toggle slides the rail out to show
each icon's label. Pure presentation: emits signals, holds no app state
beyond which view button is checked and whether it is expanded. See
docs/superpowers/specs/2026-08-05-ui-redesign-design.md and
docs/superpowers/specs/2026-08-05-hosts-and-rail-labels-design.md.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QPushButton, QVBoxLayout, QWidget

VIEWS = ("projects", "chat", "code", "terminal")

COLLAPSED_WIDTH = 56
EXPANDED_WIDTH = 176

_VIEW_ITEMS = [
    ("projects", "🛖", "Projects", "Projects — all projects and sessions  (⌘1 / Ctrl+1)"),
    ("chat", "💬", "Chat", "Chat — this project's conversations  (⌘2 / Ctrl+2)"),
    ("code", "🛠", "Code", "Code — goal runs, queue, routines  (⌘3 / Ctrl+3)"),
    ("terminal", ">_", "Terminal", "Terminal — shells, hosts, agent output  (⌘4 / Ctrl+4)"),
]

_TOOL_ITEMS = [
    ("agents", "🐾", "Agents", "Agents — models, prompts, tools, colors"),
    ("providers", "⚡", "Providers", "Providers — connect LLM services"),
    ("settings", "⚙", "Settings", "Settings — defaults, permissions, theme"),
]


class NavRail(QWidget):
    view_selected = Signal(str)      # one of VIEWS
    tool_selected = Signal(str)      # "agents" | "providers" | "settings"
    expanded_toggled = Signal(bool)  # user clicked the »/« toggle

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navRail")
        self._expanded = False
        self._anim: QPropertyAnimation | None = None
        self.setFixedWidth(COLLAPSED_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(6)

        self._view_buttons: dict[str, QPushButton] = {}
        self._labels: dict[QPushButton, tuple[str, str]] = {}  # btn -> (glyph, label)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, (view, glyph, label, tip) in enumerate(_VIEW_ITEMS):
            button = self._make_button(glyph, label, tip)
            button.setCheckable(True)
            # user click → navigate; programmatic setChecked stays silent
            button.clicked.connect(lambda _=False, v=view: self.view_selected.emit(v))
            self._group.addButton(button)
            self._view_buttons[view] = button
            layout.addWidget(button, 0, Qt.AlignLeft)
            if i == 0:  # hairline between the home view and project views
                rule = QFrame()
                rule.setObjectName("railRule")
                rule.setFixedSize(24, 1)
                layout.addWidget(rule, 0, Qt.AlignHCenter)

        layout.addStretch(1)

        for tool, glyph, label, tip in _TOOL_ITEMS:
            button = self._make_button(glyph, label, tip)
            button.clicked.connect(lambda _=False, t=tool: self.tool_selected.emit(t))
            layout.addWidget(button, 0, Qt.AlignLeft)

        self.toggle_button = self._make_button("»", "Collapse", "Show what the icons mean")
        self.toggle_button.clicked.connect(self._toggle)
        layout.addWidget(self.toggle_button, 0, Qt.AlignLeft)

        self._view_buttons["chat"].setChecked(True)
        self._apply_expansion(animate=False)

    def _make_button(self, glyph: str, label: str, tip: str) -> QPushButton:
        button = QPushButton(glyph)
        button.setObjectName("railItem")
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.TabFocus)
        self._labels[button] = (glyph, label)
        return button

    # -- expansion ------------------------------------------------------------

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_expansion(animate=animate)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)
        self.expanded_toggled.emit(self._expanded)

    def _apply_expansion(self, animate: bool) -> None:
        expanded = self._expanded
        for button, (glyph, label) in self._labels.items():
            if button is self.toggle_button:
                button.setText("«  Collapse" if expanded else "»")
            else:
                button.setText(f"{glyph}  {label}" if expanded else glyph)
            button.setFixedSize(EXPANDED_WIDTH - 16 if expanded else 40, 40)
            button.setProperty("expanded", "true" if expanded else "false")
            style = button.style()
            style.unpolish(button)
            style.polish(button)
        target = EXPANDED_WIDTH if expanded else COLLAPSED_WIDTH
        if not animate:
            self.setFixedWidth(target)
            return
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        anim = QPropertyAnimation(self, b"maximumWidth", self)
        anim.setDuration(140)
        anim.setStartValue(self.width())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self.setMinimumWidth(int(v)))
        anim.finished.connect(lambda: self.setFixedWidth(target))
        anim.start()
        self._anim = anim  # keep alive

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
