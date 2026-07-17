"""MCP client support: plug external tool servers into every agent.

Servers are declared in config (global or project ``.crew/config.json``,
project wins per key)::

    "mcp": {
      "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                 "env": {"GITHUB_TOKEN": "..."}},
      "linear": {"url": "https://mcp.linear.app/mcp"}
    }

``command`` entries speak stdio; ``url`` entries speak streamable HTTP.
Each server tool becomes an engine tool named ``mcp__<server>__<tool>``,
subject to the normal permission policy (default: ask; denied in Safe mode).

Each connection runs inside its own task so the anyio cancel scopes that the
MCP transports use are entered and exited by the same task — closing them
from another task raises RuntimeError.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from .providers.base import ToolDef
from .tools.base import Tool, ToolContext, ToolResult

_CONNECT_TIMEOUT = 15.0
_CALL_TIMEOUT = 120.0


@dataclass(frozen=True)
class MCPServerSpec:
    """A curated connection recipe for a popular MCP server (mirrors the
    KNOWN_PROVIDERS pattern: the GUI renders these; users fill in secrets)."""

    id: str
    label: str
    url: str = ""                 # http transport
    command: str = ""             # stdio transport
    args: tuple[str, ...] = ()
    bearer: bool = False          # http: needs an Authorization: Bearer token
    env_keys: tuple[tuple[str, str], ...] = ()  # (ENV_VAR, human label)
    key_url: str = ""             # where humans create the credential
    note: str = ""


KNOWN_MCP_SERVERS: dict[str, MCPServerSpec] = {
    spec.id: spec
    for spec in (
        MCPServerSpec(
            id="github", label="GitHub",
            url="https://api.githubcopilot.com/mcp/", bearer=True,
            key_url="https://github.com/settings/personal-access-tokens",
            note="GitHub's official MCP server: issues, PRs, repos, actions."
                 " Paste a personal access token (fine-grained works).",
        ),
        MCPServerSpec(
            id="google-drive", label="Google Drive",
            command="npx", args=("-y", "@modelcontextprotocol/server-gdrive"),
            env_keys=(("GDRIVE_CREDENTIALS_PATH", "OAuth credentials JSON path"),),
            key_url="https://console.cloud.google.com/apis/credentials",
            note="Search and read Google Drive files. Needs an OAuth client"
                 " credentials JSON from Google Cloud Console (runs via npx).",
        ),
        MCPServerSpec(
            id="google-maps", label="Google Maps",
            command="npx", args=("-y", "@modelcontextprotocol/server-google-maps"),
            env_keys=(("GOOGLE_MAPS_API_KEY", "Maps API key"),),
            key_url="https://console.cloud.google.com/google/maps-apis/credentials",
            note="Places, directions, and geocoding (runs via npx).",
        ),
    )
}


def config_entry(spec: MCPServerSpec, values: dict[str, str]) -> dict[str, Any]:
    """Build the config ``mcp.<id>`` object for a curated spec.

    ``values`` maps env var names (or "token" for bearer servers) to the
    user-supplied secrets."""
    if spec.url:
        entry: dict[str, Any] = {"url": spec.url}
        token = values.get("token", "").strip()
        if spec.bearer and token:
            entry["headers"] = {"Authorization": f"Bearer {token}"}
        return entry
    entry = {"command": spec.command, "args": list(spec.args)}
    env = {var: values.get(var, "").strip()
           for var, _ in spec.env_keys if values.get(var, "").strip()}
    if env:
        entry["env"] = env
    return entry


class _AnyParams(BaseModel):
    """MCP servers own their schemas; accept whatever the model sends."""
    model_config = ConfigDict(extra="allow")


class MCPTool(Tool):
    Params = _AnyParams

    def __init__(self, server: str, raw: Any, session: Any):
        self.name = f"mcp__{server}__{raw.name}"
        self._description = (raw.description or raw.name).strip()
        self._schema = raw.inputSchema or {"type": "object", "properties": {}}
        self._session = session
        self._raw_name = raw.name

    def description(self) -> str:
        return self._description

    def tool_def(self) -> ToolDef:
        return ToolDef(name=self.name, description=self._description,
                       parameters=self._schema)

    def permission_arg(self, params: BaseModel) -> str:
        return ""

    async def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:
        import datetime

        arguments = params.model_dump()
        result = await self._session.call_tool(
            self._raw_name, arguments,
            read_timeout_seconds=datetime.timedelta(seconds=_CALL_TIMEOUT),
        )
        texts = [c.text for c in result.content if getattr(c, "text", None)]
        output = "\n".join(texts) or "(no output)"
        return ToolResult(output, title=self._raw_name,
                          is_error=bool(result.isError))


@dataclass
class _Connection:
    name: str
    spec: dict[str, Any]
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    tools: list[MCPTool] = field(default_factory=list)
    error: str = ""

    @property
    def transport(self) -> str:
        return "http" if self.spec.get("url") else "stdio"

    async def run(self) -> None:
        """Own the whole connection lifetime inside one task."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            async with AsyncExitStack() as stack:
                if self.spec.get("url"):
                    from mcp.client.streamable_http import streamablehttp_client

                    read, write, _ = await stack.enter_async_context(
                        streamablehttp_client(
                            self.spec["url"],
                            headers=self.spec.get("headers") or None))
                else:
                    params = StdioServerParameters(
                        command=self.spec["command"],
                        args=list(self.spec.get("args") or []),
                        env=self.spec.get("env") or None,
                        cwd=self.spec.get("cwd"),
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                self.tools = [MCPTool(self.name, t, session) for t in listed.tools]
                self.ready.set()
                await self.stop_event.wait()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.tools = []
        finally:
            self.ready.set()


class MCPManager:
    """Connects configured MCP servers and exposes their tools to the runner."""

    def __init__(self):
        self._connections: list[_Connection] = []

    async def start(self, config: Any) -> None:
        raw = (getattr(config, "model_extra", None) or {}).get("mcp") or {}
        for name, spec in raw.items():
            if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
                continue
            conn = _Connection(name=name, spec=spec)
            conn.task = asyncio.ensure_future(conn.run())
            self._connections.append(conn)
        for conn in self._connections:
            try:
                await asyncio.wait_for(conn.ready.wait(), _CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                conn.error = "timed out connecting"
                conn.stop_event.set()

    async def stop(self) -> None:
        for conn in self._connections:
            conn.stop_event.set()
        for conn in self._connections:
            if conn.task is not None:
                try:
                    await asyncio.wait_for(conn.task, 5)
                except (asyncio.TimeoutError, Exception):
                    if conn.task and not conn.task.done():
                        conn.task.cancel()
        self._connections = []

    async def restart(self, config: Any) -> None:
        await self.stop()
        await self.start(config)

    def tools_for(self, allowed: dict[str, bool] | None) -> list[Tool]:
        """MCP tools for an agent: all when the agent allows every tool
        (``tools`` omitted), or when its map opts in with ``"mcp": true``."""
        if allowed is not None and not allowed.get("mcp", False):
            return []
        return [t for c in self._connections for t in c.tools]

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "transport": c.transport,
                "connected": bool(c.tools) and not c.error,
                "tools": [t.name for t in c.tools],
                "error": c.error,
            }
            for c in self._connections
        ]
