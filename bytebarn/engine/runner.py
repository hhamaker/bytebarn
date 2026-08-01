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
from .events import (
    PartUpdated,
    RunFinished,
    SessionActivity,
    SessionUpdated,
    TaskUpdated,
    TodoUpdated,
)
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
from .store import Session, Todo
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


_ASSET_INLINE_LIMIT = 32_000  # bytes of a text asset inlined into the prompt


async def build_system_prompt(
    agent: AgentDef,
    cwd: Path,
    instructions: list[str],
    project_instructions: str = "",
    assets: list[Any] | None = None,
    memory: list[tuple[str, str]] | None = None,
    skills_catalog: str = "",
) -> str:
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
    # Claude-style project knowledge: custom instructions + uploaded assets
    if project_instructions.strip():
        sections.append(
            "<project-instructions source=\"project\">\n"
            f"{project_instructions}\n</project-instructions>")
    for asset in assets or []:
        sections.append(_asset_section(asset))
    if memory is not None:
        sections.append(
            "<project-memory-guide>\n"
            "This project has persistent memory (an OKF markdown bundle) that"
            " survives across sessions. Its current contents follow. When you"
            " learn something durable — a decision, an architecture fact, a"
            " user preference, a gotcha — save it with the memory tool so"
            " future sessions keep that context. Update stale entries instead"
            " of duplicating them.\n</project-memory-guide>")
        for rel, text in memory:
            sections.append(f"<project-memory file=\"{rel}\">\n{text}\n</project-memory>")
    if skills_catalog.strip():
        sections.append(skills_catalog.strip())
    return "\n\n".join(s for s in sections if s)


def load_memory(memory_dir: Path, limit: int = _ASSET_INLINE_LIMIT) -> list[tuple[str, str]]:
    """Read a project's OKF memory bundle for prompt injection.

    Returns (bundle-relative path, content) pairs — concepts first, log.md
    last. Oversized or unreadable files are listed by name only."""
    out: list[tuple[str, str]] = []
    if not memory_dir.is_dir():
        return out
    files = sorted(p for p in memory_dir.rglob("*.md") if p.name != "log.md")
    log = memory_dir / "log.md"
    if log.is_file():
        files.append(log)
    for path in files:
        rel = path.relative_to(memory_dir).as_posix()
        try:
            if path.stat().st_size <= limit:
                out.append((rel, path.read_text().strip()))
                continue
        except (OSError, UnicodeDecodeError):
            pass
        out.append((rel, "[too large to inline — read with tools if relevant]"))
    return out


def _asset_section(asset: Any) -> str:
    """Inline small text assets; larger/binary ones are listed by path."""
    path = Path(asset.path)
    try:
        if path.stat().st_size <= _ASSET_INLINE_LIMIT:
            text = path.read_text()  # UnicodeDecodeError -> binary fallback
            return (f"<project-knowledge file=\"{asset.name}\">\n"
                    f"{text}\n</project-knowledge>")
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    return (f"<project-knowledge file=\"{asset.name}\" path=\"{asset.path}\">"
            "[not inlined — read this file with tools when relevant]"
            "</project-knowledge>")


_IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}
_IMAGE_CAP = 4 * 1024 * 1024  # bigger inline images blow the request size


