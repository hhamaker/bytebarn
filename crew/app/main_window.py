"""Main window: sidebar · transcript · crew stage · prompt bar (spec §7.1)."""

from __future__ import annotations

import asyncio
import html
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
    QueueUpdated,
    RunFinished,
    SessionActivity,
    SessionUpdated,
    TaskFinished,
    TaskStarted,
    TaskUpdated,
    TodoUpdated,
)
from ..engine.facade import Engine
from ..engine.permissions import ASK_MODE, FULL_AUTO, SAFE
from ..engine.providers.known import connected_providers, curated_models
from .agent_editor import AgentEditor
from .crew_stage import CrewStage
from .permission_dialog import PermissionDialog
from .prompt_bar import PromptBar
from .question_dialog import QuestionDialog
from .session_list import SessionList
from .settings import SettingsDialog
from .todo_strip import TodoStrip
from .transcript import Transcript


# UI display names for agents: the app itself is the orchestrator, so the
# picker offers "goal" mode instead of exposing the internal agent name
_AGENT_DISPLAY = {"orchestrator": "goal"}
_AGENT_INTERNAL = {v: k for k, v in _AGENT_DISPLAY.items()}


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.current_session_id: str | None = None
        self._session_stack: list[str] = []  # for back-navigation into subagents
        self._running: set[str] = set()
        self._activity: str = ""  # live run detail for the open session

        self.setWindowTitle("Crew")
        self.resize(1200, 800)

        # widgets
        self.session_list = SessionList()
        self.transcript = Transcript()
        self.crew_stage = CrewStage()
        self.todo_strip = TodoStrip()
        self.prompt_bar = PromptBar(commands=engine.commands, project_dir=engine.project_dir)

        # session header: [←] [critter] title  agent · model
        self.back_button = QPushButton("←")
        self.back_button.setFlat(True)
        self.back_button.setFixedWidth(28)
        self.back_button.setToolTip("Back to parent session")
        self.back_button.setVisible(False)
        self.back_button.clicked.connect(self._go_back)
        self.header_icon = QLabel("")
        self.header_title = QLabel("")
        self.header_meta = QLabel("")
        self.header_meta.setStyleSheet("color: #8f96a3;")
        self.dir_button = QPushButton("")
        self.dir_button.setFlat(True)
        self.dir_button.setToolTip("Working directory for this session — click to change")
        self.dir_button.clicked.connect(self._pick_directory)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 0)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.back_button)
        header_layout.addWidget(self.header_icon)
        header_layout.addWidget(self.header_title)
        header_layout.addWidget(self.header_meta, 1)
        header_layout.addWidget(self.dir_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(header)
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
        self.mode_combo.setMinimumWidth(90)
        self.mode_combo.setSizePolicy(self.mode_combo.sizePolicy().horizontalPolicy(),
                                      self.mode_combo.sizePolicy().verticalPolicy())
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.mode_combo.setToolTip(
            "Permission mode — Safe: read-only · Ask: confirm risky tools · "
            "Full-auto: no prompts")
        providers_button = QPushButton("⚡ providers")
        providers_button.setFlat(True)
        providers_button.setToolTip("Connect LLM providers (API keys, web login, local servers)")
        providers_button.clicked.connect(self._open_providers)
        agents_button = QPushButton("🐾 agents")
        agents_button.setFlat(True)
        agents_button.setToolTip("Manage agents: models, prompts, tools, colors")
        agents_button.clicked.connect(self._open_agent_editor)
        settings_button = QPushButton("⚙ settings")
        settings_button.setFlat(True)
        settings_button.setToolTip("Default models, permissions, theme")
        settings_button.clicked.connect(self._open_settings)
        self.statusBar().addWidget(self.status_project)
        self.statusBar().addWidget(QLabel("·"))
        self.statusBar().addWidget(self.status_git)
        self.statusBar().addPermanentWidget(self.status_cost)
        self.statusBar().addPermanentWidget(providers_button)
        self.statusBar().addPermanentWidget(agents_button)
        self.statusBar().addPermanentWidget(self.mode_combo)
        self.statusBar().addPermanentWidget(settings_button)

        self._build_menus()

        # wiring
        self.session_list.session_selected.connect(self._open_session)
        self.session_list.new_session.connect(lambda: self._fire(self._new_session()))
        self.session_list.close_session.connect(
            lambda sid: self._fire(self._close_session(sid)))
        self.session_list.delete_session.connect(
            lambda sid: self._fire(self._delete_session(sid)))
        self.transcript.open_session.connect(self._open_child)
        self.crew_stage.open_session.connect(self._open_child)
        self.prompt_bar.submitted.connect(self._submit)
        self.prompt_bar.aborted.connect(lambda: self._fire(self._abort()))
        self.prompt_bar.agent_changed.connect(self._agent_changed)
        self.prompt_bar.provider_changed.connect(self._provider_changed)
        self.prompt_bar.model_changed.connect(self._model_changed)
        self.prompt_bar.action_requested.connect(self._action)

        self._refresh_pickers()

    def _build_menus(self) -> None:
        """Native menu bar (on macOS this fills the "Crew" application menu)."""
        from PySide6.QtGui import QAction, QKeySequence

        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        new_action = QAction("New Session…", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(lambda: self._fire(self._new_session()))
        close_action = QAction("Close Session", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(
            lambda: self.current_session_id
            and self._fire(self._close_session(self.current_session_id)))
        quit_action = QAction("Quit Crew", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.setMenuRole(QAction.QuitRole)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(new_action)
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        session_menu = bar.addMenu("&Session")
        stop_action = QAction("Stop Run", self)
        stop_action.setShortcut(QKeySequence("Ctrl+."))
        stop_action.triggered.connect(lambda: self._fire(self._abort()))
        compact_action = QAction("Compact Context", self)
        compact_action.triggered.connect(
            lambda: self.current_session_id
            and self._fire(self.engine.compact(self.current_session_id)))
        delete_action = QAction("Delete Session…", self)
        delete_action.triggered.connect(
            lambda: self.current_session_id
            and self._fire(self._delete_session(self.current_session_id)))
        session_menu.addAction(stop_action)
        session_menu.addAction(compact_action)
        session_menu.addSeparator()
        session_menu.addAction(delete_action)

        tools_menu = bar.addMenu("&Tools")
        providers_action = QAction("Providers…", self)
        providers_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        providers_action.triggered.connect(self._open_providers)
        agents_action = QAction("Agents…", self)
        agents_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        agents_action.triggered.connect(self._open_agent_editor)
        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence.Preferences)
        settings_action.setMenuRole(QAction.PreferencesRole)  # macOS app menu
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(providers_action)
        tools_menu.addAction(agents_action)
        tools_menu.addSeparator()
        tools_menu.addAction(settings_action)

        help_menu = bar.addMenu("&Help")
        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)
        about_action = QAction("About Crew", self)
        about_action.setMenuRole(QAction.AboutRole)  # macOS app menu
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(shortcuts_action)
        help_menu.addAction(about_action)

    def _show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "Keyboard Shortcuts", (
            "<b>Prompt</b><br>"
            "Enter — send · Shift+Enter — newline · Esc — stop run<br>"
            "/ — command palette · @ — attach file<br><br>"
            "<b>Sessions</b><br>"
            "⌘N — new · ⌘W — close · ↑/↓ in sidebar — switch<br><br>"
            "<b>Tools</b><br>"
            "⌘⇧P — providers · ⌘⇧A — agents · ⌘, — settings"
        ))

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(self, "About Crew", (
            "<h3>Crew</h3>"
            "<p>A local desktop app that runs AI coding agents — a crew of"
            " pixel-art critters — against your own codebases.</p>"
            "<p>Everything runs locally; the only network traffic is to the"
            " LLM providers you connect.</p>"
        ))

    # ------------------------------------------------------------------ startup

    async def bootstrap(self) -> None:
        sessions = await self.engine.store.list_sessions(self.engine.project.id)
        if sessions:
            await self._load_session(sessions[0].id)
        else:
            self._show_no_session()
        await self._refresh_sessions()
        await self._refresh_git()
        self._tasks = [
            asyncio.ensure_future(self._event_loop()),
            asyncio.ensure_future(self._watch_files()),
        ]
        self._maybe_first_run()
        # first launch (or all sessions deleted): a session needs a directory,
        # so open the picker instead of silently defaulting somewhere
        if not sessions:
            await self._new_session()

    def _show_no_session(self) -> None:
        """No session open: welcome screen, prompt disabled, empty header."""
        self.current_session_id = None
        self.transcript.load_history([])
        self.todo_strip.set_todos([])
        self.prompt_bar.set_running(False)
        self.back_button.setVisible(False)
        self.header_icon.clear()
        self.header_title.setText("<b>No session</b>")
        self.header_meta.setText("New Session (⌘N) to start")
        self.dir_button.setText("")

    def _maybe_first_run(self) -> None:
        """One-time welcome wizard; "onboarded" flag persists the choice."""
        if (self.engine.config.model_extra or {}).get("onboarded"):
            return
        from PySide6.QtWidgets import QApplication

        if QApplication.instance().platformName() == "offscreen":
            return  # headless tests/scripts: never block on a modal
        from ..engine.config import patch_config_file
        from .first_run_wizard import FirstRunWizard

        wizard = FirstRunWizard(self)
        wizard.prompt_picked.connect(self.prompt_bar.editor.setPlainText)
        wizard.exec()
        patch_config_file(self.engine.global_dir / "config.json", {"onboarded": True})
        self.engine.reload_config()

    def closeEvent(self, event) -> None:
        """Window closed = application exits: stop the engine (aborts runs,
        closes the DB), then quit Qt so the qasync loop unwinds."""
        for task in getattr(self, "_tasks", []):
            task.cancel()

        async def _shutdown() -> None:
            from PySide6.QtWidgets import QApplication

            try:
                await self.engine.stop()
            except Exception:
                pass
            QApplication.instance().quit()

        asyncio.ensure_future(_shutdown())
        event.accept()
        super().closeEvent(event)

    # ------------------------------------------------------------------ events

    async def _event_loop(self) -> None:
        agent_colors = {a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()}
        async for event in self.engine.bus.subscribe():
            try:
                if isinstance(event, PartUpdated):
                    if event.session_id == self.current_session_id:
                        self.transcript.on_part_updated(event.part_id, event.part_type, event.data)
                        self._activity_from_part(event.part_type, event.data)
                elif isinstance(event, SessionActivity):
                    if event.session_id == self.current_session_id:
                        self._set_activity(event.detail)
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
                    # Parent may already have promoted a queued prompt (runner
                    # runs on_run_finished before emitting run.finished).
                    still = self.engine.is_running(event.session_id)
                    if still:
                        self._running.add(event.session_id)
                    if event.session_id == self.current_session_id:
                        self.prompt_bar.set_running(still)
                        self.transcript.dismiss_thinking()
                        self.transcript.finalize_streaming()
                        if still:
                            # queued prompt just promoted — keep stage/cast alive
                            self.transcript.promote_queued()
                            agent = self.prompt_bar.agent_combo.currentText() or "agent"
                            internal = _AGENT_INTERNAL.get(agent, agent)
                            color = self.engine.agents.color_of(internal)
                            self.transcript.show_thinking(agent, color)
                            self._set_activity("thinking…")
                        else:
                            self._set_activity("")
                            # only tear down the crew when this session is idle
                            self.crew_stage.handle_event(event)
                        self.prompt_bar.set_queue_depth(self.engine.queue_depth(event.session_id))
                    elif not still:
                        # subagent finished while viewing parent: stage keeps the
                        # member via task.finished; never forward child run.finished
                        pass
                    await self._refresh_sessions()
                    # git status only when the open session goes idle
                    if event.session_id == self.current_session_id and not still:
                        await self._refresh_git()
                elif isinstance(event, QueueUpdated):
                    if event.session_id == self.current_session_id:
                        self.prompt_bar.set_queue_depth(event.depth)
                elif isinstance(event, PermissionAsked):
                    if self.engine.session_mode == FULL_AUTO:
                        # switched to Full-auto after the ask was queued
                        self.engine.answer_permission(event.request_id, "allow")
                    else:
                        if event.session_id == self.current_session_id:
                            self._set_activity(f"waiting: {event.tool}")
                        dialog = PermissionDialog(event.tool, event.arg, event.input, self)
                        dialog.exec()
                        self.engine.answer_permission(event.request_id, dialog.verdict)
                elif isinstance(event, QuestionAsked):
                    if event.session_id == self.current_session_id:
                        self._set_activity("waiting for answer…")
                    dialog = QuestionDialog(event.question, event.options, self)
                    dialog.exec()
                    self.engine.answer_question(event.request_id, dialog.answer or "(no answer)")
                elif isinstance(event, AgentRegistryChanged):
                    agent_colors = {
                        a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()
                    }
                    # keep the session's current picks; only the lists refresh
                    self._refresh_pickers(
                        self.prompt_bar.agent_combo.currentText(),
                        self.prompt_bar.model_combo.currentText(),
                    )
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
        def _relevant(_change, path: str) -> bool:
            # the global dir also holds crew.db*/auth.json — DB writes must
            # not trigger config reload storms on every message
            return (
                path.endswith("config.json")
                or "/agent/" in path
                or "/command/" in path
            )

        try:
            async for _changes in awatch(*paths, watch_filter=_relevant):
                self.engine.reload_config()
                self.engine.bus.emit(AgentRegistryChanged())
        except Exception:
            pass

    # ------------------------------------------------------------------ sessions

    async def _new_session(self, directory: str | None = None) -> None:
        """Create a session. A working directory must be chosen explicitly —
        if not passed in, prompt for one; cancelling aborts creation."""
        if directory is None:
            directory = self._prompt_directory("Choose a working directory for the new session")
            if not directory:
                return
        self._remember_project(directory)
        session = await self.engine.new_session(
            model=self._default_model(), directory=directory)
        await self._load_session(session.id)
        await self._refresh_sessions()

    def _prompt_directory(self, caption: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        start = self._last_project() or str(Path.home())
        return QFileDialog.getExistingDirectory(self, caption, start)

    async def _load_session(self, session_id: str) -> None:
        session = await self.engine.store.get_session(session_id)
        if session is None:
            return
        self.current_session_id = session_id
        self._activity = "working…" if self.engine.is_running(session_id) else ""
        history = await self.engine.store.session_parts(session_id)
        rows = []
        for message, parts in history:
            for part in parts:
                rows.append((message, part))
        self.transcript.load_history(history)
        todos = await self.engine.store.get_todos(session_id)
        self.todo_strip.set_todos([{"content": t.content, "status": t.status} for t in todos])
        self.prompt_bar.set_running(self.engine.is_running(session_id))
        self.prompt_bar.set_queue_depth(self.engine.queue_depth(session_id))
        self.back_button.setVisible(session.parent_session_id is not None)
        self._update_header(session)
        self._refresh_pickers(session.agent, session.model)
        await self._refresh_cost()

    def _pick_directory(self) -> None:
        if not self.current_session_id:
            return
        from PySide6.QtWidgets import QFileDialog

        session_id = self.current_session_id
        picked = QFileDialog.getExistingDirectory(
            self, "Working directory for this session",
            str(self.engine.project_dir))
        if not picked:
            return

        self._remember_project(picked)

        async def apply() -> None:
            await self.engine.store.update_session(session_id, directory=picked)
            session = await self.engine.store.get_session(session_id)
            if session:
                self._update_header(session)

        self._fire(apply())

    def _remember_project(self, path: str) -> None:
        """Remember the last chosen folder to seed the next directory picker."""
        from ..engine.config import patch_config_file

        try:
            patch_config_file(self.engine.global_dir / "config.json",
                              {"last_project": path})
        except Exception:
            pass

    def _last_project(self) -> str:
        return (self.engine.config.model_extra or {}).get("last_project", "")

    def _open_session(self, session_id: str) -> None:
        if session_id == self.current_session_id:
            return  # selection refreshes must not reload the transcript
        self._session_stack.clear()
        self._fire(self._load_session(session_id))

    def _open_child(self, session_id: str) -> None:
        if self.current_session_id:
            self._session_stack.append(self.current_session_id)
        self._fire(self._load_session(session_id))

    def _go_back(self) -> None:
        if self._session_stack:
            self._fire(self._load_session(self._session_stack.pop()))

    async def _close_session(self, session_id: str) -> None:
        await self.engine.close_session(session_id)
        await self._after_session_removed(session_id)

    async def _delete_session(self, session_id: str) -> None:
        await self.engine.delete_session(session_id)
        await self._after_session_removed(session_id)

    async def _after_session_removed(self, session_id: str) -> None:
        """If the removed session was open, move to the next one (or a new one)."""
        self._session_stack = [s for s in self._session_stack if s != session_id]
        current_gone = self.current_session_id == session_id
        if self.current_session_id and not current_gone:
            # current may have been a child of the removed session
            current = await self.engine.store.get_session(self.current_session_id)
            current_gone = current is None or current.archived
        if current_gone:
            self.current_session_id = None
            sessions = await self.engine.store.list_sessions(self.engine.project.id)
            if sessions:
                await self._load_session(sessions[0].id)
            else:
                # nothing left — don't force a folder picker on the user here;
                # they can start one via New Session when ready
                self._show_no_session()
        await self._refresh_sessions()

    def _update_header(self, session) -> None:
        from .sprites import critter_pixmap

        color = self.engine.agents.color_of(session.agent)
        self.header_icon.setPixmap(critter_pixmap(session.agent, color, scale=2))
        self.header_title.setText(f"<b>{session.title or 'New session'}</b>")
        meta = (f"{_AGENT_DISPLAY.get(session.agent, session.agent)}"
                f" · {session.model or self._default_model()}")
        if self.engine.is_running(session.id) or session.id in self._running:
            detail = self._activity or "working…"
            meta += f"   <span style='color:#e5c07b'>● {html.escape(detail)}</span>"
        self.header_meta.setText(meta)
        directory = session.directory or str(self.engine.project_dir)
        self.status_project.setText(directory)
        self.setWindowTitle(f"Crew — {Path(directory).name}")
        self.dir_button.setText(f"📁 {Path(directory).name}")
        self.dir_button.setToolTip(
            f"Working directory: {directory}\nClick to change (this session only)")

    def _set_activity(self, detail: str) -> None:
        """Update the live run status chip in the session header."""
        self._activity = detail
        if not self.current_session_id:
            return
        # Cheap header refresh without a full sidebar rebuild.
        agent = self.prompt_bar.agent_combo.currentText() or ""
        internal = _AGENT_INTERNAL.get(agent, agent)
        model = self.prompt_bar.current_model() or self._default_model()
        meta = f"{agent or internal} · {model}"
        sid = self.current_session_id
        running = self.engine.is_running(sid) or sid in self._running
        if running:
            shown = detail or "working…"
            meta += f"   <span style='color:#e5c07b'>● {html.escape(shown)}</span>"
        self.header_meta.setText(meta)

    def _activity_from_part(self, part_type: str, data: dict) -> None:
        if part_type in ("text", "reasoning"):
            if not self._activity or self._activity in ("thinking…", "working…"):
                self._set_activity("writing…" if part_type == "text" else "thinking…")
            return
        if part_type not in ("tool", "task"):
            return
        status = data.get("status", "")
        tool = data.get("tool", "")
        if status in ("pending", "running") and tool:
            title = data.get("title") or ""
            inp = data.get("input") or {}
            summary = (
                title
                or inp.get("command")
                or inp.get("path")
                or inp.get("pattern")
                or inp.get("description")
                or ""
            )
            detail = f"{tool}: {str(summary)[:50]}" if summary else f"{tool}…"
            self._set_activity(detail)

    async def _refresh_sessions(self) -> None:
        sessions = await self.engine.store.list_sessions(self.engine.project.id)
        # one query for every child instead of one per session
        everyone = await self.engine.store.list_sessions(
            self.engine.project.id, include_children=True)
        children: dict[str, list] = {}
        for s in everyone:
            if s.parent_session_id:
                children.setdefault(s.parent_session_id, []).append(s)
        for kids in children.values():
            kids.sort(key=lambda s: s.created_at)
        running = {s.id for s in sessions if self.engine.is_running(s.id)} | self._running
        agent_colors = {a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()}
        self.session_list.populate(
            sessions, children, running, self.current_session_id or "", agent_colors)
        if self.current_session_id:
            current = await self.engine.store.get_session(self.current_session_id)
            if current:
                self._update_header(current)

    # ------------------------------------------------------------------ prompt

    def _submit(self, text: str) -> None:
        if not self.current_session_id:
            return
        session_id = self.current_session_id
        queued = self.engine.is_running(session_id)
        self.prompt_bar.set_running(True)
        self.transcript.add_user_text(f"local-{id(text)}", self._display_text(text), queued=queued)
        agent_label = self.prompt_bar.agent_combo.currentText() or "agent"
        agent = _AGENT_INTERNAL.get(agent_label, agent_label)
        if not queued:
            try:
                color = self.engine.agents.color_of(agent)
                self.transcript.show_thinking(agent_label, color)
                self._set_activity("thinking…")
            except Exception:
                pass
            self._running.add(session_id)

        async def _do_submit() -> None:
            try:
                await self.engine.submit_prompt(
                    session_id, text,
                    agent=agent or None,
                    model=self.prompt_bar.current_model() or None,
                )
            except Exception:
                # Don't leave the Stop button stuck if submit never started a run.
                if not self.engine.is_running(session_id):
                    self._running.discard(session_id)
                    if self.current_session_id == session_id:
                        self.prompt_bar.set_running(False)
                        self.transcript.dismiss_thinking()
                        self._set_activity("")
                raise

        self._fire(_do_submit())

    @staticmethod
    def _display_text(text: str) -> str:
        return text

    async def _abort(self) -> None:
        if self.current_session_id:
            sid = self.current_session_id
            await self.engine.abort(sid)
            self._running.discard(sid)
            self.prompt_bar.set_running(False)
            self.transcript.dismiss_thinking()
            self._set_activity("")

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
        agent = _AGENT_INTERNAL.get(agent, agent)
        if self.current_session_id and agent:
            self._fire(self.engine.store.update_session(self.current_session_id, agent=agent))

    def _model_changed(self, model: str) -> None:
        if self.current_session_id and model:
            self._fire(self.engine.store.update_session(self.current_session_id, model=model))
        if model:
            # remember it so the next new session starts on this model
            self._remember_setting("last_model", model)

    def _remember_setting(self, key: str, value) -> None:
        from ..engine.config import patch_config_file

        try:
            patch_config_file(self.engine.global_dir / "config.json", {key: value})
        except Exception:
            pass
        # keep in-memory config in sync without a full reload (which would
        # rebuild providers/agents and reset the pickers)
        extra = self.engine.config.model_extra
        if extra is not None:
            extra[key] = value

    def _default_model(self) -> str:
        return (self.engine.config.model_extra or {}).get("last_model") \
            or self.engine.config.model

    # ------------------------------------------------------------------ pickers & status

    def _refresh_pickers(self, agent: str = "", model: str = "") -> None:
        agents = [_AGENT_DISPLAY.get(a.name, a.name) for a in self.engine.agents.primaries()]
        self.prompt_bar.set_agents(agents, _AGENT_DISPLAY.get(agent, agent) or "build")

        # two-stage picker: connected providers, then that provider's models.
        # new sessions (no per-session model) start on the last one you chose
        model = model or self._default_model()
        provider, _, model_id = model.partition("/")
        providers = connected_providers(self.engine.config, self.engine.providers.auth)
        if provider not in providers:
            provider = providers[0] if providers else ""
            model_id = ""
        self.prompt_bar.set_providers(providers, provider)
        self._set_provider_models(provider, model_id)

    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        """Curated list immediately, live-fetched full list when it arrives."""
        if not provider:
            self.prompt_bar.set_models([], "")
            return
        curated = curated_models(provider)
        if current_id and current_id not in curated:
            curated.insert(0, current_id)
        self.prompt_bar.set_models(curated, current_id or (curated[0] if curated else ""))
        self._fire(self._load_live_models(provider))

    async def _load_live_models(self, provider: str) -> None:
        live = await self.engine.list_models(provider)
        if not live or self.prompt_bar.provider_combo.currentText() != provider:
            return  # provider changed meanwhile, or nothing better than curated
        keep = self.prompt_bar.model_combo.currentText()
        merged = list(dict.fromkeys(([keep] if keep and keep not in live else []) + live))
        self.prompt_bar.set_models(merged, keep or (merged[0] if merged else ""))

    def _provider_changed(self, provider: str) -> None:
        if not provider or provider.startswith("⚡"):
            return
        self._set_provider_models(provider)
        model = self.prompt_bar.current_model()
        if self.current_session_id and model:
            self._fire(self.engine.store.update_session(self.current_session_id, model=model))

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
        # set_session_mode also releases permission prompts already waiting
        self.engine.set_session_mode([SAFE, ASK_MODE, FULL_AUTO][index])

    # ------------------------------------------------------------------ dialogs

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.engine, self)
        dialog.exec()

    def _open_providers(self) -> None:
        from .provider_manager import ProviderManager

        dialog = ProviderManager(self.engine, self)
        dialog.exec()

    def _open_agent_editor(self) -> None:
        editor = AgentEditor(self.engine, self)
        editor.exec()

    # ------------------------------------------------------------------ util

    @staticmethod
    def _fire(coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (widget-only tests): drop silently
            coro.close()
            return
        loop.create_task(coro)
