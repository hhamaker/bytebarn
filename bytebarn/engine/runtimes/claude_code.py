"""Claude Code CLI runtime — headless ``claude -p --output-format stream-json``.

One Claude Code process per ByteBarn session run. Follow-up turns resume the
external Claude session via ``--resume`` and a small on-disk id map under
``.bytebarn/claude_code_sessions.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Iterable

from ..events import PartUpdated, RunFinished, SessionActivity, SessionUpdated
from ..runner import RunHandle, resolve_cwd
from ..store import Session
from .process import kill_process_group

if TYPE_CHECKING:
    from ..facade import Engine

log = logging.getLogger(__name__)

_SESSIONS_FILE = "claude_code_sessions.json"
_FLUSH_EVERY = 512


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ClaudeCodeConfig:
    command: str = "claude"
    extra_args: list[str] = field(default_factory=list)
    permission_mode: str = "acceptEdits"
    allowed_tools: str = "Read,Edit,Write,Glob,Grep,Bash"
    max_turns: int | None = None
    include_partial_messages: bool = True
    bare: bool = False
    model: str | None = None  # override; else session.model stripped of provider/


def claude_code_config_from_engine(config: Any) -> ClaudeCodeConfig:
    """Read ``claude_code`` block from engine config (model_extra or attr)."""
    extra = getattr(config, "model_extra", None) or {}
    raw = extra.get("claude_code") if isinstance(extra, dict) else None
    if raw is None:
        raw = getattr(config, "claude_code", None)
    if not isinstance(raw, dict):
        raw = {}

    max_turns = raw.get("max_turns", None)
    if max_turns is not None:
        try:
            max_turns = int(max_turns)
        except (TypeError, ValueError):
            max_turns = None

    extra_args = raw.get("extra_args") or []
    if isinstance(extra_args, str):
        extra_args = extra_args.split()
    else:
        extra_args = [str(a) for a in extra_args]

    model = raw.get("model")
    if model is not None:
        model = str(model) or None

    return ClaudeCodeConfig(
        command=str(raw.get("command") or "claude"),
        extra_args=extra_args,
        permission_mode=str(raw.get("permission_mode") or "acceptEdits"),
        allowed_tools=str(
            raw.get("allowed_tools") or "Read,Edit,Write,Glob,Grep,Bash"
        ),
        max_turns=max_turns,
        include_partial_messages=bool(raw.get("include_partial_messages", True)),
        bare=bool(raw.get("bare", False)),
        model=model,
    )


def model_for_cli(session_model: str, override: str | None = None) -> str | None:
    """CLI ``--model`` value: strip ``provider/`` prefix when present.

    UI sentinel ``claude-code/default`` (and bare ``default``) means "let the
    CLI pick" — omit ``--model``.
    """
    raw = (override if override is not None else session_model) or ""
    raw = str(raw).strip()
    if not raw:
        return None
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1].strip()
    if not raw or raw.lower() in {"default", "auto", "claude-code"}:
        return None
    return raw


def build_claude_argv(
    prompt: str,
    cfg: ClaudeCodeConfig,
    *,
    session_model: str = "",
    resume_id: str | None = None,
) -> list[str]:
    """Build argv for a headless Claude Code turn (no executable lookup)."""
    argv = [
        cfg.command,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        cfg.permission_mode,
        "--allowedTools",
        cfg.allowed_tools,
    ]
    if cfg.include_partial_messages:
        argv.append("--include-partial-messages")
    if cfg.max_turns is not None:
        argv.extend(["--max-turns", str(cfg.max_turns)])
    if cfg.bare:
        argv.append("--bare")
    model = model_for_cli(session_model, cfg.model)
    if model:
        argv.extend(["--model", model])
    if resume_id:
        argv.extend(["--resume", resume_id])
    argv.extend(cfg.extra_args)
    return argv


# ---------------------------------------------------------------------------
# External session id cache (ByteBarn session → Claude session)
# ---------------------------------------------------------------------------


def sessions_cache_path(project_dir: Path) -> Path:
    return Path(project_dir) / ".bytebarn" / _SESSIONS_FILE


def load_claude_session_id(project_dir: Path, bytebarn_session_id: str) -> str | None:
    path = sessions_cache_path(project_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get(bytebarn_session_id)
    return str(val) if val else None


def save_claude_session_id(
    project_dir: Path, bytebarn_session_id: str, claude_session_id: str,
) -> None:
    if not claude_session_id:
        return
    path = sessions_cache_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        data[bytebarn_session_id] = claude_session_id
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        log.debug("could not persist claude session id: %s", exc)


# ---------------------------------------------------------------------------
# Event projection (shared by real + fake runtimes)
# ---------------------------------------------------------------------------


def _tool_output_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        return json.dumps(content)
    return str(content)


def _text_delta_from_stream_event(event: dict[str, Any]) -> str | None:
    """Extract assistant text delta from a ``stream_event`` line, if any."""
    inner = event.get("event")
    if not isinstance(inner, dict):
        return None
    # Anthropic-style: event.type == content_block_delta, delta.type == text_delta
    delta = inner.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "text_delta":
        text = delta.get("text")
        return text if isinstance(text, str) else (str(text) if text is not None else None)
    # Nested under message / content_block occasionally
    if inner.get("type") == "content_block_delta":
        d2 = inner.get("delta") or {}
        if isinstance(d2, dict) and d2.get("type") == "text_delta":
            text = d2.get("text")
            return text if isinstance(text, str) else None
    return None


@dataclass
class ProjectionState:
    """Mutable projection state for one assistant message turn."""

    claude_session_id: str | None = None
    text_part_id: str | None = None
    text_buf: str = ""
    text_flushed: int = 0
    used_partials: bool = False
    tool_parts: dict[str, str] = field(default_factory=dict)  # call_id → part_id
    tool_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    result_is_error: bool = False
    result_text: str | None = None
    result_subtype: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    had_error_event: bool = False


async def project_claude_event(
    engine: "Engine",
    session: Session,
    message_id: str,
    event: dict[str, Any],
    state: ProjectionState,
) -> None:
    """Project one Claude Code stream-json event into store parts + bus events."""
    store = engine.store
    bus = engine.bus
    etype = event.get("type")

    def emit(part_id: str, ptype: str, data: dict[str, Any], delta: str = "") -> None:
        bus.emit(PartUpdated(
            session_id=session.id,
            message_id=message_id,
            part_id=part_id,
            part_type=ptype,
            data=dict(data),
            delta=delta,
        ))

    if etype == "system":
        sid = event.get("session_id")
        if sid:
            state.claude_session_id = str(sid)
        return

    if etype == "stream_event":
        delta = _text_delta_from_stream_event(event)
        if not delta:
            return
        state.used_partials = True
        state.text_buf += delta
        if state.text_part_id is None:
            part = await store.add_part(message_id, "text", {"text": ""})
            state.text_part_id = part.id
        if len(state.text_buf) - state.text_flushed >= _FLUSH_EVERY:
            await store.update_part(state.text_part_id, {"text": state.text_buf})
            state.text_flushed = len(state.text_buf)
        emit(state.text_part_id, "text", {"text": state.text_buf}, delta=delta)
        return

    if etype == "assistant":
        # Prefer top-level parent_tool_use_id — skip nested subagent chatter
        if event.get("parent_tool_use_id"):
            return
        msg = event.get("message") or {}
        if isinstance(msg, dict):
            sid = event.get("session_id") or msg.get("session_id")
            if sid:
                state.claude_session_id = str(sid)
            usage = msg.get("usage") or {}
            if isinstance(usage, dict):
                try:
                    state.tokens_in = int(usage.get("input_tokens") or state.tokens_in)
                    state.tokens_out = int(usage.get("output_tokens") or state.tokens_out)
                except (TypeError, ValueError):
                    pass
            content = msg.get("content") or []
        else:
            content = []
        if not isinstance(content, list):
            content = []

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if not isinstance(text, str):
                    text = str(text)
                # Authoritative full text: update or create once; avoid double-print
                if state.text_part_id is None:
                    part = await store.add_part(message_id, "text", {"text": text})
                    state.text_part_id = part.id
                    state.text_buf = text
                    state.text_flushed = len(text)
                    emit(part.id, "text", {"text": text})
                else:
                    # Prefer final text over partial stream buffer
                    state.text_buf = text
                    await store.update_part(state.text_part_id, {"text": text})
                    state.text_flushed = len(text)
                    emit(state.text_part_id, "text", {"text": text})
            elif btype == "tool_use":
                call_id = str(block.get("id") or "")
                name = str(block.get("name") or "tool")
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                if not call_id:
                    continue
                if call_id in state.tool_parts:
                    # Update existing card (args may finalize on a later assistant event)
                    data = state.tool_data.get(call_id) or {}
                    data["input"] = inp or data.get("input") or {}
                    data["tool"] = name
                    await store.update_part(state.tool_parts[call_id], data)
                    emit(state.tool_parts[call_id], "tool", data)
                    continue
                data = {
                    "tool": name,
                    "call_id": call_id,
                    "input": inp or {},
                    "output": "",
                    "status": "running",
                    "title": "",
                    "metadata": {},
                }
                part = await store.add_part(message_id, "tool", data)
                state.tool_parts[call_id] = part.id
                state.tool_data[call_id] = data
                emit(part.id, "tool", data)
                summary = (
                    inp.get("command")
                    or inp.get("file_path")
                    or inp.get("path")
                    or inp.get("pattern")
                    or inp.get("description")
                    or ""
                )
                detail = name if not summary else f"{name}: {str(summary)[:60]}"
                bus.emit(SessionActivity(session_id=session.id, detail=detail))
        return

    if etype == "user":
        if event.get("parent_tool_use_id"):
            return
        msg = event.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            call_id = str(block.get("tool_use_id") or "")
            if not call_id or call_id not in state.tool_parts:
                continue
            part_id = state.tool_parts[call_id]
            data = dict(state.tool_data.get(call_id) or {})
            data["output"] = _tool_output_text(block.get("content"))
            data["status"] = "error" if block.get("is_error") else "done"
            data["call_id"] = call_id
            state.tool_data[call_id] = data
            await store.update_part(part_id, data)
            emit(part_id, "tool", data)
        return

    if etype == "result":
        sid = event.get("session_id")
        if sid:
            state.claude_session_id = str(sid)
        state.result_is_error = bool(event.get("is_error"))
        state.result_subtype = str(event.get("subtype") or "")
        result_text = event.get("result")
        if isinstance(result_text, str):
            state.result_text = result_text
        usage = event.get("usage") or {}
        if isinstance(usage, dict):
            try:
                state.tokens_in = int(usage.get("input_tokens") or state.tokens_in)
                state.tokens_out = int(usage.get("output_tokens") or state.tokens_out)
            except (TypeError, ValueError):
                pass
        cost = event.get("total_cost_usd")
        if cost is not None:
            try:
                state.cost = float(cost)
            except (TypeError, ValueError):
                pass
        # If we never got assistant text, surface result text
        if state.text_part_id is None and state.result_text:
            part = await store.add_part(
                message_id, "text", {"text": state.result_text},
            )
            state.text_part_id = part.id
            state.text_buf = state.result_text
            emit(part.id, "text", {"text": state.result_text})
        elif state.text_part_id is not None and state.text_buf:
            await store.update_part(state.text_part_id, {"text": state.text_buf})
        return

    # unknown types: ignore (forward compatible)


async def project_claude_events(
    engine: "Engine",
    session: Session,
    message_id: str,
    events: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
    abort: asyncio.Event | None = None,
) -> ProjectionState:
    """Project a stream/iterable of Claude events; stop early if abort is set."""
    state = ProjectionState()
    if hasattr(events, "__aiter__"):
        async for event in events:  # type: ignore[union-attr]
            if abort is not None and abort.is_set():
                break
            if not isinstance(event, dict):
                continue
            await project_claude_event(engine, session, message_id, event, state)
            await asyncio.sleep(0)
    else:
        for event in events:  # type: ignore[union-attr]
            if abort is not None and abort.is_set():
                break
            if not isinstance(event, dict):
                continue
            await project_claude_event(engine, session, message_id, event, state)
            await asyncio.sleep(0)
    return state


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class ClaudeCodeRuntime:
    """Drive Claude Code CLI as a hidden backend for one Engine."""

    def __init__(self, engine: "Engine"):
        self.engine = engine
        # test seam: async (argv, cwd) -> Process-like with stdout/stderr/wait/pid
        self._spawn_fn: Callable[..., Any] | None = None

    def _config(self) -> ClaudeCodeConfig:
        return claude_code_config_from_engine(self.engine.config)

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
        cfg = self._config()

        # Refresh session in case model/directory changed
        live = await store.get_session(session.id) or session
        prompt = await self._latest_user_text(live.id)
        if not prompt:
            raise RuntimeError("no user prompt to send to Claude Code")

        model_label = live.model or cfg.model or "claude-code"
        message = await store.add_message(
            live.id, "assistant", model=model_label, provider="claude-code",
        )
        engine.bus.emit(SessionUpdated(session_id=live.id))
        engine.bus.emit(SessionActivity(session_id=live.id, detail="claude-code…"))

        cwd, cwd_warning = resolve_cwd(live, engine.project_dir)
        if cwd_warning:
            part = await store.add_part(message.id, "text", {"text": cwd_warning})
            engine.bus.emit(PartUpdated(
                session_id=live.id, message_id=message.id, part_id=part.id,
                part_type="text", data=part.data,
            ))

        resume_id = load_claude_session_id(engine.project_dir, live.id)
        argv = build_claude_argv(
            prompt, cfg, session_model=live.model or "", resume_id=resume_id,
        )

        if handle.abort.is_set():
            raise asyncio.CancelledError

        proc = await self._spawn(argv, cwd)
        # Tee process I/O into the terminal hub so the UI can show the
        # underlying Claude Code stream without stealing stdout.
        hub = getattr(engine, "terminals", None)
        term_id = f"cc:{live.id}"
        if hub is not None:
            try:
                hub.open(
                    kind="claude-code",
                    title=f"Claude Code · {(live.title or live.id[:8])}",
                    session_id=live.id,
                    cwd=str(cwd),
                    pid=getattr(proc, "pid", None),
                    interactive=False,
                    terminal_id=term_id,
                )
                hub.append(
                    term_id,
                    f"$ {' '.join(argv[:6])}{'…' if len(argv) > 6 else ''}\n",
                )
            except Exception:
                log.debug("terminal hub open failed", exc_info=True)
                term_id = ""
        else:
            term_id = ""

        state = ProjectionState()
        stdout_task = asyncio.create_task(
            self._read_and_project(
                proc, engine, live, message.id, state, handle, term_id),
        )
        stderr_task = asyncio.create_task(self._drain_stderr(proc, engine, term_id))
        abort_task = asyncio.create_task(self._watch_abort(proc, handle))

        try:
            await stdout_task
        finally:
            abort_task.cancel()
            try:
                await abort_task
            except asyncio.CancelledError:
                pass
            # Ensure process is gone
            await kill_process_group(proc)
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                pass
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
            if hub is not None and term_id:
                try:
                    hub.close(term_id, exit_code=proc.returncode)
                except Exception:
                    pass

        if handle.abort.is_set():
            raise asyncio.CancelledError

        # Persist external session mapping for --resume
        if state.claude_session_id:
            save_claude_session_id(
                engine.project_dir, live.id, state.claude_session_id,
            )

        # Usage / cost on the assistant message
        updates: dict[str, Any] = {}
        if state.tokens_in or state.tokens_out:
            updates["tokens_in"] = state.tokens_in
            updates["tokens_out"] = state.tokens_out
        if state.cost:
            updates["cost"] = state.cost
        if state.result_is_error:
            err = state.result_subtype or state.result_text or "claude-code error"
            updates["error"] = err
        if updates:
            await store.update_message(message.id, **updates)

        rc = proc.returncode
        if state.result_is_error:
            # Surface error text if we have nothing useful yet
            if not (state.text_buf or "").strip():
                await self._append_error_to_message(
                    live.id, message.id,
                    state.result_text or state.result_subtype or "claude-code error",
                )
            engine.bus.emit(SessionUpdated(session_id=live.id))
            engine.bus.emit(SessionActivity(session_id=live.id, detail=""))
            return "error"
        if rc not in (0, None) and not state.result_text and not state.text_buf:
            raise RuntimeError(f"claude-code exited with code {rc}")

        engine.bus.emit(SessionUpdated(session_id=live.id))
        engine.bus.emit(SessionActivity(session_id=live.id, detail=""))
        return "done"

    async def _spawn(self, argv: list[str], cwd: Path) -> asyncio.subprocess.Process:
        if self._spawn_fn is not None:
            return await self._spawn_fn(argv, cwd)
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            start_new_session=True,
        )

    async def _read_and_project(
        self,
        proc: asyncio.subprocess.Process,
        engine: "Engine",
        session: Session,
        message_id: str,
        state: ProjectionState,
        handle: RunHandle,
        term_id: str = "",
    ) -> None:
        assert proc.stdout is not None
        hub = getattr(engine, "terminals", None)
        while True:
            if handle.abort.is_set():
                break
            try:
                line_b = await proc.stdout.readline()
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                break
            if not line_b:
                break
            raw = line_b.decode("utf-8", errors="replace")
            if hub is not None and term_id:
                try:
                    hub.append(term_id, raw if raw.endswith("\n") else raw + "\n")
                except Exception:
                    pass
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.debug("claude-code non-json line: %s", line[:200])
                continue
            if not isinstance(event, dict):
                continue
            await project_claude_event(engine, session, message_id, event, state)

    async def _drain_stderr(
        self,
        proc: asyncio.subprocess.Process,
        engine: "Engine | None" = None,
        term_id: str = "",
    ) -> None:
        if proc.stderr is None:
            return
        hub = getattr(engine, "terminals", None) if engine is not None else None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace")
                log.debug("claude-code stderr: %s", text.rstrip())
                if hub is not None and term_id:
                    try:
                        hub.append(
                            term_id,
                            text if text.endswith("\n") else text + "\n",
                            stream="stderr",
                        )
                    except Exception:
                        pass
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            return
    async def _watch_abort(
        self, proc: asyncio.subprocess.Process, handle: RunHandle,
    ) -> None:
        await handle.abort.wait()
        await kill_process_group(proc)

    async def _latest_user_text(self, session_id: str) -> str:
        store = self.engine.store
        messages = await store.list_messages(session_id)
        for msg in reversed(messages):
            if msg.role != "user":
                continue
            parts = await store.list_parts(msg.id)
            text = "\n".join(
                p.data.get("text", "") for p in parts if p.type == "text"
            ).strip()
            if text:
                return text
        return ""

    async def _mark_pending_parts_error(self, session_id: str) -> None:
        store = self.engine.store
        for message, parts in await store.session_parts(session_id):
            for part in parts:
                if part.type in ("tool", "task") and part.data.get("status") in (
                    "pending", "running",
                ):
                    part.data["status"] = "error"
                    part.data["output"] = part.data.get("output") or "aborted"
                    await store.update_part(part.id, part.data)
                    self.engine.bus.emit(PartUpdated(
                        session_id=session_id,
                        message_id=message.id,
                        part_id=part.id,
                        part_type=part.type,
                        data=dict(part.data),
                    ))

    async def _append_error(self, session_id: str, text: str) -> None:
        store = self.engine.store
        message = await store.add_message(session_id, "assistant", error=text)
        part = await store.add_part(message.id, "text", {"text": f"⚠ {text}"})
        self.engine.bus.emit(PartUpdated(
            session_id=session_id,
            message_id=message.id,
            part_id=part.id,
            part_type="text",
            data=part.data,
        ))

    async def _append_error_to_message(
        self, session_id: str, message_id: str, text: str,
    ) -> None:
        part = await self.engine.store.add_part(
            message_id, "text", {"text": f"⚠ {text}"},
        )
        self.engine.bus.emit(PartUpdated(
            session_id=session_id,
            message_id=message_id,
            part_id=part.id,
            part_type="text",
            data=part.data,
        ))
