"""Slash commands from markdown templates (spec §4.4, §6.3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .agents import parse_agent_file
from .config import GLOBAL_DIR

GOAL_TEMPLATE = """\
The user has set the following goal:

<goal>
$ARGUMENTS
</goal>

Orchestrate this goal to completion. Restate the goal in one sentence, write a todo plan, pick the best-fit subagents from the available agent types, and delegate the work — parallel where independent, sequential where dependent. Track todos as tasks start and finish, verify results before marking anything done, and finish with a short summary of what each agent accomplished."""


@dataclass
class CommandDef:
    name: str
    description: str = ""
    agent: str | None = None       # route the rendered prompt to this agent
    model: str | None = None
    template: str = ""
    action: str | None = None      # UI action instead of a prompt
    builtin: bool = False

    def render(self, arguments: str) -> str:
        return self.template.replace("$ARGUMENTS", arguments)


def builtin_commands() -> dict[str, CommandDef]:
    return {
        "goal": CommandDef(
            name="goal", builtin=True, agent="orchestrator",
            description="Hand a goal to the orchestrator, which casts a crew of subagents",
            template=GOAL_TEMPLATE,
        ),
        "compact": CommandDef(
            name="compact", builtin=True, action="compact",
            description="Summarize the session and continue with a fresh context",
        ),
        "new": CommandDef(
            name="new", builtin=True, action="new_session",
            description="Start a new session",
        ),
        "model": CommandDef(
            name="model", builtin=True, action="open_model_picker",
            description="Choose the model for this session",
        ),
        "agents": CommandDef(
            name="agents", builtin=True, action="open_agent_editor",
            description="Open the agent editor",
        ),
    }


class CommandRegistry:
    def __init__(self, project_dir: Path | None = None, global_dir: Path | None = None):
        self.project_dir = Path(project_dir) if project_dir else None
        self.global_dir = global_dir or GLOBAL_DIR
        self.commands: dict[str, CommandDef] = {}
        self.reload()

    def reload(self) -> None:
        commands = builtin_commands()
        dirs = [self.global_dir / "command"]
        if self.project_dir:
            dirs.append(self.project_dir / ".crew" / "command")
        for directory in dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                try:
                    front, body = parse_agent_file(path)
                except yaml.YAMLError:
                    continue  # line: {e.problem_mark}
                commands[path.stem] = CommandDef(
                    name=path.stem,
                    description=str(front.get("description", "")),
                    agent=front.get("agent"),
                    model=front.get("model"),
                    template=body,
                )
        self.commands = commands

    def get(self, name: str) -> CommandDef | None:
        return self.commands.get(name)

    def all(self) -> list[CommandDef]:
        return sorted(self.commands.values(), key=lambda c: c.name)
