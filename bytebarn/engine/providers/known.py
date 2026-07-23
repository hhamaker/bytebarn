"""Known LLM providers: connection recipes + curated model lists.

This is the single source of truth for "what can ByteBarn talk to". Each entry
says how to reach the service (API kind + base URL), how it authenticates
(env var, stored API key, OAuth, or nothing for local servers), and which
models to offer in pickers. A provider the user adds by hand in config
still works even if it is not listed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth import AuthStore
    from ..config import Config


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    api: str = "openai"          # wire protocol: "anthropic" | "openai"
    base_url: str | None = None  # None = SDK default (anthropic/openai)
    key_env: str = ""            # conventional env var for the API key
    key_url: str = ""            # where humans create a key
    models: tuple[str, ...] = ()
    local: bool = False          # localhost server, no key needed
    oauth: bool = False          # supports an OAuth login flow
    oauth_kind: str = ""         # "loopback" (browser redirect) | "device" (enter a code)
    planned: bool = False        # listed but not connectable yet
    note: str = ""
    id_fields: tuple[str, ...] = ()


KNOWN_PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="anthropic", label="Anthropic (Claude)", api="anthropic",
            key_env="ANTHROPIC_API_KEY", key_url="https://console.anthropic.com/settings/keys",
            oauth=True, oauth_kind="paste",
            models=("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5",
                    "claude-fable-5"),
            note="Log in with a Claude Pro/Max account, or paste an API key.",
        ),
        ProviderSpec(
            id="openai", label="OpenAI",
            key_env="OPENAI_API_KEY", key_url="https://platform.openai.com/api-keys",
            models=("gpt-5.6", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-4.1"),
        ),
        ProviderSpec(
            id="xai", label="xAI (Grok)", base_url="https://api.x.ai/v1",
            key_env="XAI_API_KEY", key_url="https://console.x.ai",
            oauth=True, oauth_kind="device",
            models=("grok-4.5", "grok-4.3", "grok-4.1-fast", "grok-build-0.1"),
        ),
        ProviderSpec(
            id="groq", label="Groq", base_url="https://api.groq.com/openai/v1",
            key_env="GROQ_API_KEY", key_url="https://console.groq.com/keys",
            models=(
                "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                "openai/gpt-oss-120b", "openai/gpt-oss-20b",
            ),
        ),
        ProviderSpec(
            id="openrouter", label="OpenRouter", base_url="https://openrouter.ai/api/v1",
            key_env="OPENROUTER_API_KEY", key_url="https://openrouter.ai/keys",
            models=(
                "anthropic/claude-sonnet-4.5", "openai/gpt-4o",
                "google/gemini-2.5-pro", "deepseek/deepseek-chat-v3-0324",
            ),
            note="One key for many upstream models.",
        ),
        ProviderSpec(
            id="google", label="Google (Gemini)",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            key_env="GEMINI_API_KEY", key_url="https://aistudio.google.com/apikey",
            models=("gemini-3.5-flash", "gemini-3.1-pro-preview",
                    "gemini-2.5-pro", "gemini-2.5-flash"),
        ),
        ProviderSpec(
            id="mistral", label="Mistral", base_url="https://api.mistral.ai/v1",
            key_env="MISTRAL_API_KEY", key_url="https://console.mistral.ai/api-keys",
            models=("mistral-large-latest", "codestral-latest", "devstral-medium-latest"),
        ),
        ProviderSpec(
            id="deepseek", label="DeepSeek", base_url="https://api.deepseek.com/v1",
            key_env="DEEPSEEK_API_KEY", key_url="https://platform.deepseek.com/api_keys",
            models=("deepseek-v4-flash", "deepseek-v4-pro"),
            note="deepseek-chat/deepseek-reasoner aliases retire 2026-07-24.",
        ),
        ProviderSpec(
            id="together", label="Together AI", base_url="https://api.together.xyz/v1",
            key_env="TOGETHER_API_KEY", key_url="https://api.together.ai/settings/api-keys",
            models=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-Coder-32B-Instruct"),
        ),
        ProviderSpec(
            id="cerebras", label="Cerebras", base_url="https://api.cerebras.ai/v1",
            key_env="CEREBRAS_API_KEY", key_url="https://cloud.cerebras.ai",
            models=("llama-3.3-70b", "qwen-3-32b"),
        ),
        ProviderSpec(
            id="bedrock", label="AWS Bedrock", api="anthropic",
            key_env="",
            key_url="https://console.aws.amazon.com/iam/home#/security_credentials",
            models=(
                "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "us.anthropic.claude-opus-4-1-20250805-v1:0",
            ),
            note="Auth two ways: a Bedrock API key (bearer token), or AWS "
                 "credentials as Client ID (Access Key ID) + Client Secret "
                 "(Secret Access Key). Region from AWS_REGION (default "
                 "us-east-1).",
            id_fields=("client_id", "client_secret"),
        ),
        ProviderSpec(
            id="cloudflare", label="Cloudflare Workers AI",
            base_url="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1",
            key_env="CLOUDFLARE_API_KEY",
            key_url="https://dash.cloudflare.com/profile/api-tokens",
            models=(
                "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "@cf/qwen/qwen2.5-coder-32b-instruct",
                "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
            ),
            note="Paste your account id in the IDs field below (find it at"
                 " dash.cloudflare.com → Workers & Pages, right sidebar).",
        ),
        ProviderSpec(
            id="cloudflare-ai-gateway", label="Cloudflare AI Gateway",
            base_url="https://gateway.ai.cloudflare.com/v1/${CLOUDFLARE_ACCOUNT_ID}/${CLOUDFLARE_GATEWAY_ID}/compat",
            key_env="CLOUDFLARE_API_KEY",
            key_url="https://dash.cloudflare.com/profile/api-tokens",
            models=(
                "openai/gpt-4o", "anthropic/claude-sonnet-4-5",
                "google-ai-studio/gemini-2.5-flash",
            ),
            note="One gateway in front of many upstreams. Paste your account id"
                 " and gateway id in the IDs fields below.",
        ),
        ProviderSpec(
            id="ollama", label="Ollama (local)", base_url="http://localhost:11434/v1",
            local=True, models=(),
            note="No key needed; models come from `ollama pull`.",
        ),
        ProviderSpec(
            id="lmstudio", label="LM Studio (local)", base_url="http://localhost:1234/v1",
            local=True, models=(),
            note="No key needed; serve a model from the LM Studio UI.",
        ),
        ProviderSpec(
            id="github-copilot", label="GitHub Copilot", base_url="https://api.githubcopilot.com",
            oauth=True, oauth_kind="device",
            key_url="https://github.com/settings/copilot",
            models=("gpt-4o", "gpt-4.1", "claude-sonnet-4.5", "o3-mini"),
            note="Log in with your GitHub account — needs an active Copilot subscription.",
        ),
    )
}


def expand_env_vars(url: str | None) -> str | None:
    """Substitute ``${ENV_VAR}`` placeholders in a base URL (Cloudflare-style
    account/gateway ids). Unset vars are left as-is so callers can detect and
    report them."""
    if not url or "${" not in url:
        return url
    import os
    import re

    return re.sub(
        r"\$\{([A-Z0-9_]+)\}",
        lambda m: os.environ.get(m.group(1)) or m.group(0),
        url,
    )


def connection_status(spec: ProviderSpec, config: "Config", auth: "AuthStore") -> str:
    """One of: connected-env | connected-key | connected-oauth | local | planned | disconnected."""
    if spec.planned:
        return "planned"
    record = auth.get(spec.id)
    if spec.id == "bedrock":
        # explicit Access Key ID + Secret Access Key saved here, else the
        # ambient AWS chain (env vars / ~/.aws / SSO)
        from .bedrock import credentials_present

        if record and record.get("api_key"):
            return "connected-key"
        if record and record.get("client_id") and record.get("client_secret"):
            return "connected-key"
        return "connected-env" if credentials_present() else "disconnected"
    if record:
        if record.get("type") == "oauth":
            return "connected-oauth"
        if record.get("type") == "api" and record.get("key"):
            return "connected-key"
    pconf = config.provider.get(spec.id)
    if pconf and pconf.resolve_key():
        return "connected-env" if not pconf.api_key else "connected-key"
    if spec.local:
        return "local"
    import os

    if spec.key_env and os.environ.get(spec.key_env):
        return "connected-env"
    return "disconnected"


def is_connected(status: str) -> bool:
    return status.startswith("connected") or status == "local"


def connected_providers(config: "Config", auth: "AuthStore") -> list[str]:
    """Provider ids the user can reach right now (known + hand-configured)."""
    out = [
        spec.id for spec in KNOWN_PROVIDERS.values()
        if is_connected(connection_status(spec, config, auth))
    ]
    for name, pconf in config.provider.items():
        if name in out or name in KNOWN_PROVIDERS:
            continue
        if pconf.resolve_key() or pconf.base_url:
            out.append(name)
    return out


def curated_models(provider_id: str) -> list[str]:
    """The hand-picked model ids for a known provider (may be empty)."""
    spec = KNOWN_PROVIDERS.get(provider_id)
    return list(spec.models) if spec else []


def available_models(
    config: "Config",
    auth: "AuthStore",
    live: dict[str, list[str]] | None = None,
) -> list[str]:
    """"provider/model" strings for connected providers only.

    Connected known providers contribute their **live** model list when the
    engine has fetched one, otherwise the curated recipe. Hand-configured
    providers contribute whatever models config names for them. Config default
    models appear only when their provider is itself connected (no dead
    entries in the pickers).
    """
    models: list[str] = []
    connected: set[str] = set()
    live = live or {}
    for spec in KNOWN_PROVIDERS.values():
        if not is_connected(connection_status(spec, config, auth)):
            continue
        connected.add(spec.id)
        ids = live.get(spec.id) or list(spec.models)
        models.extend(f"{spec.id}/{m}" for m in ids)
    # hand-configured providers outside the known list: connected if they
    # resolve a key or point at a custom endpoint
    extra_catalog = (config.model_extra or {}).get("catalog") or {}
    for name, pconf in config.provider.items():
        if name in connected or name in KNOWN_PROVIDERS:
            continue
        if pconf.resolve_key() or pconf.base_url:
            connected.add(name)
            ids = live.get(name) or list(extra_catalog)
            models.extend(f"{name}/{model_id}" for model_id in ids)
    # also surface live lists for connected known providers that somehow
    # only appear via live cache (defensive; normally covered above)
    for name, ids in live.items():
        if name in connected:
            continue
        if name in KNOWN_PROVIDERS or name in config.provider:
            connected.add(name)
            models.extend(f"{name}/{m}" for m in ids)
    # config defaults: only when their provider is connected
    for m in (config.small_model, config.model):
        if m and "/" in m and m.split("/", 1)[0] in connected and m not in models:
            models.insert(0, m)
    return list(dict.fromkeys(models))
