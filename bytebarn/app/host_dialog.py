"""Add/edit dialog for saved hosts (spec: 2026-08-05-hosts-and-rail-labels)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..engine.hosts import Host


class HostDialog(QDialog):
    """Form for one host. No password field on purpose — keys/agent only."""

    def __init__(self, host: Host | None = None, parent=None):
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
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Host", self.host_edit)
        form.addRow("User", self.user_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("SSH key", key_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._maybe_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

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
        """Field values, ready for HostStore.add(**values)."""
        return {
            "name": self.name_edit.text().strip(),
            "hostname": self.host_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "port": int(self.port_spin.value()),
            "identity_file": self.key_edit.text().strip(),
        }
