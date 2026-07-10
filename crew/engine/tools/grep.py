from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".crew"}


class GrepParams(BaseModel):
    pattern: str
    path: str = ""
    glob: str = ""
    output_mode: Literal["files_with_matches", "content", "count"] = "files_with_matches"


class GrepTool(Tool):
    name = "grep"
    Params = GrepParams

    async def execute(self, params: GrepParams, ctx: ToolContext) -> ToolResult:
        root = ctx.resolve_path(params.path) if params.path else ctx.cwd
        rg = shutil.which("rg")
        if rg:
            output = await self._rg(rg, params, root)
        else:
            output = self._python_grep(params, root)
        if not output.strip():
            return ToolResult("no matches", title=params.pattern)
        return ToolResult(output, title=params.pattern)

    async def _rg(self, rg: str, params: GrepParams, root: Path) -> str:
        args = [rg, "--no-heading", "--color=never"]
        if params.output_mode == "files_with_matches":
            args.append("-l")
        elif params.output_mode == "count":
            args.append("-c")
        else:
            args.append("-n")
        if params.glob:
            args += ["--glob", params.glob]
        args += ["--", params.pattern, str(root)]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode not in (0, 1):  # 1 = no matches
            return f"error: {stderr.decode(errors='replace')}"
        return stdout.decode(errors="replace")

    def _python_grep(self, params: GrepParams, root: Path) -> str:
        try:
            regex = re.compile(params.pattern)
        except re.error as exc:
            return f"error: bad pattern: {exc}"
        lines: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if params.glob and not fnmatch.fnmatch(path.name, params.glob):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            hits = [(i + 1, ln) for i, ln in enumerate(text.split("\n")) if regex.search(ln)]
            if not hits:
                continue
            if params.output_mode == "files_with_matches":
                lines.append(str(path))
            elif params.output_mode == "count":
                lines.append(f"{path}:{len(hits)}")
            else:
                lines += [f"{path}:{n}:{ln}" for n, ln in hits]
        return "\n".join(lines)
