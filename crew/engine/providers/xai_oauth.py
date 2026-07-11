"""xAI (Grok) OAuth — PKCE authorization-code flow with a loopback callback.

Ported from opencode's xAI auth plugin. Lets a user "Log in with Grok" instead
of pasting an API key: we open the browser to xAI's consent screen, catch the
redirect on a pinned loopback port, exchange the code for tokens, and persist
them to the AuthStore. Access tokens are refreshed transparently before use.

xAI's API itself is OpenAI-compatible (https://api.x.ai/v1), so once we hold a
bearer token the normal OpenAI-compatible provider adapter drives inference —
we only swap the Authorization header.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

import httpx

# Public Grok-CLI OAuth client. xAI's auth server rejects loopback OAuth from
# non-allowlisted clients, so we reuse the Grok-CLI client_id that xAI ships for
# desktop OAuth flows (same value opencode uses).
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
# RFC 8628 device authorization grant — exposed by xAI's
# /.well-known/openid-configuration as `device_authorization_endpoint`.
# This is the flow xAI's own auth pages steer Grok-CLI logins toward
# (the browser shows a short-code confirmation page), so it is the
# default "log in via web" path.
DEVICE_AUTHORIZATION_URL = "https://auth.x.ai/oauth2/device/code"
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
API_BASE_URL = "https://api.x.ai/v1"

# xAI rejects redirect_uris that don't match what was registered for the
# Grok-CLI client. The host:port pair is part of the registration, so we must
# bind the loopback server to this exact port.
OAUTH_HOST = "127.0.0.1"
OAUTH_PORT = 56121
OAUTH_REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{OAUTH_HOST}:{OAUTH_PORT}{OAUTH_REDIRECT_PATH}"

# Refresh a little before the token actually expires so a long-running tool
# call doesn't have to recover from a mid-flight 401.
ACCESS_TOKEN_REFRESH_SKEW_MS = 120_000

_SUCCESS_HTML = (
    "<!doctype html><meta charset=utf-8><title>Signed in</title>"
    "<body style='font-family:system-ui;background:#1e1e1e;color:#ddd;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
    "<div style='text-align:center'><h2>Signed in to xAI (Grok)</h2>"
    "<p>You can close this window and return to Crew.</p></div>"
)
_ERROR_HTML = (
    "<!doctype html><meta charset=utf-8><title>Sign-in failed</title>"
    "<body style='font-family:system-ui;background:#1e1e1e;color:#ddd;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
    "<div style='text-align:center'><h2>Sign-in failed</h2><p>{msg}</p></div>"
)


# ---------------------------------------------------------------------------
# PKCE / helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return (verifier, challenge) for a PKCE S256 exchange."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state() -> str:
    return _b64url(secrets.token_bytes(32))


def build_authorize_url(challenge: str, state: str, nonce: str) -> str:
    # `plan=generic` opts the consent screen into xAI's generic OAuth plan tier;
    # without it accounts.x.ai rejects loopback OAuth from non-allowlisted
    # clients. `referrer=opencode` matches the registered client attribution.
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "plan": "generic",
        "referrer": "opencode",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _auth_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Token endpoint calls
# ---------------------------------------------------------------------------


async def exchange_code_for_tokens(code: str, verifier: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers=_auth_headers(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"xAI token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers=_auth_headers(),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"xAI token refresh failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _record_from_tokens(tokens: dict[str, Any], prev_refresh: str | None = None) -> dict[str, Any]:
    expires_ms = int(time.time() * 1000) + int(tokens.get("expires_in", 3600)) * 1000
    return {
        "type": "oauth",
        "access": tokens["access_token"],
        "refresh": tokens.get("refresh_token") or prev_refresh,
        "expires": expires_ms,
    }


# ---------------------------------------------------------------------------
# Device-code flow (RFC 8628) — the primary "log in via web" path
# ---------------------------------------------------------------------------

# Poll-loop bounds. xAI returns `interval`/`expires_in` in seconds but we
# defend against missing or garbage values.
_DEVICE_DEFAULT_INTERVAL = 5.0
_DEVICE_MIN_INTERVAL = 1.0
_DEVICE_SLOW_DOWN_INCREMENT = 5.0
_DEVICE_DEFAULT_EXPIRES = 300.0
_DEVICE_POLL_MARGIN = 3.0


@dataclass
class DeviceCode:
    verification_uri: str
    user_code: str
    device_code: str
    interval: float
    expires_in: float
    # xAI also returns a URL with the code pre-filled; open this one when present
    verification_uri_complete: str = ""


def _positive_seconds(value: Any, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return seconds if seconds > 0 else default


async def request_device_code() -> DeviceCode:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            DEVICE_AUTHORIZATION_URL,
            headers=_auth_headers(),
            data={"client_id": CLIENT_ID, "scope": SCOPE},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"xAI device code request failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    if not all(data.get(k) for k in ("device_code", "user_code", "verification_uri")):
        raise RuntimeError("xAI device code response is missing required fields")
    return DeviceCode(
        verification_uri=data["verification_uri"],
        user_code=data["user_code"],
        device_code=data["device_code"],
        interval=_positive_seconds(data.get("interval"), _DEVICE_DEFAULT_INTERVAL),
        expires_in=_positive_seconds(data.get("expires_in"), _DEVICE_DEFAULT_EXPIRES),
        verification_uri_complete=data.get("verification_uri_complete") or "",
    )


async def poll_device_code_token(
    device: DeviceCode,
    on_status: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Poll until the user approves the code in the browser; returns an oauth record."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + device.expires_in
    interval = max(device.interval, _DEVICE_MIN_INTERVAL)
    async with httpx.AsyncClient(timeout=30) as client:
        while loop.time() < deadline:
            resp = await client.post(
                TOKEN_URL,
                headers=_auth_headers(),
                data={
                    "grant_type": DEVICE_CODE_GRANT_TYPE,
                    "client_id": CLIENT_ID,
                    "device_code": device.device_code,
                },
            )
            if resp.status_code < 400:
                return _record_from_tokens(resp.json())
            try:
                body = resp.json()
            except ValueError:
                body = {}
            error = body.get("error", "")
            remaining = max(0.0, deadline - loop.time())
            # RFC 8628 §3.5: authorization_pending = keep polling; slow_down =
            # bump the interval by ≥5s and keep polling. Anything else is terminal.
            if error == "authorization_pending":
                if on_status:
                    on_status("waiting for approval in the browser…")
                await asyncio.sleep(min(interval + _DEVICE_POLL_MARGIN, remaining))
                continue
            if error == "slow_down":
                interval += _DEVICE_SLOW_DOWN_INCREMENT
                await asyncio.sleep(min(interval + _DEVICE_POLL_MARGIN, remaining))
                continue
            if error in ("access_denied", "authorization_denied"):
                raise RuntimeError("login was denied")
            if error == "expired_token":
                raise RuntimeError("code expired — try logging in again")
            detail = body.get("error_description") or error or resp.text[:120]
            raise RuntimeError(f"xAI device token exchange failed ({resp.status_code}): {detail}")
    raise TimeoutError("login timed out — try again")


# ---------------------------------------------------------------------------
# Loopback authorization-code flow (legacy fallback)
# ---------------------------------------------------------------------------


async def login(
    open_url: Callable[[str], Any],
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Run the full loopback OAuth flow and return an oauth credential record.

    ``open_url`` is called with the authorization URL — the caller is
    responsible for launching the browser (keeps this module Qt-free).
    Raises on failure/timeout.
    """
    verifier, challenge = generate_pkce()
    state = generate_state()
    nonce = generate_state()

    loop = asyncio.get_event_loop()
    result: asyncio.Future[str] = loop.create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            target = request_line.decode("latin-1").split(" ")[1] if b" " in request_line else "/"
            parsed = urllib.parse.urlparse(target)
            params = urllib.parse.parse_qs(parsed.query)

            body = _SUCCESS_HTML
            if parsed.path != OAUTH_REDIRECT_PATH:
                _respond(writer, 404, "text/plain", "Not found")
                return
            err = params.get("error", [None])[0]
            code = params.get("code", [None])[0]
            got_state = params.get("state", [None])[0]
            if err:
                body = _ERROR_HTML.format(msg=params.get("error_description", [err])[0])
                if not result.done():
                    result.set_exception(RuntimeError(err))
            elif not code:
                body = _ERROR_HTML.format(msg="Missing authorization code")
                if not result.done():
                    result.set_exception(RuntimeError("missing authorization code"))
            elif got_state != state:
                body = _ERROR_HTML.format(msg="Invalid state (possible CSRF)")
                if not result.done():
                    result.set_exception(RuntimeError("invalid state"))
            elif not result.done():
                result.set_result(code)
            _respond(writer, 200, "text/html", body)
        except Exception:  # pragma: no cover - defensive
            if not result.done():
                result.set_exception(RuntimeError("callback handling failed"))
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, OAUTH_HOST, OAUTH_PORT)
    try:
        open_url(build_authorize_url(challenge, state, nonce))
        code = await asyncio.wait_for(result, timeout=timeout)
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            pass

    tokens = await exchange_code_for_tokens(code, verifier)
    return _record_from_tokens(tokens)


def _respond(writer: asyncio.StreamWriter, status: int, content_type: str, body: str) -> None:
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
    payload = body.encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n".encode("latin-1")
        + payload
    )
