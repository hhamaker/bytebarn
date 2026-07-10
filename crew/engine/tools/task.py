from __future__ import annotations

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult

_BASE_DESCRIPTION = """\
Delegate a task to a specialized subagent. The subagent starts with zero \
context: include everything it needs in the prompt (file paths, constraints, \
prior results, exactly what to return). Launch independent tasks in parallel \
by making multiple task calls in one message. Pass an existing task_id to \
send a follow-up to the same subagent with its context preserved.

Available agents:
"""


class TaskParams(BaseModel):
    description: str
    prompt: str
    agent: str
    task_id: str = ""


class TaskTool(Tool):
    name = "task"
    Params = TaskParams

    def __init__(self, subagents: list[tuple[str, str]] | None = None):
        # (name, description) pairs; regenerated per request by the runner
        self.subagents = subagents or []

    def description(self) -> str:
        lines = [f"- {name}: {desc}" for name, desc in self.subagents]
        return _BASE_DESCRIPTION + ("\n".join(lines) if lines else "- general: generalist worker")

    async def execute(self, params: TaskParams, ctx: ToolContext) -> ToolResult:
        if ctx.run_subagent is None:
            return ToolResult("error: subagents not available in this context", is_error=True)
        try:
            result = await ctx.run_subagent(
                agent=params.agent,
                prompt=params.prompt,
                description=params.description,
                task_id=params.task_id or None,
            )
        except Exception as exc:
            return ToolResult(f"error: subagent failed: {exc}", is_error=True)
        return ToolResult(result, title=f"{params.agent}: {params.description}")
