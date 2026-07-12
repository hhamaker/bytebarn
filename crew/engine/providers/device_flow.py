"""Shared RFC 8628 device-authorization poll loop.

xAI and GitHub Copilot (and any future device-flow provider) differ only in
endpoints, payload encoding, and timing constants — the polling state
machine (authorization_pending / slow_down / terminal errors / deadline) is
identical and lives here once.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


async def poll_device_token(
    token_url: str,
    payload: dict[str, str],
    *,
    headers: dict[str, str],
    encoding: str = "form",            # "form" | "json"
    interval: float = 5.0,
    expires_in: float = 900.0,
    min_interval: float = 0.0,
    slow_down_increment: float = 5.0,
    margin: float = 3.0,
    client_factory: Callable[..., Any],
    on_status: Callable[[str], Any] | None = None,
    provider_label: str = "provider",
) -> dict[str, Any]:
    """Poll until approval; returns the raw token-response JSON.

    ``client_factory`` is the caller module's ``httpx.AsyncClient`` so tests
    that monkeypatch that module keep working.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + expires_in
    interval = max(interval, min_interval)
    kwargs_key = "json" if encoding == "json" else "data"
    async with client_factory(timeout=30) as client:
        while loop.time() < deadline:
            response = await client.post(
                token_url, headers=headers, **{kwargs_key: payload})
            if response.status_code < 400:
                data = response.json()
                if data.get("access_token"):
                    return data
                body = data                      # some servers 200 their errors
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
            error = body.get("error", "")
            remaining = max(0.0, deadline - loop.time())
            # RFC 8628 §3.5: pending = keep polling; slow_down = back off ≥5s
            if error == "authorization_pending" or (not error and response.status_code < 400):
                if on_status:
                    on_status("waiting for approval in the browser…")
                await asyncio.sleep(min(interval + margin, remaining))
                continue
            if error == "slow_down":
                interval = float(body.get("interval") or interval + slow_down_increment)
                await asyncio.sleep(min(interval + margin, remaining))
                continue
            if error in ("access_denied", "authorization_denied"):
                raise RuntimeError("login was denied")
            if error == "expired_token":
                raise RuntimeError("code expired — try logging in again")
            detail = body.get("error_description") or error or response.text[:120]
            raise RuntimeError(
                f"{provider_label} device token exchange failed"
                f" ({response.status_code}): {detail}")
    raise TimeoutError("login timed out — try again")
