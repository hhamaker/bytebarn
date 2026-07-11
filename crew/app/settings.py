"""Settings dialog (spec §7.4): providers, default models, permissions, theme."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
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

        form.addRow(QLabel("<b>Sign in with Grok (xAI)</b>"))
        self.grok_status = QLabel()
        self.grok_login_btn = QPushButton()
        self.grok_login_btn.clicked.connect(self._toggle_grok_login)
        self._refresh_grok_status()
        grok_row = QHBoxLayout()
        grok_row.addWidget(self.grok_status, 1)
        grok_row.addWidget(self.grok_login_btn)
        form.addRow("account", grok_row)

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
        current_theme = (config.model_extra or {}).get("theme", "follow system")
        self.theme.setCurrentText(current_theme)
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
        existing_theme = (config.model_extra or {}).get("theme", "follow system")
        if self.theme.currentText() != existing_theme:
            updates["theme"] = self.theme.currentText()
        if updates:
            patch_config_file(self.engine.global_dir / "config.json", updates)
            self.engine.reload_config()
            self.engine.bus.emit(AgentRegistryChanged())
            if "theme" in updates:
                from PySide6.QtWidgets import QApplication

                from .theme import apply_theme

                apply_theme(QApplication.instance(), updates["theme"])
        self.accept()

    # ------------------------------------------------------------------ grok

    def _refresh_grok_status(self) -> None:
        record = self.engine.providers.auth.get("xai")
        if record and record.get("type") == "oauth":
            self.grok_status.setText("Signed in with xAI (Grok)")
            self.grok_login_btn.setText("Log out")
        else:
            self.grok_status.setText("Not signed in")
            self.grok_login_btn.setText("Log in with Grok")

    def _toggle_grok_login(self) -> None:
        record = self.engine.providers.auth.get("xai")
        if record and record.get("type") == "oauth":
            self.engine.providers.auth.remove("xai")
            self.engine.reload_config()
            self._refresh_grok_status()
            return
        self.grok_login_btn.setEnabled(False)
        self.grok_status.setText("Opening browser…")
        asyncio.ensure_future(self._do_grok_login())

    async def _do_grok_login(self) -> None:
        from ..engine.providers import xai_oauth

        try:
            record = await xai_oauth.login(
                lambda url: QDesktopServices.openUrl(QUrl(url))
            )
            self.engine.providers.auth.set("xai", record)
            self.engine.reload_config()
            self._refresh_grok_status()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self._refresh_grok_status()
            QMessageBox.warning(self, "Grok sign-in failed", f"{exc}\nCheck API key / network. Retry?")
        finally:
            self.grok_login_btn.setEnabled(True)
