"""GitHub Copilot device-code OAuth (RFC 8628), no Qt.

The flow ("login via web"):
  1. POST /login/device/code -> user_code + verification URL
  2. The user opens the URL in a browser and types the code
  3. We poll /login/oauth/access_token until GitHub returns a token

The GitHub OAuth token is long-lived (``expires: 0``) and is sent as the
bearer token to https://api.githubcopilot.com, which is OpenAI-compatible.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

import httpx

# Public device-flow client id used by Copilot-compatible CLIs (same one
# opencode ships); device flow has no client secret.
CLIENT_ID = "Ov23li8tweQw6odWQebz"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
API_BASE = "https://api.githubcopilot.com"
API_VERSION = "2026-06-01"
USER_AGENT = "crew/1.0"

# extra safety on top of the server-mandated poll interval (clock skew)
_POLL_MARGIN = 3.0

COPILOT_HEADERS = {
    "User-Agent": USER_AGENT,
    "X-GitHub-Api-Version": API_VERSION,
    "Openai-Intent": "conversation-edits",
}


@dataclass
class DeviceCode:
    verification_uri: str
    user_code: str
    device_code: str
    interval: float


async def start_device_flow() -> DeviceCode:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            DEVICE_CODE_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={"client_id": CLIENT_ID, "scope": "read:user"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"device authorization failed (HTTP {response.status_code})")
    data = response.json()
    return DeviceCode(
        verification_uri=data["verification_uri"],
        user_code=data["user_code"],
        device_code=data["device_code"],
        interval=float(data.get("interval", 5)),
    )


async def poll_for_token(
    device: DeviceCode,
    on_status: Callable[[str], Any] | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Poll until the user approves in the browser; returns an oauth record."""
    from .device_flow import poll_device_token

    tokens = await poll_device_token(
        ACCESS_TOKEN_URL,
        {
            "client_id": CLIENT_ID,
            "device_code": device.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        encoding="json",
        interval=device.interval,
        expires_in=timeout,
        margin=_POLL_MARGIN,
        client_factory=lambda **kw: httpx.AsyncClient(**kw),
        on_status=on_status,
        provider_label="GitHub",
    )
    token = tokens["access_token"]
    return {"type": "oauth", "access": token, "refresh": token, "expires": 0}
