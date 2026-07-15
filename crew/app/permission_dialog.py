"""Modal permission request dialog (spec §7.1, §8)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .markdown import escape


def _render_input(tool: str, input_data: dict[str, Any]) -> str:
    if tool == "bash":
        # <font> wrap: Qt rich text ignores css color on <pre>, so pair the
        # dark bg with an explicit font color (readable on light themes too)
        return (
            "<pre style='background:#282828; padding:8px; border-radius:4px;'>"
            f"<font color='#f0f0f0'>{escape(input_data.get('command', ''))}</font></pre>"
        )
    if tool == "edit":
        old = escape(input_data.get("old_string", ""))
        new = escape(input_data.get("new_string", ""))
        return (
            f"<b>{escape(input_data.get('path', ''))}</b>"
            "<pre style='background:#3a2626; padding:6px; border-radius:4px;'>"
            f"<font color='#e06c75'>- {old}</font></pre>"
            "<pre style='background:#26392a; padding:6px; border-radius:4px;'>"
            f"<font color='#98c379'>+ {new}</font></pre>"
        )
    if tool == "write":
        content = escape(input_data.get("content", "")[:1500])
        return (
            f"<b>{escape(input_data.get('path', ''))}</b>"
            "<pre style='background:#26392a; padding:6px; border-radius:4px;'>"
            f"<font color='#98c379'>{content}</font></pre>"
        )
    return (
        "<pre style='background:#282828; padding:8px; border-radius:4px;'>"
        f"<font color='#f0f0f0'>{escape(str(input_data)[:1500])}</font></pre>"
    )


class PermissionDialog(QDialog):
    """Returns via .verdict: "allow" | "allow_always" | "deny" | "full_auto"."""

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
        full_auto = QPushButton("Switch to Full-auto & Allow")
        deny.setDefault(True)
        allow.clicked.connect(lambda: self._finish("allow"))
        always.clicked.connect(lambda: self._finish("allow_always"))
        deny.clicked.connect(lambda: self._finish("deny"))
        full_auto.clicked.connect(lambda: self._finish("full_auto"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(deny)
        buttons.addWidget(always)
        buttons.addWidget(allow)
        buttons.addWidget(full_auto)

        layout = QVBoxLayout(self)
        layout.addWidget(body)
        layout.addLayout(buttons)

    def _finish(self, verdict: str) -> None:
        self.verdict = verdict
        self.accept()
