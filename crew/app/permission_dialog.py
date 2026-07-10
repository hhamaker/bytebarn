"""Modal permission request dialog (spec §7.1, §8)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .markdown import escape


def _render_input(tool: str, input_data: dict[str, Any]) -> str:
    if tool == "bash":
        return (
            "<pre style='background:#282828; padding:8px; border-radius:4px;'>"
            f"{escape(input_data.get('command', ''))}</pre>"
        )
    if tool == "edit":
        old = escape(input_data.get("old_string", ""))
        new = escape(input_data.get("new_string", ""))
        return (
            f"<b>{escape(input_data.get('path', ''))}</b>"
            "<pre style='background:#3a2626; padding:6px; border-radius:4px; color:#e06c75;'>"
            f"- {old}</pre>"
            "<pre style='background:#26392a; padding:6px; border-radius:4px; color:#98c379;'>"
            f"+ {new}</pre>"
        )
    if tool == "write":
        content = escape(input_data.get("content", "")[:1500])
        return (
            f"<b>{escape(input_data.get('path', ''))}</b>"
            f"<pre style='background:#26392a; padding:6px; border-radius:4px; color:#98c379;'>{content}</pre>"
        )
    return f"<pre>{escape(str(input_data)[:1500])}</pre>"


class PermissionDialog(QDialog):
    """Returns via .verdict: "allow" | "allow_always" | "deny"."""

    def __init__(self, tool: str, arg: str, input_data: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Permission: {tool}")
        self.setMinimumWidth(520)
        self.verdict = "deny"

        body = QLabel(f"<h3>{escape(tool)}</h3>" + _render_input(tool, input_data))
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)

        allow = QPushButton("Allow once")
        always = QPushButton("Allow always")
        deny = QPushButton("Deny")
        deny.setDefault(True)
        allow.clicked.connect(lambda: self._finish("allow"))
        always.clicked.connect(lambda: self._finish("allow_always"))
        deny.clicked.connect(lambda: self._finish("deny"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(deny)
        buttons.addWidget(always)
        buttons.addWidget(allow)

        layout = QVBoxLayout(self)
        layout.addWidget(body)
        layout.addLayout(buttons)

    def _finish(self, verdict: str) -> None:
        self.verdict = verdict
        self.accept()
