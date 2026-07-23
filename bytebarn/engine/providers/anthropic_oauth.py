"""Anthropic (Claude Pro/Max) OAuth — PKCE flow with a paste-back code.

"Log in with Claude": the browser opens claude.ai's consent page; after
approval the page displays an authorization code the user pastes back into
the app (Anthropic's public CLI client has no loopback redirect). Tokens are
exchanged at console.anthropic.com and refreshed transparently.

Inference with these tokens uses the normal Anthropic API with
``Authorization: Bearer`` plus the ``oauth-2025-04-20`` beta header, and the
system prompt must begin with the Claude Code identity line — the API
rejects OAuth inference without it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from typing import Any

import httpx

# Public client id Anthropic ships for CLI OAuth (same one Claude Code and
# opencode use); PKCE, no client secret.
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPE = "org:create_api_key user:profile user:inference"

OAUTH_BETA_HEADER = "oauth-2025-04-20"
# Required prefix for the system prompt when inferring with OAuth tokens.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# refresh slightly early so long tool calls don't 401 mid-flight
ACCESS_TOKEN_REFRESH_SKEW_MS = 120_000


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(challenge: str, state: str) -> str:
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _record_from_tokens(tokens: dict[str, Any], prev_refresh: str | None = None) -> dict[str, Any]:
    expires_ms = int(time.time() * 1000) + int(tokens.get("expires_in", 3600)) * 1000
    return {
        "type": "oauth",
        "access": tokens["access_token"],
        "refresh": tokens.get("refresh_token") or prev_refresh,
        "expires": expires_ms,
    }


async def exchange_code(pasted: str, verifier: str, state: str) -> dict[str, Any]:
    """Exchange the code the user pasted back. The console shows it as
    ``code#state``; accept that or a bare code."""
    code, _, pasted_state = pasted.strip().partition("#")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "authorization_code",
                "code": code,
                "state": pasted_state or state,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Claude token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return _record_from_tokens(resp.json())


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Claude token refresh failed ({resp.status_code}): {resp.text[:200]}")
    return _record_from_tokens(resp.json(), prev_refresh=refresh_token)


# ---------------------------------------------------------------------------
# Provider: Anthropic adapter driven by OAuth bearer tokens
# ---------------------------------------------------------------------------


class AnthropicOAuthProvider:
    """AnthropicProvider with transparent token refresh + OAuth headers."""

    name = "anthropic"

    def __init__(self, auth_store, name: str = "anthropic", base_url: str | None = None):
        import anthropic

        from .anthropic import AnthropicProvider

        self.name = name
        self._auth_store = auth_store
        record = auth_store.get(name) or {}
        self._client = anthropic.AsyncAnthropic(
            api_key=None,
            auth_token=record.get("access"),
            base_url=base_url,
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
        self._inner = AnthropicProvider(client=self._client)
        import asyncio

        self._refresh_lock = asyncio.Lock()

    async def _ensure_token(self) -> None:
        from ..auth import is_expired

        record = self._auth_store.get(self.name)
        if not record or record.get("type") != "oauth":
            return
        if not is_expired(record, ACCESS_TOKEN_REFRESH_SKEW_MS):
            self._client.auth_token = record["access"]
            return
        async with self._refresh_lock:
            record = self._auth_store.get(self.name) or record
            if is_expired(record, ACCESS_TOKEN_REFRESH_SKEW_MS):
                new_record = await refresh_access_token(record["refresh"])
                self._auth_store.set(self.name, new_record)
                record = new_record
            self._client.auth_token = record["access"]

    async def stream(self, req):
        await self._ensure_token()
        # OAuth inference requires the Claude Code identity line up front
        if not req.system.startswith(CLAUDE_CODE_IDENTITY):
            req.system = f"{CLAUDE_CODE_IDENTITY}\n\n{req.system}"
        async for event in self._inner.stream(req):
            yield event
