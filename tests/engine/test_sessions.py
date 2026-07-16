"""Session lifecycle behavior on the Engine facade."""

import json

import pytest

from crew.engine.facade import Engine


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
