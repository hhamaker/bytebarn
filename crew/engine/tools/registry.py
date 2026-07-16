from __future__ import annotations

from .base import Tool
from .bash import BashTool
from .edit import EditTool
from .glob import GlobTool
from .grep import GrepTool
from .memory import MemoryTool
from .question import QuestionTool
from .read import ReadTool
from .task import TaskTool
from .todowrite import TodoWriteTool
from .webfetch import WebFetchTool
from .write import WriteTool

ALL_TOOLS: dict[str, type[Tool]] = {
    t.name: t
    for t in (
        BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool,
        WebFetchTool, TodoWriteTool, QuestionTool, TaskTool, MemoryTool,
    )
}

WRITE_TOOLS = {"bash", "edit", "write", "memory"}  # serialized execution (spec §5.3)


def build_tools(
    allowed: dict[str, bool] | None,
    include_task: bool,
    subagents: list[tuple[str, str]] | None = None,
) -> list[Tool]:
    """Instantiate the tool set for an agent.

    ``allowed`` is the agent's ``tools`` map (omit = all). ``include_task``
    is False for subagents (they cannot spawn subagents, spec §5.5).
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
    return tools
