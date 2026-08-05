"""In-app agent editor (spec §7.3).

Built-ins are edited as config overrides. Custom agents are created as
``.bytebarn/agent/<name>.md`` files (project-scoped by default).
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..engine.agents import AgentDef
from ..engine.config import DELETE, patch_config_file
from ..engine.events import AgentRegistryChanged
from ..engine.facade import Engine
from .sprites import critter_pixmap

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _starter_agent_md(name: str, mode: str) -> str:
    """Default markdown body for a newly created custom agent."""
    role = "subagent on the crew" if mode == "subagent" else "primary chat agent"
    # Keep description a non-null string — empty YAML values become None and
    # fail AgentDef validation.
    return (
        f"---\n"
        f'description: "{name}"\n'
        f"mode: {mode}\n"
        f'color: "#98c379"\n'
        f"---\n"
        f"You are **{name}**, a {role}.\n"
        f"\n"
        f"Be concrete, use tools when they help, and report what you did.\n"
    )

class AgentEditor(QDialog):
    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Agents")
        self.resize(860, 620)
        self._current: AgentDef | None = None
        self._color: str = ""

        self.agent_list = QListWidget()
        self.agent_list.currentItemChanged.connect(self._on_select)
        self.show_hidden = QCheckBox("show hidden")
        self.show_hidden.stateChanged.connect(self._reload_list)

        btn_new_primary = QPushButton("+ Primary")
        btn_new_primary.setToolTip(
            "New primary agent — appears in the prompt-bar agent picker")
        btn_new_primary.clicked.connect(lambda: self._new_agent("primary"))
        btn_new_sub = QPushButton("+ Subagent")
        btn_new_sub.setToolTip(
            "New subagent — orchestrator can delegate to it on /goal runs")
        btn_new_sub.clicked.connect(lambda: self._new_agent("subagent"))
        btn_delete = QPushButton("Delete")
        btn_delete.setToolTip("Delete a custom agent file (built-ins cannot be removed)")
        btn_delete.clicked.connect(self._delete_agent)
        self._delete_btn = btn_delete

        new_row = QHBoxLayout()
        new_row.setContentsMargins(0, 0, 0, 0)
        new_row.addWidget(btn_new_primary)
        new_row.addWidget(btn_new_sub)
        new_row.addWidget(btn_delete)

        left = QVBoxLayout()
        left.addLayout(new_row)
        left.addWidget(self.agent_list)
        left.addWidget(self.show_hidden)

        # form
        self.badge = QLabel("")
        self.preview = QLabel("")
        self.preview.setFixedHeight(64)
        # two-stage model picker: "(default)" or provider -> live model list
        self.provider_combo = QComboBox()
        self.provider_combo.currentTextChanged.connect(self._provider_changed)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip("Model list is fetched live from the provider")
        # long model ids: keep the field wide and the popup wide enough to read
        from PySide6.QtWidgets import QSizePolicy

        self.model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.model_combo.setMinimumWidth(260)
        self.model_combo.view().setMinimumWidth(380)
        self.provider_combo.setMinimumWidth(120)
        self._reload_models()
        self.description = QLineEdit()
        self.prompt = QPlainTextEdit()
        self.prompt.setMinimumHeight(200)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setSpecialValueText("(default)")
        self.top_p = QDoubleSpinBox()
        self.top_p.setRange(0.0, 1.0)
        self.top_p.setSingleStep(0.05)
        self.top_p.setSpecialValueText("(default)")
        self.thinking = QComboBox()
        self.thinking.addItems(["(default)", "off", "low", "medium", "high"])
        self.thinking.setToolTip(
            "Extended thinking budget — the model reasons before answering"
            " (supported models only)")
        self.mode = QComboBox()
        self.mode.addItems(["primary", "subagent", "all"])
        self.steps = QSpinBox()
        self.steps.setRange(1, 1000)
        self.color_button = QPushButton("pick color")
        self.color_button.clicked.connect(self._pick_color)
        self.description.textChanged.connect(self._update_preview)
        self.hidden = QCheckBox("hidden")
        save = QPushButton("Save")
        save.setToolTip(
            "Save changes — config overrides for built-ins, "
            ".bytebarn/agent/*.md for custom agents")
        save.clicked.connect(self._save)
        reset = QPushButton("Reset overrides")
        reset.setToolTip("Drop project config overrides for this agent (built-ins / overlays)")
        reset.clicked.connect(self._reset)

        form = QFormLayout()
        header = QHBoxLayout()
        header.addWidget(self.preview)
        header.addWidget(self.badge, 1)
        form.addRow(header)
        model_row = QHBoxLayout()
        model_row.addWidget(self.provider_combo)
        model_row.addWidget(self.model_combo, 1)
        form.addRow("model", model_row)
        form.addRow("description", self.description)
        form.addRow("prompt", self.prompt)
        form.addRow("temperature", self.temperature)
        form.addRow("top_p", self.top_p)
        form.addRow("thinking", self.thinking)
        form.addRow("mode", self.mode)
        form.addRow("steps", self.steps)
        form.addRow("color", self.color_button)
        form.addRow("", self.hidden)
        buttons = QHBoxLayout()
        buttons.addWidget(save)
        buttons.addWidget(reset)
        form.addRow(buttons)

        right = QWidget()
        right.setLayout(form)
        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addWidget(right, 2)

        self._reload_list()

    # ------------------------------------------------------------------

    _DEFAULT = "(default)"

    def _reload_models(self, model: str = "") -> None:
        """Rebuild provider list; select the provider/model of ``model``."""
        from ..engine.providers.known import (
            CLAUDE_CODE_PROVIDER,
            KNOWN_PROVIDERS,
            connected_providers,
            is_runtime_provider,
        )

        provider, _, model_id = model.partition("/")
        providers = list(
            connected_providers(self.engine.config, self.engine.providers.auth))
        # Ensure Claude Code is always offered as an agent default.
        if CLAUDE_CODE_PROVIDER not in providers:
            providers = [CLAUDE_CODE_PROVIDER] + providers
        elif providers[0] != CLAUDE_CODE_PROVIDER:
            providers = [CLAUDE_CODE_PROVIDER] + [
                p for p in providers if p != CLAUDE_CODE_PROVIDER
            ]

        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItem(self._DEFAULT, "")
        for pid in providers:
            if is_runtime_provider(pid):
                label = KNOWN_PROVIDERS[pid].label
            else:
                label = pid
            self.provider_combo.addItem(label, pid)
        if provider and provider in providers:
            idx = self.provider_combo.findData(provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            else:
                self.provider_combo.setCurrentIndex(0)
                model_id = ""
        else:
            self.provider_combo.setCurrentIndex(0)
            model_id = ""
        self.provider_combo.blockSignals(False)
        self._set_provider_models(self._current_provider_id(), model_id)

    def _current_provider_id(self) -> str:
        idx = self.provider_combo.currentIndex()
        data = self.provider_combo.itemData(idx)
        if data:
            return str(data)
        text = self.provider_combo.currentText()
        return "" if text == self._DEFAULT else text

    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        from ..engine.providers.known import (
            CLAUDE_CODE_PROVIDER,
            curated_models,
            is_runtime_provider,
        )

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if provider == self._DEFAULT or not provider:
            self.model_combo.setEnabled(False)
            self.model_combo.blockSignals(False)
            return
        self.model_combo.setEnabled(True)
        if is_runtime_provider(provider) or provider == CLAUDE_CODE_PROVIDER:
            models = curated_models(provider)
            if current_id and current_id not in models:
                models.insert(0, current_id)
            self.model_combo.addItems(models)
            select = current_id if current_id in models else (
                "default" if "default" in models else (models[0] if models else "")
            )
            if select:
                self.model_combo.setCurrentText(select)
            self.model_combo.blockSignals(False)
            return
        cached = self.engine.cached_models(provider)
        models = list(cached) if cached is not None else curated_models(provider)
        if current_id and current_id not in models:
            models.insert(0, current_id)
        self.model_combo.addItems(models)
        if current_id:
            self.model_combo.setCurrentText(current_id)
        else:
            self.model_combo.setCurrentText("")
        self.model_combo.blockSignals(False)
        # always re-fetch so the list matches what the provider serves right now
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no running loop (offscreen tests)
            return
        loop.create_task(self._load_live_models(provider))

    async def _load_live_models(self, provider: str) -> None:
        from ..engine.providers.known import is_runtime_provider

        if is_runtime_provider(provider):
            return
        live = await self.engine.list_models(provider, force=True)
        if self._current_provider_id() != provider:
            return
        if not live:
            return
        keep = self.model_combo.currentText().strip()
        merged = list(dict.fromkeys(([keep] if keep and keep not in live else []) + live))
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(merged)
        if keep:
            self.model_combo.setCurrentText(keep)
        else:
            self.model_combo.setCurrentText("")
        self.model_combo.blockSignals(False)
        # no emit needed; settings dialog reads _selected_model() on OK

    def _provider_changed(self, _provider: str) -> None:
        self._set_provider_models(self._current_provider_id())

    def _selected_model(self) -> str:
        """Full "provider/model-id", or "" when (default)."""
        provider = self._current_provider_id()
        model_id = self.model_combo.currentText().strip()
        if not provider or not model_id:
            return ""
        return f"{provider}/{model_id}"

    def _reload_list(self) -> None:
        from PySide6.QtGui import QColor, QFont, QIcon

        self.agent_list.clear()

        def header(text: str) -> None:
            item = QListWidgetItem(text)
            item.setFlags(Qt.NoItemFlags)  # not selectable, not clickable
            font = QFont()
            font.setBold(True)
            font.setPointSize(max(9, font.pointSize() - 1))
            item.setFont(font)
            item.setForeground(QColor("#8f96a3"))
            self.agent_list.addItem(item)

        def add_agent(agent) -> None:
            label = agent.name
            if agent.builtin:
                label += "  [native]"
            if agent.name in self.engine.config.agent:
                label += "  •"  # has overrides
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, agent.name)
            item.setIcon(QIcon(critter_pixmap(agent.name, agent.color or "#98c379", scale=2)))
            item.setToolTip(agent.description)
            self.agent_list.addItem(item)

        visible = [a for a in self.engine.agents.agents.values()
                   if not a.hidden or self.show_hidden.isChecked()]
        primaries = sorted((a for a in visible if a.mode in ("primary", "all")),
                           key=lambda a: a.name)
        subagents = sorted((a for a in visible if a.mode == "subagent"),
                           key=lambda a: a.name)
        if primaries:
            header("PRIMARY — pickable in the prompt bar")
            for agent in primaries:
                add_agent(agent)
        if subagents:
            header("SUBAGENTS — delegated to by goal runs")
            for agent in subagents:
                add_agent(agent)

    def _on_select(self, item: QListWidgetItem, _prev=None) -> None:
        if item is None or item.data(Qt.UserRole) is None:  # section header
            return
        agent = self.engine.agents.get(item.data(Qt.UserRole))
        self._current = agent
        overridden = agent.name in self.engine.config.agent
        badge = "native" if agent.builtin else agent.source
        if overridden:
            badge += " + overrides"
        self.badge.setText(f"<b>{agent.name}</b> — {badge}")
        self._reload_models(agent.model or "")
        self.description.setText(agent.description)
        self.prompt.setPlainText(agent.prompt)
        self.temperature.setValue(agent.temperature if agent.temperature is not None else 0.0)
        self.top_p.setValue(agent.top_p if agent.top_p is not None else 0.0)
        self.thinking.setCurrentText(agent.thinking or "(default)")
        self.mode.setCurrentText(agent.mode)
        self.steps.setValue(agent.steps)
        self._color = agent.color or ""
        self._style_color_button()
        self._update_preview()
        self.hidden.setChecked(agent.hidden)
        self._delete_btn.setEnabled(self._can_delete(agent))

    def _can_delete(self, agent: AgentDef | None) -> bool:
        """Only custom file-backed agents (not built-ins) can be deleted."""
        if agent is None or agent.builtin:
            return False
        return self._agent_md_path(agent.name) is not None

    def _agent_dir(self, scope: str = "project") -> Path:
        if scope == "global":
            return Path(self.engine.global_dir) / "agent"
        return Path(self.engine.project_dir) / ".bytebarn" / "agent"

    def _agent_md_path(self, name: str) -> Path | None:
        """Return the on-disk .md for a custom agent, preferring project over global."""
        for scope in ("project", "global"):
            path = self._agent_dir(scope) / f"{name}.md"
            if path.is_file():
                return path
        return None

    def _select_agent(self, name: str) -> None:
        for i in range(self.agent_list.count()):
            item = self.agent_list.item(i)
            if item is not None and item.data(Qt.UserRole) == name:
                self.agent_list.setCurrentRow(i)
                return

    def _new_agent(self, mode: str = "subagent") -> None:
        """Create a project-scoped agent .md and select it for editing."""
        title = "New primary agent" if mode == "primary" else "New subagent"
        hint = (
            "Name (letters, numbers, _ -):"
            if mode == "primary"
            else "Name — orchestrator will see this in the crew roster:"
        )
        name, ok = QInputDialog.getText(self, title, hint)
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if not _NAME_RE.match(name):
            QMessageBox.warning(
                self, "Invalid name",
                "Use a short id starting with a letter "
                "(letters, numbers, underscore, hyphen; max 64 chars).")
            return
        if name in self.engine.agents.agents and self.engine.agents.get(name).builtin:
            QMessageBox.warning(
                self, "Name taken",
                f"“{name}” is a built-in agent. Choose another name.")
            return
        path = self._agent_dir("project") / f"{name}.md"
        if path.exists():
            confirm = QMessageBox.question(
                self, "Replace agent?",
                f"“{name}” already exists in this project. Overwrite its file?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm != QMessageBox.Yes:
                return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_starter_agent_md(name, mode), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Could not create agent", str(exc))
            return
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        self._reload_list()
        self._select_agent(name)

    def _delete_agent(self) -> None:
        agent = self._current
        if not self._can_delete(agent):
            QMessageBox.information(
                self, "Cannot delete",
                "Built-in agents cannot be deleted. Hide them instead, or "
                "reset overrides to restore defaults.")
            return
        assert agent is not None
        path = self._agent_md_path(agent.name)
        if path is None:
            return
        confirm = QMessageBox.question(
            self, "Delete agent?",
            f"Delete custom agent “{agent.name}”?\n\n{path}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Could not delete", str(exc))
            return
        # Drop any leftover config overrides for this name
        if agent.name in self.engine.config.agent:
            cfg = self.engine.project_dir / ".bytebarn" / "config.json"
            try:
                patch_config_file(cfg, {f"agent.{agent.name}": DELETE})
            except Exception:
                pass
        self._current = None
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        self._reload_list()
        self.badge.setText("")
        self.preview.clear()
        self._delete_btn.setEnabled(False)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            self._color = color.name()
            self._style_color_button()
            self._update_preview()

    def _style_color_button(self) -> None:
        if self._color:
            self.color_button.setText(self._color)
            self.color_button.setStyleSheet(f"background-color: {self._color};")
        else:
            self.color_button.setText("pick color")
            self.color_button.setStyleSheet("")

    def _update_preview(self) -> None:
        if self._current:
            self.preview.setPixmap(critter_pixmap(self._current.name, self._color or "#98c379"))

    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist edits: rewrite .md for custom agents; config for built-ins."""
        agent = self._current
        if agent is None:
            return
        md_path = None if agent.builtin else self._agent_md_path(agent.name)
        if md_path is not None:
            self._save_agent_file(agent, md_path)
        else:
            self._save_config_overrides(agent)
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        name = agent.name
        self._reload_list()
        self._select_agent(name)

    def _save_agent_file(self, agent: AgentDef, path: Path) -> None:
        """Rewrite a custom agent's .md from the form fields."""
        import yaml

        front: dict = {}
        desc = self.description.text().strip()
        if desc:
            front["description"] = desc
        mode = self.mode.currentText() or agent.mode or "subagent"
        front["mode"] = mode
        model = self._selected_model()
        if model:
            front["model"] = model
        if self.temperature.value() > 0:
            front["temperature"] = round(self.temperature.value(), 2)
        if self.top_p.value() > 0:
            front["top_p"] = round(self.top_p.value(), 2)
        thinking = self.thinking.currentText()
        if thinking and thinking != "(default)":
            front["thinking"] = thinking
        # Always persist steps so reload matches the form.
        front["steps"] = int(self.steps.value())
        if self._color:
            front["color"] = self._color
        if self.hidden.isChecked():
            front["hidden"] = True
        # Preserve tools/permission from the existing file if present
        if path.is_file():
            try:
                from ..engine.agents import parse_agent_file

                old_front, _ = parse_agent_file(path)
                for key in ("tools", "permission"):
                    if key in old_front and key not in front:
                        front[key] = old_front[key]
            except Exception:
                pass
        body = self.prompt.toPlainText().strip()
        dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
        text = f"---\n{dumped}\n---\n{body}\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Could not save agent", str(exc))
    def _save_config_overrides(self, agent: AgentDef) -> None:
        """Write only changed fields to agent.<name> in project config (spec §7.3)."""
        updates: dict = {}

        def diff(key: str, new, old) -> None:
            if new != old and new not in ("", None):
                updates[f"agent.{agent.name}.{key}"] = new

        diff("model", self._selected_model(), agent.model or "")
        diff("description", self.description.text().strip(), agent.description)
        diff("prompt", self.prompt.toPlainText().strip(), agent.prompt)
        if self.temperature.value() > 0 and self.temperature.value() != (agent.temperature or 0):
            updates[f"agent.{agent.name}.temperature"] = round(self.temperature.value(), 2)
        if self.top_p.value() > 0 and self.top_p.value() != (agent.top_p or 0):
            updates[f"agent.{agent.name}.top_p"] = round(self.top_p.value(), 2)
        thinking = self.thinking.currentText()
        if thinking != "(default)":
            diff("thinking", thinking, agent.thinking or "")
        diff("mode", self.mode.currentText(), agent.mode)
        if self.steps.value() != agent.steps:
            updates[f"agent.{agent.name}.steps"] = self.steps.value()
        diff("color", self._color, agent.color or "")
        if self.hidden.isChecked() != agent.hidden:
            updates[f"agent.{agent.name}.hidden"] = self.hidden.isChecked()

        if not updates:
            return
        path = self.engine.project_dir / ".bytebarn" / "config.json"
        patch_config_file(path, updates)

    def _reset(self) -> None:
        agent = self._current
        if agent is None or agent.name not in self.engine.config.agent:
            return
        path = self.engine.project_dir / ".bytebarn" / "config.json"
        patch_config_file(path, {f"agent.{agent.name}": DELETE})
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        name = agent.name
        self._reload_list()
        self._select_agent(name)