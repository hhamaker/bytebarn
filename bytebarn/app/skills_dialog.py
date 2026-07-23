"""Minimal Skills browser dialog."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QTextEdit, QVBoxLayout
)


class SkillsDialog(QDialog):
    def __init__(self, skills: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Skills Library")
        self.resize(700, 500)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show_skill)
        for name, skill in sorted(skills.items()):
            item = QListWidgetItem(f"{name}  —  {skill.description}")
            item.setData(0, skill)
            self.list.addItem(item)

        self.viewer = QTextEdit()
        self.viewer.setReadOnly(True)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        lay = QHBoxLayout()
        lay.addWidget(self.list, 1)
        lay.addWidget(self.viewer, 2)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.addLayout(lay, 1)
        root.addLayout(btn_row)
