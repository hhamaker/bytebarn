"""Scrolling transcript: markdown text, collapsible tool cards, reasoning (spec §7.1)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
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
_MARKDOWN_SETTLE_MS = 100  # re-render markdown after stream pauses


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
        inp = data.get("input", {}) or {}
        title = data.get("title") or self._summarize_input(tool, inp)
        if tool == "task" and inp.get("agent"):
            title = f"{inp['agent']} — {title}"
        self._subagent_id = data.get("subagent_session_id", "") or self._subagent_id
        header = f"{icon} {tool}  {title[:90]}"
        if self._subagent_id:
            header += "  ↗ open"
        self.header.setText(header)
        self.header.setToolTip("Open subagent session" if self._subagent_id else "")
        self.header.setStyleSheet(
            f"QPushButton {{ border: none; text-align: left; padding: 2px; color: {color}; }}"
        )
        self.set_accent(color)
        input_html = self._pretty_input(tool, inp)[:2000]
        output = data.get("output", "") or ""
        truncated = len(output) >= 4000
        output_html = escape(output[:4000]) if output else "<i>(no output yet)</i>"
        if truncated:
            output_html += "\n… (truncated)"
        out_style = "color:#e06c75" if status == "error" else ""
        self.body.setText(
            f"<div style='color:#aaa'><b>input:</b> {input_html}</div>"
            f"<div style='white-space:pre-wrap; font-family:monospace; font-size:12px; {out_style}'>{output_html}</div>"
        )
        if status == "error":
            self.body.setVisible(True)
            self.body.show()

    @staticmethod
    def _pretty_input(tool: str, inp: dict) -> str:
        if tool == "bash" and "command" in inp:
            return f"<span style='font-family:monospace'>{escape(str(inp['command']))}</span>"
        if tool in ("read", "write", "edit", "glob") and ("path" in inp or "pattern" in inp):
            return escape(str(inp.get("path") or inp.get("pattern")))
        if tool == "webfetch" and "url" in inp:
            return escape(str(inp["url"]))
        if tool == "task":
            return escape(f"{inp.get('agent','')} — {inp.get('description','')}".strip(" —"))
        if tool == "question" and "question" in inp:
            return escape(str(inp["question"]))
        pairs = [f"{k}: {v}" for k, v in inp.items()]
        return escape("\n".join(pairs)) if pairs else ""

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
    def __init__(self, text: str, user: bool, queued: bool = False):
        super().__init__()
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._user = user
        self._queued = queued
        self._raw = text
        self._md_timer: QTimer | None = None
        if user:
            self._apply_user_style()
        else:
            self.setStyleSheet("QLabel { padding: 4px; }")
        self.update_text(text, queued, streaming=False)

    def _apply_user_style(self) -> None:
        style = "QLabel { background-color: #2c313c; color: #e6e6e6; border-radius: 6px; padding: 8px; }"
        if self._queued:
            style = (
                "QLabel { background-color: #2c313c; color: #e6e6e6; border-radius: 6px; "
                "padding: 8px; border: 1px dashed #e5c07b; }"
            )
        self.setStyleSheet(style)

    def set_queued(self, queued: bool) -> None:
        if not self._user or self._queued == queued:
            return
        self._queued = queued
        self._apply_user_style()
        self.update_text(self._raw, queued, streaming=False)

    def update_text(self, text: str, queued: bool = False, streaming: bool = False) -> None:
        self._raw = text
        if self._user:
            if queued != self._queued:
                self._queued = queued
                self._apply_user_style()
            prefix = "<span style='color:#e5c07b'>[queued]</span> " if self._queued else ""
            self.setText(prefix + escape(text).replace("\n", "<br>"))
            return
        if streaming:
            # Fast path during token stream — full markdown is expensive (pygments).
            self.setText(escape(text).replace("\n", "<br>"))
            self._schedule_markdown()
        else:
            self.setText(render_markdown(text))

    def _schedule_markdown(self) -> None:
        if self._md_timer is None:
            self._md_timer = QTimer(self)
            self._md_timer.setSingleShot(True)
            self._md_timer.timeout.connect(self._finalize_markdown)
        self._md_timer.start(_MARKDOWN_SETTLE_MS)

    def _finalize_markdown(self) -> None:
        self.setText(render_markdown(self._raw))


class _Welcome(QWidget):
    """Empty-session greeting: the workshop at night.

    A painted scene — gradient night sky, softly twinkling pixel stars, a
    warm lantern glow pooling behind the crew — with the shortcuts drawn as
    keycaps. Colors follow the active theme; light mode gets a daylight
    room without stars."""

    _KEYS = (("/", "commands"), ("@", "files"),
             ("⇧⏎", "newline"), ("esc", "stop"))

    def __init__(self):
        super().__init__()
        import random

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(380)
        rng = random.Random(7)  # deterministic sky
        self._stars = [(rng.random(), rng.random() * 0.62, rng.choice((1, 1, 2)),
                        rng.random() * 6.28) for _ in range(70)]
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._phase += 0.09
        self.update()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(640, 460)

    def paintEvent(self, event) -> None:
        import math

        from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QRadialGradient
        from PySide6.QtCore import QPointF, QRectF

        from . import theme
        from .sprites import crew_banner

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        light = theme.current_mode() == "light"
        modern = theme.is_modern()
        accent = QColor(theme.MODERN_ACCENT if modern else theme.ACCENT)

        # sky
        sky = QLinearGradient(0, 0, 0, h)
        if light:
            sky.setColorAt(0, QColor("#f3f4f7"))
            sky.setColorAt(1, QColor("#e9ebef"))
        else:
            sky.setColorAt(0.0, QColor("#101318"))
            sky.setColorAt(0.62, QColor("#161a22"))
            sky.setColorAt(1.0, QColor("#1c1a1f") if modern else QColor("#191d26"))
        painter.fillRect(self.rect(), sky)

        # stars (night only) — slow asynchronous twinkle
        if not light:
            for fx, fy, size, offset in self._stars:
                alpha = 36 + int(52 * (0.5 + 0.5 * math.sin(self._phase + offset)))
                painter.fillRect(int(fx * w), int(fy * h), size, size,
                                 QColor(174, 184, 204, alpha))

        # lantern glow pooling behind the crew
        banner = crew_banner()
        cx, cy = w / 2, h * 0.42
        glow = QRadialGradient(QPointF(cx, cy), max(w, h) * 0.40)
        g = QColor(accent)
        g.setAlpha(26 if light else 46)
        glow.setColorAt(0.0, g)
        mid = QColor(accent)
        mid.setAlpha(10 if light else 18)
        glow.setColorAt(0.55, mid)
        edge = QColor(accent)
        edge.setAlpha(0)
        glow.setColorAt(1.0, edge)
        painter.fillRect(self.rect(), glow)  # falls off to transparent — no seam

        painter.drawPixmap(int(cx - banner.width() / 2),
                           int(cy - banner.height() / 2), banner)

        # title + invitation
        text_color = QColor("#3a3f4a") if light else QColor("#d6dae2")
        muted = QColor("#7c828e") if light else QColor("#7d8590")
        title_font = QFont()
        title_font.setPointSizeF(16)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(text_color)
        painter.drawText(QRectF(0, cy + banner.height() / 2 + 14, w, 30),
                         Qt.AlignHCenter, "The crew is ready.")
        sub_font = QFont()
        sub_font.setPointSizeF(11.5)
        painter.setFont(sub_font)
        painter.setPen(muted)
        painter.drawText(
            QRectF(0, cy + banner.height() / 2 + 46, w, 24), Qt.AlignHCenter,
            "Type a prompt below — or set a goal and let them work.")

        # shortcuts as keycaps
        cap_font = QFont("Menlo")
        cap_font.setStyleHint(QFont.Monospace)
        cap_font.setPointSizeF(9.5)
        label_font = QFont()
        label_font.setPointSizeF(10)
        cm, lm = QFontMetrics(cap_font), QFontMetrics(label_font)
        gap, pad = 22, 8
        widths = []
        for key, label in self._KEYS:
            widths.append(cm.horizontalAdvance(key) + pad * 2 + 6
                          + lm.horizontalAdvance(label))
        x = (w - (sum(widths) + gap * (len(widths) - 1))) / 2
        y = cy + banner.height() / 2 + 92
        cap_bg = QColor("#ffffff") if light else QColor("#252b36")
        cap_border = QColor("#c9cdd6") if light else QColor("#333a47")
        for (key, label), width in zip(self._KEYS, widths):
            cap_w = cm.horizontalAdvance(key) + pad * 2
            cap = QRectF(x, y, cap_w, 22)
            painter.setPen(cap_border)
            painter.setBrush(cap_bg)
            painter.drawRoundedRect(cap, 5, 5)
            painter.setFont(cap_font)
            painter.setPen(text_color)
            painter.drawText(cap, Qt.AlignCenter, key)
            painter.setFont(label_font)
            painter.setPen(muted)
            painter.drawText(QRectF(cap.right() + 6, y, width - cap_w, 22),
                             Qt.AlignVCenter | Qt.AlignLeft, label)
            x += width + gap
        painter.end()


class _Thinking(QWidget):
    """Animated "thinking…" row shown between submit and the first token."""

    def __init__(self, agent: str, color: str):
        super().__init__()
        from PySide6.QtCore import QTimer

        from .sprites import critter_pixmap

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        row = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(critter_pixmap(agent, color, scale=2))
        self._text = QLabel()
        self._text.setStyleSheet("color:#8f96a3; font-style:italic;")
        h.addWidget(icon)
        h.addWidget(self._text)
        h.addStretch(1)
        layout.addWidget(row)

        self._agent = agent
        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    def _tick(self) -> None:
        self._ticks += 1
        dots = "." * (1 + self._ticks % 3)
        self._text.setText(f"{self._agent} is thinking{dots}")


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
        self._thinking: QWidget | None = None
        self._autoscroll = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.verticalScrollBar().rangeChanged.connect(self._on_range)
        self.request_older = None  # set by MainWindow

    # -- loading ----------------------------------------------------------

    def clear_all(self) -> None:
        self._part_widgets.clear()
        self._welcome = None
        self._thinking = None
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._autoscroll = True

    def load_history(self, history: list[tuple[Any, list[Any]]]) -> None:
        self.clear_all()
        for message, parts in history:
            for part in parts:
                if not hasattr(part, "data"):
                    continue
                part.data["_created_at"] = message.created_at
                self._add_part(message.role, part.id, part.type, part.data, streaming=False)
        if not self._part_widgets:
            self._welcome = _Welcome()
            self._layout.setAlignment(Qt.AlignVCenter)
            self._layout.addWidget(self._welcome)

    # ------------------------------------------------------------------
    # lazy / incremental loading helpers
    # ------------------------------------------------------------------
    def append_older(self, older: list[tuple[Any, list[Any]]]) -> None:
        """Prepend an older page of history (used for lazy loading)."""
        if not older:
            return
        # remember current scroll position so the user doesn't jump
        sb = self.verticalScrollBar()
        old_val = sb.value()
        old_max = sb.maximum()

        # the page arrives oldest→newest; insert at successive positions so
        # it stacks above the existing content in chronological order
        position = 0
        for message, parts in older:
            for part in parts:
                if not hasattr(part, "data"):
                    continue
                part.data["_created_at"] = message.created_at
                if self._add_part(message.role, part.id, part.type, part.data,
                                  streaming=False, position=position):
                    position += 1

        # restore scroll so the same visual content stays on screen
        new_max = sb.maximum()
        sb.setValue(old_val + (new_max - old_max))

    def oldest_timestamp(self) -> float | None:
        """Return the created_at of the oldest visible message, or None."""
        if not self._part_widgets:
            return None
        # The first widget in the layout corresponds to the oldest message
        # because we prepend when loading older pages.
        # We store timestamps on the widgets via setProperty.
        w = self._layout.itemAt(0).widget()
        ts = w.property("_created_at")
        return float(ts) if ts is not None else None

    def show_thinking(self, agent: str, color: str = "#98c379") -> None:
        """Waiting-for-first-token indicator; dropped when content arrives."""
        self.dismiss_thinking()
        self._dismiss_welcome()
        self._thinking = _Thinking(agent, color)
        self._layout.addWidget(self._thinking)

    def dismiss_thinking(self) -> None:
        if self._thinking is not None:
            self._layout.removeWidget(self._thinking)
            self._thinking.deleteLater()
            self._thinking = None

    def promote_queued(self) -> None:
        """Clear [queued] style on the oldest optimistic user bubble."""
        for widget in self._part_widgets.values():
            if isinstance(widget, TextBlock) and widget._user and widget._queued:
                widget.set_queued(False)
                return

    def finalize_streaming(self) -> None:
        """Force markdown render on any in-flight text blocks (end of run)."""
        for widget in self._part_widgets.values():
            if isinstance(widget, TextBlock) and not widget._user:
                widget._finalize_markdown()

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
            self._add_part(role, part_id, part_type, data, streaming=True)
            return
        if isinstance(widget, ToolCard):
            widget.update_data(data)
        elif isinstance(widget, ReasoningCard):
            widget.update_text(data.get("text", ""))
        elif isinstance(widget, TextBlock):
            widget.update_text(data.get("text", ""), streaming=not widget._user)

    def add_user_text(self, part_id: str, text: str, queued: bool = False) -> None:
        self._add_part("user", part_id, "text", {"text": text, "_queued": queued}, streaming=False)

    def _add_part(
        self,
        role: str,
        part_id: str,
        part_type: str,
        data: dict[str, Any],
        streaming: bool = False,
        position: int | None = None,
    ) -> QWidget | None:
        self._dismiss_welcome()
        if role != "user":
            self.dismiss_thinking()
        widget: QWidget | None = None
        if part_type == "text":
            widget = TextBlock(
                data.get("text", ""),
                user=role == "user",
                queued=bool(data.get("_queued")),
            )
            if streaming and role != "user":
                widget.update_text(data.get("text", ""), streaming=True)
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
            return None
        self._part_widgets[part_id] = widget
        # stash the message timestamp so we can do lazy loading
        # (the widget is either a direct part widget or a container)
        try:
            widget.setProperty("_created_at", data.get("_created_at"))
        except Exception:
            pass
        if position is not None:
            self._layout.insertWidget(position, widget)
        else:
            self._layout.addWidget(widget)
        return widget

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
        # trigger lazy load when the user scrolls near the top
        if value <= 50 and callable(self.request_older):
            self.request_older()

    def _on_range(self, _min: int, _max: int) -> None:
        if self._autoscroll:
            self.verticalScrollBar().setValue(_max)
