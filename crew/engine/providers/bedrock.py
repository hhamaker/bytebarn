"""AWS Bedrock provider — Claude models through Bedrock's SigV4 API.

Reuses the Anthropic adapter wholesale: ``AsyncAnthropicBedrock`` speaks the
same Messages API, differing only in auth (AWS credential chain: env vars,
~/.aws/credentials, SSO, instance roles) and model ids
("us.anthropic.claude-sonnet-4-5-...-v1:0" style).

boto3 is optional — only used to list available models live; inference
itself needs nothing beyond the anthropic SDK.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .anthropic import AnthropicProvider

DEFAULT_REGION = "us-east-1"


def resolve_region(config_region: str | None = None) -> str:
    return (
        config_region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    )


def credentials_present(
    client_id: str | None = None,
    client_secret: str | None = None,
    api_key: str | None = None,
) -> bool:
    """True if we have creds: Bedrock API key, explicit keys, env vars, ~/.aws."""
    if api_key or os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        return True
    if client_id and client_secret:
        return True
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return True
    return (Path.home() / ".aws" / "credentials").is_file() or (
        Path.home() / ".aws" / "config"
    ).is_file()


class BedrockProvider(AnthropicProvider):
    name = "bedrock"

    def __init__(
        self,
        region: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        aws_access_key: str | None = None,
        aws_secret_key: str | None = None,
        api_key: str | None = None,
        client=None,
    ):
        import anthropic

        # Map friendly names (client_id/client_secret) to AWS names
        access_key = aws_access_key or client_id
        secret_key = aws_secret_key or client_secret

        kwargs = {"aws_region": resolve_region(region)}
        # A Bedrock API key (bearer token) is mutually exclusive with SigV4
        # access/secret keys — the anthropic SDK raises if both are given.
        if api_key:
            kwargs["api_key"] = api_key
        else:
            if access_key:
                kwargs["aws_access_key"] = access_key
            if secret_key:
                kwargs["aws_secret_key"] = secret_key
        self._client = client or anthropic.AsyncAnthropicBedrock(**kwargs)
        # kept for the Converse path (non-Claude models via boto3)
        self._region = resolve_region(region)
        self._access_key = access_key
        self._secret_key = secret_key
        self._api_key = api_key
        self._converse_client = None  # injectable for tests

    # -- routing --------------------------------------------------------------

    async def stream(self, req):
        """Claude ids go through the Anthropic SDK; every other Bedrock model
        (Nova, Llama, Mistral, DeepSeek, …) speaks the Converse API."""
        if "anthropic" in req.model_id.lower():
            async for event in super().stream(req):
                yield event
            return
        async for event in self._converse_stream(req):
            yield event

    def _boto_client(self):
        if self._converse_client is not None:
            return self._converse_client
        import os

        import boto3

        if self._api_key and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            # boto3 supports Bedrock API keys via this env var
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self._api_key
        kwargs = {"region_name": self._region}
        if self._access_key and self._secret_key:
            kwargs["aws_access_key_id"] = self._access_key
            kwargs["aws_secret_access_key"] = self._secret_key
        return boto3.client("bedrock-runtime", **kwargs)

    async def _converse_stream(self, req):
        from .base import (
            Done,
            ErrorEv,
            ReasoningDelta,
            TextDelta,
            ToolCallDelta,
            ToolCallEnd,
            ToolCallStart,
            Usage,
        )

        try:
            client = self._boto_client()
        except ImportError:
            yield ErrorEv("install boto3 to run non-Claude Bedrock models")
            return
        except Exception as exc:
            yield ErrorEv(str(exc))
            return

        request = _to_converse(req)

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _END = object()

        def pump() -> None:
            try:
                response = client.converse_stream(**request)
                for chunk in response["stream"]:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:  # surfaced as a provider error event
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _END)

        task = loop.run_in_executor(None, pump)
        block_tools: dict[int, str] = {}   # contentBlockIndex -> call_id
        try:
            while True:
                chunk = await queue.get()
                if chunk is _END:
                    break
                if isinstance(chunk, Exception):
                    retry = "Throttling" in type(chunk).__name__ or \
                        "Throttling" in str(chunk)
                    yield ErrorEv(str(chunk), retryable=retry)
                    return
                if "contentBlockStart" in chunk:
                    start = chunk["contentBlockStart"]
                    tool = (start.get("start") or {}).get("toolUse")
                    if tool:
                        index = start.get("contentBlockIndex", 0)
                        block_tools[index] = tool["toolUseId"]
                        yield ToolCallStart(tool["toolUseId"], tool["name"])
                elif "contentBlockDelta" in chunk:
                    block = chunk["contentBlockDelta"]
                    delta = block.get("delta", {})
                    if "text" in delta:
                        yield TextDelta(delta["text"])
                    elif "reasoningContent" in delta:
                        text = delta["reasoningContent"].get("text", "")
                        if text:
                            yield ReasoningDelta(text)
                    elif "toolUse" in delta:
                        index = block.get("contentBlockIndex", 0)
                        call_id = block_tools.get(index)
                        if call_id:
                            yield ToolCallDelta(
                                call_id, delta["toolUse"].get("input", ""))
                elif "contentBlockStop" in chunk:
                    index = chunk["contentBlockStop"].get("contentBlockIndex", 0)
                    call_id = block_tools.pop(index, None)
                    if call_id:
                        yield ToolCallEnd(call_id)
                elif "metadata" in chunk:
                    usage = chunk["metadata"].get("usage", {})
                    yield Usage(usage.get("inputTokens", 0),
                                usage.get("outputTokens", 0))
                elif "messageStop" in chunk:
                    reason = chunk["messageStop"].get("stopReason", "end_turn")
                    yield Done({"tool_use": "tool_use",
                                "max_tokens": "max_tokens"}.get(reason, "end_turn"))
        finally:
            await task


def _to_converse(req) -> dict:
    """Map our provider-neutral ModelRequest onto a Converse API request."""
    messages = []
    for msg in req.messages:
        content = []
        for item in msg.content:
            kind = item.get("type")
            if kind == "text":
                content.append({"text": item.get("text", "")})
            elif kind == "tool_call":
                content.append({"toolUse": {
                    "toolUseId": item["id"], "name": item["name"],
                    "input": item.get("input") or {},
                }})
            elif kind == "tool_result":
                content.append({"toolResult": {
                    "toolUseId": item["call_id"],
                    "content": [{"text": str(item.get("output", ""))}],
                    "status": "error" if item.get("is_error") else "success",
                }})
        if content:
            messages.append({"role": msg.role, "content": content})

    request: dict = {
        "modelId": req.model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": req.max_tokens},
    }
    if req.system:
        request["system"] = [{"text": req.system}]
    if req.temperature is not None:
        request["inferenceConfig"]["temperature"] = req.temperature
    if getattr(req, "top_p", None) is not None:
        request["inferenceConfig"]["topP"] = req.top_p
    if req.tools:
        request["toolConfig"] = {"tools": [
            {"toolSpec": {
                "name": t.name, "description": t.description or t.name,
                "inputSchema": {"json": t.parameters},
            }} for t in req.tools
        ]}
    return request


def usable_bedrock_ids(
    model_summaries: list[dict], profile_summaries: list[dict]
) -> list[str]:
    """Every model id the caller's credentials can actually invoke.

    Two invocation paths exist on Bedrock:
    - legacy models support ON_DEMAND — the bare model id works
    - newer models are INFERENCE_PROFILE-only — invokable solely through a
      region-prefixed profile id (``us.…``), which the foundation-models
      list does *not* contain

    All vendors are included: Claude runs over the Anthropic SDK, everything
    else over the Converse API (see BedrockProvider.stream). Profiles are
    kept only when their underlying model is in the TEXT-modality catalog
    (drops image/embedding profiles) and the profile is ACTIVE."""
    text_ids = {m.get("modelId", "") for m in model_summaries}
    ids: set[str] = set()
    for model in model_summaries:
        if "ON_DEMAND" in model.get("inferenceTypesSupported", []):
            ids.add(model["modelId"])
    for profile in profile_summaries:
        pid = profile.get("inferenceProfileId", "")
        if profile.get("status", "ACTIVE") != "ACTIVE":
            continue
        base = pid.split(".", 1)[1] if "." in pid else pid
        if base in text_ids:
            ids.add(pid)
    return sorted(ids)


async def list_bedrock_models_via_api_key(
    api_key: str, region: str | None = None
) -> list[str]:
    """Live model ids from the Bedrock control-plane REST API using a Bedrock
    API key (bearer token) — no boto3 needed. [] on any failure."""
    import httpx

    base = f"https://bedrock.{resolve_region(region)}.amazonaws.com"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            models = await client.get(
                f"{base}/foundation-models",
                params={"byOutputModality": "TEXT"}, headers=headers)
            models.raise_for_status()
            profiles: list[dict] = []
            try:  # profiles are additive — a failure shouldn't kill the list
                resp = await client.get(
                    f"{base}/inference-profiles",
                    params={"typeEquals": "SYSTEM_DEFINED", "maxResults": 1000},
                    headers=headers)
                resp.raise_for_status()
                profiles = resp.json().get("inferenceProfileSummaries", [])
            except Exception:
                pass
            return usable_bedrock_ids(
                models.json().get("modelSummaries", []), profiles)
    except Exception:
        return []


async def list_bedrock_models(
    region: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    api_key: str | None = None,
) -> list[str]:
    """Live model ids: REST via API key when given, else boto3 (optional)."""
    if api_key:
        return await list_bedrock_models_via_api_key(api_key, region)
    try:
        import boto3  # noqa: F401
    except ImportError:
        return []

    def _list() -> list[str]:
        import boto3

        kwargs = {"region_name": resolve_region(region)}
        if client_id and client_secret:
            kwargs["aws_access_key_id"] = client_id
            kwargs["aws_secret_access_key"] = client_secret
        client = boto3.client("bedrock", **kwargs)
        response = client.list_foundation_models(byOutputModality="TEXT")
        profiles: list[dict] = []
        try:  # additive — current-gen Claude only appears here
            page = client.list_inference_profiles(
                typeEquals="SYSTEM_DEFINED", maxResults=1000)
            profiles = page.get("inferenceProfileSummaries", [])
            while page.get("nextToken"):
                page = client.list_inference_profiles(
                    typeEquals="SYSTEM_DEFINED", maxResults=1000,
                    nextToken=page["nextToken"])
                profiles += page.get("inferenceProfileSummaries", [])
        except Exception:
            pass
        return usable_bedrock_ids(response.get("modelSummaries", []), profiles)

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _list)
    except Exception:
        return []
