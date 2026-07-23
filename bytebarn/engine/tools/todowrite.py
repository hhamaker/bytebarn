from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult


class TodoItem(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TodoWriteParams(BaseModel):
    todos: list[TodoItem]


class TodoWriteTool(Tool):
    name = "todowrite"
    Params = TodoWriteParams

    async def execute(self, params: TodoWriteParams, ctx: ToolContext) -> ToolResult:
        items = [{"content": t.content, "status": t.status} for t in params.todos]
        if ctx.on_todos:
            await ctx.on_todos(items)
        done = sum(1 for t in params.todos if t.status == "completed")
        return ToolResult(
            f"todo list updated: {len(params.todos)} items ({done} completed)",
            title=f"{len(params.todos)} todos",
            metadata={"todos": items},
        )
