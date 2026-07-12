"""Model string resolution: "provider/model-id" -> (Provider, model_id, ModelInfo)."""

from __future__ import annotations

from pathlib import Path

from ..auth import AuthStore
from ..config import Config
from .base import Provider
from .catalog import ModelInfo, catalog_from_config, model_info


class ProviderRegistry:
    def __init__(self, config: Config, global_dir: Path | None = None):
        self.config = config
        self.auth = AuthStore(global_dir)
        self._providers: dict[str, Provider] = {}
        self.extra_catalog = catalog_from_config(
            getattr(config, "catalog", None) or (config.model_extra or {}).get("catalog")
        )

    def register(self, name: str, provider: Provider) -> None:
        """Inject a pre-built provider (tests, fake)."""
        self._providers[name] = provider

    def provider(self, name: str) -> Provider:
        if name in self._providers:
            return self._providers[name]
        # providers with their own auth/transport get a dedicated factory —
        # adding one is a single entry here, not another if/elif branch
        factory = _SPECIAL_FACTORIES.get(name)
        if factory is not None:
            prov = factory(self)
            if prov is not None:
                self._providers[name] = prov
                return prov
        pconf = self.config.provider.get(name)
        if pconf is None:
            # not configured by hand: fall back to the known-provider recipe
            from ..config import ProviderConfig
            from .known import KNOWN_PROVIDERS

            spec = KNOWN_PROVIDERS.get(name)
            if spec is None or spec.planned:
                raise KeyError(f"unknown provider '{name}' (add it to config.provider)")
            pconf = ProviderConfig(
                api_key_env=spec.key_env or None, base_url=spec.base_url, api=spec.api
            )

        auth_record = self.auth.get(name)
        # OAuth-authenticated providers: if the user has logged in (a token
        # exists in the auth store) and no explicit api key is configured,
        # drive the provider off the stored bearer token.
        if (
            auth_record
            and auth_record.get("type") == "oauth"
            and pconf.resolve_key() is None
        ):
            if name == "github-copilot":
                from .github_copilot_oauth import API_BASE, COPILOT_HEADERS
                from .openai_compat import OpenAICompatProvider

                prov: Provider = OpenAICompatProvider(
                    name=name,
                    api_key=auth_record.get("refresh") or auth_record.get("access"),
                    base_url=pconf.base_url or API_BASE,
                    headers=dict(COPILOT_HEADERS),
                )
            elif name == "anthropic":
                from .anthropic_oauth import AnthropicOAuthProvider

                prov = AnthropicOAuthProvider(self.auth, name=name, base_url=pconf.base_url)
            else:
                from .xai import XaiOAuthProvider

                prov = XaiOAuthProvider(self.auth, name=name, base_url=pconf.base_url)
            self._providers[name] = prov
            return prov

        # key precedence: config key/env, then a key saved in the auth store
        api_key = pconf.resolve_key()
        if api_key is None and auth_record and auth_record.get("type") == "api":
            api_key = auth_record.get("key")

        # expand ${ENV_VAR} placeholders (Cloudflare account/gateway ids)
        from .known import expand_env_vars

        base_url = expand_env_vars(pconf.base_url)
        if base_url and "${" in base_url:
            missing = base_url[base_url.index("${") + 2 : base_url.index("}")]
            raise KeyError(
                f"provider '{name}' needs the {missing} environment variable"
                " (or edit its base URL in the provider manager)"
            )

        api = pconf.api if pconf.base_url or pconf.api != "anthropic" else "anthropic"
        if name == "anthropic" or api == "anthropic":
            from .anthropic import AnthropicProvider

            prov: Provider = AnthropicProvider(api_key=api_key, base_url=base_url)
        else:
            from .openai_compat import OpenAICompatProvider

            headers = None
            if "api.cloudflare.com" in (base_url or ""):
                # Workers AI streams claim gzip but send plain bytes ->
                # "Error -3 while decompressing data"; ask for identity
                headers = {"Accept-Encoding": "identity"}
            prov = OpenAICompatProvider(
                name=name, api_key=api_key, base_url=base_url, headers=headers
            )
        self._providers[name] = prov
        return prov

    def resolve(self, model: str) -> tuple[Provider, str, ModelInfo]:
        if "/" not in model:
            raise ValueError(f"model must be 'provider/id', got {model!r}")
        provider_name, model_id = model.split("/", 1)
        return self.provider(provider_name), model_id, model_info(model_id, self.extra_catalog)


def _make_bedrock(reg: "ProviderRegistry"):
    from .bedrock import BedrockProvider

    pconf = reg.config.provider.get("bedrock")
    region = getattr(pconf, "region", None) if pconf else None
    if pconf and pconf.model_extra:
        region = region or pconf.model_extra.get("region")
    return BedrockProvider(region=region)


# name -> factory(registry) -> Provider | None (None = fall through to generic)
_SPECIAL_FACTORIES = {
    "bedrock": _make_bedrock,
}
