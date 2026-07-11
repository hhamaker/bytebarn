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


def _parse_model_ids(data) -> list[str]:
    items = data.get("data") or data.get("models") or data.get("result") or []
    ids: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str):
                ids.append(model_id)
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
    url, headers = _endpoint_and_headers(name, config, auth)
    if "${" in url:
        return []
    if "Authorization" not in headers and "x-api-key" not in headers and not (spec and spec.local):
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    try:
        return sorted(set(_parse_model_ids(response.json())))
    except ValueError:
        return []
