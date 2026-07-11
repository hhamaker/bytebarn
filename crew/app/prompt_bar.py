"""Prompt bar: growing editor, /-palette, @-file picker, agent/model pickers (spec §7.1)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_MAX_LINES = 4


class _Popup(QListWidget):
    """Floating completion list for / commands and @ files."""

    picked = Signal(str)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip)
        self.setMaximumHeight(220)
        self.itemActivated.connect(self._emit)
        self.itemClicked.connect(self._emit)

    def _emit(self, item: QListWidgetItem) -> None:
        self.picked.emit(item.data(Qt.UserRole))
        self.hide()

    def show_items(self, items: list[tuple[str, str]], anchor: QWidget) -> None:
        """items: (display, value)"""
        self.clear()
        for display, value in items[:12]:
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, value)
            self.addItem(item)
        if not self.count():
            self.hide()
            return
        self.setCurrentRow(0)
        pos = anchor.mapToGlobal(anchor.rect().topLeft())
        self.move(pos.x(), pos.y() - self.sizeHint().height())
        self.setMinimumWidth(min(600, anchor.width()))
        self.show()


def fuzzy_match(query: str, candidate: str) -> bool:
    """Subsequence match, case-insensitive."""
    query = query.lower()
    candidate = candidate.lower()
    i = 0
    for ch in candidate:
        if i < len(query) and query[i] == ch:
            i += 1
    return i == len(query)


class PromptBar(QWidget):
    submitted = Signal(str)          # prompt text
    aborted = Signal()
    agent_changed = Signal(str)
    model_changed = Signal(str)
    action_requested = Signal(str)   # built-in command actions (compact/new/...)

    def __init__(self, commands=None, project_dir: Path | None = None):
        super().__init__()
        self._commands = commands    # CommandRegistry | None
        self._project_dir = project_dir
        self._running = False

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Prompt…  (/ commands, @ files, Enter to send)")
        self.editor.installEventFilter(self)
        self.editor.textChanged.connect(self._on_text_changed)
        self.agent_combo = QComboBox()
        self.agent_combo.setToolTip("Agent for this session — /agents to edit them")
        self.agent_combo.currentTextChanged.connect(self.agent_changed)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip("Model for this session — connect more via ⚡ providers")
        self.model_combo.currentTextChanged.connect(self.model_changed)
        self.send_button = QPushButton("Send")
        self.send_button.setToolTip("Enter to send · Shift+Enter for a newline · Esc to stop")
        self.send_button.clicked.connect(self._submit_or_abort)

        controls = QHBoxLayout()
        controls.addWidget(self.agent_combo)
        controls.addWidget(self.model_combo)
        controls.addStretch(1)
        controls.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.addWidget(self.editor)
        layout.addLayout(controls)

        self._popup = _Popup(self)
        self._popup.picked.connect(self._apply_completion)
        self._resize_editor()

    # -- sizing ------------------------------------------------------------

    def _resize_editor(self) -> None:
        metrics = QFontMetrics(self.editor.font())
        lines = min(max(self.editor.document().blockCount(), 1), _MAX_LINES)
        height = metrics.lineSpacing() * lines + 14
        self.editor.setFixedHeight(height)

    # -- events -------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj is self.editor and event.type() == QEvent.KeyPress:
            assert isinstance(event, QKeyEvent)
            key = event.key()
            if self._popup.isVisible():
                if key in (Qt.Key_Up, Qt.Key_Down):
                    row = self._popup.currentRow() + (1 if key == Qt.Key_Down else -1)
                    self._popup.setCurrentRow(max(0, min(row, self._popup.count() - 1)))
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                    item = self._popup.currentItem()
                    if item:
                        self._popup._emit(item)
                    return True
                if key == Qt.Key_Escape:
                    self._popup.hide()
                    return True
            if key in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self._submit_or_abort(send_only=True)
                return True
            if key == Qt.Key_Escape:
                self.aborted.emit()
                return True
        return super().eventFilter(obj, event)

    def _on_text_changed(self) -> None:
        self._resize_editor()
        text = self.editor.toPlainText()
        cursor_pos = self.editor.textCursor().position()
        before = text[:cursor_pos]
        if before.startswith("/") and "\n" not in before and " " not in before:
            self._show_command_palette(before[1:])
        elif "@" in before:
            at = before.rfind("@")
            token = before[at + 1 :]
            if " " not in token and "\n" not in token:
                self._show_file_picker(token)
            else:
                self._popup.hide()
        else:
            self._popup.hide()

    # -- palette / file picker ----------------------------------------------

    def _show_command_palette(self, query: str) -> None:
        if not self._commands:
            return
        items = [
            (f"/{c.name} — {c.description}", f"/{c.name} ")
            for c in self._commands.all()
            if fuzzy_match(query, c.name)
        ]
        self._popup.show_items(items, self.editor)

    def _show_file_picker(self, query: str) -> None:
        if not self._project_dir:
            return
        results: list[tuple[str, str]] = []
        skip = {".git", ".venv", "node_modules", "__pycache__", ".crew"}
        count = 0
        for path in self._project_dir.rglob("*"):
            if count > 2000:
                break
            count += 1
            if not path.is_file() or any(p in skip for p in path.parts):
                continue
            rel = str(path.relative_to(self._project_dir))
            if fuzzy_match(query, rel):
                results.append((rel, rel))
            if len(results) >= 12:
                break
        self._popup.show_items(results, self.editor)

    def _apply_completion(self, value: str) -> None:
        text = self.editor.toPlainText()
        cursor_pos = self.editor.textCursor().position()
        before = text[:cursor_pos]
        if before.startswith("/") and " " not in before:
            new_text = value + text[cursor_pos:]
        else:
            at = before.rfind("@")
            new_text = before[: at + 1] + value + " " + text[cursor_pos:]
        self.editor.setPlainText(new_text)
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    # -- send / abort ---------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._running = running
        self.send_button.setText("■ Stop" if running else "Send")

    def _submit_or_abort(self, *_args, send_only: bool = False) -> None:
        if self._running and not send_only:
            self.aborted.emit()
            return
        text = self.editor.toPlainText().strip()
        if not text:
            return
        if text.startswith("/") and self._commands:
            name = text[1:].split(" ", 1)[0]
            command = self._commands.get(name)
            if command and command.action:
                self.editor.clear()
                self.action_requested.emit(command.action)
                return
        self.editor.clear()
        self.submitted.emit(text)

    # -- pickers ----------------------------------------------------------------

    def set_agents(self, names: list[str], current: str) -> None:
        self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        self.agent_combo.addItems(names)
        if current in names:
            self.agent_combo.setCurrentText(current)
        self.agent_combo.blockSignals(False)

    def set_models(self, models: list[str], current: str) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if not models:
            # nothing connected: make the problem visible instead of showing a
            # default model that cannot actually run
            self.model_combo.setCurrentText("")
            if self.model_combo.lineEdit():
                self.model_combo.lineEdit().setPlaceholderText("no providers — open ⚡ providers")
        elif current:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)
