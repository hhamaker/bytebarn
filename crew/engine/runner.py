"""The agent loop (spec §5.3) and history assembly."""

from __future__ import annotations

import asyncio
import datetime
import json
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .agents import AgentDef
from .events import PartUpdated, RunFinished, SessionUpdated, TaskUpdated, TodoUpdated
from .providers.base import (
    Done,
    ErrorEv,
    ModelRequest,
    Msg,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
    stream_with_retry,
)
from .providers.catalog import cost_of
from .store import Session, Store, Todo
from .tools.base import Tool, ToolContext, ToolResult, truncate_output
from .tools.registry import WRITE_TOOLS, build_tools

if TYPE_CHECKING:
    from .facade import Engine

_FLUSH_EVERY = 512  # chars of streamed text between DB writes


async def _git_info(cwd: Path) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            "git branch --show-current 2>/dev/null && git status --porcelain 2>/dev/null | head -5",
            cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode(errors="replace").strip()
    except (OSError, asyncio.TimeoutError):
        return ""


async def build_system_prompt(agent: AgentDef, cwd: Path, instructions: list[str]) -> str:
    git = await _git_info(cwd)
    env = (
        f"<environment>\n"
        f"working directory: {cwd}\n"
        f"platform: {platform.system().lower()}\n"
        f"date: {datetime.date.today().isoformat()}\n"
        + (f"git:\n{git}\n" if git else "")
        + "</environment>"
    )
    sections = [agent.prompt, env]
    for name in instructions:
        path = cwd / name
        if path.is_file():
            sections.append(f"<project-instructions file=\"{name}\">\n{path.read_text()}\n</project-instructions>")
    return "\n\n".join(s for s in sections if s)


def history_to_messages(history: list[tuple[Any, list[Any]]]) -> list[Msg]:
    """Convert stored messages/parts to provider-neutral Msgs.

    Drops everything before the latest compaction summary, which is included
    as a synthetic assistant message.
    """
    compaction_at: tuple[int, int] | None = None
    for mi, (_, parts) in enumerate(history):
        for pi, part in enumerate(parts):
            if part.type == "compaction":
                compaction_at = (mi, pi)

    msgs: list[Msg] = []
    if compaction_at is not None:
        mi, pi = compaction_at
        summary = history[mi][1][pi].data.get("text", "")
        msgs.append(Msg("assistant", [{"type": "text", "text": f"[Conversation summary]\n{summary}"}]))
        history = history[mi + 1 :]

    for message, parts in history:
        if message.role == "user":
            content = []
            for part in parts:
                if part.type == "text":
                    content.append({"type": "text", "text": part.data.get("text", "")})
                elif part.type == "file":
                    content.append({"type": "text", "text": f"[attached file: {part.data.get('path')}]"})
            if content:
                msgs.append(Msg("user", content))
            continue
        # assistant
        content = []
        results = []
        for part in parts:
            if part.type == "text":
                content.append({"type": "text", "text": part.data.get("text", "")})
            elif part.type in ("tool", "task"):
                data = part.data
                call_id = data.get("call_id", part.id)
                content.append({
                    "type": "tool_call", "id": call_id,
                    "name": data.get("tool", "task"), "input": data.get("input", {}),
                })
                results.append({
                    "type": "tool_result", "call_id": call_id,
                    "output": data.get("output", "") or f"[{data.get('status', 'pending')}]",
                    "is_error": data.get("status") == "error",
                })
            # reasoning parts are not replayed
        if content:
            msgs.append(Msg("assistant", content))
        if results:
            msgs.append(Msg("user", results))
    return msgs


