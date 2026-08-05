"""In-process fake Claude Code runtime for tests (no CLI binary)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from ..events import RunFinished, SessionActivity, SessionUpdated
from ..runner import RunHandle
from ..store import Session
from .claude_code import (
    ClaudeCodeRuntime,
    ProjectionState,
    load_claude_session_id,
    project_claude_event,
    save_claude_session_id,
)

if TYPE_CHECKING:
    from ..facade import Engine

TurnScript = list[dict[str, Any]] | Callable[[str], list[dict[str, Any]] | Any]


class FakeClaudeCodeRuntime(ClaudeCodeRuntime):
    """Same ``run(session, handle)`` contract as ClaudeCodeRuntime, no process.

    ``script`` is a list of turns. Each turn is either:
    - a list of stream-json event dicts, or
    - a callable ``(prompt: str) -> list[dict]`` / async callable.

    Events are projected through the same helpers as the real runtime.
    """

    def __init__(
        self,
        engine: "Engine",
        script: list[TurnScript] | None = None,
    ):
        super().__init__(engine)
        self.script: list[TurnScript] = list(script or [])
        self.prompts: list[str] = []
        self._turn_idx = 0
        # Optional gate: tests can wait on this after N events
        self.events_seen = 0
        self.hang_before_event: int | None = None  # if set, wait on abort at this index

    def push_turn(self, events: TurnScript) -> None:
        self.script.append(events)

    async def run(self, session: Session, handle: RunHandle) -> None:
        engine = self.engine
        status = "done"
        engine.checkpoints.begin(session.id)
        try:
            status = await self._run_turn(session, handle)
            if handle.abort.is_set():
                status = "aborted"
                await self._mark_pending_parts_error(session.id)
        except asyncio.CancelledError:
            status = "aborted"
            await self._mark_pending_parts_error(session.id)
        except Exception as exc:
            status = "error"
            await self._append_error(session.id, str(exc))
        finally:
            engine.checkpoints.finish(session.id)
            await engine.on_run_finished(session.id)
            engine.bus.emit(RunFinished(session_id=session.id, status=status))

    async def _run_turn(self, session: Session, handle: RunHandle) -> str:
        engine = self.engine
        store = engine.store
        live = await store.get_session(session.id) or session
        prompt = await self._latest_user_text(live.id)
        if not prompt:
            raise RuntimeError("no user prompt to send to Claude Code")
        self.prompts.append(prompt)

        model_label = live.model or "claude-code"
        message = await store.add_message(
            live.id, "assistant", model=model_label, provider="claude-code",
        )
        engine.bus.emit(SessionUpdated(session_id=live.id))
        engine.bus.emit(SessionActivity(session_id=live.id, detail="claude-code…"))

        # Resume map is still consulted so tests can assert persistence
        _ = load_claude_session_id(engine.project_dir, live.id)

        events = await self._next_events(prompt)
        state = ProjectionState()
        for i, event in enumerate(events):
            if handle.abort.is_set():
                raise asyncio.CancelledError
            if self.hang_before_event is not None and i >= self.hang_before_event:
                await handle.abort.wait()
                raise asyncio.CancelledError
            if not isinstance(event, dict):
                continue
            await project_claude_event(engine, live, message.id, event, state)
            self.events_seen += 1
            await asyncio.sleep(0)

        if handle.abort.is_set():
            raise asyncio.CancelledError

        if state.claude_session_id:
            save_claude_session_id(
                engine.project_dir, live.id, state.claude_session_id,
            )

        updates: dict[str, Any] = {}
        if state.tokens_in or state.tokens_out:
            updates["tokens_in"] = state.tokens_in
            updates["tokens_out"] = state.tokens_out
        if state.cost:
            updates["cost"] = state.cost
        if state.result_is_error:
            updates["error"] = (
                state.result_subtype or state.result_text or "claude-code error"
            )
        if updates:
            await store.update_message(message.id, **updates)

        if state.result_is_error:
            if not (state.text_buf or "").strip():
                await self._append_error_to_message(
                    live.id, message.id,
                    state.result_text or state.result_subtype or "claude-code error",
                )
            engine.bus.emit(SessionUpdated(session_id=live.id))
            engine.bus.emit(SessionActivity(session_id=live.id, detail=""))
            return "error"

        engine.bus.emit(SessionUpdated(session_id=live.id))
        engine.bus.emit(SessionActivity(session_id=live.id, detail=""))
        return "done"

    async def _next_events(self, prompt: str) -> list[dict[str, Any]]:
        if self._turn_idx >= len(self.script):
            # Default empty success so tests don't hang
            return [
                {"type": "system", "subtype": "init", "session_id": "fake-default"},
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_default",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "(no scripted turn)"}],
                    },
                    "session_id": "fake-default",
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "(no scripted turn)",
                    "session_id": "fake-default",
                },
            ]
        turn = self.script[self._turn_idx]
        self._turn_idx += 1
        if callable(turn):
            out = turn(prompt)
            if asyncio.iscoroutine(out):
                out = await out
            return list(out or [])
        return list(turn)
