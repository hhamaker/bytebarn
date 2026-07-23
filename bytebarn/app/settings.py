"""Settings dialog: default models, permission defaults, appearance.

Provider connections (keys, base URLs, web logins) live in the ⚡ providers
manager, not here — this dialog is only app-wide preferences.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..engine.config import patch_config_file
from ..engine.events import AgentRegistryChanged
from ..engine.facade import Engine
from .model_picker import ModelPicker

_PERMISSION_TOOLS = ("bash", "edit", "write", "webfetch")
_THEMES = ("follow system", "dark", "light", "modern")


def _current_crew_style(config) -> str:
    """Configured crew style; the legacy 'waifu' bool maps to 'waifu'."""
    extra = config.model_extra or {}
    style = extra.get("crew_style")
    if style in ("farm", "critters", "waifu", "dogs", "cats"):
        return style
    return "waifu" if extra.get("waifu") else "farm"


class SettingsDialog(QDialog):
    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        config = engine.config

        form = QFormLayout()

        form.addRow(QLabel("<b>Default models</b>"))
        self.model = ModelPicker(engine)
        self.model.set_model(config.model)
        self.small_model = ModelPicker(engine)
        self.small_model.set_model(config.small_model)
        form.addRow("default", self.model)
        form.addRow("small", self.small_model)
        hint = QLabel("Connect providers in <b>⚡ providers</b>; pick per-session "
                      "models in the prompt bar.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8f96a3;")
        form.addRow(hint)

        form.addRow(QLabel("<b>Permission defaults</b>"))
        self.permission_rows: dict[str, QComboBox] = {}
        for tool in _PERMISSION_TOOLS:
            combo = QComboBox()
            combo.addItems(["ask", "allow", "deny"])
            combo.setCurrentText(self._permission_default(config, tool))
            self.permission_rows[tool] = combo
            form.addRow(tool, combo)

        form.addRow(QLabel("<b>Appearance</b>"))
        self.theme = QComboBox()
        self.theme.addItems(_THEMES)
        self.theme.setCurrentText(
            (config.model_extra or {}).get("theme", "follow system"))
        form.addRow("theme", self.theme)

        self.crew_style = QComboBox()
        self.crew_style.addItems(["farm", "critters", "waifu", "dogs", "cats"])
        self.crew_style.setCurrentText(_current_crew_style(config))
        self.crew_style.setToolTip(
            "How the barn crew renders everywhere: farm animals (default), "
            "woodland critters, anime characters, all dogs, or all cats")
        form.addRow("crew style", self.crew_style)

        save = QPushButton("Save")
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(save)

    @staticmethod
    def _permission_default(config, tool: str) -> str:
        value = config.permission.get(tool)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get("default", "ask")
        return "ask"

    def _save(self) -> None:
        updates: dict = {}
        config = self.engine.config

        model = self.model.value()
        if model and model != config.model:
            updates["model"] = model
        small = self.small_model.value()
        if small and small != config.small_model:
            updates["small_model"] = small

        for tool, combo in self.permission_rows.items():
            if combo.currentText() != self._permission_default(config, tool):
                existing = config.permission.get(tool)
                if isinstance(existing, dict):
                    updates[f"permission.{tool}.default"] = combo.currentText()
                else:
                    updates[f"permission.{tool}"] = combo.currentText()

        theme = self.theme.currentText()
        if theme != (config.model_extra or {}).get("theme", "follow system"):
            updates["theme"] = theme

        style = self.crew_style.currentText()
        if style != _current_crew_style(config):
            updates["crew_style"] = style
            from . import sprites

            sprites.set_crew_style(style)

        if updates:
            patch_config_file(self.engine.global_dir / "config.json", updates)
            self.engine.reload_config()
            self.engine.bus.emit(AgentRegistryChanged())
            if "theme" in updates:
                from PySide6.QtWidgets import QApplication

                from .theme import apply_theme

                apply_theme(QApplication.instance(), theme)
        self.accept()
