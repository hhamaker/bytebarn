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
    client_id: str | None = None, client_secret: str | None = None
) -> bool:
    """True if we have AWS credentials: explicit keys, env vars, or ~/.aws."""
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
        client=None,
    ):
        import anthropic

        # Map friendly names (client_id/client_secret) to AWS names
        access_key = aws_access_key or client_id
        secret_key = aws_secret_key or client_secret

        kwargs = {"aws_region": resolve_region(region)}
        if access_key:
            kwargs["aws_access_key"] = access_key
        if secret_key:
            kwargs["aws_secret_key"] = secret_key
        self._client = client or anthropic.AsyncAnthropicBedrock(**kwargs)


async def list_bedrock_models(
    region: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> list[str]:
    """Live model ids via boto3 (optional dependency); [] without it."""
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
        ids = []
        for model in response.get("modelSummaries", []):
            if "ON_DEMAND" not in model.get("inferenceTypesSupported", []):
                continue
            ids.append(model["modelId"])
        return sorted(set(ids))

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _list)
    except Exception:
        return []
