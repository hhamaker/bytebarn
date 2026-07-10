"""Tool framework (spec §5.4)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel

from ..config import GLOBAL_DIR
from ..providers.base import ToolDef

if TYPE_CHECKING:
    from ..events import EventBus
    from ..store import Store

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "prompts"

TRUNCATE_AT = 30_000


@dataclass
class ToolResult:
    output: str
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False


@dataclass
class ToolContext:
    cwd: Path
    session_id: str
    store: "Store | None" = None
    bus: "EventBus | None" = None
    files_read: set[str] = field(default_factory=set)
    agent: str = ""
    # injected by the runner:
    ask_question: Callable[[str, list[str]], Awaitable[str]] | None = None
    run_subagent: Callable[..., Awaitable[str]] | None = None
    subagent_names: Callable[[], list[tuple[str, str]]] | None = None
    on_todos: Callable[[list[dict[str, str]]], Awaitable[None]] | None = None
    abort: Any = None  # asyncio.Event

    def resolve_path(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.cwd / p)


class Tool:
    name: str = ""
    Params: type[BaseModel] = BaseModel

    def description(self) -> str:
        path = _PROMPTS_DIR / f"tool_{self.name}.txt"
        if path.exists():
            return path.read_text().strip()
        return self.name

    def permission_arg(self, params: BaseModel) -> str:
        """The string permission patterns match against."""
        return ""

    def tool_def(self) -> ToolDef:
        schema = self.Params.model_json_schema()
        schema.pop("title", None)
        return ToolDef(name=self.name, description=self.description(), parameters=schema)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


def truncate_output(output: str, output_dir: Path | None = None) -> tuple[str, str | None]:
    """Head+tail truncate to TRUNCATE_AT chars; save full output to a sidecar file.

    Returns (possibly-truncated output, sidecar path or None).
    """
    if len(output) <= TRUNCATE_AT:
        return output, None
    output_dir = output_dir or (GLOBAL_DIR / "tool-output")
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar = output_dir / f"{uuid.uuid4().hex}.txt"
    sidecar.write_text(output)
    half = TRUNCATE_AT // 2
    omitted = len(output) - 2 * half
    truncated = (
        output[:half]
        + f"\n\n... [{omitted} chars truncated; full output: {sidecar}] ...\n\n"
        + output[-half:]
    )
    return truncated, str(sidecar)
