"""The crew stage: live pixel-art view of the working crew (spec §7.2).

State derives purely from engine events (task.*, todo.updated, run.finished).
``StageState`` is Qt-free-logic + dataclasses so it unit-tests headless;
``CrewStage`` renders it with QPainter at ~12 fps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .sprites import SPRITE_H, SPRITE_W, draw_critter, look_for, species_for

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
    waiting: list[str] = field(default_factory=list)   # pending todo contents
    current_todo: str = ""                             # in-progress todo content
    active: bool = False
    started_at: float = 0.0

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
            self._activate()
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
            self.current_todo = next(
                (t["content"] for t in event.todos if t["status"] == "in_progress"), "")
            # a plan exists -> show the stage while the orchestrator is still
            # casting the crew (sleeping critters + summary, no workers yet)
            if event.todos:
                self._activate()
            return True
        if name == "run.finished":
            # orchestrator run over -> stage hides and resets
            self.members.clear()
            self.order.clear()
            self.waiting.clear()
            self.current_todo = ""
            self.active = False
            self.started_at = 0.0
            return True
        return False

    def _activate(self) -> None:
        if not self.active:
            self.started_at = time.time()
        self.active = True

    def visible_members(self) -> list[CrewMember]:
        return [self.members[sid] for sid in self.order if sid in self.members]

    def summary(self) -> str:
        """One-line status headline for the stage."""
        counts: dict[str, int] = {}
        for member in self.members.values():
            counts[member.status] = counts.get(member.status, 0) + 1
        bits = []
        if counts.get("running"):
            bits.append(f"{counts['running']} working")
        if counts.get("retrying"):
            bits.append(f"{counts['retrying']} retrying")
        if counts.get("done"):
            bits.append(f"{counts['done']} done")
        if counts.get("error"):
            bits.append(f"{counts['error']} failed")
        if self.waiting:
            bits.append(f"{len(self.waiting)} queued")
        if not bits:
            bits.append("planning…")
        if self.started_at:
            elapsed = int(time.time() - self.started_at)
            bits.append(f"{elapsed // 60}:{elapsed % 60:02d}")
        return " · ".join(bits)

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
        self.setFixedHeight(164)
        self.setVisible(False)

    # -- state ----------------------------------------------------------------

    def handle_event(self, event: Any, agent_colors: dict[str, str] | None = None) -> None:
        changed = self.state.on_event(event, agent_colors)
        # visible from the moment a plan exists (planning phase shows the
        # summary + sleeping critters before any worker is cast)
        should_show = self.state.active
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
        metrics_header = QFontMetrics(painter.font())

        # headline: live counts + elapsed, then the todo being worked on
        painter.setPen(QColor("#e5c07b"))
        summary = self.state.summary()
        painter.drawText(12, 16, summary)
        if self.state.current_todo:
            painter.setPen(QColor("#8f96a3"))
            x = 12 + metrics_header.horizontalAdvance(summary) + 16
            todo = metrics_header.elidedText(
                f"▸ {self.state.current_todo}", Qt.ElideRight, self.width() - x - 12)
            painter.drawText(x, 16, todo)

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
        # cap slot width so a small crew clusters near the hub instead of
        # scattering across the full window
        slot = min(max((width - 140) // max(n, 1), sprite_w + 40), 170) if n else 0

        metrics = QFontMetrics(painter.font())
        for index, member in enumerate(shown):
            cx = 120 + index * slot
            cy = 26 + (10 if index % 2 else 0)
            self._draw_rope(painter, hub_x + sprite_w // 2, hub_y + sprite_h // 2,
                            cx + sprite_w // 2, cy + sprite_h // 2, member)
        # ropes under critters: draw hub + critters after
        hub_species, hub_accent = look_for("orchestrator")
        draw_critter(painter, hub_x, hub_y, scale, hub_species,
                     QColor(self.orchestrator_color), state="working",
                     frame=self._frame, crowned=True, accent=hub_accent)
        dots = "." * (1 + (self._frame // 4) % 3)
        for index, member in enumerate(shown):
            cx = 120 + index * slot
            cy = 26 + (10 if index % 2 else 0)
            state = {"running": "working", "retrying": "retrying",
                     "done": "done", "error": "retrying"}.get(member.status, "working")
            species, accent = look_for(member.agent)
            draw_critter(painter, cx, cy, scale, species,
                         QColor(member.color), state=state,
                         frame=self._frame + index * 5, accent=accent)
            self._draw_badge(painter, cx + sprite_w - 6, cy + sprite_h - 6, member.status)
            rect = QRect(cx - 4, cy - 4, sprite_w + 8, sprite_h + 8)
            self._hits.append((rect, member.session_id))
            painter.setPen(QColor("#c8ccd4"))
            name = metrics.elidedText(member.agent, Qt.ElideRight, slot - 12)
            painter.drawText(cx + sprite_w // 2 - metrics.horizontalAdvance(name) // 2,
                             cy + sprite_h + 14, name)
            # status line: unmistakable working/done wording, live detail when we have it
            status_text, status_color = {
                "running": (member.detail or f"working{dots}", "#e5c07b"),
                "retrying": (f"retrying{dots}", "#e06c75"),
                "done": ("✓ done", "#98c379"),
                "error": ("✗ failed", "#e06c75"),
            }.get(member.status, ("working" + dots, "#e5c07b"))
            if member.status == "running" and member.detail:
                status_color = "#7f848e"
            painter.setPen(QColor(status_color))
            status = metrics.elidedText(status_text, Qt.ElideRight, slot - 8)
            painter.drawText(cx + sprite_w // 2 - metrics.horizontalAdvance(status) // 2,
                             cy + sprite_h + 28, status)

        if self.state.overflow:
            painter.setPen(QColor("#c8ccd4"))
            painter.drawText(width - 80, 16, f"+{self.state.overflow} more")

        # waiting rows: faded sleeping critters for undelegated todos
        wait_y = 118
        for windex, content in enumerate(self.state.waiting[:6]):
            wx = 120 + windex * 200
            if wx + sprite_w > width:
                break
            draw_critter(painter, wx, wait_y, 3, species_for(content),
                         QColor("#888888"), state="waiting", frame=self._frame + windex * 7)
            painter.setPen(QColor("#666b74"))
            text = metrics.elidedText(content, Qt.ElideRight, 148)
            painter.drawText(wx + 44, wait_y + 22, text)
        painter.end()

    def _draw_badge(self, painter: QPainter, x: int, y: int, status: str) -> None:
        """Corner badge on the sprite: green ✓ when done, red ✗ on error."""
        if status not in ("done", "error"):
            return
        color = QColor("#98c379") if status == "done" else QColor("#e06c75")
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(color)
        painter.setPen(QColor("#1d2026"))
        painter.drawEllipse(x - 8, y - 8, 16, 16)
        painter.setPen(QColor("#1d2026"))
        font = painter.font()
        bold = QFont(font)
        bold.setBold(True)
        painter.setFont(bold)
        glyph = "✓" if status == "done" else "✗"
        painter.drawText(QRect(x - 8, y - 9, 16, 16), Qt.AlignCenter, glyph)
        painter.setFont(font)
        painter.setBrush(Qt.NoBrush)
        painter.setRenderHint(QPainter.Antialiasing, False)

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
