"""Reusable two-stage model picker: provider dropdown + live model dropdown.

The prompt bar, agent editor, and settings all need "pick a provider, then a
model from that provider's live list". This widget is that, in one place.

Emits ``model_changed(str)`` with the full "provider/model-id" (or "" when
incomplete / the (default) sentinel is selected).
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QWidget

DEFAULT_SENTINEL = "(default)"


class ModelPicker(QWidget):
    model_changed = Signal(str)

    def __init__(self, engine, allow_default: bool = False, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._allow_default = allow_default

        self.provider_combo = QComboBox()
        # Low floors so the project sidebar can shrink; popup stays readable.
        self.provider_combo.setMinimumWidth(72)
        self.provider_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.provider_combo.setMinimumContentsLength(6)
        self.provider_combo.setToolTip("Provider — connect more via ⚡ providers")
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(96)
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(8)
        # Popup list only — must not raise this widget's minimumSizeHint.
        self.model_combo.view().setMinimumWidth(280)
        self.model_combo.setToolTip("Model — list is fetched live from the provider")
        self.model_combo.currentTextChanged.connect(
            lambda _: self.model_changed.emit(self.value()))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.provider_combo)
        layout.addWidget(self.model_combo, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def minimumSizeHint(self) -> QSize:
        # QComboBox popup view mins must not pin the sidebar splitter.
        return QSize(140, super().minimumSizeHint().height())

    # -- public API ---------------------------------------------------------

    def set_model(self, model: str = "") -> None:
        """Populate providers, then select the provider/model of ``model``."""
        from ..engine.providers.known import (
            CLAUDE_CODE_PROVIDER,
            KNOWN_PROVIDERS,
            connected_providers,
            is_runtime_provider,
        )

        provider, _, model_id = model.partition("/")
        providers = list(
            connected_providers(self.engine.config, self.engine.providers.auth))
        # Claude Code is always offered (local CLI runtime).
        if CLAUDE_CODE_PROVIDER not in providers:
            providers.insert(0, CLAUDE_CODE_PROVIDER)
        elif providers[0] != CLAUDE_CODE_PROVIDER:
            providers = [CLAUDE_CODE_PROVIDER] + [
                p for p in providers if p != CLAUDE_CODE_PROVIDER
            ]

        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        if self._allow_default:
            self.provider_combo.addItem(DEFAULT_SENTINEL, "")
        for pid in providers:
            # Friendly label for runtimes; bare id for API providers (matches
            # the prompt bar and existing settings tests).
            if is_runtime_provider(pid):
                label = KNOWN_PROVIDERS[pid].label
            else:
                label = pid
            self.provider_combo.addItem(label, pid)
        if provider and provider in providers:
            idx = self.provider_combo.findData(provider)
            if idx < 0:
                idx = self.provider_combo.findText(provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            else:
                model_id = ""
        elif self._allow_default:
            self.provider_combo.setCurrentIndex(0)
            model_id = ""
        elif not providers:
            self.provider_combo.addItem("⚡ connect a provider", "")
            model_id = ""
        self.provider_combo.blockSignals(False)

        self._set_provider_models(self._current_provider_id(), model_id)

    def _current_provider_id(self) -> str:
        idx = self.provider_combo.currentIndex()
        data = self.provider_combo.itemData(idx)
        if data:
            return str(data)
        text = self.provider_combo.currentText()
        if text == DEFAULT_SENTINEL or text.startswith("⚡"):
            return ""
        return text

    def value(self) -> str:
        """Full "provider/model-id", or "" when incomplete / default."""
        provider = self._current_provider_id()
        model_id = self.model_combo.currentText().strip()
        if not provider or not model_id:
            return ""
        return f"{provider}/{model_id}"

    # -- internals ----------------------------------------------------------

    def _on_provider_changed(self, _provider: str) -> None:
        self._set_provider_models(self._current_provider_id())

    def _set_provider_models(self, provider: str, current_id: str = "") -> None:
        from ..engine.providers.known import curated_models, is_runtime_provider

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not provider or provider == DEFAULT_SENTINEL or provider.startswith("⚡"):
            self.model_combo.setEnabled(False)
            self.model_combo.blockSignals(False)
            self.model_changed.emit(self.value())
            return
        self.model_combo.setEnabled(True)
        if is_runtime_provider(provider):
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
            self.model_changed.emit(self.value())
            return
        # prefer last live fetch; curated is only a placeholder until refresh lands
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
        self.model_changed.emit(self.value())

        # always re-fetch so the list matches what the provider serves right now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (offscreen widget tests)
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
        prev = self.value()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(merged)
        if keep:
            self.model_combo.setCurrentText(keep)
        else:
            self.model_combo.setCurrentText("")
        self.model_combo.blockSignals(False)
        # only notify if user-visible value actually changed
        if self.value() != prev:
            self.model_changed.emit(self.value())
