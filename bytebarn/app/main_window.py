"""Main window: sidebar · transcript · crew stage · prompt bar (spec §7.1)."""

from __future__ import annotations

import asyncio
import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..engine.events import (
    AgentRegistryChanged,
    GoalQueueUpdated,
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
    TerminalChunk,
    TerminalClosed,
    TerminalOpened,
    TodoUpdated,
)
from ..engine.facade import Engine
from ..engine.permissions import (
    ASK_MODE,
    FULL_AUTO,
    PLAN,
    SESSION_MODE_LABELS,
    SESSION_MODES,
)
from ..engine.providers.known import (
    CLAUDE_CODE_MODELS as _CC_MODELS,
    CLAUDE_CODE_PROVIDER,
    KNOWN_PROVIDERS,
    connected_providers,
    curated_models,
)
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

# Claude Code appears in the prompt-bar picker like any other provider.
# Selecting it flips sticky Engine runtime to claude-code (local `claude`
# CLI). Per-agent defaults can also pin `claude-code/…` without the global
# runtime switch — see Engine.uses_claude_code.
CLAUDE_CODE_LABEL = KNOWN_PROVIDERS[CLAUDE_CODE_PROVIDER].label
CLAUDE_CODE_DEFAULT_MODEL = f"{CLAUDE_CODE_PROVIDER}/default"
CLAUDE_CODE_MODELS = list(_CC_MODELS)

