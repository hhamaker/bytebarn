"""Review the last run's file changes: per-file unified diff, keep or revert.

Backed by engine.checkpoints — the runner snapshots every file before the
write/edit tools touch it, so Full-auto runs can be inspected and rolled
back after the fact."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

_ID_ROLE = Qt.UserRole


class RunReviewDialog(QDialog):
    def __init__(self, engine, session_id: str, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.checkpoint = engine.checkpoints.last(session_id)
        self.setWindowTitle("Review last run")
        self.setMinimumSize(680, 480)

        layout = QVBoxLayout(self)
        hint = QLabel("Files the last run wrote (via the write/edit tools)."
                      " Revert restores the pre-run contents.")
        hint.setStyleSheet("color:#8f96a3")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.files = QListWidget()
        self.files.currentItemChanged.connect(self._show_diff)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setStyleSheet("font-family: ui-monospace, Menlo, monospace;"
                                " font-size: 12px;")
        split = QSplitter()
        split.addWidget(self.files)
        split.addWidget(self.diff)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 460])
        layout.addWidget(split, 1)

        row = QHBoxLayout()
        self.revert_one = QPushButton("Revert file")
        self.revert_one.clicked.connect(self._revert_file)
        revert_all = QPushButton("Revert all…")
        revert_all.clicked.connect(self._revert_all)
        keep = QPushButton("Keep changes")
        keep.setDefault(True)
        keep.clicked.connect(self.accept)
        row.addWidget(self.revert_one)
        row.addWidget(revert_all)
        row.addStretch(1)
        row.addWidget(keep)
        layout.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        self.files.clear()
        self.diff.setPlainText("")
        if self.checkpoint is None:
            self.diff.setPlainText("The last run made no reviewable file changes.")
            self.revert_one.setEnabled(False)
            return
        for path in self.engine.checkpoints.changed_files(self.checkpoint):
            created = self.checkpoint.originals.get(path) == ""
            item = QListWidgetItem(("＋ " if created else "± ") + Path(path).name)
            item.setData(_ID_ROLE, path)
            item.setToolTip(path)
            self.files.addItem(item)
        if self.files.count():
            self.files.setCurrentRow(0)

    def _show_diff(self, item: QListWidgetItem, _prev=None) -> None:
        if item is None or self.checkpoint is None:
            return
        self.diff.setPlainText(
            self.engine.checkpoints.diff(self.checkpoint, item.data(_ID_ROLE)))

    def _revert_file(self) -> None:
        item = self.files.currentItem()
        if item is None or self.checkpoint is None:
            return
        self.engine.checkpoints.revert_file(self.checkpoint, item.data(_ID_ROLE))
        self._show_diff(item)

    def _revert_all(self) -> None:
        if self.checkpoint is None:
            return
        answer = QMessageBox.warning(
            self, "Revert run",
            f"Restore all {self.files.count()} files to their pre-run state?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.engine.checkpoints.revert_all(self.checkpoint)
            item = self.files.currentItem()
            if item is not None:
                self._show_diff(item)
