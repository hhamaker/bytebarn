"""Sessions sidebar: per-project list, child sessions nested (spec §7.1)."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


def relative_time(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


class SessionList(QWidget):
    session_selected = Signal(str)
    new_session = Signal()

    def __init__(self):
        super().__init__()
        self.new_button = QPushButton("+ New session")
        self.new_button.clicked.connect(self.new_session)
        self.search = QLineEdit()
        self.search.setPlaceholderText("search…")
        self.search.textChanged.connect(self._filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.new_button)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)

    def populate(self, sessions: list[Any], children: dict[str, list[Any]], running: set[str], current: str) -> None:
        self.tree.clear()
        for session in sessions:
            item = self._item(session, running)
            self.tree.addTopLevelItem(item)
            for child in children.get(session.id, []):
                item.addChild(self._item(child, running))
            if session.id == current:
                self.tree.setCurrentItem(item)
                item.setExpanded(True)
        self._filter(self.search.text())

    @staticmethod
    def _item(session: Any, running: set[str]) -> QTreeWidgetItem:
        title = session.title or "(untitled)"
        prefix = "● " if session.id in running else ""
        label = f"{prefix}{title}  · {relative_time(session.updated_at)}"
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.UserRole, session.id)
        item.setToolTip(0, f"{session.agent} · {session.model or 'default model'}")
        return item

    def _on_click(self, item: QTreeWidgetItem) -> None:
        self.session_selected.emit(item.data(0, Qt.UserRole))

    def _filter(self, text: str) -> None:
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(bool(text) and text not in item.text(0).lower())
