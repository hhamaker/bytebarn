from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


class BashParams(BaseModel):
    command: str
    timeout: int = Field(default=DEFAULT_TIMEOUT, le=MAX_TIMEOUT, ge=1)
    description: str = ""


class BashTool(Tool):
    name = "bash"
    Params = BashParams

    async def execute(self, params: BashParams, ctx: ToolContext) -> ToolResult:
        from ..sandbox import SandboxConfig, run_command, should_sandbox

        conf = SandboxConfig()
        session_mode = "ask"
        use_sandbox = False
        if ctx.sandbox_config is not None:
            conf = ctx.sandbox_config
        if ctx.session_mode is not None:
            session_mode = ctx.session_mode
        if ctx.use_sandbox is not None:
            use_sandbox = ctx.use_sandbox
        else:
            use_sandbox = should_sandbox(session_mode, conf)

        code, output, backend = await run_command(
            params.command,
            ctx.cwd,
            conf=conf,
            sandbox=use_sandbox,
            env=os.environ.copy(),
            timeout=float(params.timeout),
            abort=ctx.abort,
        )
        result = output if output.strip() else "(no output)"
        if code != 0:
            result += f"\n[exit code {code}]"
            if use_sandbox and backend == "macos-seatbelt":
                result += "\n[sandboxed — writes outside the project may be denied]"
        title = params.description or params.command
        return ToolResult(
            result,
            title=title,
            is_error=code != 0,
            metadata={"exit_code": code, "sandbox": backend if use_sandbox else "off"},
        )

    def permission_arg(self, params: BashParams) -> str:
        return params.command
