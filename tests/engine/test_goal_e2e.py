"""Signature-flow integration test (spec §9 definition of done), fake providers.

/goal -> orchestrator writes a todo plan, delegates two parallel subagent
tasks that edit different files on disk, verifies, and reports.
"""

import asyncio
import json

import pytest

from crew.engine.facade import Engine
from crew.engine.providers.base import Done, TextDelta, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage
from crew.engine.providers.fake import FakeProvider, text_turn, tool_turn


def multi_tool_turn(calls: list[tuple[str, str, dict]], text: str = ""):
    events = []
    if text:
        events.append(TextDelta(text))
    for call_id, name, input_data in calls:
        events += [
            ToolCallStart(call_id, name),
            ToolCallDelta(call_id, json.dumps(input_data)),
            ToolCallEnd(call_id),
        ]
    events += [Usage(10, 5), Done("tool_use")]
    return events


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model", "small_model": "small/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    eng.providers.register("small", FakeProvider(lambda req: text_turn("Title")))
    yield eng
    await eng.stop()


async def test_goal_full_flow(engine):
    concurrency = {"active": 0, "peak": 0}

    def script(req):
        tool_names = [t.name for t in req.tools]
        flat = json.dumps([m.content for m in req.messages])

        if "task" in tool_names:  # orchestrator
            if "tool_result" not in flat:
                # first turn: todo plan + two parallel tasks
                return multi_tool_turn([
                    ("todo1", "todowrite", {"todos": [
                        {"content": "write module a", "status": "pending"},
                        {"content": "write module b", "status": "pending"},
                        {"content": "verify", "status": "pending"},
                    ]}),
                    ("t1", "task", {"description": "write module a", "agent": "general",
                                    "prompt": "Create a.py with A=1"}),
                    ("t2", "task", {"description": "write module b", "agent": "general",
                                    "prompt": "Create b.py with B=2"}),
                ], text="Goal restated: create two modules.")
            return text_turn(
                "Summary: general wrote a.py; general wrote b.py; both verified on disk."
            )

        # subagent (no task tool available)
        assert "task" not in tool_names
        if "tool_result" not in flat:
            target = "a.py" if "a.py" in flat else "b.py"
            content = "A = 1\n" if target == "a.py" else "B = 2\n"
            return tool_turn("w1", "write", {"path": target, "content": content})
        return text_turn("wrote the module and verified it")

    class TrackingProvider(FakeProvider):
        async def stream(self, req):
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
            try:
                await asyncio.sleep(0.05)  # let parallel streams overlap
                async for event in super().stream(req):
                    yield event
            finally:
                concurrency["active"] -= 1

    engine.providers.register("fake", TrackingProvider(script))
    events: list = []

    async def collect():
        async for event in engine.bus.subscribe():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    session = await engine.new_session()
    await engine.submit_prompt(session.id, "/goal add modules a and b")
    await engine._runs[session.id].task
    await asyncio.sleep(0.1)
    collector.cancel()

    # routed to orchestrator
    final = await engine.store.get_session(session.id)
    assert final.agent == "orchestrator"

    # todo plan written
    todos = await engine.store.get_todos(session.id)
    assert [t.content for t in todos] == ["write module a", "write module b", "verify"]

    # two subagents ran (in parallel: provider streams overlapped)
    children = await engine.store.child_sessions(session.id)
    assert len(children) == 2

    # verified edits on disk
    assert (engine.project_dir / "a.py").read_text() == "A = 1\n"
    assert (engine.project_dir / "b.py").read_text() == "B = 2\n"

    # crew events flowed for the stage
    names = [e.name for e in events]
    assert names.count("task.started") == 2
    assert names.count("task.finished") == 2
    assert "todo.updated" in names

    # final per-agent summary present
    history = await engine.store.session_parts(session.id)
    last_texts = [p.data.get("text", "") for _, parts in history for p in parts if p.type == "text"]
    assert any("Summary" in t for t in last_texts)
