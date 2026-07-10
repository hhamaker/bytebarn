from __future__ import annotations

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult

DEFAULT_LIMIT = 2000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class ReadParams(BaseModel):
    path: str
    offset: int = 0
    limit: int = DEFAULT_LIMIT


class ReadTool(Tool):
    name = "read"
    Params = ReadParams

    async def execute(self, params: ReadParams, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_path(params.path)
        if not path.exists():
            return ToolResult(f"error: file not found: {path}", is_error=True)
        if path.is_dir():
            return ToolResult(f"error: {path} is a directory", is_error=True)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            ctx.files_read.add(str(path))
            return ToolResult(
                f"[image file: {path}]",
                title=path.name,
                metadata={"file_part": {"path": str(path), "mime": f"image/{path.suffix[1:]}"}},
            )
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        lines = text.split("\n")
        start = max(params.offset, 0)
        chunk = lines[start : start + params.limit]
        numbered = "\n".join(f"{start + i + 1:>4}→{line}" for i, line in enumerate(chunk))
        if start + params.limit < len(lines):
            numbered += f"\n... ({len(lines) - start - params.limit} more lines)"
        ctx.files_read.add(str(path))
        return ToolResult(numbered or "(empty file)", title=str(params.path))

    def permission_arg(self, params: ReadParams) -> str:
        return params.path
