"""Main window: sidebar · transcript · crew stage · prompt bar (spec §7.1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..engine.events import (
    AgentRegistryChanged,
    PartUpdated,
    PermissionAsked,
    QuestionAsked,
    RunFinished,
    SessionUpdated,
    TaskFinished,
    TaskStarted,
    TaskUpdated,
    TodoUpdated,
)
from ..engine.facade import Engine
from ..engine.permissions import ASK_MODE, FULL_AUTO, SAFE
from ..engine.providers.catalog import CATALOG
from .agent_editor import AgentEditor
from .crew_stage import CrewStage
from .permission_dialog import PermissionDialog
from .prompt_bar import PromptBar
from .question_dialog import QuestionDialog
from .session_list import SessionList
from .settings import SettingsDialog
from .todo_strip import TodoStrip
from .transcript import Transcript


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.current_session_id: str | None = None
        self._session_stack: list[str] = []  # for back-navigation into subagents
        self._running: set[str] = set()

        self.setWindowTitle(f"Crew — {engine.project_dir.name}")
        self.resize(1200, 800)

        # widgets
        self.session_list = SessionList()
        self.transcript = Transcript()
        self.crew_stage = CrewStage()
        self.todo_strip = TodoStrip()
        self.prompt_bar = PromptBar(commands=engine.commands, project_dir=engine.project_dir)
        self.back_button = QPushButton("← back to parent session")
        self.back_button.setVisible(False)
        self.back_button.clicked.connect(self._go_back)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(self.back_button)
        right_layout.addWidget(self.transcript, 1)
        right_layout.addWidget(self.crew_stage)
        right_layout.addWidget(self.todo_strip)
        right_layout.addWidget(self.prompt_bar)

        splitter = QSplitter()
        splitter.addWidget(self.session_list)
        splitter.addWidget(right)
        splitter.setSizes([240, 960])
        self.setCentralWidget(splitter)

        # status bar
        self.status_project = QLabel(str(engine.project_dir))
        self.status_git = QLabel("")
        self.status_cost = QLabel("")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Safe", "Ask", "Full-auto"])
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        settings_button = QPushButton("⚙")
        settings_button.setFlat(True)
        settings_button.clicked.connect(self._open_settings)
        self.statusBar().addWidget(self.status_project)
        self.statusBar().addWidget(QLabel("·"))
        self.statusBar().addWidget(self.status_git)
        self.statusBar().addPermanentWidget(self.status_cost)
        self.statusBar().addPermanentWidget(self.mode_combo)
        self.statusBar().addPermanentWidget(settings_button)

        # wiring
        self.session_list.session_selected.connect(self._open_session)
        self.session_list.new_session.connect(lambda: self._fire(self._new_session()))
        self.transcript.open_session.connect(self._open_child)
        self.crew_stage.open_session.connect(self._open_child)
        self.prompt_bar.submitted.connect(self._submit)
        self.prompt_bar.aborted.connect(lambda: self._fire(self._abort()))
        self.prompt_bar.agent_changed.connect(self._agent_changed)
        self.prompt_bar.model_changed.connect(self._model_changed)
        self.prompt_bar.action_requested.connect(self._action)

        self._refresh_pickers()

    # ------------------------------------------------------------------ startup

    async def bootstrap(self) -> None:
        sessions = await self.engine.store.list_sessions(self.engine.project.id)
        if sessions:
            await self._load_session(sessions[0].id)
        else:
            await self._new_session()
        await self._refresh_sessions()
        await self._refresh_git()
        self._tasks = [
            asyncio.ensure_future(self._event_loop()),
            asyncio.ensure_future(self._watch_files()),
        ]

    def closeEvent(self, event) -> None:
        for task in getattr(self, "_tasks", []):
            task.cancel()
        super().closeEvent(event)

    # ------------------------------------------------------------------ events

    async def _event_loop(self) -> None:
        agent_colors = {a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()}
        async for event in self.engine.bus.subscribe():
            try:
                if isinstance(event, PartUpdated):
                    if event.session_id == self.current_session_id:
                        self.transcript.on_part_updated(event.part_id, event.part_type, event.data)
                elif isinstance(event, SessionUpdated):
                    await self._refresh_sessions()
                    await self._refresh_cost()
                elif isinstance(event, TodoUpdated):
                    if event.session_id == self.current_session_id:
                        self.todo_strip.set_todos(event.todos)
                    self.crew_stage.handle_event(event, agent_colors)
                elif isinstance(event, (TaskStarted, TaskUpdated, TaskFinished)):
                    if event.session_id == self.current_session_id:
                        self.crew_stage.handle_event(event, agent_colors)
                    if isinstance(event, TaskStarted):
                        self._running.add(event.subagent_session_id)
                    elif isinstance(event, TaskFinished):
                        self._running.discard(event.subagent_session_id)
                    await self._refresh_sessions()
                elif isinstance(event, RunFinished):
                    self._running.discard(event.session_id)
                    if event.session_id == self.current_session_id:
                        self.prompt_bar.set_running(False)
                        self.crew_stage.handle_event(event)
                    await self._refresh_sessions()
                    await self._refresh_git()
                elif isinstance(event, PermissionAsked):
                    dialog = PermissionDialog(event.tool, event.arg, event.input, self)
                    dialog.exec()
                    self.engine.answer_permission(event.request_id, dialog.verdict)
                elif isinstance(event, QuestionAsked):
                    dialog = QuestionDialog(event.question, event.options, self)
                    dialog.exec()
                    self.engine.answer_question(event.request_id, dialog.answer or "(no answer)")
                elif isinstance(event, AgentRegistryChanged):
                    agent_colors = {
                        a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()
                    }
                    self._refresh_pickers()
            except Exception:  # keep the loop alive no matter what
                import traceback

                traceback.print_exc()

    async def _watch_files(self) -> None:
        """Hot-reload agents/commands/config on file changes (spec §4.3)."""
        try:
            from watchfiles import awatch
        except ImportError:
            return
        paths = []
        for base in (self.engine.global_dir, self.engine.project_dir / ".crew"):
            base.mkdir(parents=True, exist_ok=True)
            paths.append(str(base))
        try:
            async for _changes in awatch(*paths):
                self.engine.reload_config()
                self.engine.bus.emit(AgentRegistryChanged())
        except Exception:
            pass

    # ------------------------------------------------------------------ sessions

    async def _new_session(self) -> None:
        session = await self.engine.new_session()
        await self._load_session(session.id)
        await self._refresh_sessions()

    async def _load_session(self, session_id: str) -> None:
        self.current_session_id = session_id
        session = await self.engine.store.get_session(session_id)
        history = await self.engine.store.session_parts(session_id)
        rows = []
        for message, parts in history:
            for part in parts:
                rows.append((message, part))
        self.transcript.load_history(history)
        todos = await self.engine.store.get_todos(session_id)
        self.todo_strip.set_todos([{"content": t.content, "status": t.status} for t in todos])
        self.prompt_bar.set_running(self.engine.is_running(session_id))
        self.back_button.setVisible(session.parent_session_id is not None)
        self._refresh_pickers(session.agent, session.model)
        await self._refresh_cost()

    def _open_session(self, session_id: str) -> None:
        self._session_stack.clear()
        self._fire(self._load_session(session_id))

    def _open_child(self, session_id: str) -> None:
        if self.current_session_id:
            self._session_stack.append(self.current_session_id)
        self._fire(self._load_session(session_id))

    def _go_back(self) -> None:
        if self._session_stack:
            self._fire(self._load_session(self._session_stack.pop()))

    async def _refresh_sessions(self) -> None:
        sessions = await self.engine.store.list_sessions(self.engine.project.id)
        children = {}
        for session in sessions:
            kids = await self.engine.store.child_sessions(session.id)
            if kids:
                children[session.id] = kids
        running = {s.id for s in sessions if self.engine.is_running(s.id)} | self._running
        self.session_list.populate(sessions, children, running, self.current_session_id or "")

    # ------------------------------------------------------------------ prompt

    def _submit(self, text: str) -> None:
        if not self.current_session_id:
            return
        self.prompt_bar.set_running(True)
        self.transcript.add_user_text(f"local-{id(text)}", self._display_text(text))
        self._fire(self.engine.submit_prompt(
            self.current_session_id, text,
            agent=self.prompt_bar.agent_combo.currentText() or None,
            model=self.prompt_bar.model_combo.currentText() or None,
        ))

    @staticmethod
    def _display_text(text: str) -> str:
        return text

    async def _abort(self) -> None:
        if self.current_session_id:
            await self.engine.abort(self.current_session_id)
            self.prompt_bar.set_running(False)

    def _action(self, action: str) -> None:
        if action == "compact" and self.current_session_id:
            self._fire(self.engine.compact(self.current_session_id))
        elif action == "new_session":
            self._fire(self._new_session())
        elif action == "open_model_picker":
            self.prompt_bar.model_combo.showPopup()
        elif action == "open_agent_editor":
            self._open_agent_editor()

    def _agent_changed(self, agent: str) -> None:
        if self.current_session_id and agent:
            self._fire(self.engine.store.update_session(self.current_session_id, agent=agent))

    def _model_changed(self, model: str) -> None:
        if self.current_session_id and model:
            self._fire(self.engine.store.update_session(self.current_session_id, model=model))

    # ------------------------------------------------------------------ pickers & status

    def _refresh_pickers(self, agent: str = "", model: str = "") -> None:
        agents = [a.name for a in self.engine.agents.primaries()]
        self.prompt_bar.set_agents(agents, agent or "build")
        models = []
        for provider_name in self.engine.config.provider:
            for model_id in CATALOG:
                if provider_name == "anthropic" and model_id.startswith("claude"):
                    models.append(f"{provider_name}/{model_id}")
                elif provider_name == "openai" and not model_id.startswith("claude"):
                    models.append(f"{provider_name}/{model_id}")
        models.insert(0, self.engine.config.model)
        self.prompt_bar.set_models(list(dict.fromkeys(models)), model or self.engine.config.model)

    async def _refresh_cost(self) -> None:
        if not self.current_session_id:
            return
        messages = await self.engine.store.list_messages(self.current_session_id)
        tokens = sum(m.tokens_in + m.tokens_out for m in messages)
        cost = sum(m.cost for m in messages)
        self.status_cost.setText(f"{tokens:,} tok · ${cost:.3f}")

    async def _refresh_git(self) -> None:
        proc = await asyncio.create_subprocess_shell(
            "git branch --show-current",
            cwd=str(self.engine.project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        self.status_git.setText(out.decode().strip() or "no git")

    def _mode_changed(self, index: int) -> None:
        self.engine.session_mode = [SAFE, ASK_MODE, FULL_AUTO][index]

    # ------------------------------------------------------------------ dialogs

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.engine, self)
        dialog.exec()

    def _open_agent_editor(self) -> None:
        editor = AgentEditor(self.engine, self)
        editor.exec()

    # ------------------------------------------------------------------ util

    @staticmethod
    def _fire(coro) -> None:
        asyncio.ensure_future(coro)
