"""Compact todo progress strip above the prompt bar (spec §9.3)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

_ICON = {"pending": "○", "in_progress": "◐", "completed": "●"}
_COLOR = {"pending": "#888", "in_progress": "#e5c07b", "completed": "#98c379"}


class TodoStrip(QWidget):
    def __init__(self):
        super().__init__()
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(12)
        self._layout.setAlignment(Qt.AlignLeft)
        self.setVisible(False)

    def set_todos(self, todos: list[dict[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not todos:
            self.setVisible(False)
            return
        current = next((t for t in todos if t["status"] == "in_progress"), None)
        done = sum(1 for t in todos if t["status"] == "completed")
        summary = QLabel(f"<b>{done}/{len(todos)}</b>")
        self._layout.addWidget(summary)
        for todo in todos:
            status = todo["status"]
            label = QLabel(
                f"<span style='color:{_COLOR[status]}'>{_ICON[status]}</span> "
                f"{todo['content'][:40]}"
            )
            label.setToolTip(todo["content"])
            if todo is current:
                label.setStyleSheet("QLabel { font-weight: bold; }")
            self._layout.addWidget(label)
        self.setVisible(True)
