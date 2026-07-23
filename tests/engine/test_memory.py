"""Project memory: OKF bundle written by the memory tool, injected into runs."""

import json
from pathlib import Path

import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.tools.base import ToolContext
from bytebarn.engine.tools.memory import MemoryParams, MemoryTool


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=tmp_path, session_id="s1", memory_dir=tmp_path / "memory")


async def test_memory_save_writes_okf_concept_and_log(ctx, tmp_path):
    tool = MemoryTool()
    result = await tool.execute(MemoryParams(
        file="decisions/database.md", type="Decision", title="Use SQLite",
        description="Local-first app, no server.", tags=["storage"],
        content="We chose SQLite (WAL) because everything runs locally.",
    ), ctx)
    assert not result.is_error and "created" in result.output

    concept = (tmp_path / "memory/decisions/database.md").read_text()
    assert concept.startswith("---\n")
    assert 'type: "Decision"' in concept and 'title: "Use SQLite"' in concept
    assert "We chose SQLite" in concept

    log = (tmp_path / "memory/log.md").read_text()
    assert log.startswith("# Update Log")
    assert "**Create**: [Use SQLite](/decisions/database.md)" in log

    # second save is an update, logged above/with the create
    await tool.execute(MemoryParams(
        file="decisions/database.md", type="Decision", title="Use SQLite",
        content="Updated body."), ctx)
    log = (tmp_path / "memory/log.md").read_text()
    assert "**Update**" in log
    assert (tmp_path / "memory/decisions/database.md").read_text().count("Updated body.") == 1


async def test_memory_delete_and_guards(ctx, tmp_path):
    tool = MemoryTool()
    await tool.execute(MemoryParams(file="fact.md", content="x"), ctx)
    result = await tool.execute(MemoryParams(action="delete", file="fact.md"), ctx)
    assert "deleted" in result.output
    assert not (tmp_path / "memory/fact.md").exists()

    escape = await tool.execute(MemoryParams(file="../outside.md", content="x"), ctx)
    assert escape.is_error
    assert not (tmp_path / "outside.md").exists()

    reserved = await tool.execute(MemoryParams(file="log.md", content="x"), ctx)
    assert reserved.is_error


async def test_load_memory_orders_and_caps(tmp_path):
    from bytebarn.engine.runner import load_memory

    root = tmp_path / "memory"
    root.mkdir()
    (root / "log.md").write_text("# Update Log\n")
    (root / "a-fact.md").write_text("---\ntype: Note\n---\nsmall fact")
    (root / "huge.md").write_text("x" * 40_000)

    out = load_memory(root)
    rels = [rel for rel, _ in out]
    assert rels == ["a-fact.md", "huge.md", "log.md"]  # log last
    assert "small fact" in out[0][1]
    assert "too large" in out[1][1]

    assert load_memory(tmp_path / "missing") == []


async def test_memory_survives_session_death(tmp_path):
    """Session 1 saves a memory via the tool; a brand-new session's system
    prompt carries it."""
    from bytebarn.engine.providers.fake import FakeProvider, text_turn, tool_turn

    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/model", "small_model": "fake/model"}))
    engine = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)

    systems: list[str] = []

    def script(req):
        systems.append(req.system)
        flat = json.dumps([m.content for m in req.messages])
        if "tool_result" not in flat and "remember this" in flat:
            return tool_turn("m1", "memory", {
                "file": "user-prefs.md", "type": "Preference",
                "title": "Terse answers",
                "content": "The user prefers terse, caveman-style answers.",
            })
        return text_turn("ok")

    class Capturing(FakeProvider):
        pass

    engine.providers.register("fake", Capturing(script))
    await engine.start()
    try:
        first = await engine.new_session(directory=str(proj))
        await engine.submit_prompt(first.id, "remember this preference")
        await engine._runs[first.id].task

        bundle = engine.memory_dir(engine.project.id)
        assert (bundle / "user-prefs.md").exists()
        assert "caveman-style" in (bundle / "user-prefs.md").read_text()
        assert "user-prefs.md" in (bundle / "log.md").read_text()

        # "session dies": start a completely fresh one
        second = await engine.new_session(directory=str(proj))
        await engine.submit_prompt(second.id, "unrelated prompt")
        await engine._runs[second.id].task

        assert "caveman-style answers" in systems[-1]
        assert "<project-memory-guide>" in systems[-1]
    finally:
        await engine.stop()
