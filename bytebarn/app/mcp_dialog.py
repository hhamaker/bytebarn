"""MCP server manager: curated one-click recipes plus custom entries.

Pick a server (GitHub, Google Drive, …), paste the credential it asks for,
and Add writes the ``mcp`` block into the global config and reconnects —
no hand-editing JSON. Custom servers take a command line or URL directly.
Servers and their tools are listed with live connection status."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..engine.config import DELETE, patch_config_file
from ..engine.mcp import KNOWN_MCP_SERVERS, config_entry

_CUSTOM = "Custom…"
_NAME_ROLE = Qt.UserRole


class MCPDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("MCP Servers")
        self.setMinimumSize(560, 560)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "External tool servers (Model Context Protocol). Their tools show"
            " up to agents as <code>mcp__server__tool</code> and follow the"
            " permission policy — ask by default, denied in Safe mode.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._server_menu)
        layout.addWidget(self.tree, 1)

        # -- add a server ----------------------------------------------------
        box = QGroupBox("Add a server")
        form = QFormLayout(box)
        self.picker = QComboBox()
        self.picker.addItems([s.label for s in KNOWN_MCP_SERVERS.values()])
        self.picker.addItem(_CUSTOM)
        self.picker.currentTextChanged.connect(self._render_fields)
        form.addRow("Server", self.picker)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8f96a3")
        self.note.setTextFormat(Qt.RichText)
        self.note.setOpenExternalLinks(True)
        form.addRow(self.note)

        # dynamic credential fields (rebuilt per selection)
        self._fields: dict[str, QLineEdit] = {}
        self._field_form = QFormLayout()
        form.addRow(self._field_form)

        add = QPushButton("Add server")
        add.clicked.connect(self._add)
        form.addRow(add)
        layout.addWidget(box)

        where = QLabel(f"Stored in {engine.global_dir / 'config.json'} under"
                       " the <code>mcp</code> key (right-click a server to"
                       " remove it).")
        where.setWordWrap(True)
        where.setStyleSheet("color:#8f96a3")
        layout.addWidget(where)

        self._render_fields(self.picker.currentText())
        self._reload()

    # -- status list ---------------------------------------------------------

    def _reload(self) -> None:
        self.tree.clear()
        status = self.engine.mcp.status()
        if not status:
            self.tree.addTopLevelItem(
                QTreeWidgetItem(["(no MCP servers configured — add one below)"]))
            return
        for server in status:
            state = "connected" if server["connected"] else \
                (server["error"] or "disconnected")
            top = QTreeWidgetItem(
                [f"{'🟢' if server['connected'] else '🔴'} {server['name']}"
                 f"  ({server['transport']}) — {state}"])
            top.setData(0, _NAME_ROLE, server["name"])
            for tool in server["tools"]:
                top.addChild(QTreeWidgetItem([tool]))
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

    def _server_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        name = item.data(0, _NAME_ROLE) if item else None
        if not name:
            return
        menu = QMenu(self)
        remove = menu.addAction(f"Remove '{name}'")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is remove:
            answer = QMessageBox.question(
                self, "Remove server", f"Remove MCP server '{name}'?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer == QMessageBox.Yes:
                patch_config_file(self.engine.global_dir / "config.json",
                                  {f"mcp.{name}": DELETE})
                self._apply()

    # -- add flow ------------------------------------------------------------

    def _spec(self):
        label = self.picker.currentText()
        return next((s for s in KNOWN_MCP_SERVERS.values() if s.label == label), None)

    def _render_fields(self, _label: str) -> None:
        while self._field_form.rowCount():
            self._field_form.removeRow(0)
        self._fields = {}
        spec = self._spec()

        def add_field(key: str, label: str, secret: bool = False) -> None:
            edit = QLineEdit()
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            self._fields[key] = edit
            self._field_form.addRow(label, edit)

        if spec is None:  # custom
            self.note.setText(
                "A command line (stdio) <i>or</i> a URL (streamable HTTP)."
                " Example command: <code>npx -y @modelcontextprotocol/server-memory</code>")
            add_field("name", "Name")
            add_field("command", "Command (stdio)")
            add_field("url", "URL (http)")
            return

        note = spec.note
        if spec.key_url:
            note += f' <a href="{spec.key_url}">Create credential ↗</a>'
        self.note.setText(note)
        if spec.bearer:
            add_field("token", "Access token", secret=True)
        for var, label in spec.env_keys:
            add_field(var, label, secret="KEY" in var or "TOKEN" in var)
        for key, label in spec.arg_keys:
            add_field(key, label)

    def _add(self) -> None:
        values = {k: e.text() for k, e in self._fields.items()}
        spec = self._spec()
        if spec is not None:
            name, entry = spec.id, config_entry(spec, values)
        else:
            name = values.get("name", "").strip()
            command = values.get("command", "").strip()
            url = values.get("url", "").strip()
            if not name or not (command or url):
                QMessageBox.information(
                    self, "Add server", "A name and a command or URL are required.")
                return
            if url:
                entry = {"url": url}
            else:
                parts = command.split()
                entry = {"command": parts[0], "args": parts[1:]}
        patch_config_file(self.engine.global_dir / "config.json", {f"mcp.{name}": entry})
        self._apply()

    def _apply(self) -> None:
        """Re-read config, reconnect servers, refresh the status list."""
        from ..engine.config import load_config

        async def refresh() -> None:
            self.engine.config = load_config(
                self.engine.project_dir, self.engine.global_dir)
            await self.engine.mcp.restart(self.engine.config)
            self._reload()

        try:
            asyncio.get_running_loop()
            asyncio.ensure_future(refresh())
        except RuntimeError:
            self._reload()
