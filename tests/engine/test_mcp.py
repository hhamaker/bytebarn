"""MCP client support: real stdio server end-to-end, permissions, gating."""

import json
import sys

import pytest

from crew.engine.facade import Engine
from crew.engine.permissions import ASK, DENY, FULL_AUTO, SAFE, PermissionPolicy
from crew.engine.providers.fake import FakeProvider, text_turn, tool_turn

_SERVER = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("testsrv")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


mcp.run()
'''


def test_mcp_tools_permission_defaults():
    policy = PermissionPolicy({}, {})
    assert policy.resolve("mcp__linear__create_issue") == ASK

    safe = PermissionPolicy({}, {}, session_mode=SAFE)
    assert safe.resolve("mcp__linear__create_issue") == DENY

    # explicit config rule wins
    allowed = PermissionPolicy({"mcp__linear__create_issue": "allow"}, {})
    assert allowed.resolve("mcp__linear__create_issue") == "allow"


@pytest.fixture
async def engine(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(_SERVER)
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model", "small_model": "fake/model",
        "mcp": {"testsrv": {"command": sys.executable, "args": [str(server)]}},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    await eng.stop()


async def test_mcp_server_connects_and_lists_tools(engine):
    status = engine.mcp.status()
    assert len(status) == 1
    assert status[0]["connected"], status[0]["error"]
    assert "mcp__testsrv__add" in status[0]["tools"]

    tools = engine.mcp.tools_for(None)
    assert [t.name for t in tools] == ["mcp__testsrv__add"]
    assert "Add two numbers" in tools[0].description()
    schema = tools[0].tool_def().parameters
    assert set(schema.get("properties", {})) >= {"a", "b"}


async def test_mcp_tool_gating_by_agent_map(engine):
    assert engine.mcp.tools_for(None)                      # omitted map -> all
    assert engine.mcp.tools_for({"read": True}) == []      # explicit map -> opt-in
    assert engine.mcp.tools_for({"read": True, "mcp": True})


async def test_agent_calls_mcp_tool_end_to_end(engine):
    def script(req):
        names = [t.name for t in req.tools]
        assert "mcp__testsrv__add" in names  # MCP tool offered to the model
        flat = json.dumps([m.content for m in req.messages])
        if "tool_result" not in flat:
            return tool_turn("m1", "mcp__testsrv__add", {"a": 20, "b": 22})
        assert "42" in flat  # server's answer came back as the tool result
        return text_turn("the answer is 42")

    engine.providers.register("fake", FakeProvider(script))
    engine.set_session_mode(FULL_AUTO)  # skip the ask prompt in tests
    session = await engine.new_session(directory=str(engine.project_dir))
    await engine.submit_prompt(session.id, "add 20 and 22")
    await engine._runs[session.id].task

    history = await engine.store.session_parts(session.id)
    parts = [p for _, ps in history for p in ps]
    tool_parts = [p for p in parts if p.type == "tool"]
    assert tool_parts and tool_parts[0].data["tool"] == "mcp__testsrv__add"
    assert "42" in tool_parts[0].data["output"]
    assert any("the answer is 42" in p.data.get("text", "") for p in parts)


async def test_bad_mcp_server_fails_gracefully(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "mcp": {"broken": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(1)"]}},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()   # must not raise
    try:
        status = eng.mcp.status()
        assert status and not status[0]["connected"]
        assert eng.mcp.tools_for(None) == []
    finally:
        await eng.stop()
