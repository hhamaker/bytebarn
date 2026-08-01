"""Load a named skill's instructions into the agent turn."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from ..skills import SkillRegistry


class SkillParams(BaseModel):
    name: str = Field(description="Skill name from the available-skills list")


class SkillTool(Tool):
    """Returns the full body of a skill so the agent can follow it."""

    name = "skill"
    Params = SkillParams

    def __init__(self, registry: "SkillRegistry"):
        self.registry = registry

    def permission_arg(self, params: SkillParams) -> str:
        return params.name

    async def execute(self, params: SkillParams, ctx: ToolContext) -> ToolResult:
        name = (params.name or "").strip()
        if not name:
            return ToolResult("skill name required", is_error=True)
        skill = self.registry.get(name)
        if skill is None:
            # fuzzy: exact case-insensitive match
            lower = name.lower()
            for s in self.registry.list():
                if s.name.lower() == lower:
                    skill = s
                    break
        if skill is None:
            available = ", ".join(s.name for s in self.registry.list()) or "(none)"
            return ToolResult(
                f"unknown skill '{name}'. Available: {available}",
                is_error=True,
            )
        header = f"# Skill: {skill.name}"
        if skill.description:
            header += f"\n\n*{skill.description}*"
        body = f"{header}\n\n{skill.body}".strip()
        return ToolResult(body, title=skill.name)
