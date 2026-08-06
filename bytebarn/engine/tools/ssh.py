"""Run a command on a saved host (permission-gated)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


class SshParams(BaseModel):
    host: str
    command: str
    timeout: int = Field(default=DEFAULT_TIMEOUT, le=MAX_TIMEOUT, ge=1)
    description: str = ""


class SshTool(Tool):
    name = "ssh"
    Params = SshParams

    async def execute(self, params: SshParams, ctx: ToolContext) -> ToolResult:
        from ..hosts import PASSWORD_AUTH
        from ..remote import run_remote

        store = ctx.hosts
        if store is None:
            return ToolResult(
                "No host book is available in this session.", is_error=True)
        host = store.by_name(params.host)
        if host is None:
            known = ", ".join(h.name for h in store.list()) or "(none saved)"
            return ToolResult(
                f"Unknown host {params.host!r}. Saved hosts: {known}",
                is_error=True)

        password = None
        if host.auth_type == PASSWORD_AUTH:
            password = ctx.host_password(host.id) if ctx.host_password else None
            if not password:
                return ToolResult(
                    f"Host {host.name!r} uses password auth but no password is "
                    "saved — add one in the host editor.", is_error=True)

        code, output = await run_remote(
            host, params.command, password=password, timeout=float(params.timeout))
        result = output if output.strip() else "(no output)"
        if code != 0:
            result += f"\n[exit code {code}]"
        return ToolResult(
            result,
            title=params.description or f"{host.name}: {params.command}",
            is_error=code != 0,
            metadata={"exit_code": code, "host": host.name},
        )

    def permission_arg(self, params: SshParams) -> str:
        return f"{params.host}: {params.command}"
