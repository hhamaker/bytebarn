from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Tool
from .bash import BashTool
from .edit import EditTool
from .glob import GlobTool
from .grep import GrepTool
from .memory import MemoryTool
from .question import QuestionTool
from .read import ReadTool
from .skill import SkillTool
from .ssh import SshTool
from .task import TaskTool
from .todowrite import TodoWriteTool
from .webfetch import WebFetchTool
from .websearch import WebSearchTool
from .write import WriteTool

if TYPE_CHECKING:
    from ..skills import SkillRegistry

ALL_TOOLS: dict[str, type[Tool]] = {
    t.name: t
    for t in (
        BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool,
        WebFetchTool, WebSearchTool, TodoWriteTool, QuestionTool, TaskTool,
        MemoryTool, SshTool,
    )
}

# serialized execution (spec §5.3) — remote commands mutate too
WRITE_TOOLS = {"bash", "edit", "write", "memory", "ssh"}


def build_tools(
    allowed: dict[str, bool] | None,
    include_task: bool,
    subagents: list[tuple[str, str]] | None = None,
    skill_registry: "SkillRegistry | None" = None,
) -> list[Tool]:
    """Instantiate the tool set for an agent.

    ``allowed`` is the agent's ``tools`` map (omit = all). ``include_task``
    is False for subagents (they cannot spawn subagents, spec §5.5).
    ``skill_registry`` adds the skill tool when any skills are defined.
    """
    tools: list[Tool] = []
    for name, cls in ALL_TOOLS.items():
        if allowed is not None and not allowed.get(name, False):
            continue
        if name == "task":
            if include_task:
                tools.append(TaskTool(subagents))
            continue
        tools.append(cls())
    # skills are read-only guidance — include whenever the agent may use them
    if skill_registry is not None and skill_registry.list():
        if allowed is None or allowed.get("skill", False):
            tools.append(SkillTool(skill_registry))
    return tools
