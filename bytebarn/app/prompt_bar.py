"""Prompt bar: growing editor, /-palette, @-file picker, agent/model pickers (spec §7.1)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_MAX_LINES = 4


class _Editor(QPlainTextEdit):
    """Prompt editor that accepts pasted images and dropped files."""

    image_added = Signal(object)     # QImage
    files_added = Signal(list)       # list[str]

    def canInsertFromMimeData(self, source) -> bool:
        return (source.hasImage() or source.hasUrls()
                or super().canInsertFromMimeData(source))

    def insertFromMimeData(self, source) -> None:
        if source.hasImage():
            self.image_added.emit(source.imageData())
            return
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            if paths:
                self.files_added.emit(paths)
                return
        super().insertFromMimeData(source)


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
    provider_changed = Signal(str)   # provider id
    model_changed = Signal(str)      # full "provider/model-id"
    action_requested = Signal(str)   # built-in command actions (compact/new/...)

    def __init__(self, commands=None, project_dir: Path | None = None,
                 attachments_dir: Path | None = None):
        super().__init__()
        self._commands = commands    # CommandRegistry | None
        self._project_dir = project_dir
        self._attachments_dir = attachments_dir
        self._attachments: list[str] = []
        self._running = False

        self.editor = _Editor()
        self.editor.setPlaceholderText("Message the crew — / for commands, @ for files")
        self.editor.installEventFilter(self)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.image_added.connect(self._add_pasted_image)
        self.editor.files_added.connect(self.add_attachments)
        self.agent_combo = QComboBox()
        self.agent_combo.setMinimumWidth(85)
        self.agent_combo.setToolTip("Agent for this session — /agents to edit them")
        self.agent_combo.currentTextChanged.connect(self.agent_changed)
        # two-stage model picker: provider first, then that provider's models
        self.provider_combo = QComboBox()
        self.provider_combo.setToolTip("Provider — connect more via ⚡ providers")
        self.provider_combo.currentTextChanged.connect(self.provider_changed)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip("Model — list is fetched live from the provider")
        self.model_combo.setMinimumWidth(240)
        self.model_combo.view().setMinimumWidth(380)
        self.provider_combo.setMinimumWidth(110)
        self.model_combo.currentTextChanged.connect(
            lambda _: self.model_changed.emit(self.current_model()))
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("send")  # styled by the modern theme only
        self.send_button.setToolTip("Enter to send · Shift+Enter for a newline · Esc to stop")
        self.send_button.clicked.connect(self._submit_or_abort)
        self.queue_label = QLabel()
        self.queue_label.setStyleSheet("color:#e5c07b; padding:0 6px;")
        self.queue_label.hide()

        # ChatGPT-style composer: one rounded surface holding the editor on
        # top and the quiet picker row + send below it
        from PySide6.QtWidgets import QFrame

        composer = QFrame()
        composer.setObjectName("composer")
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.agent_combo)
        controls.addWidget(self.provider_combo)
        controls.addWidget(self.model_combo)
        controls.addWidget(self.queue_label)
        controls.addStretch(1)
        controls.addWidget(self.send_button)
        self._chips = QHBoxLayout()
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setSpacing(4)
        self._chips.addStretch(1)
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 8, 10, 8)
        composer_layout.setSpacing(6)
        composer_layout.addLayout(self._chips)
        composer_layout.addWidget(self.editor)
        composer_layout.addLayout(controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 12)
        layout.addWidget(composer)

        self._popup = _Popup(self)
        self._popup.picked.connect(self._apply_completion)
        self._resize_editor()

    # -- sizing ------------------------------------------------------------

    def _resize_editor(self) -> None:
        metrics = QFontMetrics(self.editor.font())
        lines = min(max(self.editor.document().blockCount(), 1), _MAX_LINES)
        doc_margin = self.editor.document().documentMargin()
        frame = self.editor.frameWidth()
        cm = self.editor.contentsMargins()
        extra = 2 * doc_margin + 2 * frame + cm.top() + cm.bottom()
        height = metrics.lineSpacing() * lines + extra
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
        skip = {".git", ".venv", "node_modules", "__pycache__", ".bytebarn"}
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

    # -- attachments ----------------------------------------------------------

    def add_attachments(self, paths: list[str]) -> None:
        for path in paths:
            if path and path not in self._attachments:
                self._attachments.append(path)
        self._render_chips()

    def _add_pasted_image(self, image) -> None:
        """Save a clipboard image to the attachments dir and attach it."""
        import time

        directory = self._attachments_dir or (
            (self._project_dir or Path.home()) / ".bytebarn" / "attachments")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"pasted-{int(time.time() * 1000)}.png"
        if image is not None and image.save(str(path), "PNG"):
            self.add_attachments([str(path)])

    def take_attachments(self) -> list[str]:
        """Consume the staged attachments (called on submit)."""
        out, self._attachments = self._attachments, []
        self._render_chips()
        return out

    def _render_chips(self) -> None:
        while self._chips.count() > 1:  # keep the trailing stretch
            item = self._chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for path in self._attachments:
            chip = QPushButton(f"📎 {Path(path).name}  ✕")
            chip.setObjectName("pill")
            chip.setFlat(True)
            chip.setToolTip(f"{path} — click to remove")
            chip.clicked.connect(lambda *_, p=path: self._remove_attachment(p))
            self._chips.insertWidget(self._chips.count() - 1, chip)

    def _remove_attachment(self, path: str) -> None:
        self._attachments = [p for p in self._attachments if p != path]
        self._render_chips()

    # -- send / abort ---------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._running = running
        self.send_button.setText("■ Stop" if running else "Send")
        # disable combos + send when running
        for w in (self.agent_combo, self.provider_combo, self.model_combo, self.send_button):
            w.setEnabled(not running)
        if not running:
            # brief visual cue that run finished
            self.send_button.setStyleSheet("background-color:#3d7a3d;")  # green flash
            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, lambda: self.send_button.setStyleSheet(""))

    def _submit_or_abort(self, *_args, send_only: bool = False) -> None:
        if self._running and not send_only:
            self.aborted.emit()
            return
        text = self.editor.toPlainText().strip()
        if not text and self._attachments:
            text = "See the attached file(s)."
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

    def set_providers(self, providers: list[str], current: str) -> None:
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItems(providers)
        if not providers:
            self.provider_combo.addItem("⚡ connect a provider")
        elif current in providers:
            self.provider_combo.setCurrentText(current)
        self.provider_combo.blockSignals(False)

    def set_models(self, models: list[str], current: str) -> None:
        """``models`` are bare model ids for the selected provider.

        If ``current`` is empty, leave the combo without a selection (user must
        pick). Only auto-select when an explicit current id is provided.
        """
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if not models:
            self.model_combo.setCurrentText("")
            if self.model_combo.lineEdit():
                self.model_combo.lineEdit().setPlaceholderText("no models — check connection")
        elif current:
            self.model_combo.setCurrentText(current)
        else:
            # leave unselected; do not auto-pick first item
            self.model_combo.setCurrentText("")
        self.model_combo.blockSignals(False)

    def current_model(self) -> str:
        """Full "provider/model-id" string, or "" when incomplete."""
        provider = self.provider_combo.currentText()
        model_id = self.model_combo.currentText().strip()
        if not provider or provider.startswith("⚡") or not model_id:
            return ""
        return f"{provider}/{model_id}"

    def set_queue_depth(self, depth: int) -> None:
        if depth <= 0:
            self.queue_label.clear()
            self.queue_label.hide()
        else:
            self.queue_label.setText(f"{depth} queued · Esc drops queue")
            self.queue_label.show()
