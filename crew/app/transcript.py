"""Scrolling transcript: markdown text, collapsible tool cards, reasoning (spec §7.1)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .markdown import escape, render_markdown

_STATUS_ICON = {"pending": "…", "running": "⟳", "done": "✓", "error": "✗"}
_STATUS_COLOR = {"pending": "#888", "running": "#e5c07b", "done": "#98c379", "error": "#e06c75"}


class _Card(QFrame):
    """Collapsible card with a header button and a body widget."""

    def __init__(self, collapsed: bool = True):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.set_accent("#3a3f4b")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        self.header = QPushButton()
        self.header.setFlat(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet("QPushButton { border: none; text-align: left; padding: 2px; }")
        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setVisible(not collapsed)
        self.header.clicked.connect(lambda: self.body.setVisible(not self.body.isVisible()))
        layout.addWidget(self.header)
        layout.addWidget(self.body)

    def set_accent(self, color: str) -> None:
        self.setStyleSheet(
            "QFrame { border: 1px solid #3a3f4b; border-radius: 4px;"
            f" border-left: 3px solid {color}; }}"
        )


class ToolCard(_Card):
    clicked_task = Signal(str)  # subagent_session_id

    def __init__(self, data: dict[str, Any]):
        super().__init__(collapsed=True)
        self._subagent_id = ""
        self.update_data(data)

    def update_data(self, data: dict[str, Any]) -> None:
        status = data.get("status", "pending")
        icon = _STATUS_ICON.get(status, "?")
        color = _STATUS_COLOR.get(status, "#888")
        tool = data.get("tool", "")
        title = data.get("title") or self._summarize_input(tool, data.get("input", {}))
        self.header.setText(f"{icon} {tool}  {title[:90]}")
        self.header.setStyleSheet(
            f"QPushButton {{ border: none; text-align: left; padding: 2px; color: {color}; }}"
        )
        self.set_accent(color)
        input_html = escape(str(data.get("input", "")))[:2000]
        output = data.get("output", "")
        output_html = escape(output[:4000]) if output else "<i>(no output yet)</i>"
        self.body.setText(
            f"<div style='color:#aaa'><b>input:</b> {input_html}</div>"
            f"<div style='white-space:pre-wrap; font-family:monospace; font-size:12px'>{output_html}</div>"
        )
        self._subagent_id = data.get("subagent_session_id", "")

    @staticmethod
    def _summarize_input(tool: str, input_data: dict) -> str:
        for key in ("command", "path", "pattern", "url", "description", "question"):
            if key in input_data:
                return str(input_data[key])
        return str(input_data)[:60]


class ReasoningCard(_Card):
    def __init__(self, text: str):
        super().__init__(collapsed=True)
        self.header.setText("· thinking")
        self.header.setStyleSheet("QPushButton { border:none; text-align:left; color:#777; font-style:italic; }")
        self.update_text(text)

    def update_text(self, text: str) -> None:
        self.body.setText(f"<i style='color:#999'>{escape(text)}</i>")


class CompactionCard(_Card):
    def __init__(self, text: str):
        super().__init__(collapsed=True)
        self.header.setText("⇣ context compacted — summary")
        self.body.setText(render_markdown(text))


class TextBlock(QLabel):
    def __init__(self, text: str, user: bool):
        super().__init__()
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._user = user
        if user:
            self.setStyleSheet(
                "QLabel { background-color: #2c313c; color: #e6e6e6;"
                " border-radius: 6px; padding: 8px; }"
            )
        else:
            self.setStyleSheet("QLabel { padding: 4px; }")
        self.update_text(text)

    def update_text(self, text: str) -> None:
        if self._user:
            self.setText(escape(text).replace("\n", "<br>"))
        else:
            self.setText(render_markdown(text))


class _Welcome(QWidget):
    """Empty-session greeting: a little crew waiting for work."""

    def __init__(self):
        super().__init__()
        from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

        from .sprites import SPRITE_H, SPRITE_W, draw_critter, look_for

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        scale = 4
        cast = [("orchestrator", "#e5c07b"), ("build", "#61afef"),
                ("explore", "#56b6c2"), ("general", "#98c379")]
        image = QImage((SPRITE_W + 6) * scale * len(cast), (SPRITE_H + 4) * scale,
                       QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        for i, (name, color) in enumerate(cast):
            species, accent = look_for(name)
            draw_critter(painter, (i * (SPRITE_W + 6) + 3) * scale, 3 * scale, scale,
                         species, QColor(color), state="done", accent=accent,
                         crowned=name == "orchestrator")
        painter.end()
        sprites = QLabel()
        sprites.setPixmap(QPixmap.fromImage(image))
        sprites.setAlignment(Qt.AlignCenter)

        text = QLabel(
            "<div style='text-align:center; color:#8f96a3'>"
            "<h3 style='color:#c8ccd4'>The crew is ready.</h3>"
            "<p>Type a prompt below — or try <b>/goal …</b> to put the whole crew on it.</p>"
            "<p><b>/</b> commands · <b>@</b> attach files · <b>Shift+Enter</b> newline · "
            "<b>Esc</b> stop</p>"
            "<p>Connect models via <b>⚡ providers</b>, tune your crew via <b>🐾 agents</b> "
            "(status bar).</p></div>"
        )
        text.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(sprites)
        layout.addWidget(text)
        layout.addStretch(2)


class Transcript(QScrollArea):
    open_session = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignTop)
        self._layout.setSpacing(8)
        self.setWidget(self._container)
        self._part_widgets: dict[str, QWidget] = {}
        self._welcome: QWidget | None = None
        self._autoscroll = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.verticalScrollBar().rangeChanged.connect(self._on_range)

    # -- loading ----------------------------------------------------------

    def clear_all(self) -> None:
        self._part_widgets.clear()
        self._welcome = None
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._autoscroll = True

    def load_history(self, history: list[tuple[Any, list[Any]]]) -> None:
        self.clear_all()
        for message, parts in history:
            for part in parts:
                self._add_part(message.role, part.id, part.type, part.data)
        if not self._part_widgets:
            self._welcome = _Welcome()
            self._layout.setAlignment(Qt.AlignVCenter)
            self._layout.addWidget(self._welcome)

    def _dismiss_welcome(self) -> None:
        if self._welcome is not None:
            self._layout.removeWidget(self._welcome)
            self._welcome.deleteLater()
            self._welcome = None
            self._layout.setAlignment(Qt.AlignTop)

    # -- streaming updates --------------------------------------------------

    def on_part_updated(self, part_id: str, part_type: str, data: dict[str, Any], role: str = "assistant") -> None:
        widget = self._part_widgets.get(part_id)
        if widget is None:
            self._add_part(role, part_id, part_type, data)
            return
        if isinstance(widget, ToolCard):
            widget.update_data(data)
        elif isinstance(widget, ReasoningCard):
            widget.update_text(data.get("text", ""))
        elif isinstance(widget, TextBlock):
            widget.update_text(data.get("text", ""))

    def add_user_text(self, part_id: str, text: str) -> None:
        self._add_part("user", part_id, "text", {"text": text})

    def _add_part(self, role: str, part_id: str, part_type: str, data: dict[str, Any]) -> None:
        self._dismiss_welcome()
        widget: QWidget | None = None
        if part_type == "text":
            widget = TextBlock(data.get("text", ""), user=role == "user")
        elif part_type == "reasoning":
            widget = ReasoningCard(data.get("text", ""))
        elif part_type in ("tool", "task"):
            card = ToolCard(data)
            card.header.clicked.connect(lambda *_, c=card: self._maybe_open(c))
            widget = card
        elif part_type == "compaction":
            widget = CompactionCard(data.get("text", ""))
        elif part_type == "file":
            widget = self._file_widget(data.get("path", ""), user=role == "user")
        if widget is None:
            return
        self._part_widgets[part_id] = widget
        self._layout.addWidget(widget)

    _IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

    def _file_widget(self, path: str, user: bool) -> QWidget:
        """Attachment part: inline thumbnail for images, 📎 line otherwise."""
        from pathlib import Path as _Path

        from PySide6.QtGui import QPixmap

        p = _Path(path)
        if p.suffix.lower() in self._IMAGE_SUFFIXES and p.is_file():
            pixmap = QPixmap(str(p))
            if not pixmap.isNull():
                if pixmap.width() > 420:
                    pixmap = pixmap.scaledToWidth(420, Qt.SmoothTransformation)
                label = QLabel()
                label.setPixmap(pixmap)
                label.setToolTip(path)
                label.setStyleSheet(
                    "QLabel { border: 1px solid #3a3f4b; border-radius: 6px; padding: 4px; }")
                return label
        return TextBlock(f"📎 {path}", user=user)

    def _maybe_open(self, card: ToolCard) -> None:
        if card._subagent_id:
            self.open_session.emit(card._subagent_id)

    # -- autoscroll ----------------------------------------------------------

    def _on_scroll(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._autoscroll = value >= bar.maximum() - 10

    def _on_range(self, _min: int, _max: int) -> None:
        if self._autoscroll:
            self.verticalScrollBar().setValue(_max)
