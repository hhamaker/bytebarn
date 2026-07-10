"""Settings dialog (spec §7.4): providers, default models, permissions, theme."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..engine.config import patch_config_file
from ..engine.events import AgentRegistryChanged
from ..engine.facade import Engine


class SettingsDialog(QDialog):
    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        config = engine.config
        self.model = QLineEdit(config.model)
        self.small_model = QLineEdit(config.small_model)

        self.provider_rows: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        form = QFormLayout()
        form.addRow(QLabel("<b>Models</b>"))
        form.addRow("default model", self.model)
        form.addRow("small model", self.small_model)
        form.addRow(QLabel("<b>Providers</b> (base_url / api key env var)"))
        for name, provider in config.provider.items():
            base = QLineEdit(provider.base_url or "")
            key_env = QLineEdit(provider.api_key_env or "")
            self.provider_rows[name] = (base, key_env)
            form.addRow(f"{name} base_url", base)
            form.addRow(f"{name} api_key_env", key_env)

        form.addRow(QLabel("<b>Permission defaults</b>"))
        self.permission_rows: dict[str, QComboBox] = {}
        for tool in ("bash", "edit", "write", "webfetch"):
            combo = QComboBox()
            combo.addItems(["ask", "allow", "deny"])
            value = config.permission.get(tool)
            if isinstance(value, str):
                combo.setCurrentText(value)
            elif isinstance(value, dict):
                combo.setCurrentText(value.get("default", "ask"))
            self.permission_rows[tool] = combo
            form.addRow(tool, combo)

        form.addRow(QLabel("<b>Appearance</b>"))
        self.theme = QComboBox()
        self.theme.addItems(["follow system", "dark", "light"])
        form.addRow("theme", self.theme)

        save = QPushButton("Save to global config")
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(save)

    def _save(self) -> None:
        updates: dict = {}
        config = self.engine.config
        if self.model.text().strip() != config.model:
            updates["model"] = self.model.text().strip()
        if self.small_model.text().strip() != config.small_model:
            updates["small_model"] = self.small_model.text().strip()
        for name, (base, key_env) in self.provider_rows.items():
            provider = config.provider[name]
            if base.text().strip() != (provider.base_url or ""):
                updates[f"provider.{name}.base_url"] = base.text().strip()
            if key_env.text().strip() != (provider.api_key_env or ""):
                updates[f"provider.{name}.api_key_env"] = key_env.text().strip()
        for tool, combo in self.permission_rows.items():
            existing = config.permission.get(tool)
            existing_default = existing if isinstance(existing, str) else (
                (existing or {}).get("default", "ask")
            )
            if combo.currentText() != existing_default:
                if isinstance(existing, dict):
                    updates[f"permission.{tool}.default"] = combo.currentText()
                else:
                    updates[f"permission.{tool}"] = combo.currentText()
        if updates:
            patch_config_file(self.engine.global_dir / "config.json", updates)
            self.engine.reload_config()
            self.engine.bus.emit(AgentRegistryChanged())
        self.accept()
