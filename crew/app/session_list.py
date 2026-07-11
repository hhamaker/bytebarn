"""Sessions sidebar: per-project list, child sessions nested (spec §7.1)."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_RUNNING_COLOR = "#98c379"


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
    close_session = Signal(str)
    delete_session = Signal(str)

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
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.new_button)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)

    def populate(
        self,
        sessions: list[Any],
        children: dict[str, list[Any]],
        running: set[str],
        current: str,
        agent_colors: dict[str, str] | None = None,
    ) -> None:
        self.tree.clear()
        for session in sessions:
            item = self._item(session, running, agent_colors)
            self.tree.addTopLevelItem(item)
            for child in children.get(session.id, []):
                item.addChild(self._item(child, running, agent_colors))
            if session.id == current:
                self.tree.setCurrentItem(item)
                item.setExpanded(True)
        self._filter(self.search.text())

    @staticmethod
    def _item(
        session: Any, running: set[str], agent_colors: dict[str, str] | None
    ) -> QTreeWidgetItem:
        title = session.title or "(untitled)"
        is_running = session.id in running
        label = f"{'● ' if is_running else ''}{title} · {relative_time(session.updated_at)}"
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.UserRole, session.id)
        item.setToolTip(0, f"{session.agent} · {session.model or 'default model'}")
        color = (agent_colors or {}).get(session.agent, "#98c379")
        from .sprites import critter_pixmap

        item.setIcon(0, QIcon(critter_pixmap(session.agent, color, scale=2)))
        if is_running:
            item.setForeground(0, QColor(_RUNNING_COLOR))
            font = QFont()
            font.setBold(True)
            item.setFont(0, font)
        return item

    def _on_click(self, item: QTreeWidgetItem) -> None:
        self.session_selected.emit(item.data(0, Qt.UserRole))

    def _context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        session_id = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        close_action = menu.addAction("Close session")
        delete_action = menu.addAction("Delete session…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is close_action:
            self.close_session.emit(session_id)
        elif chosen is delete_action:
            answer = QMessageBox.warning(
                self,
                "Delete session",
                "Permanently delete this session, its subagent sessions,"
                " and all history? This cannot be undone.",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer == QMessageBox.Yes:
                self.delete_session.emit(session_id)

    def _filter(self, text: str) -> None:
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(bool(text) and text not in item.text(0).lower())
