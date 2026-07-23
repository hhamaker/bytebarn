"""Project workspace: the deliberate single-project view.

Clicking a project opens this panel in place of the all-projects sidebar.
Everything that belongs to the project lives here in tabs: Chats, Goals
(orchestrator runs with todo progress), Memory (the OKF bundle), and Agents
(per-project defaults). The transcript stays on the right, unchanged."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .session_list import relative_time

_RUNNING_COLOR = "#98c379"
_ID_ROLE = Qt.UserRole


class ProjectWorkspace(QWidget):
    back_requested = Signal()
    session_selected = Signal(str)
    new_chat = Signal(str)          # project id
    rename_session = Signal(str)
    delete_session = Signal(str)
    open_settings = Signal(str)     # project id -> ProjectDialog

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.project_id = ""

        back = QPushButton("← All projects")
        back.setFlat(True)
        back.clicked.connect(self.back_requested)
        self.title = QLabel("<b>Project</b>")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(back)
        header.addWidget(self.title, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._chats_tab(), "Chats")
        self.tabs.addTab(self._goals_tab(), "Goals")
        self.tabs.addTab(self._memory_tab(), "Memory")
        self.tabs.addTab(self._agents_tab(), "Agents")

        settings = QPushButton("Project settings…")
        settings.clicked.connect(lambda: self.open_settings.emit(self.project_id))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(settings)

    # -- tabs ----------------------------------------------------------------

    def _chats_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)
        from .delegates import SessionRowDelegate

        new_btn = QPushButton("+ New chat")
        new_btn.clicked.connect(lambda: self.new_chat.emit(self.project_id))
        self.chat_list = QListWidget()
        self.chat_list.setMouseTracking(True)
        self.chat_list.setItemDelegate(SessionRowDelegate(self.chat_list))
        self.chat_list.itemClicked.connect(self._chat_clicked)
        self.chat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_menu)
        layout.addWidget(new_btn)
        layout.addWidget(self.chat_list, 1)
        return w

    def _goals_tab(self) -> QWidget:
        from PySide6.QtWidgets import QLineEdit

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addWidget(QLabel("<b>Queue</b> — goals run one after another;"
                                " walk away, get notified"))
        row = QHBoxLayout()
        self.goal_input = QLineEdit()
        self.goal_input.setPlaceholderText("Describe a goal to queue…")
        self.goal_input.returnPressed.connect(self._queue_goal)
        queue_btn = QPushButton("+ Queue")
        queue_btn.clicked.connect(self._queue_goal)
        row.addWidget(self.goal_input, 1)
        row.addWidget(queue_btn)
        layout.addLayout(row)

        self.queue_list = QListWidget()
        self.queue_list.itemClicked.connect(self._chat_clicked)
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._queue_menu)
        layout.addWidget(self.queue_list, 1)

        hint = QLabel("Past goal runs (orchestrator) and their plan progress.")
        hint.setStyleSheet("color:#8f96a3")
        hint.setWordWrap(True)
        self.goal_list = QListWidget()
        self.goal_list.itemClicked.connect(self._chat_clicked)
        layout.addWidget(hint)
        layout.addWidget(self.goal_list, 1)

        # routines: recurring goals on a schedule (app must be running)
        layout.addWidget(QLabel("<b>Routines</b> — run a goal on a schedule"
                                " while ByteBarn is open"))
        routine_row = QHBoxLayout()
        self.routine_input = QLineEdit()
        self.routine_input.setPlaceholderText("Prompt to run repeatedly…")
        self.routine_interval = QComboBox()
        for label in ("every 30 min", "every hour", "every 3 hours",
                      "every 6 hours", "daily"):
            self.routine_interval.addItem(label)
        add_routine = QPushButton("+ Routine")
        add_routine.clicked.connect(self._add_routine)
        routine_row.addWidget(self.routine_input, 1)
        routine_row.addWidget(self.routine_interval)
        routine_row.addWidget(add_routine)
        layout.addLayout(routine_row)
        self.routine_list = QListWidget()
        self.routine_list.setMaximumHeight(110)
        self.routine_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.routine_list.customContextMenuRequested.connect(self._routine_menu)
        layout.addWidget(self.routine_list)
        return w

    _INTERVALS = {"every 30 min": 1800, "every hour": 3600, "every 3 hours": 10800,
                  "every 6 hours": 21600, "daily": 86400}

    def _add_routine(self) -> None:
        prompt = self.routine_input.text().strip()
        if not prompt:
            return
        self.routine_input.clear()
        interval = self._INTERVALS[self.routine_interval.currentText()]
        project_id = self.project_id

        async def run() -> None:
            await self.engine.store.add_routine(project_id, prompt, interval)
            await self.load(project_id)

        asyncio.ensure_future(run())

    def _routine_menu(self, pos) -> None:
        item = self.routine_list.itemAt(pos)
        if item is None:
            return
        routine_id = item.data(Qt.UserRole)
        enabled = item.data(Qt.UserRole + 1)
        menu = QMenu(self)
        toggle = menu.addAction("Pause" if enabled else "Resume")
        run_now = menu.addAction("Run now")
        remove = menu.addAction("Delete routine")
        chosen = menu.exec(self.routine_list.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        async def run() -> None:
            if chosen is toggle:
                await self.engine.store.update_routine(routine_id, enabled=0 if enabled else 1)
            elif chosen is remove:
                await self.engine.store.delete_routine(routine_id)
            elif chosen is run_now:
                routines = await self.engine.store.list_routines(self.project_id)
                match = next((r for r in routines if r.id == routine_id), None)
                if match:
                    await self.engine.queue_goal(match.prompt, self.project_id)
            await self.load(self.project_id)

        asyncio.ensure_future(run())

    _STATUS_ICON = {"pending": "◻", "running": "●", "done": "✓",
                    "error": "✗", "cancelled": "–"}

    def _queue_goal(self) -> None:
        prompt = self.goal_input.text().strip()
        if not prompt:
            return
        self.goal_input.clear()
        project_id = self.project_id

        async def run() -> None:
            await self.engine.queue_goal(prompt, project_id=project_id)
            await self.load(project_id)

        asyncio.ensure_future(run())

    def _queue_menu(self, pos) -> None:
        item = self.queue_list.itemAt(pos)
        if item is None or item.data(Qt.UserRole + 1) != "pending":
            return
        menu = QMenu(self)
        cancel = menu.addAction("Cancel goal")
        chosen = menu.exec(self.queue_list.viewport().mapToGlobal(pos))
        if chosen is cancel:
            goal_id = item.data(Qt.UserRole + 2)

            async def run() -> None:
                await self.engine.cancel_goal(goal_id)
                await self.load(self.project_id)

            asyncio.ensure_future(run())

    def _memory_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)
        hint = QLabel("Persistent project memory (OKF bundle). Agents read this"
                      " every run and add to it as they learn.")
        hint.setStyleSheet("color:#8f96a3")
        hint.setWordWrap(True)

        self.memory_files = QListWidget()
        self.memory_files.currentItemChanged.connect(self._memory_file_selected)
        self.memory_editor = QPlainTextEdit()
        self.memory_editor.setPlaceholderText("Select a memory file, or New to add one.")
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.memory_files)
        split.addWidget(self.memory_editor)
        split.setStretchFactor(1, 1)

        row = QHBoxLayout()
        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self._memory_new)
        self.memory_save = QPushButton("Save")
        self.memory_save.clicked.connect(self._memory_save)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._memory_delete)
        row.addWidget(new_btn)
        row.addWidget(self.memory_save)
        row.addWidget(del_btn)
        row.addStretch(1)

        layout.addWidget(hint)
        layout.addWidget(split, 1)
        layout.addLayout(row)
        return w

    def _agents_tab(self) -> QWidget:
        from .model_picker import ModelPicker

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addWidget(QLabel("<b>Project defaults</b> — applied to new chats"
                                " in this project"))
        self.default_agent = QComboBox()
        self.default_agent.addItem("(default)")
        self.default_agent.addItems(sorted(self.engine.agents.agents.keys()))
        row = QHBoxLayout()
        row.addWidget(QLabel("Agent:"))
        row.addWidget(self.default_agent, 1)
        layout.addLayout(row)

        layout.addWidget(QLabel("Model:"))
        self.default_model = ModelPicker(self.engine, allow_default=True)
        layout.addWidget(self.default_model)

        save = QPushButton("Save defaults")
        save.clicked.connect(self._save_defaults)
        layout.addWidget(save)

        per_session = QLabel(
            "Per-session config: every chat keeps its own agent and model —"
            " switch them any time in the prompt bar below the transcript.")
        per_session.setStyleSheet("color:#8f96a3")
        per_session.setWordWrap(True)
        layout.addWidget(per_session)

        edit = QPushButton("Edit agents (prompts, tools, colors)…")
        edit.clicked.connect(self._open_agent_editor)
        layout.addWidget(edit)
        layout.addStretch(1)
        return w

    # -- population ----------------------------------------------------------

    async def load(self, project_id: str) -> None:
        """(Re)load every tab for the project."""
        self.project_id = project_id
        project = await self.engine.store.get_project(project_id)
        if project is None:
            return
        self.title.setText(f"<b>{project.name}</b>")

        sessions = [s for s in await self.engine.store.list_sessions(project_id)]
        agent_colors = {a.name: a.color or "#98c379"
                        for a in self.engine.agents.agents.values()}

        self.queue_list.clear()
        for goal in await self.engine.store.list_goals(project_id):
            icon = self._STATUS_ICON.get(goal.status, "?")
            item = QListWidgetItem(f"{icon} {goal.prompt[:70]}")
            item.setData(_ID_ROLE, goal.session_id or "")
            item.setData(Qt.UserRole + 1, goal.status)
            item.setData(Qt.UserRole + 2, goal.id)
            item.setToolTip(f"{goal.status} — {goal.prompt}")
            if goal.status == "running":
                item.setForeground(QColor(_RUNNING_COLOR))
            self.queue_list.addItem(item)

        self.routine_list.clear()
        import time as _time

        for routine in await self.engine.store.list_routines(project_id):
            state = "⏸ " if not routine.enabled else ""
            due_min = max(0, int((routine.next_run - _time.time()) / 60))
            label = next((k for k, v in self._INTERVALS.items()
                          if v == routine.interval_s), f"{routine.interval_s}s")
            item = QListWidgetItem(
                f"{state}⟳ {routine.prompt[:60]} — {label}"
                + ("" if not routine.enabled else f" · next in {due_min}m"))
            item.setData(Qt.UserRole, routine.id)
            item.setData(Qt.UserRole + 1, routine.enabled)
            item.setToolTip(routine.prompt)
            self.routine_list.addItem(item)

        self.chat_list.clear()
        self.goal_list.clear()
        for s in sessions:
            self.chat_list.addItem(self._session_item(s, agent_colors))
            if s.agent == "orchestrator":
                todos = await self.engine.store.get_todos(s.id)
                done = sum(1 for t in todos if t.status == "completed")
                label = f"{s.title or '(goal)'} — {done}/{len(todos)} todos done" \
                    if todos else (s.title or "(goal)")
                item = QListWidgetItem(label)
                item.setData(_ID_ROLE, s.id)
                self.goal_list.addItem(item)

        self._reload_memory_files()

        self.default_agent.setCurrentText(project.default_agent or "(default)")
        self.default_model.set_model(project.default_model)

    def _session_item(self, session: Any, agent_colors: dict) -> QListWidgetItem:
        from .delegates import KIND_ROLE, META_ROLE, RUNNING_ROLE, TITLE_ROLE

        running = self.engine.is_running(session.id)
        title = session.title or "(untitled)"
        label = f"{'● ' if running else ''}{title}" \
                f" · {relative_time(session.updated_at)}"
        item = QListWidgetItem(label)
        item.setData(_ID_ROLE, session.id)
        item.setData(KIND_ROLE, "session")
        item.setData(TITLE_ROLE, title)
        item.setData(META_ROLE,
                     f"{session.agent} · {relative_time(session.updated_at)}")
        item.setData(RUNNING_ROLE, running)
        from .sprites import critter_pixmap

        color = agent_colors.get(session.agent, "#98c379")
        item.setIcon(QIcon(critter_pixmap(session.agent, color, scale=2)))
        return item

    # -- chats ---------------------------------------------------------------

    def _chat_clicked(self, item: QListWidgetItem) -> None:
        sid = item.data(_ID_ROLE)
        if sid:
            self.session_selected.emit(sid)

    def _chat_menu(self, pos) -> None:
        item = self.chat_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        delete = menu.addAction("Delete…")
        chosen = menu.exec(self.chat_list.viewport().mapToGlobal(pos))
        if chosen is rename:
            self.rename_session.emit(item.data(_ID_ROLE))
        elif chosen is delete:
            answer = QMessageBox.warning(
                self, "Delete session",
                "Permanently delete this session and all history?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer == QMessageBox.Yes:
                self.delete_session.emit(item.data(_ID_ROLE))

    # -- memory --------------------------------------------------------------

    def _memory_root(self) -> Path:
        return self.engine.memory_dir(self.project_id)

    def _reload_memory_files(self) -> None:
        self.memory_files.clear()
        self.memory_editor.setPlainText("")
        root = self._memory_root()
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            item = QListWidgetItem(("📒 " if rel == "log.md" else "📄 ") + rel)
            item.setData(_ID_ROLE, rel)
            self.memory_files.addItem(item)

    def _memory_file_selected(self, item: QListWidgetItem, _prev=None) -> None:
        if item is None:
            return
        rel = item.data(_ID_ROLE)
        path = self._memory_root() / rel
        try:
            self.memory_editor.setPlainText(path.read_text())
        except OSError:
            self.memory_editor.setPlainText("")
        # log.md is written by the memory tool, not by hand
        self.memory_editor.setReadOnly(rel == "log.md")
        self.memory_save.setEnabled(rel != "log.md")

    def _memory_new(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "New memory", "File name (e.g. decisions/database.md):")
        name = name.strip().lstrip("/")
        if not ok or not name:
            return
        if not name.endswith(".md"):
            name += ".md"
        root = self._memory_root()
        target = (root / name).resolve()
        if not str(target).startswith(str(root.resolve())):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text('---\ntype: "Note"\n---\n\n')
        self._reload_memory_files()
        for i in range(self.memory_files.count()):
            if self.memory_files.item(i).data(_ID_ROLE) == name:
                self.memory_files.setCurrentRow(i)
                break

    def _memory_save(self) -> None:
        item = self.memory_files.currentItem()
        if item is None or item.data(_ID_ROLE) == "log.md":
            return
        path = self._memory_root() / item.data(_ID_ROLE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.memory_editor.toPlainText())

    def _memory_delete(self) -> None:
        item = self.memory_files.currentItem()
        if item is None:
            return
        answer = QMessageBox.question(
            self, "Delete memory", f"Delete {item.data(_ID_ROLE)}?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        (self._memory_root() / item.data(_ID_ROLE)).unlink(missing_ok=True)
        self._reload_memory_files()

    # -- agents --------------------------------------------------------------

    def _save_defaults(self) -> None:
        agent = self.default_agent.currentText()
        if agent == "(default)":
            agent = ""
        model = self.default_model.value()
        project_id = self.project_id

        async def run() -> None:
            await self.engine.store.set_project_defaults(project_id, agent, model)

        asyncio.ensure_future(run())

    def _open_agent_editor(self) -> None:
        from .agent_editor import AgentEditor

        AgentEditor(self.engine, self).exec()
