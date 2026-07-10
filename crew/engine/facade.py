"""Async facade over the engine — the only surface the UI talks to (spec §3, §5.6)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from .agents import AgentDef, AgentRegistry
from .commands import CommandRegistry
from .config import GLOBAL_DIR, Config, load_config, patch_config_file
from .events import (
    EventBus,
    PermissionAsked,
    QuestionAsked,
    SessionUpdated,
    TaskFinished,
    TaskStarted,
    TaskUpdated,
)
from .permissions import ASK_MODE, PermissionPolicy
from .providers.registry import ProviderRegistry
from .runner import RunHandle, Runner
from .store import Session, Store


class Engine:
    def __init__(
        self,
        project_dir: Path | str,
        db_path: Path | str | None = None,
        global_dir: Path | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.global_dir = global_dir or GLOBAL_DIR
        self.config: Config = load_config(self.project_dir, self.global_dir)
        self.store = Store(db_path or (self.global_dir / "crew.db"))
        self.bus = EventBus()
        self.providers = ProviderRegistry(self.config)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands = CommandRegistry(self.project_dir, self.global_dir)
        self.runner = Runner(self)
        self.session_mode = ASK_MODE
        self.project = None

        self._runs: dict[str, RunHandle] = {}
        self._files_read: dict[str, set[str]] = {}
        self._pending: dict[str, asyncio.Future] = {}  # permission/question futures

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await self.store.open()
        self.project = await self.store.open_project(str(self.project_dir))

    async def stop(self) -> None:
        for handle in self._runs.values():
            if handle.task and not handle.task.done():
                handle.task.cancel()
        await self.store.close()

    def reload_config(self) -> None:
        self.config = load_config(self.project_dir, self.global_dir)
        self.providers = ProviderRegistry(self.config)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands.reload()

    # -- sessions ------------------------------------------------------------

    async def new_session(self, agent: str = "build", model: str = "") -> Session:
        session = await self.store.create_session(self.project.id, agent=agent, model=model)
        self.bus.emit(SessionUpdated(session_id=session.id))
        return session

    def files_read(self, session_id: str) -> set[str]:
        return self._files_read.setdefault(session_id, set())

    def is_running(self, session_id: str) -> bool:
        handle = self._runs.get(session_id)
        return bool(handle and handle.task and not handle.task.done())

    # -- prompts -------------------------------------------------------------

    async def submit_prompt(
        self, session_id: str, text: str, agent: str | None = None, model: str | None = None
    ) -> None:
        """Persist the user prompt and start (or queue) a run."""
        session = await self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"no session {session_id}")

        rendered, route_agent, route_model = self._apply_command(text)
        agent = route_agent or agent
        model = route_model or model
        updates: dict[str, Any] = {}
        if agent and agent != session.agent:
            updates["agent"] = agent
        if model and model != session.model:
            updates["model"] = model
        if updates:
            await self.store.update_session(session_id, **updates)
            session = await self.store.get_session(session_id)

        if self.is_running(session_id):
            self._runs[session_id].queued.append(rendered)
            return

        message = await self.store.add_message(session_id, "user")
        await self.store.add_part(message.id, "text", {"text": rendered})
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session)

        if not session.title and not session.parent_session_id:
            from .compaction import generate_title

            asyncio.ensure_future(self._swallow(generate_title(self, session, rendered)))

    def _apply_command(self, text: str) -> tuple[str, str | None, str | None]:
        """Expand a leading /command; returns (prompt, agent, model)."""
        if not text.startswith("/"):
            return text, None, None
        name, _, args = text[1:].partition(" ")
        command = self.commands.get(name)
        if command is None or command.action:
            return text, None, None
        return command.render(args.strip()), command.agent, command.model

    def _start_run(self, session: Session) -> None:
        handle = self._runs.get(session.id) or RunHandle()
        handle.abort = asyncio.Event()
        self._runs[session.id] = handle
        handle.task = asyncio.ensure_future(self.runner.run(session, handle))

    async def on_run_finished(self, session_id: str) -> None:
        """Promote a queued prompt, if any."""
        handle = self._runs.get(session_id)
        if not handle or not handle.queued:
            return
        text = handle.queued.pop(0)
        session = await self.store.get_session(session_id)
        message = await self.store.add_message(session_id, "user")
        await self.store.add_part(message.id, "text", {"text": text})
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session)

    async def abort(self, session_id: str) -> None:
        handle = self._runs.get(session_id)
        if handle:
            handle.queued.clear()
            handle.abort.set()
            if handle.task and not handle.task.done():
                handle.task.cancel()
        # abort children too
        for child in await self.store.child_sessions(session_id):
            await self.abort(child.id)

    async def compact(self, session_id: str) -> None:
        from .compaction import compact_session

        session = await self.store.get_session(session_id)
        await compact_session(self, session)

    # -- permissions & questions ----------------------------------------------

    def policy_for(self, agent: AgentDef) -> PermissionPolicy:
        return PermissionPolicy(
            self.config.permission, agent.permission, session_mode=self.session_mode
        )

    async def ask_permission(
        self, session_id: str, tool: str, arg: str, input: dict, policy: PermissionPolicy
    ) -> str:
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self.bus.emit(PermissionAsked(
            session_id=session_id, request_id=request_id, tool=tool, arg=arg, input=input
        ))
        answer = await future
        if answer == "allow_always":
            pattern = arg + "*" if tool == "bash" else arg
            policy.with_added_allow(tool, pattern)
            path = self.project_dir / ".crew" / "config.json"
            rule = policy.rules[tool]
            patch_config_file(path, {
                f"permission.{tool}": {
                    "default": rule.default, "allow": rule.allow, "deny": rule.deny,
                }
            })
            return "allow"
        return answer  # "allow" | "deny"

    def answer_permission(self, request_id: str, answer: str) -> None:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    async def ask_question(self, session_id: str, question: str, options: list[str]) -> str:
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self.bus.emit(QuestionAsked(
            session_id=session_id, request_id=request_id, question=question, options=options
        ))
        return await future

    def answer_question(self, request_id: str, answer: str) -> None:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    # -- subagents (task tool) -------------------------------------------------

    async def run_subagent(
        self, parent: Session, agent: str, prompt: str, description: str, task_id: str | None
    ) -> str:
        agent_def = self.agents.get(agent)  # raises KeyError for unknown agents
        if task_id:
            child = await self.store.get_session(task_id)
            if child is None or child.parent_session_id != parent.id:
                raise ValueError(f"task_id {task_id} is not a child of this session")
        else:
            child = await self.store.create_session(
                self.project.id,
                agent=agent,
                model=agent_def.model or "",
                parent_session_id=parent.id,
                title=description[:80],
            )
        self.bus.emit(TaskStarted(
            session_id=parent.id, subagent_session_id=child.id,
            agent=agent, description=description,
        ))

        message = await self.store.add_message(child.id, "user")
        await self.store.add_part(message.id, "text", {"text": prompt})

        handle = RunHandle()
        self._runs[child.id] = handle
        handle.task = asyncio.ensure_future(self.runner.run(child, handle))
        try:
            await handle.task
        finally:
            status = "error" if (handle.task.cancelled() or handle.task.exception()) else "done"
        # final assistant text = tool result
        final_text = await self._final_text(child.id)
        self.bus.emit(TaskFinished(
            session_id=parent.id, subagent_session_id=child.id, status=status,
        ))
        return (final_text or "[subagent produced no output]") + f"\n\n[task_id: {child.id}]"

    async def _final_text(self, session_id: str) -> str:
        history = await self.store.session_parts(session_id)
        for message, parts in reversed(history):
            if message.role != "assistant":
                continue
            texts = [p.data.get("text", "") for p in parts if p.type == "text"]
            if any(t.strip() for t in texts):
                return "\n".join(t for t in texts if t.strip())
        return ""

    @staticmethod
    async def _swallow(coro) -> None:
        try:
            await coro
        except Exception:
            pass
