"""Provider adapter interface (spec §5.2)."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol


# -- stream events ----------------------------------------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallStart:
    call_id: str
    name: str


@dataclass
class ToolCallDelta:
    call_id: str
    json_fragment: str


@dataclass
class ToolCallEnd:
    call_id: str


@dataclass
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass
class Done:
    stop_reason: str = "end_turn"


@dataclass
class ErrorEv:
    message: str
    retryable: bool = False


Event = TextDelta | ReasoningDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | Usage | Done | ErrorEv


# -- request ----------------------------------------------------------------


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class Msg:
    """Provider-neutral message.

    content items:
      {"type": "text", "text": str}
      {"type": "tool_call", "id": str, "name": str, "input": dict}
      {"type": "tool_result", "call_id": str, "output": str, "is_error": bool}
    """

    role: str  # "user" | "assistant"
    content: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ModelRequest:
    model_id: str
    system: str
    messages: list[Msg]
    tools: list[ToolDef] = field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int = 8192


class Provider(Protocol):
    name: str

    def stream(self, req: ModelRequest) -> AsyncIterator[Event]: ...


class RetryableProviderError(Exception):
    pass


# -- retry wrapper ----------------------------------------------------------


async def stream_with_retry(
    provider: Provider,
    req: ModelRequest,
    on_retry: Callable[[int, float], None] | None = None,
    max_attempts: int = 4,
    base_delay: float = 1.0,
) -> AsyncIterator[Event]:
    """Retry the stream on retryable errors *before any event is yielded*.

    Once events have flowed, errors are surfaced as ErrorEv (a mid-stream
    retry would duplicate output).
    """
    attempt = 0
    while True:
        attempt += 1
        yielded = False
        try:
            async for event in provider.stream(req):
                yielded = True
                yield event
            return
        except RetryableProviderError as exc:
            if yielded or attempt >= max_attempts:
                yield ErrorEv(str(exc), retryable=True)
                return
            delay = base_delay * (2 ** (attempt - 1)) * (0.5 + random.random())
            if on_retry:
                on_retry(attempt, delay)
            await asyncio.sleep(delay)
        except Exception as exc:  # non-retryable
            yield ErrorEv(str(exc))
            return
