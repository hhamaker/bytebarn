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
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
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
        form.addRow("model", self.model_combo)
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

    def _reload_models(self) -> None:
        from ..engine.providers.known import available_models

        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItem("")  # = default
        for model in available_models(self.engine.config, self.engine.providers.auth):
            self.model_combo.addItem(model)
        self.model_combo.setCurrentText(current)

    def _reload_list(self) -> None:
        self.agent_list.clear()
        for agent in self.engine.agents.agents.values():
            if agent.hidden and not self.show_hidden.isChecked():
                continue
            label = f"{agent.name}  ({agent.mode})"
            if agent.builtin:
                label += "  [native]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, agent.name)
            self.agent_list.addItem(item)

    def _on_select(self, item: QListWidgetItem, _prev=None) -> None:
        if item is None:
            return
        agent = self.engine.agents.get(item.data(Qt.UserRole))
        self._current = agent
        overridden = agent.name in self.engine.config.agent
        badge = "native" if agent.builtin else agent.source
        if overridden:
            badge += " + overrides"
        self.badge.setText(f"<b>{agent.name}</b> — {badge}")
        self.model_combo.setCurrentText(agent.model or "")
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

        diff("model", self.model_combo.currentText().strip(), agent.model or "")
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
