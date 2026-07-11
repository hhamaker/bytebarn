"""Engine -> UI event stream (spec §5.6).

The UI is a pure projection of these events plus the DB.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class EngineEvent:
    name: str = ""


@dataclass
class SessionUpdated(EngineEvent):
    session_id: str = ""
    name: str = "session.updated"


@dataclass
class PartUpdated(EngineEvent):
    session_id: str = ""
    message_id: str = ""
    part_id: str = ""
    part_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    delta: str = ""  # incremental text for streaming
    name: str = "message.part.updated"


@dataclass
class TodoUpdated(EngineEvent):
    session_id: str = ""
    todos: list[dict[str, str]] = field(default_factory=list)
    name: str = "todo.updated"


@dataclass
class PermissionAsked(EngineEvent):
    session_id: str = ""
    request_id: str = ""
    tool: str = ""
    arg: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    name: str = "permission.asked"


@dataclass
class QuestionAsked(EngineEvent):
    session_id: str = ""
    request_id: str = ""
    question: str = ""
    options: list[str] = field(default_factory=list)
    name: str = "question.asked"


@dataclass
class TaskStarted(EngineEvent):
    session_id: str = ""          # parent
    subagent_session_id: str = ""
    agent: str = ""
    description: str = ""
    name: str = "task.started"


@dataclass
class TaskUpdated(EngineEvent):
    session_id: str = ""
    subagent_session_id: str = ""
    status: str = ""              # running | retrying | done | error
    detail: str = ""              # live current-tool detail for the crew stage
    name: str = "task.updated"


@dataclass
class TaskFinished(EngineEvent):
    session_id: str = ""
    subagent_session_id: str = ""
    status: str = "done"
    name: str = "task.finished"


@dataclass
class RunFinished(EngineEvent):
    session_id: str = ""
    status: str = "done"          # done | aborted | error
    name: str = "run.finished"


@dataclass
class AgentRegistryChanged(EngineEvent):
    name: str = "agent.registry.changed"


@dataclass
class QueueUpdated(EngineEvent):
    session_id: str = ""
    depth: int = 0
    name: str = "queue.updated"


class EventBus:
    """Fan-out async event bus; each subscriber gets every event."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def emit(self, event: EngineEvent) -> None:
        for q in self._queues:
            q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[EngineEvent]:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._queues.remove(q)

    def queue(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)
