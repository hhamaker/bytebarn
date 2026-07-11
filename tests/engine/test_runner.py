import asyncio
import json

import pytest

from crew.engine.facade import Engine
from crew.engine.providers.base import Done, TextDelta, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage
from crew.engine.providers.fake import FakeProvider, text_turn, tool_turn
from crew.engine.runner import history_to_messages


@pytest.fixture
async def engine(tmp_path):
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
    # titles/summaries go to a dedicated small-model provider so they never
    # consume the main provider's scripted turns
    def small(req):
        last = json.dumps(req.messages[-1].content)
        if "Summarize this coding session" in last:
            return text_turn("summary of session")
        return text_turn("A Session Title")

    eng.providers.register("small", FakeProvider(small))
    yield eng
    await eng.stop()


def _install(engine, script):
    provider = FakeProvider(script)
    engine.providers.register("fake", provider)
    return provider


async def _run_and_wait(engine, session, text):
    await engine.submit_prompt(session.id, text)
    handle = engine._runs[session.id]
    await handle.task
    # allow queued/title tasks to settle
    await asyncio.sleep(0.05)


async def _collect(engine, session_id):
    return await engine.store.session_parts(session_id)


async def test_text_only_turn_persists_and_finishes(engine):
    _install(engine, [text_turn("Hello there")])
    events = engine.bus.queue()
    session = await engine.new_session()
    await _run_and_wait(engine, session, "hi")

    history = await _collect(engine, session.id)
    roles = [m.role for m, _ in history]
    assert roles == ["user", "assistant"]
    assert history[1][1][0].data["text"] == "Hello there"
    assert history[1][0].tokens_in == 10 and history[1][0].tokens_out == 5

    names = []
    while not events.empty():
        names.append(events.get_nowait().name)
    assert "message.part.updated" in names
    assert "run.finished" in names


async def test_tool_call_loop(engine, tmp_path):
    proj = engine.project_dir
    (proj / "hello.txt").write_text("content here")
    _install(engine, [
        tool_turn("c1", "read", {"path": "hello.txt"}, text="Let me read that."),
        text_turn("The file says: content here"),
    ])
    session = await engine.new_session()
    await _run_and_wait(engine, session, "read hello.txt")

    history = await _collect(engine, session.id)
    assert [m.role for m, _ in history] == ["user", "assistant", "assistant"]
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0].data["status"] == "done"
    assert "content here" in tool_parts[0].data["output"]

    # second request carried the tool result back to the model
    provider = engine.providers.provider("fake")
    second_req = provider.requests[1]
    flat = json.dumps([m.__dict__ for m in second_req.messages])
    assert "tool_result" in flat and "content here" in flat


async def test_permission_deny_returns_tool_error(engine):
    engine.config.permission["bash"] = "deny"
    _install(engine, [
        tool_turn("c1", "bash", {"command": "rm -rf /"}),
        text_turn("ok, denied"),
    ])
    session = await engine.new_session()
    await _run_and_wait(engine, session, "do something")
    history = await _collect(engine, session.id)
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tool_parts[0].data["status"] == "error"
    assert "denied" in tool_parts[0].data["output"]


async def test_permission_ask_flow(engine):
    engine.config.permission["bash"] = "ask"
    _install(engine, [
        tool_turn("c1", "bash", {"command": "echo hi"}),
        text_turn("done"),
    ])
    events = engine.bus.queue()
    session = await engine.new_session()
    await engine.submit_prompt(session.id, "run echo")

    request_id = None
    for _ in range(200):
        while not events.empty():
            ev = events.get_nowait()
            if ev.name == "permission.asked":
                request_id = ev.request_id
                assert ev.tool == "bash" and ev.arg == "echo hi"
        if request_id:
            break
        await asyncio.sleep(0.01)
    assert request_id, "permission.asked never emitted"
    engine.answer_permission(request_id, "allow")
    await engine._runs[session.id].task

    history = await _collect(engine, session.id)
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tool_parts[0].data["status"] == "done"
    assert "hi" in tool_parts[0].data["output"]


