"""Inline file viewer/editor: open a file from the chat, edit, save."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

_MAX_BYTES = 2 * 1024 * 1024


class FileViewerDialog(QDialog):
    def __init__(self, path: str | Path, parent=None):
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(str(self._path))
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel(f"<b>{self._path.name}</b>")
        title.setToolTip(str(self._path))
        header.addWidget(title, 1)
        self.status = QLabel("")
        header.addWidget(self.status)
        layout.addLayout(header)

        self.editor = QPlainTextEdit()
        font = QFont("Menlo")
        font.setStyleHint(QFont.Monospace)
        font.setPointSizeF(12)
        self.editor.setFont(font)
        layout.addWidget(self.editor, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("send")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(close)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self._load()

    def _load(self) -> None:
        try:
            if self._path.stat().st_size > _MAX_BYTES:
                self.editor.setPlainText("(file too large to edit here)")
                self.editor.setReadOnly(True)
                self.save_button.setEnabled(False)
                return
            self.editor.setPlainText(self._path.read_text(errors="replace"))
        except OSError as exc:
            self.editor.setPlainText(f"(could not read file: {exc})")
            self.editor.setReadOnly(True)
            self.save_button.setEnabled(False)

    def _save(self) -> None:
        try:
            self._path.write_text(self.editor.toPlainText())
            self.status.setText("saved ✓")
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
