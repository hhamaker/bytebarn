"""The crew stage: live pixel-art view of the working crew (spec §7.2).

State derives purely from engine events (task.*, todo.updated, run.finished).
``StageState`` is Qt-free-logic + dataclasses so it unit-tests headless;
``CrewStage`` renders it with QPainter at ~12 fps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .sprites import SPRITE_H, SPRITE_W, draw_critter, species_for

MAX_VISIBLE = 8


@dataclass
class CrewMember:
    session_id: str
    agent: str
    description: str
    status: str = "running"   # running | retrying | done | error
    detail: str = ""
    color: str = "#98c379"


@dataclass
class StageState:
    members: dict[str, CrewMember] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)  # pending todo contents
    active: bool = False

    def on_event(self, event: Any, agent_colors: dict[str, str] | None = None) -> bool:
        """Apply an engine event; returns True if the stage changed."""
        name = getattr(event, "name", "")
        if name == "task.started":
            color = (agent_colors or {}).get(event.agent, "#98c379")
            member = CrewMember(
                session_id=event.subagent_session_id,
                agent=event.agent,
                description=event.description,
                color=color or "#98c379",
            )
            if member.session_id not in self.members:
                self.order.append(member.session_id)
            self.members[member.session_id] = member
            self.active = True
            return True
        if name == "task.updated":
            member = self.members.get(event.subagent_session_id)
            if member:
                if event.status:
                    member.status = event.status
                if event.detail:
                    member.detail = event.detail
                return True
            return False
        if name == "task.finished":
            member = self.members.get(event.subagent_session_id)
            if member:
                member.status = "done" if event.status == "done" else "error"
                member.detail = ""
                return True
            return False
        if name == "todo.updated":
            self.waiting = [t["content"] for t in event.todos if t["status"] == "pending"]
            return True
        if name == "run.finished":
            # orchestrator run over -> stage hides and resets
            self.members.clear()
            self.order.clear()
            self.waiting.clear()
            self.active = False
            return True
        return False

    def visible_members(self) -> list[CrewMember]:
        return [self.members[sid] for sid in self.order if sid in self.members]

    @property
    def overflow(self) -> int:
        return max(0, len(self.order) - MAX_VISIBLE)


class CrewStage(QWidget):
    open_session = Signal(str)

    def __init__(self):
        super().__init__()
        self.state = StageState()
        self.orchestrator_color = "#e5c07b"
        self._frame = 0
        self._hits: list[tuple[QRect, str]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(80)  # ~12 fps
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(150)
        self.setVisible(False)

    # -- state ----------------------------------------------------------------

    def handle_event(self, event: Any, agent_colors: dict[str, str] | None = None) -> None:
        changed = self.state.on_event(event, agent_colors)
        should_show = self.state.active and bool(self.state.members)
        if should_show and not self.isVisible():
            self.setVisible(True)
            self._timer.start()
        elif not should_show and self.isVisible():
            self._timer.stop()
            self.setVisible(False)
        if changed:
            self.update()

    def _tick(self) -> None:
        self._frame += 1
        self.update()

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1d2026"))
        self._hits = []

        members = self.state.visible_members()
        shown = members[:MAX_VISIBLE]
        scale = 4
        sprite_w = SPRITE_W * scale
        sprite_h = SPRITE_H * scale
        width = self.width()

        # hub (orchestrator) on the left
        hub_x = 30
        hub_y = 34
        n = len(shown)
        slot = max((width - 140) // max(n, 1), sprite_w + 40) if n else 0

        metrics = QFontMetrics(painter.font())
        for index, member in enumerate(shown):
            cx = 120 + index * slot
            cy = 26 + (10 if index % 2 else 0)
            self._draw_rope(painter, hub_x + sprite_w // 2, hub_y + sprite_h // 2,
                            cx + sprite_w // 2, cy + sprite_h // 2, member)
        # ropes under critters: draw hub + critters after
        draw_critter(painter, hub_x, hub_y, scale, species_for("orchestrator"),
                     QColor(self.orchestrator_color), state="working",
                     frame=self._frame, crowned=True)
        for index, member in enumerate(shown):
            cx = 120 + index * slot
            cy = 26 + (10 if index % 2 else 0)
            state = {"running": "working", "retrying": "retrying",
                     "done": "done", "error": "retrying"}.get(member.status, "working")
            draw_critter(painter, cx, cy, scale, species_for(member.agent),
                         QColor(member.color), state=state, frame=self._frame + index * 5)
            rect = QRect(cx - 4, cy - 4, sprite_w + 8, sprite_h + 8)
            self._hits.append((rect, member.session_id))
            painter.setPen(QColor("#c8ccd4"))
            name = metrics.elidedText(member.agent, Qt.ElideRight, slot - 12)
            painter.drawText(cx + sprite_w // 2 - metrics.horizontalAdvance(name) // 2,
                             cy + sprite_h + 14, name)
            if member.detail:
                painter.setPen(QColor("#7f848e"))
                detail = metrics.elidedText(member.detail, Qt.ElideRight, slot - 8)
                painter.drawText(cx + sprite_w // 2 - metrics.horizontalAdvance(detail) // 2,
                                 cy + sprite_h + 28, detail)

        if self.state.overflow:
            painter.setPen(QColor("#c8ccd4"))
            painter.drawText(width - 80, 30, f"+{self.state.overflow} more")

        # waiting rows: faded sleeping critters for undelegated todos
        wait_y = 96
        for windex, content in enumerate(self.state.waiting[:6]):
            wx = 120 + windex * 140
            if wx + sprite_w > width:
                break
            draw_critter(painter, wx, wait_y, 3, species_for(content),
                         QColor("#888888"), state="waiting", frame=self._frame + windex * 7)
            painter.setPen(QColor("#666b74"))
            text = metrics.elidedText(content, Qt.ElideRight, 130)
            painter.drawText(wx + 40, wait_y + 22, text)
        painter.end()

    def _draw_rope(self, painter: QPainter, x1: int, y1: int, x2: int, y2: int, member: CrewMember) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.moveTo(x1, y1)
        mid_x = (x1 + x2) / 2
        sag = 24
        path.quadTo(mid_x, max(y1, y2) + sag, x2, y2)
        painter.setPen(QColor(90, 95, 108))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        if member.status in ("running", "retrying"):
            # pulse dot travels hub -> critter
            t = (self._frame % 24) / 24
            # quadratic bezier point
            qx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * mid_x + t**2 * x2
            qy = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * (max(y1, y2) + sag) + t**2 * y2
            painter.setBrush(QColor(member.color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(int(qx) - 3, int(qy) - 3, 6, 6)
        painter.setRenderHint(QPainter.Antialiasing, False)

    # -- interaction --------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        for rect, session_id in self._hits:
            if rect.contains(pos):
                self.open_session.emit(session_id)
                return
