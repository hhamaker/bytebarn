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


def credentials_present() -> bool:
    """True if the standard AWS credential chain has something to offer."""
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return True
    return (Path.home() / ".aws" / "credentials").is_file() or (
        Path.home() / ".aws" / "config"
    ).is_file()


class BedrockProvider(AnthropicProvider):
    name = "bedrock"

    def __init__(self, region: str | None = None, client=None):
        import anthropic

        self._client = client or anthropic.AsyncAnthropicBedrock(
            aws_region=resolve_region(region)
        )


async def list_bedrock_models(region: str | None = None) -> list[str]:
    """Live model ids via boto3 (optional dependency); [] without it."""
    try:
        import boto3  # noqa: F401
    except ImportError:
        return []

    def _list() -> list[str]:
        import boto3

        client = boto3.client("bedrock", region_name=resolve_region(region))
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
