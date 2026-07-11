"""OpenAI-compatible chat-completions adapter.

Covers OpenAI, LM Studio, Ollama, OpenRouter, Groq, ... via base_url.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import openai

from .base import (
    Done,
    ErrorEv,
    Event,
    ModelRequest,
    Msg,
    RetryableProviderError,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)


def _to_openai_messages(system: str, messages: list[Msg]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.role == "assistant":
            text_parts = [i["text"] for i in msg.content if i["type"] == "text" and i["text"]]
            tool_calls = [
                {
                    "id": i["id"],
                    "type": "function",
                    "function": {"name": i["name"], "arguments": json.dumps(i["input"])},
                }
                for i in msg.content
                if i["type"] == "tool_call"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:
            for item in msg.content:
                if item["type"] == "tool_result":
                    out.append(
                        {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
                    )
            text = "\n".join(i["text"] for i in msg.content if i["type"] == "text" and i["text"])
            if text:
                out.append({"role": "user", "content": text})
    return out


class OpenAICompatProvider:
    def __init__(
        self,
        name: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
        headers: dict[str, str] | None = None,
    ):
        self.name = name
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key or "not-needed", base_url=base_url, default_headers=headers
        )

    async def stream(self, req: ModelRequest) -> AsyncIterator[Event]:
        kwargs: dict[str, Any] = dict(
            model=req.model_id,
            messages=_to_openai_messages(req.system, req.messages),
            max_tokens=req.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if req.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in req.tools
            ]
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.top_p is not None:
            kwargs["top_p"] = req.top_p

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise RetryableProviderError(str(exc)) from exc
            yield ErrorEv(str(exc))
            return
        except openai.APIConnectionError as exc:
            raise RetryableProviderError(str(exc)) from exc

        tokens_in = 0
        tokens_out = 0
        stop_reason = "end_turn"
        open_calls: dict[int, str] = {}  # index -> call_id
        try:
            async for chunk in stream:
                if chunk.usage:
                    tokens_in = chunk.usage.prompt_tokens or 0
                    tokens_out = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    yield TextDelta(delta.content)
                if delta and getattr(delta, "reasoning_content", None):
                    from .base import ReasoningDelta

                    yield ReasoningDelta(delta.reasoning_content)
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index or 0
                        if tc.id and idx not in open_calls:
                            open_calls[idx] = tc.id
                            yield ToolCallStart(tc.id, tc.function.name if tc.function else "")
                        if tc.function and tc.function.arguments and idx in open_calls:
                            yield ToolCallDelta(open_calls[idx], tc.function.arguments)
                if choice.finish_reason:
                    stop_reason = (
                        "tool_use" if choice.finish_reason == "tool_calls" else choice.finish_reason
                    )
        except openai.APIConnectionError as exc:
            raise RetryableProviderError(str(exc)) from exc
        for call_id in open_calls.values():
            yield ToolCallEnd(call_id)
        yield Usage(tokens_in, tokens_out)
        yield Done(stop_reason)
