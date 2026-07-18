"""In-app agent editor (spec §7.3): edits persist as config overrides."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

        left = QVBoxLayout()
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
        self.mode = QComboBox()
        self.mode.addItems(["primary", "subagent", "all"])
        self.steps = QSpinBox()
        self.steps.setRange(1, 1000)
        self.color_button = QPushButton("pick color")
        self.color_button.clicked.connect(self._pick_color)
        self.description.textChanged.connect(self._update_preview)
        self.hidden = QCheckBox("hidden")
        save = QPushButton("Save overrides")
        save.clicked.connect(self._save)
        reset = QPushButton("Reset overrides")
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
        from ..engine.providers.known import connected_providers

        provider, _, model_id = model.partition("/")
        providers = connected_providers(self.engine.config, self.engine.providers.auth)
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItem(self._DEFAULT)
        self.provider_combo.addItems(providers)
        if provider in providers:
            self.provider_combo.setCurrentText(provider)
        else:
            self.provider_combo.setCurrentIndex(0)
            model_id = ""
        self.provider_combo.blockSignals(False)
        self._set_provider_models(self.provider_combo.currentText(), model_id)

    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        from ..engine.providers.known import curated_models

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if provider == self._DEFAULT or not provider:
            self.model_combo.setEnabled(False)
            self.model_combo.blockSignals(False)
            return
        self.model_combo.setEnabled(True)
        cached = self.engine.cached_models(provider)
        models = list(cached) if cached is not None else curated_models(provider)
        if current_id and current_id not in models:
            models.insert(0, current_id)
        self.model_combo.addItems(models)
        if current_id:
            self.model_combo.setCurrentText(current_id)
        elif models:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        # always re-fetch so the list matches what the provider serves right now
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no running loop (offscreen tests)
            return
        loop.create_task(self._load_live_models(provider))

    async def _load_live_models(self, provider: str) -> None:
        live = await self.engine.list_models(provider, force=True)
        if self.provider_combo.currentText() != provider:
            return
        if not live:
            return
        keep = self.model_combo.currentText()
        merged = list(dict.fromkeys(
            ([keep] if keep and keep not in live else []) + live))
        if keep and keep in merged:
            # selection didn't change; refresh silently
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(merged)
            self.model_combo.setCurrentText(keep)
            self.model_combo.blockSignals(False)
        else:
            # selection will change — unblock so currentTextChanged fires
            new = merged[0] if merged else ""
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(merged)
            self.model_combo.blockSignals(False)
            self.model_combo.setCurrentText(new)

    def _provider_changed(self, provider: str) -> None:
        self._set_provider_models(provider)

    def _selected_model(self) -> str:
        """Full "provider/model-id", or "" when (default)."""
        provider = self.provider_combo.currentText()
        model_id = self.model_combo.currentText().strip()
        if provider == self._DEFAULT or not provider or not model_id:
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
        self.mode.setCurrentText(agent.mode)
        self.steps.setValue(agent.steps)
        self._color = agent.color or ""
        self._style_color_button()
        self._update_preview()
        self.hidden.setChecked(agent.hidden)

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
        """Write only changed fields to agent.<name> in project config (spec §7.3)."""
        agent = self._current
        if agent is None:
            return
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
        diff("mode", self.mode.currentText(), agent.mode)
        if self.steps.value() != agent.steps:
            updates[f"agent.{agent.name}.steps"] = self.steps.value()
        diff("color", self._color, agent.color or "")
        if self.hidden.isChecked() != agent.hidden:
            updates[f"agent.{agent.name}.hidden"] = self.hidden.isChecked()

        if not updates:
            return
        path = self.engine.project_dir / ".crew" / "config.json"
        patch_config_file(path, updates)
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        self._reload_list()

    def _reset(self) -> None:
        agent = self._current
        if agent is None or agent.name not in self.engine.config.agent:
            return
        path = self.engine.project_dir / ".crew" / "config.json"
        patch_config_file(path, {f"agent.{agent.name}": DELETE})
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        self._reload_list()
