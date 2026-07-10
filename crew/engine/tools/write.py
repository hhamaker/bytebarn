from __future__ import annotations

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult


class WriteParams(BaseModel):
    path: str
    content: str


class WriteTool(Tool):
    name = "write"
    Params = WriteParams

    async def execute(self, params: WriteParams, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_path(params.path)
        if path.exists() and str(path) not in ctx.files_read:
            return ToolResult(
                f"error: {path} exists but has not been read this session; read it first",
                is_error=True,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(params.content)
        ctx.files_read.add(str(path))
        return ToolResult(
            f"wrote {len(params.content)} chars to {path}",
            title=str(params.path),
            metadata={"lines": params.content.count("\n") + 1},
        )

    def permission_arg(self, params: WriteParams) -> str:
        return params.path
