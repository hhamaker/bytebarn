"""Provider connections manager: connect Crew to LLM services.

One place to see every service Crew knows how to talk to, connect it
(paste an API key, log in with OAuth, or just point at a local server),
and verify the connection actually works.

Keys typed here go to ~/.crew/auth.json (0600), never into project config.
"""

from __future__ import annotations

import asyncio
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QFont, QGuiApplication

from ..engine.config import patch_config_file
from ..engine.events import AgentRegistryChanged
from ..engine.facade import Engine
from ..engine.providers.known import KNOWN_PROVIDERS, ProviderSpec, connection_status
from ..engine.providers.probe import probe_provider

_STATUS_BADGE = {
    "connected-env": ("🟢", "connected (env var)"),
    "connected-key": ("🟢", "connected (saved key)"),
    "connected-oauth": ("🟢", "connected (OAuth)"),
    "local": ("💻", "local server — no key needed"),
    "planned": ("🔒", "coming soon"),
    "disconnected": ("⚪", "not connected"),
}


class DeviceCodeDialog(QDialog):
    """Web login, device-code style: show a short code, user types it on the
    provider's website, we wait for approval."""

    def __init__(self, label: str, user_code: str, verification_uri: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log in to {label}")
        self.setMinimumWidth(380)

        code = QLabel(user_code)
        font = QFont("monospace")
        font.setPointSize(22)
        font.setBold(True)
        code.setFont(font)
        code.setAlignment(Qt.AlignCenter)
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)

        hint = QLabel(
            f"1. Your browser opens <a href='{verification_uri}'>{verification_uri}</a><br>"
            "2. Enter the code above (it's already on your clipboard)<br>"
            "3. Approve access — this window closes by itself"
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)

        self.status = QLabel("waiting for approval…")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(code)
        layout.addWidget(hint)
        layout.addWidget(self.status)
        layout.addWidget(cancel)

        QGuiApplication.clipboard().setText(user_code)


