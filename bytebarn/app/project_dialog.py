"""Claude-style project settings: name, custom instructions, knowledge assets.

Instructions and assets are injected into every session that belongs to the
project (runner.build_system_prompt)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ProjectDialog(QDialog):
    def __init__(self, engine, project_id: str, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.project_id = project_id
        self.setWindowTitle("Project")
        self.setMinimumSize(520, 480)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Name</b>"))
        self.name_edit = QLineEdit()
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("<b>Instructions</b> — included in every session"
                                " in this project"))
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "e.g. Always answer in Spanish. Prefer functional style. …")
        layout.addWidget(self.instructions, 1)

        layout.addWidget(QLabel("<b>Knowledge</b> — files every session in this"
                                " project can use"))
        self.assets = QListWidget()
        layout.addWidget(self.assets, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("+ Add files…")
        add_btn.clicked.connect(self._add_assets)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove_asset)
        row.addWidget(add_btn)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_task = asyncio.ensure_future(self._load())
        self._save_task: asyncio.Task | None = None

    # -- data ----------------------------------------------------------------

    async def _load(self) -> None:
        project = await self.engine.store.get_project(self.project_id)
        if project:
            self.name_edit.setText(project.name)
            self.instructions.setPlainText(project.instructions)
        await self._reload_assets()

    async def _reload_assets(self) -> None:
        self.assets.clear()
        for asset in await self.engine.store.list_project_assets(self.project_id):
            item = QListWidgetItem(f"📄 {asset.name}")
            item.setData(0x0100, asset.path)  # Qt.UserRole
            item.setToolTip(asset.path)
            self.assets.addItem(item)

    def _add_assets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add knowledge files", str(Path.home()))
        if not paths:
            return

        async def run() -> None:
            for p in paths:
                await self.engine.add_project_asset(self.project_id, p)
            await self._reload_assets()

        asyncio.ensure_future(run())

    def _remove_asset(self) -> None:
        item = self.assets.currentItem()
        if item is None:
            return
        path = item.data(0x0100)

        async def run() -> None:
            await self.engine.remove_project_asset(self.project_id, path)
            await self._reload_assets()

        asyncio.ensure_future(run())

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        text = self.instructions.toPlainText()

        async def run() -> None:
            if name:
                await self.engine.store.rename_project(self.project_id, name)
            await self.engine.store.set_project_instructions(self.project_id, text)
            self.accept()

        self._save_task = asyncio.ensure_future(run())
