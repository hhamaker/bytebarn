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
from PySide6.QtGui import QIcon
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

_ID_ROLE = Qt.UserRole          # session id or project id
_KIND_ROLE = Qt.UserRole + 1    # "session" | "project" | "header"

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


class _SessionTree(QTreeWidget):
    """Tree with hand-rolled session→project dragging.

    Qt's native view DnD (QDrag.exec) runs a platform event loop that is
    opaque, untestable offscreen, and was silently swallowing these drops.
    Plain mouse events are deterministic: press on a session, move past the
    drag threshold, release over a project row → emit the move signal."""

    def __init__(self, owner: "SessionList"):
        super().__init__()
        self._owner = owner
        self._press_pos: QPoint | None = None
        self._drag_session: str | None = None

    def _project_at(self, pos: QPoint):
        item = self.itemAt(pos)
        while item is not None and item.data(0, _KIND_ROLE) != "project":
            item = item.parent()
        return item

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if self._owner._session_id(item) is not None:
                self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is not None and event.buttons() & Qt.LeftButton:
            pos = event.position().toPoint()
            if self._drag_session is None:
                from PySide6.QtWidgets import QApplication

                if ((pos - self._press_pos).manhattanLength()
                        >= QApplication.startDragDistance()):
                    self._drag_session = self._owner._session_id(
                        self.itemAt(self._press_pos))
            if self._drag_session is not None:
                over = self._project_at(pos)
                self.setCursor(Qt.DragMoveCursor if over is not None
                               else Qt.ForbiddenCursor)
                return  # swallow: no rubber-band selection while dragging
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        dragged, self._drag_session = self._drag_session, None
        self._press_pos = None
        self.unsetCursor()
        if dragged is not None:
            project = self._project_at(event.position().toPoint())
            if project is not None:
                self._owner.session_moved_to_project.emit(
                    dragged, project.data(0, _ID_ROLE))
                event.accept()
                return
        super().mouseReleaseEvent(event)


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
    open_project = Signal(str)                  # project id -> workspace view
    open_project_settings = Signal(str)         # project id -> settings dialog
    move_session_to_project = Signal(str)   # session id -> pick a project
    session_moved_to_project = Signal(str, str)  # session_id, project_id (drag)
    content_search_requested = Signal(str)  # debounced full-text query

    def __init__(self):
        super().__init__()
        from PySide6.QtCore import QTimer

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._last_query = ""

        def _emit_search() -> None:
            self._last_query = self.search.text()
            self.content_search_requested.emit(self._last_query)

        self._search_timer.timeout.connect(_emit_search)
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

        self.tree = _SessionTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)  # shift/ctrl multi-select
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        # dragging is hand-rolled in _SessionTree — native view DnD stays off
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(12)
        self.tree.setMouseTracking(True)  # hover states for the painted rows
        from .delegates import SessionRowDelegate

        self.tree.setItemDelegate(SessionRowDelegate(self.tree))

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
        default_project_id: str = "",
    ) -> None:
        """Claude-style sidebar: every project is a row with its sessions
        nested; Recents is a time-bucketed view of all sessions. A session
        always appears under its project no matter which folder the app is
        open on (the old default-project carve-out made rows vanish)."""
        expanded, known = self._expansion_state()

        def keep_open(node_id: str) -> bool:
            # collapse only rows the user explicitly collapsed before; rows
            # that just appeared default to open so new content is visible
            return node_id not in known or node_id in expanded

        self.tree.blockSignals(True)
        self.tree.clear()

        if not default_project_id and projects:
            default_project_id = projects[0].id
        project_names = {p.id: (p.name or "(project)") for p in projects}

        def top_level(project_id: str) -> list[Any]:
            return [s for s in sessions_by_project.get(project_id, [])
                    if not getattr(s, "parent_session_id", None)]

        current_item: QTreeWidgetItem | None = None

        # Projects section: each project owns its sessions, nested. The
        # working-directory project sorts first but is not special-cased —
        # hiding it made "move to project" look broken whenever the app was
        # opened on that project's folder.
        user_projects = sorted(
            projects, key=lambda p: p.id != default_project_id)
        if user_projects:
            proj_header = self._header_item(f"Projects  ({len(user_projects)})")
            proj_header.setData(0, _ID_ROLE, _PROJECTS_HEADER_ID)
            self.tree.addTopLevelItem(proj_header)
            for project in user_projects:
                sessions = sorted(top_level(project.id),
                                  key=lambda s: s.updated_at, reverse=True)
                item = self._project_item(project, len(sessions))
                proj_header.addChild(item)
                has_current = False
                for session in sessions:
                    child = self._session_item(session, running, agent_colors,
                                               project_names.get(project.id, ""))
                    item.addChild(child)
                    if session.id == current:
                        current_item = child
                        has_current = True
                item.setExpanded(has_current or keep_open(project.id))
            proj_header.setExpanded(keep_open(_PROJECTS_HEADER_ID))

        # Recents: every top-level session, newest first, under time-bucket
        # headers. Subagent children are hidden everywhere.
        recents = sorted(
            ((s, pid) for pid in sessions_by_project for s in top_level(pid)),
            key=lambda pair: pair[0].updated_at, reverse=True)
        now = time.time()
        buckets: dict[str, QTreeWidgetItem] = {}
        for session, pid in recents:
            label = bucket_label(session.updated_at, now)
            bucket = buckets.get(label)
            if bucket is None:
                bucket = self._header_item(label)
                buckets[label] = bucket
                self.tree.addTopLevelItem(bucket)
                bucket.setExpanded(True)
            item = self._session_item(
                session, running, agent_colors,
                project_names.get(pid, ""))
            bucket.addChild(item)
            if session.id == current:
                current_item = item
        if current_item is not None:
            self.tree.setCurrentItem(current_item)

        self.tree.blockSignals(False)
        self._filter(self.search.text())

    def _expansion_state(self) -> tuple[set[str], set[str]]:
        """(expanded ids, all known ids) for projects + headers in the tree.

        Both sets are needed: a row absent from *known* is new and should
        default to expanded — treating "never seen" as "collapsed" made
        rows appear to vanish (e.g. a session moved into a collapsed
        project)."""
        expanded: set[str] = set()
        known: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            kind = item.data(0, _KIND_ROLE)
            if kind in ("project", "header"):
                known.add(item.data(0, _ID_ROLE))
                if item.isExpanded():
                    expanded.add(item.data(0, _ID_ROLE))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return expanded, known

    def reveal_session(self, session_id: str) -> None:
        """Expand ancestors of a session row and scroll it into view."""
        def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            if item.data(0, _KIND_ROLE) == "session" \
                    and item.data(0, _ID_ROLE) == session_id:
                return item
            for i in range(item.childCount()):
                if (hit := walk(item.child(i))) is not None:
                    return hit
            return None

        for i in range(self.tree.topLevelItemCount()):
            hit = walk(self.tree.topLevelItem(i))
            if hit is not None:
                parent = hit.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.scrollToItem(hit)
                return

    @staticmethod
    def _header_item(label: str) -> QTreeWidgetItem:
        from .delegates import TITLE_ROLE

        item = QTreeWidgetItem([label])
        item.setData(0, _KIND_ROLE, "header")
        item.setData(0, _ID_ROLE, label)
        item.setData(0, TITLE_ROLE, label.split("  (")[0])
        item.setFlags(Qt.ItemIsEnabled)  # no select, no drag
        return item

    @staticmethod
    def _project_item(project: Any, count: int) -> QTreeWidgetItem:
        from .delegates import COUNT_ROLE, TITLE_ROLE

        name = project.name or "(project)"
        item = QTreeWidgetItem([f"📁 {name}  ({count})"])
        item.setData(0, _ID_ROLE, project.id)
        item.setData(0, _KIND_ROLE, "project")
        item.setData(0, TITLE_ROLE, name)
        item.setData(0, COUNT_ROLE, count)
        item.setToolTip(0, project.path)
        item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)  # projects don't drag
        return item

    @staticmethod
    def _session_item(
        session: Any,
        running: set[str],
        agent_colors: dict[str, str] | None,
        project_name: str = "",
    ) -> QTreeWidgetItem:
        from .delegates import META_ROLE, RUNNING_ROLE, TITLE_ROLE

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
        item.setData(0, TITLE_ROLE, title)
        meta_bits = [b for b in (dir_name, relative_time(session.updated_at)) if b]
        item.setData(0, META_ROLE, " · ".join(meta_bits))
        item.setData(0, RUNNING_ROLE, is_running)
        tooltip = f"{session.agent} · {session.model or 'default model'}"
        if project_name:
            tooltip += f"\n{project_name}"
        if directory:
            tooltip += f"\n{directory}"
        item.setToolTip(0, tooltip)
        color = (agent_colors or {}).get(session.agent, "#98c379")
        from .sprites import critter_pixmap

        item.setIcon(0, QIcon(critter_pixmap(session.agent, color, scale=2)))
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
        elif item.data(0, _KIND_ROLE) == "project":
            self.open_project.emit(item.data(0, _ID_ROLE))

    def _on_current_changed(self, current: QTreeWidgetItem, _prev=None) -> None:
        # don't switch when a multi-selection is in play (would fight the user)
        if len(self.tree.selectedItems()) > 1:
            return
        sid = self._session_id(current)
        if sid:
            self.session_selected.emit(sid)

    def _on_double_click(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _KIND_ROLE) == "project":
            self.open_project.emit(item.data(0, _ID_ROLE))

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            ids = self._selected_session_ids()
            if ids and self._confirm_delete(len(ids)):
                self.delete_sessions.emit(ids)
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
            open_ws = menu.addAction("Open project")
            new_here = menu.addAction("New session in this project")
            settings = menu.addAction("Project settings…")
            rename_proj = menu.addAction("Rename project…")
            delete_proj = menu.addAction("Delete project…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is open_ws:
                self.open_project.emit(item.data(0, _ID_ROLE))
            elif chosen is new_here:
                self.new_session.emit(item.data(0, _ID_ROLE))
            elif chosen is settings:
                self.open_project_settings.emit(item.data(0, _ID_ROLE))
            elif chosen is rename_proj:
                self.rename_project.emit(item.data(0, _ID_ROLE))
            elif chosen is delete_proj and self._confirm_delete_project():
                self.delete_project.emit(item.data(0, _ID_ROLE))
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
            "Delete this project and ALL its sessions, history, and knowledge?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

    # -- filter / drag ------------------------------------------------------

    def show_search_results(self, results: list, running: set,
                            agent_colors: dict | None) -> None:
        """Replace the tree with flat full-text matches (title + snippet)."""
        from .delegates import META_ROLE

        self.tree.blockSignals(True)
        self.tree.clear()
        header = self._header_item(
            f"Search results  ({len(results)})" if results else "No matches")
        self.tree.addTopLevelItem(header)
        for session, snippet in results:
            item = self._session_item(session, running, agent_colors)
            if snippet:
                item.setData(0, META_ROLE, f"…{snippet}…")
                item.setToolTip(0, snippet)
            header.addChild(item)
        header.setExpanded(True)
        self.tree.blockSignals(False)

    def _filter(self, text: str) -> None:
        if text != self._last_query:
            self._search_timer.start()  # debounce the content search
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

