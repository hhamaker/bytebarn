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


def usable_bedrock_ids(
    model_summaries: list[dict], profile_summaries: list[dict]
) -> list[str]:
    """Every model id this provider can actually invoke.

    Two invocation paths exist on Bedrock:
    - legacy models support ON_DEMAND — the bare ``anthropic.…`` id works
    - current-generation Claude is INFERENCE_PROFILE-only — invokable solely
      through a region-prefixed profile id (``us.anthropic.…``), which the
      foundation-models list does *not* contain

    Filtering foundation models to ON_DEMAND (the old behavior) therefore
    silently dropped every current Claude model. Non-Anthropic entries are
    excluded — this provider speaks the Anthropic wire protocol only."""
    ids: set[str] = set()
    for model in model_summaries:
        if "anthropic" not in model.get("modelId", "").lower():
            continue
        if "ON_DEMAND" in model.get("inferenceTypesSupported", []):
            ids.add(model["modelId"])
    for profile in profile_summaries:
        pid = profile.get("inferenceProfileId", "")
        if "anthropic" not in pid.lower():
            continue
        if profile.get("status", "ACTIVE") == "ACTIVE":
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
