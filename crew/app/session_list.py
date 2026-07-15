"""Sessions sidebar: projects at the top level, sessions nested beneath,
subagent sessions nested under their parent. Supports multi-select delete
and drag-to-project."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
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
_ID_ROLE = Qt.UserRole          # session id or project id
_KIND_ROLE = Qt.UserRole + 1    # "session" | "project" | "folder"
_PARENT_ROLE = Qt.UserRole + 2  # for folders: owning project id


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
    new_session = Signal(str | None)   # optional target project id
    close_session = Signal(str)
    delete_session = Signal(str)
    delete_sessions = Signal(list)          # multi-select delete
    rename_session = Signal(str)
    new_project = Signal()
    rename_project = Signal(str)                # project id
    delete_project = Signal(str)                # project id
    add_folder_to_project = Signal(str)         # project id
    remove_folder_from_project = Signal(str, str)  # project id, folder path
    move_session_to_project = Signal(str)   # session id -> pick a project
    session_moved_to_project = Signal(str, str)  # session_id, project_id (drag)

    def __init__(self):
        super().__init__()
        self.new_button = QPushButton("+ New session")
        self.new_button.clicked.connect(self.new_session)

        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(QLabel("<b>Sessions</b>"), 1)
        add_proj = QPushButton("+ Project")
        add_proj.setFlat(True)
        add_proj.setToolTip("Create a project to group sessions")
        add_proj.clicked.connect(self.new_project)
        hl.addWidget(add_proj)

        self.search = QLineEdit()
        self.search.setPlaceholderText("search…")
        self.search.textChanged.connect(self._filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)  # shift/ctrl multi-select
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(header)
        layout.addWidget(self.new_button)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)

    # -- population ---------------------------------------------------------

    def populate(
        self,
        projects: list[Any],
        sessions_by_project: dict[str, list[Any]],
        running: set[str],
        current: str,
        agent_colors: dict[str, str] | None = None,
        folders_by_project: dict[str, list[str]] | None = None,
    ) -> None:
        folders_by_project = folders_by_project or {}
        expanded = self._expanded_project_ids()
        self.tree.blockSignals(True)
        self.tree.clear()

        # one project: show its sessions flat. Multiple: group under project nodes.
        multi = len(projects) > 1

        for project in projects:
            sessions = sessions_by_project.get(project.id, [])
            folders = folders_by_project.get(project.id, [])
            if multi:
                proj_item = self._project_item(project, len(sessions))
                self.tree.addTopLevelItem(proj_item)
                parent = proj_item
                if not expanded or project.id in expanded:
                    proj_item.setExpanded(True)
                for folder in folders:
                    proj_item.addChild(self._folder_item(project.id, folder))
            else:
                parent = None  # flat under the tree root
                for folder in folders:
                    self.tree.addTopLevelItem(self._folder_item(project.id, folder))

            for session in sessions:
                item = self._session_item(session, running, agent_colors)
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                for child in getattr(session, "children", []) or []:
                    child_item = self._session_item(child, running, agent_colors)
                    item.addChild(child_item)
                    if child.id == current:
                        item.setExpanded(True)
                        self.tree.setCurrentItem(child_item)
                if session.id == current:
                    self.tree.setCurrentItem(item)

        self.tree.blockSignals(False)
        self._filter(self.search.text())

    def _expanded_project_ids(self) -> set[str]:
        out: set[str] = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, _KIND_ROLE) == "project" and item.isExpanded():
                out.add(item.data(0, _ID_ROLE))
        return out

    @staticmethod
    def _project_item(project: Any, count: int) -> QTreeWidgetItem:
        name = project.name or "(project)"
        item = QTreeWidgetItem([f"📁 {name}  ({count})"])
        item.setData(0, _ID_ROLE, project.id)
        item.setData(0, _KIND_ROLE, "project")
        item.setToolTip(0, project.path)
        item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)  # projects don't drag
        font = QFont()
        font.setBold(True)
        item.setFont(0, font)
        return item

    @staticmethod
    def _folder_item(project_id: str, path: str) -> QTreeWidgetItem:
        from pathlib import Path
        name = Path(path).name or path
        item = QTreeWidgetItem([f"🗂 {name}"])
        item.setData(0, _ID_ROLE, path)
        item.setData(0, _KIND_ROLE, "folder")
        item.setData(0, _PARENT_ROLE, project_id)
        item.setToolTip(0, path)
        item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)
        item.setForeground(0, QColor("#8f96a3"))
        return item

    @staticmethod
    def _session_item(
        session: Any, running: set[str], agent_colors: dict[str, str] | None
    ) -> QTreeWidgetItem:
        title = session.title or "(untitled)"
        is_running = session.id in running
        label = f"{'● ' if is_running else ''}{title} · {relative_time(session.updated_at)}"
        item = QTreeWidgetItem([label])
        item.setData(0, _ID_ROLE, session.id)
        item.setData(0, _KIND_ROLE, "session")
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

    # -- selection ----------------------------------------------------------

    def _session_id(self, item: QTreeWidgetItem | None) -> str | None:
        if item is None or item.data(0, _KIND_ROLE) != "session":
            return None
        return item.data(0, _ID_ROLE)

    def _selected_session_ids(self) -> list[str]:
        return [sid for it in self.tree.selectedItems()
                if (sid := self._session_id(it))]

    def _on_click(self, item: QTreeWidgetItem) -> None:
        sid = self._session_id(item)
        if sid:
            self.session_selected.emit(sid)

    def _on_current_changed(self, current: QTreeWidgetItem, _prev=None) -> None:
        # don't switch when a multi-selection is in play (would fight the user)
        if len(self.tree.selectedItems()) > 1:
            return
        sid = self._session_id(current)
        if sid:
            self.session_selected.emit(sid)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            ids = self._selected_session_ids()
            if ids and self._confirm_delete(len(ids)):
                self.delete_sessions.emit(ids)
                return
            # no sessions selected: allow deleting a focused folder node
            item = self.tree.currentItem()
            if item is not None and item.data(0, _KIND_ROLE) == "folder" \
                    and self._confirm_remove_folder():
                self.remove_folder_from_project.emit(
                    item.data(0, _PARENT_ROLE), item.data(0, _ID_ROLE))
            return
        super().keyPressEvent(event)

    # -- context menu -------------------------------------------------------

    def _context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        selected = self._selected_session_ids()
        menu = QMenu(self)

        if len(selected) > 1:
            del_many = menu.addAction(f"Delete {len(selected)} sessions…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is del_many and self._confirm_delete(len(selected)):
                self.delete_sessions.emit(selected)
            return

        if item.data(0, _KIND_ROLE) == "project":
            new_here = menu.addAction("New session in this project")
            rename_proj = menu.addAction("Rename project…")
            add_folder = menu.addAction("Add folder…")
            delete_proj = menu.addAction("Delete project…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is new_here:
                self.new_session.emit(item.data(0, _ID_ROLE))
            elif chosen is rename_proj:
                self.rename_project.emit(item.data(0, _ID_ROLE))
            elif chosen is add_folder:
                self.add_folder_to_project.emit(item.data(0, _ID_ROLE))
            elif chosen is delete_proj and self._confirm_delete_project():
                self.delete_project.emit(item.data(0, _ID_ROLE))
            return

        if item.data(0, _KIND_ROLE) == "folder":
            remove = menu.addAction("Remove folder from project")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is remove:
                self.remove_folder_from_project.emit(
                    item.data(0, _PARENT_ROLE), item.data(0, _ID_ROLE))
            return

        session_id = self._session_id(item)
        if not session_id:
            return
        close_action = menu.addAction("Close session")
        rename_action = menu.addAction("Rename…")
        move_action = menu.addAction("Move to project…")
        delete_action = menu.addAction("Delete session…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is close_action:
            self.close_session.emit(session_id)
        elif chosen is rename_action:
            self.rename_session.emit(session_id)
        elif chosen is move_action:
            self.move_session_to_project.emit(session_id)
        elif chosen is delete_action and self._confirm_delete(1):
            self.delete_session.emit(session_id)

    def _confirm_delete(self, n: int) -> bool:
        what = "this session" if n == 1 else f"these {n} sessions"
        answer = QMessageBox.warning(
            self, "Delete sessions",
            f"Permanently delete {what}, subagent sessions, and all history?"
            " This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

    def _confirm_delete_project(self) -> bool:
        answer = QMessageBox.warning(
            self, "Delete project",
            "Delete this project and ALL its sessions, history, and folders?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

    def _confirm_remove_folder(self) -> bool:
        answer = QMessageBox.question(
            self, "Remove folder",
            "Remove this folder from the project? Sessions are not affected.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

    # -- filter / drag ------------------------------------------------------

    def _filter(self, text: str) -> None:
        text = text.lower()

        def match(item: QTreeWidgetItem) -> bool:
            hay = (item.text(0) + " " + item.toolTip(0)).lower()
            return not text or text in hay

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top_match = match(top)
            child_match = False
            for j in range(top.childCount()):
                ch = top.child(j)
                ch_hit = match(ch)
                gc_hit = any(match(ch.child(k)) for k in range(ch.childCount()))
                ch.setHidden(bool(text) and not (ch_hit or gc_hit))
                child_match = child_match or ch_hit or gc_hit
            top.setHidden(bool(text) and not (top_match or child_match))

    def dropEvent(self, event):
        src = self.tree.currentItem()
        if self._session_id(src) is None:
            return
        dst = self.tree.itemAt(event.position().toPoint())
        if dst is None:
            return
        project = dst
        while project is not None and project.data(0, _KIND_ROLE) != "project":
            project = project.parent()
        if project is None:
            return
        self.session_moved_to_project.emit(
            src.data(0, _ID_ROLE), project.data(0, _ID_ROLE))
        event.accept()
