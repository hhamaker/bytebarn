"""Session lifecycle behavior on the Engine facade."""

import json
from pathlib import Path

import pytest

from bytebarn.engine.facade import Engine


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/model"}))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    await eng.stop()


async def test_new_session_reuses_empty_untitled(engine):
    first = await engine.new_session(directory="/tmp/work")
    again = await engine.new_session(directory="/tmp/work")
    assert again.id == first.id  # no "(untitled)" litter

    # a different directory is a different context — new row
    other = await engine.new_session(directory="/tmp/elsewhere")
    assert other.id != first.id


async def test_new_session_not_reused_once_used(engine):
    first = await engine.new_session(directory="/tmp/work")
    message = await engine.store.add_message(first.id, "user")
    await engine.store.add_part(message.id, "text", {"text": "hi"})
    fresh = await engine.new_session(directory="/tmp/work")
    assert fresh.id != first.id

    # a titled empty session isn't hijacked either
    await engine.store.update_session(fresh.id, title="named")
    third = await engine.new_session(directory="/tmp/work")
    assert third.id not in (first.id, fresh.id)


async def test_project_asset_copied_into_knowledge_dir(engine, tmp_path):
    src = tmp_path / "spec.md"
    src.write_text("the spec")
    asset = await engine.add_project_asset(engine.project.id, src)

    copied = Path(asset.path)
    assert copied.parent == engine.global_dir / "assets" / engine.project.id
    assert copied.read_text() == "the spec"

    # same-named file doesn't clobber the first copy
    other = tmp_path / "sub"
    other.mkdir()
    (other / "spec.md").write_text("different")
    second = await engine.add_project_asset(engine.project.id, other / "spec.md")
    assert second.path != asset.path
    assert Path(second.path).read_text() == "different"

    await engine.remove_project_asset(engine.project.id, asset.path)
    assert not copied.exists()
    names = [a.name for a in (await engine.project_knowledge(engine.project.id))[1]]
    assert names == ["spec.md"]


async def test_system_prompt_includes_project_knowledge(engine, tmp_path):
    from bytebarn.engine.runner import build_system_prompt

    await engine.store.set_project_instructions(
        engine.project.id, "Prefer functional style.")
    small = tmp_path / "notes.md"
    small.write_text("remember the launch date")
    await engine.add_project_asset(engine.project.id, small)
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * 40_000)
    await engine.add_project_asset(engine.project.id, big)

    instructions, assets = await engine.project_knowledge(engine.project.id)
    agent = engine.agents.get("build")
    system = await build_system_prompt(
        agent, engine.project_dir, [], project_instructions=instructions,
        assets=assets)

    assert "Prefer functional style." in system
    assert "remember the launch date" in system          # small text inlined
    assert 'file="big.bin"' in system and "not inlined" in system


async def test_project_defaults_applied_to_new_sessions(engine):
    await engine.store.set_project_defaults(
        engine.project.id, agent="plan", model="fake/small")
    s = await engine.new_session(directory="/tmp/a")
    assert s.agent == "plan" and s.model == "fake/small"

    # explicit choice still wins over project defaults
    s2 = await engine.new_session(agent="build", model="fake/big", directory="/tmp/b")
    assert s2.agent == "build" and s2.model == "fake/big"

    # clearing defaults falls back to the builtin default agent
    await engine.store.set_project_defaults(engine.project.id)
    s3 = await engine.new_session(directory="/tmp/c")
    assert s3.agent == "build" and s3.model == ""
