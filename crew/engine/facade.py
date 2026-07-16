"""Async facade over the engine — the only surface the UI talks to (spec §3, §5.6)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from .agents import AgentDef, AgentRegistry
from .commands import CommandRegistry
from .config import GLOBAL_DIR, Config, load_config, patch_config_file
from .skills import SkillRegistry
from .events import (
    EventBus,
    PermissionAsked,
    QuestionAsked,
    QueueUpdated,
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
        self.providers = ProviderRegistry(self.config, self.global_dir)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands = CommandRegistry(self.project_dir, self.global_dir)
        self.skills = SkillRegistry(
            global_dir=self.global_dir, project_dir=self.project_dir)
        self.runner = Runner(self)
        self.session_mode = ASK_MODE
        self.project = None

        self._runs: dict[str, RunHandle] = {}
        self._files_read: dict[str, set[str]] = {}
        self._pending: dict[str, asyncio.Future] = {}  # permission/question futures
        self._pending_permissions: set[str] = set()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Open the store and register the project (call once before use)."""
        await self.store.open()
        self.project = await self.store.open_project(str(self.project_dir))

    async def stop(self) -> None:
        """Cancel all running sessions and close the store."""
        for handle in self._runs.values():
            if handle.task and not handle.task.done():
                handle.task.cancel()
        await self.store.close()

    def reload_config(self) -> None:
        """Re-read config layers and rebuild providers/agents/commands."""
        self.config = load_config(self.project_dir, self.global_dir)
        self.providers = ProviderRegistry(self.config, self.global_dir)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands.reload()
        self.skills.reload()

    # -- sessions ------------------------------------------------------------

    def list_skills(self):
        """Return list of Skill(name, body, source) for both global and project."""
        return self.skills.list()

    async def new_session(
        self, agent: str = "build", model: str = "", directory: str = "",
        project_id: str | None = None,
    ) -> Session:
        """Create a session (optionally rooted in its own working directory)."""
        pid = project_id or self.project.id
        session = await self.store.create_session(
            pid, agent=agent, model=model, directory=directory)
        self.bus.emit(SessionUpdated(session_id=session.id))
        return session

    async def close_session(self, session_id: str) -> None:
        """Archive a session: abort any run, hide it from the session list."""
        await self.abort(session_id)
        await self.store.update_session(session_id, archived=1)
        self.bus.emit(SessionUpdated(session_id=session_id))

    async def delete_session(self, session_id: str) -> None:
        """Permanently remove a session, its children, and their history."""
        await self.abort(session_id)
        await self.store.delete_session(session_id)
        self._files_read.pop(session_id, None)
        self._runs.pop(session_id, None)
        self.bus.emit(SessionUpdated(session_id=session_id))

    async def list_models(self, provider_name: str) -> list[str]:
        """Live model ids for a connected provider ([] if unreachable)."""
        from .providers.probe import fetch_models

        return await fetch_models(provider_name, self.config, self.providers.auth)

    def fallback_model(self, model: str, exclude: list[str]) -> str | None:
        """Comparable available model for automatic fallback (None = give up)."""
        from .providers.fallback import comparable_model

        return comparable_model(model, self.config, self.providers.auth, exclude)

    def files_read(self, session_id: str) -> set[str]:
        """Paths the session has read (gates edit-before-read, spec §5.4)."""
        return self._files_read.setdefault(session_id, set())

    def is_running(self, session_id: str) -> bool:
        """True while the session has an active run task."""
        handle = self._runs.get(session_id)
        return bool(handle and handle.task and not handle.task.done())

    def queue_depth(self, session_id: str) -> int:
        handle = self._runs.get(session_id)
        return len(handle.queued) if handle else 0

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
            self.bus.emit(QueueUpdated(session_id=session_id, depth=len(self._runs[session_id].queued)))
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
        self.bus.emit(QueueUpdated(session_id=session_id, depth=len(handle.queued)))
        session = await self.store.get_session(session_id)
        message = await self.store.add_message(session_id, "user")
        await self.store.add_part(message.id, "text", {"text": text})
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session)

    async def abort(self, session_id: str) -> None:
        """Stop a session's run (and its children), dropping queued prompts."""
        handle = self._runs.get(session_id)
        if handle:
            handle.queued.clear()
            self.bus.emit(QueueUpdated(session_id=session_id, depth=0))
            handle.abort.set()
            if handle.task and not handle.task.done():
                handle.task.cancel()
        # abort children too
        for child in await self.store.child_sessions(session_id):
            await self.abort(child.id)

    async def compact(self, session_id: str) -> None:
        """Summarize old history into a compaction part to free context."""
        from .compaction import compact_session

        session = await self.store.get_session(session_id)
        await compact_session(self, session)

    # -- permissions & questions ----------------------------------------------

    def policy_for(self, agent: AgentDef) -> PermissionPolicy:
        """Effective permission policy: config + agent overrides + session mode.

        The mode is read live so switching to Full-auto mid-run applies to
        tool calls of runs that started earlier."""
        return PermissionPolicy(
            self.config.permission, agent.permission,
            session_mode=lambda: self.session_mode,
        )

    def set_session_mode(self, mode: str) -> None:
        """Change the session permission mode; Full-auto also releases every
        permission prompt currently waiting on the user."""
        from .permissions import FULL_AUTO

        self.session_mode = mode
        if mode == FULL_AUTO:
            for request_id in list(self._pending_permissions):
                self.answer_permission(request_id, "allow")

    async def ask_permission(
        self, session_id: str, tool: str, arg: str, input: dict, policy: PermissionPolicy
    ) -> str:
        """Emit permission.asked and await the user's verdict; "allow_always"
        persists the pattern to project config and collapses to "allow"."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self._pending_permissions.add(request_id)
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
        """Resolve a pending permission.asked event ("allow"/"allow_always"/"deny")."""
        self._pending_permissions.discard(request_id)
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    async def ask_question(self, session_id: str, question: str, options: list[str]) -> str:
        """Emit question.asked and await the user's chosen/typed answer."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self.bus.emit(QuestionAsked(
            session_id=session_id, request_id=request_id, question=question, options=options
        ))
        return await future

    def answer_question(self, request_id: str, answer: str) -> None:
        """Resolve a pending question.asked event with the user's answer."""
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    # -- subagents (task tool) -------------------------------------------------

    async def run_subagent(
        self, parent: Session, agent: str, prompt: str, description: str, task_id: str | None
    ) -> str:
        """Run a task-tool delegation in a child session and return its final
        text (task.started/finished events drive the crew stage)."""
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
                directory=parent.directory,  # subagents work where the parent works
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

    async def create_project(self, path: str, name: str | None = None):
        return await self.store.create_project(path, name)

    async def list_projects(self):
        return await self.store.list_projects()
