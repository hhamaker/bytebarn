"""Claude Code runtime multiplexor — fake runtime, argv/config, native default."""

from __future__ import annotations

import asyncio
import json

import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.providers.fake import FakeProvider, text_turn
from bytebarn.engine.runtimes.claude_code import (
    ClaudeCodeConfig,
    ProjectionState,
    build_claude_argv,
    claude_code_config_from_engine,
    load_claude_session_id,
    model_for_cli,
    project_claude_event,
    project_claude_events,
)
from bytebarn.engine.runtimes.fake_claude import FakeClaudeCodeRuntime


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "runtime": "claude-code",
        "model": "fake/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    await eng.stop()


@pytest.fixture
async def native_engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "small_model": "small/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()

    def small(req):
        last = json.dumps(req.messages[-1].content)
        if "Summarize this coding session" in last:
            return text_turn("summary of session")
        return text_turn("A Session Title")

    eng.providers.register("small", FakeProvider(small))
    yield eng
    await eng.stop()


async def _run_and_wait(engine, session, text):
    await engine.submit_prompt(session.id, text)
    handle = engine._runs[session.id]
    try:
        await handle.task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.05)


async def _collect(engine, session_id):
    return await engine.store.session_parts(session_id)


def _text_script(text: str, session_id: str = "cc-sess-1") -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text[: max(1, len(text) // 2)]},
            },
            "session_id": session_id,
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text[max(1, len(text) // 2) :]},
            },
            "session_id": session_id,
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 3, "output_tokens": 7},
            },
            "session_id": session_id,
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": session_id,
            "total_cost_usd": 0.001,
            "usage": {"input_tokens": 3, "output_tokens": 7},
        },
    ]


def _tool_script(session_id: str = "cc-sess-tool") -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {
            "type": "assistant",
            "message": {
                "id": "msg_t",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading file…"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "hello.txt"},
                    },
                ],
            },
            "session_id": session_id,
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "file contents here",
                    "is_error": False,
                }],
            },
            "session_id": session_id,
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg_t2",
                "role": "assistant",
                "content": [{"type": "text", "text": "Done reading."}],
            },
            "session_id": session_id,
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done reading.",
            "session_id": session_id,
        },
    ]


# -- unit: config / argv -----------------------------------------------------


def test_claude_code_config_defaults():
    class C:
        model_extra = {}

    cfg = claude_code_config_from_engine(C())
    assert cfg.command == "claude"
    assert cfg.permission_mode == "acceptEdits"
    assert "Read" in cfg.allowed_tools
    assert cfg.include_partial_messages is True
    assert cfg.max_turns is None


def test_claude_code_config_from_extra():
    class C:
        model_extra = {
            "claude_code": {
                "command": "/usr/local/bin/claude",
                "permission_mode": "bypassPermissions",
                "allowed_tools": "Read,Bash",
                "max_turns": 12,
                "bare": True,
                "model": "claude-opus-4",
                "extra_args": ["--foo", "bar"],
            }
        }

    cfg = claude_code_config_from_engine(C())
    assert cfg.command == "/usr/local/bin/claude"
    assert cfg.permission_mode == "bypassPermissions"
    assert cfg.allowed_tools == "Read,Bash"
    assert cfg.max_turns == 12
    assert cfg.bare is True
    assert cfg.model == "claude-opus-4"
    assert cfg.extra_args == ["--foo", "bar"]


def test_model_for_cli_strips_provider():
    assert model_for_cli("anthropic/claude-sonnet-4") == "claude-sonnet-4"
    assert model_for_cli("claude-opus-4") == "claude-opus-4"
    assert model_for_cli("") is None
    assert model_for_cli("x", override="anthropic/y") == "y"
    # UI sentinel: omit --model so the CLI picks its default
    assert model_for_cli("claude-code/default") is None
    assert model_for_cli("default") is None
    assert model_for_cli("claude-code/sonnet") == "sonnet"


def test_build_claude_argv_basic():
    cfg = ClaudeCodeConfig()
    argv = build_claude_argv("hello world", cfg, session_model="anthropic/sonnet")
    assert argv[0] == "claude"
    assert "-p" in argv and "hello world" in argv
    assert "--output-format" in argv and "stream-json" in argv
    assert "--verbose" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "--allowedTools" in argv
    assert "--include-partial-messages" in argv
    assert "--model" in argv and "sonnet" in argv
    assert "--resume" not in argv
    assert "--max-turns" not in argv


