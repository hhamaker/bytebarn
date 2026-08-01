"""Skill manager dialog — edits live .md files under global/project .bytebarn/skills/."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from PySide6.QtWidgets import (
    QDialog,
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
        # global_dir is already ~/.bytebarn → skills at ~/.bytebarn/skills/
        # project skills live under <project>/.bytebarn/skills/
        if source == "project":
            return self.engine.project_dir / ".bytebarn" / "skills" / f"{name}.md"
        return self.engine.global_dir / "skills" / f"{name}.md"

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

    def _import_from_github(self):
        url, ok = QInputDialog.getText(
            self, "Import skill",
            "GitHub raw URL or gist URL:")
        if not ok or not url.strip():
            return
        url = url.strip()

        try:
            # normalize gist URLs to raw
            if "gist.github.com" in url and "/raw/" not in url:
                # https://gist.github.com/user/123456 -> raw
                m = re.search(r"gist\.github\.com/[^/]+/([0-9a-f]+)", url)
                if m:
                    gist_id = m.group(1)
                    url = f"https://gist.githubusercontent.com/raw/{gist_id}"
            async def fetch():
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(url, follow_redirects=True)
                    r.raise_for_status()
                    return r.text
            # run the coroutine from the Qt thread via the engine helper
            loop = __import__("asyncio").get_event_loop()
            body = loop.run_until_complete(fetch())
        except Exception as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return

        # derive a reasonable filename
        default_name = Path(url).stem or "imported-skill"
        name, ok = QInputDialog.getText(self, "Skill name", "Name:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        path = self._skill_path(name, "project")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        self.engine.skills.reload()
        self._reload()
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
