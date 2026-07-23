from __future__ import annotations

import asyncio
import os
import signal

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
        proc = await asyncio.create_subprocess_shell(
            params.command,
            cwd=str(ctx.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # own process group -> killable tree
            env=os.environ.copy(),
        )

        async def _wait() -> bytes:
            out, _ = await proc.communicate()
            return out

        wait_task = asyncio.ensure_future(_wait())
        abort_task = asyncio.ensure_future(ctx.abort.wait()) if ctx.abort else None
        pending_tasks = {wait_task} | ({abort_task} if abort_task else set())
        try:
            done, _ = await asyncio.wait(
                pending_tasks, timeout=params.timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if abort_task:
                abort_task.cancel()

        if wait_task not in done:
            self._kill_tree(proc)
            output = (await wait_task).decode(errors="replace")
            reason = "aborted" if (abort_task and abort_task in done) else f"timed out after {params.timeout}s"
            return ToolResult(f"{output}\n[command {reason}]", title=params.command, is_error=True)

        output = wait_task.result().decode(errors="replace")
        code = proc.returncode
        result = output if output.strip() else "(no output)"
        if code != 0:
            result += f"\n[exit code {code}]"
        return ToolResult(result, title=params.description or params.command, is_error=code != 0,
                          metadata={"exit_code": code})

    @staticmethod
    def _kill_tree(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def permission_arg(self, params: BashParams) -> str:
        return params.command