def test_build_claude_argv_resume_and_options():
    cfg = ClaudeCodeConfig(
        max_turns=5, bare=True, include_partial_messages=False, extra_args=["--debug"],
    )
    argv = build_claude_argv(
        "follow up", cfg, session_model="", resume_id="abc-123",
    )
    assert "--resume" in argv and "abc-123" in argv
    assert "--max-turns" in argv and "5" in argv
    assert "--bare" in argv
    assert "--include-partial-messages" not in argv
    assert "--debug" in argv
    assert "--model" not in argv


# -- integration with FakeClaudeCodeRuntime ----------------------------------


async def test_fake_claude_text_turn(engine):
    assert engine.runtime_name() == "claude-code"
    fake = FakeClaudeCodeRuntime(engine, [_text_script("Hello from Claude")])
    engine._claude_runtime = fake
    events = engine.bus.queue()

    session = await engine.new_session()
    await _run_and_wait(engine, session, "hi there")

    history = await _collect(engine, session.id)
    roles = [m.role for m, _ in history]
    assert roles == ["user", "assistant"]
    texts = [p.data.get("text", "") for p in history[1][1] if p.type == "text"]
    assert any("Hello from Claude" == t for t in texts)
    assert fake.prompts == ["hi there"]

    # external session id cached for resume
    assert load_claude_session_id(engine.project_dir, session.id) == "cc-sess-1"

    names = []
    finished = None
    while not events.empty():
        ev = events.get_nowait()
        names.append(ev.name)
        if ev.name == "run.finished":
            finished = ev
    assert "message.part.updated" in names
    assert finished is not None and finished.status == "done"


async def test_fake_claude_tool_cards(engine):
    fake = FakeClaudeCodeRuntime(engine, [_tool_script()])
    engine._claude_runtime = fake

    session = await engine.new_session()
    await _run_and_wait(engine, session, "read hello")

    history = await _collect(engine, session.id)
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0].data["tool"] == "Read"
    assert tool_parts[0].data["status"] == "done"
    assert "file contents here" in tool_parts[0].data["output"]
    assert tool_parts[0].data["call_id"] == "toolu_1"


async def test_fake_claude_abort(engine):
    # Hang after first tool_use so abort marks it error
    hang_script = [
        {"type": "system", "subtype": "init", "session_id": "cc-abort"},
        {
            "type": "assistant",
            "message": {
                "id": "msg_a",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_hang",
                    "name": "Bash",
                    "input": {"command": "sleep 999"},
                }],
            },
            "session_id": "cc-abort",
        },
        # never reached without hang_before_event
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "nope",
            "session_id": "cc-abort",
        },
    ]
    fake = FakeClaudeCodeRuntime(engine, [hang_script])
    fake.hang_before_event = 2  # after tool_use, wait for abort
    engine._claude_runtime = fake
    events = engine.bus.queue()

    session = await engine.new_session()
    await engine.submit_prompt(session.id, "go")
    # wait until tool card exists
    for _ in range(100):
        history = await _collect(engine, session.id)
        tools = [p for _, parts in history for p in parts if p.type == "tool"]
        if tools:
            break
        await asyncio.sleep(0.01)
    await engine.abort(session.id)
    try:
        await engine._runs[session.id].task
    except asyncio.CancelledError:
        pass

    history = await _collect(engine, session.id)
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tool_parts and tool_parts[0].data["status"] == "error"
    assert "aborted" in (tool_parts[0].data.get("output") or "")

    finished = None
    while not events.empty():
        ev = events.get_nowait()
        if ev.name == "run.finished":
            finished = ev
    assert finished is not None and finished.status == "aborted"


async def test_native_runtime_still_default(native_engine):
    engine = native_engine
    assert engine.runtime_name() == "native"

    engine.providers.register("fake", FakeProvider([text_turn("native path")]))
    events = engine.bus.queue()
    session = await engine.new_session()
    await _run_and_wait(engine, session, "hello")

    history = await _collect(engine, session.id)
    assert history[1][1][0].data["text"] == "native path"
    names = []
    while not events.empty():
        names.append(events.get_nowait().name)
    assert "run.finished" in names


