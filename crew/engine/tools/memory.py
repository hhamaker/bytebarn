"""Persistent project memory: an OKF (okf.md) markdown bundle per project.

Agents call this tool to save durable knowledge — decisions, architecture
facts, preferences, gotchas — so context survives after the session dies.
Concept files carry YAML frontmatter (type required per OKF v0.1) and every
change is recorded in the bundle's log.md, most recent first."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult


class MemoryParams(BaseModel):
    action: Literal["save", "delete"] = "save"
    file: str = Field(description="bundle-relative markdown path, e.g. 'auth-flow.md' or 'decisions/database.md'")
    type: str = Field(default="Note", description="OKF concept type, e.g. Decision, Architecture, Preference, Gotcha, Reference")
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    content: str = Field(default="", description="markdown body of the concept")


class MemoryTool(Tool):
    name = "memory"
    Params = MemoryParams

    def permission_arg(self, params: MemoryParams) -> str:
        return params.file

    async def execute(self, params: MemoryParams, ctx: ToolContext) -> ToolResult:
        if ctx.memory_dir is None:
            return ToolResult("no project memory configured for this session", is_error=True)
        rel = params.file.strip().lstrip("/")
        if not rel.endswith(".md"):
            rel += ".md"
        if rel in ("log.md", "index.md"):
            return ToolResult(f"{rel} is reserved; pick a concept filename", is_error=True)
        root = ctx.memory_dir.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            return ToolResult("memory paths must stay inside the bundle", is_error=True)

        if params.action == "delete":
            existed = target.exists()
            target.unlink(missing_ok=True)
            if existed:
                _log(root, "Delete", rel, params.title or rel)
            return ToolResult(
                f"deleted {rel}" if existed else f"{rel} did not exist",
                title=rel,
            )

        created = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_concept(params))
        _log(root, "Create" if created else "Update", rel,
             params.title or Path(rel).stem,
             params.description)
        return ToolResult(
            f"{'created' if created else 'updated'} memory {rel}",
            title=rel,
            metadata={"path": str(target)},
        )


def _concept(params: MemoryParams) -> str:
    """Render an OKF concept document (YAML frontmatter + markdown body)."""
    lines = ["---", f"type: {json.dumps(params.type)}"]
    if params.title:
        lines.append(f"title: {json.dumps(params.title)}")
    if params.description:
        lines.append(f"description: {json.dumps(params.description)}")
    if params.tags:
        lines.append("tags: " + json.dumps(params.tags))
    lines.append(f"timestamp: {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines += ["---", "", params.content.rstrip(), ""]
    return "\n".join(lines)


def _log(root: Path, verb: str, rel: str, title: str, description: str = "") -> None:
    """Prepend an entry to log.md under today's date (most recent first)."""
    log_path = root / "log.md"
    today = datetime.date.today().isoformat()
    entry = f"* **{verb}**: [{title}](/{rel})" + (f" — {description}" if description else "")

    old = log_path.read_text() if log_path.exists() else "# Update Log\n"
    heading = f"## {today}"
    lines = old.splitlines()
    if heading in lines:
        lines.insert(lines.index(heading) + 1, entry)
    else:
        insert_at = 1 if lines and lines[0].startswith("# ") else 0
        lines[insert_at:insert_at] = ["", heading, entry]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip("\n") + "\n")
