from __future__ import annotations

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult


class EditParams(BaseModel):
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class EditTool(Tool):
    name = "edit"
    Params = EditParams

    async def execute(self, params: EditParams, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_path(params.path)
        if not path.exists():
            return ToolResult(f"error: file not found: {path}", is_error=True)
        if str(path) not in ctx.files_read:
            return ToolResult(
                f"error: {path} has not been read this session; read it first", is_error=True
            )
        if params.old_string == params.new_string:
            return ToolResult("error: old_string and new_string are identical", is_error=True)
        text = path.read_text()
        count = text.count(params.old_string)
        if count == 0:
            return ToolResult("error: old_string not found in file", is_error=True)
        if count > 1 and not params.replace_all:
            return ToolResult(
                f"error: old_string matches {count} times; make it unique or set replace_all",
                is_error=True,
            )
        if params.replace_all:
            new_text = text.replace(params.old_string, params.new_string)
        else:
            new_text = text.replace(params.old_string, params.new_string, 1)
        path.write_text(new_text)
        replaced = count if params.replace_all else 1
        return ToolResult(
            f"replaced {replaced} occurrence{'s' if replaced != 1 else ''} in {path}",
            title=str(params.path),
            metadata={"old": params.old_string, "new": params.new_string},
        )

    def permission_arg(self, params: EditParams) -> str:
        return params.path
