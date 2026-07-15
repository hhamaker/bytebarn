from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
    QInputDialog,
    QMenu,
)


class ProjectManagerDialog(QDialog):
    """Compact catalog-style project switcher."""

    project_switched = Signal(Path)  # emitted after a successful switch

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Projects")
        self.resize(520, 320)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)

        add_btn = QPushButton("+ Add folder as project…")
        add_btn.clicked.connect(self._add_existing)
        new_btn = QPushButton("Create new…")
        new_btn.clicked.connect(self._create_new)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(new_btn)
        btn_row.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addLayout(btn_row)

        self._populate()

    # ------------------------------------------------------------------
    def _populate(self):
        self.list.clear()
        projects = self.engine.store.list_projects()  # sync helper already exists
        for p in projects:
            item = QListWidgetItem(f"{p.name}  —  {p.path}")
            item.setData(Qt.UserRole, p)
            item.setToolTip(str(p.path))
            self.list.addItem(item)

    # ------------------------------------------------------------------
    def _on_item_clicked(self, item: QListWidgetItem):
        proj = item.data(Qt.UserRole)
        if proj.path != self.engine.project_dir:
            self._switch_to(proj.path)
        self.accept()

    def _switch_to(self, path: Path):
        self.engine.load_project(path)  # UI-only wrapper around existing store calls
        self.project_switched.emit(path)

    # ------------------------------------------------------------------
    def _show_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        proj = item.data(Qt.UserRole)
        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        remove_act = menu.addAction("Remove from catalog")
        action = menu.exec(self.list.mapToGlobal(pos))
        if action == rename_act:
            self._rename(proj, item)
        elif action == remove_act:
            self._remove(proj, item)

    def _rename(self, proj, item):
        new_name, ok = QInputDialog.getText(self, "Rename project", "New name:", text=proj.name)
        if ok and new_name.strip():
            self.engine.store.rename_project(proj.id, new_name.strip())
            self._populate()

    def _remove(self, proj, item):
        self.engine.store.remove_project(proj.id)  # catalog only
        self.list.takeItem(self.list.row(item))

    # ------------------------------------------------------------------
    def _add_existing(self):
        path = QFileDialog.getExistingDirectory(self, "Select project folder")
        if path:
            p = Path(path)
            self.engine.store.add_project(p)
            self._populate()

    def _create_new(self):
        name, ok = QInputDialog.getText(self, "Project name", "Name:")
        if not (ok and name.strip()):
            return
        sentinel = f"catalog:{name.strip()}"
        self.engine.store.add_project(Path(sentinel), name=name.strip())
        self._populate()