def _file_content(path: str) -> dict[str, Any]:
    """A user "file" part as provider content: inline images, name others."""
    import base64
    from pathlib import Path as _Path

    p = _Path(path)
    media = _IMAGE_MEDIA.get(p.suffix.lower())
    if media:
        try:
            raw = p.read_bytes()
            if len(raw) <= _IMAGE_CAP:
                return {"type": "image", "media_type": media,
                        "data": base64.b64encode(raw).decode()}
        except OSError:
            pass
    return {"type": "text", "text": f"[attached file: {path}]"}


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
                    content.append(_file_content(part.data.get("path", "")))
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
    # a trailing assistant message (partial text from a mid-stream failure)
    # reads as "prefill" to the API, which newer Claude models reject —
    # close the conversation with a user turn so retries stay valid
    if msgs and msgs[-1].role == "assistant":
        msgs.append(Msg("user", [{
            "type": "text",
            "text": "[Your previous reply was cut off mid-stream. Continue"
                    " from where it stopped — or restart the reply cleanly"
                    " if that reads better.]",
        }]))
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
        # Claude subscription window exhausted (overage disabled): every
        # further request fails until the window resets — switch providers
        "out of extra usage",
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
        status = "done"
        engine.checkpoints.begin(session.id)
        try:
            await self._loop(session, handle)
        except asyncio.CancelledError:
            status = "aborted"
            await self._mark_pending_parts_error(session.id)
        except Exception as exc:  # surface, never crash the app
            status = "error"
            await self._append_error(session.id, str(exc))
        finally:
            engine.checkpoints.finish(session.id)
            # Promote any queued prompt first so RunFinished observers see an
            # already-running next turn (header / thinking indicator stay live).
            await engine.on_run_finished(session.id)
            engine.bus.emit(RunFinished(session_id=session.id, status=status))

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
            skill_registry=engine.skills,
        )
        tools += engine.mcp.tools_for(agent.tools)
        tool_map = {t.name: t for t in tools}
        ctx = self._make_context(session, agent, handle)

        cwd = Path(session.directory) if session.directory else engine.project_dir
        proj_instructions, proj_assets = await engine.project_knowledge(session.project_id)
        from .skills import catalog_section
        skills_catalog = catalog_section(engine.skills.list())
        system = await build_system_prompt(
            agent, cwd, engine.config.instructions,
            project_instructions=proj_instructions, assets=proj_assets,
            memory=load_memory(engine.memory_dir(session.project_id)),
            skills_catalog=skills_catalog)
        from .permissions import PLAN, PLAN_MODE_NOTICE
        if engine.session_mode == PLAN:
            system = f"{system}\n\n{PLAN_MODE_NOTICE}"

        # model fallback: after N consecutive failed turns, switch to a
        # comparable available model instead of giving up (spec-free QoL)
        fb_conf = (engine.config.model_extra or {}).get("model_fallback") or {}
        fb_enabled = bool(fb_conf.get("enabled", True))
        fb_after = max(1, int(fb_conf.get("after", 2)))
        rl_wait_base = float(fb_conf.get("rate_limit_wait", 20.0))
        # output-token reservation: subscription rate limiting reserves the
        # full max_tokens up front, so a greedy 32k ask gets rejected when
        # the usage window is half full — 16k is plenty for coding turns
        max_out = int((engine.config.model_extra or {}).get("max_tokens", 16_000))
        failures = 0
        rate_limit_waits = 0
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
                thinking=agent.thinking,
                max_tokens=min(info.max_output, max_out),
            )
            message = await store.add_message(
                session.id, "assistant", model=model_id, provider=provider.name
            )
            outcome = await self._stream_turn(session, message.id, provider, req, model_id, info)
            if outcome["error"]:
                await store.update_message(message.id, error=outcome["error"])
                lowered_err = outcome["error"].lower()
                # rate limits are transient by definition (often a per-minute
                # account window shared with other Claude apps) — wait them
                # out patiently instead of burning fallback strikes
                if ("rate_limit" in lowered_err or "429" in outcome["error"]) \
                        and rate_limit_waits < 3:
                    rate_limit_waits += 1
                    wait_s = min(rl_wait_base * rate_limit_waits, 90.0)
                    await self._notice(
                        session, message.id,
                        f"⏳ {model} is rate-limited — waiting {wait_s:.0f}s and"
                        f" retrying ({rate_limit_waits}/3). This is the account's"
                        " per-minute limit, shared with any other Claude apps"
                        " running right now.",
                    )
                    try:  # abort-aware wait: Esc still stops the run instantly
                        await asyncio.wait_for(handle.abort.wait(), timeout=wait_s)
                        raise asyncio.CancelledError
                    except asyncio.TimeoutError:
                        pass
                    continue
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
                    if "out of extra usage" in outcome["error"].lower():
                        why = ("hit your Claude plan's usage limit (it refills"
                               " on the next usage window)")
                    elif skip_retry:
                        why = "is out of credits or unavailable"
                    else:
                        why = f"failed {failures}×"
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
            rate_limit_waits = 0
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
            # Copy data so later in-place mutations (tool status, growing
            # text buffer) don't rewrite events already sitting on the bus.
            bus.emit(PartUpdated(
                session_id=session.id, message_id=message_id, part_id=part_id,
                part_type=ptype, data=dict(data), delta=delta,
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
                # Show the tool card immediately so the UI isn't silent while
                # args stream in (large write/edit payloads can take seconds).
                ptype = "task" if event.name == "task" else "tool"
                data = {
                    "tool": event.name, "call_id": event.call_id, "input": {},
                    "output": "", "status": "pending", "title": "", "metadata": {},
                }
                if ptype == "task":
                    data.update({
                        "agent": "", "description": "", "subagent_session_id": "",
                    })
                part = await store.add_part(message_id, ptype, data)
                calls[event.call_id] = {
                    "name": event.name, "json": "", "part_id": part.id, "data": data,
                }
                order.append(event.call_id)
                emit_part(part.id, ptype, data)
                bus.emit(SessionActivity(
                    session_id=session.id, detail=f"{event.name}…"))
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
                data = call.get("data") or {
                    "tool": call["name"], "call_id": event.call_id, "input": {},
                    "output": "", "status": "pending", "title": "", "metadata": {},
                }
                data["input"] = input_data
                data["status"] = "pending"
                data["call_id"] = event.call_id
                data["tool"] = call["name"]
                if ptype == "task":
                    data["agent"] = input_data.get("agent", "")
                    data["description"] = input_data.get("description", "")
                    data.setdefault("subagent_session_id", "")
                call["data"] = data
                if call.get("part_id"):
                    await store.update_part(call["part_id"], data)
                    emit_part(call["part_id"], ptype, data)
                else:
                    part = await store.add_part(message_id, ptype, data)
                    call["part_id"] = part.id
                    emit_part(part.id, ptype, data)
                summary = (
                    data.get("title")
                    or input_data.get("command")
                    or input_data.get("path")
                    or input_data.get("pattern")
                    or input_data.get("description")
                    or ""
                )
                detail = f"{call['name']}"
                if summary:
                    detail = f"{call['name']}: {str(summary)[:60]}"
                bus.emit(SessionActivity(session_id=session.id, detail=detail))
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
            from .permissions import PLAN
            if policy.session_mode == PLAN:
                reason = (
                    "blocked by Plan mode (read-only explore) — "
                    "switch to Ask or Full-auto to implement"
                )
            else:
                reason = "permission denied by user/policy"
            await self._set_call(session, call, "error", reason)
            return

        # snapshot the pre-write state so the run can be reviewed/reverted
        if name in ("write", "edit") and getattr(params, "path", ""):
            engine.checkpoints.snapshot(session.id, ctx.resolve_path(params.path))

        await self._set_call(session, call, "running", "")
        detail = f"{name} {arg or data.get('input', {}).get('pattern', '')}".strip()
        self._emit_task_detail(session, detail)
        self.engine.bus.emit(SessionActivity(
            session_id=session.id, detail=(detail[:80] or f"{name}…")))
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
            memory_dir=engine.memory_dir(session.project_id),
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
        self.engine.bus.emit(SessionActivity(
            session_id=session.id, detail="retrying…"))

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
