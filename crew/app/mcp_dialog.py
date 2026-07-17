"""MCP servers overview: connection status and the tools each one exposes.

Servers are configured in ``~/.crew/config.json`` (or the project's
``.crew/config.json``) under the ``mcp`` key; this dialog is read-only."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

_EXAMPLE = (
    '"mcp": {\n'
    '  "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},\n'
    '  "linear": {"url": "https://mcp.linear.app/mcp"}\n'
    "}"
)


class MCPDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("MCP Servers")
        self.setMinimumSize(520, 400)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "External tool servers (Model Context Protocol). Their tools are"
            " offered to agents as <code>mcp__server__tool</code> and follow"
            " the permission policy (ask by default, denied in Safe mode).")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree, 1)

        example = QLabel(
            f"Add servers to <code>{engine.global_dir / 'config.json'}</code>"
            f" (or the project's .crew/config.json):<pre>{_EXAMPLE}</pre>")
        example.setWordWrap(True)
        example.setStyleSheet("color:#8f96a3")
        layout.addWidget(example)

        reconnect = QPushButton("Reconnect")
        reconnect.clicked.connect(self._reconnect)
        layout.addWidget(reconnect)

        self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        status = self.engine.mcp.status()
        if not status:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(no MCP servers configured)"]))
            return
        for server in status:
            state = "connected" if server["connected"] else \
                (server["error"] or "disconnected")
            top = QTreeWidgetItem(
                [f"{'🟢' if server['connected'] else '🔴'} {server['name']}"
                 f"  ({server['transport']}) — {state}"])
            for tool in server["tools"]:
                top.addChild(QTreeWidgetItem([tool]))
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

    def _reconnect(self) -> None:
        import asyncio

        async def run() -> None:
            await self.engine.mcp.restart(self.engine.config)
            self._reload()

        asyncio.ensure_future(run())
