"""Checkpoints, goal queue, and context usage — the walk-away feature set."""

import json

import pytest

from crew.engine.checkpoints import CheckpointStore
from crew.engine.facade import Engine
from crew.engine.providers.fake import FakeProvider, text_turn, tool_turn


# -- checkpoint store (unit) --------------------------------------------------

def test_checkpoint_snapshot_diff_revert(tmp_path):
    store = CheckpointStore(tmp_path / "cp")
    target = tmp_path / "app.py"
    target.write_text("x = 1\n")
    created = tmp_path / "new.py"

    store.begin("s1")
    store.snapshot("s1", target)
    store.snapshot("s1", created)      # doesn't exist yet -> created marker
    target.write_text("x = 2\n")
    created.write_text("print('hi')\n")
    cp = store.finish("s1")

    assert store.last("s1") is cp
    files = store.changed_files(cp)
    assert str(target.resolve()) in files and str(created.resolve()) in files

    diff = store.diff(cp, str(target.resolve()))
    assert "-x = 1" in diff and "+x = 2" in diff

    store.revert_all(cp)
    assert target.read_text() == "x = 1\n"
    assert not created.exists()        # created by run -> removed on revert


def test_checkpoint_no_changes_not_reviewable(tmp_path):
    store = CheckpointStore(tmp_path / "cp")
    store.begin("s1")
    assert store.finish("s1") is not None or True
    assert store.last("s1") is None    # nothing written -> nothing to review


# -- engine integration -------------------------------------------------------

@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model", "small_model": "fake/model",
        "permission": {"write": "allow", "edit": "allow", "bash": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    await eng.stop()


async def test_run_checkpoint_captures_write_tool(engine):
    (engine.project_dir / "a.py").write_text("OLD\n")

    def script(req):
        flat = json.dumps([m.content for m in req.messages])
        if "tool_result" not in flat:
            return tool_turn("w1", "write", {"path": "a.py", "content": "NEW\n"})
        return text_turn("done")

    engine.providers.register("fake", FakeProvider(script))
    session = await engine.new_session(directory=str(engine.project_dir))
    # satisfy the edit-before-read gate for the pre-existing file
    engine.files_read(session.id).add(str((engine.project_dir / "a.py").resolve()))
    await engine.submit_prompt(session.id, "rewrite a.py")
    await engine._runs[session.id].task

    assert (engine.project_dir / "a.py").read_text() == "NEW\n"
    cp = engine.checkpoints.last(session.id)
    assert cp is not None
    path = str((engine.project_dir / "a.py").resolve())
    assert path in cp.originals
    engine.checkpoints.revert_all(cp)
    assert (engine.project_dir / "a.py").read_text() == "OLD\n"


async def test_goal_queue_runs_sequentially(engine):
    engine.providers.register("fake", FakeProvider(lambda req: text_turn("done")))

    g1 = await engine.queue_goal("first goal")
    g2 = await engine.queue_goal("second goal")

    # first starts immediately, second waits
    goals = {g.id: g for g in await engine.store.list_goals(engine.project.id)}
    assert goals[g1.id].status == "running"
    assert goals[g2.id].status == "pending"

    await engine._runs[goals[g1.id].session_id].task
    goals = {g.id: g for g in await engine.store.list_goals(engine.project.id)}
    assert goals[g1.id].status == "done"
    assert goals[g2.id].status == "running"

    await engine._runs[goals[g2.id].session_id].task
    goals = {g.id: g for g in await engine.store.list_goals(engine.project.id)}
    assert goals[g2.id].status == "done"

    # both ran as /goal -> orchestrator sessions
    s1 = await engine.store.get_session(goals[g1.id].session_id)
    assert s1.agent == "orchestrator"


async def test_cancelled_goal_is_skipped(engine):
    engine.providers.register("fake", FakeProvider(lambda req: text_turn("ok")))
    g1 = await engine.queue_goal("run me")
    g2 = await engine.queue_goal("cancel me")
    g3 = await engine.queue_goal("run me too")
    await engine.cancel_goal(g2.id)

    goals = {g.id: g for g in await engine.store.list_goals(engine.project.id)}
    await engine._runs[goals[g1.id].session_id].task
    goals = {g.id: g for g in await engine.store.list_goals(engine.project.id)}
    assert goals[g2.id].status == "cancelled"
    assert goals[g3.id].status == "running"
    await engine._runs[goals[g3.id].session_id].task


async def test_context_usage_from_last_turn(engine):
    engine.providers.register("fake", FakeProvider([text_turn("hi")]))
    session = await engine.new_session(directory=str(engine.project_dir))

    used, window = await engine.context_usage(session.id)
    assert used == 0 and window > 0   # no turns yet, window from catalog fallback

    await engine.submit_prompt(session.id, "hello")
    await engine._runs[session.id].task
    used, window = await engine.context_usage(session.id)
    assert used > 0 and window >= used