class PasteCodeDialog(QDialog):
    """Web login, paste-back style: browser shows an authorization code after
    approval; the user pastes it here."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log in to {label}")
        self.setMinimumWidth(420)

        hint = QLabel(
            "1. Approve access in the browser window that just opened<br>"
            "2. Copy the authorization code it shows you<br>"
            "3. Paste it below"
        )
        hint.setWordWrap(True)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("paste authorization code…")
        ok = QPushButton("Complete login")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.code_edit)
        layout.addLayout(buttons)


class ProviderManager(QDialog):
    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Providers")
        self.resize(760, 520)
        self._current: ProviderSpec | None = None

        self.provider_list = QListWidget()
        self.provider_list.currentItemChanged.connect(self._on_select)

        # detail pane
        self.title = QLabel("")
        self.status = QLabel("")
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.key_link = QLabel("")
        self.key_link.setOpenExternalLinks(True)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("paste API key…")
        save_key = QPushButton("Save key")
        save_key.clicked.connect(self._save_key)
        self.remove_key = QPushButton("Remove key")
        self.remove_key.clicked.connect(self._remove_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(save_key)
        key_row.addWidget(self.remove_key)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("default")
        save_url = QPushButton("Save URL")
        save_url.clicked.connect(self._save_base_url)
        url_row = QHBoxLayout()
        url_row.addWidget(self.base_url_edit, 1)
        url_row.addWidget(save_url)

        # per-provider ids (Cloudflare account/gateway): one field per
        # ${PLACEHOLDER} in the provider's base URL
        self.ids_container = QWidget()
        self.ids_layout = QHBoxLayout(self.ids_container)
        self.ids_layout.setContentsMargins(0, 0, 0, 0)
        self.id_edits: dict[str, QLineEdit] = {}
        self.ids_label = QLabel("IDs")

        self.oauth_button = QPushButton("🌐 Log in via web")
        self.oauth_button.setToolTip(
            "No API key needed — sign in with your account in the browser")
        self.oauth_button.clicked.connect(self._oauth_login)
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self._test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)

        form = QFormLayout()
        form.addRow(self.title)
        form.addRow("status", self.status)
        form.addRow(self.note)
        form.addRow(self.key_link)
        form.addRow("API key", key_row)
        form.addRow(self.ids_label, self.ids_container)
        form.addRow("base URL", url_row)
        form.addRow(self.oauth_button)
        form.addRow(self.test_button)
        form.addRow(self.test_result)

        detail = QWidget()
        detail.setLayout(form)
        layout = QHBoxLayout(self)
        layout.addWidget(self.provider_list, 1)
        layout.addWidget(detail, 2)

        self._reload_list()

    # ------------------------------------------------------------------

    def _reload_list(self) -> None:
        selected = self._current.id if self._current else None
        self.provider_list.clear()
        for spec in KNOWN_PROVIDERS.values():
            state = connection_status(spec, self.engine.config, self.engine.providers.auth)
            dot, _ = _STATUS_BADGE[state]
            item = QListWidgetItem(f"{dot}  {spec.label}")
            item.setData(Qt.UserRole, spec.id)
            self.provider_list.addItem(item)
            if spec.id == selected:
                self.provider_list.setCurrentItem(item)
        if self.provider_list.currentItem() is None and self.provider_list.count():
            self.provider_list.setCurrentRow(0)

    def _on_select(self, item: QListWidgetItem, _prev=None) -> None:
        if item is None:
            return
        spec = KNOWN_PROVIDERS[item.data(Qt.UserRole)]
        self._current = spec
        state = connection_status(spec, self.engine.config, self.engine.providers.auth)
        dot, describe = _STATUS_BADGE[state]
        self.title.setText(f"<h3>{spec.label}</h3>")
        self.status.setText(f"{dot} {describe}"
                            + (f" — env var {spec.key_env}" if state == "connected-env" and spec.key_env else ""))
        self.note.setText(spec.note)
        self.note.setVisible(bool(spec.note))
        self.key_link.setText(
            f'<a href="{spec.key_url}">get an API key ↗</a>' if spec.key_url else ""
        )
        self.key_link.setVisible(bool(spec.key_url))
        self.key_edit.clear()
        self.key_edit.setEnabled(not spec.planned and not spec.local)
        record = self.engine.providers.auth.get(spec.id)
        self.remove_key.setEnabled(bool(record))
        pconf = self.engine.config.provider.get(spec.id)
        self.base_url_edit.setText((pconf.base_url if pconf else None) or spec.base_url or "")
        self.oauth_button.setVisible(spec.oauth and not spec.planned)
        record = self.engine.providers.auth.get(spec.id)
        if spec.oauth and record and record.get("type") == "oauth":
            self.oauth_button.setText("🌐 Logged in — log in again")
        else:
            self.oauth_button.setText("🌐 Log in via web")
        self.test_button.setEnabled(not spec.planned)
        self.test_result.setText("")
        self._rebuild_id_fields(spec)

    def _rebuild_id_fields(self, spec: ProviderSpec) -> None:
        """One friendly input per ${PLACEHOLDER} in the provider's base URL
        (e.g. Cloudflare account id) — nicer than hand-editing the URL."""
        import re

        while self.ids_layout.count():
            item = self.ids_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.id_edits.clear()

        placeholders = re.findall(r"\$\{([A-Z0-9_]+)\}", spec.base_url or "")
        show = bool(placeholders)
        self.ids_label.setVisible(show)
        self.ids_container.setVisible(show)
        if not show:
            return

        # prefill from a previously saved URL by matching it against the template
        saved = ""
        pconf = self.engine.config.provider.get(spec.id)
        if pconf and pconf.base_url and "${" not in pconf.base_url:
            saved = pconf.base_url
        pattern = re.escape(spec.base_url)
        for var in placeholders:
            pattern = pattern.replace(re.escape("${" + var + "}"), f"(?P<{var}>[^/]+)")
        match = re.fullmatch(pattern, saved) if saved else None

        import os

        for var in placeholders:
            edit = QLineEdit()
            label = var.replace("CLOUDFLARE_", "").replace("_", " ").lower()
            edit.setPlaceholderText(label)
            edit.setToolTip(f"Fills ${{{var}}} in the base URL (env var {var} also works)")
            value = (match.group(var) if match else "") or os.environ.get(var, "")
            edit.setText(value)
            self.id_edits[var] = edit
            self.ids_layout.addWidget(edit, 1)
        save_ids = QPushButton("Save IDs")
        save_ids.clicked.connect(self._save_ids)
        self.ids_layout.addWidget(save_ids)

    def _save_ids(self) -> None:
        spec = self._current
        if spec is None or not self.id_edits:
            return
        url = spec.base_url or ""
        for var, edit in self.id_edits.items():
            value = edit.text().strip()
            if not value:
                self.test_result.setText(f"enter a value for {var.lower().replace('_', ' ')}")
                return
            url = url.replace("${" + var + "}", value)
        patch_config_file(self.engine.global_dir / "config.json", {
            f"provider.{spec.id}.base_url": url,
            f"provider.{spec.id}.api": spec.api,
        })
        self.base_url_edit.setText(url)
        self._after_change("IDs saved — base URL filled in")

    # ------------------------------------------------------------------

    def _save_key(self) -> None:
        spec = self._current
        key = self.key_edit.text().strip()
        if spec is None or not key:
            return
        self.engine.providers.auth.set(spec.id, {"type": "api", "key": key})
        self.key_edit.clear()
        self._after_change("key saved to ~/.crew/auth.json")

    def _remove_key(self) -> None:
        spec = self._current
        if spec is None:
            return
        self.engine.providers.auth.remove(spec.id)
        self._after_change("credentials removed")

    def _save_base_url(self) -> None:
        spec = self._current
        if spec is None:
            return
        url = self.base_url_edit.text().strip()
        default = spec.base_url or ""
        path = self.engine.global_dir / "config.json"
        if url and url != default:
            patch_config_file(path, {
                f"provider.{spec.id}.base_url": url,
                f"provider.{spec.id}.api": spec.api,
            })
            self._after_change("base URL saved to global config")
        else:
            self._after_change("using default base URL")

    def _oauth_login(self) -> None:
        spec = self._current
        if spec is None or not spec.oauth:
            return
        if spec.oauth_kind == "device":
            asyncio.ensure_future(self._device_login(spec))
            return
        if spec.oauth_kind == "paste":
            asyncio.ensure_future(self._paste_login(spec))
            return
        # loopback flow: browser redirects back to a local port
        self.test_result.setText("waiting for browser login…")

        async def run() -> None:
            try:
                from ..engine.providers.xai_oauth import login

                record = await login(webbrowser.open)
                self.engine.providers.auth.set(spec.id, record)
                self._after_change("web login complete")
            except Exception as exc:
                self.test_result.setText(f"Login failed: {exc} (provider)")

        asyncio.ensure_future(run())

    async def _paste_login(self, spec: ProviderSpec) -> None:
        from ..engine.providers import anthropic_oauth

        import secrets as _secrets

        verifier, challenge = anthropic_oauth.generate_pkce()
        state = anthropic_oauth._b64url(_secrets.token_bytes(32))
        webbrowser.open(anthropic_oauth.build_authorize_url(challenge, state))
        dialog = PasteCodeDialog(spec.label, self)
        if dialog.exec() != QDialog.Accepted or not dialog.code_edit.text().strip():
            self.test_result.setText("Login cancelled.")
            return
        self.test_result.setText("Exchanging code…")
        try:
            record = await anthropic_oauth.exchange_code(
                dialog.code_edit.text(), verifier, state)
        except Exception as exc:
            self.test_result.setText(f"Login failed: {exc}")
            return
        self.engine.providers.auth.set(spec.id, record)
        self._after_change("web login complete — Claude connected")

    async def _device_login(self, spec: ProviderSpec) -> None:
        if spec.id == "xai":
            from ..engine.providers.xai_oauth import (
                poll_device_code_token as poll_fn,
                request_device_code as start_fn,
            )
        else:
            from ..engine.providers.github_copilot_oauth import (
                poll_for_token as poll_fn,
                start_device_flow as start_fn,
            )

        self.test_result.setText("requesting device code…")
        try:
            device = await start_fn()
        except Exception as exc:
            self.test_result.setText(f"Login failed: {exc}")
            return

        dialog = DeviceCodeDialog(spec.label, device.user_code, device.verification_uri, self)
        # prefer the pre-filled URL when the provider gives one (xAI does)
        webbrowser.open(getattr(device, "verification_uri_complete", "") or device.verification_uri)
        poll = asyncio.ensure_future(
            poll_fn(device, on_status=dialog.status.setText)
        )
        dialog.rejected.connect(poll.cancel)
        dialog.show()
        try:
            record = await poll
        except asyncio.CancelledError:
            self.test_result.setText("Login cancelled.")
            return
        except Exception as exc:
            dialog.close()
            self.test_result.setText(f"Login failed: {exc}")
            return
        dialog.close()
        self.engine.providers.auth.set(spec.id, record)
        self._after_change("web login complete — Copilot connected")

    def _test(self) -> None:
        spec = self._current
        if spec is None:
            return
        self.test_result.setText("testing…")

        async def run() -> None:
            ok, message = await probe_provider(
                spec.id, self.engine.config, self.engine.providers.auth
            )
            self.test_result.setText(("✅ " if ok else "❌ ") + message)

        asyncio.ensure_future(run())

    def _after_change(self, message: str) -> None:
        # drop cached provider clients so new credentials take effect
        self.engine.reload_config()
        self.engine.bus.emit(AgentRegistryChanged())
        self._reload_list()
        if self._current:
            self._on_select(self.provider_list.currentItem())
        self.test_result.setText(message)
