"""Terminal Manager — view backend process output and spawn local shells."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .pty_session import PtySession, spawn_shell

# Strip common CSI / OSC sequences so raw PTY output is readable in a plain view
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]"
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TerminalView(QPlainTextEdit):
    """Monospace output view; interactive when backed by a PTY."""

    def __init__(self, interactive: bool = False, parent=None):
        super().__init__(parent)
        self._interactive = interactive
        self._pty: PtySession | None = None
        self.setReadOnly(not interactive)
        self.setMaximumBlockCount(20_000)
        font = QFont("Menlo")
        if not font.exactMatch():
            font = QFont("ui-monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(12)
        self.setFont(font)
        self.setStyleSheet(
            "QPlainTextEdit { background: #1e1e1e; color: #d4d4d4; border: none; }")
        self.setPlaceholderText("No output yet…")

    def set_pty(self, session: PtySession | None) -> None:
        self._pty = session
        self._interactive = session is not None
        self.setReadOnly(not self._interactive)

    def append_text(self, text: str) -> None:
        if not text:
            return
        clean = _strip_ansi(text)
        at_end = self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() - 4
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(clean)
        if at_end:
            self.moveCursor(QTextCursor.End)

    def set_text(self, text: str) -> None:
        self.setPlainText(_strip_ansi(text))
        self.moveCursor(QTextCursor.End)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._interactive or self._pty is None:
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_C and mods & Qt.ControlModifier:
            self._pty.write("\x03")
            return
        if key == Qt.Key_D and mods & Qt.ControlModifier:
            self._pty.write("\x04")
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._pty.write("\r")
            return
        if key == Qt.Key_Backspace:
            self._pty.write("\x7f")
            return
        if key == Qt.Key_Tab:
            self._pty.write("\t")
            return
        if key == Qt.Key_Up:
            self._pty.write("\x1b[A")
            return
        if key == Qt.Key_Down:
            self._pty.write("\x1b[B")
            return
        if key == Qt.Key_Left:
            self._pty.write("\x1b[D")
            return
        if key == Qt.Key_Right:
            self._pty.write("\x1b[C")
            return
        text = event.text()
        if text:
            self._pty.write(text)
            return
        super().keyPressEvent(event)


class TerminalPanel(QWidget):
    """Bottom/side manager: list of terminals + active view."""

    closed = Signal()

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._views: dict[str, TerminalView] = {}
        self._ptys: dict[str, PtySession] = {}
        self._active_id: str | None = None

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        title = QLabel("<b>Terminals</b>")
        self.new_btn = QPushButton("+ Shell")
        self.new_btn.setFlat(True)
        self.new_btn.setToolTip("Open a new local shell in the project directory")
        self.new_btn.clicked.connect(self._new_shell)
        self.kill_btn = QPushButton("Kill")
        self.kill_btn.setFlat(True)
        self.kill_btn.setToolTip("Kill the selected local shell (or dismiss a finished backend)")
        self.kill_btn.clicked.connect(self._kill_selected)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setFlat(True)
        self.clear_btn.clicked.connect(self._clear_view)
        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedWidth(28)
        close.clicked.connect(self._close)
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(self.new_btn)
        bar.addWidget(self.kill_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(close)

        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentItemChanged.connect(self._on_select)
        self.stack_host = QWidget()
        self.stack_layout = QVBoxLayout(self.stack_host)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)
        self._empty = QLabel(
            "No terminals yet.\n\n"
            "• Claude Code runs appear here automatically.\n"
            "• Click + Shell for a local interactive terminal.")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignCenter)
        self.stack_layout.addWidget(self._empty)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.list)
        split.addWidget(self.stack_host)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)
        layout.addLayout(bar)
        layout.addWidget(split, 1)
        self.setMinimumHeight(140)

    # -- public API ----------------------------------------------------------

    def set_engine(self, engine) -> None:
        self.engine = engine
        self.refresh_from_hub()

    def refresh_from_hub(self) -> None:
        if self.engine is None:
            return
        for info in self.engine.terminals.list():
            self._ensure_backend_item(info.id, info.title, info.kind, info.status)
            view = self._views.get(info.id)
            if view is not None:
                snap = self.engine.terminals.snapshot(info.id)
                if snap and not view.toPlainText():
                    view.set_text(snap)

    def handle_event(self, event) -> None:
        name = getattr(event, "name", "")
        if name == "terminal.opened":
            self._ensure_backend_item(
                event.terminal_id, event.title or event.terminal_id,
                event.kind, "running")
            if event.kind != "user" and self.engine is not None:
                view = self._views.get(event.terminal_id)
                if view is not None:
                    view.set_text(self.engine.terminals.snapshot(event.terminal_id))
            # Auto-focus new backend terminals
            self._select_id(event.terminal_id)
        elif name == "terminal.chunk":
            view = self._views.get(event.terminal_id)
            if view is not None:
                view.append_text(event.text)
            else:
                self._ensure_backend_item(
                    event.terminal_id, event.terminal_id, "claude-code", "running")
                view = self._views.get(event.terminal_id)
                if view is not None:
                    if self.engine is not None:
                        view.set_text(self.engine.terminals.snapshot(event.terminal_id))
                    else:
                        view.append_text(event.text)
        elif name == "terminal.closed":
            self._mark_status(event.terminal_id, "exited", event.exit_code)

    async def shutdown(self) -> None:
        for tid, pty in list(self._ptys.items()):
            await pty.close()
        self._ptys.clear()

    # -- internals -----------------------------------------------------------

    def _ensure_backend_item(
        self, tid: str, title: str, kind: str, status: str,
    ) -> None:
        if tid not in self._views:
            view = TerminalView(interactive=False)
            view.hide()
            self.stack_layout.addWidget(view)
            self._views[tid] = view
            if self.engine is not None:
                view.set_text(self.engine.terminals.snapshot(tid))
        # list row
        item = self._find_item(tid)
        label = self._format_label(title, kind, status)
        if item is None:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tid)
            item.setData(Qt.UserRole + 1, kind)
            self.list.addItem(item)
        else:
            item.setText(label)
        self._empty.hide()

    def _format_label(self, title: str, kind: str, status: str) -> str:
        icon = {"claude-code": "◈", "user": "❯", "bash": "$"}.get(kind, "•")
        suffix = "" if status == "running" else " (done)"
        return f"{icon} {title}{suffix}"

    def _find_item(self, tid: str) -> QListWidgetItem | None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item and item.data(Qt.UserRole) == tid:
                return item
        return None

    def _select_id(self, tid: str) -> None:
        item = self._find_item(tid)
        if item is not None:
            self.list.setCurrentItem(item)

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        self._show_view(tid)

    def _show_view(self, tid: str) -> None:
        self._active_id = tid
        for vid, view in self._views.items():
            view.setVisible(vid == tid)
        view = self._views.get(tid)
        if view is not None:
            view.setFocus()

    def _new_shell(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._spawn_shell())

    async def _spawn_shell(self) -> None:
        cwd = Path.home()
        if self.engine is not None:
            cwd = Path(self.engine.project_dir)
        tid = f"user:{uuid.uuid4().hex[:8]}"
        try:
            session = await spawn_shell(terminal_id=tid, cwd=cwd)
        except Exception as exc:
            view = TerminalView(interactive=False)
            view.set_text(f"Failed to open shell:\n{exc}\n")
            view.hide()
            self.stack_layout.addWidget(view)
            self._views[tid] = view
            self._ensure_backend_item(tid, "Shell (failed)", "user", "exited")
            self._select_id(tid)
            return

        self._ptys[tid] = session
        view = TerminalView(interactive=True)
        view.set_pty(session)
        view.hide()
        self.stack_layout.addWidget(view)
        self._views[tid] = view
        self._ensure_backend_item(tid, session.title, "user", "running")
        self._empty.hide()

        def on_data(text: str, _tid=tid) -> None:
            v = self._views.get(_tid)
            if v is not None:
                v.append_text(text)

        def on_exit(code, _tid=tid) -> None:
            self._mark_status(_tid, "exited", code)

        await session.start_reader(on_data, on_exit)
        self._select_id(tid)

    def _mark_status(self, tid: str, status: str, exit_code=None) -> None:
        item = self._find_item(tid)
        if item is None:
            return
        kind = item.data(Qt.UserRole + 1) or ""
        title = item.text()
        # strip prior suffix / icon for rebuild
        raw = title
        for prefix in ("◈ ", "❯ ", "$ ", "• "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        raw = raw.replace(" (done)", "")
        if exit_code not in (None, -1) and status == "exited":
            raw = f"{raw} [{exit_code}]"
        item.setText(self._format_label(raw, kind, status))
        if tid in self._ptys and status == "exited":
            self._ptys[tid].status = "exited"

    def _kill_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        kind = item.data(Qt.UserRole + 1)
        if kind == "user" and tid in self._ptys:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._close_pty(tid))
            return
        if kind == "claude-code" and self.engine is not None:
            info = self.engine.terminals.get(tid)
            sid = info.session_id if info else ""
            if sid and info and info.status == "running":
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(self.engine.abort(sid))
                return
        # dismiss finished backend from list
        self._remove_item(tid)

    async def _close_pty(self, tid: str) -> None:
        pty = self._ptys.pop(tid, None)
        if pty is not None:
            await pty.close()
        self._mark_status(tid, "exited", pty.exit_code if pty else None)

    def _remove_item(self, tid: str) -> None:
        item = self._find_item(tid)
        if item is not None:
            row = self.list.row(item)
            self.list.takeItem(row)
        view = self._views.pop(tid, None)
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        if self.engine is not None:
            try:
                self.engine.terminals.remove(tid)
            except Exception:
                pass
        if not self._views:
            self._empty.show()

    def _clear_view(self) -> None:
        if self._active_id and self._active_id in self._views:
            self._views[self._active_id].clear()

    def _close(self) -> None:
        self.setVisible(False)
        self.closed.emit()
