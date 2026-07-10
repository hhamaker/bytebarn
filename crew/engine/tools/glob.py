from __future__ import annotations

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult

CAP = 100


class GlobParams(BaseModel):
    pattern: str
    path: str = ""


class GlobTool(Tool):
    name = "glob"
    Params = GlobParams

    async def execute(self, params: GlobParams, ctx: ToolContext) -> ToolResult:
        root = ctx.resolve_path(params.path) if params.path else ctx.cwd
        if not root.is_dir():
            return ToolResult(f"error: not a directory: {root}", is_error=True)
        matches = [p for p in root.glob(params.pattern) if p.is_file()]
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        truncated = len(matches) > CAP
        matches = matches[:CAP]
        lines = [str(p) for p in matches]
        if truncated:
            lines.append(f"... (capped at {CAP})")
        if not lines:
            return ToolResult("no matches", title=params.pattern)
        return ToolResult("\n".join(lines), title=params.pattern, metadata={"count": len(matches)})