def _hard_failure(error: str) -> tuple[bool, bool]:
    """(skip_retry, provider_dead) for an error message.

    Out-of-credit and auth failures poison the whole provider — every model
    there shares the balance/key, so retrying or picking a sibling model is
    wasted. Model-not-found is hard for the model but the provider is fine.
    """
    e = error.lower()
    provider_dead = any(s in e for s in (
        "insufficient balance", "insufficient_quota", "exceeded your current quota",
        "credit", "billing", "payment required", "402",
        "invalid api key", "invalid_api_key", "invalid x-api-key", "401",
    ))
    model_dead = any(s in e for s in (
        "model_not_found", "does not exist", "model not found", "404",
    ))
    return (provider_dead or model_dead, provider_dead)


@dataclass
class RunHandle:
    task: asyncio.Task | None = None
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    queued: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, engine: "Engine"):
        self.engine = engine

    # ------------------------------------------------------------------
    async def run(self, session: Session, handle: RunHandle) -> None:
        engine = self.engine
        store: Store = engine.store
        status = "done"
        try:
            await self._loop(session, handle)
        except asyncio.CancelledError:
            status = "aborted"
            await self._mark_pending_parts_error(session.id)
        except Exception as exc:  # surface, never crash the app
            status = "error"
            await self._append_error(session.id, str(exc))
        finally:
            engine.bus.emit(RunFinished(session_id=session.id, status=status))
            await engine.on_run_finished(session.id)

    # ------------------------------------------------------------------
    async def _loop(self, session: Session, handle: RunHandle) -> None:
        engine = self.engine
        store = engine.store
        agent = engine.agents.get(session.agent)
        model = session.model or agent.model or engine.config.model
        provider, model_id, info = engine.providers.resolve(model)
        policy = engine.policy_for(agent)
        is_subagent = session.parent_session_id is not None

        tools = build_tools(
            agent.tools,
            include_task=not is_subagent,
            subagents=engine.agents.subagent_descriptions(),
        )
        tool_map = {t.name: t for t in tools}
        ctx = self._make_context(session, agent, handle)

        cwd = Path(session.directory) if session.directory else engine.project_dir
        system = await build_system_prompt(agent, cwd, engine.config.instructions)

        # model fallback: after N consecutive failed turns, switch to a
        # comparable available model instead of giving up (spec-free QoL)
        fb_conf = (engine.config.model_extra or {}).get("model_fallback") or {}
        fb_enabled = bool(fb_conf.get("enabled", True))
        fb_after = max(1, int(fb_conf.get("after", 2)))
        failures = 0
        models_tried = [model]
        dead_providers: set[str] = set()

        for _step in range(agent.steps):
            if handle.abort.is_set():
                raise asyncio.CancelledError
            history = await store.session_parts(session.id)
            req = ModelRequest(
                model_id=model_id,
                system=system,
                messages=history_to_messages(history),
                tools=[t.tool_def() for t in tools],
                temperature=agent.temperature,
                top_p=agent.top_p,
                max_tokens=min(info.max_output, 32_000),
            )
            message = await store.add_message(
                session.id, "assistant", model=model_id, provider=provider.name
            )
            outcome = await self._stream_turn(session, message.id, provider, req, model_id, info)
            if outcome["error"]:
                await store.update_message(message.id, error=outcome["error"])
                failures += 1
                skip_retry, provider_dead = _hard_failure(outcome["error"])
                if provider_dead:
                    dead_providers.add(model.split("/", 1)[0])
                if fb_enabled and failures < fb_after and not skip_retry:
                    await self._notice(
                        session, message.id,
                        f"⚠ {model} failed ({failures}/{fb_after}): {outcome['error'][:200]}"
                        " — trying again",
                    )
                    continue
                # pick a stand-in, skipping providers known dead (credits/keys)
                alt = None
                if fb_enabled:
                    exclude = list(models_tried)
                    for _ in range(16):
                        candidate = engine.fallback_model(model, exclude)
                        if candidate is None:
                            break
                        if candidate.split("/", 1)[0] in dead_providers:
                            exclude.append(candidate)
                            continue
                        alt = candidate
                        break
                if alt:
                    why = "is out of credits or unavailable" if skip_retry \
                        else f"failed {failures}×"
                    await self._notice(
                        session, message.id,
                        f"⤷ {model} {why} — switching to comparable"
                        f" model {alt} for the rest of this task",
                    )
                    model = alt
                    models_tried.append(alt)
                    provider, model_id, info = engine.providers.resolve(model)
                    failures = 0
                    # persist: the session now *uses* the working model, so the
                    # next prompt starts there instead of re-walking dead ones
                    await store.update_session(session.id, model=alt)
                    self.engine.bus.emit(SessionUpdated(session_id=session.id))
                    continue
                # no stand-in available: surface the failure in the transcript
                error_text = f"⚠ {outcome['error']}"
                lowered = outcome["error"].lower()
                if "auth" in lowered or "api_key" in lowered or "401" in outcome["error"]:
                    error_text += (
                        f"\n\nModel `{model}` needs credentials — open **⚡ providers**"
                        " in the status bar to connect a provider, then try again."
                    )
                await self._notice(session, message.id, error_text)
                self.engine.bus.emit(SessionUpdated(session_id=session.id))
                return
            failures = 0
            if not outcome["tool_calls"]:
                if not outcome.get("had_text"):
                    # a 200 with no content renders as nothing — say so
                    await self._notice(
                        session, message.id,
                        f"⚠ {model} returned an empty response. If this keeps"
                        " happening, pick a different model — this one may not"
                        " support chat completions.",
                    )
                await self._maybe_compact(session, outcome, info, system)
                return

            await self._execute_tool_calls(session, outcome["tool_calls"], tool_map, policy, ctx)
            await self._maybe_compact(session, outcome, info, system)
        # steps cap reached
        await self._append_error(session.id, f"stopped: reached {agent.steps}-step cap")

    # ------------------------------------------------------------------
    async def _stream_turn(self, session, message_id, provider, req, model_id, info) -> dict:
        """Stream one provider turn, persisting parts incrementally."""
        engine = self.engine
        store = engine.store
        bus = engine.bus

        text_part = None
        text_buf = ""
        flushed = 0
        reasoning_part = None
        reasoning_buf = ""
        calls: dict[str, dict] = {}       # call_id -> {name, json, part_id}
        order: list[str] = []
        usage = (0, 0)
        error = None

        def emit_part(part_id, ptype, data, delta=""):
            bus.emit(PartUpdated(
                session_id=session.id, message_id=message_id, part_id=part_id,
                part_type=ptype, data=data, delta=delta,
            ))

        async for event in stream_with_retry(
            provider, req, on_retry=lambda a, d: self._emit_retry(session)
        ):
            if isinstance(event, TextDelta):
                text_buf += event.text if isinstance(event.text, str) else str(event.text)
                if text_part is None:
                    text_part = await store.add_part(message_id, "text", {"text": ""})
                if len(text_buf) - flushed >= _FLUSH_EVERY:
                    await store.update_part(text_part.id, {"text": text_buf})
                    flushed = len(text_buf)
                emit_part(text_part.id, "text", {"text": text_buf}, delta=event.text)
            elif isinstance(event, ReasoningDelta):
                reasoning_buf += event.text if isinstance(event.text, str) else str(event.text)
                if reasoning_part is None:
                    reasoning_part = await store.add_part(message_id, "reasoning", {"text": ""})
                emit_part(reasoning_part.id, "reasoning", {"text": reasoning_buf}, delta=event.text)
            elif isinstance(event, ToolCallStart):
                calls[event.call_id] = {"name": event.name, "json": "", "part_id": None}
                order.append(event.call_id)
            elif isinstance(event, ToolCallDelta):
                if event.call_id in calls:
                    fragment = event.json_fragment
                    calls[event.call_id]["json"] += (
                        fragment if isinstance(fragment, str) else json.dumps(fragment))
            elif isinstance(event, ToolCallEnd):
                call = calls.get(event.call_id)
                if call is None:
                    continue
                try:
                    input_data = json.loads(call["json"]) if call["json"].strip() else {}
                except json.JSONDecodeError:
                    input_data = {"_raw": call["json"]}
                ptype = "task" if call["name"] == "task" else "tool"
                data = {
                    "tool": call["name"], "call_id": event.call_id, "input": input_data,
                    "output": "", "status": "pending", "title": "", "metadata": {},
                }
                if ptype == "task":
                    data.update({
                        "agent": input_data.get("agent", ""),
                        "description": input_data.get("description", ""),
                        "subagent_session_id": "",
                    })
                part = await store.add_part(message_id, ptype, data)
                call["part_id"] = part.id
                call["data"] = data
                emit_part(part.id, ptype, data)
            elif isinstance(event, Usage):
                usage = (event.tokens_in, event.tokens_out)
            elif isinstance(event, ErrorEv):
                error = event.message
            elif isinstance(event, Done):
                pass

        if text_part is not None:
            await store.update_part(text_part.id, {"text": text_buf})
        if reasoning_part is not None:
            await store.update_part(reasoning_part.id, {"text": reasoning_buf})
        cost = cost_of(model_id, usage[0], usage[1], engine.providers.extra_catalog)
        await store.update_message(message_id, tokens_in=usage[0], tokens_out=usage[1], cost=cost)
        bus.emit(SessionUpdated(session_id=session.id))

        tool_calls = [
            {"call_id": cid, "name": calls[cid]["name"],
             "part_id": calls[cid]["part_id"], "data": calls[cid].get("data", {})}
            for cid in order if calls[cid].get("part_id")
        ]
        return {"tool_calls": tool_calls, "usage": usage, "error": error,
                "message_id": message_id, "had_text": bool(text_buf.strip())}

    # ------------------------------------------------------------------
    async def _execute_tool_calls(self, session, tool_calls, tool_map, policy, ctx) -> None:
        """Permission-check then execute; write tools serialized, rest concurrent."""
        writes = [c for c in tool_calls if c["name"] in WRITE_TOOLS]
        reads = [c for c in tool_calls if c["name"] not in WRITE_TOOLS]

        async def run_one(call):
            await self._run_tool_call(session, call, tool_map, policy, ctx)

        await asyncio.gather(*(run_one(c) for c in reads))
        for call in writes:
            if ctx.abort and ctx.abort.is_set():
                await self._set_call(session, call, "error", "aborted before execution")
                continue
            await run_one(call)

    async def _run_tool_call(self, session, call, tool_map, policy, ctx) -> None:
        engine = self.engine
        name = call["name"]
        tool: Tool | None = tool_map.get(name)
        data = call["data"]

        if tool is None:
            await self._set_call(session, call, "error", f"unknown tool: {name}")
            return
        try:
            params = tool.Params(**data.get("input", {}))
        except ValidationError as exc:
            await self._set_call(session, call, "error", f"invalid parameters: {exc}")
            return

        arg = tool.permission_arg(params)
        verdict = policy.resolve(name, arg)
        if verdict == "ask":
            verdict = await engine.ask_permission(session.id, name, arg, data.get("input", {}), policy)
        if verdict == "deny":
            await self._set_call(session, call, "error", "permission denied by user/policy")
            return

        await self._set_call(session, call, "running", "")
        self._emit_task_detail(session, f"{name} {arg or data.get('input', {}).get('pattern', '')}".strip())
        try:
            result: ToolResult = await tool.execute(params, ctx)
        except Exception as exc:
            await self._set_call(session, call, "error", f"tool crashed: {exc}")
            return
        output, _sidecar = truncate_output(result.output)
        if name == "task" and not result.is_error:
            match = re.search(r"\[task_id: ([0-9a-f]+)\]\s*$", output)
            if match:
                call["data"]["subagent_session_id"] = match.group(1)
        await self._set_call(
            session, call, "error" if result.is_error else "done", output,
            title=result.title, metadata=result.metadata,
        )

    async def _set_call(self, session, call, status, output, title="", metadata=None) -> None:
        data = call["data"]
        data.update({"status": status, "output": output or data.get("output", "")})
        if title:
            data["title"] = title
        if metadata:
            data["metadata"] = metadata
        await self.engine.store.update_part(call["part_id"], data)
        self.engine.bus.emit(PartUpdated(
            session_id=session.id, message_id=call.get("message_id", ""),
            part_id=call["part_id"], part_type="task" if call["name"] == "task" else "tool",
            data=dict(data),
        ))

    # ------------------------------------------------------------------
    def _make_context(self, session: Session, agent: AgentDef, handle: RunHandle) -> ToolContext:
        engine = self.engine

        async def on_todos(items: list[dict[str, str]]) -> None:
            await engine.store.set_todos(session.id, [Todo(i["content"], i["status"]) for i in items])
            engine.bus.emit(TodoUpdated(session_id=session.id, todos=items))

        async def ask_question(question: str, options: list[str]) -> str:
            return await engine.ask_question(session.id, question, options)

        async def run_subagent(agent: str, prompt: str, description: str, task_id: str | None) -> str:
            return await engine.run_subagent(session, agent, prompt, description, task_id)

        session_dir = Path(session.directory) if session.directory else engine.project_dir
        return ToolContext(
            cwd=session_dir,
            session_id=session.id,
            store=engine.store,
            bus=engine.bus,
            files_read=engine.files_read(session.id),
            agent=agent.name,
            ask_question=ask_question,
            run_subagent=run_subagent,
            on_todos=on_todos,
            abort=handle.abort,
        )

    # ------------------------------------------------------------------
    async def _maybe_compact(self, session, outcome, info, system) -> None:
        tokens_in = outcome["usage"][0]
        if tokens_in and tokens_in > 0.85 * info.context_window:
            from .compaction import compact_session

            await compact_session(self.engine, session)

    def _emit_retry(self, session: Session) -> None:
        if session.parent_session_id:
            self.engine.bus.emit(TaskUpdated(
                session_id=session.parent_session_id,
                subagent_session_id=session.id, status="retrying",
            ))

    def _emit_task_detail(self, session: Session, detail: str) -> None:
        if session.parent_session_id:
            self.engine.bus.emit(TaskUpdated(
                session_id=session.parent_session_id,
                subagent_session_id=session.id, status="running", detail=detail[:80],
            ))

    async def _mark_pending_parts_error(self, session_id: str) -> None:
        store = self.engine.store
        for message, parts in await store.session_parts(session_id):
            for part in parts:
                if part.type in ("tool", "task") and part.data.get("status") in ("pending", "running"):
                    part.data["status"] = "error"
                    part.data["output"] = part.data.get("output") or "aborted"
                    await store.update_part(part.id, part.data)
                    self.engine.bus.emit(PartUpdated(
                        session_id=session_id, message_id=message.id, part_id=part.id,
                        part_type=part.type, data=dict(part.data),
                    ))

    async def _notice(self, session, message_id: str, text: str) -> None:
        """Visible transcript note attached to an existing assistant message."""
        part = await self.engine.store.add_part(message_id, "text", {"text": text})
        self.engine.bus.emit(PartUpdated(
            session_id=session.id, message_id=message_id, part_id=part.id,
            part_type="text", data=part.data,
        ))

    async def _append_error(self, session_id: str, text: str) -> None:
        store = self.engine.store
        message = await store.add_message(session_id, "assistant", error=text)
        part = await store.add_part(message.id, "text", {"text": f"⚠ {text}"})
        self.engine.bus.emit(PartUpdated(
            session_id=session_id, message_id=message.id, part_id=part.id,
            part_type="text", data=part.data,
        ))
