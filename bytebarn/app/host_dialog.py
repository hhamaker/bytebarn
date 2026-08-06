"""Add/edit dialog for saved hosts (spec: 2026-08-05-hosts-and-rail-labels)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..engine.hosts import KEY_AUTH, PASSWORD_AUTH, Host


class HostDialog(QDialog):
    """Form for one host: SSH key (agent/keyfile) or username + password.

    A password typed here is stored in the 0600 auth store, never in
    hosts.json and never on an ssh command line."""

    def __init__(self, host: Host | None = None, has_password: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit host" if host else "New host")
        self.setMinimumWidth(380)

        self.name_edit = QLineEdit(host.name if host else "")
        self.name_edit.setPlaceholderText("prod web server")
        self.host_edit = QLineEdit(host.hostname if host else "")
        self.host_edit.setPlaceholderText("example.com or 10.0.0.5")
        self.user_edit = QLineEdit(host.username if host else "")
        self.user_edit.setPlaceholderText("optional — defaults to your user")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(host.port if host else 22)
        self.key_edit = QLineEdit(host.identity_file if host else "")
        self.key_edit.setPlaceholderText("optional — ~/.ssh/id_ed25519")
        browse = QPushButton("Browse…")
        browse.setFlat(True)
        browse.clicked.connect(self._browse_key)
        self.key_row = QWidget()
        key_row = QHBoxLayout(self.key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(browse)

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("SSH key / agent", KEY_AUTH)
        self.auth_combo.addItem("Password", PASSWORD_AUTH)
        auth_type = host.auth_type if host else KEY_AUTH
        self.auth_combo.setCurrentIndex(1 if auth_type == PASSWORD_AUTH else 0)
        self.auth_combo.currentIndexChanged.connect(self._auth_changed)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText(
            "saved — leave blank to keep" if has_password
            else "stored encrypted-at-rest in ~/.bytebarn/auth.json (0600)")

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Host", self.host_edit)
        form.addRow("User", self.user_edit)
        form.addRow("Port", self.port_spin)
        self.accept_new_check = QCheckBox("Trust new host key on first connect")
        self.accept_new_check.setChecked(bool(host.accept_new_key) if host else False)
        self.accept_new_check.setToolTip(
            "Adds the server's key to ~/.ssh/known_hosts without asking the "
            "first time. A key that later changes is still refused. Leave off "
            "if you want to check the fingerprint yourself.")

        form.addRow("Auth", self.auth_combo)
        self._key_label = "SSH key"
        form.addRow(self._key_label, self.key_row)
        form.addRow("Password", self.password_edit)
        form.addRow("", self.accept_new_check)
        self._form = form
        self._auth_changed()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._maybe_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def auth_type(self) -> str:
        return self.auth_combo.currentData()

    def _auth_changed(self) -> None:
        password = self.auth_type() == PASSWORD_AUTH
        self.key_row.setVisible(not password)
        self.password_edit.setVisible(password)
        for widget, shown in ((self.key_row, not password),
                              (self.password_edit, password)):
            label = self._form.labelForField(widget)
            if label is not None:
                label.setVisible(shown)

    def password(self) -> str:
        """Typed password ("" means keep whatever is already stored)."""
        return self.password_edit.text() if self.auth_type() == PASSWORD_AUTH else ""

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH key")
        if path:
            self.key_edit.setText(path)

    def _maybe_accept(self) -> None:
        if self.host_edit.text().strip():
            self.accept()
        else:
            self.host_edit.setFocus()

    def values(self) -> dict:
        """Field values, ready for HostStore.add(**values). Never the password."""
        password_auth = self.auth_type() == PASSWORD_AUTH
        return {
            "name": self.name_edit.text().strip(),
            "hostname": self.host_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "port": int(self.port_spin.value()),
            "identity_file": "" if password_auth else self.key_edit.text().strip(),
            "auth_type": self.auth_type(),
            "accept_new_key": self.accept_new_check.isChecked(),
        }