async def test_subagent_task_flow(engine):
    def script(req):
        # orchestrator gets task tool; subagent doesn't
        tool_names = [t.name for t in req.tools]
        if "task" in tool_names:
            if not any("tool_result" in json.dumps(m.content) for m in req.messages):
                return tool_turn("t1", "task", {
                    "description": "explore repo",
                    "prompt": "look around",
                    "agent": "explore",
                })
            return text_turn("crew finished")
        assert "task" not in tool_names  # subagents cannot spawn subagents
        return text_turn("subagent report: repo is empty")

    _install(engine, script)
    events = engine.bus.queue()
    session = await engine.new_session(agent="orchestrator")
    await _run_and_wait(engine, session, "/goal explore the repo")

    history = await _collect(engine, session.id)
    task_parts = [p for _, parts in history for p in parts if p.type == "task"]
    assert len(task_parts) == 1
    assert task_parts[0].data["status"] == "done"
    assert "subagent report" in task_parts[0].data["output"]
    child_id = task_parts[0].data["subagent_session_id"]

    children = await engine.store.child_sessions(session.id)
    assert len(children) == 1 and children[0].id == child_id
    assert children[0].agent == "explore"

    names = []
    while not events.empty():
        names.append(events.get_nowait().name)
    assert "task.started" in names and "task.finished" in names


async def test_task_id_reuse_continues_child(engine):
    state = {"child_id": None}

    def script(req):
        tool_names = [t.name for t in req.tools]
        if "task" in tool_names:
            n_results = sum(json.dumps(m.content).count("tool_result") for m in req.messages)
            if n_results == 0:
                return tool_turn("t1", "task", {
                    "description": "step 1", "prompt": "do step 1", "agent": "general",
                })
            if n_results == 1 and state["child_id"]:
                return tool_turn("t2", "task", {
                    "description": "step 2", "prompt": "fix it", "agent": "general",
                    "task_id": state["child_id"],
                })
            return text_turn("all done")
        return text_turn("worker output")

    _install(engine, script)

    session = await engine.new_session(agent="orchestrator")

    async def watch():
        async for ev in engine.bus.subscribe():
            if ev.name == "task.started":
                state["child_id"] = ev.subagent_session_id
                break

    watcher = asyncio.ensure_future(watch())
    await _run_and_wait(engine, session, "goal")
    watcher.cancel()

    children = await engine.store.child_sessions(session.id)
    assert len(children) == 1  # reused, not duplicated
    child_history = await _collect(engine, children[0].id)
    user_texts = [
        p.data["text"] for m, parts in child_history for p in parts
        if m.role == "user" and p.type == "text"
    ]
    assert user_texts == ["do step 1", "fix it"]


async def test_abort_marks_pending_tools(engine):
    started = asyncio.Event()

    class SlowProvider:
        name = "fake"
        requests = []

        async def stream(self, req):
            yield ToolCallStart("c1", "bash")
            yield ToolCallDelta("c1", json.dumps({"command": "sleep 60"}))
            yield ToolCallEnd("c1")
            yield Usage(1, 1)
            yield Done("tool_use")
            started.set()

    engine.providers.register("fake", SlowProvider())
    session = await engine.new_session()
    await engine.submit_prompt(session.id, "go")
    await started.wait()
    await asyncio.sleep(0.1)  # let bash start
    await engine.abort(session.id)
    try:
        await engine._runs[session.id].task
    except asyncio.CancelledError:
        pass

    history = await _collect(engine, session.id)
    tool_parts = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tool_parts and tool_parts[0].data["status"] == "error"


async def test_queued_prompt_promoted(engine):
    release = asyncio.Event()

    class GatedProvider:
        name = "fake"
        requests = []

        def __init__(self):
            self.calls = 0

        async def stream(self, req):
            self.calls += 1
            if self.calls == 1:
                await release.wait()
            yield TextDelta(f"reply {self.calls}")
            yield Usage(1, 1)
            yield Done()

    provider = GatedProvider()
    engine.providers.register("fake", provider)
    session = await engine.new_session()
    await engine.submit_prompt(session.id, "first")
    await engine.submit_prompt(session.id, "second")  # queued mid-run
    release.set()
    await engine._runs[session.id].task
    for _ in range(100):
        await asyncio.sleep(0.01)
        if provider.calls >= 2 and not engine.is_running(session.id):
            break
    history = await _collect(engine, session.id)
    user_msgs = [m for m, _ in history if m.role == "user"]
    assert len(user_msgs) == 2
    assert provider.calls == 2


