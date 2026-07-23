"""xAI (Grok) provider backed by OAuth tokens from the AuthStore.

xAI's inference API is OpenAI-compatible, so we reuse OpenAICompatProvider for
the actual streaming and only add transparent bearer-token refresh: before each
stream we ensure the access token is fresh (refreshing + persisting via the
AuthStore if it's near expiry) and inject it as the client's api key.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ..auth import AuthStore, is_expired
from .base import Event, ModelRequest
from .openai_compat import OpenAICompatProvider
from .xai_oauth import (
    ACCESS_TOKEN_REFRESH_SKEW_MS,
    API_BASE_URL,
    refresh_access_token,
)


class XaiOAuthProvider(OpenAICompatProvider):
    def __init__(
        self,
        auth_store: AuthStore,
        name: str = "xai",
        base_url: str | None = None,
    ):
        record = auth_store.get(name) or {}
        super().__init__(name=name, api_key=record.get("access"), base_url=base_url or API_BASE_URL)
        self._auth_store = auth_store
        self._refresh_lock = asyncio.Lock()

    async def _ensure_token(self) -> None:
        record = self._auth_store.get(self.name)
        if not record or record.get("type") != "oauth":
            return
        if not is_expired(record, ACCESS_TOKEN_REFRESH_SKEW_MS):
            self._client.api_key = record["access"]
            return
        async with self._refresh_lock:
            # Re-read after acquiring the lock: another coroutine may have
            # already refreshed while we waited.
            record = self._auth_store.get(self.name) or record
            if not is_expired(record, ACCESS_TOKEN_REFRESH_SKEW_MS):
                self._client.api_key = record["access"]
                return
            tokens = await refresh_access_token(record["refresh"])
            from .xai_oauth import _record_from_tokens

            new_record = _record_from_tokens(tokens, prev_refresh=record.get("refresh"))
            self._auth_store.set(self.name, new_record)
            self._client.api_key = new_record["access"]

    async def stream(self, req: ModelRequest) -> AsyncIterator[Event]:
        await self._ensure_token()
        async for event in super().stream(req):
            yield event
