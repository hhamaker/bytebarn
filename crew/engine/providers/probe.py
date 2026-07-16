"""Connection test for providers: one cheap authenticated request, no Qt."""

from __future__ import annotations

import httpx

from ..auth import AuthStore
from ..config import Config
from .known import KNOWN_PROVIDERS, expand_env_vars

ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1"
OPENAI_DEFAULT = "https://api.openai.com/v1"


def _endpoint_and_headers(
    name: str, config: Config, auth: AuthStore
) -> tuple[str, dict[str, str]]:
    spec = KNOWN_PROVIDERS.get(name)
    pconf = config.provider.get(name)
    api = pconf.api if pconf else (spec.api if spec else "openai")
    base_url = expand_env_vars(
        (pconf.base_url if pconf else None) or (spec.base_url if spec else None))

    key = pconf.resolve_key() if pconf else None
    record = auth.get(name)
    if key is None and record:
        if record.get("type") == "api":
            key = record.get("key")
        elif record.get("type") == "oauth":
            key = record.get("access")
    if key is None and spec and spec.key_env:
        import os

        key = os.environ.get(spec.key_env)

    if api == "anthropic" and not base_url:
        headers = {"anthropic-version": "2023-06-01"}
        if record and record.get("type") == "oauth" and key:
            from .anthropic_oauth import OAUTH_BETA_HEADER

            headers["Authorization"] = f"Bearer {key}"
            headers["anthropic-beta"] = OAUTH_BETA_HEADER
        elif key:
            headers["x-api-key"] = key
        return f"{ANTHROPIC_DEFAULT}/models", headers
    base = (base_url or OPENAI_DEFAULT).rstrip("/")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if name == "github-copilot":
        from .github_copilot_oauth import COPILOT_HEADERS

        headers.update(COPILOT_HEADERS)
    # Cloudflare's OpenAI-compat endpoint has no GET /models (405); its
    # native models listing lives next door under /ai/models/search
    if name == "cloudflare" and "/ai/v1" in base:
        return base.replace("/ai/v1", "/ai/models/search"), headers
    return f"{base}/models", headers


async def probe_provider(name: str, config: Config, auth: AuthStore) -> tuple[bool, str]:
    """Hit the provider's /models endpoint; returns (ok, human message)."""
    spec = KNOWN_PROVIDERS.get(name)
    if spec and spec.planned:
        return False, f"{spec.label} is not connectable yet ({spec.note})"
    if name == "bedrock":
        from .bedrock import credentials_present, list_bedrock_models, resolve_region

        rec = auth.get("bedrock") or {}
        cid, csec = rec.get("client_id"), rec.get("client_secret")
        akey = rec.get("api_key")
        region = rec.get("region")
        if not credentials_present(cid, csec, akey):
            return False, ("no Bedrock credentials — enter a Bedrock API key,"
                           " or AWS Access Key ID + Secret Access Key, or"
                           " configure ~/.aws")
        live = await list_bedrock_models(region, cid, csec, akey)
        if live:
            return True, f"connected — {len(live)} models in {resolve_region(region)}"
        hint = ("check the API key / region" if akey
                else "install boto3 for live model listing")
        return True, (f"credentials saved (region {resolve_region(region)}); {hint}")
    url, headers = _endpoint_and_headers(name, config, auth)
    if "${" in url:
        missing = url[url.index("${") + 2 : url.index("}")]
        return False, f"set the {missing} environment variable, or edit the base URL"
    if "Authorization" not in headers and "x-api-key" not in headers and not (spec and spec.local):
        return False, "no API key found (set one here, or export the env var)"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        hint = " — is the server running?" if spec and spec.local else ""
        return False, f"network error: {exc}{hint}"
    if response.status_code == 200:
        try:
            data = response.json()
            items = data.get("data") or data.get("models") or data.get("result") or []
            count = len(items) if isinstance(items, list) else 0
            return True, f"connected — {count} models visible"
        except ValueError:
            return True, "connected"
    if response.status_code in (401, 403):
        return False, f"auth failed (HTTP {response.status_code}) — check the key"
    if response.status_code == 405:
        # endpoint exists but has no model listing (route matched, wrong
        # method) — the chat endpoint itself will still work
        return True, "connected — endpoint reachable (no model listing)"
    return False, f"HTTP {response.status_code}: {response.text[:120]}"


import re

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _parse_model_ids(data) -> list[str]:
    items = data.get("data") or data.get("models") or data.get("result") or []
    ids: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            # Cloudflare tags each model with a task — only chat-capable
            # models belong in the picker (skip whisper/embeddings/images)
            task = item.get("task")
            if isinstance(task, dict):
                task_name = str(task.get("name", ""))
                if task_name and "text generation" not in task_name.lower():
                    continue
            model_id = item.get("id")
            name = item.get("name")
            # Cloudflare-style listings: "id" is an opaque UUID, "name" is the
            # callable model path (@cf/...) — the name is what users need
            if isinstance(model_id, str) and _UUID_RE.match(model_id):
                model_id = None
            chosen = model_id or name
            if isinstance(chosen, str):
                ids.append(chosen)
    return ids


async def fetch_models(name: str, config: Config, auth: AuthStore) -> list[str]:
    """Live model ids from the provider's listing endpoint ([] on any failure).

    This is what makes the pickers show *everything* a provider offers —
    including whatever an Ollama/LM Studio server currently has loaded —
    rather than only the curated list.
    """
    spec = KNOWN_PROVIDERS.get(name)
    if spec and spec.planned:
        return []
    if name == "bedrock":
        from .bedrock import list_bedrock_models

        rec = auth.get("bedrock") or {}
        return await list_bedrock_models(
            rec.get("region"), rec.get("client_id"), rec.get("client_secret"),
            rec.get("api_key"))
    url, headers = _endpoint_and_headers(name, config, auth)
    if "${" in url:
        return []
    if "Authorization" not in headers and "x-api-key" not in headers and not (spec and spec.local):
        return []
    ids: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if "models/search" in url:
                # Cloudflare paginates (~50/page) — walk all pages, else
                # anything past page 1 (GLM, newer models) never shows up
                for page in range(1, 11):
                    response = await client.get(
                        url, headers=headers,
                        params={"per_page": 100, "page": page},
                    )
                    if response.status_code != 200:
                        break
                    data = response.json()
                    items = data.get("result") or []
                    ids.extend(_parse_model_ids(data))
                    if len(items) < 100:
                        break
            else:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    ids.extend(_parse_model_ids(response.json()))
    except (httpx.HTTPError, ValueError):
        return sorted(set(ids))
    return sorted(set(ids))
