"""Skill manager dialog — edits live .md files under global/project .crew/skills/."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..engine.facade import Engine


class SkillEditor(QDialog):
    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Skills")
        self.resize(900, 600)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)

        self.body = QTextEdit()
        self.body.setPlaceholderText("Skill body (markdown)")

        btn_new = QPushButton("New")
        btn_new.clicked.connect(self._new_skill)
        btn_import = QPushButton("Import from GitHub…")
        btn_import.clicked.connect(self._import_from_github)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete_skill)
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save_current)

        top = QHBoxLayout()
        top.addWidget(btn_new)
        top.addWidget(btn_import)
        top.addWidget(btn_del)
        top.addStretch()
        top.addWidget(btn_save)

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.list)
        layout.addLayout(left, 1)
        layout.addWidget(self.body, 3)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self):
        self.list.clear()
        for sk in self.engine.list_skills():
            item = QListWidgetItem(f"{sk.name} ({sk.source})")
            item.setData(0, sk.name)
            item.setData(1, sk.source)
            self.list.addItem(item)

    def _on_select(self, item, _prev):
        if not item:
            self.body.clear()
            return
        name = item.data(0)
        sk = self.engine.skills.get(name)
        self.body.setPlainText(sk.body if sk else "")

    # ------------------------------------------------------------------
    def _skill_path(self, name: str, source: str) -> Path:
        base = self.engine.project_dir if source == "project" else self.engine.global_dir
        return base / ".crew" / "skills" / f"{name}.md"

    def _new_skill(self):
        name, ok = QInputDialog.getText(self, "New skill", "Skill name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        # default to project
        path = self._skill_path(name, "project")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# " + name + "\n\n")
        self.engine.skills.reload()
        self._reload()
        # select it
        for i in range(self.list.count()):
            if self.list.item(i).data(0) == name:
                self.list.setCurrentRow(i)
                break

    def _delete_skill(self):
        item = self.list.currentItem()
        if not item:
            return
        name = item.data(0)
        src = item.data(1)
        if QMessageBox.question(self, "Delete", f"Delete {name}?") != QMessageBox.Yes:
            return
        path = self._skill_path(name, src)
        if path.exists():
            path.unlink()
        self.engine.skills.reload()
        self._reload()

    def _save_current(self):
        item = self.list.currentItem()
        if not item:
            return
        name = item.data(0)
        src = item.data(1)
        path = self._skill_path(name, src)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.body.toPlainText())
        self.engine.skills.reload()
        # keep selection
        self._reload()
        for i in range(self.list.count()):
            if self.list.item(i).data(0) == name:
                self.list.setCurrentRow(i)
                break
