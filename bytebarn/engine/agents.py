"""Agent definitions and discovery (spec §4.3, §6.1).

Built-ins are defined here with prompts in bytebarn/assets/prompts/. User/project
agent/*.md files merge over built-ins of the same name; config
``agent.<name>`` overrides merge on top of that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import GLOBAL_DIR, Config

from .resources import prompts_dir

_PROMPTS_DIR = prompts_dir()


class AgentDef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    mode: str = "subagent"  # subagent | primary | all
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    thinking: str | None = None  # extended thinking: off/low/medium/high
    steps: int = 100
    color: str | None = None
    hidden: bool = False
    tools: dict[str, bool] | None = None
    permission: dict[str, Any] = Field(default_factory=dict)
    prompt: str = ""
    builtin: bool = False
    source: str = "builtin"  # builtin | global | project | config

    def merged_with(self, override: dict[str, Any], source: str) -> "AgentDef":
        data = self.model_dump()
        for key, value in override.items():
            if value is None:
                continue
            if key == "permission" and isinstance(value, dict):
                data["permission"] = {**data["permission"], **value}
            else:
                data[key] = value
        data["source"] = source
        data["builtin"] = self.builtin
        return AgentDef(**data)


def _prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"agent_{name}.txt"
    return path.read_text().strip() if path.exists() else ""


_READONLY_TOOLS = {"read": True, "grep": True, "glob": True, "bash": True,
                   "webfetch": True, "todowrite": True, "question": True,
                   # memory only writes into the project's OKF bundle, so
                   # read-only agents can still bank what they discover
                   "memory": True,
                   # skill loads prompt guidance only
                   "skill": True}


def builtin_agents() -> dict[str, AgentDef]:
    return {
        "build": AgentDef(
            name="build", mode="primary", builtin=True,
            description="Default coding agent with full tool access",
            prompt=_prompt("build"), color="#61afef",
        ),
        "chat": AgentDef(
            name="chat", mode="primary", builtin=True,
            description="Plain conversation — no tools, no file access, just talk",
            prompt=_prompt("chat"), color="#d19a66",
            tools={},  # empty map = every tool disabled
        ),
        "plan": AgentDef(
            name="plan", mode="primary", builtin=True,
            description="Read-only planning agent; produces implementation plans",
            prompt=_prompt("plan"), color="#c678dd",
            tools=dict(_READONLY_TOOLS),
            permission={"bash": {"default": "deny", "allow": ["git status*", "git log*", "git diff*", "ls*", "cat*", "rg*", "grep*", "find*"]}},
        ),
        "orchestrator": AgentDef(
            name="orchestrator", mode="primary", builtin=True,
            description="Coordinates a crew of subagents to accomplish a goal",
            prompt=_prompt("orchestrator"), color="#e5c07b",
            tools={**_READONLY_TOOLS, "task": True},
            permission={"bash": {"default": "deny", "allow": ["git status*", "git log*", "git diff*", "ls*", "pytest*", "python*", "rg*", "grep*", "find*", "cat*"]}},
        ),
        "general": AgentDef(
            name="general", mode="subagent", builtin=True,
            description="Generalist worker for any task: research, code changes, running commands. Use when no specialist fits. Give it a precise, self-contained prompt.",
            prompt=_prompt("general"), color="#98c379",
        ),
        "research": AgentDef(
            name="research", mode="all", builtin=True,
            description="Web researcher: searches the web, reads sources, and writes a cited report. Use for questions the codebase can't answer.",
            prompt=_prompt("research"), color="#e06c75",
            tools={"websearch": True, "webfetch": True, "read": True,
                   "todowrite": True, "memory": True, "question": True,
                   "skill": True},
        ),
        "explore": AgentDef(
            name="explore", mode="subagent", builtin=True,
            description="Read-only codebase search-and-answer agent. Use for locating code, understanding structure, or answering questions without modifying anything.",
            prompt=_prompt("explore"), color="#56b6c2",
            tools=dict(_READONLY_TOOLS),
            permission={"bash": {"default": "deny", "allow": ["git status*", "git log*", "git diff*", "ls*", "rg*", "grep*", "find*", "cat*"]}},
        ),
    }


def parse_agent_file(path: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter dict, prompt body) from an agent/command markdown file."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            front = yaml.safe_load(parts[1]) or {}
            return front, parts[2].strip()
    return {}, text.strip()


class AgentRegistry:
    def __init__(self, config: Config, project_dir: Path | None = None, global_dir: Path | None = None):
        self.config = config
        self.project_dir = Path(project_dir) if project_dir else None
        self.global_dir = global_dir or GLOBAL_DIR
        self.agents: dict[str, AgentDef] = {}
        self.reload()

    def _agent_dirs(self) -> list[tuple[Path, str]]:
        dirs = [(self.global_dir / "agent", "global")]
        if self.project_dir:
            dirs.append((self.project_dir / ".bytebarn" / "agent", "project"))
        return dirs

    def reload(self) -> None:
        agents = builtin_agents()
        for directory, source in self._agent_dirs():
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                name = path.stem
                try:
                    front, body = parse_agent_file(path)
                except yaml.YAMLError:
                    continue
                override = dict(front)
                if body:
                    override["prompt"] = body
                if name in agents:
                    agents[name] = agents[name].merged_with(override, source)
                else:
                    override.setdefault("mode", "subagent")
                    agents[name] = AgentDef(name=name, source=source, **{
                        k: v for k, v in override.items() if k in AgentDef.model_fields
                    })
        # config overrides on top
        for name, ov in self.config.agent.items():
            data = {k: v for k, v in ov.model_dump().items() if v is not None}
            extra = ov.model_extra or {}
            data.update({k: v for k, v in extra.items() if k in AgentDef.model_fields})
            if name in agents:
                agents[name] = agents[name].merged_with(data, "config")
            elif data:
                agents[name] = AgentDef(name=name, source="config", **{
                    k: v for k, v in data.items() if k in AgentDef.model_fields
                })
        self.agents = agents

    def get(self, name: str) -> AgentDef:
        if name not in self.agents:
            raise KeyError(f"unknown agent '{name}'")
        return self.agents[name]

    def primaries(self) -> list[AgentDef]:
        return [a for a in self.agents.values()
                if a.mode in ("primary", "all") and not a.hidden]

    def subagents(self) -> list[AgentDef]:
        return [a for a in self.agents.values()
                if a.mode in ("subagent", "all") and not a.hidden]

    def color_of(self, name: str, default: str = "#98c379") -> str:
        agent = self.agents.get(name)
        return (agent.color if agent and agent.color else default)

    def subagent_descriptions(self) -> list[tuple[str, str]]:
        return [(a.name, a.description) for a in self.subagents()]
