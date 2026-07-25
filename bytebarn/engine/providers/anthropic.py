"""Anthropic Messages API adapter with native tool use + prompt caching."""

from __future__ import annotations

from typing import Any, AsyncIterator

import anthropic

from .base import (
    Done,
    ErrorEv,
    Event,
    ModelRequest,
    Msg,
    ReasoningDelta,
    RetryableProviderError,
    retry_after_from,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)


def _to_anthropic_messages(messages: list[Msg]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        content: list[dict[str, Any]] = []
        for item in msg.content:
            if item["type"] == "text":
                if item["text"]:
                    content.append({"type": "text", "text": item["text"]})
            elif item["type"] == "image":
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": item["media_type"],
                               "data": item["data"]},
                })
            elif item["type"] == "tool_call":
                content.append(
                    {"type": "tool_use", "id": item["id"], "name": item["name"], "input": item["input"]}
                )
            elif item["type"] == "tool_result":
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": item["call_id"],
                        "content": item["output"],
                        "is_error": item.get("is_error", False),
                    }
                )
        if content:
            out.append({"role": msg.role, "content": content})
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, client: Any = None):
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def stream(self, req: ModelRequest) -> AsyncIterator[Event]:
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in req.tools
        ]
        if tools:
            tools[-1]["cache_control"] = {"type": "ephemeral"}
        system: list[dict[str, Any]] = [
            {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
        ]
        kwargs: dict[str, Any] = dict(
            model=req.model_id,
            system=system,
            messages=_to_anthropic_messages(req.messages),
            max_tokens=req.max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.top_p is not None:
            kwargs["top_p"] = req.top_p
        if req.thinking and req.thinking != "off":
            from .base import THINKING_BUDGETS

            budget = THINKING_BUDGETS.get(req.thinking, 8192)
            # the API requires max_tokens > budget_tokens and no temperature
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["max_tokens"] = max(req.max_tokens, budget + 8192)
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)

        try:
            stream = await self._client.messages.create(stream=True, **kwargs)
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise RetryableProviderError(str(exc), retry_after_from(exc)) from exc
            yield ErrorEv(str(exc))
            return
        except anthropic.APIConnectionError as exc:
            raise RetryableProviderError(str(exc), retry_after_from(exc)) from exc

        block_types: dict[int, str] = {}
        block_call_ids: dict[int, str] = {}
        tokens_in = 0
        tokens_out = 0
        stop_reason = "end_turn"
        try:
            async for event in stream:
                etype = event.type
                if etype == "message_start":
                    usage = event.message.usage
                    tokens_in = (usage.input_tokens or 0) + (
                        getattr(usage, "cache_read_input_tokens", 0) or 0
                    ) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
                elif etype == "content_block_start":
                    block = event.content_block
                    block_types[event.index] = block.type
                    if block.type == "tool_use":
                        block_call_ids[event.index] = block.id
                        yield ToolCallStart(block.id, block.name)
                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield TextDelta(delta.text)
                    elif delta.type == "thinking_delta":
                        yield ReasoningDelta(delta.thinking)
                    elif delta.type == "input_json_delta":
                        yield ToolCallDelta(block_call_ids[event.index], delta.partial_json)
                elif etype == "content_block_stop":
                    if block_types.get(event.index) == "tool_use":
                        yield ToolCallEnd(block_call_ids[event.index])
                elif etype == "message_delta":
                    if event.delta.stop_reason:
                        stop_reason = event.delta.stop_reason
                    tokens_out = event.usage.output_tokens or tokens_out
        except anthropic.APIConnectionError as exc:
            raise RetryableProviderError(str(exc), retry_after_from(exc)) from exc
        yield Usage(tokens_in, tokens_out)
        yield Done(stop_reason)
