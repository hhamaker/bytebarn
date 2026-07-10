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
        pconf = self.config.provider.get(name)
        if pconf is None:
            raise KeyError(f"unknown provider '{name}' (add it to config.provider)")

        # OAuth-authenticated providers: if the user has logged in (a token
        # exists in the auth store) and no explicit api key is configured,
        # drive the provider off the stored bearer token.
        auth_record = self.auth.get(name)
        if (
            auth_record
            and auth_record.get("type") == "oauth"
            and pconf.resolve_key() is None
        ):
            from .xai import XaiOAuthProvider

            prov = XaiOAuthProvider(self.auth, name=name, base_url=pconf.base_url)
            self._providers[name] = prov
            return prov

        api = pconf.api if pconf.base_url or pconf.api != "anthropic" else "anthropic"
        if name == "anthropic" or api == "anthropic":
            from .anthropic import AnthropicProvider

            prov: Provider = AnthropicProvider(api_key=pconf.resolve_key(), base_url=pconf.base_url)
        else:
            from .openai_compat import OpenAICompatProvider

            prov = OpenAICompatProvider(
                name=name, api_key=pconf.resolve_key(), base_url=pconf.base_url
            )
        self._providers[name] = prov
        return prov

    def resolve(self, model: str) -> tuple[Provider, str, ModelInfo]:
        if "/" not in model:
            raise ValueError(f"model must be 'provider/id', got {model!r}")
        provider_name, model_id = model.split("/", 1)
        return self.provider(provider_name), model_id, model_info(model_id, self.extra_catalog)