async def test_session_model_routes_to_claude_code(native_engine):
    """``claude-code/…`` model strings use the CC runtime even when sticky
    runtime is still ``native`` — agents can default to Claude Code per-agent.
    """
    engine = native_engine
    assert engine.runtime_name() == "native"
    fake = FakeClaudeCodeRuntime(engine, [_text_script("via model pin")])
    engine._claude_runtime = fake

    session = await engine.new_session(model="claude-code/sonnet")
    assert engine.uses_claude_code(session)
    await _run_and_wait(engine, session, "go")

    history = await _collect(engine, session.id)
    texts = [p.data.get("text", "") for p in history[1][1] if p.type == "text"]
    assert any("via model pin" == t for t in texts)
    assert fake.prompts == ["go"]


async def test_agent_default_model_routes_to_claude_code(tmp_path):
    """Agent config ``model: claude-code/default`` drives CC without sticky runtime."""
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "agent": {"build": {"model": "claude-code/default"}},
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    try:
        assert eng.runtime_name() == "native"
        assert eng.agents.get("build").model == "claude-code/default"
        fake = FakeClaudeCodeRuntime(eng, [_text_script("agent default cc")])
        eng._claude_runtime = fake

        session = await eng.new_session(agent="build", model="")
        # empty session.model → agent default
        assert eng.effective_model(session) == "claude-code/default"
        assert eng.uses_claude_code(session)
        await _run_and_wait(eng, session, "hello agent")
        assert fake.prompts == ["hello agent"]
    finally:
        await eng.stop()


async def test_subagent_inherits_claude_code_from_parent(native_engine):
    """Subagents without their own model inherit CC when the parent is on CC."""
    engine = native_engine
    fake = FakeClaudeCodeRuntime(engine, [
        _text_script("sub done", "cc-sub-1"),
    ])
    engine._claude_runtime = fake

    parent = await engine.new_session(model="claude-code/default")
    assert engine.uses_claude_code(parent)

    out = await engine.run_subagent(
        parent, "general", "do the thing", "sub task", task_id=None,
    )
    assert "sub done" in out
    assert fake.prompts == ["do the thing"]
    # child session should have been pinned to a CC model
    children = await engine.store.child_sessions(parent.id)
    assert children and children[0].model.startswith("claude-code/")


async def test_project_stream_events_helper(engine):
    session = await engine.new_session()
    msg = await engine.store.add_message(session.id, "assistant", model="x")
    events = [
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "Hi "}},
        },
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "there"}},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hi there"}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Hi there",
            "session_id": "proj-1",
        },
    ]
    state = await project_claude_events(engine, session, msg.id, events)
    assert state.claude_session_id == "proj-1"
    parts = await engine.store.list_parts(msg.id)
    texts = [p for p in parts if p.type == "text"]
    assert len(texts) == 1
    assert texts[0].data["text"] == "Hi there"


async def test_project_tool_result_error(engine):
    session = await engine.new_session()
    msg = await engine.store.add_message(session.id, "assistant")
    state = ProjectionState()
    await project_claude_event(engine, session, msg.id, {
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "id": "t1",
                "name": "Bash",
                "input": {"command": "false"},
            }],
        },
    }, state)
    await project_claude_event(engine, session, msg.id, {
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "exit 1",
                "is_error": True,
            }],
        },
    }, state)
    parts = await engine.store.list_parts(msg.id)
    tool = next(p for p in parts if p.type == "tool")
    assert tool.data["status"] == "error"
    assert tool.data["output"] == "exit 1"


async def test_resume_id_persisted_across_turns(engine):
    fake = FakeClaudeCodeRuntime(engine, [
        _text_script("first", "sid-A"),
        _text_script("second", "sid-A"),
    ])
    engine._claude_runtime = fake
    session = await engine.new_session()
    await _run_and_wait(engine, session, "one")
    assert load_claude_session_id(engine.project_dir, session.id) == "sid-A"
    await _run_and_wait(engine, session, "two")
    assert fake.prompts == ["one", "two"]
    assert load_claude_session_id(engine.project_dir, session.id) == "sid-A"
