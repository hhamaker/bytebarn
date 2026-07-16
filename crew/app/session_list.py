"""Sessions sidebar, Claude-Desktop style: a flat reverse-chronological list
of sessions under time-bucket headers (Today / Yesterday / …), with an
optional Projects section above for organization. Subagent sessions never
appear here — they are reached from transcript task cards and the crew stage.
Supports multi-select delete and drag-to-project."""

from __future__ import annotations

import time
from pathlib import Path
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
_MUTED_COLOR = "#8f96a3"
_ID_ROLE = Qt.UserRole          # session id or project id
_KIND_ROLE = Qt.UserRole + 1    # "session" | "project" | "folder" | "header"
_PARENT_ROLE = Qt.UserRole + 2  # for folders: owning project id

_PROJECTS_HEADER_ID = "__projects__"


def relative_time(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def bucket_label(ts: float, now: float | None = None) -> str:
    """Claude-Desktop-style recency bucket for a session timestamp."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    midnight = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
    if ts >= midnight:
        return "Today"
    if ts >= midnight - 86400:
        return "Yesterday"
    if ts >= midnight - 6 * 86400:
        return "This week"
    if ts >= midnight - 29 * 86400:
        return "This month"
    return "Older"


_BUCKET_ORDER = ["Today", "Yesterday", "This week", "This month", "Older"]


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
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(12)

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
        expanded = self._expanded_ids()
        self.tree.blockSignals(True)
        self.tree.clear()

        project_names = {p.id: (p.name or "(project)") for p in projects}

        # Projects section: organization only, shown when there is more than
        # the implicit default project. Sessions never nest here.
        if len(projects) > 1:
            proj_header = self._header_item(f"Projects  ({len(projects)})")
            proj_header.setData(0, _ID_ROLE, _PROJECTS_HEADER_ID)
            self.tree.addTopLevelItem(proj_header)
            for project in projects:
                count = len(sessions_by_project.get(project.id, []))
                item = self._project_item(project, count)
                proj_header.addChild(item)
                for folder in folders_by_project.get(project.id, []):
                    item.addChild(self._folder_item(project.id, folder))
                if project.id in expanded:
                    item.setExpanded(True)
            proj_header.setExpanded(
                not expanded or _PROJECTS_HEADER_ID in expanded)

        # Recents: every top-level session, flattened across projects, newest
        # first, under time-bucket headers. Subagent children are hidden.
        flat: list[tuple[Any, str]] = []
        for project in projects:
            for session in sessions_by_project.get(project.id, []):
                if getattr(session, "parent_session_id", None):
                    continue
                flat.append((session, project.id))
        flat.sort(key=lambda pair: pair[0].updated_at, reverse=True)

        now = time.time()
        buckets: dict[str, QTreeWidgetItem] = {}
        current_item: QTreeWidgetItem | None = None
        for session, pid in flat:
            label = bucket_label(session.updated_at, now)
            bucket = buckets.get(label)
            if bucket is None:
                bucket = self._header_item(label)
                buckets[label] = bucket
                self.tree.addTopLevelItem(bucket)
                bucket.setExpanded(True)
            item = self._session_item(
                session, running, agent_colors, project_names.get(pid, ""))
            bucket.addChild(item)
            if session.id == current:
                current_item = item
        if current_item is not None:
            self.tree.setCurrentItem(current_item)

        self.tree.blockSignals(False)
        self._filter(self.search.text())

    def _expanded_ids(self) -> set[str]:
        """Ids of expanded projects (plus the Projects header sentinel)."""
        out: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            kind = item.data(0, _KIND_ROLE)
            if kind in ("project", "header") and item.isExpanded():
                out.add(item.data(0, _ID_ROLE))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    @staticmethod
    def _header_item(label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setData(0, _KIND_ROLE, "header")
        item.setData(0, _ID_ROLE, label)
        item.setFlags(Qt.ItemIsEnabled)  # no select, no drag
        item.setForeground(0, QColor(_MUTED_COLOR))
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        item.setFont(0, font)
        return item

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
        name = Path(path).name or path
        item = QTreeWidgetItem([f"🗂 {name}"])
        item.setData(0, _ID_ROLE, path)
        item.setData(0, _KIND_ROLE, "folder")
        item.setData(0, _PARENT_ROLE, project_id)
        item.setToolTip(0, path)
        item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)
        item.setForeground(0, QColor(_MUTED_COLOR))
        return item

    @staticmethod
    def _session_item(
        session: Any,
        running: set[str],
        agent_colors: dict[str, str] | None,
        project_name: str = "",
    ) -> QTreeWidgetItem:
        title = session.title or "(untitled)"
        is_running = session.id in running
        directory = getattr(session, "directory", "") or ""
        dir_name = Path(directory).name if directory else ""
        parts = [title]
        if dir_name:
            parts.append(dir_name)
        parts.append(relative_time(session.updated_at))
        label = f"{'● ' if is_running else ''}{' · '.join(parts)}"
        item = QTreeWidgetItem([label])
        item.setData(0, _ID_ROLE, session.id)
        item.setData(0, _KIND_ROLE, "session")
        tooltip = f"{session.agent} · {session.model or 'default model'}"
        if project_name:
            tooltip += f"\n{project_name}"
        if directory:
            tooltip += f"\n{directory}"
        item.setToolTip(0, tooltip)
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
        if item is None or item.data(0, _KIND_ROLE) == "header":
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

        def apply(item: QTreeWidgetItem) -> bool:
            """Hide non-matching rows; a row survives if it or a child matches.
            Headers survive only through their children."""
            child_hit = False
            for i in range(item.childCount()):
                child_hit = apply(item.child(i)) or child_hit
            is_header = item.data(0, _KIND_ROLE) == "header"
            hit = child_hit or (not is_header and match(item))
            item.setHidden(bool(text) and not hit)
            return hit

        for i in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(i))

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
