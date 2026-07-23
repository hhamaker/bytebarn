"""Scripted provider for tests and offline demos."""

from __future__ import annotations

import json
from typing import AsyncIterator, Callable

from .base import Done, Event, ModelRequest, TextDelta, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage


def text_turn(text: str) -> list[Event]:
    return [TextDelta(text), Usage(10, 5), Done("end_turn")]


def tool_turn(call_id: str, name: str, input: dict, text: str = "") -> list[Event]:
    events: list[Event] = []
    if text:
        events.append(TextDelta(text))
    events += [
        ToolCallStart(call_id, name),
        ToolCallDelta(call_id, json.dumps(input)),
        ToolCallEnd(call_id),
        Usage(10, 5),
        Done("tool_use"),
    ]
    return events


class FakeProvider:
    """Yields pre-scripted event lists, one list per stream() call.

    ``script`` may be a list of turns, or a callable(req) -> list[Event]
    for request-dependent behavior.
    """

    name = "fake"

    def __init__(self, script: list[list[Event]] | Callable[[ModelRequest], list[Event]]):
        self._script = script
        self._i = 0
        self.requests: list[ModelRequest] = []

    async def stream(self, req: ModelRequest) -> AsyncIterator[Event]:
        self.requests.append(req)
        if callable(self._script):
            events = self._script(req)
        else:
            events = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1
        for event in events:
            yield event