async def test_compaction_prunes_history(engine):
    _install(engine, [text_turn("answer one")])
    session = await engine.new_session()
    await _run_and_wait(engine, session, "question one")
    await engine.compact(session.id)

    history = await _collect(engine, session.id)
    types = [p.type for _, parts in history for p in parts]
    assert "compaction" in types

    msgs = history_to_messages(history)
    # pre-compaction content dropped; summary present
    flat = json.dumps([m.__dict__ for m in msgs])
    assert "question one" not in flat
    assert "summary of session" in flat


async def test_title_generated_for_new_session(engine):
    _install(engine, [text_turn("response")])
    session = await engine.new_session()
    await _run_and_wait(engine, session, "please fix the auth bug")
    for _ in range(100):
        got = await engine.store.get_session(session.id)
        if got.title:
            break
        await asyncio.sleep(0.02)
    assert got.title == "A Session Title"


async def test_goal_command_routes_to_orchestrator(engine):
    _install(engine, [text_turn("orchestrating")])
    session = await engine.new_session()
    await _run_and_wait(engine, session, "/goal build the thing")
    got = await engine.store.get_session(session.id)
    assert got.agent == "orchestrator"
    history = await _collect(engine, session.id)
    first_user_text = history[0][1][0].data["text"]
    assert "<goal>\nbuild the thing\n</goal>" in first_user_text


async def test_provider_error_visible_in_transcript(engine):
    from crew.engine.providers.base import ErrorEv

    class Failing:
        name = "fake"

        async def stream(self, req):
            yield ErrorEv("Could not resolve authentication method")

    engine.providers.register("fake", Failing())
    engine.fallback_model = lambda model, exclude: None  # no stand-in available
    session = await engine.new_session()
    await _run_and_wait(engine, session, "hi")

    history = await _collect(engine, session.id)
    assistant_parts = [p for m, parts in history if m.role == "assistant" for p in parts]
    texts = [p.data.get("text", "") for p in assistant_parts if p.type == "text"]
    # the error must appear as a visible transcript part, with the providers hint
    assert any("⚠" in t and "authentication" in t.lower() for t in texts)
    assert any("⚡ providers" in t for t in texts)


async def test_model_fallback_switches_after_failures(engine):
    from crew.engine.providers.base import ErrorEv
    from crew.engine.providers.fake import FakeProvider, text_turn

    class AlwaysFails:
        name = "fake"

        async def stream(self, req):
            yield ErrorEv("500 upstream exploded")

    engine.providers.register("fake", AlwaysFails())
    backup = FakeProvider([text_turn("rescued by backup model")])
    engine.providers.register("backup", backup)
    engine.fallback_model = lambda model, exclude: (
        "backup/rescue-1" if "backup/rescue-1" not in exclude else None
    )

    session = await engine.new_session()
    await _run_and_wait(engine, session, "hi")

    history = await _collect(engine, session.id)
    texts = [p.data.get("text", "")
             for m, parts in history if m.role == "assistant" for p in parts if p.type == "text"]
    # failed twice (default after=2), then announced the switch, then succeeded
    assert any("failed (1/2)" in t for t in texts)
    assert any("switching to comparable model backup/rescue-1" in t for t in texts)
    assert any("rescued by backup model" in t for t in texts)
    # the successful turn is attributed to the backup model
    models = [m.model for m, _ in history if m.role == "assistant"]
    assert "rescue-1" in models


async def test_model_fallback_disabled_by_config(tmp_path):
    import json as _json

    from crew.engine.facade import Engine
    from crew.engine.providers.base import ErrorEv

    proj = tmp_path / "proj2"
    proj.mkdir()
    gdir = tmp_path / "global2"
    gdir.mkdir()
    (gdir / "config.json").write_text(_json.dumps({
        "model": "fake/model", "small_model": "small/model",
        "model_fallback": {"enabled": False},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew2.db", global_dir=gdir)
    await eng.start()
    try:
        calls = {"n": 0}

        class AlwaysFails:
            name = "fake"

            async def stream(self, req):
                calls["n"] += 1
                yield ErrorEv("boom")

        eng.providers.register("fake", AlwaysFails())
        from crew.engine.providers.fake import FakeProvider, text_turn

        eng.providers.register("small", FakeProvider(lambda req: text_turn("t")))
        eng.fallback_model = lambda model, exclude: "backup/never"
        session = await eng.new_session()
        await eng.submit_prompt(session.id, "hi")
        await eng._runs[session.id].task
        # disabled: one failed turn, no retry, no switch
        assert calls["n"] == 1
    finally:
        await eng.stop()
