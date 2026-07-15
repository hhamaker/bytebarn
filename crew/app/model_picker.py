"""Reusable two-stage model picker: provider dropdown + live model dropdown.

The prompt bar, agent editor, and settings all need "pick a provider, then a
model from that provider's live list". This widget is that, in one place.

Emits ``model_changed(str)`` with the full "provider/model-id" (or "" when
incomplete / the (default) sentinel is selected).
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

DEFAULT_SENTINEL = "(default)"


class ModelPicker(QWidget):
    model_changed = Signal(str)

    def __init__(self, engine, allow_default: bool = False, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._allow_default = allow_default

        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumWidth(120)
        self.provider_combo.setToolTip("Provider — connect more via ⚡ providers")
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(240)
        self.model_combo.view().setMinimumWidth(380)
        self.model_combo.setToolTip("Model — list is fetched live from the provider")
        self.model_combo.currentTextChanged.connect(
            lambda _: self.model_changed.emit(self.value()))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.provider_combo)
        layout.addWidget(self.model_combo, 1)

    # -- public API ---------------------------------------------------------

    def set_model(self, model: str = "") -> None:
        """Populate providers, then select the provider/model of ``model``."""
        from ..engine.providers.known import connected_providers

        provider, _, model_id = model.partition("/")
        providers = connected_providers(self.engine.config, self.engine.providers.auth)

        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        if self._allow_default:
            self.provider_combo.addItem(DEFAULT_SENTINEL)
        self.provider_combo.addItems(providers)
        if provider and provider in providers:
            self.provider_combo.setCurrentText(provider)
        elif self._allow_default:
            self.provider_combo.setCurrentIndex(0)
            model_id = ""
        elif not providers:
            self.provider_combo.addItem("⚡ connect a provider")
            model_id = ""
        self.provider_combo.blockSignals(False)

        self._set_provider_models(self.provider_combo.currentText(), model_id)

    def value(self) -> str:
        """Full "provider/model-id", or "" when incomplete / default."""
        provider = self.provider_combo.currentText()
        model_id = self.model_combo.currentText().strip()
        if (not provider or provider == DEFAULT_SENTINEL
                or provider.startswith("⚡") or not model_id):
            return ""
        return f"{provider}/{model_id}"

    # -- internals ----------------------------------------------------------

    def _on_provider_changed(self, provider: str) -> None:
        self._set_provider_models(provider)

    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        from ..engine.providers.known import curated_models

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not provider or provider == DEFAULT_SENTINEL or provider.startswith("⚡"):
            self.model_combo.setEnabled(False)
            self.model_combo.blockSignals(False)
            self.model_changed.emit(self.value())
            return
        self.model_combo.setEnabled(True)
        models = curated_models(provider)
        if current_id and current_id not in models:
            models.insert(0, current_id)
        self.model_combo.addItems(models)
        if current_id:
            self.model_combo.setCurrentText(current_id)
        elif models:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self.model_changed.emit(self.value())

        # live list replaces the curated one when the fetch lands
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (offscreen widget tests)
            return
        loop.create_task(self._load_live_models(provider))

    async def _load_live_models(self, provider: str) -> None:
        live = await self.engine.list_models(provider)
        if not live or self.provider_combo.currentText() != provider:
            return
        keep = self.model_combo.currentText()
        merged = list(dict.fromkeys(([keep] if keep and keep not in live else []) + live))
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(merged)
        self.model_combo.setCurrentText(keep or (merged[0] if merged else ""))
        self.model_combo.blockSignals(False)