class MainWindow(QMainWindow):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.current_session_id: str | None = None
        self._session_stack: list[str] = []  # for back-navigation into subagents
        self._running: set[str] = set()
        self._activity: str = ""  # live run detail for the open session
        self._shutting_down = False
        self._bg_tasks: set[asyncio.Task] = set()
        self._tasks: list[asyncio.Task] = []

        self.setWindowTitle("ByteBarn")
        self._clamp_initial_geometry(1200, 800)

        # widgets
        self.session_list = SessionList()
        self.session_list.new_project.connect(self._create_catalog_project)
        self.session_list.rename_project.connect(self._rename_project)
        self.session_list.delete_project.connect(self._delete_project)
        self.session_list.open_project.connect(self._enter_workspace)
        self.session_list.open_project_settings.connect(self._open_project_dialog)

        from .project_workspace import ProjectWorkspace

        self.workspace = ProjectWorkspace(engine)
        self.workspace.back_requested.connect(self._show_all_projects)
        self.workspace.session_selected.connect(self._open_session)
        self.workspace.new_chat.connect(self._prompt_new_session)
        self.workspace.rename_session.connect(self._rename_session)
        self.workspace.delete_session.connect(
            lambda sid: self._fire(self._delete_session(sid)))
        self.workspace.open_settings.connect(self._open_project_dialog)
        self.transcript = Transcript()
        self.crew_stage = CrewStage()
        self.todo_strip = TodoStrip()
        self.prompt_bar = PromptBar(commands=engine.commands, project_dir=engine.project_dir,
                                    attachments_dir=engine.global_dir / "attachments")
        self._pending_edit: str | None = None  # message id being edited

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
        from . import theme as _theme

        self.header_meta.setStyleSheet(f"color: {_theme.tokens()['muted']};")
        self.dir_button = QPushButton("")
        self.dir_button.setFlat(True)
        self.dir_button.setToolTip("Working directory for this session — click to change")
        self.dir_button.clicked.connect(self._pick_directory)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 4)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.back_button)
        header_layout.addWidget(self.header_icon)
        header_layout.addWidget(self.header_title)
        header_layout.addWidget(self.header_meta, 1)
        header_layout.addWidget(self.dir_button)

        # transcript / crew stage boundary is draggable; size persists
        self.stage_split = QSplitter(Qt.Vertical)
        self.stage_split.addWidget(self.transcript)
        self.stage_split.addWidget(self.crew_stage)
        self.stage_split.setStretchFactor(0, 1)
        self.stage_split.setStretchFactor(1, 0)
        self.stage_split.setCollapsible(0, False)
        self.stage_split.setHandleWidth(6)
        self._stage_save_timer = None
        self.stage_split.splitterMoved.connect(self._stage_resized)
        self.crew_stage.shown.connect(self._restore_stage_height)

        # in-conversation search bar (⌘F), hidden until toggled
        from PySide6.QtWidgets import QLineEdit

        self.search_bar = QWidget()
        search_layout = QHBoxLayout(self.search_bar)
        search_layout.setContentsMargins(16, 0, 16, 4)
        search_layout.setSpacing(4)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find in conversation…")
        self.search_edit.textChanged.connect(self._search_changed)
        self.search_edit.returnPressed.connect(lambda: self._search_nav(1))
        self.search_count = QLabel("")
        prev_btn = QPushButton("↑")
        prev_btn.setFlat(True)
        prev_btn.setFixedWidth(28)
        prev_btn.clicked.connect(lambda: self._search_nav(-1))
        next_btn = QPushButton("↓")
        next_btn.setFlat(True)
        next_btn.setFixedWidth(28)
        next_btn.clicked.connect(lambda: self._search_nav(1))
        close_btn = QPushButton("✕")
        close_btn.setFlat(True)
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self._toggle_search)
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_count)
        search_layout.addWidget(prev_btn)
        search_layout.addWidget(next_btn)
        search_layout.addWidget(close_btn)
        self.search_bar.setVisible(False)

        # preview panel (artifacts / dev server) sits beside the transcript
        from .preview import PreviewPanel

        self.preview = PreviewPanel()
        self.preview.setVisible(False)
        self.preview.closed.connect(lambda: self.content_split.setSizes([1, 0]))
        self.content_split = QSplitter(Qt.Horizontal)
        self.content_split.addWidget(self.stage_split)
        self.content_split.addWidget(self.preview)
        self.content_split.setStretchFactor(0, 1)
        self.content_split.setStretchFactor(1, 1)
        self.content_split.setCollapsible(0, False)
        self.content_split.setHandleWidth(2)

        # Terminal manager — bottom pane (backend tees + local shells)
        from .terminal_panel import TerminalPanel

        self.terminal_panel = TerminalPanel(engine)
        self.terminal_panel.setVisible(False)
        self.terminal_panel.closed.connect(self._on_terminal_closed)
        self.mid_split = QSplitter(Qt.Vertical)
        self.mid_split.addWidget(self.content_split)
        self.mid_split.addWidget(self.terminal_panel)
        self.mid_split.setStretchFactor(0, 1)
        self.mid_split.setStretchFactor(1, 0)
        self.mid_split.setCollapsible(0, False)
        self.mid_split.setHandleWidth(6)
        self.mid_split.setSizes([700, 0])

        right = QWidget()
        right.setMinimumWidth(280)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(header)
        right_layout.addWidget(self.search_bar)
        right_layout.addWidget(self.mid_split, 1)
        right_layout.addWidget(self.todo_strip)
        right_layout.addWidget(self.prompt_bar)

        # two ways of viewing projects: All projects (0) / one workspace (1)
        from PySide6.QtWidgets import QStackedWidget

        self.sidebar = QStackedWidget()
        self.sidebar.addWidget(self.session_list)
        self.sidebar.addWidget(self.workspace)
        # Floor keeps Chats/Goals/Memory/Agents tabs readable (scroll, not
        # "Ch…"). Soft enough that the main splitter still drags freely.
        self.sidebar.setMinimumWidth(200)

        splitter = QSplitter()
        splitter.addWidget(self.sidebar)
        splitter.addWidget(right)
        # Default wide enough for the four workspace tab labels without
        # crowding; users can still drag narrower (scroll buttons appear).
        splitter.setSizes([320, 880])
        splitter.setCollapsible(0, False)
        # Allow the user to drag the session picker narrower/wider freely.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(6)
        self.setCentralWidget(splitter)
        self._main_split = splitter

        # status bar
        self.status_project = QLabel(str(engine.project_dir))
        self.status_project.setMinimumWidth(0)
        self.status_project.setMaximumWidth(400)
        self.status_project.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_project.setToolTip(str(engine.project_dir))
        self.status_git = QLabel("")
        self.status_cost = QLabel("")
        self.status_runtime = QLabel("")
        self.status_runtime.setObjectName("pill")
        self.status_runtime.setVisible(False)
        self.status_runtime.setToolTip(
            "Agent runtime — Claude Code uses your local `claude` CLI "
            "(subscription / claude auth). Pick any other provider to return "
            "to ByteBarn's native tool loop.")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([SESSION_MODE_LABELS[m] for m in SESSION_MODES])
        self.mode_combo.setMinimumWidth(90)
        self.mode_combo.setSizePolicy(self.mode_combo.sizePolicy().horizontalPolicy(),
                                      self.mode_combo.sizePolicy().verticalPolicy())
        mode = (self.engine.config.model_extra or {}).get("session_mode")
        index = {m: i for i, m in enumerate(SESSION_MODES)}.get(mode, SESSION_MODES.index(ASK_MODE))
        self.mode_combo.setCurrentIndex(index)
        # the combo isn't wired up yet, so push the persisted mode into the
        # engine explicitly — otherwise Full-auto shows in the UI but the
        # policy still asks
        self.engine.set_session_mode(SESSION_MODES[index])
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.mode_combo.setToolTip(
            "Permission mode — Safe: hard lock · Plan: explore & design (no edits) · "
            "Ask: confirm risky tools · Full-auto: no prompts. /plan enters Plan.")
        # placeholder cue if we restored Plan from config
        self._update_plan_mode_chrome()
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
        self.ui_toggle = QPushButton("✨ ui")
        self.ui_toggle.setFlat(True)
        self.ui_toggle.setToolTip(
            "Switch between the classic look and the new Night Workshop UI")
        self.ui_toggle.clicked.connect(self._toggle_ui)
        self.context_meter = QPushButton("")
        self.context_meter.setObjectName("pill")
        self.context_meter.setFlat(True)
        self.context_meter.setVisible(False)
        self.context_meter.clicked.connect(self._compact_clicked)
        self.update_button = QPushButton("⬆ update available")
        self.update_button.setObjectName("pill")
        self.update_button.setFlat(True)
        self.update_button.setVisible(False)
        self.update_button.setStyleSheet("color:#98c379;")
        self.update_button.clicked.connect(lambda: self._check_updates(manual=True))
        self.review_button = QPushButton("⏪ review run")
        self.review_button.setObjectName("pill")
        self.review_button.setFlat(True)
        self.review_button.setVisible(False)
        self.review_button.setToolTip(
            "Review the last run's file changes — keep or revert them")
        self.review_button.clicked.connect(self._open_run_review)
        self.statusBar().addWidget(self.status_project)
        self.statusBar().addWidget(QLabel("·"))
        self.statusBar().addWidget(self.status_git)
        self.statusBar().addWidget(self.status_runtime)
        self.statusBar().addPermanentWidget(self.update_button)
        self.statusBar().addPermanentWidget(self.context_meter)
        self.statusBar().addPermanentWidget(self.review_button)
        self.statusBar().addPermanentWidget(self.status_cost)
        self.statusBar().addPermanentWidget(providers_button)
        self.statusBar().addPermanentWidget(agents_button)
        self.statusBar().addPermanentWidget(self.mode_combo)
        self.statusBar().addPermanentWidget(self.ui_toggle)
        self.statusBar().addPermanentWidget(settings_button)
        self._update_runtime_status()

        self._build_menus()

        # desktop notifications for walk-away workflows (no-op headless)
        self._tray = None
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        if (QApplication.instance().platformName() != "offscreen"
                and QSystemTrayIcon.isSystemTrayAvailable()):
            from .icon import app_icon

            self._tray = QSystemTrayIcon(app_icon(), self)
            self._tray.setToolTip("ByteBarn")
            self._tray.show()

        # wiring
        self.session_list.session_selected.connect(self._open_session)
        self.session_list.new_session.connect(self._prompt_new_session)
        self.session_list.close_session.connect(
            lambda sid: self._fire(self._close_session(sid)))
        self.session_list.delete_session.connect(
            lambda sid: self._fire(self._delete_session(sid)))
        self.session_list.delete_sessions.connect(
            lambda ids: self._fire(self._delete_sessions(ids)))
        self.session_list.rename_session.connect(self._rename_session)
        self.session_list.move_session_to_project.connect(self._move_session_to_project)
        self.session_list.session_moved_to_project.connect(self._on_session_moved)
        self.transcript.open_session.connect(self._open_child)
        self.transcript.open_file_requested.connect(self._open_file_viewer)
        self.transcript.preview_file_requested.connect(self._preview_file)
        self.transcript.preview_html_requested.connect(self._preview_html)
        self.transcript.edit_requested.connect(self._begin_edit)
        self.transcript.regenerate_requested.connect(
            lambda: self._fire(self._regenerate()))
        self.session_list.content_search_requested.connect(
            lambda q: self._fire(self._content_search(q)))
        self.crew_stage.open_session.connect(self._open_child)
        self.crew_stage.stop_requested.connect(
            lambda sid: self._fire(self._abort(sid)))
        self.prompt_bar.submitted.connect(self._submit)
        self.prompt_bar.aborted.connect(self._on_prompt_abort)
        self.prompt_bar.agent_changed.connect(self._agent_changed)
        self.prompt_bar.provider_changed.connect(self._provider_changed)
        self.prompt_bar.model_changed.connect(self._model_changed)
        self.prompt_bar.action_requested.connect(self._action)

        self._refresh_pickers()

    def _build_menus(self) -> None:
        """Native menu bar (on macOS this fills the "ByteBarn" application menu)."""
        from PySide6.QtGui import QAction, QKeySequence

        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        new_action = QAction("New Session", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self._prompt_new_session)
        new_in_action = QAction("New Session in Folder…", self)
        new_in_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_in_action.triggered.connect(self._prompt_new_session_in_folder)
        close_action = QAction("Close Session", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(
            lambda: self.current_session_id
            and self._fire(self._close_session(self.current_session_id)))
        quit_action = QAction("Quit ByteBarn", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.setMenuRole(QAction.QuitRole)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(new_action)
        file_menu.addAction(new_in_action)
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        projects_menu = bar.addMenu("&Projects")
        new_proj = QAction("New Project…", self)
        new_proj.triggered.connect(self._new_project)
        all_proj = QAction("All Projects", self)
        all_proj.setShortcut(QKeySequence("Ctrl+Shift+O"))
        all_proj.triggered.connect(self._show_all_projects)
        this_proj = QAction("This Project", self)
        this_proj.triggered.connect(
            lambda: self.engine.project and self._enter_workspace(self.engine.project.id))
        projects_menu.addAction(new_proj)
        projects_menu.addSeparator()
        projects_menu.addAction(all_proj)
        projects_menu.addAction(this_proj)

        session_menu = bar.addMenu("&Session")
        stop_action = QAction("Stop Run", self)
        stop_action.setShortcut(QKeySequence("Ctrl+."))
        stop_action.triggered.connect(lambda: self._fire(self._abort()))
        compact_action = QAction("Compact Context", self)
        compact_action.triggered.connect(self._compact_clicked)
        review_action = QAction("Review Last Run…", self)
        review_action.triggered.connect(self._open_run_review)
        regen_action = QAction("Regenerate Last Response", self)
        regen_action.setShortcut(QKeySequence("Ctrl+R"))
        regen_action.triggered.connect(lambda: self._fire(self._regenerate()))
        find_action = QAction("Find in Conversation", self)
        find_action.setShortcut(QKeySequence.Find)
        find_action.triggered.connect(self._toggle_search)
        export_action = QAction("Export Transcript…", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._export_transcript)
        side_chat_action = QAction("Side Chat…", self)
        side_chat_action.setShortcut(QKeySequence("Ctrl+;"))
        side_chat_action.triggered.connect(self._open_side_chat)
        switch_action = QAction("Next Session", self)
        switch_action.setShortcut(QKeySequence("Ctrl+Tab"))
        switch_action.triggered.connect(lambda: self._cycle_session(1))
        switch_back = QAction("Previous Session", self)
        switch_back.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        switch_back.triggered.connect(lambda: self._cycle_session(-1))
        session_menu_extras = [regen_action, find_action, export_action,
                               side_chat_action, review_action, switch_action,
                               switch_back]
        delete_action = QAction("Delete Session…", self)
        delete_action.triggered.connect(
            lambda: self.current_session_id
            and self._fire(self._delete_session(self.current_session_id)))
        session_menu.addAction(stop_action)
        session_menu.addAction(compact_action)
        for extra in session_menu_extras:
            session_menu.addAction(extra)
        session_menu.addSeparator()
        session_menu.addAction(delete_action)

        tools_menu = bar.addMenu("&Tools")
        providers_action = QAction("Providers…", self)
        providers_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        providers_action.triggered.connect(self._open_providers)
        agents_action = QAction("Agents…", self)
        agents_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        agents_action.triggered.connect(self._open_agent_editor)
        skills_action = QAction("Skills…", self)
        skills_action.triggered.connect(self._open_skill_editor)
        mcp_action = QAction("MCP Servers…", self)
        mcp_action.triggered.connect(self._open_mcp)
        preview_action = QAction("Preview Panel", self)
        preview_action.setShortcut(QKeySequence("Ctrl+Shift+V"))
        preview_action.triggered.connect(self._toggle_preview)
        terminal_action = QAction("Terminals", self)
        terminal_action.setShortcut(QKeySequence("Ctrl+`"))
        terminal_action.setToolTip(
            "Show Claude Code process output and open local shells")
        terminal_action.triggered.connect(self._toggle_terminal)
        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence.Preferences)
        settings_action.setMenuRole(QAction.PreferencesRole)  # macOS app menu
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(providers_action)
        tools_menu.addAction(agents_action)
        tools_menu.addAction(skills_action)
        tools_menu.addAction(mcp_action)
        tools_menu.addAction(preview_action)
        tools_menu.addAction(terminal_action)
        tools_menu.addSeparator()
        tools_menu.addAction(settings_action)

        help_menu = bar.addMenu("&Help")
        update_action = QAction("Check for Updates…", self)
        update_action.triggered.connect(lambda: self._check_updates(manual=True))
        help_menu.addAction(update_action)
        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)
        about_action = QAction("About ByteBarn", self)
        about_action.setMenuRole(QAction.AboutRole)  # macOS app menu
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(shortcuts_action)
        help_menu.addAction(about_action)

    def _pick_project_dir(self) -> str | None:
        dlg = QFileDialog(self, "Open Project", str(Path.home()))
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)
        use_root = dlg.findChild(QWidget, "qt_use_root_cb")  # reuse if exists
        cb = None
        if not use_root:
            from PySide6.QtWidgets import QCheckBox
            cb = QCheckBox("Use project root")
            dlg.setOption(QFileDialog.DontUseNativeDialog, True)
            dlg.layout().addWidget(cb)
        if dlg.exec():
            if cb and cb.isChecked() and self.engine.project_dir:
                return str(self.engine.project_dir)
            return dlg.selectedFiles()[0] if dlg.selectedFiles() else None
        return None

    def _new_project(self) -> None:
        self._create_catalog_project()

    def _create_catalog_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        name = name.strip()
        if not ok or not name:
            return

        async def run() -> None:
            project = await self.engine.store.add_project(f"catalog:{name}", name=name)
            await self._refresh_sessions()
            # straight into the project's workspace, like Claude's project page
            self._enter_workspace(project.id)

        self._fire(run())

    def _enter_workspace(self, project_id: str) -> None:
        """Open a project's dedicated view (chats / goals / memory / agents)."""
        from . import theme

        theme.crossfade(self.sidebar, 1)
        self._fire(self.workspace.load(project_id))

    def _show_all_projects(self) -> None:
        from . import theme

        theme.crossfade(self.sidebar, 0)
        self._fire(self._refresh_sessions())

    def _toggle_ui(self) -> None:
        """One-press switch: classic look <-> the Night Workshop overhaul."""
        from PySide6.QtWidgets import QApplication

        from ..engine.config import patch_config_file
        from . import theme

        new_mode = "dark" if theme.is_modern() else "modern"
        theme.apply_theme(QApplication.instance(), new_mode)
        try:
            patch_config_file(self.engine.global_dir / "config.json",
                              {"theme": new_mode})
            self.engine.reload_config()
        except Exception:
            pass

    def _open_project_dialog(self, project_id: str) -> None:
        from .project_dialog import ProjectDialog

        dlg = ProjectDialog(self.engine, project_id, self)
        dlg.finished.connect(lambda _r: self._fire(self._refresh_sessions()))
        dlg.open()  # non-modal exec keeps the qasync loop free

    def _rename_project(self, project_id: str) -> None:
        async def run() -> None:
            current = ""
            for p in await self.engine.store.list_projects():
                if p.id == project_id:
                    current = p.name or ""
                    break
            new_name, ok = QInputDialog.getText(
                self, "Rename project", "New name:", text=current)
            new_name = new_name.strip()
            if ok and new_name and new_name != current:
                await self.engine.store.rename_project(project_id, new_name)
                await self._refresh_sessions()

        self._fire(run())

    def _delete_project(self, project_id: str) -> None:
        async def run() -> None:
            await self.engine.store.delete_project(project_id)
            if self.engine.project and self.engine.project.id == project_id:
                # current project deleted; nothing to do, window will stay open
                pass
            await self._refresh_sessions()

        self._fire(run())

    def _show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "Keyboard Shortcuts", (
            "<b>Prompt</b><br>"
            "Enter — send · Shift+Enter — newline · Esc — stop run / cancel edit<br>"
            "/ — command palette · @ — attach file · paste or drop files to attach<br><br>"
            "<b>Conversation</b><br>"
            "⌘F — find in conversation · ⌘R — regenerate · ⌘E — export transcript<br>"
            "⌘; — side chat · right-click a message — copy / edit &amp; re-run<br><br>"
            "<b>Sessions</b><br>"
            "⌘N — new · ⌘W — close · ⌃Tab / ⌃⇧Tab — next/previous ·"
            " ↑/↓ in sidebar — switch<br><br>"
            "<b>Tools</b><br>"
            "⌘⇧P — providers · ⌘⇧A — agents · ⌘⇧V — preview panel · ⌘, — settings"
        ))

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(self, "About ByteBarn", (
            "<h3>ByteBarn</h3>"
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
        # standard view: the working-directory project's workspace
        self._enter_workspace(self.engine.project.id)
        await self._refresh_sessions()
        await self._refresh_git()
        self._tasks = [
            asyncio.ensure_future(self._event_loop()),
            asyncio.ensure_future(self._watch_files()),
            asyncio.ensure_future(self.engine.run_routines()),
        ]
        # modals (welcome wizard, first-launch directory picker) must run from
        # the Qt loop, not nested inside this asyncio task, or qasync warns
        # "Cannot enter into task while another task is being executed".
        self._had_sessions = bool(sessions)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._post_bootstrap)

    def _post_bootstrap(self) -> None:
        self._maybe_first_run()
        self._maybe_auto_update_check()
        # first launch (or all sessions deleted): start a session right away
        # in the project root — the dir button can change it afterwards
        if not self._had_sessions and (self.engine.config.model_extra or {}).get("onboarded"):
            self._prompt_new_session()

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
                    if not still:
                        session = await self.engine.store.get_session(event.session_id)
                        if session and not session.parent_session_id:
                            self._notify(
                                "Run finished" if event.status == "done"
                                else f"Run {event.status}",
                                session.title or "untitled session")
                elif isinstance(event, GoalQueueUpdated):
                    if self.sidebar.currentIndex() == 1 \
                            and self.workspace.project_id == event.project_id:
                        await self.workspace.load(event.project_id)
                elif isinstance(event, QueueUpdated):
                    if event.session_id == self.current_session_id:
                        self.prompt_bar.set_queue_depth(event.depth)
                elif isinstance(event, (TerminalOpened, TerminalChunk, TerminalClosed)):
                    panel = getattr(self, "terminal_panel", None)
                    if panel is not None:
                        panel.handle_event(event)
                        # Auto-open the pane when a backend terminal starts so
                        # Claude Code output is visible without hunting menus.
                        if (isinstance(event, TerminalOpened)
                                and event.kind == "claude-code"
                                and not panel.isVisible()):
                            self._show_terminal()
                elif isinstance(event, PermissionAsked):
                    if self.engine.session_mode == FULL_AUTO:
                        # switched to Full-auto after the ask was queued
                        self.engine.answer_permission(event.request_id, "allow")
                    else:
                        if event.session_id == self.current_session_id:
                            self._set_activity(f"waiting: {event.tool}")
                        self._notify("ByteBarn needs permission",
                                     f"{event.tool}: {event.arg[:80]}")
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
                    # keep the session's current picks; only the lists refresh.
                    # Must pass full "provider/model-id" — bare model_combo text has no
                    # provider and would make _refresh_pickers fall back to providers[0].
                    self._refresh_pickers(
                        self.prompt_bar.agent_combo.currentText(),
                        self.prompt_bar.current_model(),
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
        for base in (self.engine.global_dir, self.engine.project_dir / ".bytebarn"):
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

    def _prompt_new_session(self, target_project_id: str | None = None) -> None:
        """Instant new session (Claude-Desktop style): inherit the working
        directory from context instead of forcing a picker. "New Session in
        Folder…" keeps the explicit choice available."""
        self._fire(self._new_session(
            project_id=target_project_id,
            isolated=self.session_list.isolate_requested(),
        ))

    def _prompt_new_session_in_folder(self) -> None:
        """Explicit-directory variant: pick a folder (modal, from the Qt
        loop — a modal inside an asyncio task makes qasync warn about task
        re-entry), then create."""
        directory = self._prompt_directory("Choose a working directory for the new session")
        if directory:
            self._fire(self._new_session(directory=directory))

    async def _new_session(
        self, directory: str | None = None, project_id: str | None = None,
        isolated: bool = False,
    ) -> None:
        """Create a session; with no explicit directory, inherit one from
        context: target project → current session → last used → project root.

        ``isolated`` runs it in its own git worktree checked out from HEAD, so
        uncommitted work in the live tree will not be visible to it — warn
        before that surprises anyone.
        """
        if directory is None:
            directory = await self._default_new_session_dir(project_id)
        if isolated:
            # probe the very repo the worktree will branch from — a new session
            # can be rooted in any registered project, and listing another
            # repo's dirty files is worse than saying nothing
            dirty = await self.engine.repo_dirty(directory)
            if dirty:
                shown = "\n".join(f"  {p}" for p in dirty[:10])
                if len(dirty) > 10:
                    shown += f"\n  …and {len(dirty) - 10} more"
                ok = await self._ask_yes_no(
                    "Uncommitted changes",
                    "The isolated worktree is checked out from HEAD, so these "
                    f"uncommitted files will not be in it:\n\n{shown}\n\n"
                    "Start the isolated session anyway?",
                )
                if not ok:
                    return
        self._remember_project(directory)
        # Prefer the prompt-bar agent (and any model it pins, e.g. Claude Code).
        agent_label = self.prompt_bar.agent_combo.currentText().strip()
        agent = _AGENT_INTERNAL.get(agent_label, agent_label) or ""
        model = self._default_model()
        if agent:
            try:
                pinned = self.engine.agents.get(agent).model or ""
            except KeyError:
                pinned = ""
            if pinned:
                model = pinned
        session = await self.engine.new_session(
            agent=agent, model=model, directory=directory,
            project_id=project_id, isolated=isolated)
        if isolated:
            if session.worktree_branch:
                self.statusBar().showMessage(
                    f"Isolated on {session.worktree_branch} — {session.directory}",
                    8000)
            elif not self.engine.worktree_enabled():
                self.statusBar().showMessage(
                    'Isolation unavailable — worktrees are disabled '
                    '("worktree": {"enabled": false} in config)', 8000)
            else:
                self.statusBar().showMessage(
                    "Isolation unavailable — not a git repo, or no commits yet",
                    6000)
        await self._load_session(session.id)
        await self._refresh_sessions()

    def _prompt_directory(self, caption: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        start = self._last_project() or str(Path.home())
        return QFileDialog.getExistingDirectory(self, caption, start)

    def _ask_yes_no(self, title: str, text: str):
        """Await a modal from an asyncio task without qasync task re-entry:
        the dialog opens on the next Qt loop turn and resolves a future."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        fut = asyncio.get_event_loop().create_future()

        def _show() -> None:
            box = QMessageBox(QMessageBox.Warning, title, text, parent=self)
            box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            answered = box.exec() == QMessageBox.Ok
            if not fut.done():
                fut.set_result(answered)

        QTimer.singleShot(0, _show)
        return fut

    def _ask_three_way(
        self, title: str, text: str, keep_label: str, remove_label: str
    ):
        """Await a three-button modal from an asyncio task.

        Same qasync task re-entry dodge as `_ask_yes_no` — the dialog opens on
        the next Qt loop turn and resolves a future. Resolves to "remove",
        "keep", or None for cancel.
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        fut = asyncio.get_event_loop().create_future()

        def _show() -> None:
            box = QMessageBox(QMessageBox.Warning, title, text, parent=self)
            remove = box.addButton(remove_label, QMessageBox.DestructiveRole)
            keep = box.addButton(keep_label, QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(keep)
            box.exec()
            clicked = box.clickedButton()
            answer = "remove" if clicked is remove else "keep" if clicked is keep else None
            if not fut.done():
                fut.set_result(answer)

        QTimer.singleShot(0, _show)
        return fut

    async def _default_new_session_dir(self, project_id: str | None = None) -> str:
        """Working directory for an instant new session."""
        if project_id:
            for p in await self.engine.store.list_projects():
                if p.id == project_id and p.path and Path(p.path).is_dir():
                    return p.path
        if self.current_session_id:
            current = await self.engine.store.get_session(self.current_session_id)
            # never inherit any worktree: a plain new session rooted there
            # would write to another session's branch while the UI showed
            # nothing to say so, and `git status` in the live checkout would
            # stay clean. The path test also covers subagent worktrees, which
            # carry a directory but no worktree_branch — and which are removed
            # outright when their run finishes.
            if (current and current.directory and not current.worktree_branch
                    and not self._is_worktree_path(current.directory)
                    and Path(current.directory).is_dir()):
                return current.directory
        last = self._last_project()
        if last and Path(last).is_dir():
            return last
        return str(self.engine.project_dir)

    async def _load_session(self, session_id: str) -> None:
        session = await self.engine.store.get_session(session_id)
        if session is None:
            return
        self.current_session_id = session_id
        self._activity = "working…" if self.engine.is_running(session_id) else ""
        # load the most recent page first (lazy loading)
        history = await self.engine.store.session_parts(session_id, limit=50)
        self.transcript.load_history(history)
        self._loaded_before = history[0][0].created_at if history else None
        self._full_history_loaded = len(history) < 50

        # attach lazy loader
        self.transcript.request_older = lambda: self._fire(self._load_older_history())
        todos = await self.engine.store.get_todos(session_id)
        self.todo_strip.set_todos([{"content": t.content, "status": t.status} for t in todos])
        self.prompt_bar.set_running(self.engine.is_running(session_id))
        self.prompt_bar.set_queue_depth(self.engine.queue_depth(session_id))
        self.back_button.setVisible(session.parent_session_id is not None)
        self._update_header(session)
        self._refresh_pickers(session.agent, session.model)
        await self._refresh_cost()

    async def _load_older_history(self) -> None:
        # scroll events fire in bursts — one page fetch at a time
        if getattr(self, "_loading_older", False) \
                or getattr(self, "_full_history_loaded", False) \
                or not self.current_session_id:
            return
        before = self.transcript.oldest_timestamp() or getattr(self, "_loaded_before", None)
        if before is None:
            return
        self._loading_older = True
        try:
            older = await self.engine.store.session_parts(
                self.current_session_id, limit=50, before=before
            )
            if not older:
                self._full_history_loaded = True
                return
            self.transcript.append_older(older)
            self._loaded_before = older[0][0].created_at
        finally:
            self._loading_older = False

    def _pick_directory(self) -> None:
        if not self.current_session_id:
            return
        from PySide6.QtWidgets import QFileDialog

        session_id = self.current_session_id
        dlg = QFileDialog(self, "Working directory for this session", str(self.engine.project_dir))
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)
        cb = None
        if self.engine.project_dir:
            from PySide6.QtWidgets import QCheckBox
            dlg.setOption(QFileDialog.DontUseNativeDialog, True)
            cb = QCheckBox("Use project root")
            dlg.layout().addWidget(cb)
        if not dlg.exec():
            return
        if cb and cb.isChecked():
            picked = str(self.engine.project_dir)
        else:
            picked = dlg.selectedFiles()[0] if dlg.selectedFiles() else ""
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
        """Remember the last chosen folder to seed the next directory picker.

        Worktree paths are never remembered: they are throwaway checkouts under
        the worktree store, so persisting one as ``last_project`` would outlive
        the session, survive a restart, and seed the directory picker with a
        directory the user never chose (and that git may since have removed).
        """
        from ..engine.config import patch_config_file

        if self._is_worktree_path(path):
            return
        try:
            patch_config_file(self.engine.global_dir / "config.json",
                              {"last_project": path})
        except Exception:
            pass

    def _is_worktree_path(self, path: str) -> bool:
        """True when ``path`` lives under the engine's worktree store."""
        if not path:
            return False
        try:
            Path(path).resolve().relative_to(
                Path(self.engine.worktrees.store_root).resolve())
        except (ValueError, OSError):
            return False
        return True

    def _last_project(self) -> str:
        """Last chosen folder, ignoring any stale worktree path already stored."""
        last = (self.engine.config.model_extra or {}).get("last_project", "")
        return "" if self._is_worktree_path(last) else last

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
        answer = await self._worktree_verdict([session_id])
        if answer is None:
            return
        problem = await self.engine.delete_session(
            session_id, discard_worktree=answer == "remove")
        self._report_worktree_cleanup([problem])
        await self._after_session_removed(session_id)

    async def _delete_sessions(self, session_ids: list[str]) -> None:
        answer = await self._worktree_verdict(session_ids)
        if answer is None:
            return
        problems = []
        for sid in session_ids:
            problems.append(await self.engine.delete_session(
                sid, discard_worktree=answer == "remove"))
        self._report_worktree_cleanup(problems)
        for sid in session_ids:
            self._session_stack = [s for s in self._session_stack if s != sid]
        if self.current_session_id in session_ids:
            await self._after_session_removed(self.current_session_id or "")
        else:
            await self._refresh_sessions()

    def _report_worktree_cleanup(self, problems: list[str]) -> None:
        """Surface anything the worktree cleanup could not remove.

        The delete itself always went through, so this is the user's only
        signal that a full checkout of their repo is still on disk — with the
        path, so they can finish it by hand.
        """
        left = [p for p in problems if p]
        if left:
            self.statusBar().showMessage("; ".join(left), 15000)

    async def _worktree_verdict(self, session_ids: list[str]) -> str | None:
        """Ask what to do with any worktrees in this selection.

        Returns "remove", "keep", or None to cancel the delete entirely. A
        selection with no isolated sessions answers "keep" without asking —
        SessionList already took the "permanently delete?" confirmation.
        """
        infos = []
        for sid in session_ids:
            info = await self.engine.session_worktree_info(sid)
            if info is not None:
                infos.append(info)
        if not infos:
            return "keep"

        # What removal would destroy, counted two ways. Commits are only half
        # of it: the engine never commits, so an isolated session's output is
        # normally *uncommitted* — "fully merged" on its own would invite
        # Remove on a worktree holding a whole run's work.
        lines = []
        for info in infos:
            bits = []
            if info["unmerged"]:
                plural = "" if info["unmerged"] == 1 else "s"
                bits.append(f"{info['unmerged']} commit{plural} not on any"
                            f" other branch")
            uncommitted = info.get("uncommitted", 0)
            if uncommitted:
                plural = "" if uncommitted == 1 else "s"
                bits.append(f"{uncommitted} uncommitted file{plural}")
            detail = ", ".join(bits) if bits else "nothing to lose"
            if not info["exists"]:
                detail += ", directory already gone"
            lines.append(f"  {info['branch']} — {detail}")
            if info["path"]:
                # the location, so Keep still leaves the user something to go
                # on once the session row is gone
                lines.append(f"    {info['path']}")

        # "this session" vs "these sessions" describes the isolated ones the
        # dialog is actually about, not the whole selection passed in — a
        # plain session mixed into the selection must not tip the wording.
        what = "this session" if len(infos) == 1 else "these sessions"
        body = (f"Remove the git worktree{'s' if len(infos) > 1 else ''} for"
                f" {what} too?\n\n" + "\n".join(lines))
        return await self._ask_three_way(
            "Delete worktrees", body, "Keep worktrees", "Remove worktrees")

    def _rename_session(self, session_id: str) -> None:
        title, ok = QInputDialog.getText(self, "Rename session", "New title:")
        if ok and title:
            async def apply() -> None:
                await self.engine.store.update_session(session_id, title=title)
                await self._refresh_sessions()
            self._fire(apply())

    def _move_session_to_project(self, session_id: str) -> None:
        async def run() -> None:
            projects = await self.engine.store.list_projects()
            if not projects:
                QMessageBox.information(
                    self, "Move to project",
                    "No projects yet — create one with + Project first.")
                return
            names = [p.name for p in projects]
            choice, ok = QInputDialog.getItem(
                self, "Move to project", "Destination:", names, 0, False)
            if not ok:
                return
            proj = next(p for p in projects if p.name == choice)
            await self.engine.store.update_session_project(session_id, proj.id)
            await self._refresh_sessions()
            self.session_list.reveal_session(session_id)
        self._fire(run())

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
            sessions: list = []
            for p in await self.engine.store.list_projects():
                sessions.extend(await self.engine.store.list_sessions(p.id))
            if sessions:
                sessions.sort(key=lambda s: s.updated_at, reverse=True)
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
        self.status_project.setToolTip(directory)
        # an isolated worktree's directory name is the raw session id, which
        # says nothing — show the branch short-name, as the sidebar row does
        branch = getattr(session, "worktree_branch", "") or ""
        label = branch.split("/")[-1] if branch else Path(directory).name
        self.setWindowTitle(f"ByteBarn — {label}")
        self.dir_button.setText(f"📁 {label}")
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
        projects = await self.engine.store.list_projects()
        # load every project's sessions so moved sessions all show; subagent
        # children stay out of the sidebar (reached via task cards / stage)
        sessions: list = []
        for p in projects:
            sessions.extend(await self.engine.store.list_sessions(p.id))
        sessions_by_project: dict[str, list] = {}
        for s in sessions:
            sessions_by_project.setdefault(s.project_id, []).append(s)
        running = {s.id for s in sessions if self.engine.is_running(s.id)} | self._running
        agent_colors = {a.name: a.color or "#98c379" for a in self.engine.agents.agents.values()}
        self.session_list.populate(
            projects, sessions_by_project, running, self.current_session_id or "",
            agent_colors,
            default_project_id=self.engine.project.id if self.engine.project else "")
        if self.sidebar.currentIndex() == 1 and self.workspace.project_id:
            await self.workspace.load(self.workspace.project_id)
        if self.current_session_id:
            current = await self.engine.store.get_session(self.current_session_id)
            if current:
                self._update_header(current)

    # ------------------------------------------------------------------ prompt

    def _submit(self, text: str) -> None:
        if not self.current_session_id:
            return
        session_id = self.current_session_id
        if self._pending_edit is not None:
            message_id, self._pending_edit = self._pending_edit, None

            async def _do_edit() -> None:
                await self.engine.edit_and_rerun(session_id, message_id, text)
                await self._load_session(session_id)
                self.prompt_bar.set_running(True)
                self._running.add(session_id)
            self._fire(_do_edit())
            return
        files = self.prompt_bar.take_attachments()
        queued = self.engine.is_running(session_id)
        self.prompt_bar.set_running(True)
        self.transcript.add_user_text(f"local-{id(text)}", self._display_text(text), queued=queued)
        for i, path in enumerate(files):
            self.transcript.on_part_updated(
                f"local-file-{id(text)}-{i}", "file", {"path": path}, role="user")
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
                    files=files or None,
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

    def _on_prompt_abort(self) -> None:
        """Esc in the prompt bar: cancel an in-progress edit, else stop the run."""
        if self._pending_edit is not None:
            self._pending_edit = None
            self.prompt_bar.editor.clear()
            self.statusBar().showMessage("Edit cancelled", 2000)
            return
        self._fire(self._abort())

    # ------------------------------------------------------------------ conversation actions

    def _begin_edit(self, message_id: str, raw_text: str) -> None:
        """Context-menu "Edit & re-run": prefill the prompt bar as a fork."""
        if not self.current_session_id:
            return
        if not message_id:
            self.statusBar().showMessage(
                "Can't edit this message yet — reopen the session first", 3000)
            return
        self._pending_edit = message_id
        self.prompt_bar.editor.setPlainText(raw_text)
        cursor = self.prompt_bar.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.prompt_bar.editor.setTextCursor(cursor)
        self.prompt_bar.editor.setFocus()
        self.statusBar().showMessage(
            "Editing an earlier message — Enter re-runs from there, Esc cancels", 5000)

    async def _regenerate(self) -> None:
        sid = self.current_session_id
        if not sid or self.engine.is_running(sid):
            return
        if await self.engine.regenerate(sid):
            await self._load_session(sid)
            self._running.add(sid)
            self.prompt_bar.set_running(True)
            session = await self.engine.store.get_session(sid)
            agent = session.agent if session else "agent"
            try:
                self.transcript.show_thinking(
                    _AGENT_DISPLAY.get(agent, agent), self.engine.agents.color_of(agent))
            except Exception:
                pass

    def _toggle_search(self) -> None:
        show = not self.search_bar.isVisible()
        self.search_bar.setVisible(show)
        if show:
            self.search_edit.setFocus()
            self.search_edit.selectAll()
        else:
            self.search_edit.clear()
            self.transcript.clear_search()

    def _search_changed(self, query: str) -> None:
        count = self.transcript.search(query)
        self.search_count.setText(
            "" if not query else (f"{count} match{'es' if count != 1 else ''}"))

    def _search_nav(self, delta: int) -> None:
        position = self.transcript.search_step(delta)
        if position:
            total = len(self.transcript._matches)
            self.search_count.setText(f"{position}/{total}")

    def _export_transcript(self) -> None:
        if not self.current_session_id:
            return
        sid = self.current_session_id

        async def run() -> None:
            markdown = await self.engine.export_markdown(sid)
            session = await self.engine.store.get_session(sid)
            stem = (session.title or "session").strip().replace("/", "-") or "session"
            suggested = str(Path.home() / f"{stem}.md")
            path, _ = QFileDialog.getSaveFileName(
                self, "Export transcript", suggested, "Markdown (*.md)")
            if path:
                Path(path).write_text(markdown)
                self.statusBar().showMessage(f"Exported to {path}", 4000)

        self._fire(run())

    def _cycle_session(self, delta: int) -> None:
        """Ctrl+Tab: jump between this project's recent sessions."""
        if not self.engine.project:
            return

        async def run() -> None:
            sessions = await self.engine.store.list_sessions(self.engine.project.id)
            ordered = sorted((s for s in sessions if not s.parent_session_id),
                             key=lambda s: s.updated_at, reverse=True)
            if len(ordered) < 2:
                return
            ids = [s.id for s in ordered]
            try:
                at = ids.index(self.current_session_id)
            except ValueError:
                at = 0
            self._session_stack.clear()
            await self._load_session(ids[(at + delta) % len(ids)])
            await self._refresh_sessions()

        self._fire(run())

    def _toggle_preview(self) -> None:
        show = not self.preview.isVisible()
        self.preview.setVisible(show)
        if show:
            self.content_split.setSizes([3, 2])
            self.preview.address.setFocus()

    def _show_preview(self) -> None:
        if not self.preview.isVisible():
            self.preview.setVisible(True)
            self.content_split.setSizes([3, 2])

    def _toggle_terminal(self) -> None:
        show = not self.terminal_panel.isVisible()
        if show:
            self._show_terminal()
        else:
            self.terminal_panel.setVisible(False)
            self.mid_split.setSizes([1, 0])

    def _show_terminal(self) -> None:
        self.terminal_panel.setVisible(True)
        self.terminal_panel.refresh_from_hub()
        sizes = self.mid_split.sizes()
        total = sum(sizes) or max(self.mid_split.height(), 600)
        term_h = max(180, min(280, total // 3))
        self.mid_split.setSizes([max(200, total - term_h), term_h])

    def _on_terminal_closed(self) -> None:
        self.mid_split.setSizes([1, 0])

    def _preview_file(self, path: str) -> None:
        self._show_preview()
        self.preview.show_file(path)

    def _preview_html(self, html: str) -> None:
        self._show_preview()
        self.preview.show_html(html)

    def _open_file_viewer(self, path: str) -> None:
        from .file_viewer import FileViewerDialog

        FileViewerDialog(path, self).exec()

    def _open_side_chat(self) -> None:
        from .side_chat import SideChatDialog

        dialog = SideChatDialog(
            self.engine, self.prompt_bar.current_model() or self._default_model(),
            self.current_session_id, self)
        dialog.show()  # modeless — keep working in the main window

    async def _content_search(self, query: str) -> None:
        """Sidebar search: match transcript content, not just titles."""
        if not query.strip():
            await self._refresh_sessions()
            return
        results = await self.engine.store.search_sessions(query.strip())
        running = {s.id for s, _ in results if self.engine.is_running(s.id)}
        agent_colors = {a.name: a.color or "#98c379"
                        for a in self.engine.agents.agents.values()}
        self.session_list.show_search_results(results, running, agent_colors)

    def _action(self, action: str) -> None:
        if action == "compact" and self.current_session_id:
            self._fire(self.engine.compact(self.current_session_id))
        elif action == "new_session":
            self._prompt_new_session()
        elif action == "open_model_picker":
            self.prompt_bar.model_combo.showPopup()
        elif action == "open_agent_editor":
            self._open_agent_editor()
        elif action == "plan_mode":
            self._set_mode_ui(PLAN)
        elif action == "show_diff" and self.current_session_id:
            self._fire(self.engine.submit_prompt(self.current_session_id, "/diff"))
        elif action == "show_context" and self.current_session_id:
            self._fire(self.engine.submit_prompt(self.current_session_id, "/context"))
        elif action == "rewind" and self.current_session_id:
            self._fire(self.engine.submit_prompt(self.current_session_id, "/rewind"))

    def _agent_changed(self, agent: str) -> None:
        agent = _AGENT_INTERNAL.get(agent, agent)
        if self.current_session_id and agent:
            self._fire(self.engine.store.update_session(self.current_session_id, agent=agent))
            # If this agent pins a default model (incl. Claude Code), apply it
            # so the session actually runs on that provider.
            try:
                pinned = self.engine.agents.get(agent).model or ""
            except KeyError:
                pinned = ""
            if pinned:
                self._refresh_pickers(agent=agent, model=pinned)
                self._model_changed(pinned)

    def _model_changed(self, model: str) -> None:
        if self.current_session_id and model:
            self._fire(self.engine.store.update_session(self.current_session_id, model=model))
        if not model:
            return
        # Claude Code is a runtime, not a last_model for native sessions —
        # sticky CC is stored via runtime=claude-code instead.
        if model.startswith(f"{CLAUDE_CODE_PROVIDER}/"):
            self._set_runtime(CLAUDE_CODE_PROVIDER)
            return
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

    def _set_runtime(self, name: str) -> None:
        """Flip Engine runtime (native | claude-code) without reload_config."""
        name = (name or "native").strip() or "native"
        self._remember_setting("runtime", name)
        self._update_runtime_status()

    def _update_runtime_status(self) -> None:
        if not hasattr(self, "status_runtime"):
            return
        if self.engine.runtime_name() == CLAUDE_CODE_PROVIDER:
            self.status_runtime.setText("◈ Claude Code")
            self.status_runtime.setVisible(True)
        else:
            self.status_runtime.clear()
            self.status_runtime.setVisible(False)

    def _default_model(self) -> str:
        if self.engine.runtime_name() == CLAUDE_CODE_PROVIDER:
            return CLAUDE_CODE_DEFAULT_MODEL
        return (self.engine.config.model_extra or {}).get("last_model") \
            or self.engine.config.model

    # ------------------------------------------------------------------ pickers & status

    def _picker_providers(
        self, preferred: str = "",
    ) -> list[tuple[str, str]]:
        """Connected API providers plus Claude Code — id/label pairs for the combo.

        Claude Code is listed **first** with a human label so it is obvious in
        the dropdown (not buried as a trailing ``claude-code`` slug).
        ``connected_providers`` already includes Claude Code (runtime=local);
        we still force it first for discoverability.
        """
        connected = list(
            connected_providers(self.engine.config, self.engine.providers.auth))
        items: list[tuple[str, str]] = [
            (CLAUDE_CODE_PROVIDER, CLAUDE_CODE_LABEL),
        ]
        seen = {CLAUDE_CODE_PROVIDER}
        for pid in connected:
            if pid in seen:
                continue
            # Runtimes get a friendly label; API providers keep bare ids.
            label = (
                KNOWN_PROVIDERS[pid].label
                if pid in KNOWN_PROVIDERS and KNOWN_PROVIDERS[pid].runtime
                else pid
            )
            items.append((pid, label))
            seen.add(pid)
        if preferred and preferred not in seen:
            # Keep preferred available, but never above Claude Code.
            items.insert(1, (preferred, preferred))
        return items

    def _refresh_pickers(self, agent: str = "", model: str = "") -> None:
        agents = [_AGENT_DISPLAY.get(a.name, a.name) for a in self.engine.agents.primaries()]
        self.prompt_bar.set_agents(agents, _AGENT_DISPLAY.get(agent, agent) or "build")

        # Runtime wins for which backend is active; show the matching picker.
        if self.engine.runtime_name() == CLAUDE_CODE_PROVIDER:
            if not (model or "").startswith(f"{CLAUDE_CODE_PROVIDER}/"):
                model = CLAUDE_CODE_DEFAULT_MODEL

        model = model or self._default_model()
        if model and "/" not in model:
            # bare model id (e.g. from model_combo.currentText) — keep current provider
            provider = self.prompt_bar.current_provider()
            if not provider or provider.startswith("⚡"):
                default = self._default_model()
                provider = default.partition("/")[0] if default else ""
            model_id = model
        else:
            provider, _, model_id = model.partition("/")
        providers = self._picker_providers(provider)
        provider_ids = [pid for pid, _ in providers]
        if provider and provider not in provider_ids:
            if model_id:
                providers = [(provider, provider)] + [
                    p for p in providers if p[0] != provider]
            else:
                provider = next(
                    (pid for pid, _ in providers if pid != CLAUDE_CODE_PROVIDER),
                    providers[0][0] if providers else "",
                )
                model_id = ""
        elif not provider:
            provider = next(
                (pid for pid, _ in providers if pid != CLAUDE_CODE_PROVIDER),
                providers[0][0] if providers else "",
            )
            model_id = model_id or ""
        self.prompt_bar.set_providers(providers, provider)
        self._set_provider_models(provider, model_id)
        self._update_runtime_status()
    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        """Cached/curated list immediately; always refresh live from the provider."""
        if not provider:
            self.prompt_bar.set_models([], "")
            return
        if provider == CLAUDE_CODE_PROVIDER:
            models = list(CLAUDE_CODE_MODELS)
            select = current_id if current_id in models else "default"
            self.prompt_bar.set_models(models, select)
            return
        cached = self.engine.cached_models(provider)
        models = list(cached) if cached is not None else curated_models(provider)
        if current_id and current_id not in models:
            models.insert(0, current_id)
        # Only auto-select when current_id explicitly provided.
        # Otherwise preserve an existing selection for the provider, else leave empty.
        if current_id:
            select = current_id
        elif self.prompt_bar.current_provider() == provider:
            keep = self.prompt_bar.model_combo.currentText().strip()
            select = keep if keep in models else ""
        else:
            select = ""
        self.prompt_bar.set_models(models, select)
        self._fire(self._load_live_models(provider))

    async def _load_live_models(self, provider: str) -> None:
        if provider == CLAUDE_CODE_PROVIDER:
            return
        live = await self.engine.list_models(provider, force=True)
        if self.prompt_bar.current_provider() != provider:
            return  # provider changed meanwhile
        if not live:
            return  # leave cache/curated placeholder on fetch failure
        keep = self.prompt_bar.model_combo.currentText()
        # live catalog is authoritative — drop stale curated ids
        merged = list(dict.fromkeys(
            ([keep] if keep and keep not in live else []) + live))
        # Always delegate to set_models (signals blocked inside it). Never
        # auto-pick merged[0] when keep is empty.
        self.prompt_bar.set_models(merged, keep if keep else "")

    def _provider_changed(self, provider: str) -> None:
        if not provider or provider.startswith("⚡"):
            return
        # Accept either the stable id or the friendly label from older paths.
        if provider in (CLAUDE_CODE_PROVIDER, CLAUDE_CODE_LABEL):
            provider = CLAUDE_CODE_PROVIDER
            # Local Claude Code CLI — flip runtime and seed a valid model string
            # so Send works immediately without a second click.
            # Keep the combo selection in sync when this is invoked programmatically
            # (tests / restore); the user path already changed the combo first.
            self.prompt_bar.set_providers(
                self._picker_providers(provider), provider)
            self._set_runtime(CLAUDE_CODE_PROVIDER)
            self._set_provider_models(provider, current_id="default")
            self._model_changed(CLAUDE_CODE_DEFAULT_MODEL)
            return
        # Any real API provider restores the native Runner.
        if self.engine.runtime_name() == CLAUDE_CODE_PROVIDER:
            self._set_runtime("native")
        # provider dropdown changed — do not auto-pick a model; just populate
        # the list and let the user choose. The model_changed handler will
        # persist once they actually pick one.
        self._set_provider_models(provider, current_id="")

    async def _refresh_cost(self) -> None:
        if not self.current_session_id:
            self.context_meter.setVisible(False)
            self.review_button.setVisible(False)
            return
        messages = await self.engine.store.list_messages(self.current_session_id)
        tokens = sum(m.tokens_in + m.tokens_out for m in messages)
        cost = sum(m.cost for m in messages)
        self.status_cost.setText(f"{tokens:,} tok · ${cost:.3f}")
        await self._refresh_context_meter()
        self.review_button.setVisible(
            self.engine.checkpoints.last(self.current_session_id) is not None)

    async def _refresh_context_meter(self) -> None:
        """Context-window usage for the open session; click = compact."""
        used, window = await self.engine.context_usage(self.current_session_id)
        if not used or not window:
            self.context_meter.setVisible(False)
            return
        pct = min(100, round(100 * used / window))
        blocks = "▰" * max(1, pct // 20) + "▱" * (5 - max(1, pct // 20))
        self.context_meter.setText(f"{blocks} {pct}% ctx")
        color = "#e06c75" if pct >= 80 else ("#e5c07b" if pct >= 60 else "#8f96a3")
        self.context_meter.setStyleSheet(f"color:{color};")
        self.context_meter.setToolTip(
            f"Context window: {used:,} of {window:,} tokens used last turn.\n"
            "Click to compact — old history is summarized to free space.")
        self.context_meter.setVisible(True)

    def _compact_clicked(self) -> None:
        if not self.current_session_id:
            return
        session_id = self.current_session_id

        async def run() -> None:
            await self.engine.compact(session_id)
            await self._load_session(session_id)

        self._fire(run())

    def _open_run_review(self) -> None:
        if not self.current_session_id:
            return
        from .run_review import RunReviewDialog

        RunReviewDialog(self.engine, self.current_session_id, self).exec()

    def _notify(self, title: str, body: str) -> None:
        """Desktop notification when the user is away from the window."""
        tray = getattr(self, "_tray", None)
        if tray is not None and not self.isActiveWindow():
            tray.showMessage(title, body)

    async def _refresh_git(self) -> None:
        proc = await asyncio.create_subprocess_shell(
            "git branch --show-current",
            cwd=str(self.engine.project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        self.status_git.setText(out.decode().strip() or "no git")

    def _clamp_initial_geometry(self, width: int, height: int) -> None:
        """Size and center the window within the current screen's available area."""
        screen = None
        try:
            screen = self.screen()
        except Exception:
            screen = None
        if screen is None:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
        if screen is None:
            self.resize(width, height)
            return
        avail = screen.availableGeometry()
        max_w = max(640, avail.width() - 40)
        max_h = max(480, avail.height() - 40)
        w = min(width, max_w)
        h = min(height, max_h)
        self.resize(w, h)
        x = avail.x() + max(0, (avail.width() - w) // 2)
        y = avail.y() + max(0, (avail.height() - h) // 2)
        self.move(x, y)

    def _restore_stage_height(self) -> None:
        """Give the crew stage its remembered share when it appears.

        Keep sizes within the current splitter total so Qt cannot grow the
        main window past the screen (e.g. tall stage_height from another
        display, or while maximized/fullscreen).
        """
        raw = int((self.engine.config.model_extra or {}).get("stage_height") or 164)
        total = sum(self.stage_split.sizes())
        if total <= 0:
            total = max(self.stage_split.height(), 0)
        if total <= 0:
            return
        stage_min = max(self.crew_stage.minimumHeight(), 80)
        transcript_floor = 120
        stage_h = max(stage_min, min(raw, total - transcript_floor))
        if stage_h < stage_min:
            stage_h = min(stage_min, max(0, total // 3))
        self.stage_split.setSizes([max(0, total - stage_h), stage_h])

    def _stage_resized(self, _pos: int = 0, _index: int = 0) -> None:
        """Debounced persist of the crew-stage pane height."""
        from PySide6.QtCore import QTimer

        if self._stage_save_timer is None:
            self._stage_save_timer = QTimer(self)
            self._stage_save_timer.setSingleShot(True)
            self._stage_save_timer.timeout.connect(self._save_stage_height)
        self._stage_save_timer.start(500)

    def _save_stage_height(self) -> None:
        from ..engine.config import patch_config_file

        sizes = self.stage_split.sizes()
        if len(sizes) < 2 or sizes[1] <= 0:
            return
        total = sum(sizes)
        stage_h = int(sizes[1])
        if total > 0:
            stage_h = min(stage_h, max(200, int(total * 0.6)))
        try:
            patch_config_file(self.engine.global_dir / "config.json",
                              {"stage_height": stage_h})
        except Exception:
            pass

    def _mode_changed(self, index: int) -> None:
        # set_session_mode also releases permission prompts already waiting
        mode = SESSION_MODES[index]
        self.engine.set_session_mode(mode)
        from ..engine.config import patch_config_file
        patch_config_file(self.engine.global_dir / "config.json", {"session_mode": mode})
        self._update_plan_mode_chrome()

    def _set_mode_ui(self, mode: str) -> None:
        """Programmatically select a permission mode (e.g. /plan)."""
        try:
            index = SESSION_MODES.index(mode)
        except ValueError:
            return
        if self.mode_combo.currentIndex() == index:
            # still re-apply so /plan is idempotent when already in Plan
            self.engine.set_session_mode(mode)
            self._update_plan_mode_chrome()
            return
        self.mode_combo.setCurrentIndex(index)  # fires _mode_changed

    def _update_plan_mode_chrome(self) -> None:
        """Subtle prompt-bar cue when Plan mode is active."""
        if self.engine.session_mode == PLAN:
            self.prompt_bar.editor.setPlaceholderText(
                "Plan mode — explore & design only (no edits). Switch to Ask to implement.")
        else:
            self.prompt_bar.editor.setPlaceholderText(
                "Message the crew — / for commands, @ for files")

    def _on_session_moved(self, session_id: str, project_id: str | None) -> None:
        async def run() -> None:
            await self.engine.store.update_session_project(session_id, project_id)
            await self._refresh_sessions()
            # the destination project may be collapsed — surface the row
            self.session_list.reveal_session(session_id)

        self._fire(run())
        asyncio.ensure_future(self._refresh_sessions())

    # ------------------------------------------------------------------ dialogs

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.engine, self)
        dialog.exec()
        # crew style may have flipped — redraw sidebar avatars and header
        self._fire(self._refresh_sessions())
        if self.current_session_id:
            self._fire(self._load_session(self.current_session_id))

    def _open_providers(self) -> None:
        from .provider_manager import ProviderManager

        dialog = ProviderManager(self.engine, self)
        dialog.exec()

    def _open_agent_editor(self) -> None:
        editor = AgentEditor(self.engine, self)
        editor.exec()

    def _open_mcp(self) -> None:
        from .mcp_dialog import MCPDialog

        MCPDialog(self.engine, self).exec()

    # ------------------------------------------------------------------ updates

    def _check_updates(self, manual: bool = False) -> None:
        from .. import updater

        async def run() -> None:
            info = await updater.check_for_update()
            self._show_update_result(info, manual)

        self._fire(run())

    def _maybe_auto_update_check(self) -> None:
        """Startup check, throttled to once a day; off via updates.auto_check."""
        import time as _time

        from ..engine.config import patch_config_file

        extra = self.engine.config.model_extra or {}
        settings = extra.get("updates") or {}
        if settings.get("auto_check") is False:
            return
        if _time.time() - float(settings.get("last_check") or 0) < 86_400:
            return
        try:
            patch_config_file(self.engine.global_dir / "config.json",
                              {"updates.last_check": int(_time.time())})
        except Exception:
            pass
        self._check_updates(manual=False)

    def _show_update_result(self, info, manual: bool) -> None:
        from PySide6.QtWidgets import QMessageBox

        from .. import updater

        if not info.available:
            self.update_button.setVisible(False)
            if manual:
                QMessageBox.information(
                    self, "Check for Updates",
                    info.message or "You're up to date.")
            return

        self.update_button.setVisible(True)
        if not manual:
            self._notify("ByteBarn update available", info.message)
            return  # unobtrusive at startup — button + notification only

        if info.kind == "release":
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(info.url))
            return

        commits = "\n".join(f"  • {c}" for c in info.commits[:10])
        note = ("\n\nNote: you have local uncommitted changes; the update "
                "only proceeds if they don't conflict." if info.dirty else "")
        answer = QMessageBox.question(
            self, "Update ByteBarn",
            f"{info.message}:\n\n{commits}{note}\n\nUpdate and restart now?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return

        async def apply() -> None:
            root = updater.repo_root()
            ok, detail = await updater.apply_git_update(root)
            if not ok:
                QMessageBox.warning(self, "Update failed", detail)
                return
            confirm = QMessageBox.question(
                self, "Update installed",
                "ByteBarn was updated. Restart now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if confirm == QMessageBox.Yes:
                # stop is idempotent; flag so _fire drops any late UI work
                self._shutting_down = True
                await self.engine.stop()
                updater.restart()

        self._fire(apply())

    def _open_skill_editor(self) -> None:
        from .skill_editor import SkillEditor

        editor = SkillEditor(self.engine, self)
        editor.exec()

    # ------------------------------------------------------------------ util

    def _fire(self, coro) -> None:
        """Schedule a coroutine; track it so shutdown can cancel+drain."""
        if getattr(self, "_shutting_down", False):
            coro.close()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (widget-only tests): drop silently
            coro.close()
            return
        task = loop.create_task(coro)
        tasks = getattr(self, "_bg_tasks", None)
        if tasks is None:
            self._bg_tasks = set()
            tasks = self._bg_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def closeEvent(self, event) -> None:
        if getattr(self, "_shutting_down", False):
            # second close after shutdown finished — allow destroy
            event.accept()
            super().closeEvent(event)
            return
        event.ignore()  # keep window alive until async shutdown completes
        self._shutting_down = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # no loop — best effort sync path
            event.accept()
            super().closeEvent(event)
            return
        loop.create_task(self._shutdown())

    async def _shutdown(self) -> None:
        """Cancel UI tasks, stop engine, then quit the app."""
        # Cancel long-lived bootstrap tasks and fire-and-forget background work
        tasks = list(getattr(self, "_tasks", []) or [])
        tasks.extend(list(getattr(self, "_bg_tasks", set()) or set()))
        for t in tasks:
            if t is not None and not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Best-effort tear-down of the lazy WebEngine preview (can hold the
        # process open after the window is gone if left running).
        preview = getattr(self, "preview", None)
        if preview is not None:
            shutdown = getattr(preview, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass

        # Close user PTYs before engine stop
        panel = getattr(self, "terminal_panel", None)
        if panel is not None:
            try:
                await panel.shutdown()
            except Exception:
                pass

        # Engine stop drains runs/MCP/providers/store
        try:
            await asyncio.wait_for(self.engine.stop(), timeout=8.0)
        except Exception:
            pass
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()
